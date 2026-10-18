"""DeepAgents runtime for the Ask and Ming agents (L-0396).

Feature-flagged by BIOAGENTDB_AGENT_IMPL=deepagents; the legacy implementation
remains the default until the DeepAgents path passes the regression suite.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
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
_ASK_AGENT = None
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


def _model():
    from langchain_openai import ChatOpenAI

    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("deepseek_api_key") or ""
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    return ChatOpenAI(
        model=os.environ.get("DEEPSEEK_MODEL") or cfg.get("deepseek_model") or "deepseek-flash",
        base_url=os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("deepseek_base_url")
        or "https://api.deepseek.com",
        api_key=key,
        temperature=0,
        timeout=180,
    )


_SANDBOX = None


def _sandbox_backend():
    """Return a DeepAgents BaseSandbox backend if the sidecar is configured."""
    global _SANDBOX
    if _SANDBOX is not None:
        return _SANDBOX
    mode = os.environ.get("BIOAGENTDB_SANDBOX", "").strip().lower()
    url = os.environ.get("SANDBOX_URL", "").strip()
    if mode not in ("remote", "docker", "base") or not url:
        return None
    try:
        from app.chat.sandbox_backend import RemoteSandbox

        sandbox = RemoteSandbox(url, os.environ.get("SANDBOX_TOKEN", ""))
        if not sandbox.health():
            logger.warning("sandbox sidecar unreachable at %s; using StateBackend", url)
            return None
        _SANDBOX = sandbox
        return sandbox
    except Exception:  # noqa: BLE001
        logger.warning("sandbox backend unavailable; using StateBackend", exc_info=True)
        return None


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

def _ensure_session(sid: str, kind: str = "ask") -> None:
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


# ---------------------------------------------------------------- Ask tools

def catalog_stats() -> dict:
    """Return catalog totals: datasets, verified datasets, papers, papers with data."""
    from app.catalog import store

    s = store.stats()
    return {k: s[k] for k in ("datasets_total", "datasets_verified",
                              "papers_total", "papers_mined", "papers_with_data")}


def search_records(query: str = "", theme: str = "", modality: str = "",
                   organism: str = "", limit: int = 6) -> dict:
    """Search paper records with datasets. Use query for free text, theme for a
    catalog topic slug, modality/organism to narrow. Returns total + top items."""
    from app.catalog import store

    r = store.list_records(q=query, theme=theme, modality=modality,
                           organism=organism, limit=max(1, min(int(limit), 20)),
                           offset=0, sort="year")
    label = theme or query or "catalog"
    if theme:
        try:
            from app.catalog import themes as themes_mod

            for t in themes_mod.list_themes():
                if t.get("slug") == theme:
                    label = t.get("name") or label
                    break
        except Exception:  # noqa: BLE001
            pass
    return {"total": r["total"], "label": label, "theme": theme or "",
            "items": r["items"]}


def propose_mining(topic: str, query: str = "",
                   config: RunnableConfig | None = None) -> dict:
    """Propose a mining task for a topic with no catalog coverage yet. Returns a
    proposal; the user must confirm before any task starts."""
    from app.chat.agent import ChatSession

    topic = (topic or "").strip()
    if not topic:
        return {"ok": False, "message": "topic required"}
    helper = ChatSession()
    slug, name, is_new = helper._resolve(topic, "", topic)
    budget = cfg.int("mining_task_papers_per_round", 200)
    cadence = cfg.int("mining_task_cadence_hours", 24)
    queries = helper._default_queries(name or topic, [topic])
    proposal = {
        "theme": slug, "theme_label": name or topic, "budget": budget,
        "papers_per_round": budget, "cadence_hours": cadence,
        "query": query or topic, "is_new_theme": bool(is_new), "in_scope": True,
        "theme_kind": "topic" if is_new else "",
    }
    sid = _sid_current(config)
    pending = {"theme": slug, "name": name or topic, "budget": budget,
               "papers_per_round": budget, "cadence_hours": cadence,
               "queries": queries, "query": query or topic,
               "theme_kind": "topic" if is_new else ""}
    if sid:
        _PENDING[sid] = pending
        _update_state(sid, pending_ask=pending)
    return {"ok": True, "proposal": proposal,
            "text": f"No catalog coverage yet for {name or topic}; a mining task can "
                    f"scan {budget} papers per round. Confirm to start."}


def task_status(task_id: str) -> dict:
    """Return the status of a previously started mining task."""
    from app.mining import tasks as task_engine

    t = task_engine.get_task(task_id)
    if not t:
        return {"ok": False, "message": "task not found"}
    return {"ok": True, "id": t.get("id"), "status": t.get("status"),
            "records_added": t.get("records_added"), "papers_seen": t.get("papers_seen")}


_ASK_PROMPT = """You are the BioAgentDB retrieval agent. The catalog stores paper-grounded
dataset records, each with a verbatim evidence quote and a checked link.

