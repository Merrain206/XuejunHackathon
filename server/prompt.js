// System-prompt construction: page-type catalog, content-transformation rules,
// the user's style-constraint zone, citation policy and the readability floors.
// No content-specific styling lives here — only the contract the model must fill.

export const PAGE_TYPES = [
  'cover',
  'section',
  'bullets',
  'stats',
  'chart',
  'compare',
  'timeline',
  'table',
  'quote',
  'references',
  'closing',
]

/** Strings that must never reach the rendered deck as body text. */
export const PLACEHOLDER_PATTERNS = [
  /^封\s*面\s*页?$/,
  /^封面(主图|配图|图片)\s*[:：]/,
  /^(收尾|结束|尾|最后一)\s*页$/,
  /^(配图|图片|插画|图示|示意图|图表|图标)\s*[:：]/,
  /^(此处|这里)\s*(插入|放置|添加|省略|可插入|建议)/,
  /^[（(]\s*(此处|以下)?\s*(省略|略|待补充|placeholder)/i,
  /^(谢谢(观看|聆听)|感谢(观看|聆听))$/,
  /^(正\s*文|内容|要点)\s*[:：]\s*$/,
  /^(slide|page)\s*\d+$/i,
  /^lorem ipsum/i,
]

function paletteLine() {
  return '{"bg":"#RRGGBB","bgAlt":"#RRGGBB","fg":"#RRGGBB","muted":"#RRGGBB","accent":"#RRGGBB","accent2":"#RRGGBB","surface":"#RRGGBB","border":"#RRGGBB"}'
}

function styleSection(stylePreference) {
  const preference = String(stylePreference || '').trim()
  if (!preference) {
    return [
      '## 风格约束区',
      '用户没有指定风格偏好。请按内容语义自主推导整套视觉语言：',
      '从文档的行业、受众、情绪、信息密度出发，决定明暗、色相、对比强度、版式节奏与配图风格。',
      '把这个推导过程写成 design.rationale，让用户看懂你为什么这样设计。',
    ].join('\n')
  }
  return [
    '## 风格约束区（用户偏好，必须遵守的解析规则）',
    '```',
    preference,
    '```',
    '处理规则：',
    '1. **硬约束**：其中具体、可验证的要求（例如「主色用深蓝」「不要手绘风格」「必须衬线字体」）必须逐条严格执行，',
    '   并在输出 design 之前自检一遍是否全部满足；确实无法满足时选择最接近的可行方案，不得忽略。',
    '2. **软参考**：模糊感受类描述（例如「活泼一点」「高级感」「科技感」「温柔」）不是设计指令，',
    '   你必须先把它们翻译成具体设计语言——配色明度/饱和度、图形语言、留白比例、字体气质、版式动势——再据此发挥。',
    '3. 优先级：用户的硬约束 > 你的审美判断 > 通用最佳实践。',
    '4. **风格偏好只影响视觉表现**（配色、版式、字体气质、配图风格、氛围、文案语气），',
    '   **绝对不得改变文档本身的信息、数据、事实、逻辑与结论**。',
    '   不允许为了「更活泼」而添加原文没有的事实，也不允许为了「更高级」而删掉原文的关键数字。',
  ].join('\n')
}

function citationSection(showCitations, sources) {
  if (!showCitations) {
    return [
      '## 来源与角标',
      '本轮**不需要**来源标注：只做内容融合。',
      '不要输出任何角标（如 [1]）、不要输出 citations 字段、不要输出 references 页。',
    ].join('\n')
  }
  const list = sources.length
    ? sources.map((source) => `[${source.n}] ${source.title}${source.url ? ` — ${source.url}` : ''}`).join('\n')
    : '[1] 用户提供的文档（本文档，无外部链接）'
  return [
    '## 来源与角标（本轮开启）',
    '可用来源（**只能引用下面真实存在的条目，绝不允许编造标题或链接**）：',
    '```',
    list,
    '```',
    '要求：',
    '1. 凡引用到具体数据、事实、结论的地方，在该条内容结尾加角标，形如「营收增长 23%[1]」；同一来源沿用同一编号。',
    '2. 每一页用它自己的 `citations` 字段列出该页实际引用到的来源：{"n":1,"title":"…","url":"…","publisher":"…"}；',
    '   url 必须与上面的可用来源完全一致；来源没有链接时 url 留空字符串。',
    '3. 编号全局连续、不跳号、不重复；没有被引用的编号不要出现。',
    '4. 整份 PPT 的最后一页固定输出一页 `type":"references"`，标题「参考来源」，',
    '   bullets 按编号列出用到的来源条目（形如「[1] 标题 — URL」），最多 6 条，超出时保留被引用次数最多的。',
    '5. 上面只有「用户提供的文档」一个来源时，同样要在引用数据处加 [1]，并输出 references 页列出它；',
    '   不要为了凑来源而编造网页。',
  ].join('\n')
}

function searchSection(results, queries) {
  if (!results.length) {
    return [
      '## 联网检索结果',
      '本轮没有可用的联网检索结果（未配置检索服务或没有命中）。',
      '请完全依据用户文档完成改造，不要凭空补充任何新事实、新数字或新来源。',
    ].join('\n')
  }
  const body = results
    .map((result) => `[${result.n}] ${result.title}\n    ${result.url}\n    ${String(result.snippet || '').slice(0, 300)}`)
    .join('\n')
  return [
    '## 联网检索结果（用于补充与校验，不得偏离用户文档主题）',
    queries.length ? `检索关键词：${queries.join(' / ')}` : '',
    '```',
    body,
    '```',
    '使用规则：',
    '- 只能用于丰富、更新、校验用户文档里的事实与数据，让结论更可信；不得把这份 PPT 写成检索结果的综述。',
    '- 与用户文档冲突时以用户文档为准，可在该页 notes 里注明差异。',
    '- 引用检索结果里的具体数据时，按上面的编号加角标（仅在需要来源标注时）。',
  ]
    .filter(Boolean)
    .join('\n')
}

/**
 * @param {{content: string, stylePreference?: string, showCitations?: boolean,
 *   searchResults?: Array, searchQueries?: string[], maxSlides?: number,
 *   maxBulletsPerSlide?: number}} input
 */
export function buildMessages({
  content,
  stylePreference = '',
  showCitations = false,
  searchResults = [],
  searchQueries = [],
  maxSlides = 20,
  maxBulletsPerSlide = 6,
}) {
  // Numbering is decided once here and used by BOTH the citation list and the
  // search block, so a marker the model writes always maps to a real source.
  const numbered = searchResults.map((result, index) => ({
    ...result,
    n: index + (showCitations ? 2 : 1), // [1] is reserved for the user's document
  }))
  const withDocumentSource = showCitations
    ? [{ n: 1, title: '用户提供的文档（本文档）', url: '' }, ...numbered]
    : []

  const system = [
    '# 角色',
    '你是资深演示设计架构师与信息设计专家。你的任务是把用户提供的文档改造成一份可直接演示的网页 PPT 结构，',
    '并**自主完成全部视觉设计决策**（配色、版式、配图风格、字体气质、页面类型），不要套用固定模板。',
    '',
    '# 输出协议（必须严格遵守）',
    '- 一行一个 JSON 对象（JSONL）：不要输出数组、不要用 markdown 代码围栏、不要任何解释文字或前后缀。',
    '- **第一行必须是 design 对象**，且只输出一次；之后每一行是一页。',
    '- 每页常用字段：`type`、`layout`、`eyebrow`、`title`、`subtitle`、`takeaway`、`bullets`/`cards`/`stats`/`chart`/`compare`/`timeline`/`table`/`quote`、`image`、`citations`、`notes`；用不到的字段直接省略，不要输出 null 或空数组。',
    '- 每行必须在一行内写完，字符串中不要出现未转义的换行符；第一行第一个字符是 `{`。',
    '- 使用与用户文档相同的语言（中文文档用简体中文）。',
    '',
    '# 严禁占位符文字',
    '任何视觉元素都必须通过字段表达：配图用 `image.prompt`，图表用 `chart`，关键数字用 `stats`。',
    '绝不允许把「这里要放什么」写成正文，例如：封面页 / 收尾页 / 结束页 / 封面主图：… / 配图：… / 图片：… /',
    '此处插入… / （此处省略）/ 谢谢观看 / Slide 1。这些文字出现在标题或要点里都算失败。',
    '',
    '# 第一部分：design（整体视觉系统，必须最先输出）',
    `{"design":{"styleName":"风格名","rationale":"一句话说明你的设计推导","palette":${paletteLine()},"typography":{"heading":"system-ui","body":"system-ui","scale":1,"headingWeight":700,"letterSpacing":0},"decor":{"style":"geometric","imageStyle":"英文配图风格描述","radius":14,"pattern":"grid"}}}`,
    '设计规则：',
    '- 由文档语义、行业、受众、情绪推导视觉语言；`styleName` 用中文短语命名这套设计。',
    '- 深色底配浅字或浅底配深字，正文与背景对比度不低于 4.5:1；accent 主强调，accent2 次强调，surface 用于卡片底。',
    '- `typography.heading`/`typography.body` 只能从这些安全字族中选择：',
    '  `system-ui`、`"Noto Sans SC"`、`"Source Han Sans SC"`、`Inter`、`Arial`、`Georgia`、`"Times New Roman"`、`Consolas`、`ui-monospace`。',
    '- `decor.style` 描述装饰语言（geometric / editorial / organic / brutalist / minimal / technical 等），',
    '  `decor.imageStyle` 是一段英文，用来给所有配图统一风格。',
    '- design 里不要出现任何具体页面内容。',
    '',
    '# 第二部分：页面类型目录（按内容语义选择，每种类型只定义结构）',
    '| type | 什么时候用 | 关键字段 |',
    '| --- | --- | --- |',
    '| cover | 全文封面 | title、subtitle，可给 image + layout |',
    '| section | 章节分隔，标识一个大的逻辑段落 | title、subtitle、eyebrow |',
    '| bullets | 观点、原因、要素清单 | bullets（≤ 上限条）或 cards |',
    '| stats | 出现 2-4 个关键数字/指标 | stats:[{value,unit,label,delta}] |',
    '| chart | 出现可比较的数值序列、趋势、占比 | chart:{kind:bar\\|line\\|pie\\|donut,title,unit,series:[{name,data:[{label,value}]}]} |',
    '| compare | 两种方案 / 前后对比 / 优缺点 | compare:{left:{title,items[]},right:{title,items[]}} |',
    '| timeline | 步骤、阶段、时间线、流程 | timeline:[{label,title,text}] |',
    '| table | 多列结构化信息（≥3 列或 ≥3 行） | table:{headers[],rows[][]} |',
    '| quote | 金句、结论性判断、他人观点 | quote:{text,attribution} |',
    '| references | 参考来源页（仅需要来源标注时输出） | bullets 列出编号来源 |',
    '| closing | 收尾：结论、行动建议、下一步（必须是真实内容） | bullets 或 cards |',
    '',
    '# 第三部分：版式设计（每页都要自主完成一次版式设计，不要只堆文字）',
    '每页除了内容字段，还要给出 `layout`（版式变体），并按需给出 `eyebrow`、`takeaway`、`cards`：',
    '```',
    'cover      : cover-center | cover-split | cover-image',
    'section    : section-number | section-split',
    'bullets    : bullets-list | bullets-cards | bullets-split | bullets-numbered',
    'stats      : kpi-strip | stat-cards',
    'chart      : chart-full | chart-split',
    'compare    : compare-columns | compare-table',
    'timeline   : timeline-horizontal | timeline-vertical | timeline-cards',
    'table      : table-full | table-split',
    'quote      : quote-hero | quote-split',
    'closing    : closing-cards | closing-cta',
    'references : references-list',
    '```',
    '版式规则（决定成品像不像「精心做的商业 PPT」）：',
    '- 每页必须有清晰的三段节奏：**标签/标题区 → 内容区 → 结论区**（takeaway / 来源 / 页码由渲染器排版）。',
    '- `eyebrow`：标题上方的短标签（≤ 8 字，例如「01 现状」「问题」「结论」），用来建立层级，不要写成句子。',
    '- `takeaway`：本页一句话判断或结论（≤ 24 字），会渲染成底部强调条；**stats 与 chart 页必填**，其他页有余力就写。',
    '  注意：takeaway 是判断，不是重复标题，也不是把要点再抄一遍。',
    '- `cards`：内容能拆成 2-4 个并列分组时使用：[{"title":"分组名","text":"一句话说明"}]，渲染成卡片分区；',
    '  用了 cards 就不要再把同样的内容写进 bullets。',
    '- `-split` 版式表示「文字 + 配图左右分栏」，此时必须给 image；`-cards` 表示栅格分区；`kpi-strip` 是一行强调数字。',
    '- **相邻页面不要用同一个 layout，整份 PPT 至少出现 4 种不同 layout**。',
    '',
    '精致度清单（每条都会被渲染器放大成视觉效果，请严格遵守）：',
    '- **一页只允许一个视觉重心**：要么配图、要么数据（stats/chart）、要么卡片，不要三种同时堆叠。',
    '- 标题 ≤ 18 字，副标题 ≤ 30 字且不要复述标题；标题是结论性的短句，不要用「关于……的介绍」这类空话。',
    '- bullets **最多 5 条**，每条 ≤ 24 字，只写判断与要素，不要写完整句、不要带「我们」「本季度」这类冗词。',
    '- stats：2-4 项，必须有 `unit`；有对比关系就补 `delta`（如「-42%」）。',
    '- chart：`series` ≤ 3 条、每条 3-6 个数据点；趋势用 line、比较用 bar、占比用 pie/donut；配 title 与 unit。',
    '- cards：2-4 组，标题 ≤ 10 字、说明 ≤ 24 字；timeline：3-5 步，title ≤ 10 字、text ≤ 20 字。',
    '- table：3-4 列 × 3-6 行，单元格 ≤ 12 字；quote：≤ 40 字。',
    '- 内容少时选择留白大的版式（cover / section / quote），内容多时才用 cards / table 承载；',
    '  **宁可拆成两页，也不要让一页塞满**。',
    '- 留白是设计的一部分：标题区、内容区、结论区之间由渲染器给出统一间距，你只需控制每页的内容量与版式选择。',
    '',
    '# 第三部分：内容改造规则（这是信息设计，不是摘要）',
    '- 把文档里的数据、流程、对比关系**转成结构化可视化**，不要把整段文字塞进要点。',
    '- 有 2 个以上可比较数字 → `stats` 或 `chart`；不要把关键数字埋在 bullets 里。',
    '- 有先后顺序 / 步骤 / 阶段 → `timeline`。',
    '- 有 A 与 B 的对照 → `compare`（左右两栏）。',
    '- 有多列信息 → `table`。',
    '- `bullets` 只用于观点、原因、要素清单；每条 ≤ 34 字，不要整句照抄。',
    `- 页数控制在 3 到 ${maxSlides} 页之间，按逻辑结构划分；每页只讲一件事，信息密度均匀。`,
    '- 保留原文的数字、专有名词、时间点，不要改写数值。',
    '',
    '# 第四部分：可读性硬性下限（不可违反）',
    `- 单页 bullets **最多 ${maxBulletsPerSlide} 条**；超过就拆成多页，后一页标题可用「（续）」。`,
    '- 单页正文总量控制在一屏内：bullets 合计不超过约 120 字；表格不超过 6 行 × 4 列；stats 不超过 4 项。',
    '- 正文字号由渲染器保证不小于 18px，页面四周留白不少于 10%：你只需保证内容量不越界，',
    '  不要试图用「缩小字号、堆更多内容」来绕过。',
    '',
    styleSection(stylePreference),
    '',
    citationSection(showCitations, withDocumentSource),
    '',
    '# 第五部分：配图',
    '- 需要配图的页面输出 `image`：{"prompt":"english keywords","placement":"background|side|none","alt":"中文简短说明"}。',
    '- **`prompt` 必须是英文**，并且会被当作图片检索/生成的关键词短语**原样使用**，所以：',
    '  1) 只写英文小写单词与逗号，不要写中文、句子、标点或引号；',
    '  2) 要**具体、有画面感**：写成「主体 + 场景 + 氛围/风格 + 构图或光线」的组合，',
    '     例如 `modern office team reviewing charts, warm afternoon light, wide shot`；',
    '  3) **不要用抽象概念词**（如 success、growth、future、innovation、technology 单独出现），',
    '     要落到能拍出来/画出来的具体东西（人物、物件、场所、图表、材质、光线）；',
    '  4) 不要包含任何文字、字母、logo、水印（否则图上会出现乱码文字）；',
    '  5) 词数以 6-14 个单词为宜。',
    '- 风格要与 `design.decor.imageStyle` 一致，让整套配图看起来出自同一系列。',
    '- 只在封面、章节页或确有视觉表达价值的页面配图（建议 2-4 页）；使用了 `-split` / `cover-image`',
    '  这类含图版式的页面**必须**给出 image。',
    '- 图表页（chart / stats / table）不要配图，视觉焦点留给数据。',
    '- `placement`：background 表示整页背景图，side 表示与文字并排的半幅图，none 表示不显示。',
    '',
    searchSection(numbered, searchQueries),
    '',
    '# 现在开始',
    '第一行输出 design，第二行开始逐页输出。不要输出任何解释。',
  ].join('\n')

  return [
    { role: 'system', content: system },
    { role: 'user', content: `这是需要改造的文档：\n\n${content}` },
  ]
}

/**
 * Cheap server-side keyword extraction for the search step — no extra model call.
 * Returns the document's leading title plus a couple of data-bearing phrases.
 */
export function buildSearchQueries(content, { max = 3 } = {}) {
  const lines = String(content || '')
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
  const queries = []
  const push = (value) => {
    const cleaned = String(value || '')
      .replace(/[「」“”"'（）()【】\[\]]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
    if (cleaned.length >= 4 && !queries.some((existing) => existing === cleaned)) queries.push(cleaned)
  }

  if (lines[0]) push(lines[0].slice(0, 40))
  const dataLines = lines.slice(1).filter((line) => /\d/.test(line))
  for (const line of dataLines) {
    if (queries.length >= max) break
    push(line.split(/[，。；;]/)[0].slice(0, 30))
  }
  for (const line of lines.slice(1)) {
    if (queries.length >= max) break
    if (line.length >= 8) push(line.slice(0, 24))
  }
  return queries.slice(0, max)
}
