#!/usr/bin/env bash
# ============================================================================
#  安装 / 卸载 X-Ray 夜间采集的 crontab（幂等）
#
#  用法：
#    sudo bash deploy/setup_cron.sh              # 安装（替换占位路径）
#    sudo bash deploy/setup_cron.sh --user xray  # 装到指定用户
#    sudo bash deploy/setup_cron.sh --uninstall  # 卸载
#    bash deploy/setup_cron.sh --dry-run         # 只打印将要写入的内容
#
#  幂等性：用 `# >>> xray-night-batch >>>` / `# <<< xray-night-batch <<<`
#  标记块整体替换，重复执行不会叠加多条任务。
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BEGIN_MARK="# >>> xray-night-batch >>>"
END_MARK="# <<< xray-night-batch <<<"

TARGET_USER=""
UNINSTALL=0
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --user) TARGET_USER="${2:-}"; shift 2 ;;
        --uninstall) UNINSTALL=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "未知参数：$1" >&2; exit 2 ;;
    esac
done

# 决定写入哪个用户的 crontab
CRON_CMD=(crontab)
if [ -n "${TARGET_USER}" ]; then
    CRON_CMD=(crontab -u "${TARGET_USER}")
fi

if [ "$(id -u)" -ne 0 ] && [ -n "${TARGET_USER}" ]; then
    echo "指定 --user 需要 root 权限（请用 sudo）" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 读取现有 crontab（没有则视为空）
# ---------------------------------------------------------------------------
CURRENT="$("${CRON_CMD[@]}" -l 2>/dev/null || true)"

# 去掉旧的 xray 标记块（幂等的关键）
CLEANED="$(printf '%s\n' "${CURRENT}" | awk -v b="${BEGIN_MARK}" -v e="${END_MARK}" '
    $0 == b { skip = 1; next }
    $0 == e { skip = 0; next }
    !skip { print }
')"

if [ "${UNINSTALL}" -eq 1 ]; then
    NEW_CRON="${CLEANED}"
    echo "已移除 xray 夜间采集任务。"
else
    # 校验前置条件
    [ -x "${PROJECT_ROOT}/scripts/night_batch.sh" ] || {
        echo "错误：${PROJECT_ROOT}/scripts/night_batch.sh 不存在或不可执行" >&2
        echo "     请执行：chmod +x scripts/night_batch.sh" >&2
        exit 1
    }
    if [ ! -f "${PROJECT_ROOT}/.venv/bin/activate" ]; then
        echo "警告：未找到 ${PROJECT_ROOT}/.venv/bin/activate" >&2
        echo "      脚本会退回系统 python。建议先： python -m venv .venv" >&2
    fi

    # 用真实路径替换 crontab 模板里的占位符
    BLOCK="$(sed "s#<PROJECT_ROOT>#${PROJECT_ROOT}#g" "${SCRIPT_DIR}/crontab.xray")"

    NEW_CRON="$(
        printf '%s\n' "${CLEANED}"
        printf '%s\n' "${BEGIN_MARK}"
        printf '%s\n' "${BLOCK}"
        printf '%s\n' "${END_MARK}"
    )"
fi

# 去掉多余空行（保留单个结尾换行）
NEW_CRON="$(printf '%s\n' "${NEW_CRON}" | sed '/^$/N;/^\n$/D')"

if [ "${DRY_RUN}" -eq 1 ]; then
    echo "----- 将写入的 crontab（dry-run，未生效）-----"
    printf '%s\n' "${NEW_CRON}"
    echo "---------------------------------------------"
    exit 0
fi

TMP_FILE="$(mktemp)"
trap 'rm -f "${TMP_FILE}"' EXIT
printf '%s\n' "${NEW_CRON}" > "${TMP_FILE}"

"${CRON_CMD[@]}" "${TMP_FILE}"

echo "crontab 安装完成。当前任务："
"${CRON_CMD[@]}" -l | sed -n '/xray-night-batch/,/xray-night-batch/p'
echo
echo "提示："
echo "  1) cron 不会继承你的 shell 环境，脚本内已显式激活 .venv 并加载 .env；"
echo "  2) 若应用内置调度器也在跑，请把 .env 的 SCHEDULER_ENABLED 设为 false 以免重复采集；"
echo "  3) 可用 'bash scripts/night_batch.sh' 先手工验证一次。"
