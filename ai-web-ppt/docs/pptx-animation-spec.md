# PPTX Click-Animation XML Contract

How to add **click-triggered Fade entrance animations** to an existing `.pptx` by editing
the underlying OOXML. `python-pptx` exposes no animation API, so the timing tree is
written directly into each slide part.

This document is the normative contract. A conforming implementation in any language
(Node, Python, Go) must produce the same **structure** — element names, attributes,
nesting, id allocation, and value literals below are exact. Reference implementation:
[`tools/pptx_animate.py`](../tools/pptx_animate.py).

---

## 1. Where the tree goes

`ppt/slides/slideN.xml` is a `p:sld` part:

```xml
<p:sld …>
  <p:cSld> … <p:spTree> … </p:spTree> </p:cSld>
  <p:clrMapOvr> … </p:clrMapOvr>
  <p:timing> … </p:timing>   <!-- LAST child of p:sld -->
</p:sld>
```

Rules:

* The `<p:timing>` element is inserted as the **last child of `p:sld`**, immediately
  before `</p:sld>`, after `<p:clrMapOvr>`.
* **Exactly one** `<p:timing>` per slide. If one already exists (the deck was already
  animated), it is **replaced**, never appended. Removing the old element and appending a
  new one is what makes `inject` idempotent.
* **Never assume the `p:` prefix.** Prefixes are declared on the root and are arbitrary
  (`<p:sld xmlns:p="…presentationml…">`, `<ns0:sld xmlns:ns0="…">`, or presentationml as
  the *default* namespace with no prefix at all). Resolve the presentationml namespace
  URI to whatever prefix is in scope and emit every element with that prefix. Parse the
  XML to find the namespace; do not string-match `"p:"`.
* `p:bldLst` is the last child of `p:timing`, after `p:tnLst` (see §4).

---

## 2. The timing skeleton

Whitespace between elements is insignificant. `<p:timing>`:

```xml
<p:timing><p:tnLst><p:par>
 <p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>
  <p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>

   <!-- FIRST click group: waits for the speaker -->
   <p:par><p:cTn id="3" fill="hold">
     <p:stCondLst><p:cond delay="indefinite"/></p:stCondLst>
     <p:childTnLst>
       <p:par><p:cTn id="4" fill="hold">
         <p:stCondLst><p:cond delay="0"/></p:stCondLst>
         <p:childTnLst>
           <!-- effect(s) -->
         </p:childTnLst>
       </p:cTn></p:par>
     </p:childTnLst>
   </p:cTn></p:par>

   <!-- LATER click groups: same shape, outer cond delay="0" -->
   <p:par><p:cTn id="…" fill="hold">
     <p:stCondLst><p:cond delay="0"/></p:stCondLst>
     <p:childTnLst>
       <p:par><p:cTn id="…" fill="hold">
         <p:stCondLst><p:cond delay="0"/></p:stCondLst>
         <p:childTnLst>…</p:childTnLst>
       </p:cTn></p:par>
     </p:childTnLst>
   </p:cTn></p:par>

  </p:childTnLst></p:cTn></p:seq>
 </p:childTnLst></p:cTn>
</p:par></p:tnLst></p:timing>
```

Structural facts:

* Two nested `p:par`/`p:cTn` levels per click group. The **outer** `p:cTn` carries the
  click condition; the **inner** `p:cTn` (always `fill="hold"`, `delay="0"`) carries the
  effects in its `p:childTnLst`.
* The outer condition is `delay="indefinite"` for the **first** click group (wait for the
  speaker) and `delay="0"` for every later group (chain from the previous click).

### 2.0 Optional `p:prevCondLst` / `p:nextCondLst`

After the click groups, inside the `mainSeq` `p:cTn`, an implementation may emit the empty
advance conditions PowerPoint itself round-trips (`p:seq` accepts
`cTn, prevCondLst?, nextCondLst?`):

```xml
<p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>
<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>
```

`tools/pptx_animate.py` omits this pair (PowerPoint accepts the tree without it);
`server/pptxAnimate.js` emits it. It is **not** a substitute for the click conditions in
§2 — omitting the `indefinite` first-group condition is failure mode §6.2.

### 2.1 The effect element

One effect, targeting `spid="N"`:

