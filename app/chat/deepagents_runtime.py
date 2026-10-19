"""DeepAgents runtime for the Ming and Research agents (L-0396).

Feature-flagged by BIOAGENTDB_AGENT_IMPL=deepagents; the legacy implementation
remains the default until the DeepAgents path passes the regression suite.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from typing import Any

from app.core.config import cfg
from app.core.db import db, utcnow

try:  # optional: only needed when the DeepAgents path is enabled
    from langchain_core.runnables import RunnableConfig
except Exception:  # noqa: BLE001
    from typing import Any as RunnableConfig  # type: ignore

logger = logging.getLogger(__name__)

_CURRENT_SID: contextvars.ContextVar[str] = contextvars.ContextVar("deepagents_sid", default="")
_MING_AGENT = None
_RESEARCH_AGENT = None
_SAVER = None
_PENDING: dict[str, dict] = {}


def _sid_from_config(config) -> str:
    try:
        return str((config or {}).get("configurable", {}).get("thread_id") or "")
    except Exception:  # noqa: BLE001
        return ""


def _sid_current(config=None) -> str:
    sid = _sid_from_config(config)
    if sid:
        return sid
    try:
        from langgraph.config import get_config

        cfg = get_config()
        sid = str((cfg or {}).get("configurable", {}).get("thread_id") or "")
    except Exception:  # noqa: BLE001
        sid = ""
    return sid or _CURRENT_SID.get()


_AVAILABLE: bool | None = None


def enabled() -> bool:
    """deepagents is the default when installed; legacy is used when the package
    is missing (old images) or BIOAGENTDB_AGENT_IMPL=legacy is set explicitly."""
    global _AVAILABLE
    flag = os.environ.get("BIOAGENTDB_AGENT_IMPL", "deepagents").strip().lower()
    if flag == "legacy":
        return False
    if not (os.environ.get("DEEPSEEK_API_KEY") or cfg.get("deepseek_api_key")):
        return False
    if _AVAILABLE is None:
        try:
            import deepagents  # noqa: F401

            _AVAILABLE = True
        except Exception:  # noqa: BLE001
            logger.warning("deepagents not installed; falling back to legacy agent")
            _AVAILABLE = False
    return bool(_AVAILABLE)


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _rubric_enabled() -> bool:
    return os.environ.get("BIOAGENTDB_RUBRIC", "1").strip().lower() not in (
        "0", "false", "no", "off")


def _rubric_status(agent, cfg_run: dict) -> str:
    try:
        snap = agent.get_state(cfg_run)
        return str((getattr(snap, "values", {}) or {}).get("_rubric_status") or "")
    except Exception:  # noqa: BLE001
        return ""


def _register_harness_profile(model_name: str) -> None:
    try:
        from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile

        register_harness_profile(
            f"openai:{model_name}",
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
        )
    except Exception:  # noqa: BLE001
        logger.debug("harness profile registration unavailable", exc_info=True)


def _model(name: str | None = None):
    from langchain_openai import ChatOpenAI

    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("deepseek_api_key") or ""
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    model_name = name or os.environ.get("DEEPSEEK_MODEL") or cfg.get("deepseek_model") \
        or "deepseek-flash"
    _register_harness_profile(model_name)
    return ChatOpenAI(
        model=model_name,
        base_url=os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("deepseek_base_url")
        or "https://api.deepseek.com",
        api_key=key,
        temperature=0,
        timeout=180,
    )


def _rubric_model():
    """The grader needs a model that supports forced tool_choice.

    The default DeepSeek Flash alias is a thinking model and rejects a forced
    tool choice with HTTP 400; ``deepseek-chat`` is routed to the same flash
    model but accepts the structured-output tool used by RubricMiddleware.
    """
    return _model(os.environ.get("DEEPSEEK_RUBRIC_MODEL") or "deepseek-chat")


def _saver():
    global _SAVER
    if _SAVER is None:
        try:
            import sqlite3

            from langgraph.checkpoint.sqlite import SqliteSaver

            path = cfg.data_dir / "agent_checkpoints.sqlite3"
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            _SAVER = SqliteSaver(conn)
        except Exception:  # noqa: BLE001
            logger.warning("sqlite checkpointer unavailable; using in-memory", exc_info=True)
            from langgraph.checkpoint.memory import InMemorySaver

            _SAVER = InMemorySaver()
    return _SAVER


# ---------------------------------------------------------------- session state

def _ensure_session(sid: str, kind: str = "research") -> None:
    now = utcnow()
    with db() as conn:
        row = conn.execute("SELECT id FROM chat_sessions WHERE id=?", (sid,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO chat_sessions(id,title,state,kind,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)", (sid, "", "{}", kind, now, now))


def _state(sid: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT state FROM chat_sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["state"] or "{}")
    except Exception:  # noqa: BLE001
        return {}


def _update_state(sid: str, **patch: Any) -> dict:
    state = _state(sid)
    state.update(patch)
    with db() as conn:
        conn.execute("UPDATE chat_sessions SET state=?, updated_at=? WHERE id=?",
                     (json.dumps(state, ensure_ascii=False), utcnow(), sid))
    return state


def _add_message(sid: str, role: str, content: str, meta: dict | None = None) -> None:
    _ensure_session(sid)
    with db() as conn:
        conn.execute(
            "INSERT INTO chat_messages(id,session_id,role,content,meta,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (f"msg_{uuid.uuid4().hex[:12]}", sid, role, content or "",
             json.dumps(meta or {}, ensure_ascii=False), utcnow()))
        conn.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (utcnow(), sid))


def _recent_messages(sid: str, limit: int = 8) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT role,content FROM chat_messages WHERE session_id=? "
            "ORDER BY rowid DESC LIMIT ?", (sid, limit)).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)
            if r["role"] in ("user", "assistant") and r["content"]]


# ---------------------------------------------------------------- shared intent tools

def save_profile(topic: str = "", organism: str = "", tissue: str = "",
                 modalities: list[str] | None = None, keywords: list[str] | None = None,
                 concepts_json: str = "", ready: bool = False,
                 config: RunnableConfig | None = None) -> dict:
    """Save the shared mining/research profile for the current session. Call this
    once the topic is clear (even if the user skipped details), with ready=true.
    Empty fields keep their previous values."""
    sid = _sid_current(config)
    if not sid:
        return {"ok": False, "message": "no session"}
    state = _state(sid)
    profile = dict(state.get("profile") or {})
    topic = (topic or "").strip()
    topic_changed = False
    if topic:
        old_topic = str(profile.get("topic") or profile.get("disease") or "")
        if old_topic and topic.casefold() != old_topic.casefold():
            # A new spelling/full name invalidates web aliases resolved for the old label.
            profile.pop("concepts", None)
            topic_changed = True
        profile["topic"] = topic[:140]
        profile["disease"] = topic[:140]
    if organism.strip():
        profile["organism"] = organism.strip()[:80]
    if tissue.strip():
        profile["tissue"] = tissue.strip()[:80]
    if modalities:
        profile["modalities"] = [str(m).strip() for m in modalities if str(m).strip()][:8]
    if keywords:
        old = [str(k) for k in (profile.get("keywords") or [])]
        for k in keywords:
            k = str(k).strip()
            if k and k not in old:
                old.append(k)
        profile["keywords"] = old[:12]
    if concepts_json.strip():
        try:
            parsed = json.loads(concepts_json)
            if isinstance(parsed, list):
                profile["concepts"] = [c for c in parsed if isinstance(c, dict)][:12]
        except Exception:  # noqa: BLE001
            logger.warning("invalid concepts_json ignored")
    extra = {}
    if topic_changed:
        extra = {"last_retrieval": None, "last_judgments": None,
                 "last_report": None, "last_web_brief": None,
                 "conversation_stage": "profile"}
    _update_state(sid, profile=profile, profile_ready=bool(ready),
                  ming_ready=bool(ready), **extra)
    return {"ok": True, "ready": bool(ready), "profile": profile}


def _message_count(sid: str) -> int:
    with db() as conn:
        row = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id=?",
                           (sid,)).fetchone()
    return int(row[0] if row else 0)


def _question_key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def ask_user(question: str, options: list[str] | None = None, blocking: bool = True,
             reason_code: str = "scope_choice",
             config: RunnableConfig | None = None) -> dict:
    """Ask exactly one blocking question and record that this turn must stop.

    The orchestrator decides when to call this.  The pending flag is mirrored
    into chat_sessions.state and injected into the next turn's prompt; it is
    then cleared when the next user message arrives.
    """
    sid = _sid_current(config)
    if not sid:
        return {"ok": False, "error": "no session"}
    q = re.sub(r"\s+", " ", str(question or "")).strip()
    if not q:
        return {"ok": False, "error": "question required"}
    if not blocking:
        return {"ok": False, "error": "non-blocking questions must proceed with assumptions"}
    state = _state(sid)
    asked = [str(x) for x in (state.get("asked_questions") or [])]
    key = _question_key(q)
    if key in {_question_key(x) for x in asked}:
        return {"ok": False, "error": "question already asked; proceed with assumptions"}
    opts = [str(x).strip() for x in (options or []) if str(x).strip()][:6]
    pending = {
        "question": q,
        "options": opts,
        "reason_code": str(reason_code or "scope_choice"),
        "asked_key": key,
        "created_at": utcnow(),
    }
    _update_state(sid, pending_clarification=pending,
                  asked_questions=(asked + [q])[-12:],
                  conversation_stage="clarifying")
    return {"ok": True, "stopped": True, "question": q, "options": opts}


def _prune_unmentioned_abbreviations(profile: dict, message: str) -> dict:
    """Do not let the model invent an abbreviation that the user never typed.

    A saved profile can contain aliases the model added for convenience.  If a
    short alphabetic label (e.g. ``AD``) does not occur in the user's message, it
    must not trigger an ambiguity question.  Full names and longer labels stay.
    """
    concepts = list(profile.get("concepts") or [])
    if not concepts:
        return profile
    kept = []
    changed = False
    for c in concepts:
        label = str(c.get("label") or "").strip()
        if (label and len(label) <= 6 and label.isalpha()
                and not re.search(r"\b" + re.escape(label) + r"\b", message or "", re.I)):
            changed = True
            continue
        kept.append(c)
    if changed:
        profile = {**profile, "concepts": kept}
    return profile


def run_research(config: RunnableConfig | None = None,
                 web_brief_json: str = "") -> dict:
    """Run the deterministic research pipeline for the saved profile. It streams
    step/progress/results/report events and returns the counts + report summary.
    Call only after save_profile(ready=true). Optional web_brief_json is the
    JSON returned by web_research_worker; when present, web search is not repeated.
    """
    from langgraph.config import get_stream_writer

    from app.chat import workbench

    sid = _sid_current(config)
    if not sid:
        return {"ok": False, "error": "no session"}
    state = _state(sid)
    if state.get("pending_clarification"):
        return {"ok": False, "needs_clarification": True,
                "message": "A clarification question is pending; wait for the user's answer."}
    if state.get("last_report") and _is_explanation_turn(
            str(state.get("research_message") or "")):
        return {"ok": False, "use_last_report": True,
                "message": "Answer this follow-up from the previous report; do not rerun the pipeline."}
    pending = list(state.get("pending_ambiguity") or [])
    if pending:
        if _message_count(sid) <= int(state.get("pending_ambiguity_count") or 0):
            return {"ok": False, "needs_confirmation": pending,
                    "message": "Ask the user which meaning they intend before searching: "
                               + " | ".join(u.get("question", "") for u in pending)}
        _update_state(sid, pending_ambiguity=[], pending_ambiguity_count=0)
    profile = dict(state.get("profile") or {})
    topic = str(profile.get("topic") or profile.get("disease") or "").strip()
    if not topic:
        return {"ok": False, "error": "profile missing; call save_profile(ready=true) first"}
    message = str(state.get("research_message") or topic)
    pruned = _prune_unmentioned_abbreviations(profile, message)
    if pruned != profile:
        profile = pruned
        _update_state(sid, profile=profile)
    try:
        from app.chat import web_research

        profile, unresolved = web_research.resolve_profile(profile, max_concepts=3)
        _update_state(sid, profile=profile)
        if unresolved:
            _update_state(sid, pending_ambiguity=unresolved,
                          pending_ambiguity_count=_message_count(sid))
            return {"ok": False, "needs_confirmation": unresolved,
                    "message": "Ask the user which meaning they intend before searching: "
                               + " | ".join(u.get("question", "") for u in unresolved)}
    except Exception:  # noqa: BLE001
        logger.warning("alias resolution failed; continuing with user label", exc_info=True)
    writer = get_stream_writer()
    report = None
    brief = None
    if web_brief_json:
        try:
            parsed = json.loads(web_brief_json)
            if isinstance(parsed, dict):
                brief = parsed.get("brief") if isinstance(parsed.get("brief"), dict) else parsed
        except Exception:  # noqa: BLE001
            logger.warning("invalid web_brief_json ignored")
    try:
        gen = workbench.run_pipeline(sid, message, profile, web_brief=brief)
        while True:
            ev = next(gen)
            # The structured report event renders the full result in the UI;
            # omitting the deterministic Markdown tokens avoids duplicating it
            # with the agent's own final summary.
            if ev.get("event") != "token":
                writer(ev)
    except StopIteration as stop:
        report = stop.value
    except Exception as exc:  # noqa: BLE001
        logger.exception("research pipeline failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    counts = (report or {}).get("counts") or {}
    cr = (report or {}).get("constraint_report") or {}
    ft = (report or {}).get("fulltext_stats") or {}
    per = (report or {}).get("per_source") or {}
    attempted = int(ft.get("attempted") or 0)
    n_candidates = int(counts.get("candidates") or 0)
    if report:
        _update_state(sid, last_report=report,
                      last_report_seq=int(_state(sid).get("last_report_seq") or 0) + 1,
                      conversation_stage="report")
    return {"ok": True, "counts": counts,
            "constraint_counts": cr.get("counts") or {},
            "constraint_disclosure": cr.get("disclosure") or [],
            "fulltext": {"attempted": attempted,
                         "fulltext_ok": ft.get("fulltext_ok", 0),
                         "not_assessed": max(0, n_candidates - attempted),
                         "kinds": ft.get("kinds") or {}},
            "coverage_note": (f"{attempted} of {n_candidates} candidate papers were opened; "
                              f"{max(0, n_candidates - attempted)} were not assessed. "
                              "Do not call unopened papers abstract-only."),
            "sources": {k: {"hits": v.get("hits"), "ok": v.get("ok"),
                            "error": v.get("error") or ""}
                        for k, v in per.items()},
            "rubric_ok": (report or {}).get("rubric_ok", True),
            "catalog": counts.get("catalog"), "candidates": counts.get("candidates"),
            "datasets": counts.get("datasets"),
            "note": "report emitted in the step stream; summarize these counts and "
                    "disclosures, including abstract-only/skipped sources."}


def _summarizer(model, *, trigger_tokens: int = 24000,
                keep_messages: int = 12):
    """Role-scoped summarization middleware; no shared Rubric middleware."""
    from deepagents.backends import StateBackend
    from deepagents.middleware import SummarizationMiddleware

    return SummarizationMiddleware(model=model, backend=StateBackend(),
                                   trigger=("tokens", trigger_tokens),
                                   keep=("messages", keep_messages))


# ---------------------------------------------------------------- Ming tools

def record_plan(topic: str = "", organism: str = "", tissue: str = "",
                modalities: list[str] | None = None, keywords: list[str] | None = None,
                ready: bool = False,
                config: RunnableConfig | None = None) -> dict:
    """Save the mining profile for the current Ming session. Call this when the
    user has supplied enough detail (or says any/skip), with ready=true to
    generate the mining proposal. Fields left empty are kept unchanged."""
    sid = _sid_current(config)
    if not sid:
        return {"ok": False, "message": "no session"}
    state = _state(sid)
    profile = dict(state.get("profile") or {})
    if topic.strip():
        profile["topic"] = topic.strip()[:120]
    if organism.strip():
        profile["organism"] = organism.strip()[:80]
    if tissue.strip():
        profile["tissue"] = tissue.strip()[:80]
    if modalities:
        profile["modalities"] = [str(m).strip() for m in modalities if str(m).strip()][:8]
    if keywords:
        old = [str(k) for k in (profile.get("keywords") or [])]
        for k in keywords:
            k = str(k).strip()
            if k and k not in old:
                old.append(k)
        profile["keywords"] = old[:12]
    _update_state(sid, profile=profile, ming_ready=bool(ready))
    return {"ok": True, "ready": bool(ready), "profile": profile}


_MING_PROMPT = """You are Ming, the BioAgentDB mining planner. Your job is to turn a
user's topic into a concrete, confirmable mining plan. You do NOT mine or invent data.

