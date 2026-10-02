#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pptx_animate -- add click-triggered Fade entrance animations to an existing .pptx.

Pure standard library: no python-pptx, no lxml, no pip installs.

Subcommands
-----------
  inject <input.pptx> <output.pptx> [--duration-ms 350] [--step-delay-ms 0]
  verify <file.pptx> [--expect-click]
  self-test [--workdir DIR]

The XML contract is documented in docs/pptx-animation-spec.md.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import warnings
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

# --------------------------------------------------------------------------
# Namespaces
# --------------------------------------------------------------------------

A_URI = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_URI = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_URI = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# --------------------------------------------------------------------------
# Tunables of the classification contract (see docs/pptx-animation-spec.md)
# --------------------------------------------------------------------------

FADE_PRESET_ID = "10"
FADE_PRESET_CLASS = "entr"
FADE_PRESET_SUBTYPE = "0"
FADE_FILTER = "fade"
DEFAULT_DURATION_MS = 350

SMALL_TEXT_PT = 10.0          # at or below this run size => page number / source line
DEFAULT_TEXT_PT = 18.0        # fallback when a run carries no explicit sz
MIN_FONT_PT = 1.0             # clamp for absurd sz values
MAX_FONT_PT = 400.0           # clamp for absurd sz values

BULLET_BOX_MIN_PARAS = 2      # >= this many paragraphs => one click per paragraph
# Same column: horizontal centres must agree closely.  Real decks align the
# members of one visual unit to the *exact* EMU centre, while neighbouring
# columns are ~2in apart, so a tight tolerance is both safe and sufficient.
COLUMN_CENTER_TOL_IN = 0.10
STACK_GAP_TOL_IN = 0.20       # tight vertical stacking tolerance (>= -tol)
MIN_H_OVERLAP = 0.60          # >= 60% horizontal overlap of the narrower box
# Takeaway / conclusion bar: sits in the lower part of the slide and spans a
# large share of the text column (a right-aligned page number is narrow).
TAKEAWAY_MIN_TOP_FRAC = 0.55
TAKEAWAY_MIN_WIDTH_FRAC = 0.50


# ==========================================================================
# Console / encoding helpers
# ==========================================================================


def _force_utf8_console() -> None:
    """Windows consoles default to a legacy code page; force UTF-8 where possible."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - exotic streams
            pass


# ==========================================================================
# Namespace / prefix detection
# ==========================================================================


def detect_presentation_prefix(xml_text: str) -> tuple[str, str]:
    """Return (prefix, uri) for the presentationml namespace used by this slide.

    ``prefix`` is ready for direct concatenation: ``"p:"`` or ``""`` for the
    default namespace.  The slide root is normally ``<p:sld xmlns:p="...">`` but
    the prefix is arbitrary -- it may be ``ns0``, ``ppt``, or absent entirely
    when presentationml is the *default* namespace.
    """
    root = ET.fromstring(xml_text)
    uri = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""

    m = re.compile(r"\s*<\s*([^\s/>?!][^\s/>]*)").search(xml_text)
    root_qname = m.group(1) if m else "p:sld"

    # Namespace declarations of the root start tag only (bounded, linear scan).
    gt = xml_text.find(">", m.end() if m else 0)
    head = xml_text[: gt + 1] if gt >= 0 else xml_text[:8192]
    for decl in re.finditer(r'xmlns:([A-Za-z_][\w.\-]*)\s*=\s*"([^"]*)"', head):
        if decl.group(2) == uri:
            return decl.group(1) + ":", uri
    default = re.search(r'\bxmlns\s*=\s*"([^"]*)"', head)
    if default and default.group(1) == uri:
        return "", uri  # default namespace

    # Fall back to the prefix written on the root tag itself.
    return (root_qname.split(":", 1)[0] + ":") if ":" in root_qname else "", uri


def lname(tag: str) -> str:
    """Local name of an ElementTree tag."""
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag.split(":", 1)[-1]


def is_p(tag: str, local: str) -> bool:
    """True when *tag* is the presentationml element *local* (any prefix)."""
    if not tag.startswith("{"):
        return tag == local
    uri, name = tag[1:].split("}", 1)
    return name == local and uri in (P_URI, "")


def is_a(tag: str, local: str) -> bool:
    if not tag.startswith("{"):
        return tag == local
    uri, name = tag[1:].split("}", 1)
    return name == local and uri in (A_URI, "")


def find_child(elem: ET.Element | None, local: str) -> ET.Element | None:
    if elem is None:
        return None
    for child in elem:
        if is_p(child.tag, local):
            return child
    return None


def iter_desc(elem: ET.Element, local: str):
    for node in elem.iter():
        if is_p(node.tag, local):
            yield node


# ==========================================================================
# XML / element helpers
# ==========================================================================


def xml_escape_attr(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def parse_emu(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def emu_to_inches(value: int | None) -> float:
    return (value / 914400.0) if value is not None else 0.0


def extent_inches(shape: dict) -> tuple[float, float]:
    try:
        ext = shape["node"].find(f".//{{{A_URI}}}xfrm/{{{A_URI}}}ext")
    except Exception:
        ext = None
    if ext is None:
        return (emu_to_inches(shape.get("cx")), emu_to_inches(shape.get("cy")))
    return (emu_to_inches(parse_emu(ext.get("cx"))), emu_to_inches(parse_emu(ext.get("cy"))))


# ==========================================================================
# Shape model
# ==========================================================================


def _shape_kind(node: ET.Element) -> str | None:
    local = lname(node.tag)
    if local == "sp":
        return "sp"
    if local == "pic":
        return "pic"
    if local == "graphicFrame":
        return "graphicFrame"
    if local == "cxnSp":
        return "cxnSp"
    return None


def _cNvPr(node: ET.Element) -> ET.Element | None:
    """The shape's ``p:cNvPr`` (the one carrying @id and @name)."""
    local = lname(node.tag)
    if local == "sp":
        holder = find_child(node, "nvSpPr")
    elif local == "pic":
        holder = find_child(node, "nvPicPr")
    elif local == "graphicFrame":
        holder = find_child(node, "nvGraphicFramePr")
    elif local == "cxnSp":
        holder = find_child(node, "nvCxnSpPr")
    else:
        return None
    return find_child(holder, "cNvPr")


def _placeholder_type(node: ET.Element) -> tuple[str | None, str | None]:
    """(ph/@type, ph/@idx) for a shape.

    NOTE: ``p:ph`` lives under ``p:cNvSpPr/p:nvPr`` (NOT ``p:nvSpPr/p:nvPr``),
    i.e. under the *second* child group of the nv properties.
    """
    local = lname(node.tag)
    if local == "sp":
        holder = find_child(node, "nvSpPr")
    elif local == "pic":
        holder = find_child(node, "nvPicPr")
    elif local == "graphicFrame":
        holder = find_child(node, "nvGraphicFramePr")
    elif local == "cxnSp":
        holder = find_child(node, "nvCxnSpPr")
    else:
        return (None, None)
    if holder is None:
        return (None, None)
    ph = None
    for child in holder:
        nv_pr = find_child(child, "nvPr")
        if nv_pr is None:
            continue
        found = find_child(nv_pr, "ph")
        if found is not None:
            ph = found
            break
    if ph is None:
        return (None, None)
    return (ph.get("type") or "body", ph.get("idx"))


def _tx_body(node: ET.Element) -> ET.Element | None:
    if lname(node.tag) != "sp":
        return None
    return find_child(node, "txBody")


def paragraph_elements(tx_body: ET.Element | None) -> list[ET.Element]:
    if tx_body is None:
        return []
    return [c for c in tx_body if is_a(c.tag, "p")]


def paragraph_text(p_elem: ET.Element) -> str:
    return "".join(t.text or "" for t in p_elem.iter() if is_a(t.tag, "t"))


def _rpr_sizes(p_elem: ET.Element) -> list[float]:
    """Explicit run sizes (pt) inside one a:p, including its a:endParaRPr."""
    sizes: list[float] = []
    for rpr in p_elem.iter():
        if not (is_a(rpr.tag, "rPr") or is_a(rpr.tag, "endParaRPr") or is_a(rpr.tag, "defRPr")):
            continue
        raw = rpr.get("sz")
        if not raw:
            continue
        try:
            sizes.append(int(raw) / 100.0)
        except ValueError:
            continue
    return sizes


def max_font_size_pt(shape: dict) -> float | None:
    """Largest explicit run size on the shape, or None when entirely unspecified."""
    sizes: list[float] = []
    for p_elem in shape.get("paragraphs", []):
        sizes.extend(_rpr_sizes(p_elem))
    if not sizes:
        return None
    return max(sizes)


def effective_font_pt(shape: dict) -> float:
    size = max_font_size_pt(shape)
    return DEFAULT_TEXT_PT if size is None else size


