"""M1 · 提议生成：把"未识别 token 簇"变成可评的模式提议。

M1 只做确定性部分（正则推断 + 仓库前缀猜测）；URL 模板留空，待 M1b 用探测补。
"""

from __future__ import annotations

PREFIX_REPO = {
    "GCST": "GWASCatalog", "NG": "NIAGADS", "SCP": "SingleCellPortal", "MSV": "MassIVE",
    "PXD": "PRIDE", "JPST": "jPOST", "CRA": "NGDC-GSA", "EGAS": "EGA", "EGAD": "EGA",
    "MTBLS": "MetaboLights", "PRJNA": "ENA", "PRJEB": "ENA", "SRP": "SRA", "ERR": "SRA",
    "SRR": "SRA", "GSE": "GEO", "SAMN": "BioSample", "SAME": "BioSample", "SAMD": "BioSample",
    "E": "ArrayExpress", "GSA": "NGDC-GSA", "CNP": "CNGB", "HCA": "HCA",
}

# M1b · 已知仓库的 URL 模板（让自动模式也能产出可用链接；探测留待后续）
URL_TEMPLATES = {
    "GWASCatalog": "https://www.ebi.ac.uk/gwas/studies/{acc}",
    "Synapse": "https://www.synapse.org/#!Synapse:{acc}",
    "BioSample": "https://www.ncbi.nlm.nih.gov/biosample/{acc}",
    "NIAGADS": "https://www.niagads.org/datasets/{acc}",
    "SingleCellPortal": "https://singlecell.broadinstitute.org/single_cell/study/{acc}",
    "PRIDE": "https://www.ebi.ac.uk/pride/archive/projects/{acc}",
    "MassIVE": "https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={acc}",
    "GEO": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}",
    "SRA": "https://www.ncbi.nlm.nih.gov/sra/?term={acc}",
    "ENA": "https://www.ebi.ac.uk/ena/browser/view/{acc}",
    "jPOST": "https://jpostdb.org/entry/{acc}",
    "MetaboLights": "https://www.ebi.ac.uk/metabolights/editor/{acc}",
    "ArrayExpress": "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/{acc}",
    "NGDC-GSA": "https://ngdc.cncb.ac.cn/gsa/browse/{acc}",
    "CNGB": "https://db.cngb.org/search/project/{acc}",
    "EGA": "https://ega-archive.org/studies/{acc}",
    "dbGaP": "https://www.ncbi.nlm.nih.gov/projects/gap/cgi-bin/study.cgi?study_id={acc}",
    "HCA": "https://data.humancellatlas.org/projects/{acc}",
}


def propose_patterns(clusters: list[dict]) -> list[dict]:
    """clusters 来自 errors.unmatched_tokens()。返回 pattern 提议列表。"""
    proposals = []
    for c in clusters:
        prefix = (c.get("prefix") or "").strip()
        nd = int(c.get("digits") or 0)
        if not nd:
            continue
        if prefix == "_":      # 纯数字 → 归入 L5（上下文门控），M1 不处理
            continue
        if len(prefix) > 8:
            continue
        repo = PREFIX_REPO.get(prefix.upper(), f"Auto_{prefix.upper()}")
        lo, hi = max(3, nd - 1), nd + 1
        pattern = rf"\b{prefix}\d{{{lo},{hi}}}\b"
        proposals.append({
            "repository": repo,
            "label": repo,
            "pattern": pattern,
            "url": URL_TEMPLATES.get(repo, ""),
            "source": "auto",
            "evidence": (f"shape={c.get('shape')} n={c.get('count')} "
                         f"examples={c.get('examples', [])[:4]}"),
        })
    return proposals