Rules:
1. Use tools for every fact: catalog_stats for counts, search_records for catalog
   searches, propose_mining only when search_records returns total=0 for a
   biomedical topic, task_status for a mining task.
2. Never invent accessions, counts, paper titles or evidence. If search_records
   returns total=0 for a biomedical topic, you MUST call propose_mining(topic=...)
   before answering, then tell the user a mining task can be started.
3. Answer biomedical/data questions only; refuse unrelated requests briefly.
4. Be concise (2-4 sentences). Mention the number of matching papers when known.
5. When the user asks "how many" about a previous search, use the tool result in
   the conversation; do not guess.
6. A sandboxed `execute` tool may be available. Use it for calculations or data
   processing needed for catalog answers (e.g., percentages); it has no network
   and no access to app secrets.
"""


def _summarizer(model):
    from deepagents.backends import StateBackend
    from deepagents.middleware import SummarizationMiddleware

    return SummarizationMiddleware(model=model, backend=StateBackend(),
                                   trigger=("tokens", 24000), keep=("messages", 12))


def _ask_agent():
    global _ASK_AGENT
    if _ASK_AGENT is None:
        from deepagents import create_deep_agent

        model = _model()
        _ASK_AGENT = create_deep_agent(
            model=model,
            tools=[catalog_stats, search_records, propose_mining, task_status],
            system_prompt=_ASK_PROMPT,
            middleware=[_summarizer(model)],
            backend=_sandbox_backend(),
            checkpointer=_saver(),
        )
    return _ASK_AGENT


def _tool_events(name: str, content: str, sid: str = "") -> list[dict]:
    try:
        payload = json.loads(content)
    except Exception:  # noqa: BLE001
        return []
    events: list[dict] = []
    if name == "search_records" and isinstance(payload, dict):
        events.append({"type": "records", "theme": payload.get("theme") or "",
                       "total": payload.get("total") or 0,
                       "records": (payload.get("items") or [])[:6]})
    elif name == "propose_mining" and isinstance(payload, dict) and payload.get("ok"):
        events.append({"type": "proposal", "proposal": payload.get("proposal") or {}})
    elif name == "task_status" and isinstance(payload, dict) and payload.get("ok"):
        events.append({"type": "text",
                       "text": f"Task {payload.get('id')}: {payload.get('status')} "
                               f"({payload.get('records_added')} records added)."})
    return events


def stream_ask(message: str, session_id: str | None):
    """SSE generator for the DeepAgents Ask path."""
    sid = session_id or uuid.uuid4().hex[:16]
    _ensure_session(sid, "ask")
    _add_message(sid, "user", message)
    state = _state(sid)
    if state.get("pending_ask"):
        _PENDING[sid] = state["pending_ask"]
    _CURRENT_SID.set(sid)

    tokens: list[str] = []
    final_text = ""
    kind = "text"

    try:
        agent = _ask_agent()
        cfg_run = {"configurable": {"thread_id": sid}}
        for mode, chunk in agent.stream(
                {"messages": [{"role": "user", "content": message}]},
                config=cfg_run, stream_mode=["messages", "updates"]):
            if mode == "messages":
                msg, _meta = chunk
                typ = str(getattr(msg, "type", "")).lower()
                content = getattr(msg, "content", "")
                if typ == "tool":
                    for ev in _tool_events(getattr(msg, "name", ""), str(content or ""), sid):
                        if ev.get("type") == "records":
                            kind = "records"
                        elif ev.get("type") == "proposal":
                            kind = "proposal"
                        yield _sse(ev)
                elif typ.startswith("ai") and isinstance(content, str) and content:
                    tokens.append(content)
                    yield _sse({"type": "token", "text": content})
            else:
                # keep the last complete assistant message for persistence
                for _node, payload in chunk.items():
                    if not isinstance(payload, dict):
                        continue
                    for m in payload.get("messages") or []:
                        if (str(getattr(m, "type", "")).lower().startswith("ai")
                                and isinstance(getattr(m, "content", ""), str)
                                and m.content and not getattr(m, "tool_calls", None)):
                            final_text = m.content
    except Exception as exc:  # noqa: BLE001
        logger.exception("deepagents ask failed")
        yield _sse({"type": "token", "text": f"The agent hit an error: {exc}"})
        yield _sse({"type": "meta", "session_id": sid, "kind": "text"})
        yield _sse({"type": "done"})
        return

    text = final_text or "".join(tokens)
    _add_message(sid, "assistant", text, {"kind": kind})
    _update_state(sid, last_kind=kind)
    yield _sse({"type": "meta", "session_id": sid, "kind": kind})
    yield _sse({"type": "done"})


def confirm_ask(session_id: str) -> dict:
    """Start the pending mining task proposed by the Ask agent."""
    from app.chat import mining

    p = _PENDING.pop(session_id, None) or _state(session_id).get("pending_ask")
    if not p:
        return {"kind": "refusal", "text": "No pending proposal to confirm."}
    task = mining.start_task(p.get("theme", ""),
                             papers_per_round=p.get("papers_per_round"),
                             cadence_hours=p.get("cadence_hours"),
                             name=p.get("name", ""), queries=p.get("queries"),
                             session_id=session_id, note=p.get("query", ""),
                             theme_kind=p.get("theme_kind", ""))
    _update_state(session_id, pending_ask=None)
    if not task.get("ok"):
        return {"kind": "refusal", "text": task.get("message", "Cannot start mining.")}
    return {"kind": "order", "order_id": task["task_id"], "task_id": task["task_id"],
            "theme": task["theme"],
            "text": f"Mining task started for {task['theme_label']}: "
                    f"{task['papers_per_round']} papers per round, then a patrol every "
                    f"{task['cadence_hours']}h."}

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
    if topic:
        old_topic = str(profile.get("topic") or profile.get("disease") or "")
        if old_topic and topic.casefold() != old_topic.casefold():
            # A new spelling/full name invalidates web aliases resolved for the old label.
            profile.pop("concepts", None)
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
    _update_state(sid, profile=profile, profile_ready=bool(ready),
                  ming_ready=bool(ready))
    return {"ok": True, "ready": bool(ready), "profile": profile}


def run_research(config: RunnableConfig | None = None) -> dict:
    """Run the deterministic research pipeline for the saved profile. It streams
    step/progress/results/report events and returns the counts + report summary.
    Call only after save_profile(ready=true)."""
    from langgraph.config import get_stream_writer

    from app.chat import workbench

    sid = _sid_current(config)
    if not sid:
        return {"ok": False, "error": "no session"}
    state = _state(sid)
    profile = dict(state.get("profile") or {})
    topic = str(profile.get("topic") or profile.get("disease") or "").strip()
    if not topic:
        return {"ok": False, "error": "profile missing; call save_profile(ready=true) first"}
    try:
        from app.chat import web_research

        profile, unresolved = web_research.resolve_profile(profile, max_concepts=3)
        _update_state(sid, profile=profile)
        if unresolved:
            return {"ok": False, "needs_confirmation": unresolved,
                    "message": "Ask the user which meaning they intend before searching: "
                               + " | ".join(u.get("question", "") for u in unresolved)}
    except Exception:  # noqa: BLE001
        logger.warning("alias resolution failed; continuing with user label", exc_info=True)
    writer = get_stream_writer()
    message = str(state.get("research_message") or topic)
    report = None
    try:
        gen = workbench.run_pipeline(sid, message, profile)
        while True:
            ev = next(gen)
            writer(ev)
    except StopIteration as stop:
        report = stop.value
    except Exception as exc:  # noqa: BLE001
        logger.exception("research pipeline failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    counts = (report or {}).get("counts") or {}
    return {"ok": True, "counts": counts,
            "catalog": counts.get("catalog"), "candidates": counts.get("candidates"),
            "datasets": counts.get("datasets"),
            "note": "report emitted in the step stream; summarize these counts only."}


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

Rules:
1. Ask at most two clarifying questions total, one at a time, and only for missing
   essentials: organism/system, technique/omics emphasis, or a too-vague topic.
   Accept "any" or "skip" as a complete answer.
2. Never invent accessions, paper counts or datasets. This step is planning only.
3. Use full names for topic/organism/tissue (for example
   'amyotrophic lateral sclerosis', not 'ALS'); put abbreviations and extra
   facets in concepts_json. Do not invent synonyms; aliases will be resolved online.
4. When the topic is clear enough (even if the user skipped details), call
   save_profile(topic=..., organism=..., tissue=..., modalities=[...], concepts_json='[{"type":"disease|modality|tissue|organism|topic","label":"..."}]', ready=true)
   exactly once, then reply with exactly: "Here is the mining plan."
   Do not ask further questions after ready=true.
4. Keep replies short (1-3 sentences). Do not ask more than two questions.
5. Current session state is provided below; use it instead of asking again.
"""


