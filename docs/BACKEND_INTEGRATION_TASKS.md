# 后端联调收口任务书

> 状态：已实施，本文保留为验收基线；当前事实以 `docs/HANDOFF.md` 为准。

> 项目：Ask the Company（48 小时 Hackathon Demo）  
> Demo 公司：思看科技（688583）  
> 当前基线：`main` 分支提交 `a8e574c` 及其后续提交  
> 最高原则：`No Evidence, No Claim`

## 开始前

1. 拉取最新 `main`。
2. 完整阅读仓库根目录 `AGENTS.md`、`README.md`、`docs/HANDOFF.md`。
3. 阅读 `docs/data_quality_report_688583.md` 和本任务书。
4. 执行 `git status --short`，不要覆盖其他同学尚未提交的改动。
5. 不要修改或提交 `cninfo.db`、`cninfo(1).db`、`ruvector.db`。

## 本轮目标

现有 FastAPI、SQLite 查询层、三条确定性 Demo 回答、证据校验器和响应样例已经存在。本轮不要重新搭建架构，目标是解决真实前后端联调前的四个阻塞项：

1. 后端能稳定找到仓库根目录的真实 `cninfo.db`。
2. 前后端使用同一份权威 PDF、同一套 PDF 查看器页码。
3. 后端返回的官方来源能够被前端正确识别为“高可靠度”。
4. 用真实数据库完成接口契约、四问和失败降级验收。

## P0-1：修复真实数据库路径

当前数据库实际位置是：

```text
D:\Codes\XueJunHackathon\cninfo.db
```

当前 `xray-backend/config.py` 的默认候选路径没有包含仓库根目录的 `cninfo.db`；`tests_real/conftest.py` 也默认指向不存在的 `xuejun-hackathon/data/cninfo.db`。

请完成：

- 保留 `DATABASE_PATH` 环境变量最高优先级。
- 在自动候选中加入 `BASE_DIR.parent / "cninfo.db"`。
- 真实库测试优先读取 `XRAY_REAL_DB` 或 `DATABASE_PATH`，未设置时回退到仓库根目录 `cninfo.db`。
- `.env.example` 只写通用示例，不硬编码任何同学的个人绝对路径。
- `/health` 应展示最终解析出的数据库路径，但不得泄露密钥。
- 数据库只读访问；请求期间不得修改或重建数据库。

验收：

```powershell
$env:DATABASE_PATH = "D:\Codes\XueJunHackathon\cninfo.db"
```

- 后端启动成功。
- `/health` 显示使用该数据库。
- 删除该环境变量后，从当前仓库布局启动仍能自动找到根目录数据库。
- 指向不存在的文件时返回清晰错误，不静默创建空 SQLite 文件。

## P0-2：统一权威 PDF 与页码

这是产品口径问题，不是简单把 `329` 替换为 `323`。

当前存在两套文件：

| 内容 | 前端已核验来源 | 当前后端样例 |
|---|---|---|
| 收入结构 | 上交所注册稿，PDF 查看器第 321、322、324 页 | 巨潮资讯上市招股书，第 329、332 页 |
| 盈利质量 | 上交所 2025 半年报，第 8、9 页 | 巨潮资讯半年报，当前均标为第 8 页 |
| 主要风险 | 上交所 2025 半年报，第 2、43 页 | 巨潮资讯半年报，第 2、43 页 |

项目根 `AGENTS.md` 当前规定的 Demo 口径是：

- 收入结构：上交所招股书第 321、322、324 页。
- 营业收入、归母净利润、扣非净利润：2025 半年报第 8 页。
- 经营活动现金流净额及下降原因：2025 半年报第 9 页。
- 主要风险：2025 半年报第 2、43 页。

权威文件地址以 `src/lib/mock-data.ts` 中已经人工核验的两个上交所 HTTPS URL 为准。请逐一打开 PDF，确认引用原文和 PDF 查看器页码，再修改处理器、样例和测试。

特别注意：

- 不得把一份 PDF 的页码配到另一份 PDF 的 URL 上。
- 不得为了让测试通过而只改断言。
- `tests_real/test_api_real_demo.py` 当前把第 329 页写成回归保护；统一来源后必须同步修正这条测试及其说明。
- 如果数据库中的 Chunk 来自巨潮版本，可以用它定位事实，但对三条 Demo 返回给前端的 `source_url`、`document_title`、`source_page` 和 `source_quote` 必须共同指向同一份实际核验过的上交所 PDF。
- 如发现第 323 页实际无法支持现有收入结构原文，先停止修改并把“PDF URL、查看器页码、截图或原文”发给前端负责人决策，不要自行改变产品口径。

验收：每条 Evidence 点开后，用户无需搜索即可在指定页看到与 `source_quote` 一致的内容。

