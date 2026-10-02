// Entrance animations for generated slides.
//
// pptxgenjs has no animation API, so the deck is post-processed at the XML level.
// Every `ppt/slides/slideN.xml` gets a `<p:timing>` tree built the way PowerPoint
// itself writes it for click-triggered entrance effects:
//
//   p:timing
//     p:tnLst > p:par > p:cTn(id=1, tmRoot)
//       p:seq(concurrent=1, nextAc=seek)
//         p:cTn(id=2, mainSeq) > p:childTnLst
//           click group 1 (outer p:par delay="indefinite" -> waits for the click)
//           click group 2 ... n            (outer p:par delay="0")
//         p:prevCondLst / p:nextCondLst  (onPrev/onNext on <p:sldTgt/>)
//     p:bldLst (one p:bldP per animated shape, grpId matching the effect)
//
// The exact element order comes from the ECMA-376 schemas, which PowerPoint
// enforces strictly (see docs/pptx-animation-audit.md):
//   * CT_Slide      : cSld -> clrMapOvr? -> transition? -> timing? -> extLst?
//   * CT_SlideTiming: tnLst? -> bldLst? -> extLst?
//   * CT_TLTimeNodeSequence: cTn, prevCondLst?, nextCondLst?
//   * CT_TLCommonTimeNodeData: stCondLst?, endCondLst?, endSync?, iterate?,
//                              childTnLst?, subTnLst?
//   * CT_TLTimeCondition: tgtEl?/tn?/rtn? + evt?, delay?  (delay = ms | indefinite)
//
// Rules the tree encodes:
//   * every animated target is its own click step -> the speaker controls pacing
//   * one effect: Fade (`presetID=10`, `presetClass=entr`, `filter=fade`), 350 ms
//   * a bullet list reveals paragraph by paragraph (`p:bldP build="p"`)
//   * shapes that belong to one visual unit (a KPI value + its label) share a
//     click via `withEffect`
//   * decoration, page furniture and the page title stay static
//   * pictures are the last content step, the conclusion bar closes the page
//
// The normative contract for this tree is docs/pptx-animation-spec.md; the Python
// twin is tools/pptx_animate.py.
import JSZip from 'jszip'

const DEFAULT_OPTIONS = { durationMs: 350 }

const EMU_PER_INCH = 914400
/** Below this size a text box is page furniture (page number, source list). */
const MIN_BODY_PT = 10

/** Shape-tree elements that become one animatable shape. */
const SHAPE_TAGS = new Set(['p:sp', 'p:pic', 'p:graphicFrame', 'p:cxnSp'])
/** Elements that contain shapes instead of being one (their children are read). */
const CONTAINER_TAGS = new Set(['p:spTree', 'p:grpSp'])

const PRESENTATIONML_NS = 'http://schemas.openxmlformats.org/presentationml/2006/main'

/* --------------------------------------------------------------- parsing -- */

/** Escape a string for use inside a RegExp. */
const escapeRe = (value) => String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/**
 * The namespace prefix the slide binds to the PresentationML namespace
 * (`p` in every file PowerPoint and pptxgenjs write, but it is read from the
 * document rather than assumed). `''` means it is the default namespace.
 */
export function readSlidePrefix(xml) {
  if (typeof xml !== 'string' || !xml) return null
  const root = /<([A-Za-z_][\w.-]*:)?sld[\s/>][^>]*>/m.exec(xml)
  if (!root) return null
  const tag = root[0]
  const bound = new RegExp(`xmlns:([A-Za-z_][\\w.-]*)=["']${escapeRe(PRESENTATIONML_NS)}["']`).exec(tag)
  if (bound) return bound[1]
  return /<sld[\s/>]/.test(tag) ? '' : null
}

/**
 * The prefix the slide uses for PresentationML, for the readers too: `p` for
 * every file PowerPoint and pptxgenjs write, `pm` or anything else in a hand-made
 * one, and `p` for a fragment with no root element at all.
 */
function slideTagPrefix(xml) {
  const root = /<([A-Za-z_][\w.-]*:)?sld[\s/>][^>]*>/m.exec(xml)
  if (!root) return 'p'
  return root[1] ? root[1].slice(0, -1) : 'p'
}

