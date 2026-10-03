# Ask the Company — Codex 交接记忆

> 状态日期：2026-10-03
> 工作区：`D:\Codes\XueJunHackathon`  
> 当前角色：前端开发；用户负责遇到不确定产品取舍时做决策

## 1. 项目目标

Ask the Company 是 X-Ray「透视·真相」赛道的企业信息理解产品。用户不是阅读大量公告，而是直接向公司提问；系统只基于可追溯 Evidence 生成 Answer、Claim、Signal 和 Chart。思看科技仍是稳定演示基线；动态链路已经从财务问题扩展到治理、审计、股东回报、员工与激励、供应链、研发和风险等普通投资者主题。自动化验收和第二轮真实 DeepSeek API 冒烟已通过，但公告没有披露或 Evidence 不完整时仍必须部分回答或安全拒答，不能描述为任意问题必答。

最高原则：`No Evidence, No Claim`。证据不足时固定回答：

> 根据目前掌握的信息，我无法可靠回答这个问题。

这是 48 小时 Hackathon。优先完成稳定、可讲清楚的垂直闭环，不要引入 LangGraph、多 Agent、复杂 GraphRAG、用户系统或其他非必要基础设施。

## 2. 当前已经完成

> 本节只描述已经完成并验收的能力。动态链路已通过数据库、合成后端、真实库、前端契约和真实 DeepSeek API 冒烟；扩展主题仍需产品负责人继续做浏览器人工验收。

- Next.js 16 + React 19 + TypeScript + Tailwind CSS 4 前端。
- 公司页路由：`/company/{id}`，根路径自动跳转到 `/company/688583`。
- 支持思看科技（688583）、恒生电子（600570）、中国长城（000066）和贝达药业（300558）切换；公司目录集中在 `src/lib/companies.ts`。
- 未知公司代码显示明确的不支持状态，不会套用思看科技数据。
- 切换公司会清空问题、回答、加载状态和 Evidence 抽屉，并取消旧请求，避免迟到响应串台。
- 提问输入、分阶段加载动画、Answer、Claim、X-Ray Signal、ECharts、Evidence 列表和 Evidence 抽屉。
- Claim 和 Signal 中的 Evidence ID 均可点击并打开对应证据。
- 3 个问题对应 3 套独立回答，不再复用同一段总结。
- 后端适配器把 FastAPI snake_case 响应转换成前端 camelCase 类型。
- 思看科技未配置后端时使用 Mock；后端超时、HTTP 错误或 JSON 解析失败时自动切换已核验演示数据，并显示 `DEMO FALLBACK`。
- 其他三家公司不使用思看科技 Mock；动态服务不可用时返回证据不足结构并显示 `SERVICE UNAVAILABLE`。
- 未准备的问题返回证据不足回答，不伪造公司事实。
- 数字高亮、Evidence 可靠度等级与原因说明已经完成；官方来源识别覆盖上交所与巨潮资讯（含 `https://static.cninfo.com.cn/`），后端会把该静态站的历史 HTTP 直链规范化为 HTTPS；可选 `verification_status` 支持 `verified`、`auto`、`pending`。
- FastAPI 响应具有最小运行时校验；非法结构在思看科技降级为已核验 Demo，其他公司进入服务不可用状态。
- 后端三条确定性回答、真实 SQLite 只读访问、上交所权威来源和四问样例已经接入代码库。
- 四公司动态 Evidence-first 检索、DeepSeek 结构化回答与候选库重建工具已接入代码库；后端会过滤 `excluded=1`，Claim/Signal 数字按自身引用 Evidence 硬校验，无显式 Evidence ID 时不猜出处。
- 非思看公司的财务、治理、审计、股东回报、员工与激励、供应链、关联关系、研发和风险问题会进入动态链路；回答不得声称覆盖全部经营、法律、行业或合规风险。
- 扩展库 v2 共有 2254 条 Evidence，其中 2158 条可参与回答、96 条隔离；可用 Evidence 均为同页连续原文且不超过 200 字符，FTS 2845/2845。2026-10-03 人工验收通过后，v2 已晋升为稳定 `cninfo.db`。
- 动态图表不交给模型生成，而是由后端从最终引用 Evidence 确定性构建。当前支持 `line` / `bar`，趋势问题最多给 3 张折线图，最近一期复合指标会按单位拆成最多 3 张柱状图。

