"""公网门禁：Basic Auth 反向代理（本机 8083 → app 8082），供 Cloudflare 隧道指向。

- 保护全部路径（浏览器/API 都需用户名密码）；支持任意方法、查询串、SSE 流式转发。
- 账号从环境变量 PUBLIC_GATE_USER / PUBLIC_GATE_PASS 读取（由启动脚本注入，不入仓库）。
用法: PUBLIC_GATE_USER=xxx PUBLIC_GATE_PASS=yyy python scripts/public_gate.py [--listen 8083] [--upstream http://127.0.0.1:8082]
"""

from __future__ import annotations

import argparse
import base64
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response, StreamingResponse  # noqa: E402
import os  # noqa: E402
import uvicorn  # noqa: E402

_ap = argparse.ArgumentParser()
_ap.add_argument("--listen", type=int, default=8083)
_ap.add_argument("--upstream", default="http://127.0.0.1:8082")
_ARGS, _ = _ap.parse_known_args()

USER = os.environ.get("PUBLIC_GATE_USER", "")
PASS = os.environ.get("PUBLIC_GATE_PASS", "")
if not USER or not PASS:
    raise SystemExit("PUBLIC_GATE_USER / PUBLIC_GATE_PASS 未设置")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_client = httpx.AsyncClient(base_url=_ARGS.upstream, timeout=httpx.Timeout(300.0, read=None))

_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "content-length"}


def _authed(request: Request) -> bool:
    h = request.headers.get("authorization", "")
    if not h.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(h[6:]).decode("utf-8", "replace")
        u, _, p = raw.partition(":")
    except Exception:  # noqa: BLE001
        return False
    return secrets.compare_digest(u, USER) and secrets.compare_digest(p, PASS)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy(path: str, request: Request):
    if not _authed(request):
        return JSONResponse({"error": "auth required"}, status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="BioAgentDB demo"'})
    url = "/" + path
    if request.url.query:
        url += "?" + request.url.query
    body = await request.body()
    req = _client.build_request(request.method, url,
                                headers={k: v for k, v in request.headers.items()
                                         if k.lower() not in _HOP and k.lower() != "host"},
                                content=body or None)
    try:
        resp = await _client.send(req, stream=True)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"upstream failed: {exc}"}, status_code=502)
    headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP}

    async def _iter():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()

    media = resp.headers.get("content-type", "")
    if media.startswith("text/event-stream"):
        headers["cache-control"] = "no-cache"
    return StreamingResponse(_iter(), status_code=resp.status_code, headers=headers,
                             media_type=None)


if __name__ == "__main__":
    print(f"[gate] listening 127.0.0.1:{_ARGS.listen} -> {_ARGS.upstream} (user={USER})",
          flush=True)
    uvicorn.run(app, host="127.0.0.1", port=_ARGS.listen, log_level="warning")
