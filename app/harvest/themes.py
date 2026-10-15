"""主题定义与种子：首发 Alzheimer's disease。"""

from __future__ import annotations

import json
import re

from app.core.db import db, utcnow
from app.core.utils import new_id

# 首发主题（用户已确认：先只做阿尔茨海默）
THEME_SEEDS = [
    {
        "slug": "alzheimers-disease",
        "name": "Alzheimer's Disease & Related Dementias",
        "description": (
            "Papers reporting public omics datasets on Alzheimer's disease and related "
            "dementias (AD, MCI, FTD, LBD, vascular dementia). Records come from "
            "paper abstracts, Data Availability statements and supplementary files; "
            "each record carries the actual data repository link and paper evidence."
        ),
        "facets": {
            # HAS_DATA:Y = 论文在欧洲PMC登记了数据链接/数据集号（命中率远高于纯关键词）
            "base_queries": [
                '"Alzheimer" AND HAS_DATA:Y',
                '"Alzheimer disease" AND HAS_DATA:Y',
                '"dementia" AND HAS_DATA:Y',
                '"mild cognitive impairment" AND HAS_DATA:Y',
                '"amyloid-beta" AND HAS_DATA:Y',
                '"tau protein" AND HAS_DATA:Y',
                '"frontotemporal dementia" AND HAS_DATA:Y',
                '"Lewy body disease" AND HAS_DATA:Y',
                '"single-nucleus" AND Alzheimer AND HAS_DATA:Y',
                '"single-cell" AND Alzheimer AND HAS_DATA:Y',
                '"spatial transcriptomics" AND Alzheimer AND HAS_DATA:Y',
                '"proteomics" AND Alzheimer AND HAS_DATA:Y',
                '"metabolomics" AND Alzheimer AND HAS_DATA:Y',
            ],
            "modalities": [
                "single-cell RNA-seq", "single-nucleus RNA-seq", "spatial transcriptomics",
                "proteomics", "phosphoproteomics", "metabolomics", "lipidomics",
                "methylation", "genomics", "multi-omics",
            ],
            "organisms": ["Homo sapiens", "Mus musculus"],
        },
        "target_records": 1000,
        "priority": 1,
    },
    {
        "slug": "als",
        "name": "Amyotrophic Lateral Sclerosis (ALS)",
        "description": (
            "Papers reporting public omics datasets on amyotrophic lateral sclerosis "
            "(ALS/MND, incl. ALS-FTD spectrum). Records come from paper abstracts, "
            "Data Availability statements and supplementary files; each record carries "
            "the actual data repository link and paper evidence."
        ),
        "facets": {
            "base_queries": [
                '"amyotrophic lateral sclerosis" AND HAS_DATA:Y',
                '"ALS" AND HAS_DATA:Y',
                '"motor neuron disease" AND HAS_DATA:Y',
                '"frontotemporal dementia" AND ALS AND HAS_DATA:Y',
                '"SOD1" AND HAS_DATA:Y',
                '"TDP-43" AND HAS_DATA:Y',
                '"C9orf72" AND HAS_DATA:Y',
            ],
            "modalities": [
                "single-cell RNA-seq", "single-nucleus RNA-seq", "spatial transcriptomics",
                "proteomics", "metabolomics", "genomics", "methylation", "multi-omics",
            ],
            "organisms": ["Homo sapiens", "Mus musculus"],
        },
        "target_records": 1000,
        "priority": 2,
    },
    {
        "slug": "parkinsons-disease",
        "name": "Parkinson's Disease (PD)",
        "description": (
            "Papers reporting public omics datasets on Parkinson's disease (incl. PD "
            "dementia and Lewy body disease). Records come from paper abstracts, Data "
            "Availability statements and supplementary files; each record carries the "
            "actual data repository link and paper evidence."
        ),
        "facets": {
            "base_queries": [
                '"Parkinson" AND HAS_DATA:Y',
                '"Parkinson disease" AND HAS_DATA:Y',
                '"Parkinson\'s disease" AND HAS_DATA:Y',
                '"Lewy body" AND HAS_DATA:Y',
                '"alpha-synuclein" AND HAS_DATA:Y',
                '"LRRK2" AND HAS_DATA:Y',
                '"GBA" AND Parkinson AND HAS_DATA:Y',
            ],
            "modalities": [
                "single-cell RNA-seq", "single-nucleus RNA-seq", "spatial transcriptomics",
                "proteomics", "metabolomics", "genomics", "methylation", "multi-omics",
            ],
            "organisms": ["Homo sapiens", "Mus musculus"],
        },
        "target_records": 1000,
        "priority": 3,
    },
    # ---- Story A: proteinopathies (misfolding/aggregation across neurodegeneration) ----
    {
        "slug": "frontotemporal-dementia",
        "name": "Frontotemporal Dementia (FTD)",
        "description": (
            "Public omics datasets on frontotemporal dementia / frontotemporal lobar "
            "degeneration (tau and TDP-43 proteinopathies; the AD-ALS bridge)."
        ),
        "facets": {
            "base_queries": [
                '"frontotemporal dementia" AND HAS_DATA:Y',
                '"frontotemporal lobar degeneration" AND HAS_DATA:Y',
                '"FTD" AND "TDP-43" AND HAS_DATA:Y',
                '"MAPT" AND frontotemporal AND HAS_DATA:Y',
                '"GRN" AND frontotemporal AND HAS_DATA:Y',
                '"C9orf72" AND HAS_DATA:Y',
                '"TDP-43" AND HAS_DATA:Y',
            ],
            "modalities": [
                "single-cell RNA-seq", "single-nucleus RNA-seq", "spatial transcriptomics",
                "proteomics", "metabolomics", "genomics", "methylation", "multi-omics",
            ],
            "organisms": ["Homo sapiens", "Mus musculus"],
        },
        "target_records": 1000,
        "priority": 4,
    },
    {
        "slug": "huntingtons-disease",
        "name": "Huntington's Disease (HD)",
        "description": (
            "Public omics datasets on Huntington's disease (huntingtin / polyglutamine "
            "proteinopathy)."
        ),
        "facets": {
            "base_queries": [
                '"Huntington disease" AND HAS_DATA:Y',
                '"Huntington\'s disease" AND HAS_DATA:Y',
                '"huntingtin" AND HAS_DATA:Y',
                '"HTT" AND Huntington AND HAS_DATA:Y',
                '"polyglutamine" AND HAS_DATA:Y',
                '"CAG repeat" AND HAS_DATA:Y',
            ],
            "modalities": [
                "single-cell RNA-seq", "single-nucleus RNA-seq", "spatial transcriptomics",
                "proteomics", "metabolomics", "genomics", "methylation", "multi-omics",
            ],
            "organisms": ["Homo sapiens", "Mus musculus"],
        },
        "target_records": 1000,
        "priority": 5,
    },
    {
        "slug": "prion-disease",
        "name": "Prion Disease / Creutzfeldt-Jakob Disease (CJD)",
        "description": (
            "Public omics datasets on prion disease and Creutzfeldt-Jakob disease "
            "(prion protein, PrP)."
        ),
        "facets": {
            "base_queries": [
                '"Creutzfeldt-Jakob" AND HAS_DATA:Y',
                '"prion disease" AND HAS_DATA:Y',
                '"prion protein" AND HAS_DATA:Y',
                '"PrP" AND prion AND HAS_DATA:Y',
            ],
            "modalities": [
                "single-cell RNA-seq", "single-nucleus RNA-seq", "spatial transcriptomics",
                "proteomics", "metabolomics", "genomics", "methylation", "multi-omics",
            ],
            "organisms": ["Homo sapiens", "Mus musculus"],
        },
        "target_records": 1000,
        "priority": 6,
    },
    {
        "slug": "progressive-supranuclear-palsy",
        "name": "Progressive Supranuclear Palsy (PSP)",
        "description": (
            "Public omics datasets on progressive supranuclear palsy and related "
            "tauopathies (PSP, CBD)."
        ),
        "facets": {
            "base_queries": [
                '"progressive supranuclear palsy" AND HAS_DATA:Y',
                '"PSP" AND tau AND HAS_DATA:Y',
                '"corticobasal degeneration" AND HAS_DATA:Y',
                '"tauopathy" AND HAS_DATA:Y',
            ],
            "modalities": [
                "single-cell RNA-seq", "single-nucleus RNA-seq", "spatial transcriptomics",
                "proteomics", "metabolomics", "genomics", "methylation", "multi-omics",
            ],
            "organisms": ["Homo sapiens", "Mus musculus"],
        },
        "target_records": 1000,
        "priority": 7,
    },
    {
        "slug": "dementia-with-lewy-bodies",
        "name": "Dementia with Lewy Bodies (DLB)",
        "description": (
            "Public omics datasets on dementia with Lewy bodies / Lewy body dementia "
            "(alpha-synucleinopathy)."
        ),
        "facets": {
            "base_queries": [
                '"dementia with Lewy bodies" AND HAS_DATA:Y',
                '"Lewy body dementia" AND HAS_DATA:Y',
                '"Lewy body disease" AND HAS_DATA:Y',
                '"alpha-synuclein" AND "Lewy" AND HAS_DATA:Y',
            ],
            "modalities": [
                "single-cell RNA-seq", "single-nucleus RNA-seq", "spatial transcriptomics",
                "proteomics", "metabolomics", "genomics", "methylation", "multi-omics",
            ],
            "organisms": ["Homo sapiens", "Mus musculus"],
        },
        "target_records": 1000,
        "priority": 8,
    },
    {
        "slug": "multiple-system-atrophy",
        "name": "Multiple System Atrophy (MSA)",
        "description": (
            "Public omics datasets on multiple system atrophy (alpha-synucleinopathy "
            "with glial cytoplasmic inclusions)."
        ),
        "facets": {
            "base_queries": [
                '"multiple system atrophy" AND HAS_DATA:Y',
                '"MSA" AND "alpha-synuclein" AND HAS_DATA:Y',
                '"glial cytoplasmic inclusions" AND HAS_DATA:Y',
            ],
            "modalities": [
                "single-cell RNA-seq", "single-nucleus RNA-seq", "spatial transcriptomics",
                "proteomics", "metabolomics", "genomics", "methylation", "multi-omics",
            ],
            "organisms": ["Homo sapiens", "Mus musculus"],
        },
        "target_records": 1000,
        "priority": 9,
    },
    {
        "slug": "spinocerebellar-ataxia",
        "name": "Spinocerebellar Ataxia (SCA)",
        "description": (
            "Public omics datasets on spinocerebellar ataxia and related polyglutamine "
            "ataxias (ataxin proteins)."
        ),
        "facets": {
            "base_queries": [
                '"spinocerebellar ataxia" AND HAS_DATA:Y',
                '"ataxia" AND (ataxin OR polyglutamine) AND HAS_DATA:Y',
                '"SCA" AND ataxia AND HAS_DATA:Y',
            ],
            "modalities": [
                "single-cell RNA-seq", "single-nucleus RNA-seq", "spatial transcriptomics",
                "proteomics", "metabolomics", "genomics", "methylation", "multi-omics",
            ],
            "organisms": ["Homo sapiens", "Mus musculus"],
        },
        "target_records": 1000,
        "priority": 10,
    },
]


