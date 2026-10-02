"""X-Ray 企业穿透分析 · 问题相似度匹配（命中预计算答案）。

需求[八] 白天查询链路第 1 步：
    问题标准化匹配（去标点、同义词替换、编辑距离阈值 0.8）

设计要点：
  * 纯函数、零依赖、零网络 —— 匹配必须在 <100ms 预算内完成；
  * question_hash 必须**稳定**：夜间写 answer_cache 与白天查缓存走同一个函数；
  * 同义词替换用**占位符两遍法**：先把变体整体换成占位符，再替换成规范词。
    直接顺序 str.replace 会把「现金流」拆成「现金」+「流」再二次替换，
    产生「流流」这类坏词（本文件的自检抓到了这个 bug，故用占位符法修掉）。

匹配分两档（这是「编辑距离阈值 0.8」与「尽量别让前端拿到无关答案」之间的折中）：
  * 严格档 threshold（默认 0.8）→ 正常命中；
  * 兜底档 MATCH_FALLBACK_FLOOR（默认 0.45）→ 允许命中，但调用方必须在
    answer 里标注数据时效/匹配度说明，不允许静默当作精确命中。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Sequence

#: 规范化规则版本 —— 改动同义词表或标准化规则时必须 +1（旧缓存自动失效）
HASH_VERSION: int = 1

#: 严格相似度阈值（需求[八]：编辑距离阈值 0.8）
DEFAULT_THRESHOLD: float = 0.8

#: 兜底档下限：低于此分数一律视为未命中
MATCH_FALLBACK_FLOOR: float = 0.45


# ---------------------------------------------------------------------------
# 同义词表（键 = 规范说法，值 = 用户可能写的变体）
#
# ⚠️ 维护规则：
#   1. 规范词之间不得互相包含（否则替换顺序会产生坏词）；
#   2. 变体尽量用「不会在其它变体里出现」的写法，避免歧义
#      （例如用「被执行」而不是「执行」，因为「执行」也是「执行信息」的前缀）；
#   3. 改动本表后必须升 HASH_VERSION。
# ---------------------------------------------------------------------------

SYNONYMS: dict[str, tuple[str, ...]] = {
    "营收": ("营业收入", "营业总收入", "营收规模", "营业收入情况", "营收情况", "收入情况", "销售额", "营业额", "收入"),
    "净利润": ("归母净利润", "归属于上市公司股东的净利润", "净利润情况", "净利", "利润情况", "盈利", "赚钱", "利润"),
    "现金流": ("经营活动现金流", "经营性现金流", "经营现金流", "经营活动产生的现金流量净额", "现金流量", "现金净流入"),
    "应收账款": ("应收账款情况", "应收款项", "应收", "回款", "账款"),
    "社保": ("社会保险", "参保人数", "参保人员", "参保", "员工人数", "人力", "人头"),
    "司法风险": (
        "失信被执行人",
        "被执行人",
        "法律风险",
        "法务风险",
        "执行信息",
        "被执行人情况",
        "被执行",
        "失信",
        "诉讼",
        "涉诉",
        "官司",
    ),
    "风险": ("隐患", "异常", "猫腻", "疑点", "毛病"),
    "整体": ("总体", "综合", "总体情况"),
    "情况": ("状况", "表现", "情形", "态势"),
    "健康": ("稳健", "正常", "良好", "安全"),
    "变化": ("变动", "改变", "走势", "趋势"),
    "如何": ("怎么样", "怎样", "咋样", "是什么情况", "是多少", "是多少呢", "多少", "几"),
    "有没有": ("是否存在", "是否有", "有无"),
    "公司": ("企业", "上市公司", "标的"),
}

#: 占位符（用不可打印字符包裹，确保不会与用户输入碰撞）
_PH_OPEN = "\x00"
_PH_CLOSE = "\x01"
_PLACEHOLDER = re.compile(r"\x00(\d+)\x01")

#: 需要剔除的标点/空白/语气词
_PUNCT_PATTERN = re.compile(
    r"[\s\u3000"
    r"\u3001\u3002\uff01\uff1f\uff0c\uff1b\uff1a"
    r"\uff08\uff09\u3010\u3011\u300a\u300b\u201c\u201d\u2018\u2019"
    r"!?,.;:~`\"'()\[\]{}<>\-_/\\|@#$%^&*+=—…·"
    r"]+"
)

#: 语气与冗余省略（保持语义、去掉噪声）
# ⚠️ 不要放「怎样/怎么样/如何」这类完整疑问词：否则「今天的天气怎么样」会把
#    「怎么样」剥掉、只剩「天气」，反而更容易与模板撞车（自检发现的误命中）。
#    疑问词统一交给 SYNONYMS 归一。
_FILLER_PATTERN = re.compile(
    r"(请问|帮我|给我|我想知道|想知道|看一下|看看|查一下|查询|麻烦|的话|这家|该家|一下|呢|吗|啊|吧|呀|哦)"
)

#: 关键词匹配时忽略的词（疑问/指代/连接，不承载业务语义）
_STOP_TERMS: frozenset[str] = frozenset(
    {
        "如何", "什么", "是否", "有没有", "有无", "多少", "情况", "公司", "企业",
        "该", "的", "了", "是", "在", "和", "与", "及", "这", "那", "它", "其",
        "请", "问", "看", "查", "说", "一下", "怎样", "怎么样", "咋样", "表现",
        "状况", "情形", "态势", "走势", "趋势", "良好", "正常", "安全", "稳健",
    }
)

#: 关键词覆盖率阈值：模板关键词有 75% 出现在用户问法里即认为问的是同一件事
KEYWORD_COVERAGE_THRESHOLD: float = 0.75

#: 关键词通道允许的最大长度比（防「用户问法夹带大量无关内容」被误命中）
MAX_KEYWORD_LENGTH_RATIO: float = 1.6

#: 关键词通道命中时，对外报告的相似度下限。
#: 仅用于展示/日志（「命中强度」），不参与阈值判定 —— 阈值判定走 keyword_match。
KEYWORD_HIT_SCORE: float = 0.5


# ---------------------------------------------------------------------------
# 标准化
# ---------------------------------------------------------------------------


def strip_punctuation(text: str) -> str:
    """全角转半角、统一小写、去掉标点空白与语气词。"""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = _PUNCT_PATTERN.sub("", normalized)
    normalized = _FILLER_PATTERN.sub("", normalized)
    return normalized.strip()


def replace_synonyms(text: str) -> str:
    """把用户变体替换成规范说法。

    两遍占位符法：
      第一遍 —— 按变体长度降序，把命中的变体整体替换成 \\x00N\\x01 占位符；
      第二遍 —— 把占位符替换成规范词。
    这样「现金流」不会被后续的「现金」二次处理成「流流」。

    另做**相邻规范词去重**：像「社保参保人数」里「社保」与「参保人数」都映射到
    「社保」，会产生「社保社保」这种叠词，故替换后合并相邻重复的规范词。
    """
    if not text:
        return ""

    # 收集 (变体, 规范词)，长变体优先
    pairs: list[tuple[str, str]] = []
    for canonical, variants in SYNONYMS.items():
        for variant in variants:
            cleaned = strip_punctuation(variant)
            if cleaned:
                pairs.append((cleaned, canonical))
    for canonical in SYNONYMS:
        pairs.append((canonical, canonical))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)

    result = text
    replacements: list[str] = []
    for variant, canonical in pairs:
        if variant and variant in result:
            token = f"{_PH_OPEN}{len(replacements)}{_PH_CLOSE}"
            result = result.replace(variant, token)
            replacements.append(canonical)

    if not replacements:
        return result

    def _swap(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        return replacements[idx] if 0 <= idx < len(replacements) else ""

    swapped = _PLACEHOLDER.sub(_swap, result)

    # 相邻重复规范词去重（占位符替换后才可能相邻）
    for canonical in sorted(SYNONYMS, key=len, reverse=True):
        if len(canonical) < 2:
            continue
        doubled = canonical + canonical
        while doubled in swapped:
            swapped = swapped.replace(doubled, canonical)
    return swapped


def normalize_question(text: str) -> str:
    """标准化问题：去标点 → 同义词替换 → 清理残留标点。

    这是 question_hash 的唯一输入，夜间/白天必须走同一函数。
    """
    if text is None:
        return ""
    stripped = strip_punctuation(text)
    if not stripped:
        return ""
    normalized = replace_synonyms(stripped)
    normalized = _PUNCT_PATTERN.sub("", normalized)
    return normalized.strip()


def question_hash(text: str, version: int = HASH_VERSION) -> str:
    """稳定的 32 位十六进制哈希（sha256 截断），带规范化规则版本。"""
    normalized = normalize_question(text)
    payload = f"v{version}:{normalized}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 相似度
# ---------------------------------------------------------------------------


def edit_distance_ratio(a: str, b: str) -> float:
    """编辑距离相似度（difflib 口径：2*M/(len_a+len_b)）。"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def bigram_dice(a: str, b: str) -> float:
    """二元字符组 Dice 系数（中文短句更稳）。"""
    if not a and not b:
        return 1.0
    if len(a) < 2 or len(b) < 2:
        return edit_distance_ratio(a, b)
    grams_a = {a[i : i + 2] for i in range(len(a) - 1)}
    grams_b = {b[i : i + 2] for i in range(len(b) - 1)}
    if not grams_a or not grams_b:
        return 0.0
    return 2 * len(grams_a & grams_b) / (len(grams_a) + len(grams_b))


