# 移植与交付说明（从「本机 dev」到「陌生电脑一键跑」）

## 0. 技术栈的实际情况（对应你模板里留空的几项）

| 模板字段 | 实际情况（读代码得到，不是猜的） |
| --- | --- |
| 后端框架 | **Node.js 20.12+ / Express 5**（`server/`），不是 Flask/FastAPI |
| 前端 | **React 19 + Vite 8**，`npm run build` 产出 `web/dist`，**由 Express 同端口托管**（不是 React dev server） |
| 是否需要本地模型 | **不需要**。模型走 OpenAI 兼容 HTTPS 接口（默认 `https://api.deepseek.com/v1`），只需出网 |
| 是否需要外部工具 | **不需要** LibreOffice / pandoc。PPTX 由纯 JS 库 `pptxgenjs` + `jszip` 生成 |
| 字体依赖 | **服务端无字体依赖**；版面用系统字体栈渲染（浏览器/Office 端）。生成 PPTX 时写入字体名，目标机器缺字体时由 Office 替换，见 FAQ |
| 目标系统 | Windows / macOS / Linux **均可**（脚本三套齐备；CI/容器用 Linux） |
| 交付方式 | **默认：纯源码 + 一键脚本**（最通用）；**可选：Docker**（`Dockerfile` + `docker-compose.yml` 已就绪）。PyInstaller/exe 不适用（Node 项目），如确需单文件见文末「可选：打包成可执行文件」 |

## 1. 不可移植点清单（第 0 步产出）

> 状态：✅ 已修 / ➖ 仅 dev 用，不影响交付 / ⚠ 需你确认

| 位置 | 风险 | 改法 |
| --- | --- | --- |
| `server/config.js:15` `PORT: '8787'` | 端口写死；占用时无提示 | 改为 `HOST`/`PORT` 环境变量，`EADDRINUSE` 时打印排查命令；见 `server/index.js` |
| `server/config.js` 缺 Key 时静默启动 | 陌生机器上"能打开但一生成就失败" | 新增 `assertConfig()`：缺少必填项直接退出（码 2）并打印修法；`ALLOW_INCOMPLETE_CONFIG=1` 才放行 |
| `server/app.js:16` `EXPORT_DIR = ROOT/.exports` | 输出目录写死 | 改为 `OUTPUT_DIR`（默认仍为 `<项目>/.exports`），启动即 `mkdir -p` |
| `server/app.js:61,64` `limit: '4mb'` | 上传大小写死 | 改为 `MAX_UPLOAD_MB` |
| `server/app.js` 无 CORS 配置 | 前端异地部署就会跨域失败 | `ALLOWED_ORIGINS`（逗号分隔）；**留空=只允许同源**，内置前端无需配置 |
| `server/app.js` 直接把异常 message 回前端 | 可能泄露路径/内部细节 | 统一过 `publicErrorMessage()`（剥路径、截断、单行）+ 兜底错误中间件 |
| `server/app.js:443` 静态托管无缓存头 | 每次刷新全量下载；部署后旧 index 被缓存 | `/assets/*` → `immutable, max-age=1y`；`index.html` → `no-cache` |
| `server/app.js` 无安全响应头 | 缺少基础防护 | 加 `nosniff` / `Referrer-Policy` / `X-Frame-Options` / CSP（`style-src` 需 `unsafe-inline`，因为设计令牌是内联样式） |
| `server/index.js:11,15` 只绑 `config.port`、打印 `localhost` | 容器/局域网场景绑不上 | 绑 `HOST`（容器用 `0.0.0.0`），打印时把 `0.0.0.0` 显示为 `localhost` |
| `server/index.js` 直接 `console.log` | 无法控日志级别、无处落盘 | 新增 `server/logger.js`（`LOG_LEVEL`、`LOG_FILE`、5MB 轮转） |
| `server/index.js:31-32` 立即 `process.exit` | 正在生成的 SSE 被截断 | 优雅停机：`server.close()` + 10s 兜底超时 |
| `server/app.js` `safeFilename()` 保留中文 | 跨平台文件名乱码/非法 | 改为 ASCII 化 + 中文标题走确定性短哈希后缀（`presentation-1a2b3c.pptx`） |
| `server/selfcheck.js`（新增） | 原来没有任何环境自检 | 新增 `runSelfCheck()`：Node 版本 / 必填项 / dist / 输出目录可写 / 端口 / 上游可达 |
| `server/healthPage.js`（新增） | 陌生人机器上只能看日志 | 新增 `GET /health` HTML 自检页 |
| `web/vite.config.mjs:7,20` `apiPort||8787`、`host:127.0.0.1` | 仅 dev server 用 | ➖ 保留；生产不走 Vite。注释已标明 dev-only |
| `scripts/vite-run.mjs` `net use` 补丁 | 仅 Windows 沙箱场景 | ➖ 保留（其他平台不触发该分支）；生产不需要 Vite |
| `scripts/dev.mjs:7` `npm.cmd` | 仅 Windows | ➖ 已按平台选择；生产不用它 |
| `tests/helpers/browser.mjs:10-13,164` Edge/Chrome 固定路径、`taskkill` | 换机跑 E2E 会失败 | ➖ 仅测试用；生产交付不含测试依赖 |
| `scripts/verify-pptx-animation.ps1` | 依赖 Windows + PowerShell + WPS COM | ➖ 仅人工验证工具，不影响运行；Linux/macOS 用 `tools/pptx_animate.py verify` 替代 |
| `package.json` 依赖用 `^` 范围 | 陌生机器装到不同版本 | 全部改为精确版本 `==` 等价写法（`express: "5.2.1"` …），并加 `.npmrc save-exact=true` + `engine-strict=true` |
| 启动方式 `npm start` = `node server/index.js` | 单进程、无守护 | `npm start`/`start:prod` 改走 `scripts/serve.mjs`（多 worker、崩溃重启、优雅停机）；`start:single` 保留单进程 |

