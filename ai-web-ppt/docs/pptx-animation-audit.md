# PPTX animation audit: is the injected `<p:timing>` what PowerPoint itself writes?

Scope: `server/pptxAnimate.js` — the post-processor that injects click-triggered
entrance animations into every `ppt/slides/slideN.xml` of a pptxgenjs export.

The reported failure was "F5 shows no animation at all". Two causes had already been
found and fixed (`p:cTn/@id` collisions; `nodeType="afterEffect"` combined with a
`delay="0"` main sequence). WPS Presentation then read the animation pane back
correctly (`slide2 effects: 10 OnPageClick=10`), so the tree *works* — but WPS is more
permissive than PowerPoint. This document audits the injected tree against the
ECMA-376 schemas PowerPoint actually enforces, and records what was changed.

Evidence model: every rule below is quoted from the PresentationML schema
(`c-rex.net`'s ECMA-376 Part 4 mirror and the `python-pptx` schema excerpts, both
derived from ISO/IEC 29500-1), plus the reference behaviour of
`tools/pptx_animate.py` and of genoffice's pptx engine. Nothing here was verified by
opening the file in PowerPoint itself — see "What is still unverified".

## Ranked findings

### 1. `p:seq` was missing its `prevCondLst` / `nextCondLst` pair (most likely to matter)

`CT_TLTimeNodeSequence` is a sequence with optional members:

```
<complexType name="CT_TLTimeNodeSequence">
  <sequence>
    <element name="cTn" type="CT_TLCommonTimeNodeData" minOccurs="1" maxOccurs="1"/>
    <element name="prevCondLst" type="CT_TLTimeConditionList" minOccurs="0" maxOccurs="1"/>
    <element name="nextCondLst" type="CT_TLTimeConditionList" minOccurs="0" maxOccurs="1"/>
  </sequence>
  <attribute name="concurrent" type="xsd:boolean" use="optional"/>
  <attribute name="prevAc" type="ST_TLPreviousActionType" use="optional"/>
  <attribute name="nextAc" type="ST_TLNextActionType" use="optional"/>
</complexType>
```

The emitted tree had only `cTn`, so the sequence was schema-valid but told PowerPoint
nothing about how to advance. PowerPoint writes the pair and round-trips it:

```xml
<p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>
<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>
```

An explicit "the next action is a slide-level click" condition is precisely what makes
a *click* advance the sequence rather than the engine seeking to the sequence's natural
end time (`nextAc="seek"`). **Fixed**: both lists are now emitted, in schema order
after `cTn`.

### 2. `grpId` was a click-group index, so no effect matched a build entry

`p:cTn/@grpId` is documented only as "the Group ID of the time node"
(unsignedInt), and `p:bldP/@grpId` as: *"This attribute ties effects persisted in the
animation to the build information. The attribute is used by the editor when changes to
the build information are made. GroupIDs are unique for a given shape. They are not
guaranteed to be unique IDs across all shapes on a slide."* (ECMA-376 4.6.16)

The old code used the **step index**:

```xml
<p:cTn ... grpId="0" nodeType="clickEffect">   <!-- step 0 -->
<p:cTn ... grpId="1" nodeType="clickEffect">   <!-- step 1 -->
<p:cTn ... grpId="2" nodeType="clickEffect">   <!-- step 2: third paragraph of spid 2 -->
<p:bldP spid="2" grpId="0" build="p"/>          <!-- the only build entry -->
```

So paragraph effects 1 and 2 of a build carried a `grpId` that had no build entry, and
every animated shape shared one slide-global counter — directly at odds with "unique for
a given shape". **Fixed**: `groupIdsOf()` counts per shape. A whole-shape effect is
always group `0`; the paragraphs of a build are that shape's groups `0, 1, 2 …` in
presentation order. Every animated shape now gets one `<p:bldP>` whose `grpId` is its
group `0`, so each animated group is tied to a build entry.

### 3. `p:bldLst` existed only for paragraph builds, and only sometimes

`CT_BuildList` is a repeated choice: `bldP | bldDgm | bldOleChart | bldGraphic`,
`minOccurs="0" maxOccurs="unbounded"`. The element is still schema-valid as empty or
absent, but the *purpose* of the list is "the list of graphic elements to
build" — PowerPoint maintains an entry per animated shape. With no entry for a
whole-shape effect, the effect group exists with no build to bind to (finding 2).

**Fixed**: one `<p:bldP spid="N" grpId="0"/>` per animated shape, `build="p"
uiExpand="1"` added only for paragraph builds. Position is unchanged and correct:
`CT_SlideTiming` is `tnLst? → bldLst? → extLst?`, so `p:bldLst` must follow `p:tnLst`
inside `p:timing`, which it does.

### 4. `p:cTn/@id` uniqueness was enforced, but contiguity was not

`ST_TLTimeNodeID` is just `xsd:unsignedInt`; the schema imposes no ordering. PowerPoint
*tolerates* gaps. The real failure mode is a duplicate, which makes PowerPoint drop the
whole tree silently (already fixed). **Hardened, not changed**: ids still come from one
rolling counter starting at 1, so they are unique *and* contiguous (`1..n`, `tmRoot=1`,
`mainSeq=2`). Contiguity is the more conservative form and is now asserted by a test.

### 5. A slide that already had a `p:timing` was skipped, not repaired