Use the read_file tool to load the relevant /skills/<name>/SKILL.md before
planning or clarifying (mining-planner, evidence-gates).

Rules:
1. If you need to ask the user anything, call ask_user(question=..., options=[...])
   exactly once and then stop. Do not write a question as plain text. Only ask
   when the answer is blocking; there is no fixed question count.
   If unsure whether it is blocking, call task(subagent_type="clarify_judge", ...)
   for advice; clarify_judge does not talk to the user.
2. Never invent accessions, paper counts or datasets. This step is planning only.
3. Use full names for topic/organism/tissue (for example
   'amyotrophic lateral sclerosis', not 'ALS'); put abbreviations and extra
   facets in concepts_json. Do not invent synonyms; aliases will be resolved online.
4. When the topic is clear enough (even if the user skipped details), call
   save_profile(topic=..., organism=..., tissue=..., modalities=[...], concepts_json='[{"type":"disease|modality|tissue|organism|topic","label":"..."}]', ready=true)
   exactly once. Do not ask further questions after ready=true.
5. Then call task(subagent_type="mining_planner", description="Draft the mining plan for the saved profile: <topic, organism/tissue/modalities>"). Relay its concise plan; do not invent accessions or counts.
6. Keep replies short (1-3 sentences). Current session state is provided below;
   use it instead of asking again.