/**
 * A bare slide (`<sld>` with no prefix and no namespace declaration) is not what
 * PowerPoint writes, but plain XML is easy to hand a parser, so the PresentationML
 * element names are qualified with the slide's prefix before scanning. The prefix
 * itself is left alone, because the injected XML has to match it.
 */
function canonicalXml(xml, prefix = 'p') {
  const root = /<([A-Za-z_][\w.-]*:)?sld[\s/>][^>]*>/m.exec(xml)
  if (!root || root[1]) return xml
  const bare =
    'resume|sld|cSld|clrMapOvr|bg|bgPr|bgRef|spTree|nvGrpSpPr|cNvGrpSpPr|nvPr|grpSpPr|sp|cxnSp|pic|graphicFrame|grpSp|nvSpPr|cNvSpPr|nvPicPr|cNvPicPr|nvGraphicFramePr|cNvGraphicFramePr|cNvPr|spPr|txBody|xfrm|txEl|pRg|timing|tnLst|par|cTn|stCondLst|childTnLst|cond|seq|prevCondLst|nextCondLst|bldLst|bldP|tgtEl|sldTgt|spTgt|set|cBhvr|attrNameLst|attrName|to|strVal|animEffect|prevAc|extLst'
  return xml.replace(new RegExp(`<(/?)(?=(?:${bare})[\\s/>])`, 'g'), `<$1${prefix}:`)
}

/** A qualified XML element name, lazy so it stops at the end of its own tag. */
const QNAME = String.raw`[A-Za-z_][\w.-]*(?::[\w.-]+)?`

const TAG_ATTRS = String.raw`(?:\s+[\w.:-]+\s*=\s*(?:"[^"]*"|'[^']*'))*`

/**
 * What may follow an element name: whitespace, the attribute list, the self
 * closing slash or the tag end. `\b` cannot be used here, because the character
 * after the name is usually a space and both are non-word characters.
 */
const TAG_END = String.raw`(?=[\s/>])`

/**
 * Scan `xml` for the non-self-closing elements whose qualified names are in
 * `wanted`, returning `{tag, outer, inner}` for each: `outer` is the byte-exact
 * source (open tag through close tag) and `inner` its content.
 *
 * Matching repeats the element's own qualified name while looking for the close
 * tag — a nesting-aware scan that needs no global tag state, so a self-closing
 * tag, mixed content, attributes in any order or an unknown element cannot
 * confuse it. A declared depth limit bounds the work on malformed input.
 */
function scanElements(xml, wanted) {
  const cleaned = String(xml ?? '').replace(/<!--[\s\S]*?-->/g, '').replace(/<!\[CDATA\[[\s\S]*?\]\]>/g, '')
  const found = []
  for (const tag of wanted) {
    const escaped = escapeRe(tag)
    // `(?=>)` lets the optional slash be inspected, so self-closing tags are skipped.
    const openRe = new RegExp(`<${escaped}(${TAG_ATTRS})\\s*(/?)(?=>)`, 'g')
    const stepRe = new RegExp(`<(/?)${escaped}(${TAG_ATTRS})\\s*(/?)>`, 'g')
    let open
    while ((open = openRe.exec(cleaned)) !== null) {
      const openEnd = openRe.lastIndex + 1
      if (open[2] === '/') {
        openRe.lastIndex = openEnd
        continue
      }
      stepRe.lastIndex = openEnd
      let depth = 0
      let cursor = openEnd
      let closed = false
      let guard = 0
      let step
      while ((step = stepRe.exec(cleaned)) !== null && guard < 10_000) {
        guard += 1
        if (step[1] === '/') {
          depth -= 1
          if (depth <= 0) {
            found.push({
              tag,
              at: open.index,
              outer: cleaned.slice(open.index, stepRe.lastIndex),
              inner: cleaned.slice(openEnd, step.index),
            })
            cursor = stepRe.lastIndex
            closed = true
            break
          }
        } else if (step[3] !== '/') {
          depth += 1
        }
      }
      openRe.lastIndex = closed ? cursor : openEnd
    }
  }
  // Document order, so a container is reported before the shapes inside it.
  return found.sort((a, b) => a.at - b.at)
}

