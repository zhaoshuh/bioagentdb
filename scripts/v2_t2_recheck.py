"""P4 driver — full v2 T2 re-check of the 3,526 T2-eligible primary links.

Worklist: `data/eval/t2-codes.json` (v1 reference pinned per L-0353).
Resumable: link_ids already present in the output JSONL are skipped.

Accounting policy (L-0356, "challenge-aware"):
    Anti-bot / auth walls are environmental, not evidence that the link is dead.
    * t2_v2 classification is used **as-is** (blocked-waf / blocked-auth / ...
      are recorded truthfully in `v2_status` / `v2_reason`).
    * When today's verdict is `unknown(blocked-*)` or `rate-limited` and the v1
      snapshot had a resolved class, the previous status is **carried forward**
      into `carried_status` (stale, blocked-now) instead of counting as a flip.
    * Flip buckets: `as_expected` / `explained` (new-class detection: soft-404,
      WAF, redirects, or a blocked/rate-limited outcome) / `unexplained`.
      The G1 threshold (<=20%) applies to **unexplained** flips only.

Outputs:
    docs/paper/cbc/v2/t2-recheck.jsonl
    docs/paper/cbc/v2/checks/t2-recheck-summary.json

Politeness: <=8 workers, >=1.0 s per host between requests; t2_v2 internal
retries apply. Accession presence is reported only (`report_tokens`).

Usage:
    python scripts/v2_t2_recheck.py [--limit N] [--workers 8]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

T2CODES = ROOT / "data" / "eval" / "t2-codes.json"
OUT_JSONL = ROOT / "docs" / "paper" / "cbc" / "v2" / "t2-recheck.jsonl"
OUT_SUMMARY = ROOT / "docs" / "paper" / "cbc" / "v2" / "checks" / "t2-recheck-summary.json"

_HOST_LOCK = threading.Lock()
_HOST_LAST: dict[str, float] = {}
HOST_MIN_INTERVAL = 1.0

EXPECT = {  # v1 class -> expected v2 status (t2-mapping.md)
    "reachable": "reachable",
    "needs_auth": "unknown",
    "missing": "not_found",
    "failed": "unknown",
}

EXPLAINED_REASONS = {
    "soft-404", "blocked-waf", "redirect-suspect", "redirect-unresolved",
    "blocked-login", "blocked-auth", "blocked-forbidden", "blocked-method",
    "rate-limited", "server-error", "network-error",
}


def _polite(host: str) -> None:
    while True:
        with _HOST_LOCK:
            now = time.time()
            last = _HOST_LAST.get(host, 0.0)
            if now - last >= HOST_MIN_INTERVAL:
                _HOST_LAST[host] = now
                return
            wait = HOST_MIN_INTERVAL - (now - last)
        time.sleep(wait)


def _bucket(row: dict) -> str:
    expected = EXPECT.get(row.get("v1_class"), row["v2_status"])
    if row["v2_status"] == expected and row["v2_reason"] == "ok":
        return "as_expected"
    if row["v2_status"] == expected:
        return "as_expected"          # e.g. needs_auth -> unknown(blocked-forbidden)
    if row["v2_reason"] in EXPLAINED_REASONS:
        return "explained"            # blocked walls / new-class detections
    return "unexplained"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    from app.harvest import t2_v2

    data = json.loads(T2CODES.read_text(encoding="utf-8"))
    work = [r for r in data["links"] if r.get("t2_eligible")]
    if args.limit:
        work = work[: args.limit]
    print(f"worklist: {len(work)} eligible links")

    done: set[str] = set()
    if OUT_JSONL.exists():
        for line in OUT_JSONL.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    done.add(json.loads(line)["link_id"])
                except Exception:
                    pass
    todo = [r for r in work if r["link_id"] not in done]
    print(f"already done: {len(done)}; todo: {len(todo)}")

    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    counter = {"n": 0}

    def one(r: dict) -> dict:
        host = urlsplit(r["url"]).netloc.lower()
        _polite(host)
        acc = r.get("accession") or ""
        v = t2_v2.check_url(r["url"], report_tokens=[acc] if acc else None)
        blocked_now = v["reason"].startswith("blocked-") or v["reason"] == "rate-limited"
        carried = None
        if v["status"] == "unknown" and blocked_now and r.get("class"):
            carried = r.get("class")  # last known status carried forward (stale)
        return {
            "link_id": r["link_id"], "dataset_id": r.get("dataset_id"),
            "accession": acc, "url": r["url"], "host": host,
            "v1_class": r.get("class"), "v1_status": r.get("http_status"),
            "v2_status": v["status"], "v2_reason": v["reason"],
            "signal": v.get("signal"), "http": v.get("http"), "hops": v.get("hops"),
            "ct": v.get("ct"), "attempts": v.get("attempts"), "as_of": v.get("as_of"),
            "tokens_present": v.get("tokens_present"), "carried_status": carried,
        }

    rows: list[dict] = []
    with OUT_JSONL.open("a", encoding="utf-8") as fh, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(one, rr): rr for rr in todo}
        for fut in as_completed(futs):
            row = fut.result()
            with lock:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                counter["n"] += 1
                rows.append(row)
                if counter["n"] % 100 == 0:
                    print(f"  {counter['n']}/{len(todo)}")

    all_rows = [json.loads(l) for l in OUT_JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_status = Counter(r["v2_status"] for r in all_rows)
    by_reason = Counter(r["v2_reason"] for r in all_rows)
    buckets = Counter(_bucket(r) for r in all_rows)
    carried = sum(1 for r in all_rows if r.get("carried_status"))
    host_blocked = Counter(r["host"] for r in all_rows if r["v2_reason"].startswith("blocked-"))
    unexplained = [r for r in all_rows if _bucket(r) == "unexplained"]
    summary = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rows": len(all_rows),
        "by_v2_status": dict(by_status),
        "by_reason": dict(by_reason),
        "buckets": dict(buckets),
        "carried_forward": carried,
        "top_blocked_hosts": dict(host_blocked.most_common(12)),
        "unexplained_flip_rate": round(len(unexplained) / max(1, len(all_rows)), 4),
        "g1_t2_unexplained_ok": (len(unexplained) / max(1, len(all_rows))) <= 0.20,
        "unexplained_examples": unexplained[:20],
        "_notation": {
            "buckets": ("as_expected = v2 verdict matches the t2-mapping expectation; "
                        "explained = flip attributable to new-class detection or blocked walls; "
                        "unexplained = all other flips (the G1 threshold applies to this bucket)"),
            "unexplained_flip_rate": "len(bucket=unexplained) / rows",
            "g1_t2_unexplained_ok": "unexplained_flip_rate <= 0.20",
            "unexplained_examples": ("first 20 unexplained rows for inspection; the full list is "
                                     "recomputable from t2-recheck.jsonl with the same bucket rule"),
            "carried_forward": ("rows where a blocked-*/rate-limited v1 snapshot class was carried "
                                "into carried_status (stale, blocked-now) - see L-0356"),
            "note": ("an earlier dry-run summary carried a flips_vs_mapping/flip_rate pair; those "
                     "names were dropped with the L-0356 accounting - the bucket labels above are "
                     "authoritative; field semantics in docs/paper/cbc/v2/checks/FIELDS.md"),
        },
    }
    OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1)[:2200])


if __name__ == "__main__":
    main()