Idempotency is required, but "skip" silently keeps a broken tree (for example a
third-party `afterEffect` tree) and reports success. **Kept as skip** — deliberately, to
preserve the accepted product spec and to avoid rewriting a deck that a user may have
opened and re-saved — with one tightening: detection is now prefix-aware
(`<p:timing>` in whatever prefix the slide declares), so a `pm:timing` slide is no longer
double-injected.

### 6. Prefixes were hard-coded to `p:`

`p:sld` declares the PresentationML namespace on an arbitrary prefix; the injected tree
has to match it. The old injector emitted literal `p:` tags and searched for them.
**Fixed**: `readSlidePrefix()` reads the prefix bound to
`http://schemas.openxmlformats.org/presentationml/2006/main` from the `<…sld>` root and
every emitted tag uses it, as does the parser, the extLst probe and the structural guard.
A slide with no prefix (default namespace) is left untouched rather than given an
undeclared `p:` prefix.

### 7. `p:extLst` insertion was a string replace on the first occurrence

`p.sld` child order is `cSld → clrMapOvr? → transition? → timing? → extLst?`
(CT_Slide). The old code did `xml.replace('<p:extLst>', timing + '<p:extLst>')` — the
first `<p:extLst>` may belong to `p:cSld`, which would place the timing tree *inside*
`cSld` (invalid) or before `transition` (also invalid). **Fixed**: `insertionPoint()`
locates the slide-level `extLst` specifically and otherwise inserts immediately before
`</p:sld>`, which is always after `cSld`/`clrMapOvr`/`transition`.

### 8. The parser could not read much real-world XML

`parseSlideShapes` used greedy `p:`-prefixed regexes that assumed attribute order
(`<a:off x="…" y="…"/>`), double quotes, and paired tags. Concretely:

* `<a:off y="…" x="…"/>` → `rect` is lost, so units cannot be clustered;
* single-quoted attributes → the shape is dropped entirely;
* a shape inside `<p:grpSp>` → skipped, and the group matched as one bogus shape;
* `<p:graphicFrame>` geometry sits under `<p:xfrm>`, not `<a:xfrm>` → no rect;
* a non-self-closing empty element (`<p:cNvPr …></p:cNvPr>`, as pptxgenjs writes) → the
  `[^>]*>` pattern also matched the *closing* tag, so `attr()` read the wrong tag.

**Fixed**: a nesting-aware scanner keyed on the element's own qualified name, attribute
lookup by name in any order and either quote style, geometry read from `<a:off>`/`<a:ext>`
wherever they appear, group children walked (the container itself is not animatable), and
`[]`/unchanged output for XML that cannot be read. Because the PresentationML prefix is
now data rather than a literal, the internal readers canonicalise it for lookup only.

### 9. Smaller checks that came back clean

* `CT_TLCommonTimeNodeData` child order is `stCondLst?, endCondLst?, endSync?, iterate?,
  childTnLst?, subTnLst?` — emitted as `stCondLst` then `childTnLst`. Correct.
* `CT_TLTimeCondition` is `tgtEl?/tn?/rtn?` plus `evt?`/`delay?`; `ST_TLTime` is
  `unsignedInt | "indefinite"`, so `delay="indefinite"` and `delay="0"` are both legal.
* Two-level `p:par` nesting per click group (`delay="indefinite"` first, `delay="0"`
  later) matches PowerPoint's own output and the contract in
  `docs/pptx-animation-spec.md`.
* `nodeType` values are `tmRoot` / `mainSeq` / `clickEffect` / `withEffect` only; no
  `afterEffect`, no `interactiveSeq`.
* `presetID="10" presetClass="entr" presetSubtype="0" fill="hold"`,
  `animEffect transition="in" filter="fade"`, `dur="350"`: consistent with Fade.
* The `p:spTgt` is emitted twice per effect (`p:set` and `p:animEffect`) — required, not
  redundant.
* `p:pRg` (not `pgRg`) is the correct child of `p:txEl`; paragraph indices are 0-based.
* A picture needs no build entry of its own beyond its `bldP`; `p:bldLst` is not
  mandatory for a picture-only reveal.
* Attribute sets are minimal: no `prLst`, no `afterEffect`, no `nodePh`, no extra
  smil-isms PowerPoint might reject.

## What is still unverified

* **No PowerPoint was available.** Every rule above is schema- or documentation-derived.
  The `verify-pptx-animation.ps1` COM probe also cannot be run here.
* Whether PowerPoint *requires* `prevCondLst`/`nextCondLst` or merely writes them:
  the schema says optional, so finding 1 is a fidelity fix, not a proven fatal bug.
* Whether an animated `p:pic`/`p:graphicFrame` wants a `<p:bldP>` entry at all. The
  `<p:bldLst>` documentation says it lists the objects that can have build properties —
  "text, diagrams, and charts" — so a `bldP` for a picture is the conservative reading
  (the entry is inert) but not certainly what PowerPoint writes.
* Whether PowerPoint verifies that `p:bldLst` entries are complete. If it does, missing
  entries (the old behaviour) would be the second silent-drop candidate.
* Real PowerPoint re-save round-trip behaviour (whether it rewrites `grpId`, drops the
  `uiExpand` flag, and so on).

Regression coverage for each finding lives in
`tests/unit/pptxAnimationXml.test.js`, with the schema checker in
`tests/helpers/pptxAnimationFixtures.mjs`.