"""


_MING_RUBRIC = """The final reply is a mining proposal or a clarification, not a data claim.
It must:
1. never invent accessions, paper counts, sample counts, or data availability;
2. if it proposes mining, state topic, organism/tissue/modalities, query strategy,
   expected output, cadence, and stopping rule;
3. disclose paywalled full text or controlled-access limitations;
4. ask no more than two questions total and never ask again after ready=true.
If any criterion fails, return needs_revision with a concise fix."""

_EXPLANATION_RE = re.compile(
    r"^\s*(what|who|how|why|define|explain|tell me about)\b", re.I)
_DATA_TERM_RE = re.compile(
    r"\b(dataset|datasets|data|find|search|mine|accession|atlas|cohort|"
    r"sample|samples|rna-seq|scrna|snrna|proteom|genom|transcriptom|"
    r"spatial|perturb|benchmark)\b", re.I)


def _is_explanation_turn(message: str) -> bool:
    return bool(_EXPLANATION_RE.match(message or "")) and not bool(
        _DATA_TERM_RE.search(message or ""))


_RESEARCH_RUBRIC = """The final answer must be faithful to the deterministic report.
It must:
1. use only counts and accessions that appear in the report; never invent either;
2. label paper findings as announced/unverified, not as downloaded datasets;
3. disclose full vs partial vs unknown vs restricted constraint outcomes;
4. disclose papers without open full text and sources that were skipped;
5. not call a record a full match when a requested constraint is unknown or has no quote;
6. keep the prose summary short and point to the report for details.
If any criterion fails, return needs_revision with a concise fix."""


def _ming_agent():  # shared intent core, Ming mode
    global _MING_AGENT
    if _MING_AGENT is None:
        from deepagents import create_deep_agent

        model = _model()
        from app.chat.skills_runtime import skill_backend, skill_permissions, skill_sources

        _MING_AGENT = create_deep_agent(
            model=model,
            tools=[save_profile, ask_user],
            system_prompt=_MING_PROMPT,
            middleware=[_summarizer(model, trigger_tokens=16000, keep_messages=10)],
            skills=skill_sources(),
            backend=skill_backend(),
            permissions=skill_permissions(),
            subagents=[_clarify_subagent(model), _mining_planner_subagent(model)],
            checkpointer=_saver(),
        )
    return _MING_AGENT


def stream_ming(sid: str, message: str, resume=None):
    """SSE generator for the DeepAgents Ming planning path."""
    from app.chat import ming as ming_mod

    _ensure_session(sid, "ming")
    if resume is None:
        _add_message(sid, "user", message)
    state = _state(sid)
    profile = dict(state.get("profile") or {})
    rounds = int(state.get("rounds") or 0)
    goal = state.get("goal") or message
    prior_pending = state.get("pending_clarification")
    if prior_pending:
        _update_state(sid, pending_clarification=None,
                      conversation_stage="followup")
    ctx = {"goal": goal[:300], "profile": profile, "rounds": rounds,
           "asked": state.get("asked") or [],
           "conversation_stage": state.get("conversation_stage") or "idle",
           "pending_clarification": prior_pending,
           "recent_messages": _recent_messages(sid, 8)}
    _CURRENT_SID.set(sid)
    if resume is None:
        _update_state(sid, goal=goal[:300], ming_ready=False)

    tokens: list[str] = []
    pending_revision = False
    try:
        agent = _ming_agent()
        cfg_run = {"configurable": {"thread_id": sid}}
        # Keep the state in the human message so it is present even on a fresh thread.
        prompt = (f"[session state] {json.dumps(ctx, ensure_ascii=False)}\n\n"
                  f"User: {message}")
        payload = resume if resume is not None else {
            "messages": [{"role": "user", "content": prompt}]}
        if _rubric_enabled() and isinstance(payload, dict):
            payload = {**payload, "rubric": _MING_RUBRIC}
        for mode, chunk in agent.stream(
                payload, config=cfg_run, stream_mode=["messages", "updates"]):
            if mode == "messages":
                msg, _meta = chunk
                lc_source = str((getattr(msg, "additional_kwargs", {}) or {}).get(
                    "lc_source") or "")
                if lc_source == "rubric_grader":
                    if str(getattr(msg, "type", "")).lower().startswith("human"):
                        pending_revision = True
                    continue
                typ = str(getattr(msg, "type", "")).lower()
                content = getattr(msg, "content", "")
                if typ.startswith("ai") and isinstance(content, str) and content:
                    if pending_revision:
                        tokens.clear()
                        yield _sse({"event": "token", "replace": True, "text": ""})
                        pending_revision = False
                    tokens.append(content)
                    yield _sse({"event": "token", "text": content})
    except Exception as exc:  # noqa: BLE001
        logger.exception("deepagents ming failed")
        yield _sse({"event": "token", "text": f"The agent hit an error: {exc}"})
        yield _sse({"event": "done"})
        return

    state = _state(sid)
    profile = dict(state.get("profile") or {})
    ready = bool(state.get("ming_ready"))
    pending_clar = state.get("pending_clarification")
    if pending_clar:
        q = str(pending_clar.get("question") or "Which meaning do you intend?")
        _add_message(sid, "assistant", q,
                     {"kind": "clarify", "options": pending_clar.get("options") or []})
        yield _sse({"event": "clarify", "text": q,
                    "options": pending_clar.get("options") or []})
        yield _sse({"event": "done"})
        return
    status = _rubric_status(agent, cfg_run) if _rubric_enabled() else ""
    if status:
        yield _sse({"event": "rubric", "scope": "ming", "status": status})
    topic = (profile.get("topic") or goal or "").strip()
    reply = "".join(tokens).strip() or ("Here is the mining plan." if ready
                                        else "Tell me a bit more about what to mine.")
    payload = None
    if ready and topic:
        try:
            from app.chat import web_research

            profile, unresolved = web_research.resolve_profile(profile, max_concepts=2)
            _update_state(sid, profile=profile)
            if unresolved:
                q = unresolved[0].get("question") or "Which meaning do you intend?"
                _add_message(sid, "assistant", q, {"kind": "clarify"})
                _update_state(sid, ming_ready=False)
                yield _sse({"event": "clarify", "text": q,
                            "options": unresolved[0].get("candidates") or []})
                yield _sse({"event": "done"})
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("ming alias resolution failed: %s", exc)
        try:
            payload = ming_mod._proposal(topic, profile)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ming proposal failed: %s", exc)

    _add_message(sid, "assistant", reply,
                 {"kind": "proposal" if payload else "clarify"})
    if payload:
        yield _sse({"event": "profile", "profile": profile, "ready": True,
                    "missing": [], "defaults": []})
        yield _sse({"event": "proposal", "proposal": payload})
        _update_state(sid, rounds=rounds + 1, last_ready=True)
    else:
        _update_state(sid, rounds=rounds + 1, last_ready=False)
    yield _sse({"event": "done"})

# ---------------------------------------------------------------- Research agent

_RESEARCH_PROMPT = """You are the BioAgentDB research planner. You clarify a biomedical
data request, then run a deterministic evidence pipeline and report what it found.

