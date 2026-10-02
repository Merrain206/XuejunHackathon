# `_unused/` —— 本轮改造废弃的旧链路

这里的东西**不再被任何代码引用**，保留只为可追溯（确认无误后可整目录删除）。

## 为什么废弃

新链路的数据模型与旧链路**根本不同**，无法共存：

| | 旧链路（本目录） | 新链路（当前代码） |
| --- | --- | --- |
| 数据源 | 外部渠道抓取 + `finance_fields` 结构化表 | **`data/cninfo.db` 里的公告原文** |
| 数据形态 | 15 个结构化财务字段（数值/序列） | 纯文本公告 |
| 判断方式 | 确定性规则引擎 S1–S4（阈值比对） | **LLM 读原文给出结论 + 原文引用** |
| 存储 | SQLAlchemy ORM（11 张表） | 不写任何业务表；结果落 `cache/` 与 `logs/` |
| 调度 | 应用内 APScheduler | 系统 cron 调 `scripts/run_night_batch.py` |

## 文件清单与替代关系

| 移走的文件 | 原职责 | 现在由谁负责 |
| --- | --- | --- |
| `models.py` | ORM 11 张表 + 15 个字典字段常量 | **不再需要**（不写业务表）；`ask.py` 的响应契约改由 `schemas.py` 独立定义 |
| `database.py` | engine / Session / init_db | **不再需要**；`db.py` 直接用 `sqlite3` 读 cninfo.db |
| `signal_engine.py` | S1–S4 确定性规则引擎（纯函数） | `analyzer.py`（LLM 判断，prompt 里仍限定这 4 类风险主题） |
| `mock_data.py` | 2 家演示公司的结构化假数据 | 由真实 `cninfo.db` 数据取代；测试用 `tests/fixtures.py` 造临时库 |
| `datasource.py` | 6 个外部渠道适配层（异步/超时/重试） | **不再需要**（数据已离线在 cninfo.db 里） |
| `charts.py` | 由 15 个字段确定性生成图表 | `analyzer.py` 依据 LLM 的 findings 生成 |
| `services/answer_service.py` | 请求时组装 signals/charts/answer + 缓存 | `analyzer.py`（判断+缓存）+ `ask.py`（读缓存合成回答） |
| `tasks/scheduler.py` | APScheduler 装配 | **已拆除**；生产由系统 cron 触发 |
| `tasks/night_batch.py` | 夜间采集落库 | `scripts/run_night_batch.py`（遍历股票 → `analyze_company` → 写 JSON 报告） |
| `deploy/setup_cron.sh` | crontab 幂等安装 | 保留在 `_unused/`；README 说明生产用 cron、demo 手动 |
| `deploy/logrotate.xray` | 日志按天清理 | 同上（脚本自身已能写按日报告，保留作参考） |
| `scripts/night_batch.sh` | cron 包装脚本 | 同上 |
| `scripts/check_charts.py` | charts 行为自检 | `scripts/selfcheck.py` 覆盖新链路 |

## 仍然保留在项目里的（未废弃）

| 文件 | 为什么留 |
| --- | --- |
| `llm_client.py` | DeepSeek 客户端（OpenAI SDK）—— 新链路的核心依赖 |
| `matcher.py` | 中文问题归一 + 相似度匹配 —— 缓存命中仍需要 |
| `config.py` | 已改造：新增 `DB_PATH`，删除旧的数据源/调度配置 |
| `schemas.py` | 已改造：**不再依赖 `models.py`**，响应契约（6 字段）不变 |
| `logging_config.py` | 日志基础设施，与数据模型无关 |

## 注意

`_unused/` 里的 `tasks/` 与 `services/` 保留着原有的 `__init__.py`，
但**不要**把它们加回 `sys.path` —— 它们 import 的模块已不在原位置，
导入必然失败。这个目录只是归档。
