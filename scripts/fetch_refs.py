"""按"故事线论点"核实参考文献并下载（不编造）。

每条 = (角色, id, 检索串, 期望标题)；角色对应正文里要讲的一个论点。
输出按角色分组的 references-verified.md + 机器可读 verified.json。
用法: python scripts/fetch_refs.py [--workers 12] [--limit 0]
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.utils import _request  # noqa: E402

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
OUT = ROOT / "docs" / "paper" / "refs"
PDF = ROOT / "data" / "refs"
MIN_RATIO = 0.86

# 角色 → 说明（正文里要讲的那句话）
ROLE_DESC = {
    "burden": "S1 · Disease burden (why this matters)",
    "proteinopathy": "S2 · Proteinopathies (our organizing frame)",
    "landmark": "S3 · Landmark public datasets (the data exist and are shared)",
    "repository": "S4 · General-purpose repositories (where the data physically lives)",
    "mining": "S5 · Literature mining & biocuration (prior extraction work)",
    "agent": "S6 · LLM scientific agents & benchmarks (capability frontier)",
    "verification": "S7 · Hallucination, verification & self-improvement (our method's basis)",
    "reproducibility": "S8 · Data sharing, FAIR & reproducibility",
}

# (role, id, 检索串, 期望标题)
REFS: list[tuple[str, str, str, str]] = [
    # ---------- S1 burden ----------
    ("burden", "gbd_dementia", 'EXT_ID:34998485',
     "Estimation of the global prevalence of dementia in 2019 and forecasted prevalence in 2050"),
    ("burden", "gbd_369", 'TITLE:"Global burden of 369 diseases and injuries"',
     "Global burden of 369 diseases and injuries in 204 countries and territories, 1990-2019: a systematic analysis for the Global Burden of Disease Study 2019"),
    ("burden", "hd_ross", '"Huntington\'s disease: from molecular pathogenesis to clinical treatment"',
     "Huntington's disease: from molecular pathogenesis to clinical treatment"),
    ("burden", "prion_mead", '"Genetic factors in mammalian prion diseases"',
     "Genetic Factors in Mammalian Prion Diseases"),
    # ---------- S2 proteinopathy ----------
    ("proteinopathy", "protein_agg", 'TITLE:"Protein aggregation and neurodegenerative disease"',
     "Protein aggregation and neurodegenerative disease"),
    ("proteinopathy", "misfolding", '"Unfolding the role of protein misfolding in neurodegenerative diseases"',
     "Unfolding the role of protein misfolding in neurodegenerative diseases"),
    ("proteinopathy", "goedert2015", 'TITLE:"prion concept in relation to assembled"',
     "Alzheimer's and Parkinson's diseases: the prion concept in relation to assembled Abeta, tau, and alpha-synuclein"),
    ("proteinopathy", "tdp43_neumann", '"Ubiquitinated TDP-43 in frontotemporal lobar degeneration and amyotrophic lateral sclerosis"',
     "Ubiquitinated TDP-43 in frontotemporal lobar degeneration and amyotrophic lateral sclerosis"),
    ("proteinopathy", "syn_spillantini", 'TITLE:"alpha-Synuclein in Lewy bodies"', "alpha-Synuclein in Lewy bodies"),
    ("proteinopathy", "braak1991", '"Neuropathological stageing of Alzheimer-related changes"',
     "Neuropathological stageing of Alzheimer-related changes"),
    # ---------- S3 landmark datasets ----------
    ("landmark", "adni_mueller", 'TITLE:"The Alzheimer\'s Disease Neuroimaging Initiative" AND AUTH:"Mueller"',
     "The Alzheimer's Disease Neuroimaging Initiative"),
    ("landmark", "adni_weiner2010", '"Alzheimer\'s Disease Neuroimaging Initiative: progress report and future plans"',
     "The Alzheimer's Disease Neuroimaging Initiative: progress report and future plans"),
    ("landmark", "adni_veitch", 'AUTH:"Veitch" AND TITLE:"Alzheimer\'s Disease Neuroimaging Initiative"',
     "The Alzheimer's Disease Neuroimaging Initiative in the era of Alzheimer's disease treatment: A review of ADNI studies from 2021 to 2022"),
    ("landmark", "ros", '"Overview and findings from the religious orders study"',
     "Overview and findings from the religious orders study"),
    ("landmark", "map", '"Overview and findings from the Rush Memory and Aging Project"',
     "Overview and findings from the Rush Memory and Aging Project"),
    ("landmark", "rosmap", 'TITLE:"Religious Orders Study and Rush Memory and Aging Project"',
     "Religious Orders Study and Rush Memory and Aging Project"),
    ("landmark", "seaad", '"Integrated multimodal cell atlas of Alzheimer\'s disease"',
     "Integrated multimodal cell atlas of Alzheimer's disease"),
    ("landmark", "seaad_na", '"SEA-AD is a multimodal cellular atlas"',
     "SEA-AD is a multimodal cellular atlas and resource for Alzheimer's disease"),
    ("landmark", "msbb", 'The Mount Sinai cohort of large-scale genomic, transcriptomic and proteomic data in Alzheimer\'s disease',
     "The Mount Sinai cohort of large-scale genomic, transcriptomic and proteomic data in Alzheimer's disease"),
    ("landmark", "hca", 'TITLE:"Human Cell Atlas" AND AUTH:"Regev"', "The Human Cell Atlas"),
    # ---------- S4 repositories ----------
    ("repository", "geo", '"NCBI GEO: archive for functional genomics data sets"',
     "NCBI GEO: archive for functional genomics data sets--update"),
    ("repository", "biostudies", '"From ArrayExpress to BioStudies"', "From ArrayExpress to BioStudies"),
    ("repository", "pride", '"The PRIDE database resources in 2022"',
     "The PRIDE database resources in 2022: a hub for mass spectrometry-based proteomics evidences"),
    ("repository", "ena", '"The European Nucleotide Archive in 2020"', "The European Nucleotide Archive in 2020"),
    ("repository", "sra", 'TITLE:"Sequence Read Archive" AND AUTH:"Leinonen"', "The Sequence Read Archive"),
    ("repository", "dbgap", '"The NCBI dbGaP database of genotypes and phenotypes"',
     "The NCBI dbGaP database of genotypes and phenotypes"),
    ("repository", "ega", 'TITLE:"European Genome-phenome Archive"',
     "The European Genome-phenome Archive of human data consented for biomedical research"),
    ("repository", "gwas", 'AUTH:"Buniello" AND TITLE:"GWAS Catalog"',
     "The NHGRI-EBI GWAS Catalog of published genome-wide association studies, targeted arrays and summary statistics 2019"),
    ("repository", "metabolights", '"MetaboLights: a resource evolving in response"',
     "MetaboLights: a resource evolving in response to the needs of its scientific community"),
    ("repository", "jpost", '"jPOSTrepo: an international standard data repository for proteomes"',
     "jPOSTrepo: an international standard data repository for proteomes"),
    ("repository", "omicsdi", 'EXT_ID:28486464',
     "Discovering and linking public omics data sets using the Omics Discovery Index"),
    # ---------- S5 literature mining / biocuration ----------
    ("mining", "pubtator", '"PubTator 3.0"',
     "PubTator 3.0: an AI-powered literature resource for unlocking biomedical knowledge"),
    ("mining", "epmc2020", '"Europe PMC in 2020"', "Europe PMC in 2020"),
    ("mining", "litsuggest", 'TITLE:"LitSuggest"',
     "LitSuggest: a web-based system for literature recommendation and curation using machine learning"),
    ("mining", "hitl_curation", '"human-in-the-loop AI-driven literature curation"',
     "Empowering biological knowledgebases: advances in human-in-the-loop AI-driven literature curation"),
    ("mining", "local_llm_mining", 'TITLE:"literature mining" AND AUTH:"Xiang"',
     "Local Large Language Model-Assisted Literature Mining"),
    # ---------- S6 LLM scientific agents & benchmarks ----------
    ("agent", "paperqa2", 'TITLE:"superhuman synthesis of scientific knowledge"',
     "Language agents achieve superhuman synthesis of scientific knowledge"),
    ("agent", "biomni", '"Biomni: A General-Purpose Biomedical AI Agent"', "Biomni: A General-Purpose Biomedical AI Agent"),
    ("agent", "coscientist", 'arXiv:2502.18864', "Towards an AI co-scientist"),
    ("agent", "cellvoyager", 'TITLE:"CellVoyager"',
     "CellVoyager: AI CompBio agent generates new insights by autonomously analyzing biological data"),
    ("agent", "medkgent", 'TITLE:"MedKGent"',
     "MedKGent: a large language model agent framework for constructing temporally evolving medical knowledge graph"),
    ("agent", "scienceagentbench", 'TITLE:"ScienceAgentBench"',
     "ScienceAgentBench: Toward Rigorous Assessment of Language Agents for Data-Driven Scientific Discovery"),
    ("agent", "paperarena", 'arXiv:2510.10909',
     "PaperArena: An Evaluation Benchmark for Tool-Augmented Agentic Reasoning on Scientific Literature"),
    ("agent", "agentic_biotech", '"in silico team science in biomedical research"',
     "Agentic AI and the rise of in silico team science in biomedical research"),
    ("agent", "ai_agents_biology", '"Artificial intelligence agents for biology"', "Artificial intelligence agents for biology"),
    # ---------- S7 hallucination / verification / self-improvement ----------
    ("verification", "hallucination", 'TITLE:"Survey of Hallucination" AND AUTH:"Ji"',
     "Survey of Hallucination in Natural Language Generation"),
    ("verification", "selfrefine", 'TITLE:"Self-Refine"',
     "Self-Refine: Iterative Refinement with Self-Feedback"),
    ("verification", "reflexion", 'TITLE:"Reflexion"',
     "Reflexion: Language Agents with Verbal Reinforcement Learning"),
    ("verification", "cove", 'TITLE:"Chain-of-Verification"',
     "Chain-of-Verification Reduces Hallucination in Large Language Models"),
    # ---------- S8 reproducibility / FAIR / data sharing ----------
    ("reproducibility", "fair", 'TITLE:"FAIR Guiding Principles"',
     "The FAIR Guiding Principles for scientific data management and stewardship"),
    ("reproducibility", "data_sharing", '"Biomedical data sharing and reuse"',
     "Biomedical data sharing and reuse: attitudes and practices of clinical and research staff"),
    ("reproducibility", "repro_baker", '"1,500 scientists lift the lid on reproducibility"',
     "1,500 scientists lift the lid on reproducibility"),
    # ---------- extra story-critical refs ----------
    ("proteinopathy", "ad_gwas_bellenguez", 'TITLE:"genetic etiology of Alzheimer"',
     "New insights into the genetic etiology of Alzheimer's disease and related dementias"),
    ("proteinopathy", "ad_gwas_lambert", 'TITLE:"Meta-analysis of 74,046 individuals identifies 11 new susceptibility loci"',
     "Meta-analysis of 74,046 individuals identifies 11 new susceptibility loci for Alzheimer's disease"),
    ("proteinopathy", "pd_gwas_nalls", 'TITLE:"Identification of novel risk loci, causal insights, and heritable risk for Parkinson"',
     "Identification of novel risk loci, causal insights, and heritable risk for Parkinson's disease: a meta-analysis of genome-wide association studies"),
    ("proteinopathy", "als_gwas", 'TITLE:"Common and rare variant association analyses in amyotrophic lateral sclerosis"',
     "Common and rare variant association analyses in amyotrophic lateral sclerosis identify 15 risk loci with distinct genetic architectures and neuron-specific biology"),
    ("landmark", "mathys_singlecell", 'EXT_ID:31042697',
     "Single-cell transcriptomic analysis of Alzheimer's disease"),
    ("landmark", "habib_dronc", 'TITLE:"Massively parallel single-nucleus RNA-seq"',
     "Massively parallel single-nucleus RNA-seq with DroNc-seq"),
    ("landmark", "merfish_chen", 'TITLE:"Spatially resolved, highly multiplexed RNA profiling in single cells"',
     "Spatially resolved, highly multiplexed RNA profiling in single cells"),
    ("landmark", "spatial_ad", 'TITLE:"Spatial transcriptomics and in situ sequencing to study Alzheimer"',
     "Spatial transcriptomics and in situ sequencing to study Alzheimer's disease"),
    ("repository", "cellxgene", 'TITLE:"CELLxGENE Discover"',
     "CZ CELLxGENE Discover: a single-cell data platform for scalable exploration, analysis and modeling of aggregated data"),
]


def title_sim(a: str, b: str) -> float:
    na = re.sub(r"[^a-z0-9 ]", " ", (a or "").lower())
    nb = re.sub(r"[^a-z0-9 ]", " ", (b or "").lower())
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def _long_tokens(s: str) -> set[str]:
    """长度≥8 的词（distinctive tokens）。"""
    return {w for w in re.findall(r"[a-z0-9]{8,}", (s or "").lower())}


def title_ok(expected: str, got: str, ratio: float) -> bool:
    """接受条件：相似度达标 **且** 全部"长词"都出现在结果标题里（防模板化误配）。"""
    if ratio < MIN_RATIO:
        a, b = _norm_title(expected), _norm_title(got)
        return bool(a) and len(a) >= 20 and (b.startswith(a) or a.startswith(b))
    need = _long_tokens(expected)
    have = _long_tokens(got)
    return need.issubset(have) if need else True


def best_match(search: str, expected: str) -> dict | None:
    r = _request("GET", EPMC, params={"query": search, "format": "json",
                                      "pageSize": 8, "resultType": "core"},
                 retries=1, timeout=15.0)
    if r is None or r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return None
    best, best_r, best_ok = None, 0.0, False
    for item in ((data.get("resultList") or {}).get("result") or []):
        ratio = title_sim(expected, item.get("title", ""))
        ok = title_ok(expected, item.get("title", ""), ratio)
        if ok and (not best_ok or ratio > best_r):
            best, best_r, best_ok = item, ratio, True
        elif not best_ok and ratio > best_r:
            best, best_r = item, ratio
    if best_ok and best:
        best["_match_ratio"] = round(best_r, 3)
        return best
    return None


def crossref_match(expected: str) -> dict | None:
    """Europe PMC 未收录时（arXiv/ACM/ICLR 等）用 Crossref 按标题核实。"""
    r = _request("GET", "https://api.crossref.org/works",
                 params={"query.bibliographic": expected, "rows": 5,
                         "select": "DOI,title,author,issued,container-title,type,URL"},
                 retries=1, timeout=20.0)
    if r is None or r.status_code != 200:
        return None
    try:
        items = ((r.json() or {}).get("message") or {}).get("items") or []
    except Exception:  # noqa: BLE001
        return None
    best, best_r, best_ok = None, 0.0, False
    for it in items:
        t = (it.get("title") or [""])[0]
        ratio = title_sim(expected, t)
        if ratio > best_r:
            best, best_r = it, ratio
        if title_ok(expected, t, ratio):
            best_ok = True
    if best and best_ok:
        best["_match_ratio"] = round(best_r, 3)
        return best
    return None


def _crossref_record(ref_id: str, role: str, it: dict) -> dict:
    authors = "; ".join(
        f"{a.get('family', '')} {a.get('given', '')}".strip()
        for a in (it.get("author") or [])[:12])
    year = ""
    try:
        year = str((it.get("issued") or {}).get("date-parts", [[""]])[0][0])
    except Exception:  # noqa: BLE001
        pass
    return {"role": role, "id": ref_id, "pmid": "", "pmcid": "",
            "doi": it.get("DOI", ""), "title": (it.get("title") or [""])[0],
            "authors": authors,
            "journal": (it.get("container-title") or [""])[0],
            "year": year, "source": "crossref", "is_oa": False,
            "match_ratio": it.get("_match_ratio"), "url": it.get("URL", "")}


def datacite_match(expected: str) -> dict | None:
    """arXiv/preprint 等（DataCite 注册，DOI 前缀 10.48550）用 DataCite 核实。"""
    r = _request("GET", "https://api.datacite.org/dois",
                 params={"query": expected, "page[size]": 5},
                 retries=1, timeout=20.0)
    if r is None or r.status_code != 200:
        return None
    try:
        items = ((r.json() or {}).get("data") or [])
    except Exception:  # noqa: BLE001
        return None
    best, best_r, best_ok = None, 0.0, False
    for it in items:
        attr = (it or {}).get("attributes") or {}
        title = ((attr.get("titles") or [{}])[0] or {}).get("title", "")
        ratio = title_sim(expected, title)
        if ratio > best_r:
            best, best_r = attr, ratio
        if title_ok(expected, title, ratio):
            best_ok = True
    if best and best_ok:
        best["_match_ratio"] = round(best_r, 3)
        return best
    return None


def _datacite_record(ref_id: str, role: str, attr: dict) -> dict:
    creators = "; ".join(
        ((c.get("name") or c.get("familyName") or "") for c in (attr.get("creators") or [])[:12]))
    return {"role": role, "id": ref_id, "pmid": "", "pmcid": "",
            "doi": attr.get("doi", ""),
            "title": ((attr.get("titles") or [{}])[0] or {}).get("title", ""),
            "authors": creators,
            "journal": attr.get("publisher", ""),
            "year": str(attr.get("publicationYear") or ""), "source": "datacite",
            "is_oa": False, "match_ratio": attr.get("_match_ratio"),
            "url": attr.get("url", "")}


def openalex_match(expected: str) -> dict | None:
    """arXiv/various venues: OpenAlex by title."""
    r = _request("GET", "https://api.openalex.org/works",
                 params={"search": expected, "per_page": 5,
                         "select": "id,doi,title,publication_year,authorships,"
                                   "primary_location,type"},
                 retries=1, timeout=20.0)
    if r is None or r.status_code != 200:
        return None
    try:
        items = ((r.json() or {}).get("results") or [])
    except Exception:  # noqa: BLE001
        return None
    best, best_r, best_ok = None, 0.0, False
    for it in items:
        ratio = title_sim(expected, it.get("title") or "")
        if ratio > best_r:
            best, best_r = it, ratio
        if title_ok(expected, it.get("title") or "", ratio):
            best_ok = True
    if best and best_ok:
        best["_match_ratio"] = round(best_r, 3)
        return best
    return None


def _openalex_record(ref_id: str, role: str, it: dict) -> dict:
    authors = "; ".join(
        ((a.get("author") or {}).get("display_name") or "")
        for a in (it.get("authorships") or [])[:12])
    loc = (it.get("primary_location") or {}).get("source") or {}
    return {"role": role, "id": ref_id, "pmid": "", "pmcid": "",
            "doi": (it.get("doi") or "").replace("https://doi.org/", ""),
            "title": it.get("title") or "", "authors": authors,
            "journal": loc.get("display_name") or "", "year": str(it.get("publication_year") or ""),
            "source": "openalex", "is_oa": False, "match_ratio": it.get("_match_ratio"),
            "url": it.get("id") or ""}


def arxiv_match(arxiv_id: str) -> dict | None:
    """arXiv API 核验（id_list）。"""
    r = _request("GET", "http://export.arxiv.org/api/query",
                 params={"id_list": arxiv_id}, retries=1, timeout=20.0)
    if r is None or r.status_code != 200:
        return None
    text = r.text or ""
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(text)
    except Exception:  # noqa: BLE001
        return None
    entry = root.find("a:entry", ns)
    if entry is None:
        return None
    title = re.sub(r"\s+", " ", (entry.findtext("a:title", default="", namespaces=ns) or "")).strip()
    if not title:
        return None
    authors = "; ".join(
        (a.findtext("a:name", default="", namespaces=ns) or "")
        for a in entry.findall("a:author", ns)[:12])
    published = entry.findtext("a:published", default="", namespaces=ns) or ""
    return {"title": title, "authors": authors, "year": published[:4],
            "id": arxiv_id, "url": f"https://arxiv.org/abs/{arxiv_id}"}


def download_oa(rec: dict, fname: str) -> dict:
    """开放获取：Europe PMC 全文 XML（端点：/rest/{PMCID}/fullTextXML；PDF 端点 403，跳过）。"""
    out = {"xml": "", "pdf": ""}
    urls = []
    if rec.get("pmcid"):
        urls.append(f"https://www.ebi.ac.uk/europepmc/webservices/rest/"
                    f"{rec['pmcid']}/fullTextXML")
    if rec.get("source") and rec.get("id"):
        urls.append(f"https://www.ebi.ac.uk/europepmc/webservices/rest/"
                    f"{rec['source']}/{rec['id']}/fullTextXML")
    for u in urls:
        try:
            r = _request("GET", u, retries=1, timeout=25.0)
            if r is not None and r.status_code == 200 and (r.text or "").lstrip().startswith("<"):
                f = PDF / f"{fname}.xml"
                f.write_text(r.text, encoding="utf-8")
                out["xml"] = str(f)
                return out
        except Exception:  # noqa: BLE001
            continue
    # arXiv PDF（从 DOI / source / url 三处解析 arXiv ID）
    aid = ""
    doi = (rec.get("doi") or "").lower()
    if "10.48550/arxiv." in doi:
        aid = doi.split("10.48550/arxiv.", 1)[1]
    elif rec.get("source") == "arxiv" and rec.get("id"):
        aid = str(rec["id"])
    elif "arxiv.org/abs/" in (rec.get("url") or ""):
        aid = (rec.get("url") or "").split("/abs/")[-1]
    if aid:
        try:
            r = _request("GET", f"https://arxiv.org/pdf/{aid.strip()}", retries=1, timeout=60.0)
            if r is not None and r.status_code == 200 and r.content[:4] == b"%PDF":
                f = PDF / f"{fname}.pdf"
                f.write_bytes(r.content)
                out["pdf"] = str(f)
        except Exception:  # noqa: BLE001
            pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--download-only", action="store_true",
                    help="只对已核实条目补下载 OA 全文/PDF（不重新检索）")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    PDF.mkdir(parents=True, exist_ok=True)

    if args.download_only:
        vpath = OUT / "verified.json"
        items = json.loads(vpath.read_text(encoding="utf-8")) if vpath.exists() else []
        todo = [r for r in items
                if ((r.get("pmcid") and not (r.get("files") or {}).get("xml"))
                    or (("10.48550/arxiv." in (r.get("doi") or "").lower()
                         or r.get("source") == "arxiv")
                        and not (r.get("files") or {}).get("pdf")))]
        print(f"download-only: {len(todo)} item(s) with PMCID to fetch", flush=True)

        def fetch(rec: dict) -> tuple[str, dict]:
            return rec["id"], download_oa(rec, rec["id"])

        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for ref_id, files in ex.map(fetch, todo):
                for r in items:
                    if r["id"] == ref_id:
                        r["files"] = files
                done += 1
                if files.get("xml") or files.get("pdf"):
                    print(f"  got {ref_id}: {files.get('xml','')}{files.get('pdf','')}", flush=True)
        vpath.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        got = sum(1 for r in items if (r.get("files") or {}).get("xml"))
        print(f"\ndownloaded full text for {got}/{len(items)} refs -> {PDF}")
        return

    jobs = REFS[: args.limit] if args.limit else REFS

    OUT.mkdir(parents=True, exist_ok=True)
    PDF.mkdir(parents=True, exist_ok=True)

    def process(item):
        role, ref_id, q, expected = item
        t0 = time.time()
        if q.startswith("arXiv:"):
            aid = q.split(":", 1)[1].strip()
            try:
                a = arxiv_match(aid)
            except Exception:  # noqa: BLE001
                a = None
            if a:
                rec = {"role": role, "id": ref_id, "pmid": "", "pmcid": "", "doi": "",
                       "title": a["title"], "authors": a["authors"], "journal": "arXiv",
                       "year": a["year"], "source": "arxiv", "is_oa": False,
                       "match_ratio": 1.0, "url": a["url"]}
                return role, ref_id, q, expected, rec, "", time.time() - t0
            return role, ref_id, q, expected, None, "arxiv", time.time() - t0
        r = None
        try:
            r = best_match(q, expected)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
        else:
            err = ""
        if not r:
            try:
                cr = crossref_match(expected)
            except Exception:  # noqa: BLE001
                cr = None
            if cr:
                return role, ref_id, q, expected, _crossref_record(ref_id, role, cr), "", \
                    time.time() - t0
            try:
                dc = datacite_match(expected)
            except Exception:  # noqa: BLE001
                dc = None
            if dc:
                return role, ref_id, q, expected, _datacite_record(ref_id, role, dc), "", \
                    time.time() - t0
            try:
                oa = openalex_match(expected)
            except Exception:  # noqa: BLE001
                oa = None
            if oa:
                return role, ref_id, q, expected, _openalex_record(ref_id, role, oa), "", \
                    time.time() - t0
            return role, ref_id, q, expected, None, err, time.time() - t0
        rec = {"role": role, "id": ref_id, "pmid": r.get("pmid", ""), "pmcid": r.get("pmcid", ""),
               "doi": r.get("doi", ""), "title": r.get("title", ""),
               "authors": r.get("authorString", ""),
               "journal": (r.get("journalInfo") or {}).get("journal", {}).get("title", ""),
               "year": r.get("pubYear", ""), "source": r.get("source", ""),
               "is_oa": str(r.get("isOpenAccess", "")).lower() in ("y", "true", "1"),
               "match_ratio": r.get("_match_ratio")}
        if rec["is_oa"]:
            rec["files"] = download_oa(r, ref_id)
        return role, ref_id, q, expected, rec, "", time.time() - t0

    verified, missing = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for role, ref_id, q, expected, rec, err, secs in ex.map(process, jobs):
            if rec is None:
                missing.append({"role": role, "id": ref_id, "query": q, "expected": expected,
                                "error": err})
                print(f"MISS [{role}] {ref_id} ({secs:.1f}s)", flush=True)
            else:
                verified.append(rec)
                print(f"OK   [{role}] {ref_id} {rec['year']} PMID {rec['pmid']} "
                      f"({rec['match_ratio']}) {rec['title'][:48]}", flush=True)

    (OUT / "verified.json").write_text(json.dumps(verified, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    (OUT / "missing.json").write_text(json.dumps(missing, ensure_ascii=False, indent=2),
                                      encoding="utf-8")

    def fmt(i: int, r: dict) -> str:
        doi = f" doi:{r['doi']}" if r["doi"] else ""
        pmid = f" PMID:{r['pmid']}" if r["pmid"] else ""
        pmc = f" PMCID:{r['pmcid']}" if r["pmcid"] else ""
        return (f"{i}. {r['authors'][:110]} {r['title']} *{r['journal']}* {r['year']}."
                f"{doi}{pmid}{pmc}")

    by_role: dict[str, list[dict]] = {}
    for r in verified:
        by_role.setdefault(r["role"], []).append(r)

    md = ["# References (Europe PMC-verified), grouped by the claim they support", "",
          f"- verified **{len(verified)}** / {len(jobs)} · missing {len(missing)} · "
          f"OA downloaded {sum(1 for r in verified if r.get('files'))}", ""]
    n = 0
    for role, desc in ROLE_DESC.items():
        items = by_role.get(role, [])
        if not items:
            continue
        md.append(f"## {desc}")
        for r in items:
            n += 1
            md.append(fmt(n, r))
        md.append("")
    if missing:
        md += ["## Not verified (do NOT cite; provide manually)", ""]
        for m in missing:
            md.append(f"- [{m['role']}] `{m['expected']}` (query: `{m['query']}`)")
    (OUT / "references-verified.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nverified {len(verified)} / {len(jobs)} · missing {len(missing)}")


if __name__ == "__main__":
    main()