Use the read_file tool to load the relevant /skills/<name>/SKILL.md before
answering (evidence-gates, query-expansion, report-format).
For a competition/platform/new term, task(subagent_type="web_research_worker",
description=...) is available for web context; the deterministic research_worker
still performs its own guarded web research if needed.

Rules:
1. If you need to ask the user anything, call ask_user(question=..., options=[...])
   exactly once and then stop. Do not write a question as plain text. Only ask
   when the answer is blocking; there is no fixed question count. If unsure,
   call task(subagent_type="clarify_judge", ...) for advice; it does not talk
   to the user.
2. Use full names for topic/organism/tissue (for example
   'amyotrophic lateral sclerosis', not 'ALS'); put abbreviations and extra
   facets in concepts_json as [{"type":"disease|modality|tissue|organism|topic",
   "label":"...","parent":"optional"}]. Do not invent synonyms; aliases will be resolved online.
3. When the topic is clear enough, call save_profile(topic=..., organism=...,
   tissue=..., modalities=[...], concepts_json='[...]', ready=true) exactly once.
4. Delegate retrieval: task(subagent_type="research_worker",
   description="Run retrieval for the saved profile and return its artifact counts.").
   Do NOT call a pipeline tool directly; do NOT invent accessions or counts.
5. If the worker reports a clarification need, call ask_user and stop.
6. Then delegate quote judging: task(subagent_type="quote_judge",
   description="Judge the current retrieval artifact.").
