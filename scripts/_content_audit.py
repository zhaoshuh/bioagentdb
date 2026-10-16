"""模拟用户 A（内容/组学专家）：对目录内容做对抗性审计。"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.db import db  # noqa: E402

ENTITY = re.compile(r"&lt;|&gt;|&amp;|&quot;|&#\d+;|<i>|</i>|<sup>|</sup>|<sub>|</sub>")
ACC_ONLY = re.compile(r"^(GSE\d+|PXD\d+|PRJ[A-Z]*\d+|MTBLS\d+|phs\d+|EGAS\d+|SRP\d+|ERP\d+|E-MTAB-\d+|10\.\d{4,}/\S+)$", re.I)

findings: list[tuple[str, str, str]] = []


def note(sev: str, title: str, detail: str) -> None:
    findings.append((sev, title, detail))


with db() as conn:
    # 1) HTML 实体残留
    for tbl, col in (("papers", "title"), ("papers", "abstract"), ("datasets", "title"),
                     ("datasets", "description"), ("evidence", "quote")):
        rows = conn.execute(f"SELECT id, {col} AS v FROM {tbl}").fetchall()
        bad = [(r["id"], r["v"]) for r in rows if r["v"] and ENTITY.search(r["v"])]
        if bad:
            note("P0", f"HTML residue in {tbl}.{col}", f"{len(bad)} rows, e.g. {bad[0][1][:120]}")
        else:
            note("ok", f"{tbl}.{col} entity-free", f"{len(rows)} rows checked")

    # 2) 证据自洽：每个有主登记号的数据集，是否有一条含该号的引文
    rows = conn.execute("""
        SELECT d.id, l.accession AS acc,
               (SELECT COUNT(*) FROM evidence e WHERE e.dataset_id=d.id
                  AND UPPER(e.quote) LIKE '%'||UPPER(l.accession)||'%') AS n
        FROM datasets d JOIN dataset_links l ON l.dataset_id=d.id AND l.is_primary=1
        WHERE l.accession != ''""").fetchall()
    miss = [r["acc"] for r in rows if r["n"] == 0]
    note("P0" if miss else "ok", "evidence congruent with accession",
         f"{len(miss)}/{len(rows)} datasets lack a quote containing their accession"
         + (f", e.g. {miss[:5]}" if miss else ""))

    # 3) 疾病标注 vs 论文主题
    rows = conn.execute("""
        SELECT d.id, d.disease, p.theme, p.title
        FROM datasets d JOIN dataset_papers dp ON dp.dataset_id=d.id
        JOIN papers p ON p.id=dp.paper_id
        WHERE d.disease != ''""").fetchall()
    KEYS = {"alzheimers-disease": ("alzheimer", "dementia", "down syndrome"),
            "als": ("als", "amyotrophic", "motor neuron", "frontotemporal"),
            "parkinsons-disease": ("parkinson", "lewy", "synuclein")}
    mism = []
    for r in rows:
        dz = r["disease"].lower()
        named = {k for k, ws in KEYS.items() if any(w in dz for w in ws)}
        if named and r["theme"] and r["theme"] not in named and r["theme"] in KEYS:
            mism.append((r["theme"], r["disease"], (r["title"] or "")[:60]))
    note("P1" if mism else "ok", "dataset disease matches paper theme",
         f"{len(mism)} mismatches" + (f", e.g. {mism[:3]}" if mism else ""))

    # 4) 同一登记号映射到多个数据集行
    rows = conn.execute("""
        SELECT repository, accession, COUNT(DISTINCT dataset_id) n
        FROM dataset_links WHERE accession != ''
        GROUP BY repository, accession HAVING n > 1 ORDER BY n DESC""").fetchall()
    note("P1" if rows else "ok", "no accession mapped to multiple dataset rows",
         f"{len(rows)} duplicated accessions" + (f", e.g. {[dict(r) for r in rows[:3]]}" if rows else ""))

    # 5) 有数据集的论文但没有证据
    rows = conn.execute("""
        SELECT p.id, p.title FROM papers p
        WHERE EXISTS (SELECT 1 FROM dataset_papers dp WHERE dp.paper_id=p.id)
          AND NOT EXISTS (SELECT 1 FROM evidence e WHERE e.paper_id=p.id)""").fetchall()
    note("P1" if rows else "ok", "papers with data have evidence",
         f"{len(rows)} without evidence" + (f", e.g. {rows[0]['title'][:70]}" if rows else ""))

    # 6) 标题就是登记号 / dbGaP 占位
    rows = conn.execute("SELECT id, title FROM papers").fetchall()
    bad = [r["title"] for r in rows if r["title"] and ACC_ONLY.match(r["title"].strip())]
    note("P1" if bad else "ok", "no accession-only paper titles",
         f"{len(bad)}" + (f", e.g. {bad[:3]}" if bad else ""))

    # 7) verified 但 confidence 很低
    rows = conn.execute(
        "SELECT id, title, confidence, status FROM datasets "
        "WHERE status IN ('verified','paper_verified') AND confidence < 0.5").fetchall()
    note("P2" if rows else "ok", "verified records have confidence >= 0.5",
         f"{len(rows)} low-confidence verified")

    # 8) 动态主题的检索式
    rows = conn.execute("SELECT slug, name, facets FROM themes WHERE slug='breast-cancer'").fetchall()
    for r in rows:
        note("info", "on-demand theme facets", f"{r['slug']}: {r['facets'][:200]}")

for sev, title, detail in findings:
    mark = {"P0": "P0!!", "P1": "P1! ", "P2": "P2  ", "ok": "ok  ", "info": "info"}[sev]
    print(f"[{mark}] {title}: {detail}")
p0 = sum(1 for f in findings if f[0] == "P0")
print(f"\nP0 = {p0}")
