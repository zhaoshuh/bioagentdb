"""P4 spot-check — freeze a deterministic 100-link sample, then re-check the
same links after the full run and compare verdicts (PLAN P4 gate: 100 条一致).

    python scripts/v2_t2_spotcheck.py --freeze   # checks/t2-spotcheck-list.json
    python scripts/v2_t2_spotcheck.py --run      # checks/t2-spotcheck-result.json

The sample is deterministic: every `step`-th eligible link from the pinned
worklist (t2-codes.json), step = len // 100. `--run` re-performs live checks
(fresh requests) and reports strict (status + reason) and loose (status)
agreement against the stored t2-recheck.jsonl rows. Live re-checks can differ
for genuinely dynamic pages; the report logs every mismatch.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

T2CODES = ROOT / "data" / "eval" / "t2-codes.json"
JSONL = ROOT / "docs" / "paper" / "cbc" / "v2" / "t2-recheck.jsonl"
LIST = ROOT / "docs" / "paper" / "cbc" / "v2" / "checks" / "t2-spotcheck-list.json"
RESULT = ROOT / "docs" / "paper" / "cbc" / "v2" / "checks" / "t2-spotcheck-result.json"


def freeze() -> None:
    data = json.loads(T2CODES.read_text(encoding="utf-8"))
    work = [r for r in data["links"] if r.get("t2_eligible")]
    step = max(1, len(work) // 100)
    sample = [work[i * step] for i in range(100) if i * step < len(work)]
    out = {
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_eligible": len(work),
        "step": step,
        "rows": [{"link_id": r["link_id"], "url": r["url"],
                  "accession": r.get("accession"), "v1_class": r.get("class")} for r in sample],
    }
    LIST.parent.mkdir(parents=True, exist_ok=True)
    LIST.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"frozen {len(out['rows'])} links (step={step}) -> {LIST}")


def run() -> None:
    from app.harvest import t2_v2

    sample = json.loads(LIST.read_text(encoding="utf-8"))["rows"]
    stored = {}
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            stored[r["link_id"]] = r

    rows = []
    for item in sample:
        sid = item["link_id"]
        v = t2_v2.check_url(item["url"], report_tokens=[item["accession"]] if item.get("accession") else None)
        s = stored.get(sid)
        row = {
            "link_id": sid, "url": item["url"],
            "stored": {"status": s["v2_status"], "reason": s["v2_reason"]} if s else None,
            "fresh": {"status": v["status"], "reason": v["reason"]},
            "status_agree": bool(s) and s["v2_status"] == v["status"],
            "strict_agree": bool(s) and s["v2_status"] == v["status"] and s["v2_reason"] == v["reason"],
        }
        rows.append(row)
        time.sleep(0.5)

    strict = sum(1 for r in rows if r["strict_agree"])
    loose = sum(1 for r in rows if r["status_agree"])
    out = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n": len(rows),
        "strict_agree": strict,
        "status_agree": loose,
        "strict_rate": round(strict / max(1, len(rows)), 4),
        "status_rate": round(loose / max(1, len(rows)), 4),
        "mismatches": [r for r in rows if not r["strict_agree"]],
    }
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "mismatches"}, ensure_ascii=False, indent=1))
    print(f"mismatches: {len(out['mismatches'])} (see {RESULT})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    if args.freeze:
        freeze()
    if args.run:
        run()
