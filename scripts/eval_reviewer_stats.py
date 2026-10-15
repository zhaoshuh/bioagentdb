"""W3 · 评审/核验统计（L-0283）：从 release 库候选层导出——
- verdict × run_id（管线 redteam vs 确定性扫描 s5-backfill 等）
- checks 名称分布与 ok 率（含 T2 状态码类别）
- 拒绝原因（reason）Top 分布
- 未覆层：无 checks 的入档候选份额（未经核验检查的路径）

输出: data/eval/reviewer-stats.json + .md
用法: python scripts/eval_reviewer_stats.py
环境: BIOAGENTDB_EVAL_DB=<db> 切换源库（默认 _v15；E5/L-0306 传 _v15）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os  # noqa: E402

DB = Path(os.environ.get("BIOAGENTDB_EVAL_DB")
          or ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3")
OUT_J = ROOT / "data" / "eval" / "reviewer-stats.json"
OUT_M = ROOT / "data" / "eval" / "reviewer-stats.md"


def main() -> None:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = c.execute("SELECT id, run_id, verdict, reason, payload FROM candidates").fetchall()
    c.close()

    by_run = Counter()
    by_run_verdict = defaultdict(Counter)
    no_checks_by_run = Counter()
    check_names = Counter()
    check_ok = Counter()
    t2_detail = Counter()
    reasons = Counter()

    for _cid, run, verdict, reason, payload in rows:
        run = run or "(none)"
        by_run[run] += 1
        by_run_verdict[run][verdict or "(none)"] += 1
        has_checks = False
        if payload:
            try:
                checks = json.loads(payload).get("checks") or []
            except Exception:  # noqa: BLE001
                checks = []
            if checks:
                has_checks = True
            for chk in checks:
                name = chk.get("name", "?")
                check_names[name] += 1
                if chk.get("ok"):
                    check_ok[name] += 1
                if name.startswith("t2"):
                    t2_detail[str(chk.get("detail", ""))[:28]] += 1
        if not has_checks:
            no_checks_by_run[run] += 1
        if verdict == "rejected" and reason:
            reasons[str(reason)[:80]] += 1

    total = len(rows)
    checked = total - sum(no_checks_by_run.values())
    import time as _time
    report = {
        "generated_at": _time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "db": str(DB),
        "total_candidates": total,
        "with_checks": checked,
        "without_checks": total - checked,
        "without_checks_by_run": dict(no_checks_by_run),
        "by_run": dict(by_run),
        "by_run_verdict": {k: dict(v) for k, v in by_run_verdict.items()},
        "check_names": dict(check_names),
        "check_ok_rate": {k: round(check_ok[k] / check_names[k], 4)
                          for k in check_names if check_names[k]},
        "t2_detail_top": dict(t2_detail.most_common(12)),
        "rejection_reasons_top": dict(reasons.most_common(12)),
    }
    OUT_J.parent.mkdir(parents=True, exist_ok=True)
    OUT_J.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# Reviewer / verification statistics (from the catalog)", "",
             f"- candidates: {total}; with checks: {checked}; without checks: {total - checked}",
             f"- by run: {json.dumps(dict(by_run), ensure_ascii=False)}",
             f"- without checks by run: {json.dumps(dict(no_checks_by_run), ensure_ascii=False)}",
             f"- check names: {json.dumps(dict(check_names), ensure_ascii=False)}",
             f"- ok rates: {json.dumps({k: round(check_ok[k] / check_names[k], 3) for k in check_names if check_names[k]}, ensure_ascii=False)}",
             f"- T2 details: {json.dumps(dict(t2_detail.most_common(12)), ensure_ascii=False)}",
             f"- rejection reasons: {json.dumps(dict(reasons.most_common(12)), ensure_ascii=False)}"]
    OUT_M.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"wrote {OUT_J} / {OUT_M}")


if __name__ == "__main__":
    main()
