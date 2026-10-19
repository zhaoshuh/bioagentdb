"""Report worker for the decoupled Research workflow.

It consumes retrieval + judgment artifacts and builds the final deterministic
report and Markdown summary. It never performs retrieval or quote judgment.
"""
from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph

logger = logging.getLogger(__name__)


def _report_node(state: MessagesState, config: RunnableConfig) -> dict:
    from app.chat import event_bus, quote_evidence, workbench

    sid = str((config or {}).get("configurable", {}).get("thread_id") or "")
    if not sid:
        return {"messages": [AIMessage(content="report_worker: no session")]}
    st = workbench._state(sid)
    retrieval = st.get("last_retrieval") or {}
    if not retrieval:
        return {"messages": [AIMessage(content="report_worker: no retrieval artifact")]}
    profile = retrieval.get("profile") or st.get("profile") or {}
    catalog = retrieval.get("catalog") or []
    candidates = retrieval.get("candidates") or []
    judgments = st.get("last_judgments") or []
    try:
        event_bus.emit(sid, {"event": "step", "name": "report", "status": "start"})
        constraint_report = quote_evidence.match_records(
            profile, catalog, candidates, judgments)
        report = workbench.build_report(
            profile, catalog, candidates, retrieval.get("per_source") or {},
            retrieval.get("fulltext_stats") or {},
            research=retrieval.get("research"),
            constraint_report=constraint_report)
        workbench.write_search_cache(sid, profile, catalog, candidates, report)
        st = workbench._state(sid)
        st.update(last_report=report,
                  last_report_seq=int(st.get("last_report_seq") or 0) + 1,
                  conversation_stage="report")
        workbench._set_state(sid, st)
    except Exception as exc:  # noqa: BLE001
        logger.exception("report_worker failed")
        event_bus.emit(sid, {"event": "error",
                             "message": f"report_worker: {type(exc).__name__}: {exc}"})
        return {"messages": [AIMessage(content=f"report_worker error: {type(exc).__name__}: {exc}")]}
    event_bus.emit(sid, {"event": "constraint_report",
                         "constraint_report": constraint_report})
    event_bus.emit(sid, {"event": "results", "catalog": catalog[:6],
                         "candidates": candidates[:workbench.TOP_N]})
    event_bus.emit(sid, {"event": "report", "report": report})
    event_bus.emit(sid, {"event": "step", "name": "report", "status": "done"})
    return {"messages": [AIMessage(content=json.dumps(
        {"ok": True, "counts": report.get("counts") or {},
         "constraint_counts": constraint_report.get("counts") or {},
         "rubric_ok": report.get("rubric_ok", True)}, ensure_ascii=False))]}


def build_report_worker():
    graph = StateGraph(MessagesState)
    graph.add_node("report", _report_node)
    graph.add_edge(START, "report")
    graph.add_edge("report", END)
    return graph.compile()