def query_terms(normalized: str, expand_synonyms: bool = False) -> set[str]:
    """抽取承载业务语义的关键词。

    做法：在规范化文本里找出命中的全部规范词（含变体写法），
    再加上未被任何规范词覆盖的连续中文片段（长度 ≥2）。

    :param expand_synonyms: True 时把命中的变体也展开成规范词（用于候选侧）
    """
    terms: set[str] = set()
    if not normalized:
        return terms

    # 变体必须按长度降序检查：否则「被执行」会先于「被执行人」命中，
    # 而 removeprefix 对更长的「被执行人」不成立，规范化文本反而匹配不到规范词。
    for canonical, variants in SYNONYMS.items():
        if canonical in normalized:
            terms.add(canonical)
        for variant in sorted(variants, key=len, reverse=True):
            cleaned = strip_punctuation(variant)
            if cleaned and cleaned in normalized:
                terms.add(canonical)
                break

    # 去掉已命中的规范词，剩下的中文片段同样算关键词（如「含金量」）
    residue = normalized
    for term in sorted(terms, key=len, reverse=True):
        residue = residue.replace(term, " ")

    for chunk in re.split(r"[^\u4e00-\u9fff]+", residue):
        if len(chunk) >= 2 and chunk not in _STOP_TERMS:
            terms.add(chunk)

    return {t for t in terms if t and t not in _STOP_TERMS}


