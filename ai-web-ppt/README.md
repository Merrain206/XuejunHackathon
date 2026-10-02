# AI 网页 PPT 生成器

把一段文档改造成一份**信息设计过**的网页 PPT：AI 先推导整套视觉系统（配色 / 字体 / 装饰 / 页面类型），
再逐页输出结构化页面；后端流式下发进度，前端实时渲染成可全屏、方向键翻页的演示，
并可导出**离线单文件 HTML** 与 **真正的 .pptx**（背景、配图、图表、来源标注、单击逐条动画都嵌进幻灯片）。

---

## ① 装什么

| 需要 | 版本 | 说明 |
| --- | --- | --- |
| Node.js | **20.12+（推荐 22 LTS）** | 唯一运行时。不需要 Python、不需要 LibreOffice/pandoc、不需要本地模型 |
| 网络 | 能访问模型接口（默认 `api.deepseek.com`） | 配图为可选增强，取不到会静默降级为 CSS 渐变 |
| Docker（可选） | 任意较新版本 | 不想装 Node 就用容器跑，见 `DEPLOY.md` 第 4 节 |

```bash
# Windows：双击  start.bat
# macOS / Linux：
chmod +x start.sh && ./start.sh
```

脚本会自动：建 `.env`（缺 Key 时提示你去填并暂停）→ 装依赖 → 构建前端 → 跑自检 → 启动服务。
任何一步失败都会停住等你按键，不会一闪而过。

## ② 改哪行配置

只改项目根目录的 **`.env`**（从 `.env.example` 复制，已被 `.gitignore` 排除）：

```ini
DEEPSEEK_API_KEY=sk-xxxxxxxx      # 必填：DeepSeek 控制台 → API Keys
MODEL_NAME=deepseek-flash         # 必填：模型名（代码里不写死）
BASE_URL=https://api.deepseek.com/v1   # 换供应商/网关时改这里
HOST=127.0.0.1                    # 局域网访问改成 0.0.0.0（注意：接口无鉴权）
PORT=8787
```

其余都是可选（配图、联网检索、日志级别、输出目录、worker 数、跨域白名单、PPTX 动画模式），
每一项都在 `.env.example` 里带注释。**缺必填项时服务会拒绝启动并告诉你改哪行**，
不会静默用本机默认值跑起来。

## ③ 浏览器开哪个地址

```
http://127.0.0.1:8787            ← 应用（HOST/PORT 改了就换成你的值）
http://127.0.0.1:8787/health     ← 环境自检页：缺什么、怎么修，一眼可见
http://127.0.0.1:8787/api/health ← 同一份自检的 JSON（给监控/脚本用）
```

局域网里给别人用：`HOST=0.0.0.0` 后访问 `http://<本机IP>:8787`（该服务**没有登录鉴权**，只在可信网络里这样开）。

## 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| 启动即退出，提示"配置不完整" | `.env` 缺 `DEEPSEEK_API_KEY` 或 `MODEL_NAME`；填好即可。只想先看界面：`ALLOW_INCOMPLETE_CONFIG=1` |
| 端口被占用（EADDRINUSE） | 改 `.env` 的 `PORT`；Windows `netstat -ano \| findstr :8787`，macOS/Linux `lsof -i :8787` |
| API Key 从哪来 | DeepSeek 控制台 → API Keys 创建；换供应商时同时改 `BASE_URL` + `MODEL_NAME` |
| 页面空白 / 404 | 前端没构建：`npm run build`（或直接用 start 脚本，它会自动构建） |
| 导出后 PPT 排版/字重不一致 | 目标机器缺 AI 选用的字体（如 `Noto Sans SC`）。网页端有字体栈兜底，Office 会替换字体；要严格一致就装思源黑体，或在风格偏好里写"只用系统字体" |
| 生成超时 | 调大 `.env` 的 `REQUEST_TIMEOUT_MS`（默认 180000ms），或缩短文档 / 调小 `MAX_SLIDES` |
| 导出 PPTX 后**动画不播放** | 浏览器下载的文件被标记为"来自 Internet"，PowerPoint 进受保护视图会禁用动画。用界面的「💾 存到项目目录」，或对文件右键→属性→解除锁定 |
| 权限不足 / 写输出目录失败 | 设 `OUTPUT_DIR` 到可写位置；容器里已把 `/app/.exports` 交给 `node` 用户 |
| 想少刷日志 | `LOG_LEVEL=warn` |