7. Then delegate reporting: task(subagent_type="report_worker",
   description="Build the report and summary from the current artifacts.").
8. After the report worker returns, reply in 2-3 sentences using only its counts.
9. For a follow-up after a report, use the same conversation context: answer
   questions about the last report without rerunning if no new search is needed;
   otherwise repeat steps 4-7.
10. For a new topic, confirm the profile change first.
11. Refuse unrelated/non-biomedical requests briefly.
"""


def _web_research_worker_spec() -> dict:
    from app.chat.web_research_worker import build_web_research_worker

    return {
        "name": "web_research_worker",
        "description": (
            "Search the web and return a compact brief with terms/sources. "
            "Use only for unknown competitions/platforms/new terminology; it does "
            "not read the catalog or paper full text."),
        "runnable": build_web_research_worker(),
    }


def _research_worker_spec() -> dict:
    from app.chat.research_subagent import build_research_worker

    return {
        "name": "research_worker",
        "description": (
            "Run retrieval for the saved profile and return catalog/candidate/context "
            "counts plus a retrieval artifact. Use only after save_profile(ready=true); "
            "it does not judge quotes or build the report."),
        "runnable": build_research_worker(),
    }


def _mining_planner_subagent(model) -> dict:
    """Mining proposal specialist used by Ming's orchestration layer."""
    from app.chat.skills_runtime import skill_sources

    return {
        "name": "mining_planner",
        "description": (
            "Draft a mining plan from a saved profile: scope, query strategy, "
            "expected output, cadence, stopping rule and limitations. It does "
            "not mine, retrieve, or invent data."),
        "system_prompt": (
            "You are mining_planner. Follow /skills/mining-planner/SKILL.md and "
            "/skills/evidence-gates/SKILL.md. Return a concise, concrete plan; "
            "never invent accessions, paper counts or datasets."),
        "tools": [],
        "skills": skill_sources(),
        "model": model,
    }


