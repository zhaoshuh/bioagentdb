"""Deterministic web-search worker.

It is intentionally isolated from the retrieval pipeline: it only receives a
query/context and returns a compact web brief.  No catalog access, no full-text
fetching, no DB writes.
"""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph


def _web_node(state: MessagesState, config: RunnableConfig) -> dict:
    from app.chat import web_research

    if not web_research.enabled():
        return {"messages": [AIMessage(content=json.dumps(
            {"ok": False, "error": "web research disabled"}))]}
    msgs = state.get("messages") or []
    text = ""
    if msgs:
        text = str(getattr(msgs[-1], "content", "") or "")
    # The orchestrator passes a compact JSON task; accept plain query too.
    query, context = text, ""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            query = str(data.get("query") or query)
            context = str(data.get("context") or "")
    except Exception:  # noqa: BLE001
        pass
    query = query.strip()[:240]
    if not query:
        return {"messages": [AIMessage(content=json.dumps(
            {"ok": False, "error": "query required"}))]}
    brief = web_research.brief(query, context=context)
    if brief:
        from app.chat import workbench

        sid = str((config or {}).get("configurable", {}).get("thread_id") or "")
        if sid:
            st = workbench._state(sid)
            st["last_web_brief"] = brief
            st["conversation_stage"] = "web_brief"
            workbench._set_state(sid, st)
    return {"messages": [AIMessage(content=json.dumps(
        {"ok": bool(brief), "brief": brief or None}, ensure_ascii=False))]}


def build_web_research_worker():
    graph = StateGraph(MessagesState)
    graph.add_node("web", _web_node)
    graph.add_edge(START, "web")
    graph.add_edge("web", END)
    return graph.compile()