## 3. 稳定 Demo 脚本

| 问题 | 核心 Signal | 图表 | Evidence |
|---|---|---:|---|
| 你的收入结构发生了什么变化？ | 核心产品收入增长，但依赖度下降 | 有 | 招股书 PDF 第 322 / 324 页（另 321 页为收入合计与同比） |
| 你最近真的赚钱吗？ | 收入增长，但利润和现金流增长未同步 | 有 | 2025 半年报第 8 页（会计数据）与第 9 页（现金流下降原因） |
| 目前最值得关注的风险是什么？ | 增长依赖持续技术领先与下游需求 | 无 | 2025 半年报第 2 页（未发生实质影响的重大风险）与第 43 页（风险因素） |

关键数字：

- 2021—2023 年，便携式 3D 扫描仪收入占比从 78.19% 降至 57.87%。
- 同期跟踪式产品收入从 1,893.69 万元增至 7,222.66 万元，占比从 11.77% 升至 26.58%。
- 2025 年上半年营业收入同比增长 17.70%，归母净利润增长 2.06%，扣非归母净利润下降 2.93%，经营现金流净额下降 32.59%。
- 风险回答必须说明这些是公司披露的风险提示，不代表风险已经发生。

## 4. 数据来源

- 招股说明书：`https://static.sse.com.cn/stock/disclosure/announcement/c/202408/001845_20240816_R2YE.pdf`
- 2025 年半年度报告：`https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2025-08-28/688583_20250828_9F77.pdf`
- 页码采用 PDF 查看器页码，不是印刷页码。
- `cninfo.db` 和 `ruvector.db` 是仓库根目录下被忽略的数据文件，不要删除、覆盖或提交。前端不直接读取 SQLite；真实接口由 FastAPI 只读访问 `cninfo.db`，未配置或异常时使用 `src/lib/mock-data.ts`。

### 4.1 页码逐页核验结论（2026-10-02，后端联调轮）

两份 PDF 已用 pypdf 逐页抽取文本核实，**招股书三条证据不在同一页**：

| 事实 | 注册稿 PDF 查看器页 | 该页页脚 | 说明 |
|---|---:|---|---|
| 便携式扫描仪 78.19% → 57.87% | **322** | 1-1-321 | 逐字核到 |
| 跟踪式产品 11.77% → 26.58% | **324** | 1-1-323 | 逐字核到 |
| 主营收入合计 27,170.18 / 同比 31.88% | **321** | 1-1-320 | 逐字核到 |

⚠️ **PDF 查看器第 323 页并不支持跟踪式引用** —— 该页内容是「彩色 3D 扫描仪 … 五大系列
便携式 3D 扫描仪划分依据」，没有跟踪式收入数据。前后端均已使用第 **324** 页。

⚠️ 注册稿共 **506 页**，与巨潮上市稿（520 页）**不是同一份**：页数不同、偏移也不固定
（便携式那段：上市稿 329 → 注册稿 322；跟踪式那段：上市稿 332 → 注册稿 324）。
所以后端 `cninfo.db` 无法为注册稿页码作证，只能用 PDF 本体核验。

半年报（269 页）与库内同名文档为同一份，**第 2 / 8 / 9 / 43 页已与库逐字对齐**。

## 5. 前后端契约

请求：

```http
POST /companies/688583/ask
Content-Type: application/json

{"question":"你最近真的赚钱吗？"}
```

后端返回 snake_case：

```json
{
  "answer": "...",
  "claims": [{ "id": "...", "text": "...", "evidence_ids": ["..."] }],
  "signals": [{ "id": "...", "type": "divergence", "title": "...", "severity": "attention", "description": "...", "evidence_ids": ["..."] }],
  "charts": [{ "id": "...", "type": "line", "title": "...", "subtitle": "...", "unit": "%", "periods": ["..."], "series": [{ "name": "...", "values": [1] }], "evidence_ids": ["..."] }],
  "evidence": [{ "id": "...", "category": "financial", "period": "...", "content": "...", "document_id": "...", "document_title": "...", "source_page": 8, "source_quote": "...", "source_url": "..." }],
  "suggested_questions": ["..."]
}
```

