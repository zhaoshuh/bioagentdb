"""调研下载：EPMC XML → NCBI efetch → arXiv PDF → Unpaywall PDF → metadata-only。

输出: data/survey/papers/<slug>.{xml,pdf} + data/survey/manifest.json（sha256）。
用法: python scripts/survey_download.py [--limit 0]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import utils  # noqa: E402
from app.core.config import cfg  # noqa: E402

SURVEY = ROOT / "data" / "survey"
PAPERS = SURVEY / "papers"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UNPAYWALL = "https://api.unpaywall.org/v2"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug_for(row: dict) -> str:
    base = (row.get("doi") or "").replace("/", "_") or \
        (row.get("pmcid") or "") or (row.get("source_id") or "") or \
        hashlib.sha1(row["title"].encode()).hexdigest()[:12]
    return re.sub(r"[^A-Za-z0-9_.-]", "_", base)[:90]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _save(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _valid_xml(text: str) -> bool:
    return bool(text and "<article" in text[:3000])


def try_epmc_xml(pmcid: str) -> str:
    if not pmcid:
        return ""
    text = utils.get_text(f"{EPMC}/{pmcid}/fullTextXML", timeout=60, retries=2)
    return text if _valid_xml(text) else ""


def try_ncbi(pmcid: str) -> str:
    if not pmcid:
        return ""
    text = utils.get_text("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                          params={"db": "pmc", "id": pmcid.replace("PMC", ""),
                                  "retmode": "xml"}, timeout=60, retries=2)
    return text if _valid_xml(text) else ""


def try_arxiv_pdf(arxiv_id: str) -> bytes | None:
    if not arxiv_id:
        return None
    # arxiv.org 直连超时、代理兜底可用但慢（实测 ~174s）→ 大超时、少重试
    resp = utils._request("GET", f"https://arxiv.org/pdf/{arxiv_id}", timeout=240, retries=1)
    if resp is not None and resp.status_code == 200 and resp.content[:4] == b"%PDF":
        return resp.content
    return None


def try_unpaywall_pdf(doi: str) -> bytes | None:
    if not doi:
        return None
    email = cfg.get("openalex_email") or "bioagentdb@example.org"
    data = utils.get_json(f"{UNPAYWALL}/{doi}", params={"email": email}, retries=1)
    if not isinstance(data, dict):
        return None
    loc = data.get("best_oa_location") or {}
    url = loc.get("url_for_pdf") or loc.get("url")
    if not url:
        return None
    resp = utils._request("GET", url, timeout=60, retries=1)
    if resp is not None and resp.status_code == 200 and resp.content[:4] == b"%PDF":
        return resp.content
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    rows = [json.loads(l) for l in (SURVEY / "selection.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        rows = rows[:args.limit]
    manifest_path = SURVEY / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    done = 0
    for i, r in enumerate(rows, 1):
        slug = slug_for(r)
        if slug in manifest and manifest[slug].get("route"):
            continue
        route, file = "", None
        xml = try_epmc_xml(r.get("pmcid") or "")
        if xml:
            route = "epmc_xml"
            file = PAPERS / f"{slug}.xml"
            _save(file, xml.encode("utf-8"))
        else:
            xml = try_ncbi(r.get("pmcid") or "")
            if xml:
                route = "ncbi_efetch"
                file = PAPERS / f"{slug}.xml"
                _save(file, xml.encode("utf-8"))
            else:
                pdf = try_arxiv_pdf(r.get("source_id") if r.get("source") == "arxiv" else "")
                if pdf:
                    route = "arxiv_pdf"
                    file = PAPERS / f"{slug}.pdf"
                    _save(file, pdf)
                else:
                    pdf = try_unpaywall_pdf(r.get("doi") or "")
                    if pdf:
                        route = "unpaywall_pdf"
                        file = PAPERS / f"{slug}.pdf"
                        _save(file, pdf)
        manifest[slug] = {"kind": r.get("kind"), "title": r.get("title"), "doi": r.get("doi"),
                          "pmcid": r.get("pmcid"), "route": route or "metadata-only",
                          "file": file.name if file else "", "bytes": file.stat().st_size if file else 0,
                          "sha256": sha256(file) if file else "", "retrieved_at": _utcnow()}
        done += 1
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        if i % 10 == 0 or i == len(rows):
            got = sum(1 for m in manifest.values() if m["route"] not in ("", "metadata-only"))
            print(f"  {i}/{len(rows)} · downloaded={got}")
        time.sleep(0.25)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    got = sum(1 for m in manifest.values() if m["route"] not in ("", "metadata-only"))
    by_route: dict[str, int] = {}
    for m in manifest.values():
        by_route[m["route"]] = by_route.get(m["route"], 0) + 1
    print(f"done: {len(manifest)} entries · with file {got} · routes {by_route}")


if __name__ == "__main__":
    main()