```xml
<p:par><p:cTn id="5" presetID="10" presetClass="entr" presetSubtype="0"
              fill="hold" grpId="0" nodeType="clickEffect">
  <p:stCondLst><p:cond delay="0"/></p:stCondLst>
  <p:childTnLst>
    <p:set><p:cBhvr>
      <p:cTn id="6" dur="1" fill="hold">
        <p:stCondLst><p:cond delay="0"/></p:stCondLst>
      </p:cTn>
      <p:tgtEl><p:spTgt spid="N"/></p:tgtEl>
      <p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst>
    </p:cBhvr><p:to><p:strVal val="visible"/></p:to></p:set>
    <p:animEffect transition="in" filter="fade"><p:cBhvr>
      <p:cTn id="7" dur="350"/>
      <p:tgtEl><p:spTgt spid="N"/></p:tgtEl>
    </p:cBhvr></p:animEffect>
  </p:childTnLst>
</p:cTn></p:par>
```

Fixed values — **Fade is the only effect type**:

| Attribute | Value |
|---|---|
| `p:cTn/@presetID` | `10` (Fade) |
| `p:cTn/@presetClass` | `entr` |
| `p:cTn/@presetSubtype` | `0` |
| `p:cTn/@fill` | `hold` |
| `p:animEffect/@transition` | `in` |
| `p:animEffect/@filter` | `fade` |
| fade duration | `p:animEffect/p:cBhvr/p:cTn/@dur` (default `350` ms) |
| `p:set` visibility `dur` | `1` (literal) |

* `nodeType` is `clickEffect` for the **first** effect of a click group and `withEffect`
  for every additional effect in that same group.
* `grpId` is a **0-based counter over click groups**. Every effect in one click group
  shares one `grpId`, and the next click group increments it.
* Each effect emits the `p:spTgt` **twice** — once inside `p:set/p:cBhvr/p:tgtEl` and
  once inside `p:animEffect/p:cBhvr/p:tgtEl`. This is required; do not "deduplicate" it.

### 2.2 Id allocation

Ids start at 1 and **must be unique within one slide's timing tree**.

| Node | Ids consumed |
|---|---|
| `tmRoot` | `1` (always) |
| `mainSeq` | `2` (always) |
| each click group | `2` — outer `p:cTn`, inner `p:cTn` |
| each effect | `3` — effect `p:cTn`, `p:set`/`p:cTn`, `p:animEffect`/`p:cTn` |

So group *g* (0-based) with *k* effects claims `2 + 3k` ids, and the next id is a running
counter. Reference: group 0 with 1 effect uses ids 3–7; group 1 then starts at 8; a
3-paragraph build box (3 groups × 1 effect) consumes 3–7, 8–12, 13–17.

A duplicate `p:cTn/@id` anywhere in one slide makes PowerPoint **silently drop the entire
animation tree** — see §6.

---

## 3. Which shapes get animated

Shapes are the direct children of `p:spTree`: `p:sp`, `p:pic`, `p:graphicFrame`
(charts/tables), `p:cxnSp`. `p:cNvPr/@id` is the `spid` referenced by effects. Decoration
carries no `p:cNvPr` of its own in the tree — `p:nvGrpSpPr`/`p:grpSpPr` are not shapes.

### 3.1 Static — never animated

1. **Decoration / structure**: any shape with no text that is not a `p:pic` and not a
   `p:graphicFrame`. (Bars, rules, background panels, accent shapes, `p:cxnSp`.)
2. **The title**: `p:ph/@type` of `title` or `ctrTitle`.
3. **The subtitle**: `p:ph/@type` `subTitle` — *except* as the §3.4 fallback.
4. **The eyebrow label**: the full-width bar/line across the very top of the slide.
5. **Small text**: any text run with `sz` ≤ **10 pt** (1000 hundredths) — page number,
   source line, unit caption. A shape whose largest explicit run size is ≤ 10 pt is static.
   When a run declares no `sz`, inherit the default (18 pt) rather than guessing.
6. Any other shape that is not `p:sp`, `p:pic`, or `p:graphicFrame`.

Everything else is **revealable** and must be covered by exactly one click group, in the
order of §3.3.

### 3.2 Per-unit rules