/** Attribute value lookup that tolerates any attribute order and quote style. */
function attr(tag, name) {
  const found = new RegExp(`\\b${name}=(?:"([^"]*)"|'([^']*)')`).exec(tag)
  return found ? (found[1] !== undefined ? found[1] : found[2]) : null
}

/** `<a:off>`/`<a:ext>` payload, in EMU. Attribute order is irrelevant. */
function readOffExt(source) {
  const offTag = /<a:off(?:\s[^>]*)?>/.exec(source)
  const extTag = /<a:ext(?:\s[^>]*)?>/.exec(source)
  if (!offTag || !extTag) return null
  const x = Number(attr(offTag[0], 'x'))
  const y = Number(attr(offTag[0], 'y'))
  const cx = Number(attr(extTag[0], 'cx'))
  const cy = Number(attr(extTag[0], 'cy'))
  if (![x, y, cx, cy].every((value) => Number.isFinite(value))) return null
  return { x: x / EMU_PER_INCH, y: y / EMU_PER_INCH, w: cx / EMU_PER_INCH, h: cy / EMU_PER_INCH }
}

/**
 * Geometry of a shape subtree, in inches, or null when it cannot be read.
 * The transform of `p:sp`/`p:pic`/`p:cxnSp` sits in `<p:spPr>`, while
 * `p:graphicFrame` carries it directly; the prefix does not matter here, because
 * only `<a:off>`/`<a:ext>` are read from it.
 */
function readRect(source) {
  const xfrm = /<[\w.-]+:xfrm(?:\s[^>]*)?>([\s\S]*?)<\/[\w.-]+:xfrm>/.exec(source)
  if (xfrm) return readOffExt(xfrm[1])
  return readOffExt(source)
}

/** Largest run size in the subtree, in points (default 18pt). */
function readFontPt(source) {
  let max = 0
  for (const match of source.matchAll(/\bsz=(?:"(\d+)"|'(\d+)')/g)) {
    const value = Number(match[1] ?? match[2])
    if (Number.isFinite(value)) max = Math.max(max, value / 100)
  }
  return max || 18
}

/**
 * Paragraph count of the shape's own text body (only paragraphs that carry text).
 * Mixed content behind `</a:p>` is skipped: the open-tag pattern cannot match a
 * closing tag, because a closing tag has no room for the attribute prefix.
 */
function readParagraphs(source, tagPrefix = 'p') {
  const tag = `${tagPrefix}${tagPrefix ? ':' : ''}txBody`
  const body = new RegExp(`<${escapeRe(tag)}(?:\\s[^>]*)?>([\\s\\S]*?)</${escapeRe(tag)}>`).exec(source)
  if (!body) return 0
  let count = 0
  for (const paragraph of body[1].matchAll(/<a:p(?:\s[^>]*)?>[\s\S]*?<\/a:p>/g)) {
    if (/<a:t(?:\s[^>]*)?>/.test(paragraph[0])) count += 1
  }
  return count
}

function readText(source) {
  const parts = []
  for (const match of source.matchAll(/<a:t(?:\s[^>]*)?>([\s\S]*?)<\/a:t>/g)) parts.push(match[1])
  return parts.join(' ').replace(/\s+/g, ' ').trim()
}

/**
 * Every animatable shape in draw order, with id, text, paragraph count and
 * geometry (in inches). The children of a `<p:grpSp>` are reported (the group
 * container itself is not — it has no fill, line or text of its own); unsupported
 * or damaged XML yields `[]` rather than throwing.
 *
 * @returns {Array<{id:number, kind:'shape'|'text'|'picture'|'graphic', text:string,
 *                  paragraphs:number, rect:{x:number,y:number,w:number,h:number}|null,
 *                  fontPt:number, tag:string}>}
 */