def seed_themes(force: bool = False, *, allow_new: bool | None = None) -> None:
    """写入主题种子（不存在时）。

    L-0225：新库默认**不播种**（空部署 = 纯空白；`seed_starter_themes=0`）；已存在行仍可由
    `force` 刷新 facets。`allow_new` 供测试/运维显式覆盖配置。
    """
    from app.core.config import cfg
    if allow_new is None:
        allow_new = cfg.bool("seed_starter_themes", False)
    now = utcnow()
    with db() as conn:
        for t in THEME_SEEDS:
            row = conn.execute("SELECT id FROM themes WHERE slug = ?", (t["slug"],)).fetchone()
            if row is None:
                if not allow_new:
                    continue
                conn.execute(
                    "INSERT INTO themes(id, slug, name, description, status, priority, facets,"
                    " target_records, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (new_id("theme"), t["slug"], t["name"], t["description"], "active",
                     t["priority"], json.dumps(t["facets"], ensure_ascii=False),
                     t["target_records"], now),
                )
            elif force:
                conn.execute(
                    "UPDATE themes SET name=?, description=?, facets=?, target_records=? WHERE id=?",
                    (t["name"], t["description"], json.dumps(t["facets"], ensure_ascii=False),
                     t["target_records"], row["id"]),
                )


