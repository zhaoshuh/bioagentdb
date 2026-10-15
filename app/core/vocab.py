"""领域词表：仓储 → 规范链接（登记号 → 记录页/下载页）、模态、ALZ 检索面。"""

from __future__ import annotations

import re

from app.core.utils import norm_space

# ---------------------------------------------------------------- 仓储链接模板
# 用登记号拼出"数据实际地址"（记录页 + 可选下载入口）。T2 核验用。

REPOSITORIES: dict[str, dict] = {
    "GEO": {
        "label": "NCBI GEO",
        "patterns": (r"\bGSE\d{3,8}\b",),
        "url": lambda acc: f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}",
        "download": lambda acc: f"https://ftp.ncbi.nlm.nih.gov/geo/series/{acc[:-3]}nnn/{acc}/",
    },
    "SRA": {
        "label": "NCBI SRA / ENA",
        "patterns": (r"\b(?:SRP|SRR|SRX|SRS|ERP|ERR|ERX|ERS|DRP|DRR)\d{4,9}\b",),
        "url": lambda acc: f"https://www.ncbi.nlm.nih.gov/sra/?term={acc}",
        "download": lambda acc: f"https://www.ebi.ac.uk/ena/browser/view/{acc}",
    },
    "ENA": {
        "label": "EBI ENA",
        "patterns": (r"\bPRJ[EDN][A-Z]\d{4,9}\b",),
        "url": lambda acc: f"https://www.ebi.ac.uk/ena/browser/view/{acc}",
        "download": "",
    },
    "PRIDE": {
        "label": "EBI PRIDE",
        "patterns": (r"\bPXD\d{4,9}\b",),
        "url": lambda acc: f"https://www.ebi.ac.uk/pride/archive/projects/{acc}",
        "download": "",
    },
    "GWASCatalog": {
        "label": "GWAS Catalog",
        "patterns": (r"\bGCST\d{6,9}\b",),
        "url": lambda acc: f"https://www.ebi.ac.uk/gwas/studies/{acc}",
        "download": "",
    },
    "Synapse": {
        "label": "Synapse",
        "patterns": (r"\bsyn\d{7,10}\b",),
        "url": lambda acc: f"https://www.synapse.org/#!Synapse:{acc}",
        "download": "",
    },
    "BioSample": {
        "label": "NCBI BioSample",
        "patterns": (r"\bSAM[NED][A-Z]?\d{6,9}\b",),
        "url": lambda acc: f"https://www.ncbi.nlm.nih.gov/biosample/{acc}",
        "download": "",
    },
    "jPOST": {
        "label": "jPOST",
        "patterns": (r"\bJPST\d{5,7}\b",),
        "url": lambda acc: f"https://jpostdb.org/entry/{acc}",
        "download": "",
    },
    "MassIVE": {
        "label": "MassIVE",
        "patterns": (r"\bMSV\d{5,9}\b",),
        "url": lambda acc: f"https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={acc}",
        "download": "",
    },
    "MetaboLights": {
        "label": "EBI MetaboLights",
        "patterns": (r"\bMTBLS\d{1,6}\b",),
        "url": lambda acc: f"https://www.ebi.ac.uk/metabolights/editor/{acc}",
        "download": "",
    },
    "BioStudies": {
        "label": "EBI BioStudies",
        "patterns": (r"\b(?:E-MTAB|E-GEOD|E-MEXP|E-TABM|E-ERAD)-\d{2,7}\b",
                     r"\bS-[A-Z]{4}-\d{3,6}(?:-\d+)?\b"),
        "url": lambda acc: f"https://www.ebi.ac.uk/biostudies/api/v1/studies/{acc}",
        "download": "",
    },
    "ArrayExpress": {
        "label": "EBI ArrayExpress",
        "patterns": (r"\b(?:E-MTAB|E-GEOD|E-MEXP|E-TABM|E-ERAD)-\d{2,7}\b",),
        "url": lambda acc: f"https://www.ebi.ac.uk/biostudies/arrayexpress/studies/{acc}",
        "download": "",
    },
    "Zenodo": {
        "label": "Zenodo",
        "patterns": (r"\b(?:10\.5281/zenodo\.\d+|zenodo\.\d+)\b",),
        "url": lambda acc: f"https://zenodo.org/record/{acc.split('.')[-1]}",
        "download": "",
    },
    "Figshare": {
        "label": "Figshare",
        "patterns": (r"\bfigshare\.com/s/[0-9a-f]{16,}\b",
                     r"\b10\.6084/m9\.figshare\.\d{5,9}\b"),
        "url": lambda acc: f"https://{acc}" if acc.lower().startswith("figshare")
        else f"https://doi.org/{acc}",
        "download": "",
    },
    "ProtocolsIO": {
        "label": "protocols.io",
        "patterns": (r"\b10\.17504/protocols\.io\.[a-z0-9]+\b",),
        "url": lambda acc: f"https://doi.org/{acc}",
        "download": "",
    },
    "Dryad": {
        "label": "Dryad",
        "patterns": (r"\b(?:10\.5061/dryad\.[0-9a-z]+)\b",),
        "url": lambda acc: f"https://doi.org/{acc}",
        "download": "",
    },
    "GigaDB": {
        "label": "GigaScience GigaDB",
        "patterns": (r"\b(?:10\.5524/\d+)\b",),
        "url": lambda acc: f"https://doi.org/{acc}",
        "download": "",
    },
    "dbGaP": {
        "label": "NCBI dbGaP",
        "patterns": (r"\bphs\d{6}\b",),
        "url": lambda acc: f"https://www.ncbi.nlm.nih.gov/projects/gap/cgi-bin/study.cgi?study_id={acc}",
        "download": "",
    },
    "CNGB": {
        "label": "CNGB Sequence Archive",
        "patterns": (r"\bCNP\d{6,}\b",),
        "url": lambda acc: f"https://db.cngb.org/search/project/{acc}",
        "download": "",
    },
    "NGDC-GSA": {
        "label": "NGDC GSA",
        "patterns": (r"\b(?:GSA\d{6,}|CRA\d{6,}|PRJCA\d{6,})\b",),
        "url": lambda acc: f"https://ngdc.cncb.ac.cn/gsa/browse/{acc}",
        "download": "",
    },
    "HCA": {
        "label": "Human Cell Atlas",
        "patterns": (r"\bHCA\d{4,}\b",),
        "url": lambda acc: f"https://data.humancellatlas.org/projects/{acc}",
        "download": "",
    },
    "EGA": {
        "label": "European Genome-phenome Archive",
        "patterns": (r"\bEGA[SDN]\d{8,14}\b",),
        "url": lambda acc: f"https://ega-archive.org/studies/{acc}",
        "download": "",
    },
    "SingleCellPortal": {
        "label": "Broad Single Cell Portal",
        "patterns": (r"\bSCP\d{3,6}\b",),
        "url": lambda acc: f"https://singlecell.broadinstitute.org/single_cell/study/{acc}",
        "download": "",
    },
    "NIAGADS": {
        "label": "NIAGADS",
        "patterns": (r"\bNG\d{5}\b",),
        "url": lambda acc: f"https://www.niagads.org/datasets/{acc}",
        "download": "",
    },
    "PRIDE-DOI": {
        "label": "PRIDE (DOI form)",
        "patterns": (r"\b10\.6019/[Pp][Xx][Dd]\d{5,9}\b",),
        "url": lambda acc: f"https://doi.org/{acc}",
        "download": "",
    },
    "CELLxGENE": {
        "label": "CELLxGENE Census",
        # 裸 UUID 太宽泛（易误匹配文件哈希等）→ 只在 URL 里识别（extract._URL_HINTS）
        "patterns": (),
        "url": lambda acc: f"https://cellxgene.cziscience.com/collections/{acc}",
        "download": "",
    },
}