完整的移植清单、Docker、worker 数计算、exe 方案见 **[`DEPLOY.md`](DEPLOY.md)**。

---

```
文档 ──▶ POST /api/generate (SSE)
           │  status → search → design → progress… → slide… → done
           ▼
      React 编辑器 ──▶ 演示视图（全屏 · ← → 翻页 · 手动覆盖配色）
                          ├─▶ 导出离线 HTML（内联 CSS/JS/配图，file:// 直接打开）
                          └─▶ 导出 PPTX（pptxgenjs，服务端嵌入素材 + 单击逐条动画）
```

## 开发模式（改代码时用）

```bash
npm install
npm run dev        # API 8787 + 前端 http://localhost:5173（带 HMR）
npm run check      # 环境自检
npm test           # 全部测试（单元 + 集成 + 真实浏览器 E2E）
```

## 界面上有什么

| 区域 | 说明 |
| --- | --- |
| ① 文档内容 | 粘贴要改造的文档（≥20 字符） |
| ② 风格偏好（**选填**） | 用自然语言描述期望（语言风格、视觉调性、氛围）。留空 = 由 AI 按文档语义自主决定。附几个快捷词可点选 |
| ③ 来源标注开关 | 文案「显示信息来源 / 标注数据出处」，**默认关闭** |
| 生成中 | 三个阶段条（联网检索 / 风格设计 / 逐页生成）+「正在生成第 X 页…」+ 已收页面卡片 + AI 设计色板预览 |
| 演示视图 | 全屏(F)、← → 翻页、圆点跳页、点击/滑动翻页、**配色可手动覆盖**（AI 自主 / 4 套预设） |
| 导出 | 「⬇ 导出 HTML」（离线单文件）、「⬇ 导出 PPTX」 |

请求体是三个独立字段，不会把风格描述拼进正文：

```json
{ "content": "文档正文", "stylePreference": "主色用深蓝；高级感", "showCitations": true }
```

## AI 负责的设计决策（代码里不写死内容相关样式）

模型输出**一行 design + 每行一页**的 JSONL：

```jsonc
{"design":{"styleName":"深海金融","rationale":"…",
  "palette":{"bg":"#08111f","bgAlt":"#0f2036","fg":"#eaf2ff","muted":"#93a8c6","accent":"#4fc3f7","accent2":"#ffb74d","surface":"…","border":"…"},
  "typography":{"heading":"Noto Sans SC","body":"system-ui","scale":1,"headingWeight":800,"letterSpacing":0},
  "decor":{"style":"technical","imageStyle":"english style for images","radius":16,"pattern":"grid"}}}
{"type":"stats","title":"关键指标","stats":[{"value":"2.9","unit":"天","label":"平均交付周期","delta":"-42%"}]}
{"type":"chart","title":"趋势","chart":{"kind":"line","series":[{"name":"交付","data":[{"label":"Q1","value":5}]}]}}
```

* `design` 变成 CSS 变量（`--s-accent`、`--s-heading-font`、`--s-radius`…），网页与导出文件共用同一份样式与同一套 React 组件。
* 字体只接受白名单字族；非法颜色/越界数值会被兜底或钳制（`server/deck.js`）。
* 页面类型 11 种，每种只有**结构**：`cover / section / bullets / stats / chart / compare / timeline / table / quote / references / closing`。
  前端按类型渲染差异化版式；未知类型退化为要点页。
* 图表是自绘 SVG（bar/line/pie/donut），零依赖、可离线；PPTX 侧用 pptxgenjs 原生图表。

## 两条可读性硬下限（已用代码强制）

| 下限 | 实现 |
| --- | --- |
| 单页要点 ≤ 6 条 | 提示词约束 **+** 服务端 `splitOverflowSlides()` 自动拆页（续页标题加「（续）」） |
| 正文字号 ≥ 18px、四周留白 ≥ 10% | CSS：`--f-body: max(18px, 1.32cqw)`、`padding: 10cqh min(10cqh, 10cqw)`（以幻灯片短边为基准），E2E 实测断言 |

