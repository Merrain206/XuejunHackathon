# -*- coding: utf-8 -*-
"""
pipeline.py (v4 多公司版)
- --code 指定公司，全流程按单一 company_code 隔离（4 家共用一个 db）
- --ingest 从 cninfo 下载该代码 PDF 并解析写入 docs 表（新公司首次跑用）
- 其余逻辑（去重收紧 / 取消人工复核告警）延续 v3
用法：
  python pipeline.py --code 688583 --ingest --rebuild   # 首次：下载+解析+重建
  python pipeline.py --code 000066 --ingest --rebuild
  python pipeline.py --code 300558 --ingest --rebuild
  python pipeline.py --code 600570 --ingest --rebuild
  python pipeline.py --code 688583                      # 之后增量：不重下载
依赖：pip install requests pdfplumber
"""
import os, re, csv, json, time, shutil, sqlite3, argparse, difflib
from collections import Counter

try:
    import requests
except Exception:
    requests = None

# ============ 配置 ============
DB_PATH   = r"D:\hackathon\cninfo.db"
DATA_ROOT = r"D:\hackathon\cninfo_data"     # 每家一个子文件夹：cninfo_data\{code}\*.pdf
DEFAULT_CODE = "688583"
CODE = DEFAULT_CODE                          # 运行时由 --code 覆盖
EVAL_CSV = REPORT_MD = REVIEW_CSV = None     # 运行时按 code 拼

LLM_URL   = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1/chat/completions")
LLM_KEY   = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_LIMIT = 40
# ============================

SEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"
QUERY_URL  = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC     = "http://static.cninfo.com.cn/"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36",
       "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch",
       "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

DOC_TYPES = [
    ("招股说明书", "prospectus"), ("上市公告书", "listing_announcement"),
    ("年度报告", "annual_report"), ("半年度报告", "semiannual_report"),
    ("一季度报告", "q1_report"), ("三季度报告", "q3_report"), ("季度报告", "quarterly_report"),
    ("审计报告", "audit_report"), ("内部控制", "internal_control"), ("公司章程", "articles_of_association"),
    ("募集资金", "fund_raising"), ("保荐机构", "sponsor"), ("法律意见书", "legal_opinion"),
    ("问询函", "inquiry"), ("回复", "inquiry_reply"), ("股东大会", "shareholders_meeting"),
    ("董事会", "board"), ("监事会", "supervisory_board"), ("独立董事", "independent_director"),
    ("关联交易", "related_transaction"), ("利润分配", "profit_distribution"), ("权益分派", "profit_distribution"),
]

METRICS = {"营业收入": "revenue", "营业总收入": "revenue",
           "归属于母公司股东的净利润": "net_profit_attr", "归属于母公司所有者的净利润": "net_profit_attr",
           "净利润": "net_profit", "扣除非经常性损益后的净利润": "net_profit_deducted",
           "毛利率": "gross_margin", "销售毛利率": "gross_margin",
           "研发费用": "rd_expense", "研发投入": "rd_investment",
           "经营活动产生的现金流量净额": "operating_cash_flow",
           "总资产": "total_assets", "归属于母公司所有者权益": "equity_attr",
           "基本每股收益": "eps", "资产负债率": "debt_ratio",
           "应收账款": "accounts_receivable", "存货": "inventory"}

DEFAULT_EVAL = [
    ("2024年营业收入是多少", "annual_report", "营业收入", "填写具体金额与单位"),
    ("2024年归母净利润是多少", "annual_report", "归属于母公司股东的净利润", "填写具体金额与单位"),
    ("2024年毛利率是多少", "annual_report", "毛利率", "填写百分比"),
    ("2024年研发费用是多少", "annual_report", "研发费用", "填写具体金额"),
    ("2024年经营活动现金流量净额", "annual_report", "经营活动产生的现金流量净额", "填写具体金额"),
    ("2024年末总资产是多少", "annual_report", "总资产", "填写具体金额"),
    ("2024年资产负债率", "annual_report", "资产负债率", "填写百分比"),
    ("2024年应收账款规模", "annual_report", "应收账款", "填写具体金额"),
    ("2024年存货规模", "annual_report", "存货", "填写具体金额"),
    ("2024年基本每股收益", "annual_report", "基本每股收益", "填写元/股"),
    ("2025年上半年营业收入", "semiannual_report", "营业收入", "填写具体金额"),
    ("2025年上半年净利润变化原因", "semiannual_report", "净利润", "需含变动解释"),
    ("公司主要产品有哪些", "annual_report", "主要产品", "列举产品线"),
    ("公司所属行业及竞争格局", "annual_report", "行业", "需含行业定位"),
    ("前五大客户占比", "annual_report", "前五大客户", "填写占比"),
    ("海外收入占比", "annual_report", "境外", "填写占比或说明"),
    ("公司主要风险因素", "annual_report", "风险", "列举3条以上"),
    ("募投项目进展", "annual_report", "募集资金", "需含进度描述"),
    ("员工人数及结构", "annual_report", "员工", "填写总人数"),
    ("公司分红方案", "annual_report", "利润分配", "需含每股派息"),
]

