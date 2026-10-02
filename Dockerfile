# ---------------------------------------------------------------------------
# 多阶段构建：stage 1 构建前端静态产物，stage 2 只带运行时
#   docker build -t ai-web-ppt .
#   docker run --rm -p 8787:8787 --env-file .env -v "$PWD/.exports:/app/.exports" ai-web-ppt
# 或直接用 docker compose（推荐，见 docker-compose.yml）
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS build

WORKDIR /app
# 先只拷贝清单，装依赖层可复用（改代码不必重装依赖）
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund

COPY web ./web
COPY scripts ./scripts
RUN npm run build


FROM node:22-bookworm-slim AS runtime

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=8787 \
    LOG_LEVEL=info \
    OUTPUT_DIR=/app/.exports

WORKDIR /app

# 只装运行时依赖
COPY package.json package-lock.json .npmrc ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force

# 服务端代码 + 构建好的前端
COPY server ./server
COPY scripts ./scripts
COPY web/dist ./web/dist

# 以非 root 运行；输出目录先建好并交给 node 用户
RUN mkdir -p /app/.exports /app/logs && chown -R node:node /app
USER node

EXPOSE 8787

# 容器内自检：/health 页与 /api/health 都由应用提供
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||8787)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "scripts/serve.mjs"]