def build_shape(node: ET.Element) -> dict | None:
    kind = _shape_kind(node)
    if kind is None:
        return None
    nv = _cNvPr(node)
    if nv is None or not nv.get("id"):
        return None
    xfrm = node.find(f".//{{{A_URI}}}xfrm")
    if xfrm is None:
        xfrm = node.find(f".//{{{P_URI}}}xfrm")  # graphicFrame/pic keep p:xfrm
    off = xfrm.find(f"{{{A_URI}}}off") if xfrm is not None else None
    ext = xfrm.find(f"{{{A_URI}}}ext") if xfrm is not None else None
    ph_type, ph_idx = _placeholder_type(node)
    body = _tx_body(node)
    paragraphs = paragraph_elements(body)
    shape = {
        "node": node,
        "kind": kind,
        "id": str(nv.get("id")),
        "name": nv.get("name") or "",
        "x": parse_emu(off.get("x")) if off is not None else None,
        "y": parse_emu(off.get("y")) if off is not None else None,
        "cx": parse_emu(ext.get("cx")) if ext is not None else None,
        "cy": parse_emu(ext.get("cy")) if ext is not None else None,
        "ph_type": ph_type,
        "ph_idx": ph_idx,
        "has_tx_body": body is not None,
        "paragraphs": paragraphs,
        "paragraph_texts": [paragraph_text(p) for p in paragraphs],
        "n_paragraphs": len(paragraphs),
        "text": "".join(t.text or "" for t in node.iter() if is_a(t.tag, "t")).strip(),
        "table_rows": None,
    }
    if kind == "graphicFrame":
        tbl = node.find(f".//{{{A_URI}}}tbl")
        if tbl is not None:
            shape["table_rows"] = len([c for c in tbl if is_a(c.tag, "tr")])
    return shape


# ==========================================================================
# Slide access
# ==========================================================================


def slide_number(name: str) -> int:
    m = re.search(r"(\d+)\.xml$", name.split("/")[-1])
    return int(m.group(1)) if m else 0


def list_slide_names(zf: zipfile.ZipFile) -> list[str]:
    names = [
        n
        for n in zf.namelist()
        if re.match(r"^ppt/slides/slide\d+\.xml$", n) and not n.endswith(".rels")
    ]
    return sorted(names, key=slide_number)


