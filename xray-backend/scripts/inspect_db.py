"""打印 cninfo.db 的真实表结构（用于对齐 db.py 的 SQL）。

用法：
    python scripts/inspect_db.py                      # 默认 data/cninfo.db
    python scripts/inspect_db.py path/to/cninfo.db    # 指定文件
    python scripts/inspect_db.py --rows 3             # 每个表再打印 3 行样例

只读，绝不写库。把这个脚本的输出贴回来，我就能把 db.py 的列名写成完全匹配的。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f}TB"


def describe(db_file: Path, sample_rows: int) -> int:
    if not db_file.is_file():
        print(f"✗ 找不到数据库文件：{db_file}", file=sys.stderr)
        print("  请把 cninfo.db 放到该路径，或用参数指定实际路径。", file=sys.stderr)
        return 2

    print("=" * 74)
    print(f"文件：{db_file}")
    print(f"大小：{human(db_file.stat().st_size)}")
    print("=" * 74)

    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        tables = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        if not tables:
            print("(库里没有任何用户表)")
            return 0

        views = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
            )
        ]

        for table in tables:
            print(f"\n### 表 `{table}`")
            ddl_row = cur.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if ddl_row and ddl_row[0]:
                print("-- DDL --")
                print(ddl_row[0].strip())

            try:
                count = cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                print(f"-- 行数：{count}")
            except sqlite3.Error as exc:
                print(f"-- 行数读取失败：{exc}")

            print("-- PRAGMA table_info --")
            for row in cur.execute(f'PRAGMA table_info("{table}")'):
                cid, name, ctype, notnull, default, pk = row
                flags = []
                if pk:
                    flags.append("PK")
                if notnull:
                    flags.append("NOT NULL")
                if default is not None:
                    flags.append(f"DEFAULT {default}")
                print(f"   {cid:>2}  {name:<28} {ctype or '(无类型)':<12} {' '.join(flags)}")

            print("-- 索引 --")
            idx = list(cur.execute(f'PRAGMA index_list("{table}")'))
            if not idx:
                print("   (无)")
            for row in idx:
                idx_name = row[1]
                cols = [c[2] for c in cur.execute(f'PRAGMA index_info("{idx_name}")')]
                print(f"   {idx_name}: {cols}")

            if sample_rows:
                print(f"-- 前 {sample_rows} 行样例（长文本截断到 200 字符）--")
                try:
                    for r in cur.execute(f'SELECT * FROM "{table}" LIMIT {sample_rows}'):
                        shown = []
                        for v in r:
                            if isinstance(v, bytes):
                                shown.append(f"<blob {len(v)}B>")
                            else:
                                s = str(v)
                                shown.append(s[:200] + "…" if len(s) > 200 else s)
                        print("   " + " | ".join(shown))
                except sqlite3.Error as exc:
                    print(f"   读取失败：{exc}")

        if views:
            print(f"\n### 视图：{views}")

        print("\n" + "=" * 74)
        print("请把以上输出贴回来（表格与 DDL 部分最关键）。")
        print("=" * 74)
    finally:
        con.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打印 cninfo.db 的表结构（只读）")
    parser.add_argument(
        "db_file",
        nargs="?",
        default=None,
        help="数据库路径（默认 xray-backend/data/cninfo.db）",
    )
    parser.add_argument("--rows", type=int, default=0, help="每个表打印几行样例（默认 0）")
    args = parser.parse_args(argv)

    db_file = Path(args.db_file) if args.db_file else (PROJECT_ROOT / "data" / "cninfo.db")
    if not db_file.is_absolute():
        db_file = (Path.cwd() / db_file).resolve()
    return describe(db_file, max(0, args.rows))


if __name__ == "__main__":
    sys.exit(main())
