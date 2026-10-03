# Ask the Company

Ask the Company 是一个面向普通投资者的可验证企业问答 Demo。用户可以直接向公司提问，系统从上市公司公开披露文件中检索证据，再生成带 Claim、Signal、Chart 和 Evidence 的结构化回答。

项目遵循 **No Evidence, No Claim**：没有证据就不下结论；所有结论、风险信号和图表数据都必须能追溯到具体公告、页码和原文摘录。

## 当前范围

目前支持数据库内四家公司：

| 股票代码 | 公司 |
| --- | --- |
| 688583 | 思看科技 |
| 600570 | 恒生电子 |
| 000066 | 中国长城 |
| 300558 | 贝达药业 |

思看科技保留三条确定性演示问题和一条证据不足问题，作为稳定 Demo 基线：

- 你的收入结构发生了什么变化？
- 你最近真的赚钱吗？
- 目前最值得关注的风险是什么？
- 你的员工喜欢吃水果吗？（演示证据不足）

其他公司通过 SQLite Evidence 检索与 DeepSeek 动态生成回答。动态回答同样必须通过 Evidence 校验；服务异常时不会降级为思看科技数据，避免跨公司事实污染。

## 技术架构

- 前端：Next.js 16、React 19、TypeScript、Tailwind CSS 4、ECharts
- 后端：FastAPI、Pydantic 2、SQLite、DeepSeek OpenAI 兼容接口
- 数据：仓库根目录下的 `cninfo.db`，后端只读访问
- 接口：`POST /companies/{id}/ask`

前后端只通过结构化 JSON 契约耦合。后端返回 snake_case，前端在 `src/lib/api.ts` 集中转换为 camelCase。图表由后端根据最终通过校验的 Evidence 确定性构建，不接受模型直接生成图表数据。

## 运行要求

- Git
- Node.js 20.9 或更高版本
- Python 3.10 或更高版本
- 当前项目配套的 `cninfo.db`
- DeepSeek API Key（思看科技稳定问题不依赖模型；其他动态问题需要）

数据库文件不会提交到 Git。请单独取得已经验收的稳定数据库，并放在克隆后的仓库根目录：

```text
XueJunHackathon/
├── cninfo.db
├── package.json
├── src/
└── xray-backend/
```

后端默认直接读取仓库根目录的 `cninfo.db`，正常本地运行无需设置 `DATABASE_PATH`。

## 首次安装

以下命令以 Windows PowerShell 为例：

```powershell
git clone <仓库地址>
cd XueJunHackathon

# 安装前端依赖
npm install

# 创建后端虚拟环境并安装依赖
cd xray-backend
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..
```

将 `cninfo.db` 放到仓库根目录后，创建后端配置：

```powershell
Copy-Item xray-backend\.env.example xray-backend\.env
```

编辑 `xray-backend\.env`，至少填写：

```env
DEEPSEEK_API_KEY=你的DeepSeek_API_Key
```

再创建前端配置：

```powershell
Copy-Item .env.example .env.local
```

本地默认配置为：

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

`.env`、`.env.local`、`*.db` 均已被 Git 忽略，不要提交真实密钥或数据库。

## 启动项目

打开第一个 PowerShell 窗口，启动后端：

```powershell
cd XueJunHackathon\xray-backend
.\.venv\Scripts\python.exe main.py
```

后端默认运行在 `http://127.0.0.1:8000`。可通过健康检查确认数据库路径和服务状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

正常响应中应包含：

- `status: "ok"`
- `database: true`
- `companies: 4`
- `capabilities.settings.db_path` 指向仓库根目录的 `cninfo.db`

打开第二个 PowerShell 窗口，启动前端：

```powershell
cd XueJunHackathon
npm run dev
```

浏览器访问 `http://localhost:3000`。根路径会跳转到 `http://localhost:3000/company/688583`，页面左侧可以切换四家公司。

## 数据库路径覆盖

默认布局不需要额外配置。部署或使用其他数据库位置时，可以在 `xray-backend/.env` 中显式设置：

```env
DATABASE_PATH=D:\data\cninfo.db
DB_PATH_STRICT=true
```

`DATABASE_PATH` 优先级最高。`DB_PATH_STRICT=true` 表示只接受指定路径，不再尝试其他候选位置，适合部署环境避免误连数据库。

## API 示例

```powershell
$body = @{ question = "你的收入结构发生了什么变化？" } | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/companies/688583/ask" `
  -ContentType "application/json" `
  -Body $body
```

响应的核心字段如下：

```json
{
  "answer": "...",
  "claims": [
    { "id": "CL-001", "text": "...", "evidence_ids": ["EV-001"] }
  ],
  "signals": [],
  "charts": [],
  "evidence": [
    {
      "id": "EV-001",
      "document_title": "...",
      "source_page": 322,
      "source_quote": "...",
      "source_url": "https://...pdf"
    }
  ],
  "suggested_questions": []
}
```

`source_url` 是不带 fragment 的原始 PDF 地址；前端根据 `source_page` 统一追加 `#page=N`。证据不足时，后端返回固定说明，并保持 `claims`、`signals`、`charts`、`evidence` 为空数组。

## Mock 与降级行为

前端通过 `sourceMode` 区分数据来源：

- `api`：真实后端返回
- `mock`：未配置后端时的思看科技演示数据
- `fallback`：思看科技后端异常后的已核验降级数据
- `unavailable`：其他公司的动态证据服务不可用

`NEXT_PUBLIC_API_BASE_URL` 未配置时，前端使用 Mock。思看科技 API 请求失败时可以回退到已核验演示数据；其他公司只显示证据不足或服务不可用，不会混入思看科技事实。

## 测试与构建

前端生产构建：

```powershell
npm run build
```

后端包含合成数据库测试和真实数据库测试，两套测试必须分别运行：

```powershell
cd xray-backend
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m pytest tests_real -q
```

`tests_real` 默认读取仓库根目录的 `cninfo.db`。也可以通过 `XRAY_REAL_DB` 或 `DATABASE_PATH` 指定真实库。

生产模式本地验证：

```powershell
npm run build
npm start
```

## 部署注意事项

- 显式设置稳定数据库的 `DATABASE_PATH` 和 `DB_PATH_STRICT=true`。
- 配置服务端 `DEEPSEEK_API_KEY`，不要使用 `NEXT_PUBLIC_` 前缀暴露密钥。
- 将 `CORS_ORIGINS` 设置为真实前端域名；默认值只允许本机 3000 端口。
- `ADMIN_TOKEN` 留空会关闭管理刷新接口。启用时应使用随机长 token，并只通过 `X-Admin-Token` 请求头传入。
- 对外服务还需要 HTTPS、反向代理、限流、日志与告警。

## 目录说明

```text
src/                         Next.js 前端
xray-backend/                FastAPI 后端、测试与运行配置
docs/HANDOFF.md              当前实现状态、验收结果与交接说明
docs/INVESTOR_EVIDENCE_EXPANSION.md
                             普通投资者 Evidence 覆盖范围
cninfo.db                    本地稳定数据库，不提交 Git
```

更详细的实现状态、数据口径和后续事项以 [`docs/HANDOFF.md`](docs/HANDOFF.md) 为准。
