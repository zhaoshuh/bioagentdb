"""Minimal isolated sandbox sidecar: executes shell/Python inside its own container.

No host mounts, no DB/secrets, no external network (compose internal network).
Endpoints: /health, /execute, /upload, /download.
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORKSPACE = pathlib.Path("/workspace")
TOKEN = os.environ.get("SANDBOX_TOKEN", "")
MAX_OUTPUT = 100_000


def _session_dir(raw: str) -> pathlib.Path:
    sid = re.sub(r"[^A-Za-z0-9_-]+", "", str(raw or ""))[:64] or "default"
    p = WORKSPACE / "sessions" / sid
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_path(raw: str, workspace: pathlib.Path) -> pathlib.Path:
    p = (workspace / str(raw).lstrip("/")).resolve()
    if workspace.resolve() not in p.parents and p != workspace.resolve():
        raise ValueError("path escapes workspace")
    return p


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003
        return

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self) -> bool:
        if not TOKEN:
            return True
        return self.headers.get("X-Sandbox-Token", "") == TOKEN

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._auth():
            self._send(403, {"error": "forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            self._send(400, {"error": "invalid json"})
            return

        if self.path == "/execute":
            cmd = str(data.get("command") or "")
            timeout = min(int(data.get("timeout") or 30), 120)
            workspace = _session_dir(data.get("workspace") or "")
            env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp",
                   "PYTHONUNBUFFERED": "1", "LANG": "C.UTF-8"}
            try:
                proc = subprocess.run(["sh", "-lc", cmd], cwd=str(workspace),
                                      capture_output=True, text=True,
                                      timeout=timeout, env=env)
                out = (proc.stdout or "") + (proc.stderr or "")
                self._send(200, {"output": out[:MAX_OUTPUT],
                                 "exit_code": proc.returncode,
                                 "truncated": len(out) > MAX_OUTPUT})
            except subprocess.TimeoutExpired:
                self._send(200, {"output": f"timeout after {timeout}s",
                                 "exit_code": 124, "truncated": False})
            except Exception as exc:  # noqa: BLE001
                self._send(200, {"output": f"{type(exc).__name__}: {exc}",
                                 "exit_code": 1, "truncated": False})
            return

        if self.path == "/upload":
            workspace = _session_dir(data.get("workspace") or "")
            results = []
            for item in data.get("files") or []:
                try:
                    p = _safe_path(item.get("path") or "", workspace)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(base64.b64decode(item.get("content_b64") or ""))
                    results.append({"path": str(item.get("path")), "error": None})
                except Exception as exc:  # noqa: BLE001
                    results.append({"path": str(item.get("path")), "error": str(exc)})
            self._send(200, {"files": results})
            return

        if self.path == "/download":
            workspace = _session_dir(data.get("workspace") or "")
            results = []
            for raw in data.get("paths") or []:
                try:
                    p = _safe_path(raw, workspace)
                    results.append({"path": raw,
                                    "content_b64": base64.b64encode(p.read_bytes()).decode(),
                                    "error": None})
                except Exception as exc:  # noqa: BLE001
                    results.append({"path": raw, "content_b64": None, "error": str(exc)})
            self._send(200, {"files": results})
            return

        self._send(404, {"error": "not found"})


if __name__ == "__main__":
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("SANDBOX_PORT", "9000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