def _clarify_subagent(model) -> dict:
    """Advisor for whether a question is genuinely blocking.

    The main orchestrator makes the decision; this subagent only returns a
    recommendation. It cannot write state or call tools.
    """
    from app.chat.skills_runtime import skill_sources

    return {
        "name": "clarify_judge",
        "description": (
            "Advise whether the orchestrator should ask the user a blocking "
            "clarification question or proceed. Returns JSON only; never asks "
            "the user directly."),
        "system_prompt": (
            "You are clarify_judge. Follow /skills/clarification-policy/SKILL.md. "
            "Default to proceed. Return JSON with action (ask|proceed|refuse|answer), "
            "one question max, blocking, expected_gain, assumptions. Do not use "
            "outside knowledge and do not write files."),
        "tools": [],
        "skills": skill_sources(),
        "model": model,
    }


def _quote_subagent(model) -> dict:
    """Compiled quote worker: judges the retrieval artifact, never fetches."""
    from app.chat.quote_worker import build_quote_worker

    return {
        "name": "quote_judge",
        "description": (
            "Judge the quote contexts in the current retrieval artifact and return "
            "validated per-constraint JSON. It never retrieves papers or builds a report."),
        "runnable": build_quote_worker(),
    }


def _report_worker_spec() -> dict:
    from app.chat.report_worker import build_report_worker

    return {
        "name": "report_worker",
        "description": (
            "Build the deterministic constraint report, report event and summary "
            "from the current retrieval and judgment artifacts. It never retrieves."),
        "runnable": build_report_worker(),
    }


def _research_agent():
    global _RESEARCH_AGENT
    if _RESEARCH_AGENT is None:
        from deepagents import create_deep_agent

        model = _model()
        from app.chat.skills_runtime import skill_backend, skill_permissions, skill_sources

        _RESEARCH_AGENT = create_deep_agent(
            model=model,
            tools=[save_profile, ask_user],
            system_prompt=_RESEARCH_PROMPT,
            middleware=[_summarizer(model)],
            skills=skill_sources(),
            backend=skill_backend(),
            permissions=skill_permissions(),
            subagents=[_web_research_worker_spec(), _research_worker_spec(),
                       _clarify_subagent(model), _quote_subagent(model),
                       _report_worker_spec()],
            checkpointer=_saver(),
        )
    return _RESEARCH_AGENT