export function parseSlideShapes(xml) {
  if (typeof xml !== 'string' || !xml) return []
  const tagPrefix = slideTagPrefix(xml)
  const q = (name) => `${tagPrefix}${tagPrefix ? ':' : ''}${name}`
  const cleaned = canonicalXml(xml, tagPrefix)
  const shapeTags = new Set([...SHAPE_TAGS].map((tag) => q(tag.slice(2))))
  const containerTags = new Set([...CONTAINER_TAGS].map((tag) => q(tag.slice(2))))
  const shapes = []
  const seen = new Set()

  const collect = (source, tag) => {
    const idTag = new RegExp(`<${escapeRe(q('cNvPr'))}${TAG_END}[^>]*>`).exec(source)
    const id = idTag ? Number(attr(idTag[0], 'id')) : NaN
    if (!Number.isFinite(id) || seen.has(id)) return
    seen.add(id)
    const text = readText(source)
    const kind =
      tag === q('pic') ? 'picture' : tag === q('graphicFrame') ? 'graphic' : text ? 'text' : 'shape'
    shapes.push({
      id,
      kind,
      text,
      paragraphs: kind === 'text' ? readParagraphs(source, tagPrefix) : 0,
      rect: readRect(source),
      fontPt: readFontPt(source),
      tag,
    })
  }

  const walk = (source, insideSpTree) => {
    for (const element of scanElements(source, new Set([...shapeTags, ...containerTags]))) {
      if (shapeTags.has(element.tag)) {
        if (insideSpTree) collect(element.outer, element.tag)
      } else {
        // A grpSp container draws through its children and owns no fill, line or
        // text, so only the children are animatable — which also keeps the
        // container's id out of the timing target list.
        walk(element.inner, true)
      }
    }
  }

  const trees = scanElements(cleaned, new Set([q('spTree')]))
  if (trees.length) {
    for (const tree of trees) walk(tree.inner, true)
    return shapes
  }
  // No shape tree (a fragment): read the top-level shapes directly.
  walk(cleaned, true)
  return shapes
}

/* ------------------------------------------------------------ planning ---- */

const normalise = (value) => String(value ?? '').replace(/\s+/g, ' ').trim()

/** True when a shape carries the given text (first 12 chars are enough). */
function carries(shape, value) {
  const wanted = normalise(value).slice(0, 12)
  if (!wanted) return false
  return normalise(shape.text).includes(wanted)
}

function overlapsHorizontally(a, b) {
  if (!a?.rect || !b?.rect) return false
  const left = Math.max(a.rect.x, b.rect.x)
  const right = Math.min(a.rect.x + a.rect.w, b.rect.x + b.rect.w)
  const width = right - left
  if (width <= 0) return false
  return width / Math.min(a.rect.w, b.rect.w) >= 0.6
}

/** Two shapes of the same column stacked tightly = one visual unit. */
function sameUnit(a, b) {
  if (!a?.rect || !b?.rect) return false
  if (!overlapsHorizontally(a, b)) return false
  const gap = Math.max(a.rect.y, b.rect.y) - Math.min(a.rect.y + a.rect.h, b.rect.y + b.rect.h)
  return gap >= -0.05 && gap <= 0.12
}

function readingOrder(a, b) {
  const ay = a.rect?.y ?? 0
  const by = b.rect?.y ?? 0
  if (Math.abs(ay - by) > 0.15) return ay - by
  return (a.rect?.x ?? 0) - (b.rect?.x ?? 0)
}

/**
 * Clusters shapes that belong to one visual unit (a KPI value + its label + its
 * delta, a caption under a number). Comparing against every pair — not just the
 * neighbouring shape — is what keeps two side-by-side columns apart.
 */
export function clusterUnits(shapes) {
  const parent = shapes.map((_, index) => index)
  const find = (index) => {
    let root = index
    while (parent[root] !== root) root = parent[root]
    let cursor = index
    while (parent[cursor] !== cursor) {
      const next = parent[cursor]
      parent[cursor] = root
      cursor = next
    }
    return root
  }
  const union = (a, b) => {
    const rootA = find(a)
    const rootB = find(b)
    if (rootA !== rootB) parent[rootB] = rootA
  }

  for (let i = 0; i < shapes.length; i += 1) {
    for (let j = i + 1; j < shapes.length; j += 1) {
      if (sameUnit(shapes[i], shapes[j])) union(i, j)
    }
  }

  const byRoot = new Map()
  shapes.forEach((shape, index) => {
    const root = find(index)
    if (!byRoot.has(root)) byRoot.set(root, [])
    byRoot.get(root).push(shape)
  })
  return [...byRoot.values()]
    .map((cluster) => [...cluster].sort(readingOrder))
    .sort((a, b) => readingOrder(a[0], b[0]))
}