## 配图与联网检索

**配图链路**（`server/images.js`，按顺序尝试，任何一步失败都**静默**降级）：

| 顺序 | 提供方 | 说明 |
| --- | --- | --- |
| 1 | 已配置的文生图 API | `IMAGE_API_URL` + `IMAGE_API_KEY`（OpenAI 兼容 `/images/generations`） |
| 2 | **Openverse 图库**（默认，免密） | 关键词检索，**只取 CC0 / 公有领域**（无水印、无需署名），返回真实图片 |
| 3 | Pollinations | **仅当配置 `POLLINATIONS_TOKEN` 时启用** —— 匿名调用现已强制加 pollinations.ai 水印，`nologo=true` 不再生效 |
| 4 | CSS 渐变装饰 | 全部失败时用设计令牌生成渐变/网格装饰，**页面上不出现任何错误文案** |

关键词全链路保证：模型必须输出**英文**且「主体+场景+氛围」具体可画面化 → 服务端 `normalizeImagePrompt()`
剔除非 ASCII、去重、限长（不足两个词直接放弃配图并走装饰）→ 拼 URL 与检索查询一律 `encodeURIComponent()`。
浏览器端只走同源代理 `/api/image`（严格白名单，不是开放代理），既避免 CORS 也保证匿名水印接口不会被用到。

**联网检索**（`server/search.js`，可插拔）：`tavily | serper | duckduckgo | custom`，未配置时自动降级为「不检索」。
检索结果注入提示词，并明确要求「只用于补充与校验、不得偏离文档主题」。

**来源标注（开关打开时）**：
1. 提示词要求：数据/事实处加 `[n]` 角标，每页 `citations` 列出该页来源，末尾追加 `type:"references"` 的「参考来源」页。
2. 服务端**防伪造**（`reconcileCitations()`）：只有真的检索到的 URL 或文档自身（空 URL）才算合法来源；
   模型编造的链接会被丢弃、对应角标从正文移除、编号重排为连续；来源页由服务端按真实来源重建。
3. 关闭时：角标、`citations`、来源页全部不输出（提示词与后处理双重保证）。

## 占位符清理

提示词里给出黑名单（封面页 / 收尾页 / 封面主图：… / 配图：… / 此处插入… / 谢谢观看 / Slide 1 …），
渲染前再做一次结构清洗：占位符要点整条删除、行内括号占位（如「增长 42%（配图：折线图）」）剥掉、
整页只剩占位符则丢弃、标题缺失时按页型补一个真实标题。导出文件同样不含占位符（E2E 断言）。

## .env 配置

| 变量 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `DEEPSEEK_API_KEY` | ✅ | — | 只在 `.env` 配置；代码零硬编码，前端拿不到 |
| `MODEL_NAME` | ✅ | — | 模型名（如 `deepseek-flash`），缺失时明确报错 |
| `BASE_URL` | | `https://api.deepseek.com/v1` | OpenAI 兼容端点 |
| `PORT` | | `8787` | 服务端口 |
| `MAX_SLIDES` / `MAX_BULLETS_PER_SLIDE` | | `20` / `6` | 页数与单页要点上限 |
| `REQUEST_TIMEOUT_MS` | | `180000` | 单次生成总超时 |
| `IMAGE_PROVIDER` | | `auto` | `auto / api / openverse / pollinations / none` |
| `IMAGE_API_URL` / `IMAGE_API_KEY` / `IMAGE_MODEL` | | — | 可选：接入已有文生图 API（OpenAI 兼容 `/images/generations`） |
| `OPENVERSE_URL` | | `https://api.openverse.org/v1/images/` | 免密图库地址（只取 CC0/公有领域） |
| `POLLINATIONS_TOKEN` | | — | **填了才用 Pollinations**（匿名调用带水印） |
| `IMAGE_URL_TEMPLATE` | | Pollinations 模板 | 自定义模板：`{prompt}{width}{height}{token}` |
| `IMAGE_PROXY_ALLOW` | | — | 额外允许 `/api/image` 代理的域名（逗号分隔） |
| `SEARCH_PROVIDER` | | `none` | `none / tavily / serper / duckduckgo / custom` |
| `SEARCH_API_KEY` / `SEARCH_API_URL` / `SEARCH_MAX_RESULTS` | | — / — / `4` | 检索服务配置 |

