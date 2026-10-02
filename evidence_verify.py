# -*- coding: utf-8 -*-
"""evidence_verify.py — Evidence 引文核验的共享规则（只读，无副作用）。

这个模块只回答一个问题：**这条 Evidence 的 source_quote 到底能不能在它标注的
那一页原文里找到？** 它不调用 LLM、不写库，也不生成任何业务结论。

## 为什么不能直接做整串子串匹配

`evidence.source_quote` 的真实形态是**表格行的单元格拼接**，例如：

    营业收入 | 185,101,612.27 | 176,848,509.44 | 4.67

而 PDF 文本层把同一行的单元格抽成了多行：

    营业收入 185,101,612.27 176,848,509.44 4.67

所以「整串子串匹配」对绝大多数真实证据都会失败 —— 这不是数据造假，而是
**表格抽取的表达差异**。但反过来说，也**不能**因此就放宽到"随便命中一个字就算过"，
那等于放弃校验。本模块的做法是：

1. 按 `|` 把引文拆成 **segment**（文字段 + 数值段）；
2. 每个 segment 都必须落在该页原文里；
3. 文字段按**忽略空白后的连续子串**匹配（这是"逐字原文"的可检验含义）；
4. 数值段按**完整数字**匹配 —— 归一化千分位/小数位后再比对，并且要求
   命中位置左右不是数字或逗号（否则 `4.67` 会命中 `1304.67`，`0.39` 会命中 `30.39`）。

## 三种判定

* `verbatim` —— 整段引文（忽略空白后）**连续**出现在该页。后端
  `db.page_from_markers()` 用的就是这条更严的规则，所以这类引文后端也能自证。
* `segments` —— 每个 segment 都命中该页，但引文不是连续原文（表格行拼接）。
  数据属实、位置属实，只是**后端严格核验会判为找不到**，需要按 segment 核验。
* `failed` —— 有 segment 找不到。这才是真正需要人看的问题。

判定之外，还会报出**引文实际出现过的页码**：如果标注页找不到、别页却找得到，
那是**页码标注错误**，必须单独列出来，不能混进"通过"。
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

#: `docs.text_content` 里的页界标记，形如 `--- 第8页 ---`
PAGE_MARKER = re.compile(r"^---\s*第(\d+)页\s*---\s*$")

#: 纯数值 segment（含千分位、负号、百分号、破折号占位）
_NUMERIC = re.compile(r"^[\d,.\-—－+%]+$")

#: 页面文本里的数字字面量（千分位/小数/负号/百分号）。
#: ⚠️ 负号用 `(?<![\d%])` 限制：`33,000-35,000` 里的短横是**区间分隔符**，
#: 不是负号。否则 `35,000` 会被读成 `-35000`，与区间的右端点对不上。
_NUMBER_LITERAL = re.compile(r"(?<![\d%])[-+]?\d[\d,]*(?:\.\d+)?%?")

#: 中文/全角标点，做"宽松匹配"时剔除
_PUNCT = re.compile(r"[，。、；：\u201c\u201d\u2018\u2019（）()\[\]【】|]")

#: 页脚这类和正文无关、却容易误命中的短串（放宽判定时排除）
_STOPWORDS = {"-", "--", "—", "－", "/", "不适用", "无"}


def normalize(text: Optional[str]) -> str:
    """去掉所有空白字符。这是 pages 比对的基础归一化。"""
    return re.sub(r"\s+", "", text or "")


def squash_digits(text: Optional[str]) -> str:
    """归一化数字写法：去千分位逗号、统一负号/百分号。"""
    out = (text or "")
    out = out.replace("，", ",").replace("－", "-").replace("—", "-")
    return out


def extract_pages(text_content: Optional[str]) -> Dict[int, str]:
    """把 `docs.text_content` 按 `--- 第N页 ---` 标记切成 {页码: 该页文本}。

    标记本身不属于任何一页的正文。没有标记时返回空 dict。
    """
    pages: Dict[int, str] = {}
    current: Optional[int] = None
    buf: List[str] = []
    for line in (text_content or "").split("\n"):
        match = PAGE_MARKER.match(line)
        if match:
            if current is not None:
                pages[current] = "\n".join(buf)
            current = int(match.group(1))
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        pages[current] = "\n".join(buf)
    return pages


def split_segments(quote: Optional[str]) -> List[str]:
    """把引文按 `|` 拆成非空 segment（去首尾空白）。"""
    return [part.strip() for part in (quote or "").split("|") if part.strip()]


def _canonical_number(text: str) -> str:
    """把一个数字写法压成可比对的规范形式。

    `332,583,883.61` → `332583883.61`；`-18,209,215.68` → `-18209215.68`；
    `22.41%` → `22.41`；`0.60` → `0.6`；`-`（占位符）→ `-`。

    去掉千分位逗号后，数字在原文里的写法差异就消失了；百分号也一并去掉，
    因为同一页里财务比率常同时出现「22.41」和「22.41%」两种写法，
    保留百分号只会制造假失败，而不会增加任何判别力。
    """
    raw = squash_digits(text).strip().rstrip("%")
    body = raw.replace(",", "").replace(" ", "")
    try:
        value = float(body)
    except ValueError:
        return raw
    return f"{value:g}"


def _number_variants(segment: str, *, split_range: bool = False) -> List[str]:
    """数值 segment 的规范形式。

    处理千分位、正负号、百分号，以及 `12,000-13,000` / `5.03%-13.78%` 这类区间。

    :param split_range: True 时返回 `(整段规范形式, [端点规范形式])`，
        仅供 `_number_in_page` 做"整段优先、端点兜底"的判定。
    """
    text = squash_digits(segment).strip().rstrip("%")
    whole = _canonical_number(text)
    parts = re.split(r"(?<=[\d%])-(?=\d)", text)
    endpoints = [v for v in (_canonical_number(p) for p in parts if p.strip()) if v]
    if split_range:
        return whole, (endpoints if len(parts) > 1 else [])
    return [v for v in dict.fromkeys([whole, *endpoints]) if v]


def _number_in_page(page_text: str, segment: str) -> bool:
    """页面上是否存在与 `segment` 值相等的完整数字（或完整区间）。

    ⚠️ 必须用**原始**页面文本扫描，不能用去掉空白的版本：表格里的相邻单元格
    是「数字 + 换行 + 数字」，压掉空白后会粘成一个数字
    （`332,583,883.61` + `271,707,663.51` → `332,583,883.61271,707,663.51`），
    导致后一个数字永远匹配不上。

    只要求"页面上存在该数值"，**不**要求它和标签相邻 —— PDF 表格抽取会把
    同一行的单元格拆到不同位置，位置关系不可靠。

    区间的判定分两步，避免"只命中一个端点就算过"：
      1. 页面上有完整区间（`33,000-35,000`）→ 通过；
      2. 否则要求**所有**端点（`33,000` 和 `35,000`）都在页面上 → 通过。
    """
    page_values = {_canonical_number(lit) for lit in _NUMBER_LITERAL.findall(page_text)}
    whole, endpoints = _number_variants(segment, split_range=True)
    if whole in page_values:
        return True
    if endpoints:
        return all(endpoint in page_values for endpoint in endpoints)
    return False


def _numeric_candidates(segment: str) -> List[str]:
    """一个数值 segment 的可接受规范形式（列表）。"""
    return _number_variants(segment)


def _is_subsequence(needle: str, haystack: str) -> bool:
    """needle 的字符是否按顺序出现在 haystack 里（允许中间有别的字符）。

    注意：这**只**用在短标签上（见 `segment_hits`），而且必须配合"数值段全部命中"
    才构成有效证据 —— 单靠子序列匹配太弱，不足以证明任何事。
    """
    if not needle:
        return False
    position = 0
    for char in haystack:
        if char == needle[position]:
            position += 1
            if position == len(needle):
                return True
    return False


def segment_hits(segment: str, page_text: str) -> bool:
    """单个 segment 是否命中该页的**原始文本**。

    * 数值段：页面里必须存在一个**完整的、值相等**的数字。
    * 文字段：按"忽略空白后的连续子串"匹配；不中则退一步做子序列匹配，
      以覆盖 PDF 表格把行标签拆到两行、中间夹着同行其它单元格的情况。
      子序列匹配较宽松，因此只在标签足够长（>= 4 字）时启用，避免"的""元"
      这类单字造成假命中。
    """
    if not segment or not page_text:
        return False
    if _NUMERIC.match(squash_digits(segment)):
        return _number_in_page(page_text, segment)

    page_norm = normalize(page_text)
    needle = normalize(segment)
    if not needle:
        return False
    if needle in page_norm:
        return True
    # 放宽一：去标点后连续匹配（PDF 里全角/半角括号常不一致）
    loose_needle = _PUNCT.sub("", needle)
    loose_page = _PUNCT.sub("", page_norm)
    if loose_needle and loose_needle in loose_page:
        return True
    # 放宽二：子序列匹配（仅长标签，避免单字造成假命中）
    if len(loose_needle) >= 4 and _is_subsequence(loose_needle, loose_page):
        return True
    return False


#: 表格里表示"没有数据"的占位符 —— 它们不是事实，不该当作必须命中的内容
PLACEHOLDERS = frozenset({"-", "--", "—", "－", "/", "不适用", "无", "null", "N/A"})

#: 占位符的规范化集合（模块加载时算一次）
_PLACEHOLDER_SET = {normalize(p) for p in PLACEHOLDERS}


def is_placeholder(segment: str) -> bool:
    """该 segment 是否只是"空单元格"占位符（如 `-`、`不适用`）。"""
    return normalize(segment) in _PLACEHOLDER_SET


# ---------------------------------------------------------------------------
# 引文修复：把"表格行拼接"重写成页面上真实连续的一段原文
# ---------------------------------------------------------------------------
#
# 背景：库里 `source_quote` 长这样 —— `营业收入 | 185,101,612.27 | 176,848,509.44`，
# 而 PDF 文本层把这些单元格抽成了多行。于是后端 `db.page_from_markers()` 的
# 「忽略空白后连续子串」判定会失败（503 条里 500 条会失败）。
#
# 修复不是改数字，而是**把引文换成页面上真实存在的连续片段**：在标注页里取
# 「覆盖全部内容片段的最小区间」，那一段就是页面原文本身，因此必然能通过
# 连续子串核验，而且仍然包含原来引用的每一个标签和数字。
#
# 修复后引文的语义与原来完全一致（同样的事实、同一页），只是从"拼接写法"
# 换成了"原文写法"。


def locate_segment(
    page_text: str, segment: str, *, start_at: int = 0
) -> Optional[Tuple[int, int]]:
    """定位一个片段在页面原文里的字符区间；找不到返回 None。

    优先级（很重要）：
      1. **连续子串**匹配（忽略空白）—— 这是最可信的定位；
      2. 只有连续匹配不上时，才退化为子序列匹配，用来兜住被表格拆成两行的行标签
         （`归属于上市公司股` + `东的净资产`）。

    ⚠️ 顺序不能反：子序列匹配很贪心，"归属于上市公司股东的净利润"会去匹配
    "归属于上市公司股东的扣除非经常性损益的净利润"里的一大段，
    把两条不同记录的区间粘在一起。先试连续匹配就不会踩这个坑。

    * 数值段：优先整段字面匹配（保住区间 `12,000-13,000` 的后半截），
      否则退化为"完整数字"匹配。
    * `start_at`：只接受起点不早于该下标的结果。
    """
    if not page_text or not segment:
        return None

    squeezed_chars: List[str] = []
    squeezed_to_raw: List[int] = []
    for raw_index, char in enumerate(page_text):
        if not char.isspace():
            squeezed_chars.append(char)
            squeezed_to_raw.append(raw_index)
    squeezed = "".join(squeezed_chars)

    if _NUMERIC.match(squash_digits(segment)):
        literal = squash_digits(segment).strip()
        index = page_text.find(literal, start_at)
        if index < 0 and start_at:
            index = page_text.find(literal)
        if index >= 0:
            return (index, index + len(literal))
        wanted = set(_number_variants(segment))
        for match in _NUMBER_LITERAL.finditer(page_text):
            if match.start() >= start_at and _canonical_number(match.group(0)) in wanted:
                return (match.start(), match.end())
        if start_at:
            for match in _NUMBER_LITERAL.finditer(page_text):
                if _canonical_number(match.group(0)) in wanted:
                    return (match.start(), match.end())
        return None

    needle = normalize(segment)
    if not needle:
        return None

    # 1) 连续子串（忽略空白）
    squeezed_start = 0
    while True:
        index = squeezed.find(needle, squeezed_start)
        if index < 0:
            break
        begin, finish = squeezed_to_raw[index], squeezed_to_raw[index + len(needle) - 1] + 1
        if begin >= start_at:
            return (begin, finish)
        squeezed_start = index + 1

    # 2) 子序列（仅长标签，避免单字假命中）
    if len(needle) < 4:
        return None
    position = 0
    first = last = None
    for index, char in enumerate(squeezed_chars):
        if position < len(needle) and char == needle[position]:
            if first is None:
                first = index
            last = index
            position += 1
            if position == len(needle):
                begin, finish = squeezed_to_raw[first], squeezed_to_raw[last] + 1
                if begin >= start_at:
                    return (begin, finish)
                return None
    return None


def extract_contiguous_span(
    page_text: Optional[str], quote: Optional[str]
) -> Optional[str]:
    """取标注页里覆盖引文全部内容片段**最小的一段连续原文**。

    为什么要"最小"：PDF 会把相邻表格行抽成连续的多行文本，如果随便取
    首个片段到末个片段之间的区间，很容易把上下几行的数字一起卷进来，
    引文就变得又长又容易误导。这里用**锚点 + 最邻近窗口**挑最紧的一段。

    返回 None 表示有片段无法在该页定位（这种情况下**不修**，交给人工处理，
    绝不猜一段文本充数）。
    """
    if not page_text or not quote:
        return None
    segments = [s for s in split_segments(quote) if not is_placeholder(s)]
    if not segments:
        return None

    ordered = _minimal_anchored_window(page_text, segments)
    if ordered is None:
        # 极端情况（锚点反复撞车）退回逐片段独立定位，保证仍有连续区间可用
        spans = _locate_independently(page_text, segments)
        if spans is None:
            return None
        start = min(begin for begin, _ in spans)
        end = max(finish for _, finish in spans)
        return page_text[start:end]
    return page_text[ordered[0]:ordered[1]]


def _occurrences(page_text: str, segment: str) -> List[Tuple[int, int]]:
    """一个片段在页面上的全部出现位置（升序）。

    用逐个起点重试的方式拿到所有匹配 —— `locate_segment` 本身只返回第一个。
    """
    found: List[Tuple[int, int]] = []
    cursor = 0
    while cursor <= len(page_text):
        span = locate_segment(page_text, segment, start_at=cursor)
        if span is None:
            break
        if span in found:
            break
        found.append(span)
        cursor = span[0] + 1
    return found


def _minimal_anchored_window(
    page_text: str, segments: Sequence[str]
) -> Optional[Tuple[int, int]]:
    """覆盖全部片段的最紧窗口。

    **逐个**把片段当锚点试，取所有可行方案里最紧的那个。

    为什么不只用一个锚点：最长的片段往往是行标签，而标签在整页里会反复出现
    （父串包含子串，例如"归属于上市公司股东的净利润"是
    "归属于上市公司股东的扣除非经常性损益的净利润"的子串），拿它当锚点会把
    窗口撑到别的行去。数字通常唯一得多，所以多试几个锚点再取最紧的最稳。
    """
    order = sorted(range(len(segments)), key=lambda i: len(normalize(segments[i])),
                   reverse=True)
    best: Optional[Tuple[int, int]] = None
    for anchor_index in order:
        anchors = _occurrences(page_text, segments[anchor_index])
        for anchor in anchors:
            window = _window_around(page_text, segments, anchor_index, anchor)
            if window is None:
                continue
            if best is None or (window[1] - window[0]) < (best[1] - best[0]):
                best = window
    return best


def _window_around(
    page_text: str,
    segments: Sequence[str],
    anchor_index: int,
    anchor: Tuple[int, int],
) -> Optional[Tuple[int, int]]:
    """以某个锚点出现位置为中心，向两侧扩展到覆盖全部片段。"""
    spans: List[Optional[Tuple[int, int]]] = [None] * len(segments)
    spans[anchor_index] = anchor

    cursor = anchor[0]
    for i in range(anchor_index + 1, len(segments)):
        span = locate_segment(page_text, segments[i], start_at=cursor)
        if span is None:
            return None
        spans[i] = span
        cursor = span[0]

    limit = anchor[1]
    for i in range(anchor_index - 1, -1, -1):
        candidates = [s for s in _occurrences(page_text, segments[i]) if s[1] <= limit]
        if not candidates:
            return None
        spans[i] = candidates[-1]
        limit = spans[i][0]  # type: ignore[index]

    start = min(begin for begin, _ in spans)  # type: ignore[misc]
    end = max(finish for _, finish in spans)  # type: ignore[misc]
    return (start, end)


def _scan_forward(
    page_text: str, segments: Sequence[str]
) -> Optional[List[Tuple[int, int]]]:
    """按阅读顺序逐片段定位；任一失败返回 None。"""
    spans: List[Tuple[int, int]] = []
    cursor = 0
    for segment in segments:
        span = locate_segment(page_text, segment, start_at=cursor)
        if span is None:
            return None
        spans.append(span)
        cursor = span[0]
    return spans


def _locate_independently(
    page_text: str, segments: Sequence[str]
) -> Optional[List[Tuple[int, int]]]:
    """每个片段独立定位（取首次出现）。"""
    spans: List[Tuple[int, int]] = []
    for segment in segments:
        span = locate_segment(page_text, segment)
        if span is None:
            return None
        spans.append(span)
    return spans


def is_contiguous_quote(quote: Optional[str], page_text: Optional[str]) -> bool:
    """引文（忽略空白后）是否是该页原文的一段**连续子串** —— 后端严格判定用的就是这条。"""
    needle = normalize(quote)
    if not needle or not page_text:
        return False
    return needle in normalize(page_text)


def page_findings(quote: Optional[str], page_text: Optional[str]) -> Dict[str, object]:
    """引文与**某一页**的比对明细。

    判定只看"内容片段"：`-` / `不适用` 这类空单元格占位符不算内容，缺失也不算问题
    （它们本来就代表表格里没有数字）。但只要有**一个**内容片段找不到，就不算通过。
    """
    segments = split_segments(quote)
    text = page_text or ""
    content = [s for s in segments if not is_placeholder(s)]
    matched = [s for s in content if segment_hits(s, text)]
    missed = [s for s in content if s not in matched]
    squeezed_quote = normalize(quote)
    squeezed_page = normalize(text)
    contiguous = bool(squeezed_quote) and squeezed_quote in squeezed_page
    return {
        "segments": segments,
        "content_segments": content,
        "matched": matched,
        "missed": missed,
        # contiguous == 后端 db.page_from_markers() 的判定口径
        "contiguous": contiguous,
        "verbatim": contiguous,
        "segments_ok": bool(content) and not missed,
    }


def locate_quote(
    quote: Optional[str],
    pages: Dict[int, str],
    *,
    scan_all: bool = True,
) -> List[int]:
    """引文在文档里真正出现（整段连续命中）的页码，升序。

    只用于发现"页码标错"。全文档扫描是 O(页数)，只在需要时报出。
    """
    if not scan_all:
        return []
    needle = normalize(quote)
    if not needle:
        return []
    return sorted(p for p, text in pages.items() if needle in normalize(text))


def best_pages(
    quote: Optional[str],
    pages: Dict[int, str],
    *,
    limit: int = 5,
) -> List[Tuple[int, int]]:
    """按"命中 segment 数"给页码排序，返回 [(页码, 命中数), ...]。

    用于在标注页失配时给出**线索**（例如引文其实在第 N 页）。
    这只是线索，不是结论 —— 命中多半段也可能只是表格结构相似。
    """
    segments = split_segments(quote)
    if not segments:
        return []
    scored = []
    for page, text in pages.items():
        hits = sum(1 for s in segments if segment_hits(s, text))
        if hits:
            scored.append((page, hits))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:limit]


def verify_record(
    *,
    company_code: str,
    document_id: int,
    metric: Optional[str],
    value: Optional[float],
    unit: Optional[str],
    period: Optional[str],
    source_page: Optional[int],
    source_quote: Optional[str],
    source_url: Optional[str],
    review_status: Optional[str],
    document: Dict[str, object],
    pages: Dict[int, str],
    excluded: bool = False,
    excluded_reason: Optional[str] = None,
) -> Dict[str, object]:
    """对一条 Evidence 做完整的 7 项机械核验，返回结构化结果。

    与 docs/TONIGHT_DATABASE_TASKS.md §3 的 7 项一一对应。
    本函数只做机械判断，**不把 auto 提升为 verified**。

    :param excluded: 该记录在库里被显式隔离（`evidence.excluded = 1`）。
        隔离记录一律判 `excluded`，**不计入通过、也不参与任何回答**；
        它们是被判定不能支撑回答、但保留在库中留档的行。
    """
    issues: List[str] = []
    doc_company = document.get("company_code")
    superseded = document.get("superseded")
    parse_status = document.get("parse_status")
    page_count = document.get("page_count")

    if excluded:
        return {
            "status": "excluded",
            "quote_state": "excluded",
            "quote_verbatim": False,
            "contiguous": False,
            "structural_ok": False,
            "value_ok": False,
            "unit_ok": False,
            "period_ok": False,
            "url_ok": False,
            "page_ok": False,
            "document_active": True,
            "same_company": doc_company == company_code,
            "issues": [f"已隔离，不参与回答：{excluded_reason or '未注明原因'}"],
            "findings": {},
        }

    # 1) document_id 属于同一 company_code
    if doc_company == company_code:
        same_company = True
    else:
        same_company = False
        issues.append(f"跨公司引用：evidence={company_code} document={doc_company}")

    # 2) document 未 superseded 且 parse_status='ok'
    doc_active = superseded == 0 and parse_status == "ok"
    if not doc_active:
        issues.append(f"文档不可用：superseded={superseded} parse_status={parse_status}")

    # 3) source_page 是正整数且不超过 page_count
    page_ok = (
        isinstance(source_page, int)
        and not isinstance(source_page, bool)
        and source_page > 0
        and (page_count is None or source_page <= int(page_count))
    )
    if not page_ok:
        issues.append(f"source_page 非法或越界：{source_page} / page_count={page_count}")

    # 4) source_quote 能在该页原文命中（segment 级）
    quote_state = "failed"
    findings: Dict[str, object] = {}
    if not source_quote:
        issues.append("source_quote 为空")
    elif not page_ok:
        issues.append("source_page 非法，无法核验引文")
    else:
        page_text = pages.get(int(source_page), "")
        if not page_text:
            issues.append(f"第 {source_page} 页无可用原文（该文档已切页 {len(pages)} 页）")
        else:
            findings = page_findings(source_quote, page_text)
            if findings["verbatim"]:
                quote_state = "verbatim"
            elif findings["segments_ok"]:
                quote_state = "segments"
            else:
                missed = findings["missed"]  # type: ignore[index]
                total = findings["content_segments"]  # type: ignore[index]
                issues.append(
                    f"引文有 {len(missed)}/{len(total)} 个内容片段"
                    f"在第 {source_page} 页找不到：{missed[:3]}"
                )
                elsewhere = locate_quote(source_quote, pages)
                alt = [page for page, _hits in best_pages(source_quote, pages)
                       if page != source_page]
                if elsewhere:
                    issues.append(f"整段引文实际出现在第 {elsewhere[:5]} 页（页码标注错误）")
                elif alt:
                    issues.append(f"片段最集中的页码为 {alt[:3]}（疑似页码偏移）")

    # 5) value / unit / period 与 quote 一致
    value_ok = True
    if value is not None and source_quote:
        quote_digits = squash_digits(source_quote)
        candidates = {f"{value:,.2f}", f"{value:.2f}", f"{value:,.0f}", f"{value:.0f}",
                      f"{value:g}"}
        if not any(candidate and candidate in quote_digits for candidate in candidates):
            value_ok = False
            issues.append(f"value={value} 未在 source_quote 中找到对应数字")
    # unit：只有当这条证据真的带数值时才有意义。定性证据（如风险提示）value 为空，
    # 没有单位是正常的，不能因此判它不能支撑回答。
    unit_ok = bool(unit) or value is None
    if not unit_ok:
        issues.append("unit 为空但存在数值")
    period_ok = bool(period)
    if not period_ok:
        issues.append("period 为空，无法定位报告期")

    # 6) / 7) source_url：非空、指向 PDF、不含 #page=、协议合法
    url_ok = False
    if not source_url or not isinstance(source_url, str):
        issues.append("source_url 为空")
    elif "#page=" in source_url:
        issues.append("source_url 含 #page= fragment")
    elif not squash_digits(source_url).lower().split("?")[0].endswith(".pdf"):
        issues.append(f"source_url 不是 .pdf：{source_url[:70]}")
    elif not source_url.startswith(("http://", "https://")):
        issues.append(f"source_url 协议异常：{source_url[:70]}")
    else:
        url_ok = True

    structural_ok = same_company and doc_active and page_ok and url_ok
    quote_ok = quote_state in ("verbatim", "segments")
    fields_ok = value_ok and unit_ok and period_ok
    all_ok = structural_ok and quote_ok and fields_ok

    if not structural_ok:
        status = "rejected"
    elif all_ok:
        # 永远继承库里已有的 review_status —— 机械校验**不能**把 auto 变成 verified
        status = "verified" if review_status == "verified" else "auto"
    else:
        # 引文位置正确但元数据不全（如 period/value 有问题）→ 不能支撑回答
        status = "rejected"

    return {
        "status": status,
        "quote_state": quote_state,
        "quote_verbatim": quote_state == "verbatim",
        "contiguous": bool(findings.get("contiguous")),
        "structural_ok": structural_ok,
        "value_ok": value_ok,
        "unit_ok": unit_ok,
        "period_ok": period_ok,
        "url_ok": url_ok,
        "page_ok": page_ok,
        "document_active": doc_active,
        "same_company": same_company,
        "issues": issues,
        "findings": findings,
    }


def summarize(records: Iterable[Dict[str, object]]) -> Dict[str, int]:
    """统计各判定的条数。"""
    out = {"verified": 0, "auto": 0, "rejected": 0, "excluded": 0,
           "verbatim": 0, "segments": 0, "failed": 0}
    for record in records:
        status = str(record.get("status"))
        if status in out:
            out[status] += 1
        quote_state = str(record.get("quote_state"))
        if quote_state in out:
            out[quote_state] += 1
    return out


__all__ = [
    "PAGE_MARKER",
    "PLACEHOLDERS",
    "best_pages",
    "extract_contiguous_span",
    "extract_pages",
    "is_contiguous_quote",
    "is_placeholder",
    "locate_quote",
    "locate_segment",
    "normalize",
    "page_findings",
    "segment_hits",
    "split_segments",
    "squash_digits",
    "summarize",
    "verify_record",
]