def _ming_agent():  # shared intent core, Ming mode
    global _MING_AGENT
    if _MING_AGENT is None:
        from deepagents import create_deep_agent

        model = _model()
        _MING_AGENT = create_deep_agent(
            model=model,
            tools=[save_profile],
            system_prompt=_MING_PROMPT,
            middleware=[_summarizer(model)],
            backend=_sandbox_backend(),
            checkpointer=_saver(),
        )
    return _MING_AGENT


def stream_ming(sid: str, message: str):
    """SSE generator for the DeepAgents Ming planning path."""
    from app.chat import ming as ming_mod

    _ensure_session(sid, "ming")
    _add_message(sid, "user", message)
    state = _state(sid)
    profile = dict(state.get("profile") or {})
    rounds = int(state.get("rounds") or 0)
    goal = state.get("goal") or message
    ctx = {"goal": goal[:300], "profile": profile, "rounds": rounds,
           "asked": state.get("asked") or []}
    _CURRENT_SID.set(sid)
    _update_state(sid, goal=goal[:300], ming_ready=False)

    tokens: list[str] = []
    try:
        agent = _ming_agent()
        cfg_run = {"configurable": {"thread_id": sid}}
        # Keep the state in the human message so it is present even on a fresh thread.
        prompt = (f"[session state] {json.dumps(ctx, ensure_ascii=False)}\n\n"
                  f"User: {message}")
        for mode, chunk in agent.stream(
                {"messages": [{"role": "user", "content": prompt}]},
                config=cfg_run, stream_mode=["messages", "updates"]):
            if mode == "messages":
                msg, _meta = chunk
                typ = str(getattr(msg, "type", "")).lower()
                content = getattr(msg, "content", "")
                if typ.startswith("ai") and isinstance(content, str) and content:
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

