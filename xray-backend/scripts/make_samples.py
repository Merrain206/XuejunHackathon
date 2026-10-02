"""生成响应样例，供前端联调直接使用。

产物（默认写到 xray-backend/samples/）：
    sample_structure.json       —— 收入结构
    sample_profitability.json   —— 盈利质量
    sample_risk.json            —— 主要风险
    sample_insufficient.json    —— 证据不足（固定兜底）

加了 `--dynamic` 后，额外为四家公司生成**动态 Evidence-first 问答**样例：
    dynamic_{code}_revenue.json      —— 最近营业收入和归母净利润表现如何？
    dynamic_{code}_cashflow.json     —— 经营现金流表现如何？
    dynamic_{code}_fruit.json        —— 你的员工喜欢吃水果吗？（固定兜底）

样例**不是手写的**：这里真的调用一次应用（FastAPI TestClient，走完整路由 +
校验 + 兜底链路），把响应原样落盘。因此样例与线上行为不会漂移。

⚠️ `--dynamic` 会用一个**确定性假 LLM**（它只看候选证据就能写出合法 JSON）。
   为什么不用真实模型：样例要能复现、能进版本库、不能让评审看到随机内容；
   真实模型的成功率与耗时记录在 README 的「真实模型冒烟」一节，
   受控跑法见 `scripts/smoke_dynamic.py`。

用法：
    python scripts/make_samples.py                     # 三条稳定问题（688583）
    python scripts/make_samples.py --code 688583
    python scripts/make_samples.py --dynamic           # 额外生成四家公司动态样例
    python scripts/make_samples.py --out docs/samples
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 文件名 → (问题, 中文说明)
SAMPLES: tuple[tuple[str, str, str], ...] = (
    ("sample_structure.json", "你的收入结构发生了什么变化？", "收入结构"),
    ("sample_profitability.json", "你最近真的赚钱吗？", "盈利质量"),
    ("sample_risk.json", "目前最值得关注的风险是什么？", "主要风险"),
    ("sample_insufficient.json", "你的员工喜欢吃水果吗？", "证据不足（固定兜底）"),
)

#: 动态问答样例：公司 × (文件名后缀, 问题, 说明)
DYNAMIC_COMPANIES: tuple[str, ...] = ("688583", "600570", "000066", "300558")
DYNAMIC_SAMPLES: tuple[tuple[str, str, str], ...] = (
    ("revenue", "最近营业收入和归母净利润表现如何？", "动态：营收与归母净利润"),
    ("cashflow", "经营现金流表现如何？", "动态：经营现金流"),
    ("fruit", "你的员工喜欢吃水果吗？", "动态：证据不足（固定兜底）"),
)

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _ideal_llm(question, evidence=None, **kwargs):
    """「一个完全听话的模型」：只按候选证据写合法 JSON。

    它引用的数字全部来自候选摘录，因此生成的样例能展示**真实检索结果**，
    同时内容完全确定、可复现。
    """
    from llm_client import LLMResult

    cands = list(evidence or [])
    fallback = json.dumps(
        {"answer": "根据目前掌握的信息，我无法可靠回答这个问题。", "claims": [], "signals": []},
        ensure_ascii=False,
    )
    if not cands:
        return LLMResult(text=fallback, ok=True, source="sample", model="ideal")

    claims: list[dict] = []
    for index, item in enumerate(cands[:3], start=1):
        lead = (_NUMBER.findall(item.get("source_quote") or "") or [""])[0]
        if not lead:
            continue
        claims.append(
            {
                "id": f"CL-DYN-{index:03d}",
                "text": f"{item.get('period')}该项指标为 {lead}。",
                "evidence_ids": [item["id"]],
            }
        )
    if not claims:
        return LLMResult(text=fallback, ok=True, source="sample", model="ideal")

    lead = (_NUMBER.findall(cands[0].get("source_quote") or "") or [""])[0]
    return LLMResult(
        text=json.dumps(
            {
                "answer": (
                    f"根据公开披露数据，最近报告期该项指标为 {lead}，"
                    "与对比期相比存在变化，具体见所引用证据。"
                    "以上均为公司公开披露信息，不构成投资建议。"
                ),
                "claims": claims,
                "signals": [
                    {
                        "id": "SIG-DYN-001",
                        "type": "trend",
                        "title": "指标存在变化",
                        "severity": "attention",
                        "description": f"最近报告期该项指标为 {lead}，与对比期不同。",
                        "evidence_ids": [claims[0]["evidence_ids"][0]],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        ok=True,
        source="sample",
        model="ideal",
    )


def _write_json(path: Path, payload) -> None:
    """落盘 JSON，**显式使用 LF**。

    ⚠️ Windows 上 `Path.write_text` 会把 `\\n` 转成 CRLF，而仓库的
       `.gitattributes` 规定 `* text=auto eol=lf` —— 于是每次生成样例都会让
       `git status` 把内容完全没变的文件标成 modified（坑过一次：
       样例没改，却显示 4 个文件被修改）。写死 newline="\\n" 之后样例是幂等的。
    """
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _emit(client, code: str, question: str, filename: str, label: str, out_dir: Path):
    """打一次接口并把响应原样落盘；返回 index 条目（失败返回 None）。"""
    resp = client.post(f"/companies/{code}/ask", json={"question": question})
    if resp.status_code != 200:
        print(f"[FAIL] {code} {label}: HTTP {resp.status_code} {resp.text[:200]}")
        return None
    payload = resp.json()
    _write_json(out_dir / filename, payload)
    entry = {
        "file": filename,
        "company": code,
        "question": question,
        "label": label,
        "source": resp.headers.get("X-XRay-Cache", ""),
        "claims": len(payload.get("claims", [])),
        "signals": len(payload.get("signals", [])),
        "charts": len(payload.get("charts", [])),
        "evidence": len(payload.get("evidence", [])),
    }
    print(
        f"[OK] {code} {label:<22} {filename:<34} "
        f"source={entry['source']:<14} claims={entry['claims']} "
        f"signals={entry['signals']} charts={entry['charts']} evidence={entry['evidence']}"
    )
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="生成响应样例 JSON")
    parser.add_argument("--code", default="688583", help="公司代码（默认 688583 思看科技）")
    parser.add_argument("--out", default="samples", help="输出目录（相对 xray-backend/）")
    parser.add_argument(
        "--dynamic",
        action="store_true",
        help="额外生成四家公司的动态 Evidence-first 问答样例（用确定性假 LLM）",
    )
    args = parser.parse_args()

    from fastapi.testclient import TestClient

    import main

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    index: list[dict[str, str]] = []
    failed = 0

    client = TestClient(main.app)
    for filename, question, label in SAMPLES:
        entry = _emit(client, args.code, question, filename, label, out_dir)
        if entry is None:
            failed += 1
        else:
            index.append(entry)

    dynamic_index: list[dict[str, str]] = []
    if args.dynamic:
        import dynamic_qa

        dynamic_qa.generate_answer = _ideal_llm  # 注入确定性假模型
        print("\n--- 动态 Evidence-first 问答样例（确定性假 LLM）---")
        for code in DYNAMIC_COMPANIES:
            for suffix, question, label in DYNAMIC_SAMPLES:
                filename = f"dynamic_{code}_{suffix}.json"
                entry = _emit(client, code, question, filename, label, out_dir)
                if entry is None:
                    failed += 1
                else:
                    dynamic_index.append(entry)

    _write_json(
        out_dir / "index.json",
        {
            "company": args.code,
            "note": "由 scripts/make_samples.py 自动生成，勿手改；改了请重跑生成脚本。",
            "samples": index,
            "dynamic_samples": dynamic_index,
            "dynamic_note": (
                "dynamic_* 样例由确定性假 LLM 生成（内容可复现）；"
                "真实模型成功率与耗时见 xray-backend/README.md。"
            ),
        },
    )
    print(f"\n输出目录：{out_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
