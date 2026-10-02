#!/usr/bin/env bash
# 一键启动（macOS / Linux / Git Bash / WSL）
#   chmod +x start.sh && ./start.sh
set -uo pipefail
cd "$(dirname "$0")"

pause_on_fail() {
  echo
  echo "----------------------------------------------------------"
  if [ -t 0 ]; then
    read -r -p "按回车键关闭..." _
  else
    echo "（非交互环境，跳过等待）"
  fi
  exit 1
}

echo
echo "=========================================================="
echo "  AI 网页 PPT 生成器  ·  一键启动（macOS / Linux）"
echo "=========================================================="
echo

if ! command -v node >/dev/null 2>&1; then
  echo "[错误] 找不到 Node.js。"
  echo "        macOS: brew install node@22    Ubuntu/Debian: sudo apt install nodejs npm"
  echo "        或从 https://nodejs.org/en/download 安装 20.12+ / 22 LTS"
  pause_on_fail
fi
echo "[1/5] Node.js 版本：$(node -v)  （npm $(npm -v)）"

if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    chmod 600 .env 2>/dev/null || true
    echo
    echo "[需要你操作] 已生成 .env，但里面还没有 API Key。"
    echo "             编辑 $(pwd)/.env，填写 DEEPSEEK_API_KEY=sk-... 与 MODEL_NAME=deepseek-flash"
    echo "             保存后重新运行 ./start.sh"
    echo
    ${EDITOR:-} "$(pwd)/.env" 2>/dev/null || true
    pause_on_fail
  else
    echo "[错误] 找不到 .env，也找不到 .env.example。"
    pause_on_fail
  fi
fi

echo "[2/5] 安装依赖（npm ci，首次约 1-2 分钟）..."
if [ -f package-lock.json ]; then
  npm ci --no-audit --no-fund || { echo "[错误] 依赖安装失败（检查网络/代理/镜像源）"; pause_on_fail; }
else
  npm install --no-audit --no-fund || { echo "[错误] 依赖安装失败"; pause_on_fail; }
fi

echo "[3/5] 构建前端（若已构建则跳过）..."
if [ ! -f web/dist/index.html ]; then
  npm run build || { echo "[错误] 前端构建失败"; pause_on_fail; }
else
  echo "      已是构建产物，跳过。"
fi

echo "[4/5] 环境自检..."
if ! npm run check --silent; then
  echo
  echo "[错误] 自检未通过，请按上面的「修复」提示处理后重试。"
  pause_on_fail
fi

echo "[5/5] 启动服务（Ctrl+C 停止）..."
echo
exec npm run start:prod
