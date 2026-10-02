# dsh — 多项目工作区

本仓库包含三个互相独立的项目。**根目录不再放任何项目的 `package.json`**，
每个项目在自己的子目录里，各自安装依赖、各自运行。

```
dsh/
├── ai-web-ppt/          AI 网页 PPT 生成器（Node.js + React，端口 8787）
├── xray-backend/        X-Ray 企业穿透分析后端（FastAPI + DeepSeek，端口 8000）
├── xuejun-hackathon/    Ask the Company 前端 MVP（Next.js 16，端口 3000）
│   └── data/cninfo.db   公告原文库（11.5 MB，未纳入版本库）
├── .gitattributes       换行符策略（仓库内存 LF）
└── .gitignore
```

---

## 1. `ai-web-ppt/` — AI 网页 PPT 生成器

把文档改造成信息设计过的网页 PPT，可导出离线 HTML 与真正的 `.pptx`。

```bash
cd ai-web-ppt
npm install
npm run dev          # 或 start.bat / ./start.sh（自动建 .env、构建、自检、启动）
```

配置与部署见 [`ai-web-ppt/README.md`](ai-web-ppt/README.md) 与 `ai-web-ppt/DEPLOY.md`。

## 2. `xray-backend/` — X-Ray 企业穿透分析后端

从公告原文出发，用 DeepSeek 给出**带原文引用**的风险结论；找不到依据时明确回答
「无足够信息」，绝不编造。

```bash
cd xray-backend
python -m pip install -r requirements.txt
cp .env.example .env          # 填 DEEPSEEK_API_KEY

python scripts/run_night_batch.py --demo   # 预读：分析并生成缓存与报告
python main.py                             # 起服务（:8000）
```

详见 [`xray-backend/README.md`](xray-backend/README.md)。

## 3. `xuejun-hackathon/` — Ask the Company 前端 MVP

思看科技（688583）的可验证企业问答 Demo（Next.js 16 + React 19 + Tailwind + ECharts）。

```bash
cd xuejun-hackathon
npm install
npm run dev          # http://localhost:3000 → 自动跳转 /company/688583
```

连接后端：复制 `.env.example` 为 `.env.local`，设置

```text
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

详见 [`xuejun-hackathon/README.md`](xuejun-hackathon/README.md) 与
`xuejun-hackathon/docs/HANDOFF.md`。

---

## 数据文件

| 路径 | 说明 |
| --- | --- |
| `xuejun-hackathon/data/cninfo.db` | 从 PDF 提取的公告原文库（表 `docs`，126 条）。**未纳入版本库** |

`xray-backend` 会自动找到它 —— `config.Settings.db_file` 按以下顺序解析：

```
DB_PATH  →  xray-backend/data/cninfo.db  →  ../data/cninfo.db
         →  ../xuejun-hackathon/data/cninfo.db   ← 当前生效
```

想确认实际用的是哪个库：

```bash
cd xray-backend && python -c "from config import settings; print(settings.db_file)"
```

---

## 未纳入版本库的内容

`.gitignore` 已排除：`node_modules/`、`.npm-cache/`、`data/*.db`、`.env`、
`logs/`、`cache/`、`web/dist/`、`.test-tmp/`、`.pytest-run/`、`pytest-cache-files-*/` 等。

**密钥只在各自的 `.env` 里**，三个项目各有自己的 `.env.example` 模板。