_COMPILED: list[tuple[str, re.Pattern]] = [
    (name, re.compile("|".join(spec["patterns"]), re.I))
    for name, spec in REPOSITORIES.items()
]

# 免费 DOI 前缀 → 仓储（用于把 DOI 当登记号时的链接）
_DOI_REPO = {
    "10.5281/zenodo.": "Zenodo",
    "10.5061/dryad.": "Dryad",
    "10.5524/": "GigaDB",
}


def _spec_call(spec: dict, key: str, acc: str) -> str:
    """安全调用模板函数；空字符串/None 的 download 视为无。"""
    value = spec.get(key) or ""
    if callable(value):
        return value(acc)
    return ""


def canonical_links(accession: str) -> tuple[str, str, str, str]:
    """登记号 → (repository, canonical url, download url, label)。找不到返回 ('',...)。"""
    acc = (accession or "").strip()
    if not acc:
        return "", "", "", ""
    low = acc.lower()
    for prefix, repo in _DOI_REPO.items():
        if low.startswith(prefix):
            return repo, REPOSITORIES[repo]["url"](acc), _spec_call(REPOSITORIES[repo], "download", acc), \
                REPOSITORIES[repo]["label"]
    for name, pattern in _COMPILED:
        if pattern.fullmatch(acc):
            spec = REPOSITORIES[name]
            return name, spec["url"](acc), _spec_call(spec, "download", acc), spec["label"]
    for name, spec in learned_repositories().items():
        for p in spec.get("patterns", []):
            try:
                if p and re.fullmatch(p, acc, re.I):
                    url = (spec.get("url") or "")
                    return name, (url.format(acc=acc) if url else ""), "", spec.get("label", name)
            except re.error:
                continue
    return "", "", "", ""