/**
 * Decides what animates, in which order, one step per click.
 *
 * @param {Array} shapes      from parseSlideShapes
 * @param {{title?: string, takeaway?: string, eyebrow?: string}} meta
 * @returns {{steps: Array<Array<{spid:number, paragraph:number|null}>>, static: number[]}}
 */
export function planReveal(shapes, { title = '', subtitle = '', takeaway = '', eyebrow = '' } = {}) {
  const staticIds = []
  const animated = []
  let subtitleShape = null

  for (const shape of shapes) {
    // 1) decoration / structure never animates
    if (shape.kind === 'shape') {
      staticIds.push(shape.id)
      continue
    }
    // 2) page furniture: page numbers, source lines, tiny print
    if (shape.kind === 'text' && shape.fontPt <= MIN_BODY_PT) {
      staticIds.push(shape.id)
      continue
    }
    // 3) the heading block is on screen from the moment the page appears
    if (shape.kind === 'text' && carries(shape, subtitle)) {
      subtitleShape = shape
      staticIds.push(shape.id)
      continue
    }
    if (shape.kind === 'text' && (carries(shape, title) || carries(shape, eyebrow))) {
      staticIds.push(shape.id)
      continue
    }
    animated.push(shape)
  }

  const takeawayShapes = animated.filter((shape) => shape.kind === 'text' && carries(shape, takeaway))
  const media = animated.filter((shape) => shape.kind === 'picture')
  // background pictures are the page backdrop, not a reveal step
  const bodyShapes = animated
    .filter((shape) => !takeawayShapes.includes(shape) && !media.includes(shape))
    .sort(readingOrder)
  const graphics = bodyShapes.filter((shape) => shape.kind === 'graphic')
  const texts = bodyShapes.filter((shape) => shape.kind !== 'graphic')

  // 4) one click per visual unit; a multi-paragraph box reveals paragraph by paragraph
  const steps = []
  for (const cluster of clusterUnits(texts)) {
    const paragraphs = cluster.filter((shape) => shape.paragraphs > 1)
    for (const shape of paragraphs) {
      for (let index = 0; index < shape.paragraphs; index += 1) {
        steps.push([{ spid: shape.id, paragraph: index }])
      }
    }
    const together = cluster.filter((shape) => shape.paragraphs <= 1)
    if (together.length) steps.push(together.map((shape) => ({ spid: shape.id, paragraph: null })))
  }
  for (const shape of graphics) steps.push([{ spid: shape.id, paragraph: null }])
  for (const shape of media) steps.push([{ spid: shape.id, paragraph: null }])
  for (const shape of takeawayShapes) steps.push([{ spid: shape.id, paragraph: null }])

  // A page with nothing left to reveal (a cover, a divider) would show no
  // animation at all: let the subtitle carry the one click, so entering the page
  // still has a beat — without leaving an empty frame, since the title is static.
  if (!steps.length && subtitleShape) {
    steps.push([{ spid: subtitleShape.id, paragraph: null }])
    const index = staticIds.indexOf(subtitleShape.id)
    if (index !== -1) staticIds.splice(index, 1)
  }

  return { steps, static: staticIds }
}

/* ------------------------------------------------------------ generation -- */

/** Local-name helpers: the slide's own prefix is used for every injected tag. */
function names(prefix) {
  const p = typeof prefix === 'string' ? prefix : 'p'
  const put = (name) => `${p ? `${p}:` : ''}${name}`
  return {
    p,
    timing: put('timing'),
    tnLst: put('tnLst'),
    bldLst: put('bldLst'),
    bldP: put('bldP'),
    par: put('par'),
    cTn: put('cTn'),
    stCondLst: put('stCondLst'),
    childTnLst: put('childTnLst'),
    cond: put('cond'),
    seq: put('seq'),
    prevCondLst: put('prevCondLst'),
    nextCondLst: put('nextCondLst'),
    tgtEl: put('tgtEl'),
    sldTgt: put('sldTgt'),
    spTgt: put('spTgt'),
    txEl: put('txEl'),
    pRg: put('pRg'),
    set: put('set'),
    cBhvr: put('cBhvr'),
    attrNameLst: put('attrNameLst'),
    attrName: put('attrName'),
    to: put('to'),
    strVal: put('strVal'),
    animEffect: put('animEffect'),
  }
}