## 接口

| 接口 | 说明 |
| --- | --- |
| `GET /api/health` | 配置状态 + `capabilities`（配图/检索能力），前端据此显示能力徽章与开关提示 |
| `POST /api/generate` | `{content, stylePreference, showCitations, maxSlides}` → SSE：`status` / `search` / `design` / `progress` / `slide` / `done` / `error` |
| `GET /api/image?prompt=&w=&h=` | 同源图片代理（白名单） |
| `POST /api/export/pptx` | 接收 deck（JSON 或表单 `payload`），返回 `.pptx`（原生表单提交下载，不依赖用户手势） |

## 界面呈现原则

界面上**只呈现正向信息**：模型名、风格名、页数、导出按钮。
配图失败、检索未配置、提供方名称等实现细节一律不显示——配图失败时静默换成 CSS 渐变装饰，
用户看到的是一个照常成立的版面，而不是一条错误提示。

## 版式设计（AI 自主完成，代码不写死内容样式）

每页除了内容字段，模型还要给出 `layout`（版式变体）与可选的 `eyebrow` / `takeaway` / `cards`：

| 页型 | 可用版式 |
| --- | --- |
| cover | `cover-center` `cover-split` `cover-image` |
| section | `section-number` `section-split` |
| bullets | `bullets-list` `bullets-cards` `bullets-split` `bullets-numbered` |
| stats | `kpi-strip` `stat-cards` |
| chart | `chart-full` `chart-split` |
| compare | `compare-columns` `compare-table` |
| timeline | `timeline-horizontal` `timeline-vertical` `timeline-cards` |
| table | `table-full` `table-split` |
| quote | `quote-hero` `quote-split` |
| closing | `closing-cards` `closing-cta` |

**页面结构由渲染器统一保证三段分区**（这是「精致感」的地基，模型不需要操心）：

```
┌ 标题区   eyebrow（带短横线的标签） + 标题 + 副标题
├ 正文区   内容块在剩余空间里居中/均分，间距全部来自统一间距刻度
└ 页脚区   takeaway 结论条 → 细分割线 → 来源标注 + 页码（03 / 10）
```

* **间距刻度** `--sp-1…--sp-5`（基于幻灯片高度），**字号刻度** `--f-label / --f-meta / --f-body / --f-body-lg / --f-lead / --f-title / --f-hero / --f-stat`，
  圆角/阴影/细线统一为 `--r-* / --elev-1 / --hair`；每页还有一层极淡的纹理（`data-pattern`：grid/dots/waves/plain）。
* 主次靠**对比度**而非颜色堆叠：标题字号是正文的 ~2 倍、eyebrow 小字大字距、KPI 数字最大且带 tabular-nums、
  说明文字统一 muted 色；卡片/图表/表格共用同一套表面色 + 细描边 + 轻阴影。
* 提示词里写死了「精致度清单」：标题 ≤18 字且为结论句、bullets ≤5 条 × ≤24 字、stats 必带 unit（有对比补 delta）、
  chart 系列 ≤3 × 3-6 点、cards 2-4 组（标题 ≤10 字）、table 3-4 列 × 3-6 行、一页只允许一个视觉重心。
* 服务端兜底：`-split` 版式若拿不到配图会自动降级为默认版式；**前端在配图失败时会把分栏收成单栏**，
  绝不留一个像「缺图」的空框（`cover-image` 的整页背景图则降级为渐变装饰）。
* **网页与 PPTX 同源**：`server/pptx.js` 渲染同一套字段（含 eyebrow / takeaway / cards）。

## PPTX 逐条出现（单击触发）

导出面板里的「PPTX 动画」有三种模式（也支持 `PPTX_BUILD_MODE` 环境变量）：

| 模式 | 做法 | 适用 |
| --- | --- | --- |
| `animation`（默认） | 每页注入**单击触发**的原生 `<p:timing>` 入场动画 | PowerPoint / WPS |
| `steps` | 把每次揭示拆成**累积分页**，放映时按空格逐条出现 | 任何阅读器都生效（页数变多，10 页 → 约 24 页） |
| `both` | 两者都做 | 最大兼容性 |

