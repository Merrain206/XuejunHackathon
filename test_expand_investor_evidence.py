from __future__ import annotations

import unittest

from expand_investor_evidence import MAX_QUOTE_CHARS, exact_quote, split_pages


class InvestorEvidenceExtractionTests(unittest.TestCase):
    def test_split_pages_preserves_viewer_page_numbers(self):
        text = "--- 第1页 ---\n第一页\n--- 第2页 ---\n第二页内容"
        self.assertEqual(split_pages(text), [(1, "第一页"), (2, "第二页内容")])

    def test_quote_is_contiguous_and_bounded(self):
        page = "前文。公司董事会由九名董事组成，其中独立董事三名并依法履职。后文。"
        quote, _ = exact_quote(page, "董事会由") or ("", 0)
        self.assertIn(quote, page)
        self.assertIn("九名董事", quote)
        self.assertLessEqual(len(quote), MAX_QUOTE_CHARS)

    def test_long_sentence_stays_exact(self):
        page = "开头。" + "甲" * 120 + "关键审计事项" + "乙" * 120 + "。结尾。"
        quote, _ = exact_quote(page, "关键审计事项") or ("", 0)
        self.assertIn(quote, page)
        self.assertIn("关键审计事项", quote)
        self.assertLessEqual(len(quote), MAX_QUOTE_CHARS)

    def test_short_heading_includes_following_table_rows(self):
        page = "（四）劳务外包情况\n适用\n劳务外包的工时总数 7302\n劳务外包支付的报酬总额 260.49 万元\n下一节。"
        quote, _ = exact_quote(page, "劳务外包") or ("", 0)
        self.assertIn(quote, page)
        self.assertIn("7302", quote)
        self.assertIn("260.49", quote)


if __name__ == "__main__":
    unittest.main()
