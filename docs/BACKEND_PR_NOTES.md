# 后端 PR 描述：四公司动态 Evidence-first 问答

> 历史 PR 说明：本文保留首轮 503/407 Evidence 动态链路的实现口径。后续 v2、动态图表、风险问答和稳定库晋升状态以 `docs/HANDOFF.md` 为准。

> 任务书：`docs/TONIGHT_BACKEND_TASKS.md`（后端 Agent）
> 分支：`main`（本仓库 Hackathon 工作流直接在 main 上推进）
> 日期：2026-10-02
> 范围：只改 `xray-backend/**`、后端测试与后端说明；未改 `src/**`、根目录 Pipeline

## 1. 一句话

在**不触碰**思看科技确定性 Demo 的前提下，让数据库内四家公司能回答未预设的金融问题：
后端先检索候选证据并回到原文逐字核验，模型只能在候选集合里挑 id 组织答案，最后机械校验。

## 2. 模型与耗时

| 项 | 值 |
| --- | --- |
| 模型 | `deepseek-flash`（`DEEPSEEK_MODEL`），动态问答显式 `thinking=False` |
| 温度 | `LLM_TEMPERATURE=0.2` |
| 思看科技三条稳定问题 | **0.1–0.2 ms**（确定性处理器，不碰模型） |
| 动态问答（真实模型，9 次） | **3.3–8.9 s** |
| 硬上限 | `REQUEST_TIMEOUT_MS=40000`；低于前端 45 s，预留返回与渲染时间 |

> 冒烟：`python scripts/smoke_dynamic.py`（四家公司 × 通用问题，会真联网、真花钱，
> **不放进自动测试**）。

## 3. 成功问题 / 拒答问题（真实模型，新库）

| # | 公司 | 问题 | 结果 |
| --- | --- | --- | --- |
| 1 | 688583 | 最近营业收入和归母净利润表现如何？ | `demo-handler`（稳定意图优先） |
| 2 | 688583 | 经营现金流表现如何？ | `demo-handler` |
| 3 | 688583 | 近几个报告期的盈利趋势是什么？ | `demo-handler` |
| 4 | 600570 | 最近营业收入和归母净利润表现如何？ | ✅ `dynamic-llm` |
| 5 | 600570 | 经营现金流表现如何？ | ✅ `dynamic-llm` |
| 6 | 600570 | 近几个报告期的盈利趋势是什么？ | ✅ `dynamic-llm` |
| 7 | 000066 | 最近营业收入和归母净利润表现如何？ | ✅ `dynamic-llm` |
| 8 | 000066 | 经营现金流表现如何？ | ✅ `dynamic-llm` |
| 9 | 000066 | 近几个报告期的盈利趋势是什么？ | ✅ `dynamic-llm` |
| 10 | 300558 | 最近营业收入和归母净利润表现如何？ | ✅ `dynamic-llm` |
| 11 | 300558 | 经营现金流表现如何？ | ✅ `dynamic-llm` |
| 12 | 300558 | 近几个报告期的盈利趋势是什么？ | ✅ `dynamic-llm` |
| 13 | 四家 | 你的员工喜欢吃水果吗？ | ✅ 4/4 `insufficient`，四个数组全空，**0 次模型调用** |

**成功 9/9，拒答 0/9（动态问题）；明确证据不足问题 4/4 正确兜底。**

> 换库前（`docs.source_url` 未补齐、`evidence` 较少的那版）同一组问题中有 1 次
> 模型自己判断"候选证据里没有该指标数据"而拒答 —— 拒答行为本身是正确的
> （`No Evidence, No Claim`），但不该由数据缺口触发。

## 4. 测试结果

```powershell
cd xray-backend
python -m pytest tests -q        # 197 passed
python -m pytest tests_real -q   # 101 passed, 1 skipped
python scripts/check_frontend_contract.py   # 通过 25 项，前端契约完全一致
```

新增测试覆盖（任务书第 7 节逐条）：

* 四家公司检索严格隔离（候选文档必须属于本公司）；
* 模型只能引用候选 Evidence ID；悬空 ID／跨响应 ID 被丢弃；`excluded=1` 不会进入候选；
* Claim/Signal 数字只能由其自身引用的 Evidence 支撑，Answer 只能使用最终返回 Evidence 的数字；
* 无 Key、超时、限流、空响应、非法 JSON 全部稳定降级为固定兜底；
* 动态回答 `charts=[]` 仍通过契约；
* 员工水果问题**不调用模型**（连检索都不做）；
* 思看科技原有 105 条测试全部不变且通过；
* 真实库验收：候选引文能在所引页核到、页码不越界、URL 是裸 http(s)、
  核验状态只有 `verified`/`auto`、无跨公司引用。

## 5. 本轮的关键取舍（都已在代码里写明理由）

