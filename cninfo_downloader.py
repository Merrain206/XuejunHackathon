#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
巨潮资讯网公告批量下载工具
支持流式下载、断点续传、多线程并发、进度可视化
"""

import os
import re
import time
import random
import logging
import argparse
import json
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# ──────────────────────────── 日志配置 ────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("cninfo_download.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ──────────────────────────── 常量 ────────────────────────────────
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
DOWNLOAD_BASE = "http://static.cninfo.com.cn/"
# 巨潮股票全量表（含 code→orgId 映射），沪深北三市通用
STOCK_LIST_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
# 本地缓存文件，避免每次运行都重新拉全量表
STOCK_CACHE_FILE = Path(".cninfo_stock_cache.json")
PAGE_SIZE = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "http://www.cninfo.com.cn",
}

# 非法文件名字符替换映射
ILLEGAL_CHARS = r'[\\/:*?"<>|]'


# ──────────────────────────── 工具函数 ────────────────────────────

def sanitize_filename(name: str) -> str:
    """清洗文件名：替换 Windows/Linux 不允许的特殊字符，截断超长名称"""
    name = re.sub(ILLEGAL_CHARS, "_", name)
    name = name.strip(". ")          # 去除首尾的点和空格
    return name[:200]                 # 防止文件名过长


def detect_exchange(stock_code: str) -> tuple[str, str]:
    """
    根据股票代码前缀推断交易所，返回 (column, plate)。
      沪市主板/科创板 6xxxx: column=sse,  plate=sh
      深市主板/创业板 0/3xxxx: column=szse, plate=sz
      北交所 4/8xxxx: column=bj,   plate=bj

    注意：column/plate 仅用于接口的 plate 筛选字段，
    stock 参数必须使用 orgId（见 resolve_org_id），不再拼板块后缀。
    """
    prefix = stock_code[:1]
    if prefix == "6":
        return "sse", "sh"
    elif prefix in ("0", "3"):
        return "szse", "sz"
    elif prefix in ("4", "8"):
        return "bj", "bj"
    else:
        logger.warning("无法识别股票代码前缀 %s，默认使用沪市", stock_code)
        return "sse", "sh"


def resolve_org_id(stock_code: str) -> str:
    """
    通过巨潮全量股票表查询 stock_code 对应的内部机构 ID（orgId）。

    巨潮查询接口的 stock 参数格式为 "股票代码,orgId"（而非 "代码,板块"），
    拼错将导致接口静默返回 0 条。此函数从官方全量表取到正确的 orgId。

    全量表会缓存到本地 STOCK_CACHE_FILE，避免每次运行都重新拉取。
    如果查不到该代码，抛出 ValueError，禁止静默继续（避免下载 0 条）。
    """
    # ── 读取本地缓存 ──────────────────────────────────────────────
    cache: dict[str, str] = {}
    if STOCK_CACHE_FILE.exists():
        try:
            with open(STOCK_CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
            logger.debug("从缓存加载股票列表，共 %d 条", len(cache))
        except (json.JSONDecodeError, OSError):
            logger.warning("缓存文件损坏，重新拉取")
            cache = {}

    if stock_code in cache:
        return cache[stock_code]

    # ── 拉取巨潮全量股票表 ────────────────────────────────────────
    logger.info("本地缓存未命中，正在从巨潮拉取全量股票列表…")
    try:
        resp = requests.get(
            STOCK_LIST_URL,
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        raise RuntimeError(f"拉取巨潮股票列表失败：{e}") from e
    except ValueError as e:
        raise RuntimeError(f"巨潮股票列表响应非法 JSON：{e}") from e

    # 全量表结构：{"stockList": [{"code": "000001", "orgId": "9900000001", ...}, ...]}
    stock_list: list[dict] = data.get("stockList") or []
    if not stock_list:
        raise RuntimeError("巨潮股票列表为空，接口可能已变更")

    # 构建 code→orgId 映射并写入缓存
    cache = {item["code"]: item["orgId"] for item in stock_list if "code" in item and "orgId" in item}
    try:
        with open(STOCK_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        logger.info("股票列表已缓存到 %s（%d 条）", STOCK_CACHE_FILE, len(cache))
    except OSError as e:
        logger.warning("缓存写入失败（不影响本次运行）：%s", e)

    # ── 查找目标代码 ──────────────────────────────────────────────
    if stock_code not in cache:
        raise ValueError(
            f"股票代码 {stock_code!r} 在巨潮全量表中未找到，"
            "请确认代码是否正确或该股票是否已在巨潮收录。"
        )

    return cache[stock_code]


def build_query_payload(stock_code: str, org_id: str, column: str, plate: str,
                        start_date: str, end_date: str, page: int) -> dict:
    """
    构造 POST 请求体。

    关键修正：stock 字段格式为 "股票代码,orgId"（内部机构ID），
    而非此前错误的 "股票代码,板块后缀"（后者会导致接口返回 0 条）。
    """
    return {
        "stock": f"{stock_code},{org_id}",   # 正确格式：代码 + 巨潮内部 orgId
        "tabName": "fulltext",
        "pageNum": page,
        "pageSize": PAGE_SIZE,
        "column": column,
        "category": "",
        "plate": plate,
        "seDate": f"{start_date} ~ {end_date}",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }


# ──────────────────────────── 公告列表抓取 ────────────────────────

def fetch_announcements(stock: str, start_date: str, end_date: str) -> list[dict]:
    """
    分页遍历巨潮接口，返回该股票在指定时间范围内的全部公告元数据列表。
    每条记录包含 announcementTitle、adjunctUrl、adjunctSize 等字段。

    修正：先通过 resolve_org_id 获取巨潮内部机构 ID，
    再拼装正确的 stock 参数（"代码,orgId"），否则接口静默返回 0 条。
    """
    # 查询 orgId，找不到时直接抛异常（禁止静默下载 0 条）
    org_id = resolve_org_id(stock)
    column, plate = detect_exchange(stock)
    logger.info("股票 %s 对应 orgId=%s，交易所 column=%s", stock, org_id, column)

    all_items: list[dict] = []
    page = 1

    session = requests.Session()
    session.headers.update(HEADERS)

    while True:
        payload = build_query_payload(stock, org_id, column, plate, start_date, end_date, page)
        try:
            resp = session.post(QUERY_URL, data=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error("查询第 %d 页失败：%s", page, e)
            break
        except ValueError:
            logger.error("第 %d 页响应不是合法 JSON", page)
            break

        items = data.get("announcements") or []
        total = data.get("totalAnnouncement", 0)
        all_items.extend(items)

        logger.info("第 %d 页：获取 %d 条，累计 %d / %d", page, len(items), len(all_items), total)

        # 判断是否已到最后一页
        if not items or len(all_items) >= total:
            break

        page += 1
        # 礼貌性延迟，降低被封风险
        time.sleep(random.uniform(1, 3))

    logger.info("共获取 %d 条公告元数据", len(all_items))
    return all_items


# ──────────────────────────── 单文件下载 ──────────────────────────

def download_file(item: dict, output_dir: Path) -> bool:
    """
    下载单个公告 PDF，实现：
      1. 流式下载（stream=True + iter_content）：内存恒定，不受文件大小影响
      2. 断点续传（Range 请求头）：网络中断后从已下载位置继续
      3. tqdm 进度条：实时显示下载速度和进度

    返回 True 表示成功，False 表示失败。
    """
    adjunct_url: str = item.get("adjunctUrl", "")
    title: str = item.get("announcementTitle", "未知标题")
    remote_size: int = item.get("adjunctSize", 0)

    if not adjunct_url:
        logger.warning("公告《%s》缺少 adjunctUrl，跳过", title)
        return False

    download_url = DOWNLOAD_BASE + adjunct_url
    # 取 URL 末段作为文件扩展名，通常为 .PDF
    suffix = Path(adjunct_url).suffix or ".PDF"
    filename = sanitize_filename(title) + suffix
    filepath = output_dir / filename

    # ── 断点续传：计算已下载大小 ──────────────────────────────────
    resume_pos = 0
    if filepath.exists():
        resume_pos = filepath.stat().st_size
        if remote_size and resume_pos >= remote_size:
            logger.info("已完整下载，跳过：%s", filename)
            return True
        logger.info("断点续传（已有 %d 字节）：%s", resume_pos, filename)

    # ── 构造请求头 ────────────────────────────────────────────────
    headers = dict(HEADERS)
    if resume_pos > 0:
        # Range: bytes=已下载字节数-  → 服务端从该位置开始返回剩余内容
        headers["Range"] = f"bytes={resume_pos}-"

    try:
        # stream=True：响应体不立即加载到内存，而是通过迭代器逐块读取
        response = requests.get(download_url, headers=headers, stream=True, timeout=60)

        # 服务端可能返回 206 Partial Content（断点续传成功）或 200（重新下载）
        if response.status_code == 416:
            # 416 Range Not Satisfiable 表示已完整下载
            logger.info("文件已完整（416 响应），跳过：%s", filename)
            return True

        response.raise_for_status()

        # 获取本次响应的内容长度（不含已下载部分）
        content_length = int(response.headers.get("Content-Length", 0))
        total_size = resume_pos + content_length if content_length else None

        # 若服务端返回 200（而非 206），说明不支持断点续传，需从头写入
        file_mode = "ab" if response.status_code == 206 else "wb"
        if file_mode == "wb":
            resume_pos = 0  # 重置，不再追加

        # ── tqdm 进度条 ───────────────────────────────────────────
        with tqdm(
            desc=filename[:50],          # 显示截断后的文件名
            total=total_size,
            initial=resume_pos,          # 已完成部分不重复计入
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            leave=False,                 # 下载完成后清除进度条
        ) as pbar:
            # 以追加（ab）或覆写（wb）模式打开文件
            with open(filepath, file_mode) as f:
                # iter_content 按 chunk_size=8192 字节逐块读取并写入
                # 避免一次性加载整个 PDF 到内存（OOM 防护核心）
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:           # 过滤心跳空块
                        f.write(chunk)
                        pbar.update(len(chunk))

        logger.info("下载完成：%s", filename)
        return True

    except requests.exceptions.HTTPError as e:
        logger.error("HTTP 错误（%s）：%s", e.response.status_code, filename)
    except requests.exceptions.Timeout:
        logger.error("下载超时：%s", filename)
    except requests.exceptions.ConnectionError as e:
        logger.error("连接错误（%s）：%s", e, filename)
    except OSError as e:
        logger.error("文件写入失败（%s）：%s", e, filename)

    return False


# ──────────────────────────── 并发下载调度 ────────────────────────

def batch_download(items: list[dict], output_dir: Path, workers: int) -> None:
    """
    使用 ThreadPoolExecutor 并发下载，每个任务之间加入随机延迟。
    任务粒度：单个公告 PDF。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(items)
    success = 0
    failed = 0

    logger.info("开始下载，共 %d 个文件，并发数 %d", total, workers)

    def download_with_delay(item: dict) -> bool:
        # 随机延迟，模拟人工操作，降低反爬触发概率
        time.sleep(random.uniform(0.5, 2.0))
        return download_file(item, output_dir)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download_with_delay, item): item for item in items}

        # 使用外层进度条统计整体完成情况
        with tqdm(total=total, desc="总进度", unit="文件") as overall:
            for future in as_completed(futures):
                item = futures[future]
                try:
                    ok = future.result()
                    if ok:
                        success += 1
                    else:
                        failed += 1
                except Exception as e:
                    title = item.get("announcementTitle", "未知")
                    logger.error("下载任务异常（%s）：%s", title, e)
                    failed += 1
                finally:
                    overall.update(1)

    logger.info("下载完成：成功 %d，失败 %d，共 %d", success, failed, total)


