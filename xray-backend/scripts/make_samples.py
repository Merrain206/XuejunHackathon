"""生成四个真实响应样例，供前端联调直接使用。

产物（默认写到 xray-backend/samples/）：
    sample_structure.json       —— 收入结构
    sample_profitability.json   —— 盈利质量
    sample_risk.json            —— 主要风险
    sample_insufficient.json    —— 证据不足（固定兜底）

样例**不是手写的**：这里真的调用一次应用（FastAPI TestClient，走完整路由 +
校验 + 兜底链路），把响应原样落盘。因此样例与线上行为不会漂移。

用法：
    python scripts/make_samples.py                # 用默认公司 688583
    python scripts/make_samples.py --code 688583
    python scripts/make_samples.py --out docs/samples
"""

from __future__ import annotations

import argparse
import json
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


def main() -> int:
    parser = argparse.ArgumentParser(description="生成四个响应样例 JSON")
    parser.add_argument("--code", default="688583", help="公司代码（默认 688583 思看科技）")
    parser.add_argument("--out", default="samples", help="输出目录（相对 xray-backend/）")
    args = parser.parse_args()

    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    index: list[dict[str, str]] = []
    failed = 0

    for filename, question, label in SAMPLES:
        resp = client.post(f"/companies/{args.code}/ask", json={"question": question})
        if resp.status_code != 200:
            print(f"[FAIL] {label}: HTTP {resp.status_code} {resp.text[:200]}")
            failed += 1
            continue

        payload = resp.json()
        path = out_dir / filename
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        index.append(
            {
                "file": filename,
                "question": question,
                "label": label,
                "source": resp.headers.get("X-XRay-Cache", ""),
                "claims": len(payload.get("claims", [])),
                "signals": len(payload.get("signals", [])),
                "charts": len(payload.get("charts", [])),
                "evidence": len(payload.get("evidence", [])),
            }
        )
        print(
            f"[OK] {label:<16} {filename:<28} "
            f"claims={len(payload.get('claims', []))} "
            f"signals={len(payload.get('signals', []))} "
            f"charts={len(payload.get('charts', []))} "
            f"evidence={len(payload.get('evidence', []))}"
        )

    (out_dir / "index.json").write_text(
        json.dumps(
            {
                "company": args.code,
                "note": "由 scripts/make_samples.py 自动生成，勿手改；改了请重跑生成脚本。",
                "samples": index,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n输出目录：{out_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