## 2. 移植检查清单（我改了哪些原本写死的东西）

1. **端口/监听地址** → `HOST` + `PORT`，占用时报错并给排查命令。
2. **密钥与模型** → 全部走 `.env`/环境变量；缺必填项**拒绝启动**（不再静默 fallback）；`.env.example` 只留示例值。
3. **输出目录** → `OUTPUT_DIR`，启动即创建；容器里挂到卷。
4. **绝对路径** → 统一由 `server/config.js` 的 `ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)))` 推导；前端产物路径 `ROOT/web/dist` 不再假设 CWD。
5. **上传/请求体大小、生成超时** → `MAX_UPLOAD_MB`、`REQUEST_TIMEOUT_MS`（默认 180s，SSE 心跳 15s，`/api/export/pptx` 走同一超时）。
6. **文件命名** → ASCII 安全 + 短哈希后缀，避免中文/空格导致的跨平台乱码。
7. **日志** → `LOG_LEVEL`（error/warn/info/debug）、`LOG_FILE`（可选，自动轮转）。
8. **生产/调试开关** → `NODE_ENV=production` 时不暴露绝对路径；没有 `debug=True`、没有自动重载（自动重载只在 `npm run dev` 的 Vite 侧）。
9. **静态资源** → 由 Express 托管构建产物（单端口），带缓存策略；不再依赖 dev server。
10. **跨域** → `ALLOWED_ORIGINS`（默认同源）。
11. **进程模型** → `WORKERS`（默认 `min(4, CPU-1)`），崩溃退避重启；`TRUST_PROXY` 供反代场景使用。
12. **依赖锁** → `package-lock.json` + 精确版本 + `engine-strict` + Node 版本要求 `>=20.12.0`（`.nvmrc` 22）。

## 3. 生产启动（不用 Docker）

```bash
# Windows：双击 start.bat
# macOS / Linux：
chmod +x start.sh && ./start.sh
```

脚本做的事：检查 Node → 缺 `.env` 就生成并提示填 Key → `npm ci` → 缺 `web/dist` 就 `npm run build` →
`npm run check`（自检）→ `npm run start:prod`；**任何一步失败都会停住等你按键/回车**，不会一闪而过。

手动等价流程：

