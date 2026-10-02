@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ==========================================================
echo   AI 网页 PPT 生成器  ·  一键启动（Windows）
echo ==========================================================
echo.

where node >nul 2>nul
if errorlevel 1 (
  echo [错误] 找不到 Node.js。
  echo        请先安装 Node.js 20.12+ 或 22 LTS：https://nodejs.org/en/download
  echo        安装后重新双击本文件。
  goto :fail
)

for /f "tokens=*" %%v in ('node -v') do set NODE_VERSION=%%v
echo [1/5] Node.js 版本：%NODE_VERSION%

if not exist ".env" (
  if exist ".env.example" (
    copy /y ".env.example" ".env" >nul
    echo.
    echo [需要你操作] 已为你生成 .env，但里面还没有 API Key。
    echo              用记事本打开：%~dp0.env
    echo              填写 DEEPSEEK_API_KEY=sk-... 和 MODEL_NAME=deepseek-flash
    echo              保存后重新双击 start.bat。
    echo.
    notepad ".env"
    goto :fail
  ) else (
    echo [错误] 找不到 .env 也找不到 .env.example。
    goto :fail
  )
)

echo [2/5] 安装依赖（npm ci，首次约 1-2 分钟）...
if exist "package-lock.json" (
  call npm ci --no-audit --no-fund
) else (
  call npm install --no-audit --no-fund
)
if errorlevel 1 (
  echo [错误] 依赖安装失败。常见原因：网络无法访问 npm registry（可设置代理或换镜像源）。
  goto :fail
)

echo [3/5] 构建前端（若已构建则跳过）...
if not exist "web\dist\index.html" (
  call npm run build
  if errorlevel 1 (
    echo [错误] 前端构建失败。
    goto :fail
  )
) else (
  echo       已是构建产物，跳过。
)

echo [4/5] 环境自检...
call npm run check --silent
if errorlevel 1 (
  echo.
  echo [错误] 自检未通过，请按上面的「修复」提示处理后重试。
  goto :fail
)

echo [5/5] 启动服务（关闭本窗口即停止服务）...
echo.
call npm run start:prod
echo.
echo 服务已退出。
goto :fail

:fail
echo.
echo ----------------------------------------------------------
echo  按任意键关闭本窗口...
pause >nul
endlocal