`sourceMode: "api" | "mock" | "fallback" | "unavailable"` 由前端适配层添加，后端无需返回。图表类型支持 `line` 和 `bar`。不要让模型或后端返回 ECharts JavaScript，只返回由最终 Evidence 确定性构建的语义图表数据。

Evidence 可选返回 `verification_status: "verified" | "auto" | "pending"`；字段出现时前端会校验枚举值。

`source_url` 必须是没有 `#page=` fragment 的裸 PDF URL；`source_page` 独立返回，由前端在打开原文时统一追加页码锚点。

## 6. 关键文件

- `src/components/company-experience.tsx`：公司页主体、提问状态、加载步骤、Answer/Signal/Evidence 交互。
- `src/components/evidence-chart.tsx`：ECharts 折线图/柱状图渲染，支持正负值范围。
- `src/lib/companies.ts`：四家公司资料、首页指标和推荐问题的单一来源。
- `src/lib/mock-data.ts`：三套 Demo 回答、真实证据和问题路由。
- `src/lib/api.ts`：FastAPI 请求、45 秒超时、字段转换和按公司隔离的失败降级。
- `src/lib/types.ts`：前端统一数据类型。
- `src/app/globals.css`：页面样式和动画。
- `README.md`：启动方法与接口示例。
- `expand_investor_evidence.py`：从公告全文按 54 个普通投资者主题扩展 Evidence，始终写入新候选库。
- `xray-backend/investor_topics.py`：扩库与动态问题路由共用的主题目录。
- `docs/INVESTOR_EVIDENCE_EXPANSION.md`：扩展库统计、主题范围、缺口和复现命令。
- `AGENTS.md`：下次 Codex 必须遵守的项目约束。

## 7. 运行与验证

```powershell
npm install
npm run dev
```

打开 `http://localhost:3000/company/688583`，再使用左侧入口切换四家公司。

真实后端地址通过 `.env.local` 配置：

```text
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### 7.1 后端启动（联调时）

```powershell
cd xray-backend
python -m pip install -r requirements.txt
# 显式指向仓库根目录稳定库，避免误用 xray-backend/data/cninfo.db
$env:DATABASE_PATH = "D:\Codes\XueJunHackathon\cninfo.db"
$env:DB_PATH_STRICT = "true"
python main.py                        # http://127.0.0.1:8000
curl.exe -s http://127.0.0.1:8000/health     # 看 db_path 与 database
```

后端自测（两套必须分开跑，数量以当前测试输出为准）：

```powershell
python -m pytest tests -q
python -m pytest tests_real -q
```

2026-10-02 后端联调轮验证结果：四条问题全部 HTTP 200；三条稳定回答互不相同且各带
收入结构与盈利质量各带 1 个图表，风险问题无图表；未知问题返回固定兜底
且四个数组全空；所有证据均为上交所 HTTPS 裸 PDF URL，页码由前端追加；单条回答耗时
约 70–90 ms（远低于前端 45 秒超时）。

提交前至少运行：

```powershell
npm run build
```

2026-10-02 验证结果：构建通过；四家公司切换、未知代码状态、切换清理和非思看公司断网隔离已完成人工验收。思看科技 3 个问题均返回不同答案，8 个 Claim 的 Evidence 均可点击，Evidence 抽屉正常，浏览器运行时错误为 0。动态 API 已通过 Fake LLM、真实候选库和契约自动化验收；真实 DeepSeek + 浏览器第一轮结果见下节。

### 7.2 真实 DeepSeek + 浏览器第一轮联调（2026-10-02）

联调使用候选库 `cninfo.multicompany.next.db`（SHA-256：`13f95fad520c56539e9b32227c6d48697691ebb56b9c74c20cc3c60fc1567670`），没有替换稳定 `cninfo.db`。模型为 `deepseek-flash`，请求硬超时 40 秒。

- API 首轮财务问题均成功：恒生电子约 5.6 秒、中国长城约 5.3 秒、贝达药业约 9.0 秒，均为 HTTP 200、`X-XRay-Cache: dynamic-llm`、`charts=[]`。返回 Evidence 均属于当前公司且 `excluded=0`，`source_url` 不含 `#page=`。
- 中国长城首轮暴露 Answer 保留已被删除 Signal 的 `EV-014` 引用，而最终 `evidence[]` 不含该项。现已在 `dynamic_qa.py` 增加 Answer 引用集合校验：Answer 出现未最终返回的 Evidence ID 时，从已通过校验的 Claims 重建；新增真实缺陷回归测试。
- 修复后中国长城复测约 4.8 秒成功，Answer 中的 Evidence 引用与最终 `evidence[]` 完全一致。
- 浏览器端贝达药业动态问题成功，约 4.3 秒；Evidence 抽屉、可靠度原因和前端追加的 `#page=1` 正常。四家公司切换会先清空旧回答，未发现串台。
- 模型格式存在波动：同一财务问题后续在恒生电子约 16.3 秒、中国长城约 8.4 秒时，模型未给出可验证的 Claim/Signal 引用，后端按设计返回 `insufficient`；改问经营现金流后两家公司约 4.9 秒和 8.5 秒，仍因同一原因安全拒答。没有为提高成功率放宽 Evidence 校验。
- 四家公司水果问题均 HTTP 200、`X-XRay-Cache: insufficient`，固定拒答且四个数组全空；日志确认不调用模型。
- 思看科技三条稳定问题浏览器端均为 `LIVE API`，答案互不相同，图表数量依次为 1、1、0；Evidence 抽屉、可靠度原因和 PDF 页码链接正常。非思看公司断网模拟为 `SERVICE UNAVAILABLE`，未混入思看科技数据。
- 后续人工反馈修复：动态问答此前误用公共 LLM 客户端的旧风险分析 system prompt，导致模型输出 S1/S2/S3/S4 说明或非结构化 `conclusion`。现已支持调用方覆盖 system prompt，动态问答固定使用自己的严格 JSON/Evidence 契约。
- Evidence 抽屉会把纯空格分隔的表格型摘录整理为标题与数字块；只调整空格、换行和分组，不改原文字词与数值。裸千分位金额和裸小数也可与 Evidence 匹配并高亮。
- 证据不足时保留固定兜底句，并在前端补充确定性的可能原因和改问建议，不把原因当作公司 Claim。
- 风险问题真实联调：恒生电子、中国长城、贝达药业均得到 `dynamic-llm`，分别返回 6 条 Evidence，且 `charts=[]`。
- 修复后验证：合成后端 201 项通过；真实库 101 项通过、1 项按设计跳过；前后端契约 25 项通过。

