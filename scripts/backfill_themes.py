"""回填多主题归属（本地、秒级、无网络）。

1) 从 papers.theme 回填 paper_themes（幂等）
2) 按"标题+摘要"的疾病关键词，为每篇论文补所有相关主题（多主题归属）

可选（慢，默认不做）：--resweep 用 link_only 重扫各主题检索式，按"真实命中"补归属。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import db, init_db, utcnow  # noqa: E402
from app.harvest import themes as themes_mod  # noqa: E402
from app.harvest.ingest import update_theme_stats  # noqa: E402

# 主题 → 疾病关键词（与检索面一致；用于本地归属）
KEYWORDS: dict[str, str] = {
    "alzheimers-disease": r"alzheim|dementia|amyloid|\btau\b|\bmci\b|cognitive impairment|down syndrome",
    "als": r"\bals\b|amyotrophic|motor neuron|\bmnd\b|sod1|tdp-?43|c9orf72|frontotemporal",
    "parkinsons-disease": r"parkinson|lewy|synuclein|lrrk2|\bgba\b|dopamin",
    "breast-cancer": r"breast cancer|breast carcinoma|breast tumou?r|mammary carcinoma",
}


def _patterns() -> list[tuple[str, re.Pattern]]:
    pats = [(slug, re.compile(p, re.I)) for slug, p in KEYWORDS.items()]
    # 其他按需主题：用主题名做包含匹配
    for th in themes_mod.list_themes():
        if th["slug"] in KEYWORDS:
            continue
        name = (th.get("name") or "").strip()
        if name:
            pats.append((th["slug"], re.compile(re.escape(name), re.I)))
    return pats


def backfill_from_papers() -> int:
    from app.core.relevance import theme_relevant
    with db() as conn:
        rows = conn.execute(
            "SELECT id, theme, title, abstract FROM papers WHERE theme != ''").fetchall()
    now = utcnow()
    n = 0
    with db() as conn:
        for r in rows:
            if not theme_relevant(r["theme"], r["title"], r["abstract"]):
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO paper_themes(paper_id, theme, source, created_at) "
                "VALUES (?, ?, 'backfill', ?)", (r["id"], r["theme"], now))
            n += cur.rowcount
    return n


def link_by_keywords() -> int:
    pats = _patterns()
    with db() as conn:
        rows = conn.execute("SELECT id, title, abstract FROM papers").fetchall()
    linked = 0
    now = utcnow()
    with db() as conn:
        for r in rows:
            title = r["title"] or ""
            abstract = r["abstract"] or ""
            for slug, pat in pats:
                if _matches(title, abstract, pat):
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO paper_themes(paper_id, theme, source, created_at) "
                        "VALUES(?,?,?,?)", (r["id"], slug, "keyword", now))
                    linked += cur.rowcount
    return linked


def _matches(title: str, abstract: str, pat: re.Pattern) -> bool:
    """疾病词出现在标题 → 归属；否则需在摘要出现 ≥2 次（避免顺带一提）。"""
    if pat.search(title):
        return True
    return len(pat.findall(abstract)) >= 2


def reset_keyword_links() -> int:
    with db() as conn:
        cur = conn.execute("DELETE FROM paper_themes WHERE source='keyword'")
        return cur.rowcount


def attribute_empty() -> int:
    """空主题且有数据的论文：至少给一个主题。"""
    pats = _patterns()
    with db() as conn:
        rows = conn.execute(
            "SELECT id, title, abstract FROM papers WHERE theme='' "
            "AND EXISTS (SELECT 1 FROM dataset_papers dp WHERE dp.paper_id=papers.id)").fetchall()
    n = 0
    now = utcnow()
    for r in rows:
        title, abstract = r["title"] or "", r["abstract"] or ""
        for slug, pat in pats:
            if _matches(title, abstract, pat):
                with db() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO paper_themes(paper_id, theme, source, created_at) "
                        "VALUES(?,?,?,?)", (r["id"], slug, "keyword", now))
                    conn.execute("UPDATE papers SET theme=?, updated_at=? WHERE id=?",
                                 (slug, now, r["id"]))
                n += 1
                break
    return n


def resweep(max_pages: int = 15) -> int:
    from app.harvest.sweep import sweep_papers
    total = 0
    for th in themes_mod.list_themes():
        for q in (th.get("facets") or {}).get("base_queries", []):
            try:
                out = sweep_papers(q, max_papers=10 ** 9, theme=th["slug"],
                                   link_only=True, max_pages=max_pages)
                total += out["linked"]
            except Exception as exc:  # noqa: BLE001
                print(f"  skip {q[:50]}: {exc}")
    return total


if __name__ == "__main__":
    init_db()
    if "--reset" in sys.argv:
        print("0) reset keyword links:", reset_keyword_links())
    print("1) backfill from papers.theme:", backfill_from_papers())
    print("2) keyword-linked memberships:", link_by_keywords())
    print("3) empty-theme papers attributed:", attribute_empty())
    if "--resweep" in sys.argv:
        print("4) link-only re-sweep:", resweep())
    for th in themes_mod.list_themes():
        update_theme_stats(th["slug"])
    with db() as conn:
        rows = conn.execute(
            "SELECT pt.theme, COUNT(DISTINCT pt.paper_id) papers FROM paper_themes pt "
            "GROUP BY pt.theme ORDER BY papers DESC").fetchall()
        multi = conn.execute(
            "SELECT COUNT(*) FROM (SELECT paper_id FROM paper_themes GROUP BY paper_id "
            "HAVING COUNT(*) > 1)").fetchone()[0]
    print("memberships per theme:")
    for r in rows:
        print(f"  {r['theme']:<20} {r['papers']}")
    print(f"papers in >1 theme: {multi}")