def coverage_terms(question: str, candidate: str) -> tuple[int, int]:
    """返回 (命中的关键词数, 候选关键词总数)。"""
    candidate_terms = query_terms(normalize_question(candidate), expand_synonyms=True)
    if not candidate_terms:
        return 0, 0
    normalized_question = normalize_question(question)
    search_space = normalized_question
    for canonical, variants in SYNONYMS.items():
        for variant in sorted(variants, key=len, reverse=True):
            cleaned = strip_punctuation(variant)
            if cleaned and cleaned in normalized_question:
                search_space += " " + canonical
                break
    hit = sum(1 for term in candidate_terms if term in search_space)
    return hit, len(candidate_terms)


def keyword_coverage(question: str, candidate: str) -> float:
    """候选问题的关键词有多大比例出现在用户问法里（0~1）。"""
    hit, total = coverage_terms(question, candidate)
    return hit / total if total else 0.0


def keyword_match(question: str, candidate: str) -> bool:
    """关键词覆盖是否达标 —— 与「编辑距离阈值」并列的一条独立命中通道。

    为什么需要它：中文同义改写经归一后字面差异仍然很大
    （「营业收入怎么样」→「营收如何」vs 模板「该公司的营收如何」字符相似度仅 0.5），
    单靠编辑距离会大量漏命中。覆盖率达标即视为问的是同一件事。

    为防止「用户问法夹带大量无关内容」被误命中，要求归一化长度比不超过 1.6。
    """
    if keyword_coverage(question, candidate) < KEYWORD_COVERAGE_THRESHOLD:
        return False
    length_ratio = len(normalize_question(question)) / max(len(normalize_question(candidate)), 1)
    return length_ratio <= MAX_KEYWORD_LENGTH_RATIO


