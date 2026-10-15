"""CBC submission checks C1-C7 (pre-c1 gate).

C1 number parity vs the frozen master
C2 banned tokens (stale values, process narrative) — md / html / pdf + supplementary
C3 citation coverage: all 1..61 cited (ranges expanded); no PMID/PMCID in pdf
C4 figure specs: CBC hi-res rasters >= 2244 px; required files present
C5 format: abstract words <= 250; highlights <= 85 chars each; keywords count
C6 carriers: md numeric tokens ⊆ html (tags stripped) and ⊆ pdf (tolerated)
C7 placeholder inventory (informational; replace all before freeze)

Usage: python scripts/cbc_checks.py
Writes: docs/paper/cbc/checks/cbc-checks.json (and prints a summary)
Exit code 1 if any check fails.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

from pypdf import PdfReader  # type: ignore
from PIL import Image  # type: ignore

ROOT = pathlib.Path(__file__).resolve().parents[1]
CBC = ROOT / "docs" / "paper" / "cbc"
FINAL = ROOT / "docs" / "paper" / "final"
CHECKS = CBC / "checks"

NUM_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?")

BANNED = [
    "1,187", "1,189", "2,172", "88 rows", "T1/T2-gated", "T2 not passed",
    "46/46", "v1.3", "v1.4", "E5b", "L-0", "NAR",
]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def num_tokens(text: str) -> set[str]:
    return set(NUM_RE.findall(text))


def strip_html(html: str) -> str:
    html = re.sub(r"(?s)<style.*?</style>", " ", html)
    html = re.sub(r"(?s)<script.*?</script>", " ", html)
    return re.sub(r"<[^>]+>", " ", html)


def pdf_text(path: pathlib.Path) -> str:
    return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)


def cited_numbers(text: str) -> set[int]:
    out: set[int] = set()
    for grp in re.findall(r"\((\d+(?:\s*[,\-–]\s*\d+)*)\)", text):
        for token in re.split(r"\s*,\s*", grp):
            mm = re.match(r"^(\d+)\s*[–-]\s*(\d+)$", token)
            if mm:
                a, b = int(mm.group(1)), int(mm.group(2))
                out.update(range(min(a, b), max(a, b) + 1))
            elif token.isdigit():
                out.add(int(token))
    return out


def main() -> None:
    results: list[dict] = []
    md = (CBC / "cbc-paper.md").read_text(encoding="utf-8")
    html = (CBC / "cbc-paper.html").read_text(encoding="utf-8")
    pdf = pdf_text(CBC / "cbc-paper.pdf")
    master = (FINAL / "paper.md").read_text(encoding="utf-8")

    # ---- C1 number parity ------------------------------------------------
    m_t, c_t = num_tokens(master), num_tokens(md)
    cbc_extra = sorted(c_t - m_t)
    master_only = sorted(m_t - c_t)
    results.append({
        "check": "C1 number parity (cbc tokens vs master)",
        "ok": not master_only,
        "detail": {"cbc_extra_tokens": cbc_extra[:20], "master_only_tokens": master_only[:20]},
    })

    # ---- C2 banned tokens (main carriers + supplementary) ----------------
    hits = {tok: {"md": md.count(tok), "html": html.count(tok), "pdf": norm(pdf).count(tok)}
            for tok in BANNED}
    bad = {k: v for k, v in hits.items() if any(v.values())}
    supp_html_p = CBC / "cbc-supplementary.html"
    supp_pdf_p = CBC / "cbc-supplementary.pdf"
    supp_html = supp_html_p.read_text(encoding="utf-8") if supp_html_p.exists() else ""
    supp_pdf = norm(pdf_text(supp_pdf_p)) if supp_pdf_p.exists() else ""
    supp_banned = ["TABLES-NOTES", "hand-ported", "NAR"]
    supp_hits = {tok: {"html": supp_html.count(tok), "pdf": supp_pdf.count(tok)}
                 for tok in supp_banned}
    supp_bad = {k: v for k, v in supp_hits.items() if any(v.values())}
    results.append({"check": "C2 banned tokens (main + supplementary)",
                    "ok": (not bad) and (not supp_bad),
                    "detail": {"main": bad or "clean", "supplementary": supp_bad or "clean"}})

    # ---- C3 citation coverage -------------------------------------------
    cited = cited_numbers(md)
    missing_refs = sorted(set(range(1, 62)) - cited)
    pmid_in_pdf = len(re.findall(r"PMID|PMCID", pdf))
    results.append({"check": "C3 citation coverage 1..61 + no PMID/PMCID in pdf",
                    "ok": not missing_refs and pmid_in_pdf == 0,
                    "detail": {"uncited": missing_refs, "pmid_pmcid_hits_in_pdf": pmid_in_pdf}})

    # ---- C4 figure specs -------------------------------------------------
    hires = CBC / "figures-hires"
    fig_files = ["figure6-ledger.png", "figure7-record.png", "figure8-ask.png",
                 "supplementary-figure-s3.png", "supplementary-figure-s4.png"]
    fig_detail = {}
    c4_ok = True
    for f in fig_files:
        p = hires / f
        if not p.exists():
            fig_detail[f] = "MISSING"; c4_ok = False; continue
        w = Image.open(p).width
        fig_detail[f] = {"width_px": w, "ok": w >= 2244}
        c4_ok = c4_ok and w >= 2244
    results.append({"check": "C4 hi-res rasters >= 2244 px", "ok": c4_ok, "detail": fig_detail})

    # ---- C5 format -------------------------------------------------------
    s = md.index("## Abstract"); e = md.index("## 1. Introduction")
    abs_words = len(re.findall(r"\S+", md[s + len("## Abstract"):e].strip()))
    hl = (CBC / "cbc-highlights.md").read_text(encoding="utf-8")
    items = []
    for l in hl.splitlines():
        l = l.strip()
        if not l or l.startswith("#"):
            continue
        items.append(l[2:].strip() if l.startswith("- ") else l)
    hl_ok = [len(b) for b in items]
    kw_block = (CBC / "cbc-drafts.md").read_text(encoding="utf-8")
    mkw = re.search(r"bioinformatics;.*", kw_block)
    kw_count = len(mkw.group(0).split(";")) if mkw else 0
    c5_ok = abs_words <= 250 and len(items) == 5 and all(n <= 85 for n in hl_ok) and kw_count >= 6
    results.append({"check": "C5 format (abstract<=250; 5 highlights<=85; keywords>=6)",
                    "ok": c5_ok,
                    "detail": {"abstract_words": abs_words, "highlights_chars": hl_ok,
                               "keywords": kw_count}})

    # ---- C6 carriers (md -> html stripped -> pdf) ------------------------
    h_t = num_tokens(strip_html(html))
    p_t = num_tokens(norm(pdf))
    md_not_html = sorted(c_t - h_t)
    md_not_pdf = sorted(c_t - p_t)
    c6_ok = not md_not_html and len(md_not_pdf) <= 3
    results.append({"check": "C6 carriers md -> html -> pdf",
                    "ok": c6_ok,
                    "detail": {"md_not_html": md_not_html[:15], "md_not_pdf": md_not_pdf[:15]}})

    # ---- C7 placeholder inventory (informational; zero required at freeze) ----
    placeholders = ["[URL]", "[DOI]", "[DOI-CODE]", "[DOI-DATA]",
                    "[URL — release instance]", "bioagentdb.example",
                    "inserted at submission"]
    pdf_flat = re.sub(r"\s+", "", pdf)          # defeat PDF soft line breaks
    supp_pdf_flat = re.sub(r"\s+", "", supp_pdf)
    inv = {}
    for tok in placeholders:
        tk = re.sub(r"\s+", "", tok)
        inv[tok] = {"md": md.count(tok), "html": html.count(tok),
                    "pdf": pdf_flat.count(tk),
                    "supp_html": supp_html.count(tok), "supp_pdf": supp_pdf_flat.count(tk)}
    total = sum(sum(v.values()) for v in inv.values())
    results.append({"check": "C7 placeholder inventory (replace all before freeze)",
                    "ok": True, "detail": {"total_occurrences": total, "by_token": inv}})

    CHECKS.mkdir(parents=True, exist_ok=True)
    out = CHECKS / "cbc-checks.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    fails = [r for r in results if not r["ok"]]
    print(f"CBC checks: {len(results) - len(fails)}/{len(results)} passed")
    for r in results:
        status = "PASS" if r["ok"] else "FAIL"
        print(f"  {status} · {r['check']}")
        if not r["ok"]:
            print(f"        detail: {json.dumps(r['detail'], ensure_ascii=False)[:400]}")
    print(f"report: {out.relative_to(ROOT)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