PAGE_SEP = re.compile(r"^--- 第(\d+)页 ---\s*$")
MAX_CHUNK, MIN_CHUNK, OVERLAP = 1200, 200, 80
JUNK = re.compile(r"^(第?\s*\d+\s*页?|\d{1,4}|[A-Za-z]{0,3}\s*\d{1,4})$")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def norm(s):
    return re.sub(r"[\s《》()（）:：\-—_·,，。、\"'’“”\[\]【】]", "", s or "").lower()


def classify(title):
    for kw, t in DOC_TYPES:
        if kw in title:
            return t
    return "other"


def parse_period(title, dtype):
    y = re.search(r"(20\d{2})\s*年", title)
    if not y:
        return None
    year = y.group(1)
    if dtype in ("annual_report", "audit_report", "internal_control", "profit_distribution"):
        return f"{year}FY"
    if dtype == "semiannual_report":
        return f"{year}H1"
    if "一季度" in title:
        return f"{year}Q1"
    if "三季度" in title:
        return f"{year}Q3"
    return None


def date_of(fname):
    m = re.search(r"(20\d{6})", fname)
    return m.group(1) if m else None


# ---------- schema ----------
def ensure_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS docs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, company_code TEXT NOT NULL,
        file_name TEXT, rel_path TEXT, text_content TEXT, page_count INTEGER,
        tables_json TEXT);
    """)
    have = {r[1] for r in conn.execute("PRAGMA table_info(docs)")}
    for col, ddl in [("document_type", "TEXT"), ("published_at", "TEXT"),
                     ("report_period", "TEXT"), ("source_url", "TEXT"),
                     ("parse_status", "TEXT DEFAULT 'ok'"), ("superseded", "INTEGER DEFAULT 0")]:
        if col not in have:
            conn.execute(f"ALTER TABLE docs ADD COLUMN {col} {ddl}")
    conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_docs_company ON docs(company_code);
    CREATE INDEX IF NOT EXISTS idx_docs_type ON docs(document_type);
    CREATE TABLE IF NOT EXISTS chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL,
        company_code TEXT NOT NULL, page_number INTEGER, chunk_index INTEGER,
        content TEXT NOT NULL, content_len INTEGER);
    CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);
    CREATE TABLE IF NOT EXISTS evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT, company_code TEXT NOT NULL,
        document_id INTEGER NOT NULL, category TEXT, metric TEXT, period TEXT,
        value REAL, unit TEXT, content TEXT, source_page INTEGER, source_quote TEXT,
        method TEXT, review_status TEXT DEFAULT 'auto',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS idx_ev_doc ON evidence(document_id);
    CREATE INDEX IF NOT EXISTS idx_ev_metric ON evidence(metric);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.commit()


def fetch_announcements(code):
    s = requests.Session(); s.headers.update(HDR)
    org = next((i["orgId"] for i in s.post(SEARCH_URL, data={"keyWord": code, "maxNum": 10},
                timeout=20).json() if i.get("code") == code), None)
    if not org:
        raise RuntimeError("未取到 orgId")
    col, plate = ("sse", "sse") if code.startswith(("60", "68")) else ("szse", "szse")
    out, page = [], 1
    while page <= 200:
        time.sleep(1.0)
        js = s.post(QUERY_URL, timeout=25, data={
            "pageNum": page, "pageSize": 30, "column": col, "tabName": "fulltext", "plate": plate,
            "stock": f"{code},{org}", "searchkey": "", "secid": "", "category": "", "trade": "",
            "seDate": "2015-01-01~2026-10-02", "sortName": "", "sortType": "",
            "isHLtitle": "true"}).json()
        anns = js.get("announcements") or []
        if not anns:
            break
        for a in anns:
            t = (a.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
            out.append({"title": t, "raw_title": a.get("announcementTitle") or "",
                        "date": time.strftime("%Y%m%d", time.localtime(a["announcementTime"] / 1000)),
                        "url": STATIC + a.get("adjunctUrl", ""),
                        "adjunct": a.get("adjunctUrl", "")})
        if str(js.get("hasMore", "")).lower() != "true":
            break
        page += 1
    return out


def parse_pdf(path):
    """解析 PDF -> (带页分隔符的正文, 页数, 表格json)"""
    import pdfplumber
    text_parts, tables, pcount = [], [], 0
    with pdfplumber.open(path) as pdf:
        pcount = len(pdf.pages)
        for i, page in enumerate(pdf.pages, 1):
            text_parts.append(f"--- 第{i}页 ---")
            text_parts.append(page.extract_text() or "")
            for tb in (page.extract_tables() or []):
                rows = [[("" if c is None else str(c).strip()) for c in row] for row in tb]
                if len(rows) >= 2:
                    tables.append({"page": i, "rows": rows})
    return "\n".join(text_parts), pcount, json.dumps(tables, ensure_ascii=False)


# ---------- step 0: ingest (下载+解析写入 docs) ----------
def step_ingest(conn, args, warn):
    if not args.ingest:
        return
    log("步骤0 下载+解析 PDF 入库 ...")
    pdf_dir = os.path.join(DATA_ROOT, CODE)
    os.makedirs(pdf_dir, exist_ok=True)
    have = {r[0] for r in conn.execute("SELECT file_name FROM docs WHERE company_code=?", (CODE,))}
    n = 0

    if args.no_network:
        # 离线模式：直接扫描本地已下载的 PDF 文件
        log("  离线模式：扫描本地 PDF ...")
        pdf_files = sorted(f for f in os.listdir(pdf_dir) if f.lower().endswith(".pdf"))
        for fname in pdf_files:
            if fname in have and not args.rebuild:
                continue
            local = os.path.join(pdf_dir, fname)
            try:
                text, pc, tabs = parse_pdf(local)
            except Exception as e:
                log(f"  跳过 {fname}: {e}"); continue
            fdate = date_of(fname)
            title_raw = re.sub(r"^20\d{6}_", "", os.path.splitext(fname)[0])
            dtype = classify(title_raw)
            conn.execute(
                "INSERT INTO docs (company_code, file_name, rel_path, text_content, page_count, "
                "tables_json, document_type, source_url, published_at, report_period, parse_status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (CODE, fname, local, text, pc, tabs,
                 dtype, None, fdate, parse_period(title_raw, dtype),
                 "ok" if (text and len(text) > 200) else "ocr_required"))
            have.add(fname); n += 1
            if n % 10 == 0:
                conn.commit()
                log(f"  已入库 {n} 份（增量提交）")
        conn.commit()
        log(f"  新入库 {n} 份（{CODE}，离线模式）")
        return

    # 在线模式：从 cninfo 下载
    if not requests:
        warn.append("未安装 requests，无法 --ingest"); return
    try:
        anns = fetch_announcements(CODE)
    except Exception as e:
        warn.append(f"接口失败，无法 ingest：{e}"); log(f"  ⚠️ {e}"); return
    for a in anns:
        title = a["title"]
        fname = f"{a['date']}_{re.sub(r'[\\\\/:*?\"<>|]', '', title)[:60]}.pdf"
        if fname in have and not args.rebuild:
            continue
        local = os.path.join(pdf_dir, fname)
        try:
            if not os.path.exists(local):
                r = requests.get(a["url"], headers=HDR, timeout=60)
                if r.status_code != 200 or not r.content:
                    continue
                with open(local, "wb") as fp:
                    fp.write(r.content)
            text, pc, tabs = parse_pdf(local)
        except Exception as e:
            log(f"  跳过 {fname}: {e}"); continue
        # published_at = PDF 文件名前面的时间戳 YYYYMMDD（不加工，纯 8 位数字）
        published_at = date_of(fname) or a["date"]
        dtype = classify(title)
        conn.execute(
            "INSERT INTO docs (company_code, file_name, rel_path, text_content, page_count, "
            "tables_json, document_type, source_url, published_at, report_period, parse_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (CODE, fname, local, text, pc, tabs,
             dtype, a["url"], published_at, parse_period(title, dtype),
             "ok" if (text and len(text) > 200) else "ocr_required"))
        have.add(fname); n += 1
        if n % 10 == 0:
            conn.commit()
            log(f"  已入库 {n} 份（增量提交）")
    conn.commit()
    log(f"  新入库 {n} 份（{CODE}）")


# ---------- step 1: meta ----------
def step_meta(conn, args, warn):
    log("步骤1/4 补元数据 ...")
    conn.execute("UPDATE docs SET superseded=0, parse_status='ok' WHERE company_code=?", (CODE,))

    anns = []
    if not args.no_network and requests:
        try:
            anns = fetch_announcements(CODE)
            log(f"  接口返回 {len(anns)} 条公告")
        except Exception as e:
            warn.append(f"cninfo 接口失败，元数据仅从文件名推导：{e}")
            log(f"  ⚠️ 接口失败：{e}")
    by_date = {}
    for a in anns:
        by_date.setdefault(a["date"], []).append(a)

    matched = 0
    for did, fname in conn.execute("SELECT id, file_name FROM docs WHERE company_code=?", (CODE,)):
        fdate = date_of(fname)
        ftitle = norm(re.sub(r"^20\d{6}_", "", os.path.splitext(fname)[0]))
        cand = by_date.get(fdate) or [a for a in anns
                                      if norm(a["title"]) and difflib.SequenceMatcher(
                                          None, ftitle, norm(a["title"])).ratio() > 0.65]
        best, score = None, 0.0
        for a in cand:
            r = difflib.SequenceMatcher(None, ftitle, norm(a["title"])).ratio()
            if ftitle and (ftitle in norm(a["title"]) or norm(a["title"]) in ftitle):
                r = max(r, 0.95)
            if r > score:
                best, score = a, r
        if best and score >= 0.6:
            dt = classify(best["title"])
            conn.execute("UPDATE docs SET source_url=?, published_at=?, document_type=?, report_period=? WHERE id=?",
                         (best["url"], best["date"],
                          dt, parse_period(best["title"], dt), did))
            matched += 1
        else:
            dt = classify(os.path.splitext(fname)[0])
            conn.execute("UPDATE docs SET document_type=?, published_at=? WHERE id=? AND document_type IS NULL",
                         (dt, fdate if fdate else None, did))
    conn.commit()

    # 归一化 published_at 为 YYYYMMDD 8位纯数字格式
    for did, pa in conn.execute("SELECT id, published_at FROM docs WHERE company_code=? "
                                "AND published_at IS NOT NULL AND published_at != ''", (CODE,)).fetchall():
        digits = re.sub(r"\D", "", pa)
        if len(digits) == 8 and digits != pa:
            conn.execute("UPDATE docs SET published_at=? WHERE id=?", (digits, did))
    conn.commit()

    conn.execute("UPDATE docs SET parse_status='ocr_required' "
                 "WHERE company_code=? AND length(text_content) / NULLIF(page_count,0) < 60", (CODE,))

    for dtype, period, ids in conn.execute(
            "SELECT document_type, report_period, GROUP_CONCAT(id) FROM docs "
            "WHERE company_code=? AND report_period IS NOT NULL AND parse_status='ok' "
            "GROUP BY document_type, report_period HAVING COUNT(*)>1", (CODE,)).fetchall():
        id_list = sorted(int(x) for x in ids.split(","))
        keep = id_list[-1]
        for i in id_list:
            if keep - i >= 10:
                continue
            if i != keep:
                conn.execute("UPDATE docs SET superseded=1 WHERE id=?", (i,))
    conn.commit()

    nulls = conn.execute("SELECT COUNT(*) FROM docs WHERE company_code=? AND source_url IS NULL", (CODE,)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM docs WHERE company_code=?", (CODE,)).fetchone()[0]
    log(f"  匹配 {matched}/{total}；source_url 缺失 {nulls}")
    if nulls:
        warn.append(f"{nulls} 份文档未匹配到 source_url（前端无法打开原文）")


# ---------- step 2: chunks ----------
def split_pages(text):
    pages, cur, num = {}, [], None
    for line in text.split("\n"):
        m = PAGE_SEP.match(line.strip())
        if m:
            if num is not None:
                pages[num] = "\n".join(cur)
            num, cur = int(m.group(1)), []
        else:
            cur.append(line)
    if num is not None:
        pages[num] = "\n".join(cur)
    return pages


def boilerplate(pages):
    c = Counter()
    for t in pages.values():
        for ln in {l.strip() for l in t.split("\n") if l.strip()}:
            c[ln] += 1
    n = max(len(pages), 1)
    return {ln for ln, k in c.items() if k >= max(3, n * 0.4) and len(ln) <= 60}


def clean(text, junk):
    out = [s for ln in text.split("\n") if (s := ln.strip()) and s not in junk and not JUNK.match(s)]
    return re.sub(r"\n{2,}", "\n", "\n".join(out)).strip()


def chunk_page(text):
    if len(text) <= MAX_CHUNK:
        return [text] if len(text) >= MIN_CHUNK else []
    parts, buf = [], ""
    for para in re.split(r"\n(?=\S)", text):
        if len(buf) + len(para) > MAX_CHUNK and buf:
            parts.append(buf.strip()); buf = buf[-OVERLAP:] + para
        else:
            buf += para + "\n"
    if buf.strip():
        parts.append(buf.strip())
    return [p for p in parts if len(p) >= MIN_CHUNK]


def step_chunks(conn, args, warn):
    log("步骤2/4 拆页建索引 ...")
    if args.rebuild:
        chunk_ids = [r[0] for r in conn.execute(
            "SELECT id FROM chunks WHERE company_code=?", (CODE,)).fetchall()]
        if chunk_ids:
            batch_size = 500
            for i in range(0, len(chunk_ids), batch_size):
                batch = chunk_ids[i:i+batch_size]
                placeholders = ",".join("?" * len(batch))
                conn.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({placeholders})", batch)
        conn.execute("DELETE FROM chunks WHERE company_code=?", (CODE,))
        conn.commit()
    if conn.execute("SELECT COUNT(*) FROM chunks WHERE company_code=?", (CODE,)).fetchone()[0] > 0 and not args.rebuild:
        log(f"  chunks 已存在（{CODE}），跳过（--rebuild 可重建）"); return

    try:
        import jieba; mode = "jieba"
    except Exception:
        mode = "trigram"
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('fts_mode',?)", (mode,))
    tok = "unicode61" if mode == "jieba" else "trigram"
    conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content, tokenize='{tok}')")
    conn.commit()
    if mode == "trigram":
        warn.append("未安装 jieba，中文检索质量下降（pip install jieba 后 --rebuild）")

    where = "" if args.scope == "all" else "AND document_type IN ('annual_report','semiannual_report')"
    docs = conn.execute(f"SELECT id, company_code, text_content FROM docs "
                        f"WHERE company_code=? AND parse_status='ok' AND superseded=0 {where}", (CODE,)).fetchall()
    if not docs:
        warn.append("没有可拆页的文档（检查 document_type / parse_status，或新公司是否先 --ingest）")
        return
    total = 0
    for did, code, text in docs:
        pages = split_pages(text); junk = boilerplate(pages); cnt = 0
        for pnum, ptext in sorted(pages.items()):
            for i, ck in enumerate(chunk_page(clean(ptext, junk))):
                cur = conn.execute("INSERT INTO chunks (document_id, company_code, page_number, "
                                   "chunk_index, content, content_len) VALUES (?,?,?,?,?,?)",
                                   (did, code, pnum, i, ck, len(ck)))
                indexed = " ".join(jieba.cut(ck)) if mode == "jieba" else ck
                conn.execute("INSERT INTO chunks_fts(rowid, content) VALUES (?,?)", (cur.lastrowid, indexed))
                cnt += 1
        conn.commit(); total += cnt
    log(f"  共 {total} 个 chunk（分词：{mode}）")


# ---------- step 3: evidence ----------
def to_num(s):
    if not s:
        return None
    s = str(s).replace(",", "").replace(" ", "").replace("—", "").replace("--", "")
    m = re.search(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def detect_unit(text):
    for u in ("亿元", "万元", "千元", "元", "%", "％"):
        if u in text:
            return u
    return "元"


def rule_extract(conn, scope_sql):
    n = 0
    for did, code, period, tables in conn.execute(
            f"SELECT id, company_code, report_period, tables_json FROM docs "
            f"WHERE company_code=? AND parse_status='ok' AND superseded=0 {scope_sql}", (CODE,)):
        try:
            tabs = json.loads(tables or "[]")
        except Exception:
            continue
        for t in tabs:
            rows = t.get("rows") or []
            if len(rows) < 2:
                continue
            header = " ".join(str(c) for c in rows[0] if c)
            unit = detect_unit(header)
            ycol = None
            for j, c in enumerate(rows[0]):
                m = re.search(r"(20\d{2})", str(c or ""))
                if m:
                    ycol = (j, m.group(1)); break
            for r in rows[1:]:
                if not r or not r[0]:
                    continue
                label = str(r[0]).strip()
                metric = next((v for k, v in METRICS.items() if k in label), None)
                if not metric:
                    continue
                idx, yr = ycol if ycol else (1, None)
                val = to_num(r[idx]) if len(r) > idx else None
                if val is None:
                    continue
                conn.execute("INSERT INTO evidence (company_code, document_id, category, metric, "
                             "period, value, unit, content, source_page, source_quote, method) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (code, did, "financial", metric, yr or period, val, unit,
                              f"{label}：{val}{unit}", t.get("page"),
                              " | ".join(str(x) for x in r if x)[:300], "rule"))
                n += 1
    conn.commit()
    return n


def llm_extract(conn, scope_sql):
    if not LLM_KEY or not requests:
        return 0
    chunks = conn.execute(
        f"SELECT c.id, c.document_id, c.company_code, c.page_number, c.content, d.report_period "
        f"FROM chunks c JOIN docs d ON d.id=c.document_id WHERE d.company_code=? {scope_sql} "
        f"AND length(c.content) BETWEEN 300 AND 1500 LIMIT {LLM_LIMIT}", (CODE,)).fetchall()
    sysp = ("你是财务/公告信息抽取器。从文本中抽取可核验事实，输出 JSON 数组，无则 []。"
            "字段：category(risk|business|customer|capacity|rd|governance|other)、"
            "metric(短英文标识)、content(中文事实<=80字)、source_quote(逐字摘自原文<=120字)。"
            "禁止推测，禁止使用原文没有的数字。")
    n = 0
    for cid, did, code, pnum, content, period in chunks:
        try:
            time.sleep(0.6)
            resp = requests.post(LLM_URL, timeout=90, headers={"Authorization": f"Bearer {LLM_KEY}"},
                                 json={"model": LLM_MODEL, "temperature": 0,
                                       "messages": [{"role": "system", "content": sysp},
                                                    {"role": "user", "content": f"报告期：{period}\n原文：\n{content}"}]})
            txt = resp.json()["choices"][0]["message"]["content"]
            items = json.loads(re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M))
        except Exception as e:
            print(f"  ⚠️ LLM 抽取失败 chunk {cid}：{e}"); continue
        page_text = re.sub(r"\s+", "", conn.execute(
            "SELECT content FROM chunks WHERE document_id=? AND page_number=?", (did, pnum)).fetchone()[0] or "")
        for it in (items if isinstance(items, list) else []):
            q = re.sub(r"\s+", "", it.get("source_quote", ""))
            if not q or q not in page_text:
                print(f"  ⚠️ 引用校验失败，丢弃：{it.get('metric')}"); continue
            conn.execute("INSERT INTO evidence (company_code, document_id, category, metric, "
                         "period, content, source_page, source_quote, method) VALUES (?,?,?,?,?,?,?,?,?)",
                         (code, did, it.get("category"), it.get("metric"), period,
                          it.get("content"), pnum, it.get("source_quote"), "llm"))
            n += 1
    conn.commit()
    return n


def step_evidence(conn, args, warn):
    log("步骤3/4 抽取 Evidence ...")
    if args.rebuild:
        conn.execute("DELETE FROM evidence WHERE company_code=?", (CODE,))
        conn.commit()
    if conn.execute("SELECT COUNT(*) FROM evidence WHERE company_code=?", (CODE,)).fetchone()[0] > 0 and not args.rebuild:
        log(f"  evidence 已存在（{CODE}），跳过（--rebuild 可重抽）"); return
    scope_sql = "" if args.scope == "all" else "AND d.document_type IN ('annual_report','semiannual_report')"
    a = rule_extract(conn, scope_sql.replace("d.", "")); log(f"  规则抽取：{a} 条")
    b = llm_extract(conn, scope_sql) if args.with_llm else 0
    if args.with_llm:
        log(f"  LLM 抽取：{b} 条")
    if a == 0:
        warn.append("规则抽取 0 条：tables_json 结构可能与预期不符，需人工核对表格质量")
    log(f"  合计 {a + b} 条")


# ---------- step 4: eval ----------
def ensure_eval_csv():
    if os.path.exists(EVAL_CSV):
        return
    with open(EVAL_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "question", "expected_doc_type", "expected_keyword",
                    "expected_page", "page_source", "expected_answer_note"])
        for i, (q, dt, kw, note) in enumerate(DEFAULT_EVAL, 1):
            w.writerow([i, q, dt, kw, "", "", note])
    log(f"  已生成评测集 {EVAL_CSV}")


def search(conn, query, k=8, doc_types=None):
    q = query.strip()
    if not q:
        return []
    mode = (conn.execute("SELECT value FROM meta WHERE key='fts_mode'").fetchone() or ("trigram",))[0]
    where, params = ["d.company_code=?", "d.parse_status='ok' AND d.superseded=0"], [CODE]
    if doc_types:
        where.append("d.document_type IN (%s)" % ",".join("?" * len(doc_types)))
        params += list(doc_types)
    W = " AND ".join(where)
    rows = []
    if mode == "jieba":
        try:
            import jieba
            mq = " OR ".join(f'"{t}"' for t in jieba.cut(q) if t.strip())
        except Exception:
            mq = None
    else:
        mq = f'"{q}"' if len(q) >= 3 else None
    if mq:
        try:
            rows = conn.execute(
                f"SELECT c.id, c.document_id, c.page_number, c.content, bm25(chunks_fts), "
                f"d.file_name, d.source_url, d.published_at FROM chunks_fts f "
                f"JOIN chunks c ON c.id=f.rowid JOIN docs d ON d.id=c.document_id "
                f"WHERE chunks_fts MATCH ? AND {W} ORDER BY bm25(chunks_fts) LIMIT ?",
                [mq] + params + [k]).fetchall()
        except Exception:
            rows = []
    if not rows:
        rows = conn.execute(
            f"SELECT c.id, c.document_id, c.page_number, c.content, 0.0, d.file_name, "
            f"d.source_url, d.published_at FROM chunks c JOIN docs d ON d.id=c.document_id "
            f"WHERE c.content LIKE ? AND {W} LIMIT ?", [f"%{q}%"] + params + [k]).fetchall()
    keys = ["chunk_id", "document_id", "page_number", "content", "score", "file_name", "source_url", "published_at"]
    return [dict(zip(keys, r)) for r in rows]


def step_eval(conn, args, warn):
    log("步骤4/4 评测 ...")
    ensure_eval_csv()
    with open(EVAL_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    filled = 0
    for r in rows:
        if not r["expected_page"]:
            hits = search(conn, r["expected_keyword"], k=3, doc_types=[r["expected_doc_type"]])
            if hits:
                r["expected_page"] = hits[0]["page_number"]; r["page_source"] = "auto"; filled += 1
    with open(EVAL_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    log(f"  自动填充 expected_page {filled} 条")

    hit = done = 0; misses = []
    for r in rows:
        if not r["expected_page"]:
            continue
        done += 1
        pages = [h["page_number"] for h in search(conn, r["question"], k=8, doc_types=[r["expected_doc_type"]])]
        ok = int(r["expected_page"]) in pages
        hit += ok
        if not ok:
            misses.append(f"#{r['id']} {r['question']}（期望第{r['expected_page']}页，命中 {pages}）")
        print(f"  {'OK' if ok else 'MISS'} #{r['id']} {r['question']} -> {pages}")
    rate = hit / max(done, 1) * 100
    log(f"  Top-8 命中率：{hit}/{done} = {rate:.1f}%")
    if done == 0:
        warn.append("评测未执行（expected_page 全为空）")
    elif rate < 70:
        warn.append(f"命中率 {rate:.1f}% 低于 70%，建议调 chunk 大小 / 补同义词 / 装 jieba")
    return {"hit": hit, "done": done, "rate": rate, "misses": misses}


# ---------- report ----------
def write_report(conn, args, warn, ev, t0):
    w = "company_code=?"
    stats = {
        "公司代码": CODE,
        "docs 总数": conn.execute(f"SELECT COUNT(*) FROM docs WHERE {w}", (CODE,)).fetchone()[0],
        "ocr_required": conn.execute(f"SELECT COUNT(*) FROM docs WHERE {w} AND parse_status='ocr_required'", (CODE,)).fetchone()[0],
        "superseded": conn.execute(f"SELECT COUNT(*) FROM docs WHERE {w} AND superseded=1", (CODE,)).fetchone()[0],
        "source_url 缺失": conn.execute(f"SELECT COUNT(*) FROM docs WHERE {w} AND source_url IS NULL", (CODE,)).fetchone()[0],
        "chunks": conn.execute("SELECT COUNT(*) FROM chunks WHERE company_code=?", (CODE,)).fetchone()[0],
        "evidence": conn.execute("SELECT COUNT(*) FROM evidence WHERE company_code=?", (CODE,)).fetchone()[0],
        "evidence 指标数": conn.execute("SELECT COUNT(DISTINCT metric) FROM evidence WHERE company_code=?", (CODE,)).fetchone()[0],
        "命中率": f"{ev['hit']}/{ev['done']} = {ev['rate']:.1f}%",
        "耗时": f"{time.time() - t0:.1f}s",
    }
    with open(REVIEW_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["type", "id", "file_name", "note"])
        for i, fn in conn.execute("SELECT id, file_name FROM docs WHERE company_code=? AND superseded=1", (CODE,)):
            w.writerow(["superseded", i, fn, "已自动判定为相邻修订版（误标≈0，可抽查）"])
        for i, fn in conn.execute("SELECT id, file_name FROM docs WHERE company_code=? AND parse_status='ocr_required'", (CODE,)):
            w.writerow(["ocr_required", i, fn, "文本近空，需 OCR 或排除检索"])
        for i, fn in conn.execute("SELECT id, file_name FROM docs WHERE company_code=? AND source_url IS NULL", (CODE,)):
            w.writerow(["no_source_url", i, fn, "前端无法打开原文"])
        for m in ev["misses"]:
            w.writerow(["eval_miss", "", "", m])

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write(f"# Pipeline Report ({CODE})\n\n## 统计\n\n")
        for k, v in stats.items():
            f.write(f"- **{k}**：{v}\n")
        f.write("\n## 提示\n\n")
        for w_ in warn:
            f.write(f"- {w_}\n")
        if not warn:
            f.write("- 无\n")
    log(f"报告已写入 {REPORT_MD}")
    return stats, warn


def main():
    global CODE, EVAL_CSV, REPORT_MD, REVIEW_CSV
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=DEFAULT_CODE)
    ap.add_argument("--scope", choices=["main", "all"], default="main")
    ap.add_argument("--with-llm", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--no-network", action="store_true")
    ap.add_argument("--ingest", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    CODE = args.code
    EVAL_CSV   = rf"D:\hackathon\eval_set_{CODE}.csv"
    REPORT_MD  = rf"D:\hackathon\pipeline_report_{CODE}.md"
    REVIEW_CSV = rf"D:\hackathon\review_needed_{CODE}.csv"
    t0 = time.time()

    if not os.path.exists(DB_PATH):
        print(f"找不到数据库：{DB_PATH}"); return 1
    bak = DB_PATH + ".bak_" + time.strftime("%Y%m%d%H%M%S")
    shutil.copy2(DB_PATH, bak)
    log(f"公司={CODE} 已备份 -> {bak}")

    conn = sqlite3.connect(DB_PATH); ensure_schema(conn)
    warn = []
    for fn in (step_ingest, step_meta, step_chunks, step_evidence):
        try:
            fn(conn, args, warn)
        except Exception as e:
            warn.append(f"{fn.__name__} 执行失败：{e}"); log(f"失败 {fn.__name__}：{e}")
    try:
        ev = step_eval(conn, args, warn)
    except Exception as e:
        warn.append(f"评测失败：{e}"); ev = {"hit": 0, "done": 0, "rate": 0.0, "misses": []}

    stats, warn = write_report(conn, args, warn, ev, t0)
    print("\n" + "=" * 56)
    for k, v in stats.items():
        print(f"{str(k):<18}{v}")
    print("=" * 56)
    for w_ in warn:
        print(f"[WARN] {w_}")
    conn.close()
    return 1 if (args.strict and warn) else 0


if __name__ == "__main__":
    raise SystemExit(main())