def similarity(question: str, candidate: str) -> float:
    """字面相似度 = max(编辑距离, bigram Dice)，子串包含只作上限约束。

    ⚠️ 刻意不做「子串包含就给高分」的捷径：像「整体风险」与「营收」这类
    同域不同问题会被子串规则误判成高相似（本文件自检抓到了这个 bug）。
    """
    a = normalize_question(question)
    b = normalize_question(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    score = max(edit_distance_ratio(a, b), bigram_dice(a, b))
    if a in b or b in a:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        score = min(score, max(0.5, len(shorter) / len(longer)))
    return score


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchCandidate:
    """待匹配的候选问题（来自 answer_cache 的 question_text）。"""

    key: str
    text: str
    payload: object = None


@dataclass(frozen=True)
class MatchResult:
    """匹配结果。"""

    matched: bool
    key: str | None = None
    text: str | None = None
    score: float = 0.0
    payload: object = None
    #: 精确哈希命中（最快的路径）
    exact: bool = False
    #: 关键词覆盖达标命中（中文同义改写的主通道）
    keyword: bool = False
    #: 命中但仅达到兜底相似度（调用方必须标注数据时效说明）
    fallback: bool = False
    threshold: float = DEFAULT_THRESHOLD

    @property
    def note(self) -> str:
        """给 answer 文案用的匹配说明。"""
        if self.exact:
            return "命中预生成答案（问题与模板完全一致）"
        if self.keyword:
            return f"按关键词覆盖命中预生成答案（字面相似度 {self.score:.2f}）"
        if self.fallback:
            return f"按相似度 {self.score:.2f} 命中近似问题（低于严格阈值 {self.threshold}）"
        return f"按相似度 {self.score:.2f} 命中预生成答案"


def best_match(
    question: str,
    candidates: Iterable[MatchCandidate | tuple[str, str]],
    threshold: float = DEFAULT_THRESHOLD,
    fallback_floor: float = MATCH_FALLBACK_FLOOR,
) -> MatchResult:
    """在候选集里找最相似的问题。

    命中优先级（三条独立通道）：
      1. 规范化后 question_hash 精确相等（exact=True）；
      2. 关键词覆盖达标（keyword=True）—— 中文同义改写的主要通道；
      3. 字面相似度 ≥ threshold（严格档）/ ≥ fallback_floor（兜底档，fallback=True）。
    """
    normalized = normalize_question(question)
    if not normalized:
        return MatchResult(False, threshold=threshold)

    target_hash = question_hash(question)
    best: MatchResult = MatchResult(False, threshold=threshold)

    for item in candidates:
        candidate = item if isinstance(item, MatchCandidate) else MatchCandidate(key=item[0], text=item[1])
        if not candidate.text:
            continue

        # 通道 1：精确哈希（最快）
        if question_hash(candidate.text) == target_hash:
            return MatchResult(
                matched=True,
                key=candidate.key,
                text=candidate.text,
                score=1.0,
                payload=candidate.payload,
                exact=True,
                threshold=threshold,
            )

        # 通道 2：关键词覆盖达标
        if keyword_match(question, candidate.text):
            return MatchResult(
                matched=True,
                key=candidate.key,
                text=candidate.text,
                score=max(similarity(question, candidate.text), KEYWORD_HIT_SCORE),
                payload=candidate.payload,
                keyword=True,
                threshold=threshold,
            )

        # 通道 3：字面相似度
        score = similarity(question, candidate.text)
        if score > best.score:
            best = MatchResult(False, candidate.key, candidate.text, score, candidate.payload, threshold=threshold)

    if best.score >= threshold:
        return MatchResult(
            matched=True, key=best.key, text=best.text, score=best.score,
            payload=best.payload, threshold=threshold,
        )
    # 兜底档额外要求「至少命中一个业务关键词」：否则像「整体风险」vs「营收」
    # 这种同域不同问题的字面分也会过线（自检抓到的误命中）。
    if best.score >= fallback_floor and best.text and coverage_terms(question, best.text)[0] > 0:
        return MatchResult(
            matched=True, key=best.key, text=best.text, score=best.score,
            payload=best.payload, fallback=True, threshold=threshold,
        )
    return MatchResult(False, threshold=threshold)


def rank_matches(
    question: str,
    candidates: Sequence[MatchCandidate | tuple[str, str]],
    limit: int = 5,
) -> list[tuple[str, str, float]]:
    """按相似度排序（诊断/调试用）。"""
    rows: list[tuple[str, str, float]] = []
    for item in candidates:
        candidate = item if isinstance(item, MatchCandidate) else MatchCandidate(key=item[0], text=item[1])
        rows.append((candidate.key, candidate.text, similarity(question, candidate.text)))
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows[:limit]


def is_same_question(a: str, b: str, threshold: float = DEFAULT_THRESHOLD) -> bool:
    """便捷判断（测试用）：三条通道任一命中即算同一问题。"""
    if question_hash(a) == question_hash(b):
        return True
    if keyword_match(a, b) or keyword_match(b, a):
        return True
    return similarity(a, b) >= threshold


__all__ = [
    "DEFAULT_THRESHOLD",
    "HASH_VERSION",
    "MATCH_FALLBACK_FLOOR",
    "SYNONYMS",
    "MatchCandidate",
    "MatchResult",
    "best_match",
    "bigram_dice",
    "edit_distance_ratio",
    "is_same_question",
    "normalize_question",
    "question_hash",
    "rank_matches",
    "replace_synonyms",
    "similarity",
    "strip_punctuation",
]

if __name__ == "__main__":  # pragma: no cover - 手动自检
    TEMPLATES = (
        "该公司的营收情况如何？",
        "该公司的利润含金量如何？",
        "该公司的现金流是否健康？",
        "该公司的司法风险如何？",
        "该公司的整体风险如何？",
    )
    # (用户问法, 期望命中的模板下标 or None)
    cases = [
        ("该公司的营收情况如何？", 0),
        ("营业收入怎么样", 0),
        ("公司的收入情况如何", 0),
        ("营收多少", 0),
        ("该公司的利润含金量如何？", 1),
        ("净利润怎么样", 1),
        ("现金流健康吗", 2),
        ("有没有诉讼", 3),
        ("被执行情况怎么样", 3),
        ("该公司的整体风险如何？", 4),
        # 必须**不**命中：同域但问的不是同一件事 / 完全无关
        ("帮我看看这家公司的社保参保人数", None),
        ("该公司的营收情况如何？", None),
        ("今天天气不错", None),
    ]
    ok = 0
    total = 0
    for q, expect_idx in cases:
        template = TEMPLATES[expect_idx] if expect_idx is not None else TEMPLATES[3]
        result = best_match(q, [MatchCandidate("C", template)])
        total += 1
        bad = result.matched != (expect_idx is not None)
        ok += not bad
        channel = "exact" if result.exact else "keyword" if result.keyword else "fallback" if result.fallback else "none"
        label = f"模板{expect_idx}" if expect_idx is not None else "应不命中"
        print(f"[{'BAD' if bad else 'OK '}] {result.score:.3f} {channel:8s} {q!r} → {label}")

    negatives = [
        ("该公司的营收情况如何？", "该公司的司法风险如何？"),
        ("该公司的整体风险如何？", "该公司的营收情况如何？"),
        ("今天天气不错", "该公司的营收情况如何？"),
        ("帮我看看这家公司的社保参保人数", "该公司的营收情况如何？"),
    ]
    for q, template in negatives:
        result = best_match(q, [MatchCandidate("C", template)])
        total += 1
        bad = result.matched
        ok += not bad
        channel = "exact" if result.exact else "keyword" if result.keyword else "fallback" if result.fallback else "none"
        print(f"[{'BAD' if bad else 'OK '}] {result.score:.3f} {channel:8s} 应不命中: {q!r} vs {template!r}")

    print(f"\n判定正确 {ok}/{total}")

    # 坏词检查：规范化结果里不应出现叠字伪影
    artifacts = ["流流", "润润", "收收", "入入", "净净", "社保社保"]
    hits = [
        (q, normalize_question(q))
        for q, _e in cases
        if any(a in normalize_question(q) for a in artifacts)
    ]
    print("叠字伪影:", hits if hits else "无")
