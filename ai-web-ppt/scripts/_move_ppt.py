"""把根目录的 PPT 生成器收进 ai-web-ppt/，使根目录成为三个互不干扰的项目。

只移动文件，不合并/不删除内容。执行前会打印计划。
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST_DIR = ROOT / "ai-web-ppt"

# 属于 PPT 生成器的顶层条目
PPT_ITEMS = [
    "server",
    "web",
    "scripts",
    "tests",
    "tools",
    "docs",
    "package.json",
    "package-lock.json",
    ".npmrc",
    ".nvmrc",
    ".dockerignore",
    "Dockerfile",
    "docker-compose.yml",
    "start.bat",
    "start.sh",
    "DEPLOY.md",
]

# 明确不动的（属于 X-Ray 或数据）
KEEP_AT_ROOT = ["xray-backend", "xuejun-hackathon", "data", ".git", ".gitignore", ".gitattributes"]


def main() -> int:
    print(f"根目录: {ROOT}")
    DEST_DIR.mkdir(exist_ok=True)
    print(f"目标:   {DEST_DIR}\n")

    print("=== 明确保留在根目录 ===")
    for name in KEEP_AT_ROOT:
        p = ROOT / name
        print(f"  {'存在' if p.exists() else '不存在'}  {name}")

    print("\n=== 计划移动 ===")
    to_move = []
    for name in PPT_ITEMS:
        src = ROOT / name
        if not src.exists():
            print(f"  跳过（不存在）  {name}")
            continue
        dst = DEST_DIR / name
        to_move.append((src, dst))
        print(f"  {name}  →  ai-web-ppt/{name}")

    if not to_move:
        print("\n没有需要移动的内容")
        return 0

    print(f"\n=== 执行移动（{len(to_move)} 项）===")
    for src, dst in to_move:
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        shutil.move(str(src), str(dst))
        print(f"  ✓ {src.name}")

    print("\n=== 移动后根目录 ===")
    for item in sorted(ROOT.iterdir(), key=lambda p: (p.is_file(), p.name)):
        if item.name == ".git":
            continue
        kind = "d" if item.is_dir() else "-"
        print(f"  {kind} {item.name}")

    print("\n=== ai-web-ppt/ 内容 ===")
    for item in sorted(DEST_DIR.iterdir(), key=lambda p: p.name):
        kind = "d" if item.is_dir() else "-"
        print(f"  {kind} {item.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
