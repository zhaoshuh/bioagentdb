"""Quote judging worker for the decoupled Research workflow.

It reads the retrieval artifact from session state, judges every quote context,
and stores the validated judgments. No retrieval or report generation happens
here.
"""
from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph

logger = logging.getLogger(__name__)


def _quote_node(state: MessagesState, config: RunnableConfig) -> dict:
    from app.chat import event_bus, quote_evidence, workbench

    sid = str((config or {}).get("configurable", {}).get("thread_id") or "")
    if not sid:
        return {"messages": [AIMessage(content="quote_judge: no session")]}
    st = workbench._state(sid)
    retrieval = st.get("last_retrieval") or {}
    contexts = retrieval.get("contexts") or []
    profile = retrieval.get("profile") or st.get("profile") or {}
    try:
        event_bus.emit(sid, {"event": "step", "name": "quotes", "status": "start"})
        judgments = quote_evidence.judge_contexts(contexts, profile)
    except Exception as exc:  # noqa: BLE001
        logger.exception("quote_judge failed")
        event_bus.emit(sid, {"event": "error",
                             "message": f"quote_judge: {type(exc).__name__}: {exc}"})
        return {"messages": [AIMessage(content=f"quote_judge error: {type(exc).__name__}: {exc}")]}
    st["last_judgments"] = judgments
    st["conversation_stage"] = "judged"
    workbench._set_state(sid, st)
    n_supp = sum(1 for j in judgments for c in (j.get("constraints") or {}).values()
                 if c.get("status") == "supported")
    event_bus.emit(sid, {
        "event": "step", "name": "quotes", "status": "done",
        "detail": f"{len(contexts)} context(s) · {n_supp} supported constraint(s)"})
    return {"messages": [AIMessage(content=json.dumps(
        {"ok": True, "contexts": len(contexts), "judgments": len(judgments),
         "supported_constraints": n_supp}, ensure_ascii=False))]}


def build_quote_worker():
    graph = StateGraph(MessagesState)
    graph.add_node("quote", _quote_node)
    graph.add_edge(START, "quote")
    graph.add_edge("quote", END)
    return graph.compile()
