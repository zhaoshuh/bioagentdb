"""S5 · 正式回填：把确定性候选扫描的新登记号入库（T1 天然锚定 + T2 链接检查）。

- 只加"论文正文里出现、且目录里还没有"的登记号；不改动已有记录。
- 状态：T1 锚定（有原文引文）→ `paper_verified`；T2 可达性写入链接。
- 记录来源标记 run_id=`s5-backfill`。
用法: python scripts/backfill_apply.py [--no-links] [--limit 0]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import vocab  # noqa: E402
from app.core.db import db  # noqa: E402
from app.core.utils import http_status  # noqa: E402
from app.harvest.candidates import candidates_for_paper  # noqa: E402
from app.harvest.ingest import ingest_record  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-links", action="store_true", help="跳过 T2 链接检查")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    with db() as conn:
        rows = conn.execute(
            "SELECT id, title, abstract, da_text, supp_files FROM papers "
            "WHERE abstract != '' OR da_text != ''").fetchall()
        existing = {}
        for r in conn.execute(
                "SELECT dp.paper_id, l.accession FROM dataset_papers dp "
                "JOIN dataset_links l ON l.dataset_id=dp.dataset_id"):
            if r["accession"]:
                existing.setdefault(r["paper_id"], set()).add(vocab.canon_accession(r["accession"]))
    if args.limit:
        rows = rows[: args.limit]

    # 1) 收集待入库候选
    todo = []
    for r in rows:
        try:
            supp = json.loads(r["supp_files"] or "[]")
        except Exception:  # noqa: BLE001
            supp = []
        have = existing.get(r["id"], set())
        for c in candidates_for_paper({"abstract": r["abstract"], "da_text": r["da_text"]}, supp):
            a = vocab.canon_accession(c["accession"])
            if not a or a in have:
                continue
            have.add(a)
            repo, url, dl, label = vocab.canonical_links(c["accession"])
            todo.append({"paper_id": r["id"], "paper_title": r["title"],
                         "repository": c["repository"] or repo, "identifier": c["accession"],
                         "url": url, "download_url": dl, "label": label,
                         "quote": c["quote"], "zone": c["zone"]})

    print(f"candidates to ingest: {len(todo)}")

    # 2) T2 链接检查（并发）
    if not args.no_links:
        def check(t):
            if t["url"]:
                ok, status = http_status(t["url"])
                t["reachable"] = bool(ok)
                t["http_status"] = status
            return t
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            todo = list(ex.map(check, todo))

    # 3) 入库
    added = 0
    by_repo = Counter()
    reach = Counter()
    for t in todo:
        rec = {
            "repository": t["repository"], "identifier": t["identifier"],
            "title": f"{t['label'] or t['repository']} {t['identifier']}",
            "description": f"Grounded in paper text: {t['quote'][:200]}",
            "organism": "", "tissue": "", "disease": "", "platform": "",
            "modality": [], "sample_count": None, "role": "primary",
            "url": t["url"], "download_url": t["download_url"], "link_type": "portal",
            "is_primary": True, "link_reachable": t.get("reachable"),
            "http_status": t.get("http_status", ""), "repository_label": t["label"],
            "confidence": 0.7, "evidence": {"zone": t["zone"], "quote": t["quote"]},
        }
        info = ingest_record(rec, paper_id=t["paper_id"], run_id="s5-backfill",
                             verdict="paper_verified",
                             reason="s5 deterministic candidate scan (T1 grounded)")
        if info.get("created"):
            added += 1
            by_repo[t["repository"]] += 1
            reach["reachable" if t.get("reachable") else (
                "unreachable" if "reachable" in t else "unchecked")] += 1

    out = {"ingested": len(todo), "created": added, "by_repository": dict(by_repo.most_common()),
           "reachability": dict(reach)}
    g = ROOT / "data" / "gold"
    (g / "s5-backfill-report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2)[:800])


if __name__ == "__main__":
    main()