### 动画编排规则（系统按页面结构自行决定）

| 元素 | 处理 |
| --- | --- |
| 装饰/结构形状（色块、分隔线、卡片底板） | **不动画** —— 它们是页面的静态骨架 |
| 标题、副标题、eyebrow 标签 | **静态** —— 翻到这一页就在，观众立刻知道这页讲什么 |
| ≤10pt 的文字（页码、来源行） | **静态** —— 页脚信息不抢节奏 |
| 正文要点 | **逐条单击出现**：多段落文本框用 `p:bldP build="p"` 拆成独立点击 |
| 同一视觉单元的多个文本框（KPI 数值 + 单位 + 说明） | **合并为一次点击**（按几何聚类：同列且紧邻） |
| 图表 / 表格 | 一次点击 |
| 配图 | 正文之后一次点击 |
| 结论条（takeaway） | **最后**一次点击，作为这页的收束 |
| 只有标题的页面（封面、章节页） | 让副标题承担这一次点击，避免"空屏首帧" |

效果统一为 **Fade（`presetID=10` / `presetClass="entr"` / `filter="fade"`），350ms**，
不混用飞入/弹跳/旋转；触发统一为**单击**（`nodeType="clickEffect"`，主序列起始条件
`<p:cond delay="indefinite"/>` 等待演讲者），不点击就停在当前状态。

### 实现要点（`server/pptxAnimate.js`）

1. 解析每页 `slideN.xml`：形状 id（`p:cNvPr/@id`）、文本（`a:t`）、段落数、几何（`a:off`/`a:ext`）、字号（`a:rPr/@sz`）；
2. 用**标题/副标题/eyebrow/takeaway 文本匹配** + 字号规则把形状分成「静态」与「待揭示」；
3. 对正文形状做**几何聚类**（同列、紧邻 → 一个视觉单元），每个单元一次点击；
4. 生成 PowerPoint 原生写法的点击分组：
   `p:seq → p:cTn(mainSeq) → 每步一个 p:par 组合（首组 delay="indefinite"）→ 效果（clickEffect / withEffect）`。

> ⚠️ 两个会让动画**整体失效**的坑（都已修复并被测试覆盖）：
> 1. **同一页 timing 树里的 `p:cTn/@id` 必须唯一** —— 重复会让 PowerPoint 直接丢弃整棵树
>    （症状：文件里有 `<p:timing>`，动画窗格却是空的）。骨架占 1-2，每个点击组占 2 个，每个效果独占 3 个；
> 2. **效果节点必须真的按 3 个 id 预留**（效果本体 + `p:set` + `p:animEffect`），否则相邻组会撞号；
>    另外：`afterEffect` + 主序列 `delay="0"` 会变成**自动连续播放**，必须改用 `clickEffect` + `delay="indefinite"`。