def repository_for(accession: str) -> str:
    return canonical_links(accession)[0]


def learned_repositories() -> dict[str, dict]:
    """读 `learned/patterns.json`，合并为 {repo: {label, patterns[], url, download}}。"""
    try:
        from app import learned
        items = learned.read_patterns()
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict] = {}
    for p in items:
        repo = p.get("repository") or "Learned"
        spec = out.setdefault(repo, {"label": p.get("label", repo), "patterns": [],
                                     "url": None, "download": None})
        if p.get("pattern"):
            spec["patterns"].append(p["pattern"])
        if p.get("url"):
            spec["url"] = p["url"]
    return out


def iter_accession_patterns() -> list[tuple[str, re.Pattern]]:
    """内置 + 学到的全部 (repo, compiled pattern)。

    可用环境变量 `BIOAGENTDB_EXCLUDE_REPOS`（逗号分隔）临时屏蔽若干仓库——仅用于
    自改进闭环的"反事实"评测（验证能否重新发现被屏蔽的模式）。
    """
    import os
    excl = {x.strip() for x in (os.environ.get("BIOAGENTDB_EXCLUDE_REPOS") or "").split(",")
            if x.strip()}
    pats: list[tuple[str, re.Pattern]] = []
    for name, spec in REPOSITORIES.items():
        if name in excl:
            continue
        for p in spec.get("patterns", ()):  # type: ignore[arg-type]
            pats.append((name, re.compile(p, re.I)))
    for name, spec in learned_repositories().items():
        for p in spec.get("patterns", []):
            if p:
                try:
                    pats.append((name, re.compile(p, re.I)))
                except re.error:
                    continue
    return pats


def canon_accession(acc: str) -> str:
    """规范化用于比较：小写、去 dbGaP 版本后缀、去 protocols.io `/vN` 后缀。"""
    a = (acc or "").strip().lower()
    a = re.sub(r"\.v\d+\.p\d+$", "", a)
    a = re.sub(r"/v\d+$", "", a)
    return a


_RANGE_RE = re.compile(r"^(?P<a>[a-z]*\d+)\s*[\u2013\u2014-]\s*(?P<b>[a-z]*\d+)$", re.I)


def expand_accession(acc: str) -> list[str]:
    """把区间表示展开为端点（如 `GCST90199621-90201020` → 两端）。

    仅展开为两个端点，不做全量枚举（避免区间过大）。
    """
    c = canon_accession(acc)
    m = _RANGE_RE.match(c)
    if not m:
        return [c] if c else []
    a = m.group("a")
    b = m.group("b")
    pa = re.match(r"^[a-z]*", a, re.I).group(0)
    b_full = b if re.match(r"^[a-z]", b, re.I) else (pa + b)
    return [a, b_full]


