"""Paper-context quote evidence, constraint matching, and quote judging.

The pipeline used to stop at "an accession appears in the Data Availability
section".  This module adds the missing question: *does the paper text actually
support the constraints the user asked for?*  It deliberately separates:

1. deterministic retrieval (locate the accession, take a paper window);
2. semantic reading (an LLM, or a conservative rule fallback);
3. deterministic validation (quote must be a verbatim substring, numbers must
   agree, no quote => unknown);
4. deterministic matching (`match_records`) and disclosure checks.

Only metadata, quotes and links are kept.  Full text is used from the existing
``paper_datasets`` cache and is never written into the report.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import unicodedata
from typing import Any

from app.core.config import cfg

logger = logging.getLogger(__name__)

CONSTRAINT_KEYS = ("organism", "tissue", "modality", "sample_count")
VALID_STATUS = {"supported", "contradicted", "unknown"}
USAGE_TYPES = {"generated", "reused", "mentioned", "unknown"}
MAX_CONTEXTS = int(os.environ.get("BIOAGENTDB_MAX_QUOTE_CONTEXTS", "0"))
"""0 means no cap: every announced accession with a quote is judged.

An explicit positive value is an opt-in cost/latency guard; the product
default follows the requirement not to silently omit any accession.
"""
MAX_WINDOW = 2600

_XML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_SECTION_RE = re.compile(
    r"\b(abstract|introduction|background|results?|discussion|conclusions?|"
    r"methods?|materials?\s+and\s+methods?|data\s+availability|"
    r"availability\s+of\s+data|supplementary(?:\s+information)?)\b", re.I)

_ORG_HUMAN = re.compile(r"\b(human|homo\s+sapiens|patients?|donors?|subjects?|"
                        r"post[- ]?mortem|clinical\s+samples?)\b", re.I)
_ORG_MOUSE = re.compile(r"\b(mouse|mice|murine|mus\s+musculus)\b", re.I)
_ORG_RAT = re.compile(r"\b(rat|rats|rattus\s+norvegicus)\b", re.I)
_ORG_OTHER = re.compile(r"\b(zebrafish|drosophila|fly|yeast|pig|porcine|monkey|"
                        r"macaque|marmoset|cow|bovine)\b", re.I)

_TISSUE_HINTS = {
    "brain": r"\b(brain|cortex|cortical|hippocamp|substantia\s+nigra|striatum)\b",
    "csf": r"\b(csf|cerebrospinal\s+fluid)\b",
    "blood": r"\b(blood|plasma|serum|pbmc)\b",
    "liver": r"\b(liver|hepatic|hepatocyte)\b",
    "kidney": r"\b(kidney|renal)\b",
    "gut": r"\b(gut|intestin|colon|fecal|faecal|stool)\b",
    "skin": r"\b(skin|epiderm|derm)\b",
    "lung": r"\b(lung|pulmonary|airway)\b",
    "retina": r"\b(retina|retinal)\b",
    "muscle": r"\b(muscle|myofib|cardiac)\b",
}

_MODALITY_HINTS = {
    "proteomics": r"\b(proteom\w*|mass\s+spectrom\w*|lc[- ]?ms|tmt|itraq|cytof)\b",
    "transcriptomics": r"\b(rna[- ]?seq|transcriptom\w*|scrna|snrna|single[- ]?(cell|nucleus))\b",
    "spatial_transcriptomics": r"\b(spatial|visium|stereo[- ]?seq|merfish|slide[- ]?seq)\b",
    "epigenomics": r"\b(methylat\w*|bisulfite|atac|chip[- ]?seq|epigenom\w*)\b",
    "genomics": r"\b(whole[- ]?genome|wgs|exome|wxs|gwas|genom\w*)\b",
    "metabolomics": r"\b(metabolom\w*|lipidom\w*)\b",
    "multi_omics": r"\b(multi[- ]?omic\w*|multiomic\w*|multi[- ]?omics)\b",
}

_SAMPLE_RE = re.compile(
    r"\b(\d{1,6})\s+"
    r"(?:(?:[A-Za-z][A-Za-z-]*|AD)\s+){0,4}"
    r"(?:samples?|patients?|controls?|subjects?|donors?|cases?|"
    r"individuals?|participants?|specimens?|biopsies?|mice|animals?)\b", re.I)

_GENERATED_RE = re.compile(
    r"\b(?:we|our\s+(?:study|group|lab))\b.{0,120}?"
    r"\b(?:generated|generat(?:ed|ing)|deposited|deposit(?:ed|ing)|uploaded|"
    r"submitted|produced|released|made\s+(?:public|available))\b", re.I)
_REUSED_RE = re.compile(
    r"\b(?:re[- ]?analy[sz]|reanalys|downloaded|obtained\s+from|retrieved\s+from|"
    r"previously\s+(?:published|reported|generated)|publicly\s+available|"
    r"reused|taken\s+from|from\s+(?:the\s+)?(?:geo|gene\s+expression\s+omnibus|"
    r"sra|arrayexpress|ena|pride|massive))\b", re.I)


# ------------------------------------------------------------------ plain text

def to_plain_text(text: str) -> str:
    """Turn JATS/EPMC XML or PDF text into searchable plain text.

    Section titles are preserved as line breaks before tags are removed, so a
    later window can be labelled Methods/Results/Data Availability.
    """
    if not text:
        return ""
    text = _XML_TITLE_RE.sub(lambda m: "\n\n" + m.group(1) + "\n\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    return _WS_RE.sub(" ", text).strip()


def _sentence_window(text: str, idx: int, radius: int = 700) -> str:
    start = max(0, idx - radius)
    end = min(len(text), idx + radius)
    # Keep preceding context: constraints often sit one sentence before the
    # accession (e.g. "80 samples ... Data Availability: GSE...").  Trim only
    # on the right so the quote does not run into the next section.
    right = text.find(". ", idx, min(len(text), idx + 260))
    if right != -1:
        end = min(end, right + 1)
    return text[start:end].strip()[:MAX_WINDOW]


def _section_at(text: str, idx: int) -> str:
    prefix = text[max(0, idx - 5000):idx]
    matches = list(_SECTION_RE.finditer(prefix))
    if not matches:
        return ""
    raw = matches[-1].group(1)
    return _WS_RE.sub(" ", raw).strip().title()


def _find_occurrences(text: str, accession: str) -> list[int]:
    acc = (accession or "").strip()
    if not acc:
        return []
    return [m.start() for m in re.finditer(r"(?<![A-Za-z0-9])"
                                           + re.escape(acc)
                                           + r"(?![A-Za-z0-9])", text, re.I)]


def _usage_type(window: str) -> str:
    w = window or ""
    # Reuse wording ("reanalyzed", "downloaded from") is stronger than a
    # nearby "generated" in the same window (e.g. "no new samples were generated").
    if _REUSED_RE.search(w):
        return "reused"
    if _GENERATED_RE.search(w):
        return "generated"
    if re.search(r"\b(?:data\s+availability|accessed|available\s+(?:at|from|in))\b",
                 w, re.I):
        return "mentioned"
    return "unknown"


def _quote_for(text: str, idx: int, term_re: re.Pattern) -> str:
    m = term_re.search(text, max(0, idx - 500), min(len(text), idx + 500))
    if not m:
        return _sentence_window(text, idx, radius=400)
    return _sentence_window(text, m.start(), radius=420)


def _best_occurrence(text: str, accession: str, profile: dict) -> tuple[int, str]:
    """Choose the occurrence with the richest constraint context."""
    occ = _find_occurrences(text, accession)
    if not occ:
        return -1, ""
    wanted = _profile_terms(profile)
    best_i, best_score = occ[0], -1
    for i in occ:
        w = text[max(0, i - 1000):min(len(text), i + 1200)].lower()
        score = sum(1 for term in wanted if term and term in w)
        # Prefer a methods/results occurrence over a bare DA mention.
        sec = _section_at(text, i).lower()
        if "method" in sec or "result" in sec:
            score += 2
        if score > best_score:
            best_i, best_score = i, score
    return best_i, _section_at(text, best_i)


def _profile_terms(profile: dict) -> list[str]:
    terms: list[str] = []
    for k in ("organism", "tissue"):
        v = str(profile.get(k) or "").strip().lower()
        if v:
            terms.append(v)
    for m in profile.get("modalities") or []:
        terms.append(str(m).replace("_", " ").lower())
    return terms


def build_quote_contexts(candidates: list[dict], profile: dict,
                         members_map: dict[str, list[dict]] | None = None,
                         limit: int = MAX_CONTEXTS) -> list[dict]:
    """Extract one window per announced accession (cached full text only)."""
    from app.chat import paper_datasets

    contexts: list[dict] = []
    for cand in candidates or []:
        if limit and len(contexts) >= limit:
            break
        datasets = (cand.get("datasets") or [])
        if not datasets:
            continue
        members = (members_map or {}).get(cand.get("gid") or "") or []
        text, kind = paper_datasets.cached_fulltext(cand, members)
        plain = to_plain_text(text) if text else ""
        for d in datasets:
            if limit and len(contexts) >= limit:
                break
            acc = str(d.get("accession") or "").strip()
            if not acc:
                continue
            quote = str(d.get("quote") or "").strip()
            window = quote
            section = "Data Availability" if d.get("da_evidence") else ""
            source = "da_quote"
            title = str(cand.get("title") or "").strip()
            abstract = str(cand.get("abstract") or "").strip()[:1200]
            if plain:
                idx, sec = _best_occurrence(plain, acc, profile)
                if idx >= 0:
                    window = _sentence_window(plain, idx, radius=950) or quote
                    if window:
                        quote = quote or _sentence_window(plain, idx, radius=420)
                        section = sec or section
                        source = "fulltext"
            if not quote:
                continue
            # The title/abstract are paper metadata, not an accession window, but
            # they often state organism/tissue/modality when the DA sentence does
            # not.  Keep them in the window and let the validator require a
            # verbatim quote from this exact combined text.
            meta = " ".join(x for x in (f"Title: {title}." if title else "",
                                         f"Abstract: {abstract}." if abstract else "")
                            if x)
            combined = (meta + " " + (window or quote)).strip()[:MAX_WINDOW]
            ctx = {
                "id": f"q{len(contexts) + 1}",
                "accession": acc,
                "paper_id": cand.get("gid") or "",
                "paper_title": title,
                "doi": cand.get("doi") or "",
                "pmid": cand.get("pmid") or "",
                "repository": d.get("repository") or "",
                "section": section or "unknown",
                "window": combined,
                "quote": quote,
                "source": source,
                "fulltext_kind": kind or cand.get("fulltext_kind") or "none",
                "heuristic_usage": _usage_type(combined),
                "url": d.get("url") or "",
            }
            contexts.append(ctx)
    return contexts


# ------------------------------------------------------------------ rule judge

def _norm(s: str) -> str:
    s = html.unescape(str(s or ""))
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    return _WS_RE.sub(" ", s).strip().lower()


def _quote_in_window(quote: str, window: str) -> bool:
    q, w = _norm(quote), _norm(window)
    return bool(q) and q in w


def _term_quote(window: str, pattern: re.Pattern) -> tuple[str, bool]:
    m = pattern.search(window or "")
    if not m:
        return "", False
    return _sentence_window(window, m.start(), radius=420), True


def _organism_judgment(window: str, target: str) -> dict:
    w = window or ""
    target_l = target.lower()
    mouse = bool(_ORG_MOUSE.search(w))
    human = bool(_ORG_HUMAN.search(w))
    rat = bool(_ORG_RAT.search(w))
    other = _ORG_OTHER.search(w)
    if "human" in target_l or "sapiens" in target_l:
        if mouse and not human:
            q, _ = _term_quote(w, _ORG_MOUSE)
            return {"status": "contradicted", "value": "Mus musculus", "quote": q,
                    "inference": False, "confidence": 0.9}
        if human:
            q, _ = _term_quote(w, _ORG_HUMAN)
            explicit = bool(re.search(r"\b(human|homo\s+sapiens)\b", w, re.I))
            return {"status": "supported", "value": "Homo sapiens", "quote": q,
                    "inference": not explicit, "confidence": 0.85 if explicit else 0.7}
        if rat:
            q, _ = _term_quote(w, _ORG_RAT)
            return {"status": "contradicted", "value": "Rattus norvegicus",
                    "quote": q, "inference": False, "confidence": 0.9}
        if other:
            q, _ = _term_quote(w, _ORG_OTHER)
            return {"status": "contradicted", "value": other.group(1),
                    "quote": q, "inference": False, "confidence": 0.8}
        return {"status": "unknown", "value": None, "quote": "", "inference": False,
                "confidence": 0.0}
    if "mouse" in target_l or "mus" in target_l:
        if human and not mouse:
            q, _ = _term_quote(w, _ORG_HUMAN)
            return {"status": "contradicted", "value": "Homo sapiens", "quote": q,
                    "inference": False, "confidence": 0.9}
        if mouse:
            q, _ = _term_quote(w, _ORG_MOUSE)
            return {"status": "supported", "value": "Mus musculus", "quote": q,
                    "inference": False, "confidence": 0.9}
        return {"status": "unknown", "value": None, "quote": "", "inference": False,
                "confidence": 0.0}
    return {"status": "unknown", "value": None, "quote": "", "inference": False,
            "confidence": 0.0}


def _tissue_judgment(window: str, target: str) -> dict:
    t = target.lower()
    key = next((k for k in _TISSUE_HINTS if k in t), "")
    if not key:
        return {"status": "unknown", "value": None, "quote": "", "inference": False,
                "confidence": 0.0}
    pat = re.compile(_TISSUE_HINTS[key], re.I)
    m = pat.search(window or "")
    if not m:
        return {"status": "unknown", "value": None, "quote": "", "inference": False,
                "confidence": 0.0}
    return {"status": "supported", "value": target, "quote": _sentence_window(window, m.start(), 420),
            "inference": False, "confidence": 0.85}


def _modality_judgment(window: str, target: list[str]) -> dict:
    wanted = [str(x).replace("_", " ").lower() for x in (target or []) if str(x).strip()]
    if not wanted:
        return {"status": "unknown", "value": None, "quote": "", "inference": False,
                "confidence": 0.0}
    for key, pat_s in _MODALITY_HINTS.items():
        if any(key in w or w in key for w in wanted):
            m = re.compile(pat_s, re.I).search(window or "")
            if m:
                return {"status": "supported", "value": key,
                        "quote": _sentence_window(window, m.start(), 420),
                        "inference": False, "confidence": 0.85}
    return {"status": "unknown", "value": None, "quote": "", "inference": False,
            "confidence": 0.0}


def _sample_judgment(window: str) -> dict:
    nums = [int(m.group(1)) for m in _SAMPLE_RE.finditer(window or "")]
    if not nums:
        return {"status": "unknown", "value": None, "quote": "", "inference": False,
                "confidence": 0.0}
    # Prefer a total number in parentheses, otherwise the largest stated group.
    paren = re.search(r"\(\s*(\d{1,6})\s+(?:samples?|participants?|individuals?)\s*\)",
                      window or "", re.I)
    value = int(paren.group(1)) if paren else max(nums)
    m = re.search(r"\b" + str(value) + r"\b", window or "")
    return {"status": "supported", "value": value,
            "quote": _sentence_window(window, m.start() if m else 0, 420),
            "inference": False, "confidence": 0.8}


def deterministic_judgment(ctx: dict, profile: dict) -> dict:
    """Conservative fallback when the LLM is unavailable or malformed."""
    w = ctx.get("window") or ""
    constraints: dict[str, dict] = {}
    org = str(profile.get("organism") or "").strip()
    if org:
        constraints["organism"] = _organism_judgment(w, org)
    tissue = str(profile.get("tissue") or "").strip()
    if tissue:
        constraints["tissue"] = _tissue_judgment(w, tissue)
    mods = list(profile.get("modalities") or [])
    if mods:
        constraints["modality"] = _modality_judgment(w, mods)
    if profile.get("sample_size_min") not in (None, "", 0):
        constraints["sample_count"] = _sample_judgment(w)
    return {"id": ctx.get("id"), "accession": ctx.get("accession"),
            "paper_id": ctx.get("paper_id"),
            "usage_type": ctx.get("heuristic_usage") or "unknown",
            "constraints": constraints, "source": "rules"}


# ------------------------------------------------------------------ LLM judge

_QUOTE_JUDGE_SYSTEM = """You are quote_judge, a strict evidence reader for BioAgentDB.
Read ONLY the supplied paper window. Never use outside knowledge, the title alone,
or assumptions about what the study probably did. Return JSON only.

