# -*- coding: utf-8 -*-
"""evidence_verify 与 cninfo_queries 的回归测试。

这些用例锁住的是**核验口径**本身 —— 口径一旦被放松，抽检就会给出假通过。
运行：

    python -m pytest test_evidence_verify.py -q
    python test_evidence_verify.py            # 无 pytest 时也能跑

重点覆盖两类曾经真实踩过的坑：

1. **数值比对被 PDF 表格抽取破坏**
   - 相邻单元格（`332,583,883.61` + `271,707,663.51`）压掉空白后会粘成一个数字，
     导致后一个数字永远匹配不上；
   - 区间短横（`33,000-35,000`）被当成负号，`35,000` 被读成 `-35000`；
   - 千分位/百分号/小数位写法不一致（`22.41` vs `22.41%`、`0.60` vs `0.6`）。

2. **不能把"找不到"降级成"通过"**
   上一版抽检脚本在引文完全找不到时仍然判 auto，并给出 100% 通过率。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evidence_verify import (  # noqa: E402
    extract_contiguous_span,
    extract_pages,
    is_contiguous_quote,
    is_placeholder,
    locate_quote,
    locate_segment,
    normalize,
    page_findings,
    segment_hits,
    split_segments,
    summarize,
    verify_record,
)
from cninfo_queries import (  # noqa: E402
    QueryError,
    latest_evidence,
    period_rank,
    recent_periods,
    search_chunks,
)
from repair_evidence_quotes import (  # noqa: E402
    MAX_REPAIRED_QUOTE_CHARS,
    _extra_content,
)

#: 真实踩过的坑：第 10 页既有 `332,583,883.61`，也有 `271,707,663.51` 等
INTERLEAVED_PAGE = """思看科技（杭州）股份有限公司2024 年年度报告
10/283
主要会计数据
2024 年
2023 年
本期比上
年同期增
减(%)
2022 年
营业收入
332,583,883.61
271,707,663.51
22.41
206,024,686.41
归属于上市公司股东的净利润
120,527,578.92
114,254,997.25
5.49
77,634,952.88
经营活动产生的现金流量净额
99,579,594.23
118,031,786.22
-15.63
92,380,644.81
研发投入占营业收入的比例（%）
17.76
17.78
减少0.02 个百分点
17.82
"""

#: 招股书预测区间写法
RANGE_PAGE = """单位：万元
2024年度 2023年度
项目 变动率
（未经审计或审阅） （经审计）
营业收入 33,000-35,000 27,170.77 21.45%-28.81%
归属于母公司股东的净利润 12,000-13,000 11,425.50 5.03%-13.78%
"""

#: 行标签被 PDF 拆到两行、中间夹着同行其它单元格
SPLIT_LABEL_PAGE = """总资产 745,453,503.55 578,248,398.57 28.92 455,843,820.77
归属于上市公司股 391,251,319.82
624,684,613.64 490,451,882.67 27.37
东的净资产
"""


class TestNormalizeAndPages(unittest.TestCase):
    def test_normalize_strips_whitespace(self):
        self.assertEqual(normalize("  营业\n收入\t 1 "), "营业收入1")
        self.assertEqual(normalize(None), "")

    def test_extract_pages_splits_on_markers(self):
        text = "封面\n--- 第1页 ---\n第一页正文\n--- 第2页 ---\n第二页正文"
        pages = extract_pages(text)
        self.assertEqual(sorted(pages), [1, 2])
        self.assertEqual(pages[1], "第一页正文")
        self.assertEqual(pages[2], "第二页正文")
        # 页界标记本身不属于任何一页
        self.assertNotIn("第1页", pages[1])

    def test_extract_pages_without_markers_is_empty(self):
        self.assertEqual(extract_pages("没有页标记的正文"), {})

    def test_split_segments_drops_empty(self):
        self.assertEqual(
            split_segments("营业收入 | 185,101,612.27 |  | 4.67"),
            ["营业收入", "185,101,612.27", "4.67"])
        self.assertEqual(split_segments(None), [])


class TestNumericMatching(unittest.TestCase):
    """数值比对：既要能认出等价写法，又不能误命中别的数。"""

    def test_thousands_separator_forms_are_equivalent(self):
        self.assertTrue(segment_hits("332,583,883.61", INTERLEAVED_PAGE))
        self.assertTrue(segment_hits("332583883.61", INTERLEAVED_PAGE))

    def test_adjacent_table_cells_do_not_fuse(self):
        """回归：压掉空白会让相邻单元格粘成一个数字，后一个数就永远找不到。"""
        for value in ("332,583,883.61", "271,707,663.51", "206,024,686.41"):
            self.assertTrue(segment_hits(value, INTERLEAVED_PAGE), value)

    def test_percent_and_plain_forms_are_equivalent(self):
        self.assertTrue(segment_hits("22.41%", INTERLEAVED_PAGE))
        self.assertTrue(segment_hits("22.41", INTERLEAVED_PAGE))
        self.assertTrue(segment_hits("17.76%", INTERLEAVED_PAGE))

    def test_trailing_zero_forms_are_equivalent(self):
        self.assertTrue(segment_hits("0.60", "基本每股收益 0.6 元"))
        self.assertTrue(segment_hits("0.6", "基本每股收益 0.60 元"))

    def test_negative_numbers(self):
        self.assertTrue(segment_hits("-15.63", INTERLEAVED_PAGE))
        self.assertTrue(segment_hits("-18,209,215.68", "净额 -18,209,215.68 元"))

    def test_partial_number_must_not_match(self):
        """`4.67` 不能命中 `1304.67`，`0.39` 不能命中 `30.39`。"""
        self.assertFalse(segment_hits("4.67", "合计 1304.67 元"))
        self.assertFalse(segment_hits("0.39", "比例 30.39 %"))
        self.assertFalse(segment_hits("101", "金额 185,101,612.27 元"))


class TestRangeMatching(unittest.TestCase):
    """招股书预测区间：整段命中，或两个端点都命中。"""

    def test_whole_range_present(self):
        self.assertTrue(segment_hits("33,000-35,000", RANGE_PAGE))

    def test_range_endpoints_split_across_lines(self):
        page = "营业收入\n33,000\n35,000\n"
        self.assertTrue(segment_hits("33,000-35,000", page))

    def test_range_missing_one_endpoint_fails(self):
        """只有一个端点在页面上，不算命中整个区间。"""
        self.assertFalse(segment_hits("33,000-35,000", "营业收入 33,000 万元"))

    def test_percent_range_present(self):
        self.assertTrue(segment_hits("21.45%-28.81%", RANGE_PAGE))
        self.assertTrue(segment_hits("5.03%-13.78%", RANGE_PAGE))

    def test_range_dash_is_not_a_minus_sign(self):
        """回归：`33,000-35,000` 的短横不是负号，`35,000` 不能被读成 `-35000`。"""
        self.assertTrue(segment_hits("35,000", RANGE_PAGE))


class TestTextSegmentMatching(unittest.TestCase):
    def test_label_split_across_lines_matches(self):
        """回归：行标签被拆成两行、中间夹着其它单元格。"""
        self.assertTrue(segment_hits("归属于上市公司股东的净资产", SPLIT_LABEL_PAGE))

    def test_absent_label_does_not_match(self):
        self.assertFalse(segment_hits("基本每股收益（元／股）", SPLIT_LABEL_PAGE))

    def test_short_garbage_label_does_not_match(self):
        """短标签不启用子序列匹配，避免单字假命中。"""
        self.assertFalse(segment_hits("毛利", "管理费用 1,234.00 元"))

    def test_placeholder_detection(self):
        for token in ("-", "--", "—", "不适用", "无"):
            self.assertTrue(is_placeholder(token), token)
        self.assertFalse(is_placeholder("营业收入"))


class TestPageFindings(unittest.TestCase):
    def test_placeholders_are_not_required_content(self):
        findings = page_findings("营业收入 | 185,101,612.27 | -", "营业收入 185,101,612.27")
        self.assertTrue(findings["segments_ok"])
        self.assertEqual(findings["missed"], [])
        self.assertIn("-", findings["segments"])

    def test_missing_content_segment_fails(self):
        findings = page_findings("研发投入 | 17.76 | 17.82", "营业收入 332,583,883.61")
        self.assertFalse(findings["segments_ok"])
        self.assertIn("研发投入", findings["missed"])

    def test_verbatim_detection(self):
        quote = "归属于上市公司股东的净利润 14,121,534.22"
        page = "主要会计数据\n归属于上市公司股东的净利润 14,121,534.22\n"
        findings = page_findings(quote, page)
        self.assertTrue(findings["verbatim"])

    def test_table_row_quote_is_not_verbatim(self):
        findings = page_findings("营业收入 | 332,583,883.61", INTERLEAVED_PAGE)
        self.assertFalse(findings["verbatim"])
        self.assertTrue(findings["segments_ok"])

    def test_locate_quote_reports_real_page(self):
        pages = {
            1: "无关内容",
            2: "营业收入 332,583,883.61 元",
            3: "别的页",
        }
        self.assertEqual(locate_quote("营业收入332,583,883.61元", pages), [2])
        self.assertEqual(locate_quote("不存在的引文", pages), [])


def _doc(**overrides):
    base = {
        "company_code": "688583",
        "superseded": 0,
        "parse_status": "ok",
        "page_count": 3,
    }
    base.update(overrides)
    return base


class TestVerifyRecord(unittest.TestCase):
    """verify_record 的判定必须严格：找不到就是找不到，不许降级。"""

    def _pages(self):
        return {
            2: ("主要会计数据\n营业收入 332,583,883.61 271,707,663.51 22.41\n"
                "归属于上市公司股东的净利润 120,527,578.92\n"),
        }

    def test_all_checks_pass_gives_auto_not_verified(self):
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=332583883.61,
            unit="元", period="2024FY", source_page=2,
            source_quote="营业收入 | 332,583,883.61 | 271,707,663.51 | 22.41",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="auto",
            document=_doc(), pages=self._pages())
        self.assertEqual(result["status"], "auto")
        self.assertEqual(result["quote_state"], "segments")
        self.assertTrue(result["structural_ok"])

    def test_manual_review_keeps_verified(self):
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=332583883.61,
            unit="元", period="2024FY", source_page=2,
            source_quote="营业收入 | 332,583,883.61 | 271,707,663.51 | 22.41",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="verified",
            document=_doc(), pages=self._pages())
        self.assertEqual(result["status"], "verified")

    def test_auto_is_never_promoted_to_verified(self):
        """机械校验再干净也不能把 auto 变成 verified。"""
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=332583883.61,
            unit="元", period="2024FY", source_page=2,
            source_quote="营业收入 | 332,583,883.61",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="auto",
            document=_doc(), pages=self._pages())
        self.assertNotEqual(result["status"], "verified")

    def test_quote_not_on_page_is_rejected(self):
        """回归：引文在该页完全找不到时，绝不能判 auto/通过。"""
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=1.0,
            unit="元", period="2024FY", source_page=2,
            source_quote="研发投入占营业收入的比例 | 17.76 | 17.82",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="auto",
            document=_doc(), pages=self._pages())
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["quote_state"], "failed")
        self.assertFalse(result["structural_ok"] is False)  # 结构仍然合格
        self.assertTrue(any("找不到" in issue for issue in result["issues"]))

    def test_page_number_out_of_range_is_rejected(self):
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=332583883.61,
            unit="元", period="2024FY", source_page=99,
            source_quote="营业收入 | 332,583,883.61",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="auto",
            document=_doc(), pages=self._pages())
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["page_ok"])

    def test_cross_company_document_is_rejected(self):
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=332583883.61,
            unit="元", period="2024FY", source_page=2,
            source_quote="营业收入 | 332,583,883.61",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="auto",
            document=_doc(company_code="600570"), pages=self._pages())
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["same_company"])

    def test_superseded_document_is_rejected(self):
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=332583883.61,
            unit="元", period="2024FY", source_page=2,
            source_quote="营业收入 | 332,583,883.61",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="auto",
            document=_doc(superseded=1), pages=self._pages())
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["document_active"])

    def test_url_with_fragment_is_rejected(self):
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=332583883.61,
            unit="元", period="2024FY", source_page=2,
            source_quote="营业收入 | 332,583,883.61",
            source_url="http://static.cninfo.com.cn/a.PDF#page=2", review_status="auto",
            document=_doc(), pages=self._pages())
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("#page=" in issue for issue in result["issues"]))

    def test_non_pdf_url_is_rejected(self):
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=332583883.61,
            unit="元", period="2024FY", source_page=2,
            source_quote="营业收入 | 332,583,883.61",
            source_url="http://static.cninfo.com.cn/announcement", review_status="auto",
            document=_doc(), pages=self._pages())
        self.assertEqual(result["status"], "rejected")

    def test_missing_period_is_rejected(self):
        """真实缺陷：规则抽取出的 3 条记录 period 为空。"""
        result = verify_record(
            company_code="688583", document_id=1, metric="net_profit", value=2024.0,
            unit="元", period=None, source_page=2,
            source_quote="归属于公司普通股股东的净利润 | 2024年1-6月 | 10.16%",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="auto",
            document=_doc(), pages=self._pages())
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["period_ok"])

    def test_qualitative_evidence_without_unit_is_allowed(self):
        """定性风险证据没有 unit 是正常的，不能因此判不能支撑回答。"""
        pages = {3: "如果公司未来产品技术优势减弱或消除，公司销售价格将受到不利影响"}
        result = verify_record(
            company_code="688583", document_id=1, metric="risk_tech_edge", value=None,
            unit=None, period="2025H1", source_page=3,
            source_quote="如果公司未来产品技术优势减弱或消除，公司销售价格将受到不利影响",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="verified",
            document=_doc(page_count=3), pages=pages)
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["unit_ok"])

    def test_numeric_evidence_without_unit_is_rejected(self):
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue",
            value=332583883.61, unit=None, period="2024FY", source_page=2,
            source_quote="营业收入 | 332,583,883.61",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="auto",
            document=_doc(), pages=self._pages())
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["unit_ok"])

    def test_value_not_in_quote_is_rejected(self):
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=999999.99,
            unit="元", period="2024FY", source_page=2,
            source_quote="营业收入 | 332,583,883.61",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="auto",
            document=_doc(), pages=self._pages())
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["value_ok"])

    def test_missing_page_text_is_rejected(self):
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=1.0,
            unit="元", period="2024FY", source_page=2,
            source_quote="营业收入 | 1.00",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="auto",
            document=_doc(), pages={1: "别的页"})
        self.assertEqual(result["status"], "rejected")

    def test_summarize_counts(self):
        good = {"status": "auto", "quote_state": "segments"}
        bad = {"status": "rejected", "quote_state": "failed"}
        counts = summarize([good, bad, good])
        self.assertEqual(counts["auto"], 2)
        self.assertEqual(counts["rejected"], 1)
        self.assertEqual(counts["segments"], 2)
        self.assertEqual(counts["failed"], 1)


class TestQuoteRepair(unittest.TestCase):
    """引文修复：把表格行拼接重写成页面上真实连续的原文。

    这是"后端严格核验能否通过"的关键 —— 修复后引文必须是标注页的连续子串，
    且覆盖原引文的全部内容片段，不引入原引文之外的数字。
    """

    #: 行列顺序与 PDF 抽取顺序一致的表格（可以修成连续原文）
    LINEAR_PAGE = (
        "六、近三年主要会计数据和财务指标\n"
        "主要会计数据\n"
        "营业收入\n"
        "332,583,883.61\n"
        "271,707,663.51\n"
        "22.41\n"
        "206,024,686.41\n"
        "归属于上市公司股东的净利润\n"
        "120,527,578.92\n"
    )

    #: 同页相邻行：修复时必须只框住目标行，不能把上一行的净利润卷进来
    NEIGHBOUR_PAGE = (
        "归属于上市公司股东的净利润\n"
        "120,527,578.92\n"
        "114,254,997.25\n"
        "5.49\n"
        "77,634,952.88\n"
        "归属于上市公司股东的扣除非\n"
        "经常性损益的净利润\n"
        "110,333,126.69\n"
        "98,943,724.69\n"
        "11.51\n"
        "71,234,032.75\n"
    )

    def test_locate_segment_finds_number(self):
        span = locate_segment(self.LINEAR_PAGE, "271,707,663.51")
        self.assertIsNotNone(span)
        self.assertEqual(self.LINEAR_PAGE[span[0]:span[1]], "271,707,663.51")

    def test_locate_segment_prefers_contiguous_over_subsequence(self):
        """回归：子序列匹配会把"归属于上市公司股东的净利润"吞进更长的标签里。"""
        span = locate_segment(self.NEIGHBOUR_PAGE, "归属于上市公司股东的净利润")
        self.assertIsNotNone(span)
        self.assertEqual(
            self.NEIGHBOUR_PAGE[span[0]:span[1]], "归属于上市公司股东的净利润")

    def test_locate_segment_respects_start_at(self):
        self.assertIsNone(
            locate_segment(self.LINEAR_PAGE, "营业收入", start_at=len(self.LINEAR_PAGE)))

    def test_extract_span_is_contiguous_page_text(self):
        quote = "营业收入 | 332,583,883.61 | 271,707,663.51 | 22.41 | 206,024,686.41"
        span = extract_contiguous_span(self.LINEAR_PAGE, quote)
        self.assertIsNotNone(span)
        self.assertTrue(is_contiguous_quote(span, self.LINEAR_PAGE))
        for segment in split_segments(quote):
            self.assertTrue(segment_hits(segment, span), segment)

    def test_extract_span_does_not_swallow_neighbour_row(self):
        """关键回归：修复后的引文不能包含上一行的净利润数字。"""
        quote = ("归属于上市公司股东的扣除非\n经常性损益的净利润 | 110,333,126.69 "
                 "| 98,943,724.69 | 11.51 | 71,234,032.75")
        span = extract_contiguous_span(self.NEIGHBOUR_PAGE, quote)
        self.assertIsNotNone(span)
        self.assertTrue(is_contiguous_quote(span, self.NEIGHBOUR_PAGE))
        self.assertNotIn("120,527,578.92", span)
        self.assertNotIn("114,254,997.25", span)

    def test_extra_content_compares_canonical_numbers(self):
        original = ["营业收入", "332,583,883.61", "271,707,663.51", "22.41"]
        same = "营业收入\n332,583,883.61\n271,707,663.51\n22.41"
        polluted = same + "\n归母净利润 120,527,578.92"
        self.assertEqual(_extra_content(same, original), [])
        self.assertEqual(_extra_content(polluted, original), ["120,527,578.92"])

    def test_evidence_3091_does_not_absorb_neighbouring_table_numbers(self):
        """真实回归：3091 的净利润摘录不能吞入营业收入、成本和税金数字。"""
        original = ["归属于上市公司股东的净利润", "52,840,640.11", "114,500,854.73"]
        polluted_span = (
            "营业收入 150,248,052.96\n营业成本 102,468,674.22\n"
            "税金及附加 1,698,358.08\n归属于上市公司股东的净利润 "
            "52,840,640.11 114,500,854.73"
        )
        extras = _extra_content(polluted_span, original)
        self.assertIn("150,248,052.96", extras)
        self.assertIn("102,468,674.22", extras)
        self.assertIn("1,698,358.08", extras)

    def test_quote_length_limit_matches_backend(self):
        self.assertEqual(MAX_REPAIRED_QUOTE_CHARS, 200)

    def test_extract_span_keeps_range_segment_intact(self):
        page = "营业收入 33,000-35,000 27,170.77 21.45%-28.81%\n"
        span = extract_contiguous_span(
            page, "营业收入 | 33,000-35,000 | 27,170.77 | 21.45%-28.81%")
        self.assertIsNotNone(span)
        self.assertIn("21.45%-28.81%", span)

    def test_extract_span_returns_none_when_segment_missing(self):
        self.assertIsNone(
            extract_contiguous_span(self.LINEAR_PAGE, "研发投入 | 17.76 | 17.82"))

    def test_extract_span_returns_none_for_empty_input(self):
        self.assertIsNone(extract_contiguous_span("", "营业收入 | 1"))
        self.assertIsNone(extract_contiguous_span(self.LINEAR_PAGE, ""))

    def test_is_contiguous_quote_ignores_whitespace(self):
        self.assertTrue(is_contiguous_quote("营业收入\n332,583,883.61", self.LINEAR_PAGE))
        self.assertFalse(is_contiguous_quote("营业收入 | 332,583,883.61", self.LINEAR_PAGE))

    def test_repaired_quote_satisfies_backend_strict_check(self):
        """修复后的引文必须能通过后端 page_from_markers 的口径。"""
        quote = "营业收入 | 332,583,883.61 | 271,707,663.51 | 22.41 | 206,024,686.41"
        span = extract_contiguous_span(self.LINEAR_PAGE, quote)
        page_text = f"--- 第10页 ---\n{self.LINEAR_PAGE}"
        self.assertEqual(locate_quote(span, {10: self.LINEAR_PAGE}), [10])
        self.assertIn(normalize(span), normalize(page_text))

    def test_verify_record_marks_repaired_quote_contiguous(self):
        quote = "营业收入 | 332,583,883.61 | 271,707,663.51"
        span = extract_contiguous_span(self.LINEAR_PAGE, quote)
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=332583883.61,
            unit="元", period="2024FY", source_page=10, source_quote=span,
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="auto",
            document=_doc(page_count=10), pages={10: self.LINEAR_PAGE})
        self.assertEqual(result["quote_state"], "verbatim")
        self.assertTrue(result["contiguous"])
        self.assertEqual(result["status"], "auto")


class TestExcludedEvidence(unittest.TestCase):
    """隔离记录一律判 excluded，不计入通过，也不参与回答。"""

    def test_excluded_record_is_not_counted_as_pass(self):
        result = verify_record(
            company_code="688583", document_id=1, metric="net_profit", value=2024.0,
            unit="元", period=None, source_page=3,
            source_quote="归属于公司普通股股东的净利润",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="auto",
            document=_doc(), pages={3: "归属于公司普通股股东的净利润"},
            excluded=True, excluded_reason="period 为空")
        self.assertEqual(result["status"], "excluded")
        self.assertEqual(result["quote_state"], "excluded")
        self.assertTrue(any("隔离" in issue for issue in result["issues"]))

    def test_excluded_record_is_never_verified(self):
        result = verify_record(
            company_code="688583", document_id=1, metric="revenue", value=None,
            unit=None, period="2024FY", source_page=3, source_quote="营业收入",
            source_url="http://static.cninfo.com.cn/a.PDF", review_status="verified",
            document=_doc(), pages={3: "营业收入"}, excluded=True)
        self.assertEqual(result["status"], "excluded")

    def test_summarize_counts_excluded(self):
        """`excluded` 会同时被 status 与 quote_state 计入 —— 两者含义一致，故为 2。"""
        counts = summarize([
            {"status": "excluded", "quote_state": "excluded"},
            {"status": "auto", "quote_state": "verbatim"},
        ])
        self.assertEqual(counts["excluded"], 2)
        self.assertEqual(counts["auto"], 1)
        self.assertEqual(counts["verbatim"], 1)


class TestPeriodRank(unittest.TestCase):
    def test_ordering_within_year(self):
        self.assertLess(period_rank("2025H1"), period_rank("2025FY"))
        self.assertLess(period_rank("2025Q1"), period_rank("2025H1"))
        self.assertLess(period_rank("2024FY"), period_rank("2025H1"))

    def test_bare_year_behaves_like_fy(self):
        self.assertEqual(period_rank("2025"), period_rank("2025FY"))

    def test_unparsable_ranks_lowest(self):
        self.assertEqual(period_rank(None), 0.0)
        self.assertEqual(period_rank(""), 0.0)
        self.assertEqual(period_rank("未知"), 0.0)

    def test_year_only_suffix(self):
        self.assertGreater(period_rank("2024年"), 0.0)


class TestQueryGuards(unittest.TestCase):
    """参数校验：不允许空公司、越界 limit、过短关键词。"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE evidence (id INTEGER PRIMARY KEY, company_code TEXT, document_id INT,"
            " metric TEXT, period TEXT, value REAL, unit TEXT, content TEXT, source_page INT,"
            " source_quote TEXT, method TEXT, review_status TEXT)")
        self.conn.execute(
            "CREATE TABLE docs (id INTEGER PRIMARY KEY, company_code TEXT, file_name TEXT,"
            " document_type TEXT, report_period TEXT, published_at TEXT, page_count INT,"
            " source_url TEXT, superseded INT DEFAULT 0, parse_status TEXT DEFAULT 'ok',"
            " text_content TEXT)")
        self.conn.execute(
            "CREATE TABLE chunks (id INTEGER PRIMARY KEY, document_id INT, company_code TEXT,"
            " page_number INT, chunk_index INT, content TEXT)")
        self.conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

    def tearDown(self):
        self.conn.close()

    def test_company_code_is_required(self):
        with self.assertRaises(QueryError):
            latest_evidence(self.conn, "", "revenue")
        with self.assertRaises(QueryError):
            search_chunks(self.conn, "  ", "营业收入")

    def test_short_keyword_is_rejected(self):
        with self.assertRaises(QueryError):
            search_chunks(self.conn, "688583", "营")

    def test_invalid_limit_is_rejected(self):
        with self.assertRaises(QueryError):
            latest_evidence(self.conn, "688583", "revenue", limit=0)
        with self.assertRaises(QueryError):
            latest_evidence(self.conn, "688583", "revenue", limit=-1)
        with self.assertRaises(QueryError):
            recent_periods(self.conn, "688583", ["revenue"], periods=0)

    def test_empty_metrics_is_rejected(self):
        with self.assertRaises(QueryError):
            recent_periods(self.conn, "688583", [])

    def test_queries_are_company_scoped(self):
        """公司隔离：别的公司的数据不能出现在结果里。"""
        self.conn.executemany(
            "INSERT INTO docs (id, company_code, file_name, source_url) VALUES (?,?,?,?)",
            [(1, "688583", "a.pdf", "http://x/a.PDF"),
             (2, "600570", "b.pdf", "http://x/b.PDF")])
        self.conn.executemany(
            "INSERT INTO evidence (id, company_code, document_id, metric, period, value, unit,"
            " source_page, source_quote, review_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(1, "688583", 1, "revenue", "2024FY", 1.0, "元", 1, "q", "auto"),
             (2, "600570", 2, "revenue", "2024FY", 2.0, "元", 1, "q", "auto")])
        rows = latest_evidence(self.conn, "688583", "revenue")
        self.assertEqual([r["id"] for r in rows], [1])

    def test_inactive_documents_are_filtered_out(self):
        self.conn.execute(
            "INSERT INTO docs (id, company_code, file_name, source_url, superseded, parse_status)"
            " VALUES (1,'688583','a.pdf','http://x/a.PDF',1,'ok')")
        self.conn.execute(
            "INSERT INTO evidence (id, company_code, document_id, metric, period, value, unit,"
            " source_page, source_quote, review_status) VALUES"
            " (1,'688583',1,'revenue','2024FY',1.0,'元',1,'q','auto')")
        self.assertEqual(latest_evidence(self.conn, "688583", "revenue"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