# ---------------------------------------------------------------- 模态
MODALITIES: dict[str, str] = {
    "scrna": "scRNA-seq",
    "snrna": "snRNA-seq",
    "scatac": "scATAC-seq",
    "spatial_transcriptomics": "Spatial transcriptomics",
    "spatial_proteomics": "Spatial proteomics",
    "transcriptomics": "Transcriptomics",
    "proteomics": "Proteomics",
    "phosphoproteomics": "Phosphoproteomics",
    "metabolomics": "Metabolomics",
    "lipidomics": "Lipidomics",
    "metagenomics": "Metagenomics",
    "microbiome": "Microbiome",
    "epigenomics": "Epigenomics",
    "genomics": "Genomics",
    "multi_omics": "Multi-omics",
    "other": "Other",
}

_MODALITY_RULES: list[tuple[str, str]] = [
    ("scrna", r"single[- ]?cell[^.]{0,60}(rna|transcriptom)|scrna"),
    ("snrna", r"single[- ]?nucleus[^.]{0,60}(rna|transcriptom)|snrna"),
    ("scatac", r"single[- ]?cell[^.]{0,40}atac|scatac"),
    ("spatial_transcriptomics", r"spatial[^.]{0,40}(transcriptom|rnaseq|visium|xenium|merfish)"),
    ("spatial_proteomics", r"spatial[^.]{0,40}proteom|imc\b|codex|cycif"),
    ("phosphoproteomics", r"phospho[- ]?proteom"),
    ("lipidomics", r"lipidom"),
    ("metabolomics", r"metabolom"),
    ("metagenomics", r"metagenom|metatranscriptom"),
    ("microbiome", r"microbiom|microbiota|16s"),
    ("epigenomics", r"epigenom|methylation|atac|chip[- ]?seq|hi-?c"),
    ("proteomics", r"proteom|mass spectrometr|lc[- ]?ms"),
    ("genomics", r"\bwgs\b|whole[- ]genome|exome|snp|gwas"),
    ("transcriptomics", r"transcriptom|rna[- ]?seq|microarray|expression"),
    ("multi_omics", r"multi[- ]?om"),
]


def normalize_modality(*texts: str) -> list[str]:
    """返回命中的模态 code 列表（有序）。"""
    blob = " ".join(norm_space(t) for t in texts if t).lower()
    found: list[str] = []
    seen: set[str] = set()
    for code, pattern in _MODALITY_RULES:
        if code not in seen and re.search(pattern, blob):
            found.append(code)
            seen.add(code)
    return found


def modality_label(code: str) -> str:
    return MODALITIES.get(code or "", code or "")


# ---------------------------------------------------------------- Alzheimer 检索面
ALZ_TERMS = [
    "Alzheimer disease",
    "Alzheimer's disease",
    "Alzheimer's",
    "dementia",
    "mild cognitive impairment",
    "MCI",
    "frontotemporal dementia",
    "FTD",
    "Lewy body dementia",
    "LBD",
    "vascular dementia",
    "amyloid",
    "beta-amyloid",
    "amyloid-beta",
    "tau pathology",
    "tau protein",
    "neurofibrillary tangle",
    "APOE",
    "presenilin",
    "amyloid precursor protein",
]

# 每个主题运行时由 LLM 扩展，这里是静态种子。
ALZ_FACETS = {
    "modalities": [
        "single-cell RNA-seq", "single-nucleus RNA-seq", "spatial transcriptomics",
        "proteomics", "phosphoproteomics", "metabolomics", "lipidomics",
        "methylation", "genomics / GWAS", "multi-omics",
    ],
    "organisms": ["Homo sapiens", "Mus musculus"],
    "data_types": ["bulk RNA-seq", "scRNA-seq", "snRNA-seq", "WGS", "WES",
                   "mass spectrometry", "NMR", "imaging", "GWAS summary"],
    "subjects": ["brain", "cerebrospinal fluid", "blood", "plasma", "serum",
                 "hippocampus", "cortex", "prefrontal cortex", "entorhinal",
                 "microglia", "astrocytes", "neurons", "organoids", "iPSC-derived"],
}