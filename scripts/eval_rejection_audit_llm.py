"""W3 · 否决精确率审计（模型辅助版，L-0298）：为 rejection-audit.csv 的样本逐条判定
`audit_verdict(correct/wrong/uncertain)` + `audit_note`，并生成汇总 JSON。

判定依据（逐条注入）：红队拒绝理由 + 候选的 T1 引文/检查载荷（release DB `candidates.payload`）
+ 论文 Data Availability 原文（release DB `papers.da_text`）。

口径（实质正确性；含 case-fold 归一化）：
- **correct**：该记录不应入目录——标识符未公开存缴/不存在、仅"向作者索取"且无公开存缴、
  字段错误（分析代码/数据申请程序页）、重复、离题。
- **wrong**：记录实质合格而被拒——仅大小写差异；DA 明示已在公共库存缴（GEO/Zenodo/PRIDE/ENA…）
  却因"另有 upon-request 条款"整条拒；同类过严情形。
- **uncertain**：所给材料不足以判定（含空理由/LLM 失败）。

用法: python scripts/eval_rejection_audit_llm.py [--limit N]
输出：回写 data/eval/rejection-audit.csv 两列 + data/eval/rejection-audit-summary.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CSV = ROOT / "data" / "eval" / "rejection-audit.csv"
SUMMARY = ROOT / "data" / "eval" / "rejection-audit-summary.json"
DB = Path(os.environ.get("BIOAGENTDB_EVAL_DB")
          or ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3")

SYSTEM = (
    "You audit red-team rejections for BioAgentDB, a catalog of public omics datasets announced "
    "in papers. Decide whether each rejection was SUBSTANTIVELY correct, using the provided "
    "Data Availability (DA) text and the candidate's grounding quote.\n"
    "Decision rules:\n"
    "- Accessions are case-folded in this catalog: case-only differences (ZENODO vs zenodo, "
    "FIGSHARE.COM/... vs figshare.com/...) are NOT valid rejection grounds.\n"
    "- An 'available upon request' clause alone is not enough: if the DA text shows this "
    "accession/dataset deposited in a public repository, or the on-request clause concerns OTHER "
    "data, the rejection of this record is over-strict.\n"
    "- correct: the record genuinely should not be in the catalog — identifier not deposited / "
    "absent; on-request-only with no public deposit; wrong field (analysis code or an "
    "access-procedure page, not data); duplicate; off-topic; or a failed deterministic gate "
    "(t1_paper_grounding ok=false means the identifier was not found in its grounding quote).\n"
    "- If no rejection reason is recorded, decide from the candidate checks: a failed "
    "t1_paper_grounding / t2 check makes the rejection policy-correct; if all checks pass yet "
    "the candidate was rejected, mark uncertain unless the evidence clearly shows a defect.\n"
    "- wrong: the record is substantively fine and the rejection was over-strict.\n"
    "- uncertain: the provided material cannot settle it.\n"
    'Return JSON ONLY: {"verdict": "correct"|"wrong"|"uncertain", '
    '"note": "<=25 words, cite the deciding evidence"}'
)


def bucket(reason: str) -> str:
    r = (reason or "").lower()
    if "request" in r:
        return "on_request"
    if "case" in r:
        return "case_mismatch"
    if "field" in r or "mistake" in r:
        return "field_mistake"
    return "other"


def enrich(conn: sqlite3.Connection, row: dict) -> str:
    """候选载荷（检查名/引文）+ 论文 DA 原文。"""
    parts = []
    cand = conn.execute("SELECT payload FROM candidates WHERE id=?",
                        (row.get("candidate_id"),)).fetchone()
    if cand:
        try:
            payload = json.loads(cand[0] or "{}")
        except Exception:  # noqa: BLE001
            payload = {}
        for chk in (payload.get("checks") or [])[:6]:
            parts.append(f"candidate check [{chk.get('name')}] ok={chk.get('ok')}: "
                         + str(chk.get("detail") or "")[:240])
    p = conn.execute("SELECT da_text, abstract FROM papers WHERE id=?",
                     (row.get("paper_id"),)).fetchone()
    if p:
        da = (p[0] or "").strip()
        if da:
            parts.append("Data Availability text: " + da[:900])
        elif p[1]:
            parts.append("Abstract (no DA text): " + (p[1] or "")[:500])
    return "\n".join(parts)


def judge(enrichment: str, row: dict) -> tuple[str, str]:
    from app.harvest.llm import chat_json

    reason = (row.get("rejection_reason") or "").strip()
    reason_line = (f"Red-team rejection reason: {reason}" if reason else
                   "Rejection reason: (none recorded — decide from the candidate checks below)")
    user = (
        f"Record accession: {row.get('accession') or ''}\n"
        f"Repository: {row.get('repository') or ''}\n"
        f"Record title: {row.get('record_title') or ''}\n"
        f"Paper title: {row.get('paper_title') or ''}\n"
        f"PMID: {row.get('pmid') or ''} | DOI: {row.get('doi') or ''}\n"
        f"{reason_line}\n"
        f"--- evidence ---\n{enrichment}\n"
        "Was this rejection substantively correct?"
    )
    out = chat_json([
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ], model=None, timeout=90)
    verdict = str((out or {}).get("verdict") or "").strip().lower()
    note = str((out or {}).get("note") or "").strip()[:140]
    if verdict not in ("correct", "wrong", "uncertain"):
        return "uncertain", note or "llm returned no verdict"
    return verdict, note


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rejudge-empty-reason", action="store_true",
                    help="只重判 rejection_reason 为空的样本（其余保留既有判定）")
    args = ap.parse_args()

    rows = list(csv.DictReader(CSV.open(encoding="utf-8-sig")))
    limited = bool(args.limit)
    if limited:
        rows = rows[:args.limit]
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    vcol = "audit_verdict(correct/wrong/uncertain)"
    counts = Counter()
    by_bucket: dict[str, Counter] = {}
    by_cohort: dict[str, Counter] = {}
    t0 = time.time()
    for i, row in enumerate(rows, 1):
        reason_empty = not (row.get("rejection_reason") or "").strip()
        if args.rejudge_empty_reason:
            if not reason_empty:
                continue
        elif limited:
            pass
        verdict, note = judge(enrich(conn, row), row)
        row[vcol] = verdict
        row["audit_note"] = note
        cohort = "no_reason" if reason_empty else "with_reason"
        by_cohort.setdefault(cohort, Counter())[verdict] += 1
        print(f"[{i}/{len(rows)}] {verdict:9s} | {bucket(row.get('rejection_reason') or ''):13s} | "
              f"{(row.get('accession') or '')[:40]}", flush=True)
    conn.close()
    if limited:
        print("preflight only (--limit): CSV/summary not written")
        print(json.dumps({"counts": dict(counts),
                          "by_bucket": {k: dict(v) for k, v in sorted(by_bucket.items())}},
                         ensure_ascii=False, indent=1))
        return
    # recompute the full picture from the (possibly partially updated) CSV state
    counts = Counter()
    by_bucket = {}
    by_cohort = {}
    for row in rows:
        v = row.get(vcol) or ""
        if v not in ("correct", "wrong", "uncertain"):
            continue
        counts[v] += 1
        b = bucket(row.get("rejection_reason") or "")
        by_bucket.setdefault(b, Counter())[v] += 1
        cohort = "no_reason" if not (row.get("rejection_reason") or "").strip() else "with_reason"
        by_cohort.setdefault(cohort, Counter())[v] += 1
    fieldnames = list(rows[0].keys())
    with CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    summary = {
        "method": ("model-assisted audit (disclosed); enriched with DA text + candidate "
                   "grounding quote; case-fold normalization criterion"),
        "n": len(rows),
        "counts": dict(counts),
        "by_bucket": {k: dict(v) for k, v in sorted(by_bucket.items())},
        "by_cohort": {k: dict(v) for k, v in sorted(by_cohort.items())},
        "rejection_precision_correct_over_judged": (
            round(counts["correct"] / max(1, counts["correct"] + counts["wrong"]), 3)),
        "elapsed_s": round(time.time() - t0, 1),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"wrote {CSV} + {SUMMARY.name}")


if __name__ == "__main__":
    main()
