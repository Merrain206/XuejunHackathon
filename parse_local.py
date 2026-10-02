# -*- coding: utf-8 -*-
"""
本地批量解析 PDF -> 写入本地 SQLite
自动递归扫描子目录，自动识别公司代码
"""
import os
import sys
import glob
import json
import sqlite3
import traceback

import fitz  # PyMuPDF

sys.stdout.reconfigure(encoding='utf-8')   # 防止中文文件名打印乱码

# ============ 只改这两行 ============
DATA_DIR = r"D:\hackathon\cninfo_data"        # 扫描这个目录下的所有 PDF（含子文件夹）
DB_PATH  = r"D:\hackathon\cninfo.db"          # 数据库存放位置
# ===================================


def init_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_code TEXT,
            file_name TEXT,
            rel_path TEXT UNIQUE,
            page_count INTEGER,
            text_content TEXT,
            tables_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def parse_pdf(path):
    doc = fitz.open(path)
    page_count = doc.page_count
    parts, tables = [], []
    for i, page in enumerate(doc, 1):
        parts.append(f"--- 第{i}页 ---\n{page.get_text()}")
        try:
            for t in page.find_tables().tables:
                tables.append({"page": i, "rows": t.extract()})
        except Exception:
            pass
    doc.close()          # 及时释放内存，避免大文件吃满内存
    return page_count, "\n".join(parts), tables


def main():
    conn = init_db(DB_PATH)
    done = {r[0] for r in conn.execute("SELECT rel_path FROM docs")}

    # 递归找出所有 PDF
    files = sorted(glob.glob(os.path.join(DATA_DIR, "**", "*.pdf"), recursive=True))
    total = len(files)
    if total == 0:
        print(f"⚠️ 在 {DATA_DIR} 下没找到任何 PDF，请检查路径")
        return

    print(f"共发现 {total} 个 PDF，已入库 {len(done)} 个\n")

    ok = skip = fail = 0
    for idx, path in enumerate(files, 1):
        rel = os.path.relpath(path, DATA_DIR).replace("\\", "/")
        name = os.path.basename(path)
        code = rel.split("/")[0] if "/" in rel else ""   # 第一层目录名 = 公司代码

        if rel in done:
            skip += 1
            print(f"[{idx}/{total}] 跳过 {name}")
            continue
        try:
            pages, text, tables = parse_pdf(path)
            conn.execute(
                "INSERT OR IGNORE INTO docs "
                "(company_code, file_name, rel_path, page_count, text_content, tables_json) "
                "VALUES (?,?,?,?,?,?)",
                (code, name, rel, pages, text, json.dumps(tables, ensure_ascii=False)),
            )
            conn.commit()
            ok += 1
            print(f"[{idx}/{total}] ✅ [{code}] {name} | {pages}页 | {len(text)}字符 | 表格{len(tables)}个")
        except Exception:
            fail += 1
            print(f"[{idx}/{total}] ❌ {name}")
            traceback.print_exc()

    conn.close()
    print(f"\n✅ 完成：新增 {ok}，跳过 {skip}，失败 {fail}")
    print(f"数据库位置：{DB_PATH}")


if __name__ == "__main__":
    main()