def read_slide_tree(zf: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(zf.read(name).decode("utf-8"))


def slide_shape_tree(root: ET.Element) -> ET.Element | None:
    cSld = find_child(root, "cSld")
    return find_child(cSld, "spTree")


def collect_shapes(root: ET.Element) -> list[dict]:
    tree = slide_shape_tree(root)
    if tree is None:
        return []
    shapes: list[dict] = []
    for node in tree:
        shape = build_shape(node)
        if shape is not None:
            shapes.append(shape)
    return shapes


def slide_size(root: ET.Element) -> tuple[float, float]:
    """(width_in, height_in) from p:sld/@sldSz; default 10 x 7.5 (4:3)."""
    cSld = find_child(root, "cSld")
    sz = None
    if cSld is not None:
        sz = cSld.get("sldSz")
    if not sz:
        return (10.0, 7.5)
    m = re.match(r"\s*(\d+)\s*x\s*(\d+)", sz)
    if not m:
        return (10.0, 7.5)
    return (int(m.group(1)) / 914400.0, int(m.group(2)) / 914400.0)


def existing_timing(root: ET.Element) -> ET.Element | None:
    return find_child(root, "timing")


def existing_targets(timing: ET.Element | None) -> set[str]:
    """All spids already targeted by an effect in an existing timing tree."""
    if timing is None:
        return set()
    found: set[str] = set()
    for tgt in timing.iter():
        if is_p(tgt.tag, "spTgt") and tgt.get("spid"):
            found.add(str(tgt.get("spid")))
    return found


def carried_builds(timing: ET.Element | None) -> list[tuple[str, str]]:
    """(spid, build) pairs from an existing p:bldLst, preserved on re-inject."""
    out: list[tuple[str, str]] = []
    if timing is None:
        return out
    bld = find_child(timing, "bldLst")
    if bld is None:
        return out
    for item in bld:
        spid = item.get("spid")
        if spid:
            out.append((str(spid), item.get("build") or "p"))
    return out


# ==========================================================================
# Classification
# ==========================================================================

STRUCTURAL_PLACEHOLDER_TYPES = {"title", "ctrTitle", "subTitle"}


def is_decoration_shape(shape: dict) -> bool:
    """No text, not a picture, not a graphicFrame => decoration / structure."""
    if shape["kind"] in ("pic", "graphicFrame"):
        return False
    return shape["text"] == ""


def is_static_shape(shape: dict, slide_w: float, slide_h: float) -> bool:
    """True when the shape must never be animated."""
    if is_decoration_shape(shape):
        return True
    if shape["kind"] in ("pic", "graphicFrame"):
        return False
    if shape["ph_type"] in STRUCTURAL_PLACEHOLDER_TYPES:
        return True

    size = max_font_size_pt(shape)
    if size is None:
        return False
    if size <= SMALL_TEXT_PT:
        return True

    # Layout heuristics for untyped decks: full-width bar at the very top is the
    # eyebrow label, the page-number / source corner is small text.
    top = emu_to_inches(shape["y"])
    w, h = extent_inches(shape)
    if slide_h and top <= 0.12 * slide_h and w >= 0.85 * slide_w:
        return True  # eyebrow
    if slide_w and slide_h:
        right = emu_to_inches(shape["x"]) + w
        if (
            top >= 0.78 * slide_h
            and right >= 0.93 * slide_w
            and emu_to_inches(shape["x"]) >= 0.70 * slide_w
        ):
            return True  # page number
    return False


def _h_overlap_ratio(a: dict, b: dict) -> float:
    ax, aw = emu_to_inches(a["x"]), extent_inches(a)[0]
    bx, bw = emu_to_inches(b["x"]), extent_inches(b)[0]
    if aw <= 0 or bw <= 0:
        return 0.0
    lo = max(ax, bx)
    hi = min(ax + aw, bx + bw)
    inter = max(0.0, hi - lo)
    return inter / min(aw, bw)


def _same_column(a: dict, b: dict) -> bool:
    aw, ah = extent_inches(a)
    bw, bh = extent_inches(b)
    ca = emu_to_inches(a["x"]) + aw / 2.0
    cb = emu_to_inches(b["x"]) + bw / 2.0
    return abs(ca - cb) <= COLUMN_CENTER_TOL_IN


def _tightly_stacked(a: dict, b: dict) -> bool:
    ay, ah = emu_to_inches(a["y"]), extent_inches(a)[1]
    by, bh = emu_to_inches(b["y"]), extent_inches(b)[1]
    if ay <= by:
        gap = by - (ay + ah)
    else:
        gap = ay - (by + bh)
    return gap >= -STACK_GAP_TOL_IN and gap <= STACK_GAP_TOL_IN


def group_single_paragraph_boxes(boxes: list[dict]) -> list[list[dict]]:
    """Group single-paragraph text boxes that read as one visual unit.

    Three conditions, all required:
      1. same column  -- horizontal centres agree within COLUMN_CENTER_TOL_IN,
      2. tight stacking -- consecutive members of that column have a vertical gap
         within +/- STACK_GAP_TOL_IN (no other candidate wedged between them),
      3. horizontal overlap >= 60% of the narrower box.

    Resolving the *column first* matters: a slide with several rows and columns
    (a 4x3 stat grid) interleaves the rows when sorted by y alone, so same-column
    boxes must be collected before stacking is evaluated.
    """
    if not boxes:
        return []

    # --- 1. column pass: union boxes whose horizontal centres coincide ------
    parent = list(range(len(boxes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _same_column(boxes[i], boxes[j]) and _h_overlap_ratio(boxes[i], boxes[j]) >= MIN_H_OVERLAP:
                union(i, j)

    columns: dict[int, list[dict]] = {}
    for i, box in enumerate(boxes):
        columns.setdefault(find(i), []).append(box)

    # --- 2. stacking pass: consecutive members of each column ---------------
    groups: list[list[dict]] = []
    for members in columns.values():
        stack = sorted(members, key=lambda s: (emu_to_inches(s["y"]), emu_to_inches(s["x"]), str(s["id"])))
        run = [stack[0]]
        for prev, cur in zip(stack, stack[1:]):
            if _tightly_stacked(prev, cur) and _h_overlap_ratio(prev, cur) >= MIN_H_OVERLAP:
                run.append(cur)
            else:
                groups.append(run)
                run = [cur]
        groups.append(run)

    groups.sort(key=lambda g: (emu_to_inches(g[0]["y"]), emu_to_inches(g[0]["x"]), str(g[0]["id"])))
    return groups


def _pick_anchor(shapes: list[dict]) -> dict:
    """Chapter heading (16-17pt) else the first shape, used as unit sort key."""
    for s in shapes:
        eff = effective_font_pt(s)
        if 15.0 <= eff <= 17.5:
            return s
    return shapes[0]


def classify_slide(shapes: list[dict], slide_w: float, slide_h: float,
                   preserve_spids: set[str] | None = None) -> list[dict]:
    """Return the ordered reveal plan: ``[{kind, shapes|paras, takeaway}]``.

    ``preserve_spids`` is the set of spids an existing <p:timing> already targets.
    The contract's static rules take priority over it (a decoration that a deck
    previously animated is *not* re-animated), but it lets a future revision keep
    the original relative order of already-animated content.
    """
    preserve_spids = preserve_spids or set()

    static_ids: set[str] = set()

    def never_revealable(shape: dict) -> bool:
        """Static by contract, regardless of any pre-existing animation."""
        if is_decoration_shape(shape):
            return True
        if is_static_shape(shape, slide_w, slide_h):
            return True
        if shape["kind"] not in ("sp", "pic", "graphicFrame"):
            return True
        if shape["kind"] == "sp" and shape["n_paragraphs"] == 0:
            return True
        return False

    candidates: list[dict] = []
    for shape in shapes:
        # The contract's static rules always win, even for a shape that an
        # existing <p:timing> already animated: re-injecting must leave the deck
        # spec-compliant rather than preserving e.g. a decoration wipe.
        if never_revealable(shape):
            static_ids.add(shape["id"])
            continue
        candidates.append(shape)

    # --- 1. one click per paragraph for multi-paragraph text boxes ----------
    bullets = [s for s in candidates if s["kind"] == "sp" and s["n_paragraphs"] >= BULLET_BOX_MIN_PARAS]
    bullet_ids = {s["id"] for s in bullets}
    units: list[dict] = [
        {"kind": "bullet", "shape": s, "anchor": s, "paras": list(range(s["n_paragraphs"])),
         "shapes": [s], "takeaway": False}
        for s in bullets
    ]

    # --- 2. grouped single-paragraph text boxes ----------------------------
    # A title placeholder is never animated (contract rule 1), so when a deck
    # marks the chapter heading as a title placeholder, drop it from its stack and
    # let the subtitle / body line beneath it carry the click.
    singles = [
        s
        for s in candidates
        if s["kind"] == "sp"
        and s["id"] not in bullet_ids
        and s["n_paragraphs"] <= 1
        and s["ph_type"] not in ("title", "ctrTitle")
    ]
    for group in group_single_paragraph_boxes(singles):
        units.append(
            {
                "kind": "group",
                "shapes": group,
                "anchor": _pick_anchor(group),
                "paras": None,
                "takeaway": False,
            }
        )

    # --- 3. graphicFrames, then pictures ----------------------------------
    for kind in ("graphicFrame", "pic"):
        for s in candidates:
            if s["kind"] != kind:
                continue
            units.append(
                {"kind": kind, "shapes": [s], "anchor": s, "paras": None, "takeaway": False}
            )

    # --- 4. order: contract order (bullets, units, frames, pictures) then
    #        reading order (top, left) inside each class ---------------------
    def class_rank(unit: dict) -> int:
        if unit["kind"] == "bullet":
            return 0
        if unit["kind"] == "group":
            return 1
        if unit["kind"] == "graphicFrame":
            return 2
        return 3  # pic

    def position_key(unit: dict) -> tuple[float, float, str]:
        members = unit["shapes"] if unit["kind"] != "bullet" else [unit["shape"]]
        top = min(emu_to_inches(s["y"]) for s in members)
        left = min(emu_to_inches(s["x"]) for s in members)
        return (top, left, str(members[0]["id"]))

    units.sort(key=lambda u: (class_rank(u),) + position_key(u))

    # --- 5. the takeaway / conclusion bar posts last -----------------------
    # It is the lowest wide text unit on the slide -- distinct from the narrow,
    # right-aligned page number.
    takeaway = None
    for unit in units:
        if unit["kind"] != "group" or len(unit["shapes"]) != 1:
            continue
        shape = unit["shapes"][0]
        if emu_to_inches(shape["y"]) < TAKEAWAY_MIN_TOP_FRAC * slide_h:
            continue
        if extent_inches(shape)[0] < TAKEAWAY_MIN_WIDTH_FRAC * slide_w:
            continue
        takeaway = unit
    if takeaway is not None:
        units.remove(takeaway)
        takeaway["takeaway"] = True
        units.append(takeaway)  # always the final click

    # --- 6. a cover with only a title + subtitle: subtitle carries the click
    if not units:
        subtitle = next(
            (s for s in shapes if s["ph_type"] in ("subTitle", "body") and s["text"]),
            None,
        )
        if subtitle is None:
            subtitle = next(
                (
                    s
                    for s in shapes
                    if s["kind"] == "sp"
                    and s["text"]
                    and s["ph_type"] not in ("title", "ctrTitle")
                ),
                None,
            )
        if subtitle is not None:
            units.append(
                {
                    "kind": "group",
                    "shapes": [subtitle],
                    "anchor": subtitle,
                    "paras": None,
                    "takeaway": False,
                    "fallback": True,
                }
            )
    elif len(units) == 1 and len(units[0]["shapes"]) > 1:
        # Cover heuristic for decks with no placeholder markup: a slide whose only
        # revealable content is one stack of text (display title + subtitle) must
        # not animate the display title.  Drop the topmost line of that stack.
        stack = units[0]["shapes"]
        units[0]["shapes"] = stack[1:]
        units[0]["anchor"] = _pick_anchor(units[0]["shapes"])
    return units


def plan_summary(plan: list[dict]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for unit in plan:
        if unit["kind"] == "bullet":
            shape = unit["shape"]
            for i in unit["paras"]:
                label = shape["paragraph_texts"][i][:24] if i < len(shape["paragraph_texts"]) else ""
                out.append((f"sp{shape['id']} p{i}", label))
        else:
            ids = "+".join(f"sp{s['id']}" for s in unit["shapes"])
            label = (unit["shapes"][0]["text"] or unit["shapes"][0]["name"])[:24]
            tag = " [takeaway]" if unit["takeaway"] else ""
            out.append((ids + tag, label))
    return out


# ==========================================================================
# Timing XML generation
# ==========================================================================


class IdAllocator:
    """Per-slide id allocator: tmRoot=1, mainSeq=2, then 2 per click group and
    3 per effect.  Every id is unique inside one slide's timing tree."""

    def __init__(self, first_id: int = 1) -> None:
        self.next_id = first_id
        self._first = first_id

    def take(self) -> int:
        value = self.next_id
        self.next_id += 1
        return value

    def take_many(self, count: int) -> list[int]:
        return [self.take() for _ in range(count)]


TM_ROOT_ID = 1
MAIN_SEQ_ID = 2


def _effect_xml(
    pfx: str,
    effect_id: int,
    set_id: int,
    anim_id: int,
    node_type: str,
    grp_id: int,
    spid: str,
    duration_ms: int,
    step_delay_ms: int,
    para: int | None,
    indent: str,
) -> str:
    content_indent = indent + " " * 2
    sp_tgt = f'<{pfx}spTgt spid="{xml_escape_attr(spid)}">'
    if para is not None:
        sp_tgt += (
            f'<{pfx}txEl><{pfx}pRg st="{para}" end="{para}"/></{pfx}txEl>'
        )
    sp_tgt += f"</{pfx}spTgt>"

    set_block = (
        f'<{pfx}set><{pfx}cBhvr>'
        f'<{pfx}cTn id="{set_id}" dur="1" fill="hold">'
        f'<{pfx}stCondLst><{pfx}cond delay="0"/></{pfx}stCondLst>'
        f"</{pfx}cTn>"
        f"<{pfx}tgtEl>{sp_tgt}</{pfx}tgtEl>"
        f"<{pfx}attrNameLst><{pfx}attrName>style.visibility</{pfx}attrName></{pfx}attrNameLst>"
        f"</{pfx}cBhvr><{pfx}to><{pfx}strVal val=\"visible\"/></{pfx}to></{pfx}set>"
    )
    anim_block = (
        f'<{pfx}animEffect transition="in" filter="{FADE_FILTER}"><{pfx}cBhvr>'
        f'<{pfx}cTn id="{anim_id}" dur="{int(duration_ms)}"/>'
        f"<{pfx}tgtEl>{sp_tgt}</{pfx}tgtEl>"
        f"</{pfx}cBhvr></{pfx}animEffect>"
    )
    return (
        f'{indent}<{pfx}par><{pfx}cTn id="{effect_id}" presetID="{FADE_PRESET_ID}" '
        f'presetClass="{FADE_PRESET_CLASS}" presetSubtype="{FADE_PRESET_SUBTYPE}" '
        f'fill="hold" grpId="{grp_id}" nodeType="{node_type}">'
        f'<{pfx}stCondLst><{pfx}cond delay="{int(step_delay_ms)}"/></{pfx}stCondLst>'
        f"<{pfx}childTnLst>{set_block}{anim_block}</{pfx}childTnLst>"
        f"</{pfx}cTn></{pfx}par>"
    )


def build_timing_xml(
    pfx: str,
    plan: list[dict],
    duration_ms: int = DEFAULT_DURATION_MS,
    step_delay_ms: int = 0,
    carried: list[tuple[str, str]] | None = None,
    indent: str = "",
) -> str:
    """Render the whole ``<p:timing>`` element for one slide."""
    alloc = IdAllocator(TM_ROOT_ID)
    tm_root = alloc.take()   # 1
    main_seq = alloc.take()  # 2
    assert tm_root == TM_ROOT_ID and main_seq == MAIN_SEQ_ID

    groups: list[str] = []
    build_entries: list[tuple[str, str]] = list(carried or [])
    seen_builds = {spid for spid, _ in build_entries}

    for grp_id, unit in enumerate(plan):
        outer_id = alloc.take()
        middle_id = alloc.take()

        effects: list[str] = []
        if unit["kind"] == "bullet":
            shape = unit["shape"]
            if shape["id"] not in seen_builds:
                build_entries.append((shape["id"], "p"))
                seen_builds.add(shape["id"])
            for n, para in enumerate(unit["paras"]):
                node_type = "clickEffect" if n == 0 else "withEffect"
                ids = alloc.take_many(3)
                effects.append(
                    _effect_xml(
                        pfx, ids[0], ids[1], ids[2], node_type, grp_id,
                        shape["id"], duration_ms, step_delay_ms, para,
                        indent + " " * 10,
                    )
                )
        else:
            for n, shape in enumerate(unit["shapes"]):
                node_type = "clickEffect" if n == 0 else "withEffect"
                ids = alloc.take_many(3)
                effects.append(
                    _effect_xml(
                        pfx, ids[0], ids[1], ids[2], node_type, grp_id,
                        shape["id"], duration_ms, step_delay_ms, None,
                        indent + " " * 10,
                    )
                )

        # First click group waits for the speaker; later groups chain off the
        # previous click (delay="0").
        outer_delay = "indefinite" if grp_id == 0 else "0"
        groups.append(
            f'{indent}  <{pfx}par><{pfx}cTn id="{outer_id}" fill="hold">'
            f'<{pfx}stCondLst><{pfx}cond delay="{outer_delay}"/></{pfx}stCondLst>'
            f"<{pfx}childTnLst>"
            f'<{pfx}par><{pfx}cTn id="{middle_id}" fill="hold">'
            f'<{pfx}stCondLst><{pfx}cond delay="0"/></{pfx}stCondLst>'
            f"<{pfx}childTnLst>{''.join(effects)}</{pfx}childTnLst>"
            f"</{pfx}cTn></{pfx}par>"
            f"</{pfx}childTnLst></{pfx}cTn></{pfx}par>"
        )

    bld_xml = ""
    if build_entries:
        items = "".join(
            f'<{pfx}bldP spid="{xml_escape_attr(spid)}" grpId="0" build="{xml_escape_attr(build)}"/>'
            for spid, build in build_entries
        )
        bld_xml = f"{indent}<{pfx}bldLst>{items}</{pfx}bldLst>"

    return (
        f"{indent}<{pfx}timing><{pfx}tnLst><{pfx}par>"
        f'<{pfx}cTn id="{tm_root}" dur="indefinite" restart="never" nodeType="tmRoot">'
        f"<{pfx}childTnLst>"
        f'<{pfx}seq concurrent="1" nextAc="seek">'
        f'<{pfx}cTn id="{main_seq}" dur="indefinite" nodeType="mainSeq">'
        f"<{pfx}childTnLst>{''.join(groups)}</{pfx}childTnLst>"
        f"</{pfx}cTn></{pfx}seq>"
        f"</{pfx}childTnLst>"
        f"</{pfx}cTn>"
        f"</{pfx}par></{pfx}tnLst>{bld_xml}</{pfx}timing>"
    )


# ==========================================================================
# Slide surgery
# ==========================================================================


def strip_existing_timing(xml_text: str) -> tuple[str, str | None]:
    """Remove any existing presentationml <p:timing>...</p:timing> element.

    Returns (xml_without_timing, raw_timing_markup_or_None).  Prefix-agnostic:
    the element is located by namespace via ElementTree, then removed with a
    literal slice on its serialized form.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return xml_text, None

    timing = None
    parent_map = {child: parent for parent in root.iter() for child in parent}
    for node in root.iter():
        if is_p(node.tag, "timing"):
            timing = node
            break
    if timing is None:
        return xml_text, None

    raw = ET.tostring(timing, encoding="unicode")
    idx = xml_text.find(raw)
    if idx >= 0:
        return xml_text[:idx] + xml_text[idx + len(raw):], raw
    # Serialization mismatch: fall back to a namespace-aware regex.
    prefix = detect_presentation_prefix(xml_text)[0]
    pattern = re.compile(
        rf"<{re.escape(prefix)}timing(?:\s[^>]*)?>.*?</{re.escape(prefix)}timing\s*>",
        re.DOTALL,
    )
    new_text, count = pattern.subn("", xml_text, count=1)
    if count:
        return new_text, raw
    return xml_text, None


def inject_slide_xml(
    xml_text: str,
    duration_ms: int = DEFAULT_DURATION_MS,
    step_delay_ms: int = 0,
) -> tuple[str, dict]:
    """Return (new_slide_xml, stats).  Idempotent: never leaves two <p:timing>."""
    prefix, _uri = detect_presentation_prefix(xml_text)
    working, _old_raw = strip_existing_timing(xml_text)

    root = ET.fromstring(working)
    shapes = collect_shapes(root)
    slide_w, slide_h = slide_size(root)

    timing = existing_timing(ET.fromstring(xml_text))
    preserve = existing_targets(timing)
    carried = carried_builds(timing)

    plan = classify_slide(shapes, slide_w, slide_h, preserve_spids=preserve)
    timing_xml = build_timing_xml(
        prefix, plan, duration_ms=duration_ms, step_delay_ms=step_delay_ms, carried=carried
    )

    stripped = working.rstrip()
    close_tag = f"</{prefix}sld>"
    if not stripped.endswith(close_tag):
        raise ValueError(
            f"slide XML does not end with {close_tag!r}; cannot append <{prefix}timing> safely"
        )
    new_xml = stripped[: -len(close_tag)] + timing_xml + close_tag
    # Sanity: the result must still parse and hold exactly one timing element.
    ET.fromstring(new_xml)

    stats = {
        "prefix": prefix.rstrip(":") or "(default)",
        "shapes": len(shapes),
        "clicks": len(plan),
        "effects": sum(len(u["paras"]) if u["kind"] == "bullet" else len(u["shapes"]) for u in plan),
        "builds": sum(1 for u in plan if u["kind"] == "bullet"),
        "preserved": sorted(preserve, key=lambda s: int(s) if s.isdigit() else 0),
        "had_timing": timing is not None,
        "plan": plan_summary(plan),
    }
    return new_xml, stats


def rewrite_pptx(
    src: Path,
    dst: Path,
    duration_ms: int = DEFAULT_DURATION_MS,
    step_delay_ms: int = 0,
    verbose: bool = True,
) -> dict:
    """Copy *src* to *dst*, replacing each slide's timing tree."""
    src = Path(src)
    dst = Path(dst)
    if src.resolve() == dst.resolve():
        raise ValueError("input and output must differ; inject never writes in place")

    dst.parent.mkdir(parents=True, exist_ok=True)
    per_slide: list[tuple[str, dict]] = []

    with zipfile.ZipFile(src, "r") as zin:
        slide_names = set(list_slide_names(zin))
        tmp_fd, tmp_name = tempfile.mkstemp(suffix=".pptx", dir=str(dst.parent))
        os.close(tmp_fd)
        try:
            with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as zout:
                for info in zin.infolist():
                    data = zin.read(info.filename)
                    if info.filename in slide_names:
                        xml_text = data.decode("utf-8")
                        try:
                            new_xml, stats = inject_slide_xml(
                                xml_text, duration_ms=duration_ms, step_delay_ms=step_delay_ms
                            )
                        except Exception as exc:  # keep the original slide on failure
                            raise RuntimeError(f"{info.filename}: {exc}") from exc
                        data = new_xml.encode("utf-8")
                        per_slide.append((info.filename, stats))
                    new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                    new_info.compress_type = (
                        info.compress_type if info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        else zipfile.ZIP_DEFLATED
                    )
                    new_info.external_attr = info.external_attr
                    new_info.internal_attr = info.internal_attr
                    new_info.create_system = info.create_system
                    zout.writestr(new_info, data)
            shutil.move(tmp_name, dst)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    if verbose:
        print(f"inject: {src.name} -> {dst.name}")
        print(f"  slides rewritten : {len(per_slide)}")
        total_clicks = sum(s['clicks'] for _, s in per_slide)
        total_effects = sum(s['effects'] for _, s in per_slide)
        print(f"  click groups     : {total_clicks}")
        print(f"  effects          : {total_effects}  (duration {duration_ms}ms, step-delay {step_delay_ms}ms)")
        for name, stats in per_slide:
            note = " (existing timing replaced)" if stats["had_timing"] else ""
            preserved = f" preserved={','.join(stats['preserved'])}" if stats["preserved"] else ""
            print(
                f"  slide {slide_number(name):>3}: prefix={stats['prefix']:<9} "
                f"clicks={stats['clicks']:<3} effects={stats['effects']:<3} "
                f"builds={stats['builds']}{preserved}{note}"
            )
            for label, text in stats["plan"]:
                print(f"        - {label:<22} {text}")
    return {"slides": per_slide}


# ==========================================================================
# Verification
# ==========================================================================


class SlideReport:
    def __init__(self, name: str) -> None:
        self.name = name
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.clicks = 0
        self.effects = 0
        self.with_effects = 0
        self.has_timing = False

    @property
    def ok(self) -> bool:
        return not self.errors


def _shape_index(shapes: list[dict]) -> dict[str, dict]:
    return {s["id"]: s for s in shapes}


def verify_slide_xml(name: str, xml_text: str, expect_click: bool = False) -> SlideReport:
    report = SlideReport(name)

    # 1. well-formedness -----------------------------------------------------
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        report.errors.append(f"slide XML is not well-formed: {exc}")
        return report

    # 2. exactly one <p:timing> ---------------------------------------------
    timing_nodes = [n for n in root.iter() if is_p(n.tag, "timing")]
    if len(timing_nodes) != 1:
        if len(timing_nodes) == 0:
            report.errors.append("no <p:timing> element found")
        else:
            report.errors.append(f"expected exactly 1 <p:timing>, found {len(timing_nodes)}")
        if expect_click:
            report.errors.append("--expect-click but the slide has no animation")
        return report
    report.has_timing = True
    timing = timing_nodes[0]

    # 3. every p:cTn/@id unique ---------------------------------------------
    ids = [n.get("id") for n in iter_desc(timing, "cTn")]
    missing = [i for i, v in enumerate(ids) if v is None]
    if missing:
        report.errors.append(f"{len(missing)} <p:cTn> element(s) missing @id")
    seen: set[str] = set()
    dupes: list[str] = []
    for value in ids:
        if value is None:
            continue
        if value in seen and value not in dupes:
            dupes.append(value)
        seen.add(value)
    if dupes:
        report.errors.append("duplicate p:cTn/@id: " + ", ".join(sorted(dupes, key=lambda s: int(s) if s.isdigit() else 0)))

    # 4. targets resolve to a shape -----------------------------------------
    shapes = collect_shapes(root)
    by_id = _shape_index(shapes)
    known = set(by_id)

    targets = [n for n in iter_desc(timing, "spTgt")]
    bldps = [n for n in iter_desc(timing, "bldP")]

    effect_targets: list[tuple[str, ET.Element]] = []
    for tgt in targets:
        spid = tgt.get("spid")
        if not spid:
            report.errors.append("<p:spTgt> without @spid")
            continue
        if spid not in known:
            report.errors.append(f"<p:spTgt spid=\"{spid}\"> matches no p:cNvPr/@id in this slide")
        effect_targets.append((spid, tgt))
    for bld in bldps:
        spid = bld.get("spid")
        if not spid:
            report.errors.append("<p:bldP> without @spid")
            continue
        if spid not in known:
            report.errors.append(f"<p:bldP spid=\"{spid}\"> matches no p:cNvPr/@id in this slide")

    # 5. effect node types + preset + filter + duration ----------------------
    effect_ctns = [n for n in iter_desc(timing, "cTn") if n.get("presetID") or n.get("presetClass") or n.get("nodeType")]
    effect_ctns = [
        n
        for n in effect_ctns
        if n.get("nodeType") not in ("tmRoot", "mainSeq")
    ]
    if not effect_ctns:
        report.errors.append("no effect nodes found (expected presetID=10 clickEffect entries)")

    for ctn in effect_ctns:
        node_type = ctn.get("nodeType")
        if node_type == "afterEffect":
            report.errors.append(
                f'p:cTn id={ctn.get("id")} uses nodeType="afterEffect" '
                "(main sequence must use clickEffect + delay=\"indefinite\")"
            )
        elif node_type not in ("clickEffect", "withEffect"):
            report.errors.append(
                f'p:cTn id={ctn.get("id")} has nodeType={node_type!r}; only clickEffect/withEffect are allowed'
            )
        else:
            if node_type == "clickEffect":
                report.clicks += 1
            else:
                report.with_effects += 1

        if ctn.get("presetID") != FADE_PRESET_ID:
            report.errors.append(
                f'p:cTn id={ctn.get("id")} presetID={ctn.get("presetID")!r}, expected "{FADE_PRESET_ID}" (Fade)'
            )
        if ctn.get("presetClass") != FADE_PRESET_CLASS:
            report.errors.append(
                f'p:cTn id={ctn.get("id")} presetClass={ctn.get("presetClass")!r}, expected "{FADE_PRESET_CLASS}"'
            )

        anim = next((n for n in ctn.iter() if is_p(n.tag, "animEffect")), None)
        if anim is None:
            report.errors.append(f'p:cTn id={ctn.get("id")} has no <p:animEffect>')
        else:
            if anim.get("filter") != FADE_FILTER:
                report.errors.append(
                    f'p:cTn id={ctn.get("id")} animEffect filter={anim.get("filter")!r}, expected "{FADE_FILTER}"'
                )
            if anim.get("transition") != "in":
                report.errors.append(
                    f'p:cTn id={ctn.get("id")} animEffect transition={anim.get("transition")!r}, expected "in"'
                )
        report.effects += 1

    # 6. main sequence must not use the afterEffect/idle shape ---------------
    for seq in iter_desc(timing, "seq"):
        ctn = next((c for c in seq if is_p(c.tag, "cTn")), None)
        if ctn is not None and ctn.get("nodeType") != "mainSeq":
            report.errors.append(f'p:seq holds p:cTn nodeType={ctn.get("nodeType")!r}, expected "mainSeq"')

    # The first p:par under mainSeq must carry delay="indefinite".
    main_seq = None
    for ctn in iter_desc(timing, "cTn"):
        if ctn.get("nodeType") == "mainSeq":
            main_seq = ctn
            break
    if main_seq is not None:
        first_group = next((c for c in main_seq.iter() if is_p(c.tag, "par")), None)
        cond = None
        if first_group is not None:
            cond = next((c for c in first_group.iter() if is_p(c.tag, "cond")), None)
        if cond is None:
            report.errors.append('mainSeq has no first click group with <p:cond delay="indefinite"/>')
        elif cond.get("delay") != "indefinite":
            report.errors.append(
                f'first click group delay={cond.get("delay")!r}, expected "indefinite" '
                "(a delay=\"0\" main sequence makes PowerPoint drop the tree)"
            )

    # 7. no decoration shape targeted ---------------------------------------
    for spid, _tgt in effect_targets:
        shape = by_id.get(spid)
        if shape is None:
            continue
        if is_decoration_shape(shape):
            report.errors.append(
                f"decoration/structure shape spid={spid} ({shape['name'] or shape['kind']}) is targeted by an effect"
            )

    # 8. paragraph build declarations ---------------------------------------
    bullet_targets: dict[str, set[int]] = {}
    for ctn in effect_ctns:
        tgt = next((n for n in ctn.iter() if is_p(n.tag, "spTgt")), None)
        if tgt is None:
            continue
        prg = next((n for n in tgt.iter() if is_p(n.tag, "pRg")), None)
        if prg is None:
            continue
        spid = tgt.get("spid")
        try:
            st, end = int(prg.get("st")), int(prg.get("end"))
        except (TypeError, ValueError):
            report.errors.append(f'sp{spid}: <p:pRg> needs integer st/end (got {prg.get("st")!r}/{prg.get("end")!r})')
            continue
        if st != end:
            report.errors.append(f"sp{spid}: <p:pRg st=\"{st}\" end=\"{end}\"> must target exactly one paragraph")
        bullet_targets.setdefault(spid, set()).add(st)

    declared = {b.get("spid") for b in bldps if b.get("spid")}
    for spid, paras in bullet_targets.items():
        if spid not in declared:
            report.errors.append(
                f'sp{spid} has per-paragraph effects but no <p:bldLst><p:bldP spid="{spid}" build="p"/>'
            )
        shape = by_id.get(spid)
        if shape is not None and shape["n_paragraphs"] and len(paras) != shape["n_paragraphs"]:
            report.errors.append(
                f"sp{spid} has {shape['n_paragraphs']} paragraphs but {len(paras)} paragraph effects"
            )
    for spid in declared:
        if spid not in bullet_targets:
            report.warnings.append(f'sp{spid} is declared in <p:bldLst> but has no per-paragraph effect')

    # 9. expect-click --------------------------------------------------------
    if expect_click and report.clicks == 0:
        report.errors.append("--expect-click but no clickEffect entry was found")

    report.notes.append(
        f"prefix={detect_presentation_prefix(xml_text)[0].rstrip(':') or '(default)'} "
        f"shapes={len(shapes)} clicks={report.clicks} with={report.with_effects} ids={len(seen)}"
    )
    if report.with_effects:
        report.notes.append("grouped units: first shape clickEffect, siblings withEffect")
    return report


def verify_pptx(path: Path, expect_click: bool = False, verbose: bool = True) -> tuple[bool, list[SlideReport]]:
    path = Path(path)
    reports: list[SlideReport] = []
    if not path.exists():
        print(f"ERROR: file not found: {path}")
        return (False, reports)
    try:
        zf = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        print(f"ERROR: not a valid zip/pptx: {exc}")
        return (False, reports)

    with zf:
        bad = zf.testzip()
        if bad is not None:
            print(f"ERROR: corrupt zip entry: {bad}")
            return (False, reports)
        names = list_slide_names(zf)
        if not names:
            print("ERROR: no ppt/slides/slideN.xml parts found")
            return (False, reports)
        for name in names:
            xml_text = zf.read(name).decode("utf-8")
            reports.append(verify_slide_xml(name, xml_text, expect_click=expect_click))

    ok = all(r.ok for r in reports)
    if verbose:
        print(f"verify: {path}")
        print(f"  zip           : valid ({len(reports)} slides)")
        print(f"  expect-click  : {'yes' if expect_click else 'no'}")
        for report in reports:
            status = "OK  " if report.ok else "FAIL"
            print(
                f"  [{status}] slide {slide_number(report.name):>3}  "
                f"clicks={report.clicks} with={report.with_effects} effects={report.effects}"
            )
            for note in report.notes:
                print(f"          . {note}")
            for err in report.errors:
                print(f"          ! {err}")
            for warn in report.warnings:
                print(f"          ~ {warn}")
        total_clicks = sum(r.clicks for r in reports)
        total_effects = sum(r.effects for r in reports)
        print(
            f"  total         : {total_clicks} click groups, {total_effects} effects, "
            f"{sum(len(r.errors) for r in reports)} error(s), "
            f"{sum(len(r.warnings) for r in reports)} warning(s)"
        )
        print(f"  result        : {'PASS' if ok else 'FAIL'}")
    return (ok, reports)


# ==========================================================================
# self-test fixture
# ==========================================================================


def _rels_xml(entries: list[tuple[str, str, str]]) -> str:
    body = "".join(
        f'<Relationship Id="{i}" Type="{t}" Target="{g}/>' for i, t, g in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{body}</Relationships>"
    )


RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def slide_xml(
    prefix: str = "p",
    title: str = "2026 年度复盘",
    subtitle: str = "AI 辅助内容生产的效率验证",
    body_paras: tuple[str, ...] = ("第一条要点", "第二条要点", "第三条要点"),
    with_chart: bool = True,
    with_takeaway: bool = True,
    page_number: str = "1 / 2",
) -> str:
    """Build a minimal slide whose prefix is configurable (p, ns0, ppt...)."""
    p = f"{prefix}:"
    a = f"a:"
    ns = (
        f'xmlns:a="{A_URI}" xmlns:r="{R_URI}" '
        f'xmlns:{prefix}="{P_URI}"'
    )
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
        f'<{p}sld {ns}><{p}cSld name="Slide"><{p}spTree>',
        f'<{p}nvGrpSpPr><{p}cNvPr id="1" name=""/><{p}cNvGrpSpPr/><{p}nvPr/></{p}nvGrpSpPr>',
        f'<{p}grpSpPr><{a}xfrm><{a}off x="0" y="0"/><{a}ext cx="0" cy="0"/>'
        f'<{a}chOff x="0" y="0"/><{a}chExt cx="0" cy="0"/></{a}xfrm></{p}grpSpPr>',
        # id 2: decoration band (no text)
        f'<{p}sp><{p}nvSpPr><{p}cNvPr id="2" name="Band 0"/><{p}cNvSpPr/><{p}nvPr/></{p}nvSpPr>'
        f'<{p}spPr><{a}xfrm><{a}off x="0" y="0"/><{a}ext cx="914400" cy="91440"/></{a}xfrm>'
        f'<{a}prstGeom prst="rect"><{a}avLst/></{a}prstGeom></{p}spPr>'
        f'<{p}txBody><{a}bodyPr/><{a}lstStyle/><{a}p/></{p}txBody></{p}sp>',
        # id 3: eyebrow (14pt, full width, top)
        f'<{p}sp><{p}nvSpPr><{p}cNvPr id="3" name="Text 1"/><{p}cNvSpPr txBox="1"/><{p}nvPr/></{p}nvSpPr>'
        f'<{p}spPr><{a}xfrm><{a}off x="514350" y="477774"/><{a}ext cx="8115300" cy="237744"/></{a}xfrm>'
        f'<{a}prstGeom prst="rect"><{a}avLst/></{a}prstGeom></{p}spPr>'
        f'<{p}txBody><{a}bodyPr/><{a}lstStyle/><{a}p><{a}r><{a}rPr lang="zh-CN" sz="1400"/>'
        f'<{a}t>01 目标</{a}t></{a}r></{a}p></{p}txBody></{p}sp>',
        # id 4: title (title placeholder, 28pt)
        f'<{p}sp><{p}nvSpPr><{p}cNvPr id="4" name="Title 2"/><{p}cNvSpPr><{p}nvPr>'
        f'<{p}ph type="title"/></{p}nvPr></{p}cNvSpPr></{p}nvSpPr>'
        f'<{p}spPr><{a}xfrm><{a}off x="514350" y="660654"/><{a}ext cx="8115300" cy="786384"/></{a}xfrm>'
        f'<{a}prstGeom prst="rect"><{a}avLst/></{a}prstGeom></{p}spPr>'
        f'<{p}txBody><{a}bodyPr/><{a}lstStyle/><{a}p><{a}r><{a}rPr lang="zh-CN" sz="2800"/>'
        f'<{a}t>{title}</{a}t></{a}r></{a}p></{p}txBody></{p}sp>',
        # id 5: subtitle (subTitle placeholder, 16pt)
        f'<{p}sp><{p}nvSpPr><{p}cNvPr id="5" name="Subtitle 3"/><{p}cNvSpPr><{p}nvPr>'
        f'<{p}ph type="subTitle" idx="1"/></{p}nvPr></{p}cNvSpPr></{p}nvSpPr>'
        f'<{p}spPr><{a}xfrm><{a}off x="514350" y="1500000"/><{a}ext cx="8115300" cy="400000"/></{a}xfrm>'
        f'<{a}prstGeom prst="rect"><{a}avLst/></{a}prstGeom></{p}spPr>'
        f'<{p}txBody><{a}bodyPr/><{a}lstStyle/><{a}p><{a}r><{a}rPr lang="zh-CN" sz="1600"/>'
        f'<{a}t>{subtitle}</{a}t></{a}r></{a}p></{p}txBody></{p}sp>',
    ]
    if with_chart:
        # ids 6-8: a 3-shape stat column (one visual unit)
        parts += [
            f'<{p}sp><{p}nvSpPr><{p}cNvPr id="6" name="Shape 4"/><{p}cNvSpPr/><{p}nvPr/></{p}nvSpPr>'
            f'<{p}spPr><{a}xfrm><{a}off x="514350" y="2011680"/><{a}ext cx="2596890" cy="1042416"/></{a}xfrm>'
            f'<{a}prstGeom prst="rect"><{a}avLst/></{a}prstGeom></{p}spPr>'
            f'<{p}txBody><{a}bodyPr/><{a}lstStyle/><{a}p/></{p}txBody></{p}sp>',
            f'<{p}sp><{p}nvSpPr><{p}cNvPr id="7" name="Text 5"/><{p}cNvSpPr txBox="1"/><{p}nvPr/></{p}nvSpPr>'
            f'<{p}spPr><{a}xfrm><{a}off x="658368" y="2194560"/><{a}ext cx="2304288" cy="438912"/></{a}xfrm>'
            f'<{a}prstGeom prst="rect"><{a}avLst/></{a}prstGeom></{p}spPr>'
            f'<{p}txBody><{a}bodyPr/><{a}lstStyle/><{a}p><{a}r><{a}rPr lang="zh-CN" sz="3000"/>'
            f'<{a}t>2.9 天</{a}t></{a}r></{a}p></{p}txBody></{p}sp>',
            f'<{p}sp><{p}nvSpPr><{p}cNvPr id="8" name="Text 6"/><{p}cNvSpPr txBox="1"/><{p}nvPr/></{p}nvSpPr>'
            f'<{p}spPr><{a}xfrm><{a}off x="658368" y="2633472"/><{a}ext cx="2304288" cy="246888"/></{a}xfrm>'
            f'<{a}prstGeom prst="rect"><{a}avLst/></{a}prstGeom></{p}spPr>'
            f'<{p}txBody><{a}bodyPr/><{a}lstStyle/><{a}p><{a}r><{a}rPr lang="zh-CN" sz="1400"/>'
            f'<{a}t>单篇平均交付周期</{a}t></{a}r></{a}p></{p}txBody></{p}sp>',
            # id 9: chart graphicFrame
            f'<{p}graphicFrame><{p}nvGraphicFramePr><{p}cNvPr id="9" name="Chart 7"/>'
            f'<{p}cNvGraphicFramePr/><{p}nvPr/></{p}nvGraphicFramePr>'
            f'<{p}xfrm><{a}off x="514350" y="3300000"/><{a}ext cx="8115300" cy="1200000"/></{p}xfrm>'
            f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart"/>'
            f'</a:graphic></{p}graphicFrame>',
        ]
    if with_takeaway:
        # id 10/11 + id 12: takeaway bar (bar + text, one unit), id 13 page number
        parts += [
            f'<{p}sp><{p}nvSpPr><{p}cNvPr id="10" name="Shape 8"/><{p}cNvSpPr/><{p}nvPr/></{p}nvSpPr>'
            f'<{p}spPr><{a}xfrm><{a}off x="514350" y="4102456"/><{a}ext cx="8115300" cy="526694"/></{a}xfrm>'
            f'<{a}prstGeom prst="rect"><{a}avLst/></{a}prstGeom></{p}spPr>'
            f'<{p}txBody><{a}bodyPr/><{a}lstStyle/><{a}p/></{p}txBody></{p}sp>',
            f'<{p}sp><{p}nvSpPr><{p}cNvPr id="11" name="Text 9"/><{p}cNvSpPr txBox="1"/><{p}nvPr/></{p}nvSpPr>'
            f'<{p}spPr><{a}xfrm><{a}off x="697230" y="4102456"/><{a}ext cx="7749540" cy="526694"/></{a}xfrm>'
            f'<{a}prstGeom prst="rect"><{a}avLst/></{a}prstGeom></{p}spPr>'
            f'<{p}txBody><{a}bodyPr/><{a}lstStyle/><{a}p><{a}r><{a}rPr lang="zh-CN" sz="1400"/>'
            f'<{a}t>效率是主线，产量与质量是约束</{a}t></{a}r></{a}p></{p}txBody></{p}sp>',
            f'<{p}sp><{p}nvSpPr><{p}cNvPr id="12" name="Text 10"/><{p}cNvSpPr txBox="1"/><{p}nvPr/></{p}nvSpPr>'
            f'<{p}spPr><{a}xfrm><{a}off x="7532370" y="4299966"/><{a}ext cx="960120" cy="292608"/></{a}xfrm>'
            f'<{a}prstGeom prst="rect"><{a}avLst/></{a}prstGeom></{p}spPr>'
            f'<{p}txBody><{a}bodyPr/><{a}lstStyle/><{a}p><{a}r><{a}rPr lang="zh-CN" sz="1400"/>'
            f'<{a}t>{page_number}</{a}t></{a}r></{a}p></{p}txBody></{p}sp>',
        ]
    # id 14: multi-paragraph bullet box (one click per paragraph)
    y = 3600000 if with_chart else 2011680
    rows = "".join(
        f'<{a}p><{a}r><{a}rPr lang="zh-CN" sz="1500"/><{a}t>{t}</{a}t></{a}r></{a}p>'
        for t in body_paras
    )
    parts.append(
        f'<{p}sp><{p}nvSpPr><{p}cNvPr id="14" name="Text 11"/><{p}cNvSpPr txBox="1"/><{p}nvPr/></{p}nvSpPr>'
        f'<{p}spPr><{a}xfrm><{a}off x="514350" y="{y}"/><{a}ext cx="8115300" cy="1000000"/></{a}xfrm>'
        f'<{a}prstGeom prst="rect"><{a}avLst/></{a}prstGeom></{p}spPr>'
        f'<{p}txBody><{a}bodyPr/><{a}lstStyle/>{rows}</{p}txBody></{p}sp>'
    )
    parts.append(f"</{p}spTree></{p}cSld></{p}sld>")
    return "".join(parts)


COVER_SLIDE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<p:sld xmlns:a="{A_URI}" xmlns:r="{R_URI}" xmlns:p="{P_URI}"><p:cSld name="Cover"><p:spTree>'
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Shape 0"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
    '<p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="9144000" cy="192024"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
    '<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
    '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Title 1"/><p:cNvSpPr><p:nvPr>'
    '<p:ph type="title"/></p:nvPr></p:cNvSpPr></p:nvSpPr>'
    '<p:spPr><a:xfrm><a:off x="514350" y="1500000"/><a:ext cx="8115300" cy="2057400"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
    '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="zh-CN" sz="4000"/>'
    '<a:t>2026 年第三季度复盘</a:t></a:r></a:p></p:txBody></p:sp>'
    '<p:sp><p:nvSpPr><p:cNvPr id="4" name="Subtitle 2"/><p:cNvSpPr><p:nvPr>'
    '<p:ph type="subTitle" idx="1"/></p:nvPr></p:cNvSpPr></p:nvSpPr>'
    '<p:spPr><a:xfrm><a:off x="514350" y="3648456"/><a:ext cx="8115300" cy="978408"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
    '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="zh-CN" sz="1600"/>'
    '<a:t>AI 辅助内容生产的效率验证</a:t></a:r></a:p></p:txBody></p:sp>'
    '</p:spTree></p:cSld></p:sld>'
)


def build_fixture(dest: Path) -> None:
    """Write a minimal but real .pptx (3 slides, one prefixed with ns0:)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    slide1 = slide_xml("p", title="把交付效率提升一倍")
    slide2 = slide_xml("ns0", title="前段省时，后段加负", with_chart=True)
    slide3_plain = COVER_SLIDE  # title + subtitle only -> subtitle carries the click

    ct = "application/vnd.openxmlformats-officedocument"
    parts = [
        ("/ppt/presentation.xml", f"{ct}.presentationml.presentation.main+xml"),
        ("/ppt/slideMasters/slideMaster1.xml", f"{ct}.presentationml.slideMaster+xml"),
        ("/ppt/slideLayouts/slideLayout1.xml", f"{ct}.presentationml.slideLayout+xml"),
        ("/ppt/slides/slide1.xml", f"{ct}.presentationml.slide+xml"),
        ("/ppt/slides/slide2.xml", f"{ct}.presentationml.slide+xml"),
        ("/ppt/slides/slide3.xml", f"{ct}.presentationml.slide+xml"),
        ("/ppt/theme/theme1.xml", f"{ct}.theme+xml"),
    ]
    overrides = "".join(
        f'<Override PartName="{p}" ContentType="{c}"/>' for p, c in parts
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{overrides}</Types>"
    )

    presentation = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<p:presentation xmlns:a="{A_URI}" xmlns:r="{R_URI}" xmlns:p="{P_URI}" saveSubsetFonts="1">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        '<p:sldIdLst>'
        '<p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId3"/><p:sldId id="258" r:id="rId4"/>'
        "</p:sldIdLst>"
        '<p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
        "</p:presentation>"
    )

    theme = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<a:theme xmlns:a="{A_URI}" name="Office"><a:themeElements>'
        '<a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
        '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
        '<a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
        '<a:accent1><a:srgbClr val="4472C4"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2>'
        '<a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4>'
        '<a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6>'
        '<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
        "</a:clrScheme>"
        '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri Light"/>'
        '<a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
        '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
        "</a:fontScheme>"
        '<a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        "</a:fillStyleLst>"
        "<a:lnStyleLst><a:ln><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:ln>"
        "<a:ln><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:ln>"
        "<a:ln><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:ln></a:lnStyleLst>"
        "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle>"
        "</a:effectStyleLst>"
        "<a:bgFillStyleLst><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill>"
        "<a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill>"
        "<a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:bgFillStyleLst>"
        "</a:fmtScheme></a:themeElements></a:theme>"
    )

    master = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<p:sldMaster xmlns:a="{A_URI}" xmlns:r="{R_URI}" xmlns:p="{P_URI}">'
        '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
        '</p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld>'
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2"'
        ' accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6"'
        ' hlink="hlink" folHlink="folHlink"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        "</p:sldMaster>"
    )

    layout = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<p:sldLayout xmlns:a="{A_URI}" xmlns:r="{R_URI}" xmlns:p="{P_URI}" type="blank">'
        '<p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/>'
        '<p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    )

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", _rels_xml([("rId1", f"{RT}/officeDocument", "ppt/presentation.xml")]))
        zf.writestr("ppt/presentation.xml", presentation)
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            _rels_xml(
                [
                    ("rId1", f"{RT}/slideMaster", "slideMasters/slideMaster1.xml"),
                    ("rId2", f"{RT}/slide", "slides/slide1.xml"),
                    ("rId3", f"{RT}/slide", "slides/slide2.xml"),
                    ("rId4", f"{RT}/slide", "slides/slide3.xml"),
                    ("rId5", f"{RT}/theme", "theme/theme1.xml"),
                ]
            ),
        )
        zf.writestr("ppt/theme/theme1.xml", theme)
        zf.writestr("ppt/slideMasters/slideMaster1.xml", master)
        zf.writestr(
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            _rels_xml(
                [
                    ("rId1", f"{RT}/slideLayout", "../slideLayouts/slideLayout1.xml"),
                    ("rId2", f"{RT}/theme", "../theme/theme1.xml"),
                ]
            ),
        )
        zf.writestr("ppt/slideLayouts/slideLayout1.xml", layout)
        zf.writestr(
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            _rels_xml(
                [
                    ("rId1", f"{RT}/slideMaster", "../slideMasters/slideMaster1.xml"),
                    ("rId2", f"{RT}/theme", "../theme/theme1.xml"),
                ]
            ),
        )
        for idx, xml in enumerate((slide1, slide2, slide3_plain), start=1):
            zf.writestr(f"ppt/slides/slide{idx}.xml", xml)
            zf.writestr(
                f"ppt/slides/_rels/slide{idx}.xml.rels",
                _rels_xml(
                    [
                        ("rId1", f"{RT}/slideLayout", "../slideLayouts/slideLayout1.xml"),
                        ("rId2", f"{RT}/theme", "../theme/theme1.xml"),
                    ]
                ),
            )


# ==========================================================================
# self-test
# ==========================================================================


def _self_test(workdir: Path) -> bool:
    workdir = Path(workdir)
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)

    checks: list[tuple[str, bool, str]] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        checks.append((label, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))

    fixture = workdir / "fixture.pptx"
    out1 = workdir / "animated.pptx"
    out2 = workdir / "animated-twice.pptx"

    print("build fixture")
    build_fixture(fixture)
    check("fixture written", fixture.exists() and fixture.stat().st_size > 0, f"{fixture.name}")
    with zipfile.ZipFile(fixture) as zf:
        check("fixture is a valid zip", zf.testzip() is None)
        check("fixture has 3 slides", len(list_slide_names(zf)) == 3)

    # --- a raw slide XML must contain ZERO timing elements -----------------
    with zipfile.ZipFile(fixture) as zf:
        raw = zf.read("ppt/slides/slide1.xml").decode("utf-8")
    check("fixture slide has no <p:timing>", "<p:timing" not in raw)

    print("inject")
    rewrite_pptx(fixture, out1, verbose=False)
    check("injected file written", out1.exists() and out1.stat().st_size > 0)

    with zipfile.ZipFile(out1) as zf:
        check("injected file is a valid zip", zf.testzip() is None)
        s1 = zf.read("ppt/slides/slide1.xml").decode("utf-8")
        s2 = zf.read("ppt/slides/slide2.xml").decode("utf-8")
        s3 = zf.read("ppt/slides/slide3.xml").decode("utf-8")

    # --- structure assertions ---------------------------------------------
    check("exactly one timing per slide",
          all(x.count(":timing>") + x.count(":timing ") >= 1 and
              len(re.findall(r"<\w+:timing[ >]", x)) == 1 for x in (s1, s2, s3)))

    root1 = ET.fromstring(s1)
    timing1 = find_child(root1, "timing")
    ids1 = [c.get("id") for c in iter_desc(timing1, "cTn")]
    check("p:cTn ids unique on slide 1", len(ids1) == len(set(ids1)) and None not in ids1,
          f"{len(ids1)} ids: {','.join(ids1)}")
    check("tmRoot=1 and mainSeq=2",
          [c.get("id") for c in iter_desc(timing1, "cTn") if c.get("nodeType") == "tmRoot"] == ["1"]
          and [c.get("id") for c in iter_desc(timing1, "cTn") if c.get("nodeType") == "mainSeq"] == ["2"])

    ids2 = [c.get("id") for c in iter_desc(find_child(ET.fromstring(s2), "timing"), "cTn")]
    check("p:cTn ids unique on slide 2", len(ids2) == len(set(ids2)) and None not in ids2, f"{len(ids2)} ids")

    # --- prefix detection --------------------------------------------------
    check("slide1 keeps the p: prefix", s1.rstrip().endswith("</p:sld>") and "<p:timing>" in s1)
    check("slide2 detects and keeps ns0: prefix",
          s2.rstrip().endswith("</ns0:sld>") and "<ns0:timing>" in s2 and "<p:timing>" not in s2)

    # --- paragraph building -----------------------------------------------
    check("multi-paragraph box declares bldLst build=\"p\"", '<p:bldLst><p:bldP spid="14" grpId="0" build="p"/></p:bldLst>' in s1)
    # Each paragraph effect carries the pRg twice: once in p:set, once in p:animEffect.
    prg = re.findall(r'<p:pRg st="(\d+)" end="(\d+)"/>', s1)
    check("3 paragraph effects (st==end) for the 3-paragraph box",
          prg == [("0", "0")] * 2 + [("1", "1")] * 2 + [("2", "2")] * 2, f"{prg}")
    check("every paragraph effect targets the same spid",
          len(set(re.findall(r'<p:spTgt spid="(\d+)"><p:txEl>', s1))) == 1
          and set(re.findall(r'<p:spTgt spid="(\d+)"><p:txEl>', s1)) == {"14"})

    # --- first click group waits for the speaker --------------------------
    check('first click group uses delay="indefinite"', '<p:cond delay="indefinite"/>' in s1)

    # --- single-paragraph unit grouping -----------------------------------
    # ids 7,8 stack in one column -> one click (clickEffect + withEffect, same grpId)
    grp = re.findall(r'fill="hold" grpId="(\d+)" nodeType="(clickEffect|withEffect)"', s2)
    check("no afterEffect anywhere", "afterEffect" not in s1 + s2 + s3)
    check("grouped siblings use withEffect with a shared grpId",
          len(grp) >= 3 and grp[0] == ("0", "clickEffect") and grp[1] == ("0", "withEffect")
          and grp[2] == ("0", "withEffect"),
          f"{grp}")
    # Contract: the FIRST effect of every click group is clickEffect; the rest of
    # that group is withEffect.  (A 3-paragraph bullet box is three groups.)
    per_group: dict[str, list[str]] = {}
    for grp_id, node_type in grp:
        per_group.setdefault(grp_id, []).append(node_type)
    check("each click group starts with clickEffect, siblings withEffect",
          all(types[0] == "clickEffect" and all(t == "withEffect" for t in types[1:])
              for types in per_group.values()),
          f"{per_group}")

    # --- cover fallback: title+subtitle => subtitle carries the click ------
    s3t = find_child(ET.fromstring(s3), "timing")
    tgts3 = [t.get("spid") for t in iter_desc(s3t, "spTgt")]
    check("cover: subtitle (spid 4) carries the single click, title (spid 3) static",
          set(tgts3) == {"4"}, f"targets={sorted(set(tgts3))}")

    # --- decoration never targeted ----------------------------------------
    for label, xm in (("slide1", s1), ("slide2", s2), ("slide3", s3)):
        r = ET.fromstring(xm)
        shapes = {s["id"]: s for s in collect_shapes(r)}
        tgt_ids = {t.get("spid") for t in iter_desc(find_child(r, "timing"), "spTgt")}
        bad = [i for i in tgt_ids if i in shapes and is_decoration_shape(shapes[i])]
        check(f"{label}: no decoration targeted", not bad, f"targets={sorted(tgt_ids)}")

    # --- verification tool agrees -----------------------------------------
    ok1, reports1 = verify_pptx(out1, expect_click=True, verbose=False)
    check("verify --expect-click passes on the injected fixture", ok1,
          "; ".join(e for r in reports1 for e in r.errors))

    # --- idempotency -------------------------------------------------------
    print("inject again (idempotency)")
    rewrite_pptx(out1, out2, verbose=False)
    with zipfile.ZipFile(out2) as zf:
        t1 = zf.read("ppt/slides/slide1.xml").decode("utf-8")
        t2 = zf.read("ppt/slides/slide2.xml").decode("utf-8")
        t3 = zf.read("ppt/slides/slide3.xml").decode("utf-8")
    check("no second <p:timing> after re-inject",
          all(len(re.findall(r"<\w+:timing[ >]", x)) == 1 for x in (t1, t2, t3)))
    check("re-inject is byte-identical", (t1, t2, t3) == (s1, s2, s3))
    ok2, _ = verify_pptx(out2, expect_click=True, verbose=False)
    check("verify passes on the twice-injected file", ok2)

    # --- duration / step-delay plumbing -----------------------------------
    out3 = workdir / "animated-800ms.pptx"
    rewrite_pptx(fixture, out3, duration_ms=800, step_delay_ms=40, verbose=False)
    with zipfile.ZipFile(out3) as zf:
        s = zf.read("ppt/slides/slide1.xml").decode("utf-8")
    check("--duration-ms reaches p:animEffect", s.count('dur="800"') >= 1)
    check("--step-delay-ms reaches effect start conditions", s.count('<p:cond delay="40"/>') >= 1)

    # --- corrupt input must be rejected -----------------------------------
    bad = workdir / "bad.pptx"
    build_fixture(bad)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # deliberate duplicate part
        with zipfile.ZipFile(bad, "a") as zf:
            zf.writestr("ppt/slides/slide2.xml", "<p:sld><p:cSld></p:sld>")
    ok3, reports3 = verify_pptx(bad, expect_click=False, verbose=False)
    check("verify FAILS on malformed slide XML", not ok3)

    # --- duplicated cTn id must be caught ----------------------------------
    dup = workdir / "dup-id.pptx"
    rewrite_pptx(fixture, dup, verbose=False)
    with zipfile.ZipFile(dup) as zf:
        s = zf.read("ppt/slides/slide1.xml").decode("utf-8")
    # Collide the first click group's id (3) with tmRoot's id (1), inside the
    # timing tree only -- p:cNvPr ids live outside it.
    head, sep, tail = s.partition("<p:timing>")
    tail_dup, n_sub = re.subn(r'id="3"', 'id="1"', tail, count=1)
    s_dup = head + sep + tail_dup
    check("duplicate-id fixture actually changed the XML", s_dup != s and n_sub == 1)
    dup2 = workdir / "dup-id2.pptx"
    with zipfile.ZipFile(dup, "r") as zin, zipfile.ZipFile(dup2, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "ppt/slides/slide1.xml":
                data = s_dup.encode("utf-8")
            zout.writestr(info, data)
    ok4, reports4 = verify_pptx(dup2, expect_click=False, verbose=False)
    check("verify FAILS on duplicate p:cTn/@id", not ok4)
    check("duplicate id is named in the report",
          any("duplicate p:cTn/@id: 1" in e for r in reports4 for e in r.errors),
          "; ".join(e for r in reports4 for e in r.errors)[:110])

    # --- afterEffect main sequence must fail ------------------------------
    aef = workdir / "after-effect.pptx"
    with zipfile.ZipFile(dup, "r") as zin, zipfile.ZipFile(aef, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "ppt/slides/slide1.xml":
                t = data.decode("utf-8")
                t = t.replace('nodeType="clickEffect"', 'nodeType="afterEffect"', 1)
                t = t.replace('<p:cond delay="indefinite"/>', '<p:cond delay="0"/>', 1)
                data = t.encode("utf-8")
            zout.writestr(info, data)
    ok5, reports5 = verify_pptx(aef, expect_click=False, verbose=False)
    check("verify FAILS on afterEffect + delay=0 main sequence", not ok5,
          "; ".join(e for r in reports5 for e in r.errors)[:90])

    # --- decoration targeted must fail ------------------------------------
    dec = workdir / "decoration-target.pptx"
    with zipfile.ZipFile(dup, "r") as zin, zipfile.ZipFile(dec, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "ppt/slides/slide1.xml":
                data = data.decode("utf-8").replace('spid="14"', 'spid="2"').encode("utf-8")
            zout.writestr(info, data)
    ok6, reports6 = verify_pptx(dec, expect_click=False, verbose=False)
    check("verify FAILS when a decoration shape is targeted", not ok6,
          "; ".join(e for r in reports6 for e in r.errors)[:90])

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print()
    print(f"self-test: {passed}/{total} checks passed")
    print(f"self-test: {'PASS' if passed == total else 'FAIL'}")
    print(f"artifacts: {workdir}")
    return passed == total


# ==========================================================================
# CLI
# ==========================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pptx_animate",
        description="Add click-triggered Fade entrance animations to a .pptx by editing OOXML.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inject = sub.add_parser("inject", help="rewrite a .pptx with click-triggered Fade animations")
    p_inject.add_argument("input", type=Path, help="source .pptx (never modified)")
    p_inject.add_argument("output", type=Path, help="destination .pptx")
    p_inject.add_argument("--duration-ms", type=int, default=DEFAULT_DURATION_MS,
                          help=f"Fade duration in ms (default {DEFAULT_DURATION_MS})")
    p_inject.add_argument("--step-delay-ms", type=int, default=0,
                          help="delay applied to each effect inside a click group (default 0)")
    p_inject.add_argument("--quiet", action="store_true", help="suppress the per-slide report")

    p_verify = sub.add_parser("verify", help="check the animation contract and report per slide")
    p_verify.add_argument("file", type=Path, help=".pptx to inspect")
    p_verify.add_argument("--expect-click", action="store_true",
                          help="fail when a slide has no clickEffect entry")

    p_self = sub.add_parser("self-test", help="build a fixture, inject, verify, and report PASS/FAIL")
    p_self.add_argument("--workdir", type=Path, default=Path(".test-tmp") / "pptx-animate-selftest",
                        help="scratch directory for the fixture")
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "inject":
        if args.duration_ms <= 0:
            print("ERROR: --duration-ms must be > 0")
            return 2
        if args.step_delay_ms < 0:
            print("ERROR: --step-delay-ms must be >= 0")
            return 2
        if not args.input.exists():
            print(f"ERROR: input not found: {args.input}")
            return 2
        try:
            rewrite_pptx(
                args.input,
                args.output,
                duration_ms=args.duration_ms,
                step_delay_ms=args.step_delay_ms,
                verbose=not args.quiet,
            )
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 1
        ok, _ = verify_pptx(args.output, expect_click=False, verbose=True)
        return 0 if ok else 1

    if args.command == "verify":
        ok, _ = verify_pptx(args.file, expect_click=args.expect_click, verbose=True)
        return 0 if ok else 1

    if args.command == "self-test":
        return 0 if _self_test(args.workdir) else 1

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