### 7.3 普通投资者 Evidence 扩展与第二轮 API 冒烟（2026-10-03）

- 基于 `cninfo.multicompany.next.db` 只读复制生成 `cninfo.multicompany.expanded.v2.db`，没有覆盖旧扩展库或稳定库。v2 SHA-256 为 `639e6c8c672c188c259829d3917c65b15d73a6f33f00f2ba6eb151cdbe90cac1`。
- 新增 1751 条 Evidence，扩展后共 2254 条；2158 条可参与回答、96 条沿用原隔离状态。全部可用摘录通过同页连续原文、最长 200 字符和数字边界机械核验。
- 主题目录新增 54 类普通投资者问题；v2 将“每股分红方案”从历史分红中独立出来，避免派息率证据被总额/政策文本占满候选配额。完整范围、各公司覆盖缺口和复现命令见 `docs/INVESTOR_EVIDENCE_EXPANSION.md`。
- DeepSeek 使用原生 JSON 输出模式，降低复杂复合问题因格式不合规而拒答的概率；证据校验没有放宽。
- 真实 API 冒烟通过：恒生电子审计意见/审计机构/关键审计事项、中国长城董事会与中小股东机制、思看科技员工持股平台与劳务外包、恒生电子净现比、贝达药业和中国长城资产负债问题均返回 Evidence-first 的完整或明确部分回答。
- 复合问题只对已取到 Evidence 的子问题作答，并明确列出未披露/未检索到的部分；不会因为一个子问题缺证据而丢弃整份回答。
- 当前只允许两类受控派生计算：净现比必须由同期间经营现金流净额与归母净利润 Evidence 共同支撑；持股分红金额必须由用户给出的持股数和 Evidence 明确披露的每股/每若干股现金红利共同计算。两者都必须展示公式，其余数字仍要求直接出现在引用 Evidence 中。
- 同行毛利率比较尚未实现：当前四家公司不构成同行样本，数据库也没有真实可比公司集合，不能把四家公司强行当成同行。
- 第二轮人工反馈后的修复：普通投资者主题的 Evidence 卡片只显示规范主题名和报告期，不再把同一段原文同时当摘要和原文重复展示；抽屉会突出显示金额、比例、人数、席位、股数、工时等数字。表格数字后紧邻的 `元/万元/亿元/千元` 会保留在连续摘录中。
- “实控人提名几席”不再宽泛召回实际控制人承诺类 Evidence；“董事会一共几席”会正确命中董事会构成。
- 净现比改为同期间 Evidence 的确定性计算，不调用模型；分红问法在披露了每股/每若干股派息率时确定性计算用户持股金额，否则明确解释缺少换算依据。
- v2 真实分红复测：恒生电子约 12.0 秒返回 `dynamic-llm`，按 2026 披露方案每 10 股 2 元计算 1000 股约 200 元；中国长城约 15.2 秒返回 `dynamic-llm`，按 2023 披露方案每 10 股 0.07 元计算约 7 元。两条计算 Claim 的基数和派息率都能在各自返回的 Evidence 原文中直接核到。
- 前端 Investigation progress 现在显示真实等待时间，并按 0.8 / 2.5 / 6 / 11 秒阈值推进阶段，避免前四步快速闪过后长期停在最后一步。短请求可以在中间阶段直接完成；该进度是基于请求耗时的诚实状态提示，不声称后端提供了实时阶段流。