For every context and every requested constraint, return:
- status: "supported", "contradicted", or "unknown";
- value: the actual value (species, tissue, modality, or a number) or null;
- quote: a VERBATIM substring of the supplied window for supported/contradicted;
- inference: true only when the quote implies the value without stating the exact
  term (for example "patients" -> human); otherwise false;
- confidence: 0.0-1.0.

Rules:
- No quote, or a quote not present verbatim, means status=unknown.
- If the paper says a different explicit species/tissue, use contradicted.
- If only a Data Availability accession is present with no description, unknown.
- usage_type is one of generated/reused/mentioned/unknown.
- sample_count must be a number stated in the quote. Do not add groups together.
Return exactly:
{"results":[{"id":"q1","usage_type":"unknown","constraints":{"organism":
{"status":"unknown","value":null,"quote":"","inference":false,"confidence":0.0}}}]}
Only include the requested constraint keys, but include every context."""


def _constraint_payload(profile: dict) -> dict:
    out: dict[str, Any] = {}
    if profile.get("organism"):
        out["organism"] = str(profile["organism"])
    if profile.get("tissue"):
        out["tissue"] = str(profile["tissue"])
    if profile.get("modalities"):
        out["modality"] = [str(x) for x in profile["modalities"] if str(x).strip()]
    if profile.get("sample_size_min") not in (None, "", 0):
        out["sample_count"] = {
            "minimum": int(profile["sample_size_min"]),
            "instruction": "report the stated number; do not decide the comparison",
        }
    return out


def _llm_batch(contexts: list[dict], requested: dict) -> list[dict]:
    from app.harvest import llm

    if not contexts or not requested or not llm.available():
        return []
    payload = {
        "constraints": requested,
        "contexts": [{k: c.get(k) for k in
                      ("id", "accession", "section", "window", "heuristic_usage")}
                     for c in contexts],
    }
    model = cfg.get("deepseek_scoring_model") or cfg.get("deepseek_model")
    data = llm.chat_json([
        {"role": "system", "content": _QUOTE_JUDGE_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ], model=model, timeout=90)
    results = data.get("results") if isinstance(data, dict) else None
    return results if isinstance(results, list) else []


def _validate_one(ctx: dict, raw: dict, requested: dict) -> dict:
    window = ctx.get("window") or ""
    out = {"id": ctx.get("id"), "accession": ctx.get("accession"),
           "paper_id": ctx.get("paper_id"),
           "usage_type": str((raw or {}).get("usage_type") or "").lower(),
           "constraints": {}, "source": "llm"}
    if out["usage_type"] not in USAGE_TYPES:
        out["usage_type"] = ctx.get("heuristic_usage") or "unknown"
    # A Data Availability-only quote cannot establish that this study generated
    # the data; downgrade an over-eager generated label.
    if ctx.get("source") == "da_quote" and out["usage_type"] == "generated":
        out["usage_type"] = "mentioned"
    raw_c = (raw or {}).get("constraints") or {}
    for key in CONSTRAINT_KEYS:
        if key not in requested:
            continue
        r = raw_c.get(key) if isinstance(raw_c, dict) else None
        if not isinstance(r, dict):
            r = {}
        status = str(r.get("status") or "unknown").lower()
        if status not in VALID_STATUS:
            status = "unknown"
        quote = str(r.get("quote") or "").strip()
        value = r.get("value")
        try:
            conf = max(0.0, min(1.0, float(r.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            conf = 0.0
        inference = bool(r.get("inference"))
        # Deterministic gate: supported/contradicted must be backed by a quote
        # that literally occurs in the window.
        if status in ("supported", "contradicted") and not _quote_in_window(quote, window):
            status, value, quote, conf = "unknown", None, "", 0.0
        if key == "sample_count" and status == "supported":
            nums = {int(n) for n in re.findall(r"\b(\d{1,6})\b", quote)}
            if not nums or value is None:
                status, value, quote, conf = "unknown", None, "", 0.0
            else:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    status, value, quote, conf = "unknown", None, "", 0.0
                else:
                    if value not in nums:
                        status, value, quote, conf = "unknown", None, "", 0.0
        # Explicit species conflict is not overridable by the model.
        if key == "organism" and status == "supported":
            target = str(requested.get("organism") or "")
            det = _organism_judgment(window, target)
            if det.get("status") == "contradicted":
                status, value, quote, conf = "contradicted", det.get("value"), det.get("quote"), 0.9
                inference = False
        out["constraints"][key] = {
            "status": status, "value": value, "quote": quote,
            "inference": inference, "confidence": conf,
        }
    return out


def judge_contexts(contexts: list[dict], profile: dict) -> list[dict]:
    """Judge contexts with an LLM and validate every judgment deterministically."""
    requested = _constraint_payload(profile)
    if not contexts:
        return []
    if not requested:
        return [{"id": c["id"], "accession": c["accession"],
                 "usage_type": c.get("heuristic_usage") or "unknown",
                 "constraints": {}, "source": "rules"} for c in contexts]
    judged: dict[str, dict] = {}
    use_llm = cfg.bool("quote_judge_llm", True)
    if os.environ.get("BIOAGENTDB_QUOTE_JUDGE_LLM", "").strip().lower() in (
            "0", "false", "no", "off"):
        use_llm = False
    if use_llm:
        for i in range(0, len(contexts), 4):
            chunk = contexts[i:i + 4]
            try:
                raw_results = _llm_batch(chunk, requested)
            except Exception as exc:  # noqa: BLE001
                logger.warning("quote judge LLM failed: %s", exc)
                raw_results = []
            by_id = {str(r.get("id")): r for r in raw_results if isinstance(r, dict)}
            for ctx in chunk:
                raw = by_id.get(str(ctx.get("id"))) or {}
                if raw:
                    judged[ctx["id"]] = _validate_one(ctx, raw, requested)
    out: list[dict] = []
    for ctx in contexts:
        j = judged.get(ctx["id"])
        if not j:
            j = deterministic_judgment(ctx, profile)
        out.append(j)
    return out


# ------------------------------------------------------------------ matching

def requested_constraints(profile: dict) -> list[str]:
    keys: list[str] = []
    if profile.get("organism"):
        keys.append("organism")
    if profile.get("tissue"):
        keys.append("tissue")
    if profile.get("modalities"):
        keys.append("modality")
    if profile.get("sample_size_min") not in (None, "", 0):
        keys.append("sample_count")
    return keys


def _status_from_fields(d: dict, profile: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    org = str(profile.get("organism") or "").strip()
    if org:
        val = str(d.get("organism") or "").strip()
        if not val:
            out["organism"] = {"status": "unknown", "value": None, "quote": ""}
        elif (("human" in org.lower() or "sapiens" in org.lower())
              and ("human" in val.lower() or "sapiens" in val.lower())):
            out["organism"] = {"status": "supported", "value": val, "quote": ""}
        elif (("mouse" in org.lower() or "mus" in org.lower())
              and ("mouse" in val.lower() or "mus" in val.lower())):
            out["organism"] = {"status": "supported", "value": val, "quote": ""}
        else:
            out["organism"] = {"status": "contradicted", "value": val, "quote": ""}
    tissue = str(profile.get("tissue") or "").strip()
    if tissue:
        val = str(d.get("tissue") or "").strip()
        if not val:
            out["tissue"] = {"status": "unknown", "value": None, "quote": ""}
        elif tissue.lower() in val.lower() or val.lower() in tissue.lower():
            out["tissue"] = {"status": "supported", "value": val, "quote": ""}
        else:
            out["tissue"] = {"status": "unknown", "value": val, "quote": ""}
    mods = list(profile.get("modalities") or [])
    if mods:
        have = [str(d.get("modality") or "")] + [str(x) for x in (d.get("modalities") or [])]
        have_l = " ".join(have).lower()
        if not have_l.strip():
            out["modality"] = {"status": "unknown", "value": None, "quote": ""}
        elif any(str(m).replace("_", " ").lower() in have_l for m in mods):
            out["modality"] = {"status": "supported", "value": have[0] or mods[0], "quote": ""}
        else:
            out["modality"] = {"status": "unknown", "value": have[0], "quote": ""}
    min_n = profile.get("sample_size_min")
    if min_n not in (None, "", 0):
        try:
            n = int(d.get("sample_count"))
        except (TypeError, ValueError):
            n = None
        if n is None:
            out["sample_count"] = {"status": "unknown", "value": None, "quote": ""}
        elif n >= int(min_n):
            out["sample_count"] = {"status": "supported", "value": n, "quote": ""}
        else:
            out["sample_count"] = {"status": "contradicted", "value": n, "quote": ""}
    return out


def _aggregate(constraints: dict[str, dict], requested: list[str]) -> str:
    vals = [constraints.get(k, {}).get("status") or "unknown" for k in requested]
    if "contradicted" in vals:
        return "fail"
    if not vals:
        return "full"
    if all(v == "supported" for v in vals):
        return "full"
    if any(v == "supported" for v in vals):
        return "partial"
    return "unknown"


def _is_restricted(repo: str, url: str) -> bool:
    blob = f"{repo} {url}".lower()
    return any(x in blob for x in ("dbgap", "ega", "synapse", "controlled"))


def _record(accession: str, paper: str, tier: str, usage: str, status: str,
            constraints: dict, quote: str, url: str, note: str = "") -> dict:
    missing = [k for k, v in constraints.items()
               if str((v or {}).get("status") or "unknown") in ("unknown", "")]
    contradicted = [k for k, v in constraints.items()
                    if (v or {}).get("status") == "contradicted"]
    return {"accession": accession, "paper": paper[:160], "source_tier": tier,
            "usage_type": usage, "status": status, "constraints": constraints,
            "missing_constraints": missing,
            "contradicted_constraints": contradicted,
            "quote": quote[:500], "url": url, "note": note}


def match_records(profile: dict, catalog: list[dict], candidates: list[dict],
                  judgments: list[dict]) -> dict:
    """Match each record/dataset against the requested constraints.

    Catalog metadata and paper quotes are separate sources of truth.  A record
    is full only when every requested constraint is supported; unknown is kept
    and disclosed, never discarded.
    """
    requested = requested_constraints(profile)
    by_id = {str(j.get("id")): j for j in judgments}
    by_acc: dict[str, list[dict]] = {}
    for j in judgments:
        by_acc.setdefault(str(j.get("accession") or "").upper(), []).append(j)
    records: list[dict] = []

    for it in catalog:
        for d in (it.get("datasets") or []):
            c = _status_from_fields(d, profile)
            st = _aggregate(c, requested)
            acc = str(d.get("accession") or "")
            records.append(_record(acc, it.get("title") or "", "catalog", "unknown",
                                   st, c, it.get("evidence") or "",
                                   str((d.get("links") or [{}])[0].get("url") or ""),
                                   "catalog metadata and evidence"))
        if not (it.get("datasets") or []):
            records.append(_record("", it.get("title") or "", "catalog", "unknown",
                                   "unknown", {k: {"status": "unknown", "value": None,
                                                   "quote": ""} for k in requested},
                                   it.get("evidence") or "", "",
                                   "catalog record has no dataset metadata"))

    for c in candidates:
        for d in (c.get("datasets") or []):
            acc = str(d.get("accession") or "")
            ctxs = by_acc.get(acc.upper()) or []
            # Prefer a judgment from the same paper when available.
            j = next((x for x in ctxs if x.get("paper_id") and
                      x.get("paper_id") == c.get("gid")), None)
            if j is None and ctxs:
                j = ctxs[0]
            if j:
                cj = dict(j.get("constraints") or {})
                usage = j.get("usage_type") or "unknown"
                # The LLM reports the stated count; compare it to the minimum here.
                try:
                    stated = int((cj.get("sample_count") or {}).get("value"))
                except (TypeError, ValueError):
                    stated = None
                if "sample_count" in requested and stated is not None:
                    if stated >= int(profile.get("sample_size_min")):
                        cj["sample_count"] = {"status": "supported", "value": stated,
                                               "quote": (cj.get("sample_count") or {}).get("quote", "")}
                    else:
                        cj["sample_count"] = {"status": "contradicted", "value": stated,
                                               "quote": (cj.get("sample_count") or {}).get("quote", "")}
            else:
                pseudo = {"window": d.get("quote") or "", "heuristic_usage": "unknown"}
                j = deterministic_judgment(pseudo, profile)
                cj = dict(j.get("constraints") or {})
                usage = "unknown"
            st = _aggregate(cj, requested)
            url = d.get("url") or ""
            if _is_restricted(d.get("repository") or "", url):
                st = "restricted"
            quote = ""
            for k in requested:
                if (cj.get(k) or {}).get("quote"):
                    quote = str(cj[k]["quote"])
                    break
            records.append(_record(acc, c.get("title") or "", "paper", usage, st, cj,
                                   quote, url,
                                   "quote-validated" if j.get("source") == "llm" else
                                   "rule-validated"))

    # Attach statuses back to datasets/items so the workbench can badge them.
    status_by_acc = {r["accession"].upper(): r for r in records if r["accession"]}
    for it in catalog:
        for d in it.get("datasets") or []:
            rec = status_by_acc.get(str(d.get("accession") or "").upper())
            d["match_status"] = rec["status"] if rec else "unknown"
            if rec:
                d["constraint_gaps"] = rec.get("missing_constraints") or []
                d["constraint_contradictions"] = rec.get("contradicted_constraints") or []
    for c in candidates:
        for d in c.get("datasets") or []:
            rec = status_by_acc.get(str(d.get("accession") or "").upper())
            d["match_status"] = rec["status"] if rec else "unknown"
            if rec:
                d["constraint_gaps"] = rec.get("missing_constraints") or []
                d["constraint_contradictions"] = rec.get("contradicted_constraints") or []

    counts: dict[str, int] = {}
    usage_counts: dict[str, int] = {}
    for r in records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        usage_counts[r["usage_type"]] = usage_counts.get(r["usage_type"], 0) + 1
    disclosure: list[str] = []
    if requested:
        if not counts.get("full"):
            disclosure.append("0 records meet all requested constraints; "
                              "partial/unknown are not full matches")
        else:
            disclosure.append(f"{counts['full']} record(s) meet all requested constraints")
    if counts.get("unknown"):
        disclosure.append(f"{counts['unknown']} record(s) have unknown evidence")
    if counts.get("partial"):
        disclosure.append(f"{counts['partial']} record(s) only partially match")
    if counts.get("restricted"):
        disclosure.append(f"{counts['restricted']} controlled-access record(s) returned")
    if counts.get("fail"):
        disclosure.append(f"{counts['fail']} record(s) contradict a requested constraint")
    return {"requested": requested, "records": records, "counts": counts,
            "eligible": [r for r in records if r["status"] == "full"],
            "usage_counts": usage_counts, "disclosure": disclosure,
            "rubric_ok": not any(r["status"] == "full" and requested and
                                 any((r["constraints"].get(k) or {}).get("status") != "supported"
                                     or not (r["constraints"].get(k) or {}).get("quote")
                                     for k in requested)
                                 for r in records if r["source_tier"] == "paper")}