/**
 * `grpId` for every target.
 *
 * ECMA-376 4.6.16: "GroupIDs are unique for a given shape. They are not
 * guaranteed to be unique IDs across all shapes on a slide" — the value ties an
 * animation group to its build entry. A whole-shape effect is always group 0; the
 * paragraphs of a per-paragraph build are the shape's groups 0, 1, 2 ... in
 * presentation order (grpId 0 also backs the shape's `<p:bldP>` entry).
 */
export function groupIdsOf(steps) {
  const counters = new Map()
  return steps.map((targets) =>
    targets.map((target) => {
      if (target.paragraph === null || target.paragraph === undefined) return 0
      const grpId = counters.get(target.spid) ?? 0
      counters.set(target.spid, grpId + 1)
      return grpId
    }),
  )
}

/**
 * Builds the `<p:timing>` tree for the planned steps.
 *
 * @param {Array<Array<{spid:number, paragraph:number|null}>>} steps one entry per click
 * @param {{durationMs?: number, prefix?: string, buildSpids?: number[]}} options
 */
export function buildTimingXml(steps, { durationMs = 350, prefix = 'p', buildSpids = [] } = {}) {
  const ns = names(prefix)
  let nextId = 3
  /** Reserve a block of ids: every effect owns three of them. */
  const takeId = (span = 1) => {
    const id = nextId
    nextId += span
    return id
  }

  const targetXml = (spid, paragraph) => {
    if (paragraph === null || paragraph === undefined) return `<${ns.spTgt} spid="${spid}"/>`
    return (
      `<${ns.spTgt} spid="${spid}"><${ns.txEl}><${ns.pRg} st="${paragraph}" end="${paragraph}"/>` +
      `</${ns.txEl}></${ns.spTgt}>`
    )
  }

  /** One Fade entrance effect (`presetID=10` = Fade, `presetClass=entr`). */
  const effectXml = (target, baseId, grpId, nodeType) =>
    `<${ns.par}><${ns.cTn} id="${baseId}" presetID="10" presetClass="entr" presetSubtype="0" fill="hold" grpId="${grpId}" nodeType="${nodeType}">` +
    `<${ns.stCondLst}><${ns.cond} delay="0"/></${ns.stCondLst}><${ns.childTnLst}>` +
    `<${ns.set}>` +
    `<${ns.cBhvr}><${ns.cTn} id="${baseId + 1}" dur="1" fill="hold"><${ns.stCondLst}><${ns.cond} delay="0"/></${ns.stCondLst}></${ns.cTn}>` +
    `<${ns.tgtEl}>${targetXml(target.spid, target.paragraph)}</${ns.tgtEl}>` +
    `<${ns.attrNameLst}><${ns.attrName}>style.visibility</${ns.attrName}></${ns.attrNameLst}></${ns.cBhvr}>` +
    `<${ns.to}><${ns.strVal} val="visible"/></${ns.to}>` +
    `</${ns.set}>` +
    `<${ns.animEffect} transition="in" filter="fade"><${ns.cBhvr}><${ns.cTn} id="${baseId + 2}" dur="${durationMs}"/>` +
    `<${ns.tgtEl}>${targetXml(target.spid, target.paragraph)}</${ns.tgtEl}></${ns.cBhvr}></${ns.animEffect}>` +
    `</${ns.childTnLst}></${ns.cTn}></${ns.par}>`

  const groupIds = groupIdsOf(steps)
  const groups = steps
    .map((targets, index) => {
      const outerId = takeId()
      const innerId = takeId()
      const effects = targets
        .map((target, targetIndex) =>
          effectXml(target, takeId(3), groupIds[index][targetIndex], targetIndex === 0 ? 'clickEffect' : 'withEffect'),
        )
        .join('')
      // The first group waits for the speaker's click; later groups start when the
      // previous one is over, which is still one click each.
      const outerCondition = index === 0 ? 'indefinite' : '0'
      return (
        `<${ns.par}><${ns.cTn} id="${outerId}" fill="hold"><${ns.stCondLst}><${ns.cond} delay="${outerCondition}"/></${ns.stCondLst}><${ns.childTnLst}>` +
        `<${ns.par}><${ns.cTn} id="${innerId}" fill="hold"><${ns.stCondLst}><${ns.cond} delay="0"/></${ns.stCondLst}><${ns.childTnLst}>` +
        effects +
        `</${ns.childTnLst}></${ns.cTn}></${ns.par}>` +
        `</${ns.childTnLst}></${ns.cTn}></${ns.par}>`
      )
    })
    .join('')

  // One build entry per animated shape (the spec's model: <p:bldLst> "specifies
  // the list of graphic elements to build"), with grpId 0 — the shape's first
  // animation group. A per-paragraph build is marked build="p" + uiExpand="1".
  const paragraphBuilds = new Set(
    steps
      .flat()
      .filter((target) => target.paragraph !== null && target.paragraph !== undefined)
      .map((target) => target.spid),
  )
  const buildNodes = buildSpids
    .map((spid) =>
      paragraphBuilds.has(spid)
        ? `<${ns.bldP} spid="${spid}" grpId="0" uiExpand="1" build="p"/>`
        : `<${ns.bldP} spid="${spid}" grpId="0"/>`,
    )
    .join('')

  return (
    `<${ns.timing}><${ns.tnLst}><${ns.par}>` +
    `<${ns.cTn} id="1" dur="indefinite" restart="never" nodeType="tmRoot"><${ns.childTnLst}>` +
    `<${ns.seq} concurrent="1" nextAc="seek"><${ns.cTn} id="2" dur="indefinite" nodeType="mainSeq"><${ns.childTnLst}>` +
    groups +
    `</${ns.childTnLst}></${ns.cTn}>` +
    // PowerPoint writes these: the pair is optional in CT_TLTimeNodeSequence, but
    // the explicit onPrev/onNext conditions are what it round-trips.
    `<${ns.prevCondLst}><${ns.cond} evt="onPrev" delay="0"><${ns.tgtEl}><${ns.sldTgt}/></${ns.tgtEl}></${ns.cond}></${ns.prevCondLst}>` +
    `<${ns.nextCondLst}><${ns.cond} evt="onNext" delay="0"><${ns.tgtEl}><${ns.sldTgt}/></${ns.tgtEl}></${ns.cond}></${ns.nextCondLst}>` +
    `</${ns.seq}>` +
    `</${ns.childTnLst}></${ns.cTn}>` +
    `</${ns.par}></${ns.tnLst}>` +
    (buildNodes ? `<${ns.bldLst}>${buildNodes}</${ns.bldLst}>` : '') +
    `</${ns.timing}>`
  )
}