本轮自动化结果：扩库脚本 4 项通过；数据库验收 0 失败、1 个已解释警告；后端合成测试全通过；真实库测试 101 项通过、1 项按设计跳过；前后端契约 25 项通过；Next.js 生产构建通过。

结论：扩展库的数据隔离、机械证据质量和浏览器展示均通过人工验收。2026-10-03 经产品负责人确认，v2 已原子晋升为稳定 `cninfo.db`，SHA-256 为 `639e6c8c672c188c259829d3917c65b15d73a6f33f00f2ba6eb151cdbe90cac1`；旧稳定库备份为 `cninfo.pre-v2-7408dcc2.db`，SHA-256 为 `7408dcc2be70c3afbbd0bd4437beea6f2f5bf63803f6a69a6f8ce932055cad40`。替换后 `PRAGMA integrity_check=ok`，共 2254 条 Evidence、2158 条可用 Evidence。

## 8. V0.2 架构：规划而非现状

产品规划采用“白天查询，夜间更新”：22:00–05:00 构建新的数据 Snapshot，验证通过才原子切换；失败则继续使用上一份稳定 Snapshot。后端计划增加 `GET /system/status`、维护期 503、Snapshot 元数据、Nightly Intelligence Pipeline 和 Evidence Validation。

这些能力目前尚未接入前端。实现时先确认后端接口已经可用，再做维护页和 Snapshot 更新时间展示；不要只按本地时间强制锁死页面，也不要把架构文档中的规划当作已完成事实。

## 9. 下一步优先级

1. 用浏览器人工覆盖 `docs/INVESTOR_EVIDENCE_EXPANSION.md` 中的主题样例，重点检查复合问题的部分回答、2–3 张图表布局和移动端可读性。
2. 保持思看科技确定性三问及其 Mock/Fallback 不退化，作为动态链路失败时的稳定 Demo。
3. 稳定库晋升已经完成；下一轮回归确认前保留 `cninfo.pre-v2-7408dcc2.db` 作为回滚备份。
4. 如需真正回答“毛利率跟同行比怎么样”，先由产品负责人确认同行口径，再补齐同行公司官方披露 Evidence；不要拿当前四家公司互相比。
5. 继续确认没有跨公司引用、跨 Evidence 数字借用、被隔离证据泄漏或模型生成的无证据图表。

需要用户决策的问题：真实后端契约发生变化、是否启用维护模式、是否牺牲 Demo 稳定性增加新功能。不要擅自扩大范围。

## 10. 工作区注意事项

- 修改前先执行 `git status --short`，不要覆盖其他同学的未提交改动。
- `cninfo.db`、`cninfo(1).db`、`ruvector.db`、`cninfo.multicompany.next.db`、`cninfo.multicompany.expanded.db`、`cninfo.multicompany.expanded.v2.db`、`cninfo.pre-v2-7408dcc2.db` 不要删除、覆盖或提交，除非用户明确授权。
- 仓库只保留 Ask the Company 前端、后端、数据库管道与项目文档，不要混入其他独立项目。
