"""三级核验：
T1 论文内证 —— 标识在 abstract / DA 原文逐字出现（extract 已保证）+ LLM 判定。
T2 地址可达 —— 规范链接可达；GEO/ENA/PRIDE/MetaboLights/BioStudies/Zenodo 再回读元数据。
T3 深度核验 —— 可选（复用 omics-discovery 的 SOFT/清单核验）。
"""

from __future__ import annotations

import logging

from app.core import utils, vocab

logger = logging.getLogger(__name__)

from app.core.config import cfg

# 核验加权（键 = 检查项名；文件真源 config/bioagentdb.example.toml 的 [verify] weights）
WEIGHTS = cfg.jsonv("verify_weights",
                    {"t1_paper_grounding": 1.5, "t2_link_reachable": 1.0,
                     "t2_source_metadata": 1.5})


def _w(name: str, default: float) -> float:
    try:
        return float((WEIGHTS or {}).get(name, default))
    except (TypeError, ValueError):
        return default


# 未知权重键告警（L-0212 修复 P3：拼错的子键不再静默回退）
if isinstance(WEIGHTS, dict):
    _UNKNOWN_W = sorted(set(WEIGHTS) - {"t1_paper_grounding", "t2_link_reachable",
                                        "t2_source_metadata"})
    if _UNKNOWN_W:
        logger.warning("verify_weights has unknown keys (ignored): %s", ", ".join(_UNKNOWN_W))


def metadata_lookup(acc: str) -> dict:
    """按登记号回读仓储元数据（存在性 + 字段补全）。返回 {ok, title, organism,
    sample_count, platform, detail}。失败/未知源也返回 ok=False。"""
    low = acc.lower()
    try:
        if acc.startswith("GSE") or acc.startswith("E-GEOD"):
            data = utils.get_json(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                params={"db": "gds", "id": acc, "retmode": "json", "version": "2.0"},
                retries=1)
            entry = ((data or {}).get("result") or {}).get(acc)
            if entry:
                return {"ok": True, "title": entry.get("title", ""),
                        "organism": entry.get("taxon", ""),
                        "sample_count": _num(entry.get("n_samples")),
                        "platform": _join(entry.get("gpl")),
                        "detail": "NCBI esummary OK"}
        elif acc.startswith(("PRJ", "SRP", "ERP", "DRP")):
            data = utils.get_json(
                "https://www.ebi.ac.uk/ena/portal/api/search",
                params={"result": "study", "query": f'study_accession="{acc}"',
                        "fields": "study_accession,study_title,tax_id,scientific_name",
                        "format": "json", "limit": "1"}, retries=1)
            if data:
                row = data[0] if isinstance(data, list) and data else {}
                if row.get("study_accession"):
                    return {"ok": True, "title": row.get("study_title", ""),
                            "organism": row.get("scientific_name", ""),
                            "sample_count": 0, "platform": "",
                            "detail": "ENA portal OK"}
        elif acc.startswith("PXD"):
            data = utils.get_json(
                f"https://www.ebi.ac.uk/pride/ws/archive/v2/projects/{acc}", retries=1)
            if isinstance(data, dict) and data.get("accession") == acc:
                return {"ok": True, "title": data.get("title", ""),
                        "organism": _join([(o or {}).get("name") for o in (data.get("organisms") or [])]),
                        "sample_count": _num(data.get("numberOfAssays") or data.get("sampleProcessingProtocol")),
                        "platform": "", "detail": "PRIDE API OK"}
        elif acc.startswith(("E-MTAB", "E-GEOD", "E-MEXP", "E-TABM", "S-BSST")):
            data = utils.get_json(f"https://www.ebi.ac.uk/biostudies/api/v1/studies/{acc}", retries=1)
            if isinstance(data, dict) and data.get("accno"):
                return {"ok": True, "title": data.get("title", ""),
                        "organism": "", "sample_count": 0, "platform": "",
                        "detail": "BioStudies OK"}
        elif acc.startswith("MTBLS"):
            data = utils.get_json(
                "https://www.ebi.ac.uk/ebisearch/ws/rest/metabolights",
                params={"query": f'id:"{acc}"', "format": "json", "size": "1"}, retries=1)
            if data and int(data.get("hitCount", 0) or 0) > 0:
                return {"ok": True, "title": "", "organism": "", "sample_count": 0,
                        "platform": "", "detail": "MetaboLights(EBI Search) OK"}
        elif low.startswith("zenodo.") or acc.isdigit():
            rid = acc.split(".")[-1]
            data = utils.get_json(f"https://zenodo.org/api/records/{rid}", retries=1)
            if isinstance(data, dict) and data.get("id"):
                meta = (data.get("metadata") or {})
                return {"ok": True, "title": meta.get("title", ""),
                        "organism": "", "sample_count": 0, "platform": "",
                        "detail": "Zenodo API OK"}
        # ftp.ncbi.nlm.nih.gov 系列（GEO suppl 目录）也常被护栏拦；用 ESL 检测即可
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "title": "", "organism": "", "sample_count": 0,
                "platform": "", "detail": f"lookup error: {str(exc)[:120]}"}
    return {"ok": False, "title": "", "organism": "", "sample_count": 0,
            "platform": "", "detail": "no metadata API for this identifier"}


