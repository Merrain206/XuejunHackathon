# -*- coding: utf-8 -*-
"""rebuild_fts_4companies.py — 在候选库中重建四公司全文索引（P0，任务书 §2）。

## 背景

稳定库 `cninfo.db` 的 `chunks_fts` 只有 **801** 行，而有资格被索引的 chunks 有
**2845** 行 —— 缺的 2044 行**全部**属于除恒生电子以外的三家，也就是说
思看科技 / 中国长城 / 贝达药业在 FTS 里根本搜不到。本脚本在**候选库**里重建，
覆盖全部有效 chunks。

## 设计要点

* 只改候选库，稳定 `cninfo.db` 一个字节都不动（`--dst` 与 `--src` 相同会直接报错）。
* `DROP TABLE IF EXISTS` + `CREATE` + 全量 `INSERT`，因此**可以重复执行**，
  不会重复插入（这也是为什么不用 `INSERT INTO ... SELECT` 增量补）。
* tokenizer 固定 `trigram`（中文子串检索需要它），并把真实 tokenizer 写回
  `meta.fts_mode`，两者必须一致。
* `rowid` 显式用 `chunks.id`，保证 `chunks_fts.rowid = chunks.id` 一一对应；
  这样每次都能先 JOIN `chunks` 再限制 `company_code` 做公司隔离。
* 只索引「未 superseded 且 parse_status='ok'」的文档的 chunks。

## 用法

    python rebuild_fts_4companies.py --src cninfo.db --dst cninfo.multicompany.next.db
    python rebuild_fts_4companies.py --verify-only --db cninfo.multicompany.next.db

退出码：0 = 重建并自检通过；1 = 自检失败；2 = 参数/文件问题。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sqlite3
import stat
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

COMPANY_CODES: tuple[str, ...] = ("000066", "300558", "600570", "688583")
COMPANY_NAMES: Dict[str, str] = {
    "000066": "中国长城",
    "300558": "贝达药业",
    "600570": "恒生电子",
    "688583": "思看科技",
}
DEMO_KEYWORDS: tuple[str, ...] = (
    "营业收入",
    "归属于上市公司股东的净利润",
    "经营活动产生的现金流量净额",
)
LATENCY_TARGET_MS = 300.0
FTS_MODE = "trigram"

#: 只索引这些文档的 chunks
ELIGIBLE = "d.superseded = 0 AND d.parse_status = 'ok'"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_fts(conn: sqlite3.Connection) -> Dict[str, Any]:
    """读取当前 FTS 状态（重建前后都用它对比）。"""
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    info: Dict[str, Any] = {"present": "chunks_fts" in tables}
    if not info["present"]:
        return info

    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'chunks_fts'").fetchone()
    sql = (ddl[0] if ddl else "") or ""
    info["ddl"] = sql
    info["tokenizer"] = (
        "trigram" if "trigram" in sql.lower()
        else ("unicode61" if "unicode61" in sql.lower() else "unknown"))
    info["rows"] = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    info["per_company"] = {
        row[0]: row[1] for row in conn.execute(
            "SELECT c.company_code, COUNT(*) FROM chunks_fts f "
            "JOIN chunks c ON c.id = f.rowid GROUP BY c.company_code")
    }
    return info


def eligible_stats(conn: sqlite3.Connection) -> Tuple[int, Dict[str, int]]:
    """纳入范围的 chunks 总数与各公司分布。"""
    total = conn.execute(
        f"SELECT COUNT(*) FROM chunks c JOIN docs d ON d.id = c.document_id "
        f"WHERE {ELIGIBLE}").fetchone()[0]
    per_company = {
        row[0]: row[1] for row in conn.execute(
            f"SELECT c.company_code, COUNT(*) FROM chunks c JOIN docs d ON d.id = c.document_id "
            f"WHERE {ELIGIBLE} GROUP BY c.company_code")
    }
    return total, per_company


def rebuild_fts(conn: sqlite3.Connection) -> int:
    """重建 chunks_fts，返回写入行数。

    DROP + CREATE + 全量 INSERT：整段放在一个事务里，中途失败不会留下半成品索引。
    """
    log("步骤 1/4  删除旧 chunks_fts ...")
    conn.execute("DROP TABLE IF EXISTS chunks_fts")

    log(f"步骤 2/4  创建 chunks_fts（tokenize='{FTS_MODE}'）...")
    conn.execute(
        f"CREATE VIRTUAL TABLE chunks_fts USING fts5(content, tokenize='{FTS_MODE}')")

    log("步骤 3/4  写入纳入范围的 chunks（rowid = chunks.id）...")
    rows = conn.execute(
        f"SELECT c.id, c.content FROM chunks c JOIN docs d ON d.id = c.document_id "
        f"WHERE {ELIGIBLE} ORDER BY c.id").fetchall()
    if not rows:
        raise SystemExit("纳入范围的 chunks 为空，中止重建（不会写入空索引）")

    conn.executemany(
        "INSERT INTO chunks_fts(rowid, content) VALUES (?, ?)",
        [(row[0], row[1] or "") for row in rows])
    conn.commit()
    log(f"          已写入 {len(rows)} 行")

    log(f"步骤 4/4  同步 meta.fts_mode = '{FTS_MODE}' ...")
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('fts_mode', ?)", (FTS_MODE,))
    conn.commit()
    return len(rows)


def verify_fts(conn: sqlite3.Connection) -> List[str]:
    """重建后的自检，返回失败原因列表（空 = 全部通过）。"""
    failures: List[str] = []
    eligible, eligible_per_company = eligible_stats(conn)
    info = inspect_fts(conn)

    if not info.get("present"):
        return ["chunks_fts 不存在"]

    if info["tokenizer"] != FTS_MODE:
        failures.append(
            f"真实 tokenizer='{info['tokenizer']}'，期望 '{FTS_MODE}'")

    declared = conn.execute(
        "SELECT value FROM meta WHERE key = 'fts_mode'").fetchone()
    declared_value = declared[0] if declared else None
    if declared_value != info["tokenizer"]:
        failures.append(
            f"meta.fts_mode='{declared_value}' 与真实 tokenizer="
            f"'{info['tokenizer']}' 不一致")
    elif declared_value != FTS_MODE:
        failures.append(f"meta.fts_mode='{declared_value}'，期望 '{FTS_MODE}'")

    if info["rows"] != eligible:
        failures.append(
            f"FTS 行数 {info['rows']} <> 纳入范围 {eligible}"
            f"（差额 {eligible - info['rows']}）")

    orphan = conn.execute(
        "SELECT COUNT(*) FROM chunks_fts f LEFT JOIN chunks c ON c.id = f.rowid "
        "WHERE c.id IS NULL").fetchone()[0]
    if orphan:
        failures.append(f"FTS 中有 {orphan} 行指向不存在的 chunks")

    missing = conn.execute(
        f"SELECT COUNT(*) FROM chunks c JOIN docs d ON d.id = c.document_id "
        f"WHERE {ELIGIBLE} AND NOT EXISTS "
        f"(SELECT 1 FROM chunks_fts f WHERE f.rowid = c.id)").fetchone()[0]
    if missing:
        failures.append(f"纳入范围内有 {missing} 条 chunks 未进 FTS")

    bad_scope = conn.execute(
        "SELECT COUNT(*) FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
        "JOIN docs d ON d.id = c.document_id "
        f"WHERE NOT ({ELIGIBLE})").fetchone()[0]
    if bad_scope:
        failures.append(f"FTS 索引了 {bad_scope} 条本不该索引的 chunks")

    duplicate = conn.execute(
        "SELECT COUNT(*) FROM (SELECT rowid FROM chunks_fts "
        "GROUP BY rowid HAVING COUNT(*) > 1)").fetchone()[0]
    if duplicate:
        failures.append(f"FTS 中有 {duplicate} 个重复 rowid")

    for code, expected in sorted(eligible_per_company.items()):
        actual = info["per_company"].get(code, 0)
        if actual != expected:
            failures.append(
                f"{COMPANY_NAMES.get(code, code)}({code}) FTS {actual} <> 期望 {expected}")
    return failures


def isolation_test(conn: sqlite3.Connection) -> Tuple[List[str], List[float]]:
    """四公司 × 三关键词检索：验证不串公司，并记录耗时。"""
    failures: List[str] = []
    timings: List[float] = []

    print("\n  公司隔离与检索耗时（FTS）")
    for code in COMPANY_CODES:
        for keyword in DEMO_KEYWORDS:
            started = time.perf_counter()
            rows = conn.execute(
                "SELECT c.company_code, d.company_code AS doc_company "
                "FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
                "JOIN docs d ON d.id = c.document_id "
                "WHERE chunks_fts MATCH ? AND c.company_code = ? "
                f"  AND {ELIGIBLE} LIMIT 20",
                (keyword, code)).fetchall()
            elapsed = (time.perf_counter() - started) * 1000
            timings.append(elapsed)
            leaked = {row[0] for row in rows} | {row[1] for row in rows}
            leaked.discard(code)
            flag = ""
            if leaked:
                failures.append(
                    f"{COMPANY_NAMES.get(code, code)}({code}) 检索「{keyword}」"
                    f"串到 {sorted(leaked)}")
                flag = f"  ← 串公司 {sorted(leaked)}"
            print(f"    {code} | {keyword[:16]:18s} hits={len(rows):3d} "
                  f"{elapsed:7.2f} ms{flag}")

    if timings:
        worst = max(timings)
        print(f"    最慢 {worst:.2f} ms，平均 {sum(timings) / len(timings):.2f} ms，"
              f"目标 < {LATENCY_TARGET_MS:.0f} ms")
        if worst >= LATENCY_TARGET_MS:
            failures.append(f"最慢检索 {worst:.2f} ms 超过目标 {LATENCY_TARGET_MS:.0f} ms")
    return failures, timings


def integrity_check(conn: sqlite3.Connection) -> List[str]:
    """§6.3 / §6.4 的结构检查。"""
    failures: List[str] = []
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        failures.append(f"PRAGMA integrity_check = {result}")

    for label, sql in (
        ("孤儿 chunks",
         "SELECT COUNT(*) FROM chunks c LEFT JOIN docs d ON d.id = c.document_id "
         "WHERE d.id IS NULL"),
        ("孤儿 evidence",
         "SELECT COUNT(*) FROM evidence e LEFT JOIN docs d ON d.id = e.document_id "
         "WHERE d.id IS NULL"),
        ("chunks 跨公司",
         "SELECT COUNT(*) FROM chunks c JOIN docs d ON d.id = c.document_id "
         "WHERE c.company_code <> d.company_code"),
        ("evidence 跨公司",
         "SELECT COUNT(*) FROM evidence e JOIN docs d ON d.id = e.document_id "
         "WHERE e.company_code <> d.company_code"),
    ):
        count = conn.execute(sql).fetchone()[0]
        if count:
            failures.append(f"{label} = {count}")
    return failures


def report_delivery(path: str, failures: List[str], timings: Sequence[float]) -> None:
    size = os.path.getsize(path)
    print(f"\n{'=' * 72}\n候选库交付信息\n{'=' * 72}")
    print(f"  路径    : {os.path.abspath(path)}")
    print(f"  大小    : {size} bytes ({size / 1024 / 1024:.1f} MiB)")
    print(f"  SHA-256 : {sha256_file(path)}")
    if timings:
        print(f"  检索耗时: 最慢 {max(timings):.2f} ms / 平均 "
              f"{sum(timings) / len(timings):.2f} ms")
    print(f"  自检    : {'全部通过' if not failures else f'{len(failures)} 项失败'}")
    for failure in failures:
        print(f"    - {failure}")
    print("\n  注意：.db 已被 Git 忽略，通过团队约定的文件传递方式交付；")
    print("        只有用户明确确认后，才允许替换稳定 cninfo.db。")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="重建四公司 chunks_fts")
    parser.add_argument("--src", default="cninfo.db", help="稳定库路径（只读来源）")
    parser.add_argument("--dst", default="cninfo.multicompany.next.db",
                        help="候选库输出路径")
    parser.add_argument("--verify-only", action="store_true",
                        help="只对 --db 做自检，不重建")
    parser.add_argument("--db", default=None, help="配合 --verify-only 使用的库路径")
    args = parser.parse_args(argv)

    if args.verify_only:
        target = os.path.abspath(args.db or args.dst)
        if not os.path.isfile(target):
            print(f"数据库不存在：{target}", file=sys.stderr)
            return 2
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            log(f"只读自检：{target}")
            failures = verify_fts(conn)
            failures += integrity_check(conn)
            failures += isolation_test(conn)[0]
            if failures:
                print("\n自检未通过：")
                for failure in failures:
                    print(f"  - {failure}")
                return 1
            print("\n自检全部通过。")
            return 0
        finally:
            conn.close()

    src = os.path.abspath(args.src)
    dst = os.path.abspath(args.dst)
    if src == dst:
        print("--src 与 --dst 不能是同一个文件（稳定库不得被覆盖）", file=sys.stderr)
        return 2
    if not os.path.isfile(src):
        print(f"源库不存在：{src}", file=sys.stderr)
        return 2

    log(f"源库（只读）: {src}")
    log(f"候选库      : {dst}")

    # 1) 复制候选库
    if os.path.exists(dst):
        log("候选库已存在，覆盖（每次都是全新的完整副本，不做增量修改）")
        os.remove(dst)
    shutil.copy2(src, dst)
    # Windows 上 copy2 会继承源库的只读属性。稳定库可以保持只读，但候选库必须
    # 可写，否则紧接着的 DROP/CREATE 会报 "attempt to write a readonly database"。
    os.chmod(dst, os.stat(dst).st_mode | stat.S_IWUSR)
    log(f"已复制 {os.path.getsize(dst)} bytes")

    conn = sqlite3.connect(dst)
    try:
        before = inspect_fts(conn)
        eligible, eligible_per_company = eligible_stats(conn)
        print(f"\n{'=' * 72}\n重建前\n{'=' * 72}")
        print(f"  chunks_fts 行数 : {before.get('rows', 0)}")
        print(f"  纳入范围 chunks : {eligible}")
        print(f"  缺口            : {eligible - before.get('rows', 0)}")
        print(f"  重建前各公司 : "
              f"{ {k: before.get('per_company', {}).get(k, 0) for k in eligible_per_company} }")
        print(f"  纳入范围各公司: {eligible_per_company}")

        inserted = rebuild_fts(conn)

        print(f"\n{'=' * 72}\n重建后自检\n{'=' * 72}")
        failures = verify_fts(conn)
        after = inspect_fts(conn)
        print(f"  chunks_fts 行数 : {after['rows']}（本次写入 {inserted}）")
        print(f"  真实 tokenizer  : {after['tokenizer']}")
        print(f"  各公司覆盖      : {after['per_company']}")

        failures += integrity_check(conn)
        isolation_failures, timings = isolation_test(conn)
        failures += isolation_failures

        report_delivery(dst, failures, timings)
        if failures:
            print("\n结论：[FAIL] 候选库自检未通过，不得替换稳定库。")
            return 1
        print("\n结论：[PASS] 候选库重建完成且自检通过。")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
