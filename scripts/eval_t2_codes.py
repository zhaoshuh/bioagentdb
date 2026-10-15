"""W2 · T2 分级重检（L-0283）：对目录内主链接做一次分级探测，替代"可达/未达"二分。

分级：2xx=reachable · 401/403/429=needs_auth（存在但拒绝自动访问）· 404/410=missing ·
超时/连接错误=failed · URL 非法=invalid；跟随重定向，记录最终状态与耗时。
范围：release 库主链接（is_primary=1）；标注 T2-eligible（排除 link_type='doi'）。
输出：data/eval/t2-codes.json（逐链接 + 汇总）。
用法: python scripts/eval_t2_codes.py [--limit N] [--workers 16]
环境: BIOAGENTDB_EVAL_DB=<db> 切换源库（默认 _v15；E5/L-0306 传 _v15）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os  # noqa: E402

DB = Path(os.environ.get("BIOAGENTDB_EVAL_DB")
          or ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3")
OUT = ROOT / "data" / "eval" / "t2-codes.json"


def classify(status: int | None, err: str = "") -> str:
    if err == "invalid":
        return "invalid"
    if status is None:
        return "failed"
    if 200 <= status < 300:
        return "reachable"
    if status in (401, 403, 429, 451):
        return "needs_auth"
    if status in (404, 410):
        return "missing"
    if 300 <= status < 400:
        return "redirected"
    if 500 <= status < 600:
        return "server_error"
    return "other"


def probe(url: str) -> dict:
    from app.core.utils import client
    cl = client()
    t0 = time.time()
    status, err = None, ""
    try:
        r = cl.head(url, follow_redirects=True,
                    headers={"User-Agent": "BioAgentDB/0.1 (link grading)"})
        if r.status_code in (403, 405, 501):        # 一些站点拒绝 HEAD → GET 复核
            r = cl.get(url, follow_redirects=True,
                       headers={"User-Agent": "BioAgentDB/0.1 (link grading)"})
        status = r.status_code
    except Exception as exc:  # noqa: BLE001
        err = type(exc).__name__
    return {"status": status, "error": err, "seconds": round(time.time() - t0, 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--refine", action="store_true",
                    help="re-probe only failed/server_error entries from an existing t2-codes.json")
    args = ap.parse_args()

    if args.refine:
        data = json.loads(OUT.read_text(encoding="utf-8"))
        links = [r for r in data.get("links", []) if r.get("class") in ("failed", "server_error")]
        print(f"refining {len(links)} links")
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(probe, str(r.get("url") or "")): r for r in links}
            for fut in as_completed(futs):
                r = futs[fut]
                try:
                    p = fut.result()
                except Exception as exc:  # noqa: BLE001
                    p = {"status": None, "error": type(exc).__name__, "seconds": 0.0}
                r["http_status"] = p["status"]
                r["error"] = p["error"]
                r["class"] = classify(p["status"], p["error"])
                r["seconds"] = p["seconds"]
        eligible = [r for r in data["links"] if r["t2_eligible"]]
        data["summary"]["all_class"] = dict(Counter(r["class"] for r in data["links"]))
        data["summary"]["eligible_class"] = dict(Counter(r["class"] for r in eligible))
        data["summary"]["eligible_status"] = dict(Counter(str(r["http_status"]) for r in eligible))
        data["summary"]["refined_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(data["summary"], ensure_ascii=False, indent=1))
        return

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = c.execute(
        "SELECT id, dataset_id, repository, accession, url, link_type FROM dataset_links "
        "WHERE is_primary=1 ORDER BY dataset_id, id").fetchall()
    c.close()
    if args.limit:
        rows = rows[:args.limit]

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {}
        for lid, did, repo, acc, url, ltype in rows:
            futs[ex.submit(probe, str(url or ""))] = (lid, did, repo, acc, url, ltype)
        done = 0
        for fut in as_completed(futs):
            lid, did, repo, acc, url, ltype = futs[fut]
            try:
                p = fut.result()
            except Exception as exc:  # noqa: BLE001
                p = {"status": None, "error": type(exc).__name__, "seconds": 0.0}
            cls = classify(p["status"], p["error"])
            results.append({
                "link_id": lid, "dataset_id": did, "repository": repo, "accession": acc,
                "url": url, "link_type": ltype, "t2_eligible": ltype != "doi",
                "http_status": p["status"], "error": p["error"],
                "class": cls, "seconds": p["seconds"],
            })
            done += 1
            if done % 250 == 0:
                print(f"  probed {done}/{len(rows)}", flush=True)

    eligible = [r for r in results if r["t2_eligible"]]
    summary = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total_primary": len(results),
        "t2_eligible": len(eligible),
        "all_class": dict(Counter(r["class"] for r in results)),
        "eligible_class": dict(Counter(r["class"] for r in eligible)),
        "eligible_status": dict(Counter(str(r["http_status"]) for r in eligible)),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"summary": summary, "links": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
