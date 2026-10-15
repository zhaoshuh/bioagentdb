"""W6 · 与现有资源的同样本定量对比（L-0283）。

对金标 250 篇：
  - Europe PMC 文本挖掘的 Accession Numbers（Annotations API）：vs gold → P/R/F1；
  - Europe PMC 策展 database links（databaseLinks API）：覆盖率旁证（预期稀疏）；
  - OmicsDI（search API）：gold accession 收录覆盖率；
  - PubTator 3.0：实体类型清单证据（结构性不适用）。
输出：data/eval/vs-resources.json + .md；网络响应缓存于 data/eval/comparison-cache/。
用法: python scripts/eval_vs_resources.py [--limit N] [--skip-omicsdi] [--workers 4]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3"
GOLD = ROOT / "data" / "gold" / "gold.jsonl"
CACHE = ROOT / "data" / "eval" / "comparison-cache"
OUT_J = ROOT / "data" / "eval" / "vs-resources.json"
OUT_M = ROOT / "data" / "eval" / "vs-resources.md"
EPMC_ANN = "https://www.ebi.ac.uk/europepmc/annotations_api/annotationsByArticleIds"
EPMC_DBL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
OMICSDI = "https://www.omicsdi.org/ws/dataset/search"


def norm(acc: str) -> str:
    from app.core.vocab import canon_accession
    return canon_accession(acc)


def _fetch(url: str, cache_key: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{cache_key}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    from app.core.utils import client
    try:
        r = client().get(url, headers={"User-Agent": "BioAgentDB/0.1 (comparison)"})
        data = r.json() if r.status_code == 200 else {"_status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        data = {"_error": type(exc).__name__}
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    time.sleep(0.3)
    return data


def _keep_accession(s: str) -> bool:
    """只保留匹配数据仓词汇（与管线同一参照系）的字符串，滤掉 DOI/PDB/RRID 等。"""
    try:
        from app.harvest.extract import find_identifiers
        ids = find_identifiers(s)
    except Exception:  # noqa: BLE001
        return False
    return any(norm(i.get("acc", "")) == norm(s) and norm(s) for i in ids)


def epmc_accessions(pmid: str, pmcid: str) -> tuple[set[str], int]:
    ids = []
    if pmid:
        ids.append(f"MED:{pmid}")
    if pmcid:
        ids.append(f"PMC:{pmcid}")
    if not ids:
        return set(), 0
    from urllib.parse import quote
    d = _fetch(f"{EPMC_ANN}?articleIds={quote(','.join(ids))}&format=JSON"
               f"&type={quote('Accession Numbers')}", f"epmcann-{pmid or pmcid}")
    raw = 0
    out = set()
    if isinstance(d, list):
        for art in d:
            for ann in (art.get("annotations") or []):
                for tag in (ann.get("tags") or []):
                    name = tag.get("name") or ann.get("exact")
                    if name:
                        raw += 1
                        if _keep_accession(str(name)):
                            out.add(str(name))
    return out, raw


def epmc_dblinks(pmid: str) -> int:
    if not pmid:
        return 0
    d = _fetch(f"{EPMC_DBL}/MED/{pmid}/databaseLinks?format=json", f"epmcdbl-{pmid}")
    xr = (d or {}).get("dbCrossReferenceList") or {}
    return sum(len(v) for v in xr.values()) if isinstance(xr, dict) else 0


def omicsdi_has(acc: str) -> bool:
    from urllib.parse import quote
    d = _fetch(f"{OMICSDI}?query={quote(acc)}&size=10",
               f"omicsdi-{re.sub(r'[^A-Za-z0-9_.-]', '_', acc)}")
    if not isinstance(d, dict):
        return False
    for ds in (d.get("datasets") or []):
        if acc.lower() in json.dumps(ds, ensure_ascii=False).lower():
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-omicsdi", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    gold = {}
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            gold[r["paper_id"]] = r
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ids = {r[0]: {"pmid": r[1] or "", "doi": r[2] or "", "pmcid": r[3] or ""}
           for r in c.execute("SELECT id, pmid, doi, pmcid FROM papers")}
    c.close()
    papers = sorted(gold)
    if args.limit:
        papers = papers[:args.limit]

    tp = fp = fn = 0
    dblinks_total = 0
    raw_annotations = 0
    per_paper = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(epmc_accessions, ids.get(p, {}).get("pmid", ""),
                          ids.get(p, {}).get("pmcid", "")): p for p in papers}
        for i, fut in enumerate(as_completed(futs), 1):
            p = futs[fut]
            got_raw, raw_n = fut.result()
            raw_annotations += raw_n
            got = {norm(x) for x in got_raw}
            got.discard("")
            ref = {norm(x) for x in gold[p].get("accessions", [])}
            ref.discard("")
            tp += len(got & ref)
            fp += len(got - ref)
            fn += len(ref - got)
            per_paper.append({"paper_id": p, "epmc": sorted(got), "gold": sorted(ref),
                              "tp": sorted(got & ref), "fp": sorted(got - ref),
                              "fn": sorted(ref - got)})
            if i % 50 == 0:
                print(f"  epmc {i}/{len(papers)}", flush=True)

    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = 2 * prec * rec / max(1e-9, prec + rec)

    if not args.limit:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs3 = {ex.submit(epmc_dblinks, ids.get(p, {}).get("pmid", "")): p for p in papers}
            for fut in as_completed(futs3):
                dblinks_total += fut.result()

    omicsdi = {"checked": 0, "covered": 0, "missing": []}
    if not args.skip_omicsdi:
        gold_accs = sorted({norm(x) for p in papers for x in gold[p].get("accessions", [])} - {""})
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs2 = {ex.submit(omicsdi_has, a): a for a in gold_accs}
            for i, fut in enumerate(as_completed(futs2), 1):
                a = futs2[fut]
                omicsdi["checked"] += 1
                if fut.result():
                    omicsdi["covered"] += 1
                else:
                    omicsdi["missing"].append(a)
                if i % 50 == 0:
                    print(f"  omicsdi {i}/{len(gold_accs)}", flush=True)
        omicsdi["coverage"] = round(omicsdi["covered"] / max(1, omicsdi["checked"]), 4)

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sample": {"papers": len(papers), "gold_accessions": tp + fn},
        "europepmc_accessions": {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
            "raw_annotations_seen": raw_annotations,
            "note": "EPMC annotations filtered to strings matching the databank vocabulary",
        },
        "europepmc_curated_dblinks": {"total_links": dblinks_total, "papers": len(papers)},
        "omicsdi": omicsdi,
        "note": "EPMC measured with the same accession normalisation as the pipeline, against "
                "the same human gold; OmicsDI measured as accession coverage.",
        "per_paper": per_paper,
    }
    OUT_J.parent.mkdir(parents=True, exist_ok=True)
    OUT_J.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Same-sample comparison with existing resources", "",
             f"- papers: {len(papers)}; gold accessions: {tp + fn}",
             f"- Europe PMC (text-mined accessions): P {prec:.3f} / R {rec:.3f} / F1 {f1:.3f} "
             f"(tp={tp}, fp={fp}, fn={fn})",
             f"- Europe PMC (curated database links): {dblinks_total} links across {len(papers)} papers",
             f"- OmicsDI: coverage {omicsdi.get('coverage', 'n/a')} "
             f"({omicsdi.get('covered')}/{omicsdi.get('checked')} gold accessions listed)"]
    OUT_M.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"wrote {OUT_J}")


if __name__ == "__main__":
    main()