/** The animated shapes, in shape-tree (draw) order, for the build list. */
function buildSpidsOf(steps, shapes) {
  const animated = new Set(steps.flat().map((target) => target.spid))
  return shapes.filter((shape) => animated.has(shape.id)).map((shape) => shape.id)
}

/**
 * Position of the `<p:timing>` insertion point in the slide's child sequence
 * (CT_Slide: cSld -> clrMapOvr? -> transition? -> timing? -> extLst?), or -1.
 * An existing slide-level `<p:extLst>` (a private extension pptxgenjs may write)
 * must stay last, so the tree goes in front of it.
 */
/**
 * Index of the first `extLst` that is a DIRECT child of the slide element, or -1.
 *
 * A plain search is not enough: a `p:graphicFrame` (a table) carries its own
 * `extLst` inside `p:nvGraphicFramePr`, and inserting the timing tree there puts
 * it in the middle of `p:spTree` — invalid CT_Slide. PowerPoint/WPS then discard
 * that page's animations entirely (observed on table pages).
 */
function slideLevelExtLstIndex(xml, from) {
  const tagRe = new RegExp(String.raw`<(\/?)(${QNAME})(${TAG_ATTRS})\s*(\/?)>`, 'g')
  tagRe.lastIndex = from
  let depth = 0
  let match
  while ((match = tagRe.exec(xml)) !== null) {
    const closing = Boolean(match[1])
    const name = match[2]
    const selfClosing = Boolean(match[4])
    if (closing) {
      depth -= 1
      continue
    }
    if (depth === 0 && /(^|:)extLst$/.test(name)) return match.index
    if (!selfClosing) depth += 1
  }
  return -1
}

