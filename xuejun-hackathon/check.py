# -*- coding: utf-8 -*-
"""
check.py — 数据完整性校验脚本
校验所有公司的 published_at、document_type、report_period、source_url、parse_status
"""
import sqlite3
import sys

DB_PATH = r"D:\hackathon\cninfo.db"

def check():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 1. 各公司文档总数
    print("=" * 60)
    print("1. 各公司文档总数")
    print("=" * 60)
    rows = c.execute("SELECT company_code, COUNT(*) FROM docs GROUP BY company_code ORDER BY company_code").fetchall()
    if not rows:
        print("  ⚠️ docs 表为空，尚未入库任何数据")
    for code, cnt in rows:
        print(f"  {code}: {cnt} 份")
    
    # 2. 解析状态分布
    print("\n" + "=" * 60)
    print("2. 解析状态分布 (parse_status)")
    print("=" * 60)
    rows = c.execute(
        "SELECT company_code, parse_status, COUNT(*) FROM docs "
        "GROUP BY company_code, parse_status ORDER BY company_code, parse_status"
    ).fetchall()
    for code, status, cnt in rows:
        flag = "⚠️" if status == "ocr_required" else "✅"
        print(f"  {code} | {status}: {cnt} {flag}")
    
    # 3. document_type 分布
    print("\n" + "=" * 60)
    print("3. 文档类型分布 (document_type)")
    print("=" * 60)
    rows = c.execute(
        "SELECT company_code, document_type, COUNT(*) FROM docs "
        "GROUP BY company_code, document_type ORDER BY company_code, document_type"
    ).fetchall()
    for code, dtype, cnt in rows:
        print(f"  {code} | {dtype}: {cnt}")
    
    # 4. 关键字段缺失检查
    print("\n" + "=" * 60)
    print("4. 关键字段缺失检查")
    print("=" * 60)
    fields = [
        ("published_at", "发布日期"),
        ("document_type", "文档类型"),
        ("report_period", "报告期"),
        ("source_url", "来源链接"),
    ]
    codes = [r[0] for r in c.execute("SELECT DISTINCT company_code FROM docs").fetchall()]
    if not codes:
        print("  ⚠️ 无数据可校验")
        conn.close()
        return
    
    all_ok = True
    for code in codes:
        total = c.execute("SELECT COUNT(*) FROM docs WHERE company_code=?", (code,)).fetchone()[0]
        print(f"\n  [{code}] 共 {total} 份文档:")
        for field, label in fields:
            null_cnt = c.execute(
                f"SELECT COUNT(*) FROM docs WHERE company_code=? AND ({field} IS NULL OR {field}='')", (code,)
            ).fetchone()[0]
            if null_cnt > 0:
                print(f"    ⚠️ {label}({field}) 缺失: {null_cnt}/{total}")
                all_ok = False
            else:
                print(f"    ✅ {label}({field}): 全部填充")
    
    # 5. published_at 格式检查（应为 YYYYMMDD 8位纯数字）
    print("\n" + "=" * 60)
    print("5. published_at 格式检查 (应为 YYYYMMDD)")
    print("=" * 60)
    bad_dates = c.execute(
        "SELECT company_code, file_name, published_at FROM docs "
        "WHERE published_at IS NOT NULL AND published_at != '' "
        "AND (length(published_at) != 8 OR published_at NOT LIKE '20%')"
    ).fetchall()
    if bad_dates:
        print(f"  ⚠️ 发现 {len(bad_dates)} 条格式异常:")
        for code, fname, date in bad_dates[:10]:
            print(f"    {code} | {date} | {fname[:50]}")
        if len(bad_dates) > 10:
            print(f"    ... 共 {len(bad_dates)} 条")
        all_ok = False
    else:
        print("  ✅ 所有 published_at 格式正确 (YYYYMMDD)")
    
    # 6. parse_status = ocr_required 的文档列表
    print("\n" + "=" * 60)
    print("6. 需 OCR 重解析的文档 (parse_status='ocr_required')")
    print("=" * 60)
    ocr_docs = c.execute(
        "SELECT company_code, file_name, page_count, length(text_content) as text_len "
        "FROM docs WHERE parse_status='ocr_required' ORDER BY company_code"
    ).fetchall()
    if ocr_docs:
        print(f"  共 {len(ocr_docs)} 份需 OCR:")
        for code, fname, pages, tlen in ocr_docs:
            print(f"    {code} | {pages}页 | {tlen}字 | {fname[:50]}")
    else:
        print("  ✅ 无需 OCR 的文档")
    
    # 7. chunks / evidence 统计
    print("\n" + "=" * 60)
    print("7. chunks / evidence 索引统计")
    print("=" * 60)
    for code in codes:
        chunks = c.execute("SELECT COUNT(*) FROM chunks WHERE company_code=?", (code,)).fetchone()[0]
        ev = c.execute("SELECT COUNT(*) FROM evidence WHERE company_code=?", (code,)).fetchone()[0]
        ev_metrics = c.execute("SELECT COUNT(DISTINCT metric) FROM evidence WHERE company_code=?", (code,)).fetchone()[0]
        print(f"  {code}: chunks={chunks}, evidence={ev} ({ev_metrics} 个指标)")
    
    # 总结
    print("\n" + "=" * 60)
    if all_ok:
        print("✅ 全部校验通过！")
    else:
        print("⚠️ 存在异常项，请查看上方详情")
    print("=" * 60)
    
    conn.close()

if __name__ == "__main__":
    check()
