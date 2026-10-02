#!/usr/bin/env bash
# ============================================================================
#  X-Ray 夜间数据采集 · cron 包装脚本
#
#  为什么需要包装脚本而不是直接在 crontab 里写一长串命令：
#    1. cron 的 PATH 极简（通常只有 /usr/bin:/bin），必须显式激活虚拟环境；
#    2. 必须先 cd 到项目根目录，否则相对路径（DB_URL=sqlite:///./xray.db、
#       logs/）会落到 $HOME 而不是项目里；
#    3. .env 需要被导出（config.py 读环境变量优先于读 .env 文件）；
#    4. 日志文件名带日期 + 退出码记录，这些逻辑写在脚本里比塞进 crontab 可读得多。
#
#  用法：
#    ./scripts/night_batch.sh              # 增量采集
#    ./scripts/night_batch.sh --force      # 忽略水位，强制全量重采
# ============================================================================
set -uo pipefail

# ---- 项目根目录（本脚本位于 <root>/scripts/ 下）----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}" || exit 1

VENV_DIR="${VENV_DIR:-${PROJECT_ROOT}/.venv}"
LOG_DIR="${LOG_DIR:-${PROJECT_ROOT}/logs}"
mkdir -p "${LOG_DIR}"

# ---- 所有输出追加写入「按日」日志文件 ----
LOG_FILE="${LOG_DIR}/night-batch-$(date +%F).log"

log_line() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"
}

{
    log_line "=============== 夜间采集开始（pid=$$）==============="

    # ---- 1) 激活虚拟环境 ----
    if [ -f "${VENV_DIR}/bin/activate" ]; then
        # shellcheck disable=SC1091
        . "${VENV_DIR}/bin/activate"
        log_line "已激活虚拟环境：${VENV_DIR}"
    else
        log_line "警告：未找到 ${VENV_DIR}/bin/activate，将使用系统 python"
    fi

    PYTHON_BIN="${VENV_DIR}/bin/python"
    [ -x "${PYTHON_BIN}" ] || PYTHON_BIN="$(command -v python3 || command -v python)"

    # ---- 2) 导出 .env（环境变量优先级高于 .env 文件，便于 cron 覆盖）----
    if [ -f "${PROJECT_ROOT}/.env" ]; then
        set -a
        # shellcheck disable=SC1091
        . "${PROJECT_ROOT}/.env"
        set +a
        log_line "已加载 .env"
    else
        log_line "提示：未找到 .env，将使用 config.py 的默认值"
    fi

    # ---- 3) 应用日志也走 stdout，避免与 shell 重定向重复写两份 ----
    export LOG_FILE=""
    export LOG_LEVEL="${LOG_LEVEL:-INFO}"
    # 让打印立即落盘（否则异常退出时最后几行可能丢失）
    export PYTHONUNBUFFERED=1
    export PYTHONIOENCODING=utf-8

    # ---- 4) 执行采集 ----
    log_line "执行：${PYTHON_BIN} scripts/run_night_batch.py $*"
    "${PYTHON_BIN}" scripts/run_night_batch.py "$@"
    EXIT_CODE=$?
    log_line "退出码：${EXIT_CODE}"

    # ---- 5) 兜底日志清理（logrotate 不可用时的替代）----
    # 与 logrotate 的 maxage 不冲突：都只删 7 天前的同一批文件。
    "${PYTHON_BIN}" scripts/run_night_batch.py --cleanup-logs --days "${LOG_RETAIN_DAYS:-7}" \
        || log_line "警告：日志清理失败（不影响采集结果）"

    log_line "=============== 夜间采集结束 ==============="
    exit "${EXIT_CODE}"
} >> "${LOG_FILE}" 2>&1