def get_theme(slug: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM themes WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["facets"] = json.loads(out.get("facets") or "{}") if isinstance(out.get("facets"), str) else (out.get("facets") or {})
    return out


def list_themes(kinds: tuple[str, ...] | None = None,
                include_hidden: bool = False) -> list[dict]:
    """主题清单。`kinds` 非空时按 `kind` 过滤（公共目录传 ("disease",)；L-0223）。

    L-0250：隐藏主题默认过滤（`include_hidden=True` 供 My mining 管理区）。
    """
    where: list[str] = []
    args: list = []
    if kinds:
        marks = ",".join("?" * len(kinds))
        where.append(f"kind IN ({marks})")
        args.extend(kinds)
    if not include_hidden:
        where.append("hidden=0")
    sql = "SELECT * FROM themes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY priority, name"
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
        # 实时计数（按多主题归属），避免依赖矿机退出时更新的陈旧值
        live = {r["theme"]: r["n"] for r in conn.execute(
            "SELECT pt.theme, COUNT(DISTINCT dp.paper_id) n FROM paper_themes pt "
            "JOIN dataset_papers dp ON dp.paper_id = pt.paper_id GROUP BY pt.theme")}
    out = []
    for r in rows:
        d = dict(r)
        d["facets"] = json.loads(d.get("facets") or "{}") if isinstance(d.get("facets"), str) else (d.get("facets") or {})
        d["records_count"] = live.get(d["slug"], 0)
        out.append(d)
    return out


def rename_theme(slug: str, name: str, description: str | None = None) -> dict:
    """L-0250：改主题**显示名**（slug 不动，零级联）；description 传 None 表示不改。"""
    name = (name or "").strip()[:120]
    if not name:
        return {"ok": False, "reason": "empty_name", "message": "new name required"}
    with db() as conn:
        row = conn.execute("SELECT id, name FROM themes WHERE slug=?", (slug,)).fetchone()
        if row is None:
            return {"ok": False, "reason": "not_found", "message": "theme not found"}
        old_name = row["name"] or ""
        if description is None:
            conn.execute("UPDATE themes SET name=? WHERE slug=?", (name, slug))
        else:
            conn.execute("UPDATE themes SET name=?, description=? WHERE slug=?",
                         (name, str(description).strip()[:500], slug))
        if old_name and old_name != name:
            # L-0259 / C-R54-4：自动标题的任务卡同步新名（自定义标题不动）
            conn.execute("UPDATE mining_requests SET title=? WHERE theme=? AND title=?",
                         (name, slug, old_name))
    return {"ok": True, "slug": slug, "name": name}


def set_hidden(slug: str, hidden: bool) -> dict:
    """L-0250：隐藏/恢复主题（软删；从公共列表过滤，可在 My mining 恢复）。"""
    with db() as conn:
        row = conn.execute("SELECT id FROM themes WHERE slug=?", (slug,)).fetchone()
        if row is None:
            return {"ok": False, "reason": "not_found", "message": "theme not found"}
        conn.execute("UPDATE themes SET hidden=? WHERE slug=?",
                     (1 if hidden else 0, slug))
    return {"ok": True, "slug": slug, "hidden": bool(hidden)}


def is_hidden(slug: str) -> bool:
    t = get_theme(slug)
    return bool(t and t.get("hidden"))


def slugify(name: str) -> str:
    """主题名 → slug（"Alzheimer's disease" → "alzheimers-disease"）。

    L-0223：纯非拉丁主题名 → `topic-<hash>` 兜底；
    L-0225（修复 F5）：含 CJK 的名称在拉丁片段后追加 hash（防 `阿尔茨海默病 AD` → `ad` 误复用）。
    """
    s = (name or "").lower().replace("'", "").replace("\u2019", "")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    base = (name or "").strip()
    has_cjk = bool(re.search(r"[\u3400-\u9fff\uf900-\ufaff]", base))
    if s and not has_cjk:
        return s
    if base:
        import hashlib
        h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
        return f"{s}-{h}" if s else f"topic-{h}"
    return "disease"


def theme_options(kinds: tuple[str, ...] | None = None) -> list[dict]:
    """给对话提示词用的已有主题清单。"""
    return [{"slug": t["slug"], "name": t["name"]} for t in list_themes(kinds=kinds)]


def default_queries(name: str, aliases: list[str] | None = None) -> list[str]:
    """给任意疾病生成一组 Europe PMC 检索式（都带 AND HAS_DATA:Y）。"""
    names = [name] + [a for a in (aliases or []) if a]
    qs = [f'"{n}" AND HAS_DATA:Y' for n in names[:3]]
    for n in names[:2]:
        qs.append(f'"{n}" AND ("single-cell" OR "single-nucleus" OR "spatial") AND HAS_DATA:Y')
    if name:
        qs.append(f'"{name}" AND (proteomics OR transcriptomics OR genomics) AND HAS_DATA:Y')
    return list(dict.fromkeys(qs))


def ensure_theme(slug: str, name: str, *, kind: str = "disease",
                 queries: list[str] | None = None, aliases: list[str] | None = None,
                 origin: str = "on_demand", target_records: int = 200,
                 priority: int | None = None) -> dict:
    """按需建主题（疾病或任意研究主题；已存在则直接返回——L-0223 泛化自 ensure_disease_theme）。"""
    existing = get_theme(slug)
    if existing:
        return existing
    name = (name or slug).strip()
    if name[:1].islower():
        name = name[:1].upper() + name[1:]
    qs = [q for q in (queries or []) if q]
    if not qs:
        qs = [f'"{name}" AND HAS_DATA:Y']
    if priority is None:
        priority = 90 if kind == "topic" else 50
    desc = (f"On-demand topic: omics datasets announced in papers about {name} (user-created "
            "theme; mined records publish to the catalog by default)." if kind == "topic"
            else f"On-demand theme: public omics datasets on {name}, created from a chat "
                 "mining order.")
    facets = {
        "base_queries": qs,
        "modalities": [
            "single-cell RNA-seq", "single-nucleus RNA-seq", "spatial transcriptomics",
            "proteomics", "phosphoproteomics", "metabolomics", "lipidomics",
            "methylation", "genomics", "multi-omics",
        ],
        "organisms": ["Homo sapiens", "Mus musculus"],
        "origin": origin,
        "aliases": [a for a in (aliases or []) if a],
    }
    with db() as conn:
        row = conn.execute("SELECT id FROM themes WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO themes(id, slug, name, description, status, kind, priority, facets,"
                " target_records, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (new_id("theme"), slug, name, desc, "active", kind, priority,
                 json.dumps(facets, ensure_ascii=False), target_records, utcnow()),
            )
    return get_theme(slug) or {"slug": slug, "name": name, "kind": kind, "facets": facets}


def ensure_disease_theme(slug: str, name: str, *, queries: list[str] | None = None,
                         aliases: list[str] | None = None, origin: str = "on_demand",
                         target_records: int = 200, priority: int = 50) -> dict:
    """按需为某个疾病建主题（已存在则直接返回）。这是"对话下单任意疾病"的落点。"""
    return ensure_theme(slug, name, kind="disease", queries=queries, aliases=aliases,
                        origin=origin, target_records=target_records, priority=priority)


def ensure_topic_theme(slug: str, name: str, *, queries: list[str] | None = None,
                       aliases: list[str] | None = None, origin: str = "mining_panel",
                       target_records: int = 200) -> dict:
    """按需为任意研究主题建主题（L-0223/0225：用户挖掘任意主题；低优先级、公开可见）。"""
    return ensure_theme(slug, name, kind="topic", queries=queries, aliases=aliases,
                        origin=origin, target_records=target_records)