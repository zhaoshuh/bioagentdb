"""Deterministic Research worker exposed as a DeepAgents subagent.

The LLM supervisor should not have to re-implement the evidence pipeline.  This
compiled subagent is a thin graph around ``workbench.run_pipeline``: it reads the
saved profile for the current thread, runs the deterministic pipeline, and
returns a compact text result.  The normal Research path still calls
``run_research`` directly so the workbench receives every step/report event; the
worker is available for delegation and testing without duplicating pipeline
logic in the model.
"""
from __future__ import annotations

import json
import logging
import time

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph

logger = logging.getLogger(__name__)


def _run_pipeline_node(state: MessagesState, config: RunnableConfig) -> dict:
    from app.chat import event_bus, workbench

    sid = str((config or {}).get("configurable", {}).get("thread_id") or "")
    if not sid:
        return {"messages": [AIMessage(content="research_worker: no session thread")]}
    sess = workbench.get_session(sid)
    if not sess:
        return {"messages": [AIMessage(content="research_worker: session not found")]}
    st = workbench._state(sid)
    profile = dict(st.get("profile") or {})
    topic = str(profile.get("topic") or profile.get("disease") or "").strip()
    if not topic:
        return {"messages": [AIMessage(content="research_worker: profile missing")]}
    message = str(st.get("research_message") or topic)
    trace_id = f"tr_{sid}_{int(time.time())}"
    retrieval = None
    web_brief = st.get("last_web_brief")
    try:
        gen = workbench.run_pipeline(
            sid, message, profile,
            web_brief=web_brief,
            stop_after_contexts=True,
            trace_id=trace_id,
        )
        while True:
            try:
                ev = next(gen)
            except StopIteration as stop:
                retrieval = stop.value
                break
            event_bus.emit(sid, ev)
    except Exception as exc:  # noqa: BLE001
        logger.exception("research_worker pipeline failed")
        event_bus.emit(sid, {"event": "error",
                             "message": f"research_worker: {type(exc).__name__}: {exc}"})
        return {"messages": [AIMessage(content=f"research_worker error: {type(exc).__name__}: {exc}")]}
    if not isinstance(retrieval, dict):
        event_bus.emit(sid, {"event": "error", "message": "research_worker: no retrieval"})
        return {"messages": [AIMessage(content="research_worker: no retrieval")]}
    st_after = workbench._state(sid)
    st_after["last_retrieval"] = retrieval
    st_after["last_web_brief"] = None
    st_after["conversation_stage"] = "retrieved"
    workbench._set_state(sid, st_after)
    payload = {
        "trace_id": trace_id,
        "catalog": len(retrieval.get("catalog") or []),
        "candidates": len(retrieval.get("candidates") or []),
        "contexts": len(retrieval.get("contexts") or []),
        "fulltext": retrieval.get("fulltext_stats") or {},
    }
    return {"messages": [AIMessage(
        content="research_worker completed retrieval. "
                + json.dumps(payload, ensure_ascii=False))]}


def build_research_worker():
    graph = StateGraph(MessagesState)
    graph.add_node("pipeline", _run_pipeline_node)
    graph.add_edge(START, "pipeline")
    graph.add_edge("pipeline", END)
    return graph.compile()
