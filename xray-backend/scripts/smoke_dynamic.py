"""临时冒烟脚本：用假 LLM 跑通动态问答全链路（真实库）。

不入库、不提交，只用来在写正式测试前暴露问题。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from llm_client import LLMResult  # noqa: E402

QUOTED = re.compile(r"(\d[\d,]*(?:\.\d+)?)")


def fake_llm(question, evidence=None, **kwargs):
    """按候选证据造一个"像模型"的 JSON 回答：引用第一条证据里的数字。"""
    cands = list(evidence or [])
    if not cands:
        return LLMResult(
            text=json.dumps(
                {"answer": "根据目前掌握的信息，我无法可靠地回答这个问题。", "claims": [], "signals": []},
                ensure_ascii=False,
            ),
            ok=True,
            source="fake",
            model="fake",
        )
    first = cands[0]
    second = cands[1] if len(cands) > 1 else cands[0]
    nums = QUOTED.findall(first.get("source_quote") or "")
    lead = nums[0] if nums else "本期数据"
    claims = [
        {
            "id": "CL-DYN-001",
            "text": f"{first.get('period')}的该项指标为 {lead}。",
            "evidence_ids": [first["id"]],
        },
        {
            "id": "CL-DYN-002",
            "text": f"另一组数据见 {second.get('period')}：{QUOTED.findall(second.get('source_quote') or '')[0]}。",
            "evidence_ids": [second["id"]],
        },
    ]
    signals = [
        {
            "id": "SIG-DYN-001",
            "type": "trend",
            "title": "指标存在变化",
            "severity": "attention",
            "description": f"{first.get('period')}为 {lead}，与对比期不同。",
            "evidence_ids": [first["id"], second["id"]],
        }
    ]
    answer = (
        f"根据公开披露数据，最近报告期该项指标为 {lead}。"
        f"与对比期相比存在变化，具体见所引用证据。以上仅为公开信息摘录。"
    )
    return LLMResult(
        text=json.dumps({"answer": answer, "claims": claims, "signals": signals}, ensure_ascii=False),
        ok=True,
        source="fake",
        model="fake",
        elapsed_ms=1.0,
    )


def main() -> int:
    import ask
    import dynamic_qa

    questions = [
        "最近营业收入和归母净利润表现如何？",
        "经营现金流表现如何？",
        "近几个报告期的盈利趋势是什么？",
        "你的员工喜欢吃水果吗？",
        "完全无关的问题",
    ]
    failures: list[str] = []
    # 离线段：用假 LLM 验证链路与机械校验（不联网）
    for code in ("688583", "600570", "000066", "300558"):
        print("=" * 90)
        print("公司", code)
        for question in questions:
            payload, source = dynamic_qa.build_dynamic_response(code, question, llm=fake_llm)
            evidence = payload["evidence"]
            print(f"  Q: {question}  source={source} claims={len(payload['claims'])} "
                  f"signals={len(payload['signals'])} evidence={len(evidence)} "
                  f"charts={len(payload['charts'])}")
            for ev in evidence:
                mark = "OK " if ev.get("verification_status") in ("verified", "auto") else "!! "
                if "#" in ev["source_url"]:
                    failures.append(f"{code} {ev['id']} URL 带 fragment")
                print(f"      {mark}{ev['id']} p{ev['source_page']} {ev['verification_status']} "
                      f"{ev['source_quote'][:60]!r}")
            if question in ("你的员工喜欢吃水果吗？", "完全无关的问题"):
                if any(payload[k] for k in ("claims", "signals", "charts", "evidence")):
                    failures.append(f"{code} 水果/无关问题没有返回空数组：{question}")
                if source != dynamic_qa.SOURCE_INSUFFICIENT:
                    failures.append(f"{code} 水果/无关问题走了动态链路：{question}")

    # 直接打接口（走完整 FastAPI 路径 + 响应校验）
    print("=" * 90)
    import main
    import time as _time
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        for code in ("688583", "600570", "000066", "300558"):
            for question in questions:
                started = _time.perf_counter()
                resp = client.post(f"/companies/{code}/ask", json={"question": question})
                elapsed = (_time.perf_counter() - started) * 1000
                body = resp.json()
                header = resp.headers.get("X-XRay-Cache")
                print(f"  HTTP {code} {question[:22]} -> {resp.status_code} [{header}] "
                      f"{elapsed:6.0f}ms claims={len(body.get('claims', []))} "
                      f"ev={len(body.get('evidence', []))} q={len(body.get('suggested_questions', []))}")
                if resp.status_code != 200:
                    failures.append(f"{code} {question} HTTP {resp.status_code}")
                if question in ("你的员工喜欢吃水果吗？", "完全无关的问题"):
                    if any(body.get(k) for k in ("claims", "signals", "charts", "evidence")):
                        failures.append(f"{code} 水果/无关问题没有返回空数组：{question}")
                if question in questions[:3] and code != "688583":
                    if header != "dynamic-llm":
                        failures.append(f"{code} 动态问题未走动态链路：{question} [{header}]")

    print("=" * 90)
    if failures:
        print("FAILURES:")
        for item in failures:
            print("  -", item)
        return 1
    print("ALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