def stream_research(sid: str, message: str, resume=None):
    """SSE generator for the DeepAgents Research path (custom step events)."""
    _ensure_session(sid, "research")
    if resume is None:
        _add_message(sid, "user", message)
    state = _state(sid)
    profile = dict(state.get("profile") or {})
    goal = state.get("goal") or message
    if resume is None:
        _update_state(sid, goal=goal[:300], research_message=message)
    _CURRENT_SID.set(sid)
    prev_report_seq = int(state.get("last_report_seq") or 0)
    prior_pending = state.get("pending_clarification")
    if prior_pending:
        # The new user message is the answer to the pending question.  Keep it
        # in this turn's context, then clear it so the orchestrator can resume.
        _update_state(sid, pending_clarification=None,
                      conversation_stage="followup")

    # Deterministic guard for the highest-frequency dangerous abbreviation.
    # The model otherwise tends to auto-add "AD" and loop through alias
    # rejection.  One explicit question is cheaper and safer.
    if (resume is None and re.search(r"\bAD\b", message)
            and not re.search(r"Alzheimer|atopic|eczema", message, re.I)):
        q = ("Do you mean **Alzheimer's disease** or **atopic dermatitis** "
             "by \"AD\"?")
        options = ["Alzheimer's disease", "Atopic dermatitis"]
        _update_state(
            sid,
            pending_clarification={
                "question": q, "options": options, "reason_code": "ambiguous_alias",
                "asked_key": _question_key(q), "created_at": utcnow(),
            },
            asked_questions=(list(state.get("asked_questions") or []) + [q])[-12:],
            conversation_stage="clarifying",
        )
        _add_message(sid, "assistant", q, {"kind": "clarify", "options": options})
        yield _sse({"event": "clarify", "text": q, "options": options})
        yield _sse({"event": "done"})
        return

    # Preflight the saved profile before invoking the LLM.  An unresolved
    # abbreviation must become one short clarification, not a tool/rubric loop.
    if resume is None and profile and state.get("profile_ready"):
        pruned = _prune_unmentioned_abbreviations(profile, message)
        if pruned != profile:
            profile = pruned
            _update_state(sid, profile=profile)
        try:
            from app.chat import web_research

            resolved, unresolved = web_research.resolve_profile(profile, max_concepts=3)
            profile = resolved
            _update_state(sid, profile=profile)
            if unresolved:
                u = unresolved[0]
                q = u.get("question") or "Which meaning do you intend?"
                _update_state(
                    sid,
                    pending_ambiguity=unresolved,
                    pending_ambiguity_count=_message_count(sid),
                    pending_clarification={
                        "question": q, "options": u.get("candidates") or [],
                        "reason_code": "ambiguous_alias",
                        "asked_key": _question_key(q), "created_at": utcnow(),
                    },
                    asked_questions=(list(state.get("asked_questions") or []) + [q])[-12:],
                    conversation_stage="clarifying",
                )
                _add_message(sid, "assistant", q, {"kind": "clarify"})
                yield _sse({"event": "clarify", "text": q,
                            "options": u.get("candidates") or []})
                yield _sse({"event": "done"})
                return
        except Exception:  # noqa: BLE001
            logger.warning("research preflight alias resolution failed", exc_info=True)

    tokens: list[str] = []
    pending_revision = False
    steps: list[dict] = []
    report_obj: dict = {}
    report_seen = False
    started_at = time.time()
    yield _sse({"event": "status", "phase": "preparing", "text": "Preparing request",
                "elapsed_ms": 0})
    try:
        agent = _research_agent()
        cfg_run = {"configurable": {"thread_id": sid}}
        last_report_state = state.get("last_report") or {}
        ctx = {"goal": goal[:300], "profile": profile,
               "ready": bool(state.get("profile_ready")),
               "conversation_stage": state.get("conversation_stage") or "idle",
               "pending_clarification": prior_pending,
               "last_report_counts": (last_report_state.get("counts") or {}),
               "last_report_disclosure": ((last_report_state.get("constraint_report") or {})
                                          .get("disclosure") or []),
               "recent_messages": _recent_messages(sid, 8)}
        prompt = (f"[session state] {json.dumps(ctx, ensure_ascii=False)}\n\n"
                  f"User: {message}")
        payload = resume if resume is not None else {
            "messages": [{"role": "user", "content": prompt}]}
        use_rubric = (_rubric_enabled() and bool(state.get("profile_ready"))
                      and not _is_explanation_turn(message))
        if use_rubric and isinstance(payload, dict):
            payload = {**payload, "rubric": _RESEARCH_RUBRIC}
        from app.chat import event_bus

        bus = event_bus.open_session(sid)
        agent_q: queue.Queue = queue.Queue()

        def _produce():
            try:
                for m, ch in agent.stream(
                        payload, config=cfg_run,
                        stream_mode=["messages", "updates", "custom"]):
                    agent_q.put((m, ch))
            except Exception as exc:  # noqa: BLE001
                agent_q.put(("__error__", exc))
            finally:
                agent_q.put(("__end__", None))

        producer = threading.Thread(target=_produce, daemon=True)
        producer.start()
        while True:
            # Drain worker events first, so step/report updates are visible
            # while the research_worker task is still running.
            while True:
                try:
                    bus_ev = bus.get_nowait()
                except queue.Empty:
                    break
                if isinstance(bus_ev, dict):
                    etype = str(bus_ev.get("event") or "")
                    if etype in ("step", "progress"):
                        steps.append(bus_ev)
                    if etype == "report":
                        report_obj = bus_ev.get("report") or {}
                        report_seen = True
                    if etype == "token":
                        continue
                yield _sse(bus_ev)
            try:
                mode, chunk = agent_q.get(timeout=0.2)
            except queue.Empty:
                if not producer.is_alive():
                    # One final drain after the producer finished.
                    while True:
                        try:
                            bus_ev = bus.get_nowait()
                        except queue.Empty:
                            break
                        if isinstance(bus_ev, dict):
                            etype = str(bus_ev.get("event") or "")
                            if etype in ("step", "progress"):
                                steps.append(bus_ev)
                            if etype == "report":
                                report_obj = bus_ev.get("report") or {}
                                report_seen = True
                            if etype == "token":
                                continue
                        yield _sse(bus_ev)
                    break
                continue
            if mode == "__end__":
                break
            if mode == "__error__":
                raise chunk
            if mode == "custom":
                if isinstance(chunk, dict):
                    etype = str(chunk.get("event") or "")
                    if etype in ("step", "progress"):
                        steps.append(chunk)
                    if etype == "report":
                        report_obj = chunk.get("report") or {}
                        report_seen = True
                    if str(chunk.get("type") or "").startswith("rubric_evaluation_"):
                        continue
                    if etype == "token":
                        continue
                yield _sse(chunk)
            elif mode == "messages":
                msg, _meta = chunk
                lc_source = str((getattr(msg, "additional_kwargs", {}) or {}).get(
                    "lc_source") or "")
                if lc_source == "rubric_grader":
                    if str(getattr(msg, "type", "")).lower().startswith("human"):
                        pending_revision = True
                    continue
                typ = str(getattr(msg, "type", "")).lower()
                content = getattr(msg, "content", "")
                if typ.startswith("ai") and isinstance(content, str) and content:
                    if report_seen:
                        # The deterministic report summary is the visible answer.
                        continue
                    if pending_revision:
                        tokens.clear()
                        yield _sse({"event": "token", "replace": True, "text": ""})
                        pending_revision = False
                    tokens.append(content)
                    yield _sse({"event": "token", "text": content})
                elif typ == "tool" and getattr(msg, "name", "") == "save_profile":
                    try:
                        payload = json.loads(str(content or "{}"))
                    except Exception:  # noqa: BLE001
                        payload = {}
                    if payload.get("profile"):
                        yield _sse({"event": "profile", "profile": payload["profile"],
                                    "ready": bool(payload.get("ready")),
                                    "missing": [], "defaults": []})
        event_bus.close_session(sid)
    except Exception as exc:  # noqa: BLE001
        logger.exception("deepagents research failed")
        yield _sse({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
        yield _sse({"event": "done"})
        return

    text = "".join(tokens).strip() or "Research complete — see the report above."
    state_after = _state(sid)
    # ask_user is the explicit stop path; emit its question even if the model
    # did not include a question mark in the visible text.
    pending_clar = state_after.get("pending_clarification")
    if pending_clar and \
            int(state_after.get("last_report_seq") or 0) <= prev_report_seq:
        q = str(pending_clar.get("question") or text or
                "Which meaning do you intend?")
        options = pending_clar.get("options") or []
        _add_message(sid, "assistant", text or q,
                     {"kind": "clarify", "options": options,
                      "steps": steps,
                      "elapsed_ms": int((time.time() - started_at) * 1000)})
        yield _sse({"event": "clarify", "text": text or q, "options": options})
        yield _sse({"event": "done",
                    "elapsed_ms": int((time.time() - started_at) * 1000)})
        return
    # Clarification is a first-class result, not a report graded by the rubric.
    if state_after.get("pending_ambiguity") and \
            int(state_after.get("last_report_seq") or 0) <= prev_report_seq:
        pending = state_after["pending_ambiguity"]
        q = str((pending[0] or {}).get("question") or text or
                "Which meaning do you intend?")
        options = []
        topic = str((state_after.get("profile") or {}).get("topic") or "")
        if topic:
            options.append(topic)
        for u in pending:
            options.extend(str(x) for x in (u.get("candidates") or []))
        options = list(dict.fromkeys(options))[:6]
        _add_message(sid, "assistant", text or q,
                     {"kind": "clarify", "options": options,
                      "steps": steps,
                      "elapsed_ms": int((time.time() - started_at) * 1000)})
        yield _sse({"event": "clarify", "text": text or q, "options": options})
        yield _sse({"event": "done"})
        return
    status = _rubric_status(agent, cfg_run) if use_rubric else ""
    last_report = state_after.get("last_report") or {}
    if int(state_after.get("last_report_seq") or 0) <= prev_report_seq:
        last_report = {}
    report_final = report_obj or last_report
    # The model can ask a scope question before the pipeline.  Do not save it
    # as a report or run it through the report quality gate.
    if not report_final and re.search(r"[?？]", text or ""):
        key = _question_key(text)
        _update_state(
            sid,
            pending_clarification={
                "question": text, "options": [], "reason_code": "scope_choice",
                "asked_key": key, "created_at": utcnow(),
            },
            asked_questions=(list(state_after.get("asked_questions") or []) + [text])[-12:],
            conversation_stage="clarifying",
        )
        _add_message(sid, "assistant", text,
                     {"kind": "clarify", "steps": steps,
                      "elapsed_ms": int((time.time() - started_at) * 1000)})
        yield _sse({"event": "clarify", "text": text, "options": []})
        yield _sse({"event": "done",
                    "elapsed_ms": int((time.time() - started_at) * 1000)})
        return
    # Rubric grades a dataset report.  Definition/refusal turns have no report;
    # showing a failed quality gate there is misleading.
    if status and report_final:
        yield _sse({"event": "rubric", "scope": "research", "status": status})
    summary_md = ""
    if report_final:
        try:
            from app.chat import workbench as _wb

            summary_md = _wb._final_reply(report_final)
        except Exception:  # noqa: BLE001
            logger.warning("report summary rendering failed", exc_info=True)
        if summary_md:
            yield _sse({"event": "summary", "markdown": summary_md,
                        "elapsed_ms": int((time.time() - started_at) * 1000)})
    content = summary_md or text
    meta = {"kind": "report", "report": report_final,
            "summary": summary_md, "steps": steps,
            "elapsed_ms": int((time.time() - started_at) * 1000)}
    _add_message(sid, "assistant", content, meta)
    yield _sse({"event": "done", "elapsed_ms": meta["elapsed_ms"]})