def _num(value) -> int | None:
    return utils.parse_int(value)


def _join(values) -> str:
    import re
    if not values:
        return ""
    if isinstance(values, str):
        return utils.norm_space(values)
    seen = []
    for v in values:
        s = utils.norm_space(v)
        if s and s not in seen:
            seen.append(s)
    return " / ".join(seen[:5])


def gate_reject_reason(result: dict) -> str:
    """门拒（T1/T2）原因单行摘要（L-0299/L-0302）：拒绝必须留下可读理由。

    此前 paperflow/runner 两条路径都写空 reason（审计发现大量无理由 rejected）。
    """
    reason = str(result.get("reason") or "").strip()
    if reason:
        return reason
    fails = [c for c in (result.get("checks") or []) if not c.get("ok")]
    if not fails:
        return "gate rejected: verification failed"
    parts = [f"{c.get('name')}: {str(c.get('detail') or '')[:120]}" for c in fails[:2]]
    return " ".join(("gate rejected: " + " | ".join(parts)).split())


def verify_record(record: dict, *, abstract: str = "", da_text: str = "",
                  llm_chat=None) -> dict:
    """T1+T2 核验一条记录。返回 {status, confidence, checks, meta}。

    status: verified | paper_verified | rejected
    """
    checks: list[dict] = []
    acc = record["identifier"]

    # ---- T1 论文内证 ----
    quote = (record.get("evidence") or {}).get("quote", "")
    zone = (record.get("evidence") or {}).get("zone", "")
    grounded = bool(quote and utils.acc_in_text(acc, quote)) or bool(
        (abstract and utils.acc_in_text(acc, abstract))
        or (da_text and utils.acc_in_text(acc, da_text)))
    checks.append({"name": "t1_paper_grounding", "ok": grounded,
                   "detail": f"zone={zone}; quote: {quote[:180]}",
                   "weight": _w("t1_paper_grounding", 1.5)})
    on_request = str(record.get("role") or "") == ""  # extract 已过滤 on_request
    relevant = record.get("relevant", True)

    # ---- T2 地址可达 + 元数据回读 ----
    reachable, http_detail = utils.http_status(record.get("url", ""))
    checks.append({"name": "t2_link_reachable", "ok": reachable,
                   "detail": http_detail, "weight": _w("t2_link_reachable", 1.0)})
    meta = metadata_lookup(acc)
    if meta.get("ok"):
        checks.append({"name": "t2_source_metadata", "ok": True,
                       "detail": meta.get("detail", ""),
                       "weight": _w("t2_source_metadata", 1.5)})
        if meta.get("title"):
            record.setdefault("title", meta["title"])
        if not record.get("organism") and meta.get("organism"):
            record["organism"] = meta["organism"]
        if not record.get("sample_count") and meta.get("sample_count"):
            record["sample_count"] = meta["sample_count"]
        if not record.get("platform") and meta.get("platform"):
            record["platform"] = meta["platform"]
    else:
        checks.append({"name": "t2_source_metadata", "ok": reachable,
                       "detail": meta.get("detail", "") or "metadata API unavailable",
                       "weight": _w("t2_source_metadata", 1.5)})

    # ---- 汇总 ----
    t2_ok = reachable or meta.get("ok")
    t1_ok = grounded
    out = {
        "status": "verified" if (t1_ok and t2_ok) else ("paper_verified" if t1_ok else "rejected"),
        "confidence": 0.0 if not t1_ok else round(
            (sum(c["weight"] for c in checks if c["ok"]) / (sum(c["weight"] for c in checks) or 1.0)), 3),
        "checks": checks,
        "meta": meta,
        "reason": "",
        "link_reachable": reachable,
        "http_status": http_detail,
        "t1_ok": t1_ok,
        "t2_ok": t2_ok,
    }
    if out["status"] == "rejected":
        out["reason"] = gate_reject_reason({"checks": checks})
    return out