function insertionPoint(xml) {
  const endTag = new RegExp(String.raw`<\/(${QNAME})\s*>\s*$`).exec(xml)
  if (!endTag) return -1
  const rootName = endTag[1]
  if (!/(^|:)sld$/.test(rootName)) return -1
  const closingAt = xml.lastIndexOf(endTag[0])
  const open = new RegExp(`<${escapeRe(rootName)}(${TAG_ATTRS})\\s*(/?)(?=>)`).exec(xml)
  if (!open || open[2] === '/' || open.index >= closingAt) return -1

  const prefixed = rootName.replace(/[^:]*$/, '')
  const extLst = new RegExp(`<${escapeRe(prefixed)}extLst(${TAG_ATTRS})\\s*(/?)(?=>)`).exec(xml)
  const openTagEnd = xml.indexOf('>', open.index)
  if (openTagEnd === -1 || openTagEnd >= closingAt) return closingAt
  const extLstAt = slideLevelExtLstIndex(xml, openTagEnd + 1)
  return extLstAt !== -1 && extLstAt < closingAt ? extLstAt : closingAt
}

/** Injects the timing tree into one slide XML. Idempotent and total. */
export function injectTiming(xml, options = {}) {
  const settings = { ...DEFAULT_OPTIONS, ...options }
  if (typeof xml !== 'string' || !xml) return xml
  const prefix = readSlidePrefix(xml)
  // No PresentationML prefix to reuse: a default-namespace slide is not something
  // PowerPoint writes, and guessing would risk emitting an undeclared prefix.
  if (!prefix) return xml
  const ns = names(prefix)
  // Already animated (any prefix): leave the file alone rather than stacking trees.
  if (new RegExp(`<${ns.timing}${TAG_END}`).test(xml)) return xml
  const shapes = parseSlideShapes(xml)
  if (!shapes.length) return xml
  const { steps } = planReveal(shapes, settings)
  if (!steps.length) return xml
  const timing = buildTimingXml(steps, {
    durationMs: settings.durationMs,
    prefix,
    buildSpids: buildSpidsOf(steps, shapes),
  })
  const at = insertionPoint(xml)
  if (at < 0) return xml
  const next = xml.slice(0, at) + timing + xml.slice(at)
  // Never emit a tree we cannot read back: a malformed slide stays untouched.
  return reparseGuard(next, prefix) ? next : xml
}

/** Cheap structural guard: the injected tree must end where it starts. */
function reparseGuard(xml, prefix) {
  const ns = names(prefix)
  const open = xml.indexOf(`<${ns.timing}>`)
  const close = xml.lastIndexOf(`</${ns.timing}>`)
  if (open < 0 || close < open) return false
  const opens = (xml.match(new RegExp(`<${ns.par}${TAG_END}`, 'g')) || []).length
  const closes = (xml.match(new RegExp(`</${ns.par}>`, 'g')) || []).length
  return opens === closes
}

/* ------------------------------------------------------------------ entry -- */

function slideNumber(name) {
  const match = /slide(\d+)\.xml$/.exec(name)
  return match ? Number(match[1]) : 0
}

/**
 * Post-processes a generated .pptx: every slide gets its click-triggered entrance
 * animations. Anything unexpected leaves that slide untouched rather than
 * producing a broken file.
 *
 * @param {Buffer} buffer
 * @param {{slides?: Array<{title?:string, takeaway?:string, eyebrow?:string}>}} options
 */
export async function addSlideAnimations(buffer, { slides = [], ...options } = {}) {
  const zip = await JSZip.loadAsync(buffer)
  const names = Object.keys(zip.files)
    .filter((name) => /^ppt\/slides\/slide\d+\.xml$/.test(name))
    .sort((a, b) => slideNumber(a) - slideNumber(b))

  let patched = 0
  let steps = 0
  for (const name of names) {
    try {
      const xml = await zip.file(name).async('string')
      const meta = slides[slideNumber(name) - 1] || {}
      const next = injectTiming(xml, {
        ...options,
        title: meta.title || '',
        subtitle: meta.subtitle || '',
        takeaway: meta.takeaway || '',
        eyebrow: meta.eyebrow || '',
      })
      if (next !== xml) {
        zip.file(name, next)
        patched += 1
        steps += planReveal(parseSlideShapes(next), meta).steps.length
      }
    } catch {
      /* keep the slide as-is */
    }
  }
  const output = await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' })
  return { buffer: output, patched, slides: names.length, clickSteps: steps }
}