1. **只使用数据库标记为可用的精确摘录**：候选库 503 条 Evidence 中有 407 条可参与回答，
   均为所引页连续原文且不超过 200 字符；其余 96 条已隔离，后端查询层强制排除。
2. **不含数字的定性引文**（风险因素）走更严格的口径：整串忽略空白后必须是
   该页原文的连续子串且 ≥ 20 字。否则 3 条 `review_status='verified'`
   的人工核验证据会被全数拒绝。
3. **`category='risk'` 归一成 `business`**：前端 `api.ts` 只接受
   financial/business/company，任务书禁止改前端。这是**已知的契约妥协**。
4. **风险类问题一律诚实拒答**（除思看稳定路径）：库里只有 688583 有 3 条
   风险类证据。拿营收/净利润去"回答"风险问题是最坏的答非所问。
5. **支持名单由产品决定**：`SUPPORTED_COMPANY_CODES` 在检索层与调用层双重把关，
   名单外的公司即使将来有证据也不作答。
6. **保命开关** `DYNAMIC_QA_ENABLED=false`：关掉后除稳定三问外一律证据不足。

## 6. 真实模型踩到的坑（已修 + 有回归测试）

`deepseek-flash` 即使 prompt 三次强调"只输出 JSON"，仍会：
① 先写一段分析、末尾才补 JSON；② 把 `answer` 写成 `结论`/`conclusion`；
③ 给出 prose 结论却把 `claims` 留空、连 `EV-xxx` 编号都不写。

早期实现只做整串 `json.loads` + 只认 `claims` 字段 → 一份**引用完全正确**的回答
被整次降级成"无法回答"（界面看着像模型答不出来，其实是后端把它扔了）。
现在解析做三层确定性容错 + 键名归一化；只有句子明确携带有效 `EV-xxx` 时才机械转成 Claim，
再按该 ID 做数字硬校验。没有明确 Evidence ID 时不再凭相同数字猜测出处。

## 7. 已知限制

1. **非思看公司问风险 → 证据不足**：库里只有 688583 有风险类证据。
   要支持，需要数据库侧补齐风险证据，或接 `chunks` 全文检索（任务书 P1）。
2. `evidence.value` / `unit` / `content` 是自动抽取的残渣，不可信；
   动态证据的 `content` 由后端按已核验摘录重新拼。
3. `evidence.period` 有误标（2025 半年报的行里混着 `2026FY`），
   报告期以 `docs.report_period` / 文档标题为准。
4. 候选库隔离 96/503 条无法安全形成精确短摘录或字段有缺陷的 Evidence；动态链路可用 407 条。
5. 动态回答 P0 固定 `charts: []`。
6. 注册稿 PDF 不在版本库，页码核验用例需手工下载后才会跑（否则 skip）。
7. `cninfo.db` 不入库；换库前的备份为 `cninfo.db.bak-20261002`（同样不入库）。

## 8. 未做的事（明确不做）

* 不引入 LangGraph、多 Agent、向量数据库、复杂 GraphRAG；
* 不让模型生成或猜测 Evidence 来源（候选集合由后端封闭给定）；
* 不在 `/ask` 期间下载公告、不修改 SQLite；
* 不为动态回答生成图表；
* 不修改思看科技已核验 PDF 与页码（321/322/324、2/8/9/43 全部回归测试钉死）；
* 不修改前端 `src/**`。

## 9. 交付文件

| 文件 | 说明 |
| --- | --- |
| `xray-backend/dynamic_evidence.py` | 候选检索 + 原文/页码机械核验（新增） |
| `xray-backend/dynamic_qa.py` | Prompt / JSON 解析 / 机械校验 / 组装（新增） |
| `xray-backend/db.py` | 批量页文本与文档元信息（扩展） |
| `xray-backend/schemas.py` | `Evidence.verification_status` 可选字段 + `risk` 类别归一 |
| `xray-backend/response_validator.py` | 数据库侧证据复核 + fragment 校验 |
| `xray-backend/ask.py` | 四层决策顺序接入 |
| `xray-backend/config.py` | 动态问答开关与参数 |
| `xray-backend/tests/test_api_dynamic_qa.py` | 动态问答单元测试（新增） |
| `xray-backend/tests/test_api_dynamic.py` | 接口与隔离测试（新增） |
| `xray-backend/tests_real/test_api_real_dynamic.py` | 真实库验收（新增） |
| `xray-backend/samples/dynamic_*.json` | 四家公司 × 3 问 = 12 份动态响应样例（新增） |
| `xray-backend/scripts/smoke_dynamic.py` | 真实模型受控冒烟（新增） |
| `xray-backend/scripts/make_samples.py` | 支持 `--dynamic`（扩展） |
| `xray-backend/.env.example` | 动态问答配置说明，不含密钥 |
| `xray-backend/README.md` | 「②-补 动态 Evidence-first 问答」章节 + 已知限制 |
