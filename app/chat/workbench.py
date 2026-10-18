"""对话工作台编排器（v1.5）：不限疾病 · 只发现不入库 · 四路全文。

形态参照 vendor/omics-discovery（lite）：画像澄清 → 查询扩展 → 目录 + 五源检索
→ 去重 → 论文内数据集提取（DA 段优先）→ 核验 → rubric → 三类结果报告。

事件流（SSE data 行，JSON）：token / step / profile / clarify / refusal /
results / report / error / done。结构化结果不经模型文本搬运。
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import threading
import time

from app.catalog import store
from app.chat import paper_datasets, web_research
from app.core.db import db, utcnow
from app.core.relevance import THEME_KEYWORDS
from app.core.utils import new_id, norm_space
from app.harvest import dedup
from app.harvest.extract import find_identifiers

logger = logging.getLogger(__name__)

from app.core.config import cfg

TOP_N = cfg.int("workbench_top_n", 10)
K_FULLTEXT = cfg.int("workbench_k_fulltext", 8)
SOURCE_WANT = cfg.int("workbench_source_want", 15)
TIME_BUDGET_S = cfg.int("workbench_time_budget_s", 25)
VERIFY_MAX = cfg.int("workbench_verify_max", 6)
MAX_CLARIFY = cfg.int("workbench_max_clarify", 2)
PDF_SESSION_CAP = cfg.int("workbench_pdf_session_cap", 20)

DEFAULT_ORGANISM = cfg.get("workbench_default_organism", "Homo sapiens")
REQUIRED_FIELDS = ("topic", "organism")

_FIELD_QUESTIONS = {
    "topic": ("What should I look for? A disease, a biological process, or a few keywords "
              "— e.g. \"liver regeneration\", \"Alzheimer\", \"spatial atlas of mouse brain\"."),
    "organism": ("Which organism — human, mouse, or another? (say \"any\" to skip)"),
    "modality": ("Any technique preference, e.g. scRNA-seq, spatial transcriptomics, "
                 "proteomics? (optional)"),
    "min_samples": "Any minimum sample count? (optional)",
}
DISEASE_CHIPS = ["Alzheimer's disease", "ALS", "Parkinson's disease",
                 "frontotemporal dementia", "Huntington's disease", "prion disease",
                 "PSP", "DLB", "MSA", "spinocerebellar ataxia"]
MODALITY_CHIPS = ["single-nucleus RNA-seq", "single-cell RNA-seq",
                  "spatial transcriptomics", "bulk RNA-seq", "proteomics",
                  "epigenomics", "genomics", "multi-omics"]
EXAMPLES = [
    "spatial transcriptomics of mouse brain development",
    "single-nucleus RNA-seq of human brain in Alzheimer's disease, at least 20 samples",
    "proteomics of Parkinson's disease CSF",
]

_THEME_RE = {slug: re.compile(pat, re.I) for slug, pat in THEME_KEYWORDS.items()}
_THEME_NAMES = {
    "alzheimers-disease": "Alzheimer's disease", "als": "ALS",
    "parkinsons-disease": "Parkinson's disease", "frontotemporal-dementia":
    "frontotemporal dementia", "huntingtons-disease": "Huntington's disease",
    "prion-disease": "prion disease", "progressive-supranuclear-palsy": "PSP",
    "dementia-with-lewy-bodies": "DLB", "multiple-system-atrophy": "MSA",
    "spinocerebellar-ataxia": "spinocerebellar ataxia",
}

_MODALITY_HINTS = {
    "scrna": ["scrna", "single cell", "single-cell", "10x"],
    "snrna": ["snrna", "single nucleus", "single-nucleus"],
    "spatial_transcriptomics": ["spatial", "visium", "stereo-seq"],
    "transcriptomics": ["bulk rna", "transcriptome", "rna-seq", "rna seq", "microarray"],
    "epigenomics": ["methylation", "bisulfite", "atac", "chip-seq", "epigenom"],
    "proteomics": ["proteom", "mass spec", "cytof"],
    "genomics": ["wgs", "whole genome", "gwas", "exome", "genom"],
    "metabolomics": ["metabolom", "lipidom"],
    "multi_omics": ["multi-omic", "multiomic", "multi omic"],
}
_MODALITY_WORDS = {w for hints in _MODALITY_HINTS.values() for w in hints}
_BIOMED_RE = re.compile(
    r"disease|cancer|tumou?r|cell|gene|protein|rna|dna|sequenc|brain|neuron|liver|kidney|"
    r"blood|tissue|organism|mouse|human|patient|clinical|omics|genom|transcriptom|proteom|"
    r"metabol|microbiom|epigen|atlas|expression|mutation|cohort|biops|immune|microglia|"
    r"cardio|neuro|biolog|medic", re.I)
_WAIVE_RE = re.compile(r"\b(any|no preference|don'?t care|whatever|either)\b|不限|都行|无所谓", re.I)
_REFRESH_RE = re.compile(r"search again|refresh|retry|重新检索|再查", re.I)

SOURCE_LABEL = {"europepmc": "Europe PMC", "openalex": "OpenAlex", "core": "CORE",
                "pubmed": "PubMed", "preprints": "bioRxiv/medRxiv"}
_PHASE_DONE = object()


def _run_phase_with_events(work, phase: str, heartbeat_s: float = 5.0):
    """在子线程里跑 work(emit)；过程中产出 progress 事件，>heartbeat_s 无事件时发心跳。"""
    q: queue.Queue = queue.Queue()
    box: dict = {}

    def emit(text: str) -> None:
        q.put({"event": "progress", "phase": phase, "text": str(text)[:220],
               "t_ms": int(time.time() * 1000)})

    def runner() -> None:
        try:
            box["value"] = work(emit)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            q.put(_PHASE_DONE)

    threading.Thread(target=runner, daemon=True).start()
    last = time.time()
    while True:
        try:
            item = q.get(timeout=0.5)
        except queue.Empty:
            gap = time.time() - last
            if gap >= heartbeat_s:
                yield {"event": "progress", "phase": phase, "heartbeat": True,
                       "text": f"still working… ({int(gap)}s)",
                       "t_ms": int(time.time() * 1000)}
                last = time.time()
            continue
        if item is _PHASE_DONE:
            break
        last = time.time()
        yield item
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ---------------------------------------------------------------- sessions

def create_session(title: str = "", kind: str = "research") -> dict:
    """L-0234：kind = research（检索工作台）| ming（挖掘对话）。"""
    kind = "ming" if str(kind or "").strip().lower() == "ming" else "research"
    sid = new_id("chat")
    now = utcnow()
    with db() as conn:
        conn.execute(
            "INSERT INTO chat_sessions(id, title, state, kind, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?)", (sid, (title or "")[:80], "{}", kind, now, now))
    return {"id": sid, "title": title or "", "kind": kind}


def list_sessions(limit: int = 30, kind: str | None = None) -> list[dict]:
    with db() as conn:
        if kind in ("research", "ming"):
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at FROM chat_sessions "
                "WHERE kind=? ORDER BY updated_at DESC LIMIT ?", (kind, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at FROM chat_sessions "
                "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_session(sid: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (sid,)).fetchone()
    return dict(row) if row else None


def get_messages(sid: str, limit: int = 200) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE session_id=? ORDER BY rowid DESC LIMIT ?",
            (sid, limit)).fetchall()
        rows = rows[::-1]          # 取最近 limit 条，再按插入顺序返回
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except Exception:  # noqa: BLE001
            d["meta"] = {}
        out.append(d)
    return out


def add_message(sid: str, role: str, content: str, meta: dict | None = None) -> None:
    now = utcnow()
    with db() as conn:
        conn.execute(
            "INSERT INTO chat_messages(id, session_id, role, content, meta, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (new_id("msg"), sid, role, content or "",
             json.dumps(meta or {}, ensure_ascii=False), now))
        conn.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (now, sid))


def stamp_steps(sid: str, steps: list[dict]) -> None:
    """把本轮 step/progress 轨迹并入最后一条 assistant 消息的 meta（历史会话可回看）。

    同名 step 只保留一条（原位保留、内容取最后一次）——空结果重跑会二次发出
    "sources" 步骤，回放不应重复渲染（R-v16 F-8）。
    """
    if not steps:
        return
    compact: list[dict] = []
    index: dict[str, int] = {}
    for ev in steps[-80:]:
        if ev.get("event") == "step":
            item = {k: ev[k] for k in ("event", "name", "status", "detail", "counts", "ms")
                    if k in ev}
            name = str(item.get("name") or "")
            if name in index:
                compact[index[name]] = item
            else:
                index[name] = len(compact)
                compact.append(item)
        elif ev.get("event") == "progress" and not ev.get("heartbeat"):
            compact.append({"event": "progress", "text": str(ev.get("text") or "")[:200]})
    with db() as conn:
        row = conn.execute(
            "SELECT id, meta FROM chat_messages WHERE session_id=? AND role='assistant' "
            "ORDER BY rowid DESC LIMIT 1", (sid,)).fetchone()
        if not row:
            return
        meta = json.loads(row["meta"] or "{}")
        meta["steps"] = compact
        conn.execute("UPDATE chat_messages SET meta=? WHERE id=?",
                     (json.dumps(meta, ensure_ascii=False), row["id"]))


def _state(sid: str) -> dict:
    s = get_session(sid)
    try:
        return json.loads((s or {}).get("state") or "{}")
    except Exception:  # noqa: BLE001
        return {}


def _set_state(sid: str, state: dict) -> None:
    with db() as conn:
        conn.execute("UPDATE chat_sessions SET state=?, updated_at=? WHERE id=?",
                     (json.dumps(state, ensure_ascii=False), utcnow(), sid))


# ---------------------------------------------------------------- profile (W1)

def _norm_modalities(text: str, given: list[str]) -> list[str]:
    out: list[str] = []
    hay = (text or "").lower()
    for code, hints in _MODALITY_HINTS.items():
        if any(h in hay for h in hints):
            out.append(code)
    for m in given or []:
        ml = str(m).lower()
        for code, hints in _MODALITY_HINTS.items():
            if any(h in ml for h in hints) or ml == code:
                if code not in out:
                    out.append(code)
                break
        else:
            if ml and ml not in out and ml != "other":
                out.append(str(m))
    return out[:5]


def _clean_keywords(keywords: list[str], profile: dict) -> list[str]:
    out = []
    drop = {str(profile.get("disease") or "").lower(), str(profile.get("topic") or "").lower()}
    for k in keywords or []:
        kl = str(k).strip().lower()
        if not kl or kl in drop:
            continue
        if any(w in kl or kl in w for w in _MODALITY_WORDS):
            continue
        if kl not in [o.lower() for o in out]:
            out.append(str(k).strip())
    return out[:8]


def _rule_extract(message: str) -> dict:
    text = message or ""
    theme = next((slug for slug, pat in _THEME_RE.items() if pat.search(text)), "")
    disease = _THEME_NAMES.get(theme, "")
    topic = disease or ""
    if not topic:
        # 取首个非停用词的短语作为主题
        m = re.search(r"([a-z][a-z\- ]{3,60})", text.lower())
        if m and _BIOMED_RE.search(text):
            topic = m.group(1).strip()
    organism = "Mus musculus" if re.search(r"\bmouse|mice\b", text, re.I) else (
        "Homo sapiens" if re.search(r"\bhuman|patient|clinical\b", text, re.I) else "")
    return {"topic": topic, "disease": disease, "theme": theme, "organism": organism,
            "tissue": "", "modalities": _norm_modalities(text, []),
            "sample_size_min": None, "keywords": [], "constraints": [],
            "waived": ["organism"] if _WAIVE_RE.search(text) else []}


def extract_profile(sid: str, history: list[str], state: dict) -> tuple[dict, bool, list[str], str, str]:
    """返回 (profile, ready, missing, reply, domain)。对整段对话 1 次 LLM。"""
    prev = dict(state.get("profile") or {})
    clarify_count = int(state.get("clarify_count") or 0)
    conversation = "\n".join(history[-12:])
    data: dict = {}
    llm_ok = False
    try:
        from app.harvest.llm import available as llm_available, chat_json
        llm_ok = llm_available()
        if llm_ok:
            prompt = (
                "You extract a research profile for a biomedical dataset-discovery assistant. "
                "Return JSON ONLY: {\"domain\": \"biomedical\"|\"other\", \"reply\": str, "
                "\"profile\": {\"topic\": str, \"disease\": str, \"organism\": str, "
                "\"tissue\": str, \"modalities\": [str], \"sample_size_min\": int|null, "
                "\"keywords\": [str], \"constraints\": [str]}, "
                "\"research\": {\"needed\": bool, \"query\": str}, "
                "\"waived\": [\"organism\"|\"topic\"|\"modality\"|\"min_samples\"]}. "
                "Rules: domain=biomedical when the request is about biology, medicine or omics "
                "(ANY topic — a disease, a process, an organ, a technique; not only diseases); "
                "domain=other for weather, stocks, politics, code, small talk. "
                "topic = the research subject; disease only if a specific disease is named. "
                "research.needed = true when finding the right data depends on knowledge you may "
                "not have — a specific competition, project, platform, dataset format or event "
                "(e.g. \"virtual cell challenge 2026\") — then research.query = a short web search "
                "phrase (same language as the user) to clarify it; otherwise needed=false and "
                "query=\"\". "
                "waived = fields the user explicitly left open (\"any\", \"no preference\"). "
                "Never invent values.")
            data = chat_json([
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(
                    {"conversation": conversation[:3000],
                     "current_profile": prev}, ensure_ascii=False)},
            ], model=None, timeout=60)
            if not isinstance(data, dict):
                data = {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile LLM failed: %s", exc)
    if not llm_ok or not data:
        data = {"domain": "biomedical" if (_BIOMED_RE.search(conversation)
                                           or any(p.search(conversation)
                                                  for p in _THEME_RE.values())) else "other",
                "reply": "", "profile": _rule_extract(conversation), "waived": []}

    domain = str(data.get("domain") or "biomedical").lower()
    new = data.get("profile") or {}
    profile = {**prev}
    for k in ("topic", "disease", "organism", "tissue"):
        v = str(new.get(k) or "").strip()
        if v:
            profile[k] = v
    if new.get("modalities"):
        profile["modalities"] = _norm_modalities(conversation, list(new["modalities"]))
    elif not profile.get("modalities"):
        profile["modalities"] = _norm_modalities(conversation, [])
    if new.get("keywords"):
        profile["keywords"] = _clean_keywords(list(new["keywords"]), profile)
    if new.get("constraints"):
        profile["constraints"] = [str(c)[:60] for c in new["constraints"]][:5]
    try:
        ms = new.get("sample_size_min")
        profile["sample_size_min"] = int(ms) if ms not in (None, "", 0) else \
            profile.get("sample_size_min")
    except (TypeError, ValueError):
        pass
    if not profile.get("theme"):
        for slug, pat in _THEME_RE.items():
            if pat.search(str(profile.get("disease") or "") or conversation):
                profile["theme"] = slug
                break
    if profile.get("disease") and not profile.get("topic"):
        profile["topic"] = profile["disease"]

    waived = list(dict.fromkeys(list(prev.get("waived") or []) + list(data.get("waived") or [])))
    if _WAIVE_RE.search(conversation):
        for f in ("organism", "modality", "min_samples"):
            if f not in waived:
                waived.append(f)
    profile["waived"] = waived

    # 默认值填充（organism 澄清过 1 轮后）
    defaults = list(profile.get("defaults") or [])
    if not profile.get("organism") and "organism" not in waived and clarify_count >= 1:
        profile["organism"] = DEFAULT_ORGANISM
        if "organism" not in defaults:
            defaults.append("organism")
    profile["defaults"] = defaults

    missing = [f for f in REQUIRED_FIELDS
               if not profile.get(f) and f not in waived]
    if not profile.get("modalities") and "modality" not in waived and clarify_count < 1:
        missing.append("modality")
    ready = not [f for f in missing if f in REQUIRED_FIELDS]
    if ready and missing == ["modality"] and clarify_count >= 1:
        ready = True
        missing = []

    # v1.6（L-0292）：把"未知知识"信号留给 run_turn 决策（是否联网查证）
    research = data.get("research") if isinstance(data.get("research"), dict) else {}
    r_needed = bool(research.get("needed"))
    if not r_needed and not llm_ok:
        r_needed = web_research.looks_like_unknown_topic(conversation)
    state["research_signal"] = {"needed": r_needed,
                                "query": norm_space(research.get("query"))[:120]}

    state["profile"] = profile
    state["clarify_count"] = clarify_count + (0 if ready else 1)
    _set_state(sid, state)
    return profile, ready, missing, str(data.get("reply") or "").strip(), domain


def _pick_questions(state: dict, missing: list[str]) -> list[str]:
    asked = set(state.get("asked") or [])
    keys = [k for k in missing if k in _FIELD_QUESTIONS and k not in asked]
    keys.sort(key=lambda k: 0 if k in REQUIRED_FIELDS else 1)
    picked = keys[:2]
    state["asked"] = list(asked | set(picked))
    return [_FIELD_QUESTIONS[k] for k in picked]


# ---------------------------------------------------------------- search (W2)

def _fingerprint(profile: dict) -> str:
    core = {k: profile.get(k) for k in
            ("topic", "disease", "organism", "tissue", "modalities",
             "sample_size_min", "keywords")}
    return hashlib.sha1(json.dumps(core, sort_keys=True, ensure_ascii=False)
                        .encode("utf-8")).hexdigest()[:16]


def _extract_terms(text: str) -> list[str]:
    """LLM 输出的检索式提取（L-0223）：兼容"每行一条"与 JSON 漂移（`[{"content": ...}]`）。

    JSON 形态 → 递归取字符串；失败 → 引号片段/按行；统一按换行/字面 \\n 切分、去噪、去重。
    """
    import json as _json
    import re as _re

    raw = str(text or "").strip()
    candidates: list[str] = []
    if raw[:1] in "[{":
        try:
            parsed = _json.loads(raw)
        except Exception:  # noqa: BLE001
            parsed = None
        if parsed is not None:
            def walk(o):
                if isinstance(o, str):
                    candidates.append(o)
                elif isinstance(o, dict):
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(parsed)
    if not candidates:
        quoted = _re.findall(r'["\']([^"\']{6,240})["\']', raw)
        candidates = quoted or raw.splitlines()
    out: list[str] = []
    for c in candidates:
        for piece in _re.split(r"[\n\r]+|\\n", c):
            p = piece.strip(" -*•\t\"'[]{}:,")
            # 去除伪 JSON 头部残迹（content' : ' / content": " 等）
            p = _re.sub(r'^[A-Za-z_ ]{1,24}["\']\s*:\s*["\']?', "", p).strip(" -*•\t\"'[]{}:,")
            if 4 < len(p) <= 140 and p.lower() not in (
                    "content", "queries", "query", "phrases", "search phrases",
                    "tool_calls", "finish_reason", "role", "assistant", "function",
                    "arguments", "stop", "error", "type"):
                out.append(p)
    return list(dict.fromkeys(out))[:10]


def expand_queries(profile: dict, state: dict,
                   research: dict | None = None) -> list[str]:
    """画像 → 6-10 检索词变体；research（v1.6）的查证词置顶、并计入缓存指纹。"""
    research_terms = [str(t) for t in ((research or {}).get("search_terms") or []) if t]
    fp = _fingerprint(profile)
    if research_terms:
        fp += "|r:" + hashlib.sha1(
            json.dumps(research_terms, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:10]
    cache = state.get("qcache") or {}
    if fp in cache:
        return list(cache[fp])
    topic = profile.get("topic") or profile.get("disease") or "omics"
    terms: list[str] = []
    try:
        from app.harvest.llm import available as llm_available, chat
        if llm_available():
            rctx = ""
            if research_terms or (research or {}).get("summary"):
                hints = ""
                rmods = [str(m) for m in ((research or {}).get("modalities") or []) if m]
                rms = (research or {}).get("min_samples")
                if rmods:
                    hints += (" Modality hints (do NOT override the user): "
                              + ", ".join(rmods[:3]) + ".")
                if isinstance(rms, int) and rms > 0:
                    hints += f" Sample-size hint (do NOT override the user): >= {rms}."
                rctx = (" Background from web research: "
                        + str((research or {}).get("summary") or "")[:180]
                        + ". Include these researched terms: "
                        + ", ".join(f'"{t}"' for t in research_terms[:6]) + "."
                        + hints)
            resp = chat([{"role": "user", "content":
                          "Return 6-10 short English search phrases (one per line, no "
                          "numbering) for finding research papers that announce public "
                          f"datasets about: {topic}. Context: "
                          f"organism={profile.get('organism') or 'any'}, "
                          f"tissue={profile.get('tissue') or 'any'}, "
                          f"modalities={','.join(profile.get('modalities') or []) or 'any'}. "
                          "Include dataset/omics wording variants."
                          f"{rctx}"}],
                        model=None, timeout=45)
            text = ""
            if resp is not None:
                if isinstance(resp, dict):
                    # L-0225 修复 R49-F3：llm.chat 返回 dict（{content, tool_calls, ...}）
                    text = resp.get("content") or ""
                else:
                    c = getattr(resp, "content", None)
                    text = c if isinstance(c, str) else str(resp)
            terms = _extract_terms(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("query expansion failed: %s", exc)
    fallback = [topic]
    fallback += [str(k) for k in (profile.get("keywords") or [])[:4]]
    for m in (profile.get("modalities") or [])[:2]:
        fallback.append(f"{topic} {m.replace('_', ' ')}")
    if profile.get("organism"):
        fallback.append(f"{topic} {profile['organism']}")
    if profile.get("tissue"):
        fallback.append(f"{topic} {profile['tissue']}")
    fallback += [f"{topic} dataset", f"{topic} sequencing"]
    combined: list[str] = []
    seen_q: set[str] = set()
    for t in (research_terms + terms + fallback):
        t = str(t or "").strip()
        if not t:
            continue
        key = t.casefold()
        if key in seen_q:
            continue
        seen_q.add(key)
        combined.append(t)
        if len(combined) >= 10:
            break
    cache[fp] = combined
    state["qcache"] = dict(list(cache.items())[-20:])
    return combined


def search_catalog(profile: dict, limit: int = 12) -> list[dict]:
    q = profile.get("disease") or profile.get("topic") or ""
    if not q:
        return []
    try:
        data = store.list_records(q=q, theme=profile.get("theme") or "", limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog search failed: %s", exc)
        return []
    items = []
    for it in data.get("items", []):
        datasets = it.get("datasets") or []
        ev = (it.get("evidence") or [{}])[0]
        items.append({
            "type": "catalog",
            "paper_id": it.get("id"),
            "title": it.get("title") or "",
            "year": it.get("year"),
            "datasets": [{"accession": (d.get("links") or [{}])[0].get("accession", ""),
                          "repository": d.get("repository") or "",
                          "status": d.get("status") or ""} for d in datasets[:5]],
            "evidence": ev.get("quote") or "",
            "n_datasets": len(datasets),
        })
    return items


def _adapters():
    from app.sources.core_api import CoreAdapter
    from app.sources.europepmc import EuropePmcAdapter, PreprintsAdapter
    from app.sources.openalex import OpenAlexAdapter
    from app.sources.pubmed import PubMedAdapter
    # L-0393: fast sources first — a slow CORE must not exhaust the budget for
    # PubMed/preprints (observed: CORE 195 s, PubMed+preprints skipped).
    return [EuropePmcAdapter(), OpenAlexAdapter(), PubMedAdapter(),
            PreprintsAdapter(), CoreAdapter()]


def search_sources(profile: dict, queries: list[str],
                   on_progress=None) -> tuple[list[dict], dict, dict]:
    """五源检索 → 去重。返回 (candidates, per_source, members_map)。"""
    seen: set[str] = set()
    raw: list[dict] = []
    per_source: dict[str, dict] = {}
    deadline = time.time() + TIME_BUDGET_S
    for adapter in _adapters():
        t0 = time.time()
        label = SOURCE_LABEL.get(adapter.name, adapter.name)
        rec = {"hits": 0, "ms": 0, "ok": True, "error": ""}
        if time.time() > deadline:
            rec.update({"ok": False, "error": "skipped (time budget)"})
            per_source[adapter.name] = rec
            if on_progress:
                on_progress(f"{label}: skipped (time budget)")
            continue
        try:
            papers = adapter.search(profile.get("topic") or profile.get("disease") or "",
                                    queries[:4], SOURCE_WANT, seen)
            rec["hits"] = len(papers)
            raw.extend(p.to_row() for p in papers)
        except Exception as exc:  # noqa: BLE001
            rec.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:120]})
            logger.warning("source %s failed: %s", adapter.name, exc)
        rec["ms"] = int((time.time() - t0) * 1000)
        per_source[adapter.name] = rec
        if on_progress:
            if rec["ok"]:
                on_progress(f"{label}: {rec['hits']} candidate(s) · {rec['ms']/1000:.1f}s")
            else:
                on_progress(f"{label}: failed — {rec['error'] or 'unknown'}")
    if not raw:
        return [], per_source, {}
    result = dedup.merge_records(raw)
    members_map: dict[str, list[dict]] = {}
    for lg in result["merge_log"]:
        members_map[lg["gid"]] = json.loads(lg["members"])
    return result["papers"], per_source, members_map


def presort(candidates: list[dict], profile: dict,
            research: dict | None = None) -> list[dict]:
    org = (profile.get("organism") or "").lower()
    mods = [m.replace("_", " ") for m in (profile.get("modalities") or [])]
    excl = [str(e).strip().lower() for e in ((research or {}).get("exclusions") or [])
            if str(e).strip()][:4]

    def score(c: dict) -> float:
        s = 0.0
        s += 3.0 * len(json.loads(c.get("sources") or "[]"))
        if c.get("doi"):
            s += 1.0
        if c.get("is_oa"):
            s += 0.5
        text = f"{c.get('title') or ''} {c.get('abstract') or ''}".lower()
        if org and (org in text or ("human" in org and "patient" in text)):
            s += 2.0
        if any(m and m in text for m in mods):
            s += 1.5
        if excl and any(e in text for e in excl):
            s -= 1.5  # 查证排除项：软惩罚（不硬删；L-0292）
        ids = find_identifiers(f"{c.get('title') or ''} {c.get('abstract') or ''}")
        if ids:
            s += 2.0
        y = c.get("year") or 0
        s += min(1.0, max(0, (int(y) - 2015) * 0.1)) if y else 0
        return s
    return sorted(candidates, key=score, reverse=True)


def _link_for(c: dict) -> str:
    url = c.get("oa_url") or c.get("landing_url") or ""
    if url:
        return str(url)
    doi = str(c.get("doi") or "").strip()
    if doi:
        return f"https://doi.org/{doi}"
    pmid = str(c.get("pmid") or "").strip()
    if pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    return ""


def _fast_status(url: str) -> tuple[bool, str]:
    from app.core import utils
    try:
        resp = utils._request("HEAD", url, timeout=4.0, retries=0)
        if resp is None:
            resp = utils._request("GET", url, timeout=5.0, retries=0)
        if resp is None:
            return False, ""
        code = resp.status_code
        return (code < 400 or code in (401, 403, 405, 406, 429)), str(code)
    except Exception:  # noqa: BLE001
        return False, ""


def verify_candidates(candidates: list[dict], on_progress=None) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    for c in candidates:
        c["landing_url"] = _link_for(c)
        c.setdefault("t2", False)
    subset = [c for c in candidates[:VERIFY_MAX] if c.get("landing_url")]
    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_fast_status, c["landing_url"]): c for c in subset}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                ok, status = fut.result(timeout=12)
                c["t2"] = bool(ok)
                c["http_status"] = status
            except Exception:  # noqa: BLE001
                c["t2"] = False
            done += 1
            if on_progress and (done % 2 == 0 or done == len(subset)):
                on_progress(f"checked {done}/{len(subset)} link(s)")
    # 数据集链接核验（预算 ≤4 条）
    checked = 0
    for c in candidates[:K_FULLTEXT]:
        for d in c.get("datasets") or []:
            if checked >= 4 or not d.get("url"):
                continue
            ok, status = _fast_status(d["url"])
            d["t2"] = bool(ok)
            checked += 1
            if on_progress:
                on_progress(f"dataset link {d.get('accession')}: "
                            f"{'ok' if ok else 'unreachable'}")
    return candidates


def _resolve_pmcid(doi: str, pmid: str) -> str:
    """DOI/PMID → PMCID（Europe PMC 检索）。"""
    from app.core import utils

    q = f'DOI:"{doi}"' if doi else (f"EXT_ID:{pmid}" if pmid else "")
    if not q:
        return ""
    data = utils.get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                          params={"query": q, "format": "json", "pageSize": 1})
    try:
        hit = ((data or {}).get("resultList") or {}).get("result") or []
        return (hit[0].get("pmcid") or "") if hit else ""
    except Exception:  # noqa: BLE001
        return ""


def verify_candidate_live(cand: dict) -> dict:
    """现场核验单篇候选：解析 PMC 全文 → 抽取 announced accessions → 检查链接。

    返回 {title, checked_at, pmcid, fulltext_kind, datasets:[{accession, repository,
    quote, da_evidence, url, t2, http_status}], link:{url, ok, http_status}, note}。
    """
    from app.core import utils, vocab

    out = {"title": cand.get("title") or "", "checked_at": utcnow(),
           "pmcid": "", "fulltext_kind": "none", "datasets": [], "link": None, "note": ""}
    pmcid = _resolve_pmcid((cand.get("doi") or "").strip(), (cand.get("pmid") or "").strip())
    out["pmcid"] = pmcid
    if pmcid:
        text, kind = paper_datasets.fetch_fulltext(
            {"pmcid": pmcid, "pmid": cand.get("pmid") or "", "doi": cand.get("doi") or "",
             "is_preprint": bool(cand.get("is_preprint"))}, [], pdf_budget_left=0)
        if text:
            out["fulltext_kind"] = kind
            ds, _from_da = paper_datasets.datasets_from_text(
                text, is_xml=kind in ("europepmc_fulltext", "ncbi_efetch", "pmc_oa",
                                      "pmc", "preprint"))
            for d in ds[:8]:
                try:
                    repo, url, _dl, _lbl = vocab.canonical_links(d["accession"])
                except Exception:  # noqa: BLE001
                    repo, url = "", ""
                d["repository"] = repo or d.get("repository") or ""
                d["url"] = url
                if url:
                    ok, status = _fast_status(url)
                    d["t2"] = bool(ok)
                    d["http_status"] = status
                else:
                    d["t2"] = False
                    d["http_status"] = ""
                d["da_evidence"] = bool(d.get("da_evidence"))
            out["datasets"] = ds[:8]
    url = cand.get("landing_url") or _link_for(cand)
    if url:
        ok, status = _fast_status(url)
        out["link"] = {"url": url, "ok": bool(ok), "http_status": status}
    if out["datasets"]:
        n_ok = sum(1 for d in out["datasets"] if d.get("t2"))
        out["note"] = (f"{len(out['datasets'])} accession(s) found in the full text"
                       + (f" — {n_ok} link(s) reachable" if n_ok else " — links not reachable"))
    elif out["fulltext_kind"] != "none":
        out["note"] = "full text read — no announced accession found"
    else:
        out["note"] = "no open full text found — nothing to verify yet"
    return out


def rubric_check(report: dict) -> dict:
    issues: list[str] = []
    for item in report.get("items", []):
        if item.get("type") == "catalog" and not item.get("evidence"):
            issues.append(f"catalog item without evidence: {item.get('title', '')[:40]}")
        if item.get("type") == "candidate":
            item["label"] = "unverified"
            for d in item.get("datasets") or []:
                if not re.fullmatch(r"[A-Za-z0-9_.:\-]{3,40}", str(d.get("accession") or "")):
                    issues.append(f"odd accession: {d.get('accession')}")
    if len(report.get("items", [])) > TOP_N:
        issues.append("top-n exceeded")
    report["rubric_issues"] = issues
    report["rubric_ok"] = not issues
    return report


def build_report(profile: dict, catalog: list[dict], candidates: list[dict],
                 per_source: dict, fulltext_stats: dict,
                 research: dict | None = None) -> dict:
    items = list(catalog[:6])
    for c in candidates[:TOP_N - len(items)]:
        items.append({
            "type": "candidate",
            "title": c.get("title") or "",
            "year": c.get("year"),
            "sources": json.loads(c.get("sources") or "[]"),
            "doi": c.get("doi") or "",
            "pmid": c.get("pmid") or "",
            "landing_url": c.get("landing_url") or _link_for(c),
            "oa_url": c.get("oa_url") or "",
            "t2": bool(c.get("t2")),
            "is_preprint": bool(c.get("is_preprint")),
            "fulltext_kind": c.get("fulltext_kind") or "none",
            "datasets": c.get("datasets") or [],
        })
    n_datasets = sum(len(i.get("datasets") or []) for i in items
                     if i.get("type") == "candidate")
    report = {
        "profile": profile,
        "counts": {"catalog": len(catalog), "candidates": len(candidates),
                   "datasets": max(n_datasets, int((fulltext_stats or {}).get("datasets") or 0))},
        "per_source": per_source,
        "fulltext_stats": fulltext_stats,
        "items": items,
        "relax_suggestion": ("" if (catalog or candidates) else
                             "No matches this round — try relaxing conditions "
                             "(broader keywords, another organism, or drop the technique "
                             "filter) and ask again."),
        "generated_at": utcnow(),
    }
    if research:
        report["research"] = {
            "summary": norm_space(research.get("summary"))[:400],
            "search_terms": list(research.get("search_terms") or []),
            "exclusions": list(research.get("exclusions") or []),
            "modalities": list(research.get("modalities") or []),
            "min_samples": research.get("min_samples"),
            "sources": [{"title": norm_space(s.get("title"))[:120],
                         "url": str(s.get("url") or "")}
                        for s in (research.get("sources") or []) if s.get("url")][:5],
            "checked_at": research.get("fetched_at") or research.get("checked_at") or utcnow(),
            "cached": bool(research.get("cached")),
        }
    return rubric_check(report)


# ---------------------------------------------------------------- turn

def _research_detail(brief: dict | None) -> str:
    if not brief:
        return "no result — continuing with profile queries"
    return (f"{len(brief.get('search_terms') or [])} term(s) · "
            f"{len(brief.get('sources') or [])} source(s)"
            + (" · cached" if brief.get("cached") else ""))


def _final_reply(report: dict) -> str:
    """确定性 Markdown 收尾：结论 + 三类亮点 + 覆盖说明 + 下一步。"""
    p = report.get("profile") or {}
    items = report.get("items") or []
    catalog = [i for i in items if i.get("type") == "catalog"]
    cands = [i for i in items if i.get("type") == "candidate"]
    ft = report.get("fulltext_stats") or {}
    topic = p.get("topic") or p.get("disease") or "your request"
    ctx = " · ".join(str(x) for x in (
        p.get("organism"), p.get("tissue"),
        ", ".join(str(m).replace("_", " ") for m in (p.get("modalities") or []))) if x)
    lines = [f'Here is what I found for **"{topic}"**' + (f" — {ctx}." if ctx else "."), ""]

    res = report.get("research") or {}
    if res.get("search_terms") or res.get("summary"):
        if res.get("summary"):
            lines.append("Web research: " + res["summary"])
        if res.get("search_terms"):
            lines.append("Added search terms: "
                         + ", ".join(f"`{t}`" for t in res["search_terms"][:6]))
        lines.append("")

    if report.get("relax_suggestion"):
        lines.append(report["relax_suggestion"])
        lines.append("")
    if catalog:
        lines.append(f"### Catalog records — verified, with evidence ({len(catalog)})")
        for it in catalog[:8]:
            accs = ", ".join(f"`{d.get('accession')}`"
                             for d in (it.get("datasets") or []) if d.get("accession"))
            year = f" ({it['year']})" if it.get("year") else ""
            lines.append(f"- **{it.get('title', '')}**{year}" + (f" — {accs}" if accs else ""))
        lines.append("")
    ds_rows = [(d, it) for it in cands for d in (it.get("datasets") or [])]
    if ds_rows:
        lines.append("### Datasets announced in papers — unverified, from Data Availability "
                     f"({len(ds_rows)})")
        for d, it in ds_rows[:8]:
            kind = {"europepmc_fulltext": "EPMC full text", "ncbi_efetch": "PMC (NCBI)",
                    "pmc_oa": "PMC OA", "preprint": "preprint XML",
                    "core": "CORE full text", "core_pdf": "CORE PDF",
                    "openalex_pdf": "OpenAlex PDF"}.get(it.get("fulltext_kind") or "",
                                                         "full text")
            ev = "DA section" if d.get("da_evidence") else kind
            repo = d.get("repository") or "repository"
            title = (it.get("title") or "")[:64]
            lines.append(f"- `{d.get('accession')}` ({repo}) — from *\"{title}\"* ({ev})")
        lines.append("")
    if cands:
        lines.append(f"### Papers to inspect — no accession extracted ({len(cands)})")
        for it in cands[:6]:
            srcs = ", ".join(SOURCE_LABEL.get(s, s) for s in (it.get("sources") or []))
            lines.append(f"- {it.get('title', '')} — {srcs}")
        lines.append("")

    lines.append("---")
    attempted = int(ft.get("attempted") or 0)
    ok = int(ft.get("fulltext_ok") or 0)
    if attempted:
        extra = f" ({attempted - ok} abstract-only)" if attempted > ok else ""
        lines.append(f"I read **{attempted} paper(s)**; {ok} had full text{extra}.")
    lines.append("Catalog records are verified with evidence; external findings are "
                 "unverified.")
    lines.append("**Next:** open a record in the ledger, export JSONL/CSV, or ask me to look "
                 "deeper at a specific accession. To narrow the search, add an organism, "
                 "a technique or a minimum sample size.")
    return "\n".join(lines).strip()


def run_turn(sid: str, message: str):
    sess = get_session(sid)
    if sess is None:
        yield {"event": "error", "message": "session not found"}
        return
    add_message(sid, "user", message)
    try:
        yield {"event": "step", "name": "clarify", "status": "start"}
        t_clar = time.time()
        state = _state(sid)
        history = [m.get("content") or "" for m in get_messages(sid, limit=24)][-12:]
        profile, ready, missing, reply, domain = yield from _run_phase_with_events(
            lambda emit: extract_profile(sid, history, _state(sid)), "clarify",
            heartbeat_s=5.0)
        state = _state(sid)
        yield {"event": "step", "name": "clarify", "status": "done",
               "ms": int((time.time() - t_clar) * 1000)}

        if domain == "other":
            text = ("That is outside what I do. BioAgentDB looks for public datasets "
                    "announced in biomedical papers — ask about any disease, organ, "
                    "organism or omics technique.")
            yield {"event": "refusal", "text": text}
            add_message(sid, "assistant", text, {"kind": "refusal"})
            yield {"event": "done"}
            return

        yield {"event": "profile", "profile": profile, "ready": ready,
               "missing": missing, "defaults": profile.get("defaults") or []}

        if not ready:
            questions = _pick_questions(state, missing)
            text = reply or "To search well, I need a bit more:"
            if questions:
                text = (reply + "\n" if reply else "") + "\n".join(f"- {q}" for q in questions)
            options = DISEASE_CHIPS if "topic" in missing else MODALITY_CHIPS
            _set_state(sid, state)
            yield {"event": "clarify", "text": text, "options": options}
            add_message(sid, "assistant", text,
                        {"kind": "clarify", "profile": profile, "options": options})
            yield {"event": "done"}
            return

        # v1.6（L-0292）：查证信号须在"同画像复用"门之前判定——明示查证不得被缓存短路
        fp = _fingerprint(profile)
        cache = state.get("search_cache") or {}
        sig = state.get("research_signal") or {}
        explicit_lookup = web_research.looks_like_lookup_request(message)
        need_research = bool(sig.get("needed")) or explicit_lookup
        if not need_research:
            need_research = (web_research.looks_like_unknown_topic(message)
                             or web_research.looks_like_unknown_topic(
                                 str(profile.get("topic") or "")))
        research_query = (str(sig.get("query") or "").strip()
                          or str(profile.get("topic") or profile.get("disease") or "").strip())
        cached_research = (cache.get("report", {}).get("report") or {}).get("research")

        # 同轮/同画像复用（空结果 / "search again" / 明示查证 / 该查未查 → 重跑）
        if (cache.get("fp") == fp and cache.get("report") and not cache.get("empty")
                and not _REFRESH_RE.search(message)
                and not explicit_lookup
                and not (need_research and not cached_research)):
            if cached_research:
                yield {"event": "research", "research": cached_research}
            yield {"event": "results", "catalog": cache["report"].get("catalog") or [],
                   "candidates": cache["report"].get("candidates") or [],
                   "note": "reusing the previous search for the same profile"}
            yield {"event": "report", "report": cache["report"]["report"]}
            final_text = _final_reply(cache["report"]["report"])
            for i in range(0, len(final_text), 48):
                yield {"event": "token", "text": final_text[i:i + 48]}
            add_message(sid, "assistant", final_text,
                        {"kind": "report", "report": cache["report"]["report"]})
            yield {"event": "done"}
            return

        yield {"event": "step", "name": "catalog", "status": "start"}
        t_cat = time.time()
        catalog = search_catalog(profile)
        yield {"event": "step", "name": "catalog", "status": "done",
               "detail": f"{len(catalog)} catalog record(s)",
               "ms": int((time.time() - t_cat) * 1000)}

        # v1.6（L-0292）：未知知识 → 联网查证 → 检索改写（need_research/research_query 已在缓存门之前算好）
        research_brief: dict | None = None
        research_attempted = False
        if need_research and web_research.enabled() and research_query:
            research_attempted = True
            yield {"event": "step", "name": "research", "status": "start"}
            t_res = time.time()
            research_brief = yield from _run_phase_with_events(
                lambda emit: web_research.brief(research_query, context=message,
                                                on_progress=emit),
                "research", heartbeat_s=5.0)
            yield {"event": "step", "name": "research", "status": "done",
                   "detail": _research_detail(research_brief),
                   "ms": int((time.time() - t_res) * 1000)}
            if research_brief:
                yield {"event": "research", "research": research_brief}

        def _expand(emit) -> list[str]:
            st = _state(sid)
            qs = expand_queries(profile, st, research=research_brief)
            _set_state(sid, st)
            return qs

        t_q = time.time()
        queries = yield from _run_phase_with_events(_expand, "queries", heartbeat_s=5.0)
        yield {"event": "progress", "phase": "queries",
               "text": f"planned {len(queries)} query variant(s) · "
                       f"{int((time.time() - t_q) * 1000)}ms",
               "t_ms": int(time.time() * 1000)}

        yield {"event": "step", "name": "sources", "status": "start"}
        t_src = time.time()
        candidates, per_source, members_map = yield from _run_phase_with_events(
            lambda emit: search_sources(profile, queries, on_progress=emit), "sources")
        yield {"event": "step", "name": "sources", "status": "done",
               "counts": {k: v.get("hits") for k, v in per_source.items()},
               "detail": per_source,
               "ms": int((time.time() - t_src) * 1000)}

        # 首轮全空且未查证过 → 查证 → 重扩词 → 重跑五源一次（有界；L-0292）
        if (not candidates and not catalog and not research_attempted
                and web_research.enabled()):
            rq = research_query or str(profile.get("topic") or profile.get("disease") or "").strip()
            if rq:
                research_attempted = True
                yield {"event": "step", "name": "research", "status": "start"}
                t_res2 = time.time()
                research_brief = yield from _run_phase_with_events(
                    lambda emit: web_research.brief(rq, context=message, on_progress=emit),
                    "research", heartbeat_s=5.0)
                yield {"event": "step", "name": "research", "status": "done",
                       "detail": _research_detail(research_brief),
                       "ms": int((time.time() - t_res2) * 1000)}
                if research_brief:
                    yield {"event": "research", "research": research_brief}

                    def _expand2(emit) -> list[str]:
                        st = _state(sid)
                        qs = expand_queries(profile, st, research=research_brief)
                        _set_state(sid, st)
                        return qs

                    queries = yield from _run_phase_with_events(
                        _expand2, "queries", heartbeat_s=5.0)
                    yield {"event": "progress", "phase": "queries",
                           "text": f"re-planned {len(queries)} query variant(s) after web "
                                   "research",
                           "t_ms": int(time.time() * 1000)}
                    yield {"event": "step", "name": "sources", "status": "start"}
                    t_src2 = time.time()
                    candidates, per_source, members_map = yield from _run_phase_with_events(
                        lambda emit: search_sources(profile, queries, on_progress=emit),
                        "sources")
                    yield {"event": "step", "name": "sources", "status": "done",
                           "counts": {k: v.get("hits") for k, v in per_source.items()},
                           "detail": per_source,
                           "ms": int((time.time() - t_src2) * 1000)}

        yield {"event": "step", "name": "dedup", "status": "done",
               "detail": f"{len(candidates)} unique candidate(s)"}
        candidates = presort(candidates, profile, research=research_brief)

        state = _state(sid)
        pdf_used = int(state.get("pdf_used") or 0)
        yield {"event": "step", "name": "papers", "status": "start"}
        t_pap = time.time()
        fulltext_stats = yield from _run_phase_with_events(
            lambda emit: paper_datasets.attach_datasets(
                candidates, members_map, k=K_FULLTEXT, pdf_cap=PDF_SESSION_CAP,
                pdf_used=pdf_used, on_progress=emit), "papers")
        state = _state(sid)
        state["pdf_used"] = fulltext_stats.get("pdf_used", pdf_used)
        _set_state(sid, state)
        yield {"event": "step", "name": "papers", "status": "done",
               "detail": f"{fulltext_stats.get('datasets', 0)} dataset(s) from "
                         f"{fulltext_stats.get('fulltext_ok', 0)} paper(s) — "
                         + " · ".join(f"{k} {v}" for k, v in
                                      (fulltext_stats.get('kinds') or {}).items()),
               "ms": int((time.time() - t_pap) * 1000)}

        yield {"event": "step", "name": "verify", "status": "start"}
        t_ver = time.time()
        candidates = yield from _run_phase_with_events(
            lambda emit: verify_candidates(candidates, on_progress=emit), "verify")
        yield {"event": "step", "name": "verify", "status": "done",
               "detail": f"checked {min(VERIFY_MAX, len(candidates))} link(s)",
               "ms": int((time.time() - t_ver) * 1000)}

        yield {"event": "results", "catalog": catalog[:6],
               "candidates": [{
                   "title": c.get("title") or "", "year": c.get("year"),
                   "sources": json.loads(c.get("sources") or "[]"),
                   "landing_url": c.get("landing_url") or "",
                   "t2": bool(c.get("t2")),
                   "fulltext_kind": c.get("fulltext_kind") or "none",
                   "datasets": c.get("datasets") or [],
               } for c in candidates[:TOP_N]]}

        yield {"event": "step", "name": "report", "status": "start"}
        t_rep = time.time()
        report = build_report(profile, catalog, candidates, per_source, fulltext_stats,
                              research=research_brief)
        yield {"event": "report", "report": report}
        final_text = _final_reply(report)
        for i in range(0, len(final_text), 48):
            yield {"event": "token", "text": final_text[i:i + 48]}
        state = _state(sid)
        state["search_cache"] = {
            "fp": fp,
            "empty": not (catalog or candidates),
            "report": {"catalog": catalog[:6],
                       "candidates": [{
                           "title": c.get("title") or "", "year": c.get("year"),
                           "sources": json.loads(c.get("sources") or "[]"),
                           "landing_url": c.get("landing_url") or "",
                           "t2": bool(c.get("t2")),
                           "fulltext_kind": c.get("fulltext_kind") or "none",
                           "datasets": c.get("datasets") or [],
                       } for c in candidates[:TOP_N]],
                       "report": report},
        }
        _set_state(sid, state)
        add_message(sid, "assistant", final_text, {"kind": "report", "report": report})
        yield {"event": "step", "name": "report", "status": "done",
               "ms": int((time.time() - t_rep) * 1000)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("workbench turn failed: %s", exc)
        yield {"event": "error", "message": f"{type(exc).__name__}: {exc}"}
    yield {"event": "done"}

def run_pipeline(sid: str, message: str, profile: dict):
    """Deterministic research pipeline after the profile is ready.

    Yields the same step/progress/results/report events as run_turn and
    returns the final report dict. No clarify, no assistant message.
    """
    state = _state(sid)
    # v1.6（L-0292）：查证信号须在"同画像复用"门之前判定——明示查证不得被缓存短路
    fp = _fingerprint(profile)
    cache = state.get("search_cache") or {}
    sig = state.get("research_signal") or {}
    explicit_lookup = web_research.looks_like_lookup_request(message)
    need_research = bool(sig.get("needed")) or explicit_lookup
    if not need_research:
        need_research = (web_research.looks_like_unknown_topic(message)
                         or web_research.looks_like_unknown_topic(
                             str(profile.get("topic") or "")))
    research_query = (str(sig.get("query") or "").strip()
                      or str(profile.get("topic") or profile.get("disease") or "").strip())
    cached_research = (cache.get("report", {}).get("report") or {}).get("research")

    # 同轮/同画像复用（空结果 / "search again" / 明示查证 / 该查未查 → 重跑）
    if (cache.get("fp") == fp and cache.get("report") and not cache.get("empty")
            and not _REFRESH_RE.search(message)
            and not explicit_lookup
            and not (need_research and not cached_research)):
        if cached_research:
            yield {"event": "research", "research": cached_research}
        yield {"event": "results", "catalog": cache["report"].get("catalog") or [],
               "candidates": cache["report"].get("candidates") or [],
               "note": "reusing the previous search for the same profile"}
        report = cache["report"]["report"]
        yield {"event": "report", "report": report}
        return report

    yield {"event": "step", "name": "catalog", "status": "start"}
    t_cat = time.time()
    catalog = search_catalog(profile)
    yield {"event": "step", "name": "catalog", "status": "done",
           "detail": f"{len(catalog)} catalog record(s)",
           "ms": int((time.time() - t_cat) * 1000)}

    # v1.6（L-0292）：未知知识 → 联网查证 → 检索改写（need_research/research_query 已在缓存门之前算好）
    research_brief: dict | None = None
    research_attempted = False
    if need_research and web_research.enabled() and research_query:
        research_attempted = True
        yield {"event": "step", "name": "research", "status": "start"}
        t_res = time.time()
        research_brief = yield from _run_phase_with_events(
            lambda emit: web_research.brief(research_query, context=message,
                                            on_progress=emit),
            "research", heartbeat_s=5.0)
        yield {"event": "step", "name": "research", "status": "done",
               "detail": _research_detail(research_brief),
               "ms": int((time.time() - t_res) * 1000)}
        if research_brief:
            yield {"event": "research", "research": research_brief}

    def _expand(emit) -> list[str]:
        st = _state(sid)
        qs = expand_queries(profile, st, research=research_brief)
        _set_state(sid, st)
        return qs

    t_q = time.time()
    queries = yield from _run_phase_with_events(_expand, "queries", heartbeat_s=5.0)
    yield {"event": "progress", "phase": "queries",
           "text": f"planned {len(queries)} query variant(s) · "
                   f"{int((time.time() - t_q) * 1000)}ms",
           "t_ms": int(time.time() * 1000)}

    yield {"event": "step", "name": "sources", "status": "start"}
    t_src = time.time()
    candidates, per_source, members_map = yield from _run_phase_with_events(
        lambda emit: search_sources(profile, queries, on_progress=emit), "sources")
    yield {"event": "step", "name": "sources", "status": "done",
           "counts": {k: v.get("hits") for k, v in per_source.items()},
           "detail": per_source,
           "ms": int((time.time() - t_src) * 1000)}

    # 首轮全空且未查证过 → 查证 → 重扩词 → 重跑五源一次（有界；L-0292）
    if (not candidates and not catalog and not research_attempted
            and web_research.enabled()):
        rq = research_query or str(profile.get("topic") or profile.get("disease") or "").strip()
        if rq:
            research_attempted = True
            yield {"event": "step", "name": "research", "status": "start"}
            t_res2 = time.time()
            research_brief = yield from _run_phase_with_events(
                lambda emit: web_research.brief(rq, context=message, on_progress=emit),
                "research", heartbeat_s=5.0)
            yield {"event": "step", "name": "research", "status": "done",
                   "detail": _research_detail(research_brief),
                   "ms": int((time.time() - t_res2) * 1000)}
            if research_brief:
                yield {"event": "research", "research": research_brief}

                def _expand2(emit) -> list[str]:
                    st = _state(sid)
                    qs = expand_queries(profile, st, research=research_brief)
                    _set_state(sid, st)
                    return qs

                queries = yield from _run_phase_with_events(
                    _expand2, "queries", heartbeat_s=5.0)
                yield {"event": "progress", "phase": "queries",
                       "text": f"re-planned {len(queries)} query variant(s) after web "
                               "research",
                       "t_ms": int(time.time() * 1000)}
                yield {"event": "step", "name": "sources", "status": "start"}
                t_src2 = time.time()
                candidates, per_source, members_map = yield from _run_phase_with_events(
                    lambda emit: search_sources(profile, queries, on_progress=emit),
                    "sources")
                yield {"event": "step", "name": "sources", "status": "done",
                       "counts": {k: v.get("hits") for k, v in per_source.items()},
                       "detail": per_source,
                       "ms": int((time.time() - t_src2) * 1000)}

    yield {"event": "step", "name": "dedup", "status": "done",
           "detail": f"{len(candidates)} unique candidate(s)"}
    candidates = presort(candidates, profile, research=research_brief)

    state = _state(sid)
    pdf_used = int(state.get("pdf_used") or 0)
    yield {"event": "step", "name": "papers", "status": "start"}
    t_pap = time.time()
    fulltext_stats = yield from _run_phase_with_events(
        lambda emit: paper_datasets.attach_datasets(
            candidates, members_map, k=K_FULLTEXT, pdf_cap=PDF_SESSION_CAP,
            pdf_used=pdf_used, on_progress=emit), "papers")
    state = _state(sid)
    state["pdf_used"] = fulltext_stats.get("pdf_used", pdf_used)
    _set_state(sid, state)
    yield {"event": "step", "name": "papers", "status": "done",
           "detail": f"{fulltext_stats.get('datasets', 0)} dataset(s) from "
                     f"{fulltext_stats.get('fulltext_ok', 0)} paper(s) — "
                     + " · ".join(f"{k} {v}" for k, v in
                                  (fulltext_stats.get('kinds') or {}).items()),
           "ms": int((time.time() - t_pap) * 1000)}

    yield {"event": "step", "name": "verify", "status": "start"}
    t_ver = time.time()
    candidates = yield from _run_phase_with_events(
        lambda emit: verify_candidates(candidates, on_progress=emit), "verify")
    yield {"event": "step", "name": "verify", "status": "done",
           "detail": f"checked {min(VERIFY_MAX, len(candidates))} link(s)",
           "ms": int((time.time() - t_ver) * 1000)}

    yield {"event": "results", "catalog": catalog[:6],
           "candidates": [{
               "title": c.get("title") or "", "year": c.get("year"),
               "sources": json.loads(c.get("sources") or "[]"),
               "landing_url": c.get("landing_url") or "",
               "t2": bool(c.get("t2")),
               "fulltext_kind": c.get("fulltext_kind") or "none",
               "datasets": c.get("datasets") or [],
           } for c in candidates[:TOP_N]]}

    yield {"event": "step", "name": "report", "status": "start"}
    t_rep = time.time()
    report = build_report(profile, catalog, candidates, per_source, fulltext_stats,
                          research=research_brief)
    yield {"event": "report", "report": report}
    state = _state(sid)
    state["search_cache"] = {
        "fp": fp,
        "empty": not (catalog or candidates),
        "report": {"catalog": catalog[:6],
                   "candidates": [{
                       "title": c.get("title") or "", "year": c.get("year"),
                       "sources": json.loads(c.get("sources") or "[]"),
                       "landing_url": c.get("landing_url") or "",
                       "t2": bool(c.get("t2")),
                       "fulltext_kind": c.get("fulltext_kind") or "none",
                       "datasets": c.get("datasets") or [],
                   } for c in candidates[:TOP_N]],
                   "report": report},
    }
    _set_state(sid, state)
    yield {"event": "step", "name": "report", "status": "done",
           "ms": int((time.time() - t_rep) * 1000)}
    return report
