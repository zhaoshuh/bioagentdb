"""Per-session event bridge for subagent workers.

The pipeline runs inside a CompiledSubAgent, so its step/report events do not
automatically reach the parent SSE stream.  Workers publish to a bounded queue
keyed by the session; stream_research drains it while the agent runs.
"""
from __future__ import annotations

import queue
from typing import Any

_QUEUES: dict[str, queue.Queue] = {}


def open_session(sid: str) -> queue.Queue:
    q = _QUEUES.get(sid)
    if q is None:
        q = queue.Queue(maxsize=2000)
        _QUEUES[sid] = q
    return q


def emit(sid: str, event: dict[str, Any]) -> None:
    q = _QUEUES.get(sid)
    if q is None:
        return
    try:
        q.put_nowait(event)
    except queue.Full:
        # Never block a worker because the UI is slow.
        pass


def close_session(sid: str) -> None:
    _QUEUES.pop(sid, None)
