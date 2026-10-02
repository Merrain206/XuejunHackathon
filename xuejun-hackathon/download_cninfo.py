# -*- coding: utf-8 -*-
"""
巨潮资讯网公告批量下载
- 自动获取 orgId
- 按代码前缀自动判断交易所
- 断点续传（已存在的文件跳过）
- 限速 + 重试，避免被封
"""
import os
import re
import time
import random
import requests

# ============ 配置区 ============
COMPANIES = [
    ("600570", "恒生电子"),
    ("300558", "贝达药业"),
    ("000066", "中国长城"),
    ("688469", "联芸科技"),
    ("301263", "富特科技"),
    ("300604", "长川科技"),
    ("688802", "沐曦股份"),
]

OUT_ROOT = r"D:\hackathon\cninfo_data"   # 和之前 688583 同一根目录
START_DATE = "2023-01-01"                # 起始日期，按需改
END_DATE   = "2026-10-02"
CATEGORY   = ""                          # 留空=全部公告；只要年报填 "category_ndbg_szsh"
MAX_PAGES  = 200                         # 单公司最大翻页数，防死循环
# ===============================

QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
SEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"
STATIC_BASE = "http://static.cninfo.com.cn/"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "http://www.cninfo.com.cn",
    "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

session = requests.Session()
session.headers.update(HEADERS)


def sleep_polite(lo=1.0, hi=2.5):
    time.sleep(random.uniform(lo, hi))


def get_org_id(code):
    """查询公司 orgId"""
    for _ in range(3):
        try:
            sleep_polite()
            r = session.post(SEARCH_URL, data={"keyWord": code, "maxNum": 10}, timeout=20)
            data = r.json()
            for item in data:
                if item.get("code") == code:
                    return item.get("orgId"), item.get("secName")
            return None, None
        except Exception as e:
            print(f"  获取 orgId 失败，重试... {e}")
    return None, None


def get_plate(code):
    """60/68 开头 = 上交所；00/30 开头 = 深交所"""
    return ("sse", "sse") if code.startswith(("60", "68")) else ("szse", "szse")


def sanitize(name):
    """清理文件名非法字符"""
    name = re.sub(r'[\\/:*?"<>|\r\n]', "_", name)
    return name[:120]


def fetch_list(code, org_id, column, plate, page):
    data = {
        "pageNum": page,
        "pageSize": 30,
        "column": column,
        "tabName": "fulltext",
        "plate": plate,
        "stock": f"{code},{org_id}",
        "searchkey": "",
        "secid": "",
        "category": CATEGORY,
        "trade": "",
        "seDate": f"{START_DATE}~{END_DATE}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    for _ in range(3):
        try:
            sleep_polite()
            r = session.post(QUERY_URL, data=data, timeout=25)
            return r.json()
        except Exception as e:
            print(f"  列表请求异常，重试... {e}")
    return None


def download_pdf(url, path):
    for _ in range(3):
        try:
            sleep_polite(0.8, 1.8)
            r = session.get(url, timeout=60)
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                with open(path, "wb") as f:
                    f.write(r.content)
                return True
            print(f"    ⚠️ 非 PDF 内容或状态码 {r.status_code}")
            return False
        except Exception as e:
            print(f"    下载异常，重试... {e}")
    return False


def main():
    os.makedirs(OUT_ROOT, exist_ok=True)
    summary = []

    for code, cname in COMPANIES:
        print(f"\n{'='*60}\n开始处理：{cname}（{code}）\n{'='*60}")
        save_dir = os.path.join(OUT_ROOT, code)
        os.makedirs(save_dir, exist_ok=True)

        org_id, sec_name = get_org_id(code)
        if not org_id:
            print(f"❌ 未找到 {code} 的 orgId，跳过（请检查代码是否正确）")
            summary.append((code, cname, "失败：未找到 orgId"))
            continue
        print(f"orgId={org_id}  简称={sec_name}")

        column, plate = get_plate(code)
        print(f"交易所参数：column={column}, plate={plate}")

        new = skip = fail = 0
        for page in range(1, MAX_PAGES + 1):
            js = fetch_list(code, org_id, column, plate, page)
            if not js:
                break
            anns = js.get("announcements") or []
            if not anns:
                break

            for i, a in enumerate(anns, 1):
                title = a.get("announcementTitle", "").replace("<em>", "").replace("</em>", "")
                adj = a.get("adjunctUrl", "")
                if not adj or not adj.lower().endswith(".pdf"):
                    continue
                t = a.get("announcementTime", 0)
                date = time.strftime("%Y%m%d", time.localtime(t / 1000)) if t else "unknown"
                fname = sanitize(f"{date}_{title}") + ".pdf"
                fpath = os.path.join(save_dir, fname)

                if os.path.exists(fpath) and os.path.getsize(fpath) > 1024:
                    skip += 1
                    continue

                ok = download_pdf(STATIC_BASE + adj, fpath)
                if ok:
                    new += 1
                    print(f"  ✅ [{page}-{i}] {fname}")
                else:
                    fail += 1
                    print(f"  ❌ [{page}-{i}] {fname}")

            # 判断是否还有下一页
            if str(js.get("hasMore", "")).lower() != "true":
                break
            print(f"  —— 第 {page} 页完成，继续翻页 ——")

        print(f"{cname} 完成：新增 {new}，跳过 {skip}，失败 {fail}")
        summary.append((code, cname, f"新增{new} / 跳过{skip} / 失败{fail}"))

    print(f"\n{'='*60}\n全部汇总\n{'='*60}")
    for code, cname, res in summary:
        print(f"{code}  {cname:<10} {res}")
    print(f"文件目录：{OUT_ROOT}")


if __name__ == "__main__":
    main()