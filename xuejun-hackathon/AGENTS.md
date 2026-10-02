<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# Ask the Company 项目约束

- 这是 48 小时 Hackathon 项目，当前 Demo 公司固定为思看科技（688583）。优先级：可演示闭环 > 稳定性 > 工程扩展性。
- 产品原则是 `No Evidence, No Claim`。Claim、Signal、Chart 必须引用真实 Evidence；证据不足时不得用模型常识补全公司事实。
- 前端技术栈固定为 Next.js 16、React 19、TypeScript、Tailwind CSS 4、ECharts。不要在核心链路稳定前引入大型框架。
- 写 Next.js 代码前，先读取 `node_modules/next/dist/docs/` 中与改动相关的本地文档。
- 前端与后端只通过 `POST /companies/{id}/ask` 的结构化 JSON 契约耦合；snake_case 到前端 camelCase 的转换集中放在 `src/lib/api.ts`。
- `NEXT_PUBLIC_API_BASE_URL` 未配置时使用 Mock；后端超时、HTTP 错误或解析失败时必须自动降级为已核验演示数据，不能破坏 Demo。
- 当前 UI 必须保持三条稳定演示问题：收入结构、盈利质量、主要风险。每套回答都要有独立 Answer、Claim、Signal 和 Evidence；前两套包含图表。
- Evidence 页码使用 PDF 查看器页码：招股书第 323 页，2025 半年报第 8、9、43 页。
- V0.2 的夜间更新、Snapshot、维护模式是目标架构，目前尚未在此前端实现；不要把规划写成已完成功能。
- 交接现状、运行方法、关键文件和后续事项见 `docs/HANDOFF.md`。