* **Multi-paragraph text box** — a revealable `p:sp` with **> 1 paragraph** (`a:p` count in
  `p:txBody`): one click **per paragraph**. Mark the shape as a paragraph build and give
  each paragraph its own click group whose effect targets that paragraph:

  ```xml
  <p:spTgt spid="N"><p:txEl><p:pRg st="i" end="i"/></p:txEl></p:spTgt>
  ```

  `st` and `end` are the **0-based** paragraph index; `st == end` always (one paragraph per
  effect). The first paragraph's effect is `clickEffect`, and because **each paragraph is
  its own click group**, every paragraph's first (only) effect is `clickEffect` and each
  gets its own `grpId`.

* **Single-paragraph text boxes forming one visual unit** — one click for the whole group.
  First shape `clickEffect`, the rest `withEffect`, all sharing one `grpId`. Grouping
  requires **all three**:
  1. **same column** — horizontal centres agree within **0.10 in**;
  2. **tight vertical stacking** — the vertical gap between *consecutive members of that
     column* is within **±0.20 in** (boxes may overlap slightly);
  3. **horizontal overlap ≥ 60%** of the narrower box.

  Resolve the **column first**, then evaluate stacking only between consecutive members of
  that column. Sorting everything by `y` alone interleaves the rows of a multi-row grid and
  wrongly chains one row onto the next.

* **graphicFrame** (chart/table) — one click.
* **pictures** (`p:pic`) — one click each.
* **takeaway / conclusion bar** — the last click (see §3.3).

### 3.3 Click order

1. multi-paragraph builds (each paragraph, in shape order);
2. grouped single-paragraph units — reading order (topmost, then leftmost);
3. graphicFrames;
4. pictures;
5. **the takeaway / conclusion bar**, always the final click.

Within a class, sort by reading order using the **topmost, then leftmost** member of the
unit. The takeaway is the lowest wide single-shape text unit in the lower part of the slide
(top ≥ 55% of slide height, width ≥ 50% of slide width) — deliberately excluding the
narrow, right-aligned page number.

### 3.4 Empty case

If nothing else is revealable (a cover with only a title + subtitle), the **subtitle**
carries the single click.

Decks that use no `p:ph` placeholder markup (a common exporter output) need the title
inferred: when the only revealable content is one tight stack of display text, animate the
lines *below* the topmost one.

---

## 4. Paragraph build declarations

A shape that is clicked paragraph-by-paragraph **must** also be declared in `p:bldLst`, the
last child of `p:timing`:

```xml
<p:timing><p:tnLst> … </p:tnLst><p:bldLst>
  <p:bldP spid="N" grpId="0" build="p"/>
</p:bldLst></p:timing>
```

`build="p"` marks a per-paragraph build; `grpId` ties the entry to the shape's animation
group. `p:bldP/@spid` must match a `p:cNvPr/@id` in the same slide.

Two variants exist in this repo (both are schema-valid; pick one per implementation):

* **minimal** (`tools/pptx_animate.py`) — declare **only** shapes that are built
  paragraph-by-paragraph. `p:bldLst` is omitted entirely when there are no builds.
* **expanded** (`server/pptxAnimate.js`) — declare **every** animated shape, adding
  `uiExpand="1"` to per-paragraph entries:

  ```xml
  <p:bldP spid="4" grpId="0" uiExpand="1" build="p"/>   <!-- per-paragraph build -->
  <p:bldP spid="9" grpId="0"/>                          <!-- whole-shape effect -->
  ```

On re-injection, carry forward declarations for shapes that are still built.

### 4.1 `grpId` scoping

Two scopings are in use and both satisfy "unique within the shape's own groups":

* **slide-global counter** (`tools/pptx_animate.py`) — increments once per click group
  across the whole slide, so `grpId` values are unique on the slide.
* **per-shape counter** (`server/pptxAnimate.js`) — `0` for every whole-shape effect, and
  `0, 1, 2 …` for the paragraphs of one build, matching the shape's `p:bldP` entry.
  ECMA-376 4.6.16: *"GroupIDs are unique for a given shape. They are not guaranteed to be
  unique IDs across all shapes on a slide."*

`grpId` has **no** relationship to `p:cTn/@id`; the two numbering schemes are independent.

---

## 5. Verification checklist

Per slide, all of the following must hold:

1. the slide XML is well-formed (`xml.etree.ElementTree` parses the whole part);
2. **exactly one** `<p:timing>`;
3. every `p:cTn/@id` is present and **unique**;
4. every `p:spTgt/@spid` and `p:bldP/@spid` matches a `p:cNvPr/@id` present in that slide;
5. only `clickEffect`/`withEffect` node types appear — **no `afterEffect`**;
6. every effect has `presetID="10"`, `presetClass="entr"`, and `filter="fade"`;
7. no decoration shape (no text, not `p:pic`, not `p:graphicFrame`) is targeted;
8. the first click group's condition is `delay="indefinite"`;
9. each multi-paragraph shape has a `p:bldP` and one `p:pRg` effect per paragraph.

Reference `verify` output for the shipped `C-真实模型-单击动画.pptx`: 7 slides, 43 click
groups, 54 effects, **0 errors**.

---

## 6. The two failure modes that silently kill the tree

PowerPoint does not report an error for either. The animations simply do not play.

### 6.1 Duplicate `p:cTn/@id`

Ids must be unique **within one slide's timing tree**. A collision makes PowerPoint discard
the tree silently. Two ways this happens in practice:

* allocating ids **per click group** instead of from one running counter, so group *n*
  restarts at a fixed base and collides with group *n−1*;
* appending a second tree to a slide that already had one, so both contain ids `1` and `2`.

The second is the idempotency trap: **never add a second `<p:timing>`** — remove the
existing one first (or skip the slide). A slide with two timing elements is invalid and can
present as "the animation vanished".

*Note:* `p:cNvPr/@id` values are a **different id space** and may legitimately equal timing
ids. Only `p:cTn/@id` must be unique.

### 6.2 `afterEffect` with a `delay="0"` main sequence

An entrance must be driven by the **main sequence** waiting for a click:

* the first click group's outer condition must be `<p:cond delay="indefinite"/>` — "wait
  for the speaker";
* effect nodes must be `nodeType="clickEffect"` (first of a group) or `"withEffect"`
  (siblings), never `afterEffect`.

Writing `nodeType="afterEffect"` together with `<p:cond delay="0"/>` produces a sequence
that tries to fire every effect at time 0 with no trigger. PowerPoint drops it: nothing
plays and the Animation Pane looks empty. This is exactly the shape of the shipped
`A-入场动画.pptx`, which is why `verify` rejects it — 32 `afterEffect` nodes, zero click
groups, 326 errors.

`withEffect` is valid **only** for the 2nd..nth effect *inside one click group*; it must
never replace a group's leading `clickEffect`.

---

## 7. Reference implementation notes

`tools/pptx_animate.py` — standard library only (`zipfile`, `re`,
`xml.etree.ElementTree`, `argparse`, `pathlib`).

```
python tools/pptx_animate.py inject <input.pptx> <output.pptx> [--duration-ms 350] [--step-delay-ms 0]
python tools/pptx_animate.py verify <file.pptx> [--expect-click]
python tools/pptx_animate.py self-test [--workdir DIR]
```

* `inject` rewrites the `.pptx` as bytes. Slide parts are decoded and encoded as **UTF-8**
  and never routed through the console code page, so Chinese/CJK content round-trips
  exactly. Output is assembled in a temp file and moved into place, so a failure cannot
  leave a corrupt `.pptx`.
* `inject` replaces any existing `<p:timing>` (idempotent: it never leaves a second one)
  and re-applies the contract's static rules, so a deck whose existing animation targets
  decoration comes out spec-compliant. Re-injecting a deck **this tool produced** is
  byte-identical; re-injecting a hand-authored or third-party tree re-derives it from the
  contract instead (the shipped `C-真实模型-单击动画.pptx` is already spec-compliant but
  groups some units differently, so its tree is rebuilt to 37 click groups).
* `--step-delay-ms` writes that delay into each effect's start condition
  (`p:cTn/p:stCondLst/p:cond/@delay`) and `--duration-ms` into the `p:animEffect` duration.
* `verify` exits non-zero when any slide fails, and `--expect-click` additionally fails a
  slide with no `clickEffect`.
* `self-test` builds a real 3-slide fixture (one slide prefixed `ns0:` rather than `p:`),
  injects, verifies, re-injects to prove idempotency, and asserts that malformed XML,
  duplicate ids, `afterEffect`, and decoration targeting are all rejected.
