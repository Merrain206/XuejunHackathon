"""同步远端仓库 —— 但先用只读方式侦察，避免误伤本地文件。

⚠️ 前提：需要「能通过证书校验地访问 github.com」。
   当前本机 github.com 被 hosts 指向 127.0.0.1（Steam++），git 的证书校验会失败。
   解决办法见 README/对话说明（停用 Steam++ 的 GitHub 加速，或信任其根证书）。

本脚本只做三件事：
  1. 检查 git 连 github.com 是否可用（不再依赖"忽略证书校验"）；
  2. 配置 origin（若未配置）；
  3. fetch 远端并把远程分支列出来 —— **不合并、不改工作区**。

用法：
    python scripts/sync_remote.py            # 侦察
    python scripts/sync_remote.py --setup    # 顺带把 origin 配上
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GIT_CANDIDATES = [
    "git",
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
]
REMOTE_URL = "https://github.com/William-7268/XuejunHackathon.git"


def find_git() -> str:
    for candidate in GIT_CANDIDATES:
        try:
            proc = subprocess.run(
                [candidate, "--version"],
                capture_output=True, text=True, timeout=20,
                encoding="utf-8", errors="replace",
            )
            if proc.returncode == 0:
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            continue
    print("✗ 找不到可用的 git，请把 C:\\Program Files\\Git\\cmd 加入 PATH", file=sys.stderr)
    sys.exit(2)


def run(git: str, *args: str, check: bool = False, timeout: int = 120) -> subprocess.CompletedProcess:
    cmd = [git, "-C", str(PROJECT_ROOT), *args]
    print(f"  $ git {' '.join(args)}")
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        for line in out.splitlines()[:20]:
            print("    " + line)
    if err and proc.returncode != 0:
        print("    [stderr] " + err.splitlines()[0][:200])
    if check and proc.returncode != 0:
        print(f"✗ 命令失败（exit={proc.returncode}）", file=sys.stderr)
        sys.exit(1)
    return proc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读侦察远端仓库（不合并）")
    parser.add_argument("--setup", action="store_true", help="顺便配置 origin remote")
    parser.add_argument("--url", default=REMOTE_URL, help="远端地址")
    args = parser.parse_args(argv)

    git = find_git()
    print(f"git: {git}")
    print(f"仓库: {PROJECT_ROOT}")

    print("\n=== 1) 连通性检查（走正常证书校验）===")
    probe = run(git, "ls-remote", "--heads", args.url)
    if probe.returncode != 0:
        print()
        print("✗ 无法通过证书校验访问 GitHub。")
        print("  本机原因：github.com 在 hosts 里被指向 127.0.0.1（Steam++ 的 GitHub 加速），")
        print("  git 的证书校验因此失败。请任选其一后重试：")
        print("   A) 打开 Steam++ → 网络加速 → 关闭 GitHub 加速（并移除它写入 hosts 的条目）")
        print("   B) 在 Steam++ 中导出其根证书并导入「受信任的根证书颁发机构」")
        print()
        print("  验证是否修好： python scripts/sync_remote.py")
        return 1
    print("✓ 可以正常访问远端")

    if args.setup:
        print("\n=== 2) 配置 origin ===")
        existing = run(git, "remote", "get-url", "origin")
        if existing.returncode == 0:
            print("  origin 已存在，改为指定 URL")
            run(git, "remote", "set-url", "origin", args.url, check=True)
        else:
            run(git, "remote", "add", "origin", args.url, check=True)
        run(git, "remote", "-v")

    print("\n=== 3) fetch（只下载，不合并、不改工作区）===")
    run(git, "fetch", "--no-tags", "origin", check=True, timeout=600)
    for ref in ("refs/heads/main", "refs/heads/master"):
        run(git, "rev-parse", "--verify", ref)

    print("\n=== 4) 远端有哪些内容（顶层目录）===")
    run(git, "ls-tree", "--name-only", "origin/main")

    print("\n=== 5) 本地 HEAD 与远端的关系 ===")
    run(git, "log", "--oneline", "-3")
    run(git, "merge-base", "HEAD", "origin/main")

    print()
    print("侦察完成 —— 工作区未被改动。下一步怎么合并请先确认：")
    print("  * 远端内容与本地目录有重叠吗？（看第 4 步）")
    print("  * 想保留本地这两个项目，还是以远端为准？")
    return 0


if __name__ == "__main__":
    sys.exit(main())