## P0-3：对齐前端可靠度规则

前端当前仅把以下来源认定为官方高可靠度：

```text
HTTPS + sse.com.cn 或其子域名
```

当前后端样例使用 `http://static.cninfo.com.cn/...`，因此即使原文真实，页面也会显示“中可靠度”。三条稳定 Demo 应返回上交所 HTTPS 原始 PDF 地址，以保持已经验收的高可靠度展示。

要求：

- 不伪造 URL，不把公告列表页冒充原始 PDF。
- `source_url` 必须是可直接打开的 PDF 地址。
- `source_url` 不带 `#page=` fragment；前端根据 `source_page` 统一追加。
- `source_page` 必须是正整数。
- `source_quote` 必须是该 URL 对应 PDF 指定页的原文或忠实摘录。
- 未完成上述核验的 Evidence 不得标成已核验，也不得支撑 Claim、Signal 或 Chart。

## P0-4：锁定四个 Demo 问题

接口保持：

```http
POST /companies/688583/ask
Content-Type: application/json
```

必须稳定支持且分别返回对应答案：

1. `你的收入结构发生了什么变化？`
2. `你最近真的赚钱吗？`
3. `目前最值得关注的风险是什么？`
4. `你的员工喜欢吃水果吗？`

第四问必须返回证据不足：

```text
根据目前掌握的信息，我无法可靠回答这个问题。
```

并返回空的 `claims`、`signals`、`charts`、`evidence`。未知问题是正常业务结果，应返回 HTTP 200，不能调用模型用常识补全公司事实。

`suggested_questions` 必须只返回上述四条，且顺序一致。不要返回“司法风险”“现金流是否健康”等额外推荐问题。

## P0-5：真实接口与契约验收

使用仓库根目录真实数据库执行测试，不得只使用合成 fixture 或静态 JSON。

每个正常回答必须满足：

- 顶层具有 `answer`、`claims`、`signals`、`charts`、`evidence`、`suggested_questions`。
- `answer` 是非空字符串。
- Claim、Signal、Chart 的 `evidence_ids` 非空且全部存在于本次响应。
- Evidence ID 不重复。
- 图表 `periods` 与每组 `values` 等长，所有数值有限。
- 收入结构和盈利质量包含图表；主要风险不强制包含图表。
- 三条稳定问题的 Answer 不相同。
- 不存在跨公司查询或跨响应悬空引用。
- 正常 Demo 请求尽量在 2 秒内完成，并且不能超过前端 45 秒超时。

至少执行并记录：

```powershell
python -m pytest tests
python -m pytest tests_real
```

如果项目实际测试命令不同，请在 README 中给出唯一、可复制的命令。不得仅写“测试通过”，请附测试数量和终端结果摘要。

同时更新四个响应样例：

- `samples/sample_structure.json`
- `samples/sample_profitability.json`
- `samples/sample_risk.json`
- `samples/sample_insufficient.json`

样例必须来自当前真实接口，不能与处理器、测试或数据库口径互相矛盾。

## P0-6：交付前联调信息

完成后请在 PR 描述中提供：

- 启动命令。
- Python 版本与依赖安装命令。
- `.env` 必需变量。
- `/health` 示例结果。
- 四条问题的 `curl` 或 PowerShell 调用示例。
- `pytest` 和 `tests_real` 结果摘要。
- 最终采用的两份上交所 PDF URL 及对应页码。
- 已知限制和未完成事项。

前端负责人拿到上述信息后，只需要设置：

```text
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

即可开始真实接口验收。

## 本轮暂缓，不要扩范围

以下内容不是本轮前后端联调的阻塞项：

- Nightly Pipeline 自动调度。
- Snapshot 和版本切换。
- 维护模式。
- 通用任意问题的 LLM 分析。
- LangGraph、多 Agent、复杂 GraphRAG。
- 用户系统、权限系统或新的管理后台。
- 新增更多公司。
- 前端页面重构。

仓库中已经存在的实验代码不要求本轮删除，但不要继续扩展，也不要把它们描述成当前 Demo 已完成并上线的产品能力。

## 完成定义

只有同时满足以下条件，本任务才算完成：

- [x] 后端无需移动数据库即可读取仓库根目录 `cninfo.db`。
- [x] 三条稳定回答全部使用已核验的上交所 HTTPS PDF。
- [x] 页码为收入结构 321/322/324、盈利质量 8/9、主要风险 2/43。
- [x] 四个问题均通过真实 API 验收。
- [x] 所有 Claim、Signal、Chart 均有有效 Evidence 引用。
- [x] 未准备的问题稳定返回证据不足。
- [x] 单元测试和真实库测试均通过，并提供结果摘要。
- [x] README、样例、实现和测试使用同一套路径、来源及页码口径。

