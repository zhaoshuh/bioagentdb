"""意图分析 / Ming 追问（L-0223 首版；L-0233 追问对话）。

`analyze_request`：一句话目标 + 可选补充（organism/tissue）+ 追问历史 `history`
→ `{clarify: true, questions: [...]}` 或提议结构（**只读**，不建任何东西）。

判定为确定性规则（不看 LLM）：文本/补答中缺 organism 或缺 technique → 问对应问题；
每轮 ≤2 问、全局 ≤2 轮（answered 计数）；两者齐备或额度用尽 → 收敛为提议。
"""

from __future__ import annotations

import re

_TECH_RE = re.compile(
    r"metagenom|16s|shotgun|single[- ]cell|scrna|snrna|scatac|spatial|transcriptom|proteom|"
    r"metabolom|lipidom|epigenom|methylat|sequenc|rna[- ]?seq|microarray|atac|multi[- ]?omics",
    re.I)
_ORG_RE = re.compile(
    r"human|homo sapiens|mouse|mice|mus musculus|rat|patient|clinical|drosophila|zebrafish|"
    r"soil|marine|ocean|environment|plant", re.I)

_QUESTIONS = {
    "organism": {"text": "Which organism or system?",
                 "options": ["any", "Homo sapiens", "Mus musculus", "environmental"]},
    "technique": {"text": "Any technique emphasis?",
                  "options": ["any", "metagenomics", "16S amplicon", "single-cell",
                              "proteomics", "spatial"]},
}
_MAX_ASKS = 2   # 追问上限（两条问题被答/跳即停）


def _answers(history: list | None) -> dict:
    out: dict[str, str] = {}
    for h in (history or []):
        if isinstance(h, dict) and h.get("q"):
            out[str(h["q"])] = str(h.get("a") or "")
    return out


def _split_segments(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[;；,，\n]+", text) if s.strip()]


def analyze_request(text: str, *, organism: str = "", tissue: str = "",
                    history: list | None = None) -> dict:
    text = (text or "").strip()[:300]      # 长度钳制（L-0225）
    if not text:
        return {"error": "please describe what to mine"}
    answered = _answers(history)
    first = (_split_segments(text) or [text])[0]

    # —— 追问判定（L-0233）
    asked = len(answered)
    questions: list[dict] = []
    if asked < _MAX_ASKS:
        org_ok = (((answered.get("organism") or "").strip().lower() not in ("", "any"))
                  or bool((organism or "").strip()) or bool(_ORG_RE.search(text)))
        tech_ok = (((answered.get("technique") or "").strip().lower() not in ("", "any"))
                   or bool(_TECH_RE.search(text)))
        if not org_ok:
            questions.append({"id": "organism", **_QUESTIONS["organism"]})
        if not tech_ok:
            questions.append({"id": "technique", **_QUESTIONS["technique"]})
        questions = questions[: max(0, _MAX_ASKS - asked)]
    if questions:
        return {"clarify": True, "round": asked, "questions": questions,
                "text": "Two quick questions so mining is on target (pick one or "
                        "\u201cany\u201d):"}

    # —— 收敛为提议（原 L-0223 逻辑 + 答案折叠）
    from app.chat import workbench as wb
    from app.core.config import cfg as _cfg
    from app.harvest import themes as _themes

    segments = _split_segments(text)
    keywords = segments[1:5]
    modalities: list[str] = []
    org_ans = (answered.get("organism") or "").strip()
    tech_ans = (answered.get("technique") or "").strip()
    profile_org = (organism or "").strip() or (org_ans if org_ans.lower() != "any" else "")
    if tech_ans and tech_ans.lower() != "any":
        keywords = keywords + [tech_ans]
        modalities = [tech_ans]
    profile = {"topic": first, "organism": profile_org, "tissue": (tissue or "").strip(),
               "keywords": keywords, "modalities": modalities}
    # v1.6（L-0292）：未知知识 → 联网查证 → queries 改写
    research: dict | None = None
    try:
        from app.chat import web_research
        if web_research.enabled() and web_research.looks_like_unknown_topic(first):
            research = web_research.brief(first)
    except Exception:  # noqa: BLE001
        research = None
    try:
        queries = wb.expand_queries(profile, {}, research=research)[:8]
    except Exception:  # noqa: BLE001
        queries = [f'"{first}" AND HAS_DATA:Y']
    # 守卫（L-0295）：即使 expand_queries 退化/旧 seam，查证词也必须并入（不静默丢失）
    if research and (research.get("search_terms") or []):
        have = {str(q).casefold() for q in queries}
        head = [str(t) for t in research["search_terms"] if str(t).casefold() not in have]
        if head:
            queries = (head + queries)[:8]
    slug = _themes.slugify(first)
    existing = _themes.get_theme(slug)
    if existing:
        kind = existing.get("kind") or "disease"
        label = existing.get("name") or first
    else:
        kind, label = "topic", first
    out = {"theme": slug, "theme_label": label, "kind": kind,
           "existing": bool(existing), "queries": queries,
           "papers_per_round": _cfg.int("mining_task_papers_per_round", 200),
           "cadence_hours": _cfg.int("mining_task_cadence_hours", 24)}
    if research:
        out["research"] = {"summary": research.get("summary") or "",
                           "search_terms": research.get("search_terms") or [],
                           "sources": research.get("sources") or []}
    return out