### 自检工具

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify-pptx-animation.ps1 -Path <your.pptx>
```

它通过 WPS/PowerPoint 的 COM 接口读取**动画窗格**（等价于人工打开「动画」选项卡），逐页列出条目数、
触发方式与效果类型，并直接给出 PASS/FAIL 结论。真实模型样例的实测结果：

```
slide1  effects: 1   OnPageClick=1
slide2  effects: 10  OnPageClick=10
slide3  effects: 13  OnPageClick=6 WithPrevious=7      ← 同一视觉单元共享一次点击
…
effects total: 54 | decoration effects: 0
RESULT: PASS - every effect is a click-triggered fade, decorations untouched
```

### ⚠️ 为什么"动画在放映时不出现"还有一个非 XML 的原因：文件来源标记（MOTW）

浏览器下载的 `.pptx` 会被 Windows 打上 **Zone.Identifier（ZoneId=3，"来自 Internet"）**。
PowerPoint 打开这类文件时进入**受保护视图**，而受保护视图下**动画不播放**（WPS 不这么严格，
所以同一份文件在 WPS 里看着是正常的）。实测证据：

```
.test-tmp\downloads\….pptx    → Zone.Identifier 存在（ReferrerUrl=http://127.0.0.1:…/api/export/pptx）
.explorts\….pptx（应用自己写盘） → 无
```

两种处理方式（任选其一）：

1. **用界面上的「💾 存到项目目录」**按钮：服务端把 pptx 直接写进 `<项目>/.exports/`，
   页面会显示完整路径，从磁盘打开即可 —— 文件没有来源标记，动画正常播放；
2. 继续用「⬇ 导出 PPTX」下载，打开前先解除锁定：右键文件 → 属性 → 勾选「解除锁定」，
   或执行 `Unblock-File .\你的文件.pptx`。

## 动画的两套独立实现与验证工具链

同一份动画契约有两套互不依赖的实现，互为交叉验证：

| 产物 | 语言 | 用途 |
| --- | --- | --- |
| `server/pptxAnimate.js` | Node（应用内置） | 导出时自动注入；命名空间前缀自适应、schema 顺序校验、逐形状 `grpId`/`bldLst` |
| `tools/pptx_animate.py` | Python **纯标准库**（zipfile/re/ElementTree） | 给任何 `.pptx` 后处理加动画，适合 python-pptx 流水线；`inject` / `verify` / `self-test` |
| `docs/pptx-animation-spec.md` | 文档 | XML 契约：骨架、id 分配、分类规则、两种致命失败模式 |
| `docs/pptx-animation-audit.md` | 文档 | 逐条审计发现（按"最可能让 PowerPoint 忽略动画"排序）与证据 |
| `scripts/verify-pptx-animation.ps1` | PowerShell | 通过 WPS/PPT 的 COM 读**动画窗格**：逐页条目数、触发方式、效果类型，并检查 MOTW |

三个独立验证器（缺一不可）：

```powershell
# ① 引擎级：读动画窗格（等价于人工打开「动画」选项卡）
powershell -ExecutionPolicy Bypass -File scripts/verify-pptx-animation.ps1 -Path <deck.pptx>
# ② 结构级：独立实现的校验器（也检查装饰元素是否被误加动画）
python tools/pptx_animate.py verify <deck.pptx> --expect-click
# ③ 回归：124 项自动化测试
npm test
```

审计发现里最值得记的三条（都已被代码与测试覆盖）：

1. `p:seq` 缺少 `prevCondLst`/`nextCondLst` —— 这是让**点击**推进序列（而不是按时间轴 seek 到末尾）的关键；
2. `grpId` 语义 —— 必须按形状/构建组唯一，段落构建用 0,1,2…，不能一个全局计数器糊过去；
3. `p:bldLst` 只覆盖段落构建是不够的 —— 每个被动画的形状都要有条目（动画窗格才会逐条列出）。

另有一处是**我在验收时抓到的**（子代理自查没覆盖）：表格页的 `p:graphicFrame` 内部
`p:nvGraphicFramePr` 里也有一个 `p:extLst`，插入逻辑若简单匹配"第一个 extLst"就会把整棵 timing
塞进表格内部 → 该页动画被引擎整页丢弃。现在只认 `p:sld` 的**直接子** extLst，并有回归测试锁住。

## 目录结构

```
server/
  index.js    入口（读 .env → 启服务）
  config.js   环境变量解析 + 能力报告
  prompt.js   系统提示词：页面类型目录 / 风格约束区（硬约束·软参考）/ 角标规则 / 硬下限 / 占位符黑名单
  deck.js     容错流式解析 + 设计令牌归一化 + 占位符清洗 + 自动拆页 + 来源防伪造
  search.js   可插拔联网检索（tavily/serper/duckduckgo/custom）
  images.js   配图链路：模型地址 → 文生图 API → Pollinations → CSS 装饰；白名单校验
  app.js      Express：SSE 生成、图片代理、PPTX 导出、静态托管
  pptx.js     deck + design → pptxgenjs 幻灯片（背景/图片/原生图表/表格/来源/备注）
web/src/
  App.jsx               状态机：编辑 → 生成 → 演示；配图预取；两种导出
  components/Deck.jsx   应用设计令牌并渲染全部页面（导出用同一组件做静态渲染）
  components/slides/    11 种页型布局 + SVG 图表 + 图片回退链
  shared/design.js      设计令牌 → CSS 变量（含 4 套手动覆盖预设）
  shared/deckCss.js     幻灯片样式（应用与导出共用同一份）
  shared/exportHtml.js  离线单文件外壳（纯字符串函数，可在 Node 里测）
  imageSource.js        浏览器侧配图候选链（代理 → Pollinations）
