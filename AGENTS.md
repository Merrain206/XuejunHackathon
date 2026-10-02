<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# Ask the Company 项目约束

- 这是 48 小时 Hackathon 项目。思看科技（688583）是必须保留的稳定 Demo 基线；2026-10-02 已由产品负责人确认扩展到数据库内四家公司：688583、600570、000066、300558。优先级：稳定基线 > 可验证的多公司闭环 > 工程扩展性。
- 产品原则是 `No Evidence, No Claim`。Claim、Signal、Chart 必须引用真实 Evidence；证据不足时不得用模型常识补全公司事实。
- 前端技术栈固定为 Next.js 16、React 19、TypeScript、Tailwind CSS 4、ECharts。不要在核心链路稳定前引入大型框架。
- 写 Next.js 代码前，先读取 `node_modules/next/dist/docs/` 中与改动相关的本地文档。
- 前端与后端只通过 `POST /companies/{id}/ask` 的结构化 JSON 契约耦合；snake_case 到前端 camelCase 的转换集中放在 `src/lib/api.ts`。
- `NEXT_PUBLIC_API_BASE_URL` 未配置时使用 Mock；后端超时、HTTP 错误或解析失败时必须自动降级为已核验演示数据，不能破坏 Demo。
- 思看科技 UI 必须保持三条稳定演示问题：收入结构、盈利质量、主要风险；另有第四问「你的员工喜欢吃水果吗？」用于演示证据不足。其他公司允许基于 SQLite Evidence + LLM 回答未预设金融问题，但同样必须通过 Evidence 校验；动态回答第一版可以不含图表。
- Evidence 页码使用 PDF 查看器页码，且必须与引用的 PDF 版本配套（两份 PDF 的页码不可混用）：招股说明书（注册稿，506 页）第 **321、322、324** 页；2025 半年报（269 页）第 **2、8、9、43** 页。核验记录见 `docs/HANDOFF.md` 第 4.1 节。
- 后端 `source_url` 只返回不带 fragment 的原始 PDF URL，页码通过 `source_page` 独立返回；前端负责追加 `#page=N`，禁止前后端重复追加。
- 思看科技 `suggested_questions` 保持原四条和固定顺序；其他公司可以使用已确认的通用财务问题，但不得由模型自由生成或推荐无证据覆盖的问题。
- 非思看公司的 API 失败时不得降级到思看科技 Mock；只能显示证据不足或服务不可用，避免跨公司事实污染。
- 三条并行任务书见 `docs/TONIGHT_FRONTEND_TASKS.md`、`docs/TONIGHT_BACKEND_TASKS.md`、`docs/TONIGHT_DATABASE_TASKS.md`；实现状态以 `docs/HANDOFF.md` 为准，不得把任务书目标直接描述成已完成功能。
- 数据库与动态后端合并后的收口要求见 `docs/SUPPLEMENTAL_DATABASE_BACKEND_TASKS.md`：后端必须过滤 `evidence.excluded=1`，Claim/Signal 的数字只能由其自身引用的 Evidence 支撑，不能用全部候选语料交叉兜底。
- V0.2 的夜间更新、Snapshot、维护模式是目标架构，目前尚未在此前端实现；不要把规划写成已完成功能。
- 交接现状、运行方法、关键文件和后续事项见 `docs/HANDOFF.md`。