# ──────────────────────────── 命令行入口 ──────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="巨潮资讯网公告批量下载工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 下载思看科技（688583）2024~2025 年所有公告
  python cninfo_downloader.py --stock 688583 --start-date 2024-01-01 --end-date 2025-12-31

  # 指定保存目录和并发数
  python cninfo_downloader.py --stock 000001 --start-date 2023-01-01 --end-date 2024-12-31 \\
    --output-dir ./平安银行_公告 --workers 5
""",
    )
    parser.add_argument("--stock", required=True, help="股票代码，例如 688583")
    parser.add_argument("--start-date", required=True, help="开始日期，格式 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="结束日期，格式 YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=3,
                        help="并发下载线程数（默认 3，建议不超过 5）")
    parser.add_argument("--output-dir", default="./cninfo_data",
                        help="文件保存目录（默认 ./cninfo_data）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    output_dir = Path(args.output_dir) / args.stock
    logger.info(
        "参数：股票=%s，日期=%s ~ %s，线程数=%d，保存至=%s",
        args.stock, args.start_date, args.end_date, args.workers, output_dir,
    )

    # 第一步：分页获取全部公告元数据
    # resolve_org_id 内部若找不到代码会抛 ValueError，避免静默下载 0 条
    try:
        items = fetch_announcements(args.stock, args.start_date, args.end_date)
    except ValueError as e:
        logger.error("股票代码错误：%s", e)
        raise SystemExit(1)

    if not items:
        logger.warning("未查询到任何公告，程序退出")
        return

    # 过滤无下载链接的条目
    downloadable = [i for i in items if i.get("adjunctUrl")]
    skipped = len(items) - len(downloadable)
    if skipped:
        logger.info("过滤掉 %d 条无下载链接的公告", skipped)

    # 第二步：并发批量下载
    batch_download(downloadable, output_dir, args.workers)


if __name__ == "__main__":
    main()