```bash
npm ci
npm run build
npm run check          # 或 npm run check:json
npm run start:prod     # = node scripts/serve.mjs，WORKERS 见 .env
```

worker 数怎么算：本应用是 I/O 密集（每请求一条到模型的上游 SSE），
默认 `min(4, CPU核数-1)`——留一个核给系统；**不要**按 CPU 核数拉满，也不要给 SSE 服务配单 worker（长连接会互相排队）。
压测后再调 `WORKERS`。

## 4. 生产启动（Docker）

```bash
cp .env.example .env      # 填 DEEPSEEK_API_KEY / MODEL_NAME
docker compose up -d --build
docker compose logs -f
# 打开 http://localhost:8787/health
```

要点：`.env` 以 `env_file` 注入（不烤进镜像）；`8787` 与输出目录 `.exports/`、日志目录 `logs/` 都挂载出来；
容器内以非 root 用户运行；`HEALTHCHECK` 打 `/api/health`。

## 5. exe / 单文件（可选，需要你确认是否要走）

Node 项目没有 PyInstaller 的等价物，可选方案按推荐度排序：

1. **便携 Node + 脚本**（最稳）：把 Node 的 Windows 版解压到 `runtime/`，`start.bat` 里把 `node` 换成 `runtime\node.exe`。
   优点：无编译、行为与开发机一致；缺点：多约 60MB。
2. **Node SEA / `pkg`**：可产出单个 exe，但 `pptxgenjs`/`jszip` 为纯 JS 才能这样打；需要额外处理 `web/dist` 资源内嵌（`sys._MEIPASS` 的 Node 对应物是 `process.execPath` 同级解包，或 `SEA` 的 embedded assets）。
3. **安装包**：用 Inno Setup / NSIS 包一层（内含便携 Node + 构建产物 + `start.bat`）。

> 【待确认】是否需要我把 1 或 2 落地成脚本（默认不做，源码 + 启动脚本已可交付）。

## 6. 常见问题（也可直接看 `/health` 页）

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 启动就退出，提示"配置不完整" | `.env` 缺 `DEEPSEEK_API_KEY` 或 `MODEL_NAME` | 填好 `.env` 再启动；只想看界面可临时 `ALLOW_INCOMPLETE_CONFIG=1` |
| `EADDRINUSE` 端口被占用 | 别的程序占了 8787 | 改 `.env` 的 `PORT`；Windows `netstat -ano \| findstr :8787`，macOS/Linux `lsof -i :8787` |
| API Key 从哪来 | — | DeepSeek 控制台 → API Keys 创建 `sk-...`；换供应商时同时改 `BASE_URL` 与 `MODEL_NAME` |
| 打开页面空白 / 404 | 忘了构建前端 | `npm run build`；或直接用 `start.bat` / `start.sh`（会自动构建） |
| 生成的 PPT 排版错乱、字重不对 | 目标机器没有 AI 选用的字体（如 `Noto Sans SC`） | 网页端有字体栈兜底；Office 里会替换字体。要严格一致：装 `Noto Sans SC`/思源黑体，或在风格偏好里写"只用系统字体" |
| 生成很久没反应 / 超时 | 文档太长、模型慢或网络抖动 | 调大 `REQUEST_TIMEOUT_MS`（默认 180000ms）；缩短文档或调小 `MAX_SLIDES`；`/api/generate` 是 SSE，代理层需关闭缓冲（`X-Accel-Buffering: no` 已设） |
| 导出 PPTX 后"动画不播放" | 浏览器下载的文件被标记为"来自 Internet"，PowerPoint 进受保护视图（动画禁用） | 用界面上的「💾 存到项目目录」，或对文件右键→属性→解除锁定 / `Unblock-File` |
| 权限不足：写输出目录失败 | 目录只读或属于别的用户 | `chmod`/换目录；容器里已把 `/app/.exports` 交给 `node` 用户；或设 `OUTPUT_DIR` 到可写位置 |
| 日志刷屏 | 默认 `info` | 调 `LOG_LEVEL=warn` |
| 丢包/乱码的中文文件名 | 旧版本会保留中文文件名 | 已修为 ASCII 安全命名（`presentation-<hash>.pptx`） |