tests/                  unit（66）+ e2e（10 集成 + 1 真实浏览器验收）
```

## 测试与验证

```bash
npm test          # 全部（unit + 集成 + 真实浏览器）
npm run test:unit # 66 项，秒级
npm run test:e2e  # 先 build，再跑 SSE 集成 + 浏览器验收
```

本轮新增/覆盖的关键场景：

* **提示词契约**：风格偏好只进「风格约束区」不污染正文；硬约束/软参考规则在位；开关开/关两套角标规则；
  检索结果「不得偏离主题」；≤6 条、18px、10%、占位符黑名单都在提示词里。
* **解析与清洗**：design 行 + 任意切分（1/3/7/29 字符）都正确出页；嵌套对象不会被切碎；
  占位符整条删除、行内占位剥离、空标题按页型修复；超限自动拆页。
* **来源防伪造**：编造 URL 被丢弃、角标移除、编号重排连续、来源页按真实来源重建；关闭时全部消失。
* **配图链路**：Pollinations URL 编码与尺寸替换；代理白名单拒绝任意外域；已配置文生图 API 优先；全失败→null→CSS 装饰。
* **PPTX**：ZIP 结构、`ppt/slides/slideN.xml` 内含标题、原生图表/表格、备注、背景嵌入、设计令牌生效。
* **配图链路**：中文关键词被丢弃且不发起任何请求；英文关键词被编码进 URL/检索查询；
  **匿名 Pollinations 绝不使用**（仅 token 配置后启用）；Openverse 只取 CC0/公有领域；
  文生图 API 优先；全失败 → null → CSS 装饰。
* **PPTX 动画**：`planReveal` 顺序为 标题/标签 → 正文逐段 → 图表 → 配图；
  `injectTiming` 生成的 `<p:timing>` 幂等、可解析；无形状或非法 XML 时保持原样；
  真实导出的每一页都带 `<p:timing>`，要点页带 `p:bldP build="p"`。
* **浏览器验收**（Edge + CDP，零测试框架依赖）：
  ① 填风格偏好 + 开来源标注 → 深蓝方案、角标、每页来源、来源页含**真实**链接、无编造链接、无占位符、
  正文 ≥18px、留白 ≥10%、方向键翻页、导出 PPTX（XML 内含标题与来源）与离线 HTML（`file://` 打开可翻页且 **0 个 http 请求**）；
  ② 全部留空 → 风格名/强调色/背景色**都不同**，页面类型与标题序列**完全一致**（内容逻辑不变），无任何角标与来源页。

截图与导出产物在 `.test-tmp/`（已 gitignore）。

> 环境适配（普通机器上透明直通，见 README 说明）：`--test-isolation=none --test-force-exit`（Node 默认会给每个测试文件 spawn 子进程，沙箱下 EPERM）、
> `scripts/vite-run.mjs`（Vite 在 Windows 用 `net use` 探测网络驱动器）、`web/vite.config.mjs` 的 `watch.ignored`（忽略 Vite 自己写的 `.tmpdir`，否则 Windows 下 EBUSY 会把 dev server 打挂）。

## 已知限制

* **未配置检索服务时**，「来源标注」只能指向文档自身（不编造网页）；要拿到网页级来源请配置 `SEARCH_PROVIDER` + Key。
* 配图默认走 Openverse（只取 CC0/公有领域）：图库网络不可达、关键词太泛或没有命中时，
  自动降级为 CSS 渐变装饰——页面上不会有任何提示，也不影响离线可用。
* 动画是**底层 XML 注入**：本机没有 PowerPoint/LibreOffice 可做像素级渲染验证，
  因此验证方式是解析 pptx 的 zip、断言 `ppt/slides/slideN.xml` 里存在合法的 `p:timing` / `p:seq` / `p:bldP` 结构。
  若某个阅读器对时间轴挑剔，最坏情况是该页动画不播放，幻灯片本身仍然完整可读。
* 导出 HTML 是**生成时的静态快照**；PPTX 由服务端渲染，与网页共用设计令牌与页型/版式结构。
* 演示视图无持久化：刷新回到编辑态（导出文件不受影响）。