Rules:
1. Ask at most two clarifying questions total, one at a time, only for missing
   essentials: topic/disease, organism/system, technique/omics or sample constraints.
   Accept "any" or "skip" as complete.
2. Use full names for topic/organism/tissue (for example
   'amyotrophic lateral sclerosis', not 'ALS'); put abbreviations and extra
   facets in concepts_json as [{"type":"disease|modality|tissue|organism|topic",
   "label":"...","parent":"optional"}]. Do not invent synonyms; aliases will be resolved online.
3. When the topic is clear enough, call save_profile(topic=..., organism=...,
   tissue=..., modalities=[...], concepts_json='[...]', ready=true) exactly once.
3. Then call run_research() exactly once. It streams step/progress and the final
   report; do NOT invent accessions or counts.
4. If run_research returns needs_confirmation, ask the user which meaning they intend
   (one short question), then call save_profile with their full name and run_research again.
5. After run_research returns, reply in 2-3 sentences using only its counts.
5. For a new refinement after a report, update the profile and call run_research again.
6. Refuse unrelated/non-biomedical requests briefly.
"""


def _research_agent():
    global _RESEARCH_AGENT
    if _RESEARCH_AGENT is None:
        from deepagents import create_deep_agent

        model = _model()
        _RESEARCH_AGENT = create_deep_agent(
            model=model,
            tools=[save_profile, run_research],
            system_prompt=_RESEARCH_PROMPT,
            middleware=[_summarizer(model)],
            backend=_sandbox_backend(),
            checkpointer=_saver(),
        )
    return _RESEARCH_AGENT


def stream_research(sid: str, message: str):
    """SSE generator for the DeepAgents Research path (custom step events)."""
    _ensure_session(sid, "research")
    _add_message(sid, "user", message)
    state = _state(sid)
    profile = dict(state.get("profile") or {})
    goal = state.get("goal") or message
    _update_state(sid, goal=goal[:300], research_message=message)
    _CURRENT_SID.set(sid)

    tokens: list[str] = []
    try:
        agent = _research_agent()
        cfg_run = {"configurable": {"thread_id": sid}}
        ctx = {"goal": goal[:300], "profile": profile,
               "ready": bool(state.get("profile_ready"))}
        prompt = (f"[session state] {json.dumps(ctx, ensure_ascii=False)}\n\n"
                  f"User: {message}")
        for mode, chunk in agent.stream(
                {"messages": [{"role": "user", "content": prompt}]},
                config=cfg_run, stream_mode=["messages", "updates", "custom"]):
            if mode == "custom":
                yield _sse(chunk)
            elif mode == "messages":
                msg, _meta = chunk
                typ = str(getattr(msg, "type", "")).lower()
                content = getattr(msg, "content", "")
                if typ.startswith("ai") and isinstance(content, str) and content:
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
    except Exception as exc:  # noqa: BLE001
        logger.exception("deepagents research failed")
        yield _sse({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
        yield _sse({"event": "done"})
        return

    text = "".join(tokens).strip() or "Research complete — see the report above."
    _add_message(sid, "assistant", text, {"kind": "report"})
    yield _sse({"event": "done"})
