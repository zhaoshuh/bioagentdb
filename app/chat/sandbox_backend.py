"""DeepAgents BaseSandbox implementation backed by the isolated sandbox sidecar."""
from __future__ import annotations

import base64
import os
import uuid

import httpx

from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)


class RemoteSandbox(BaseSandbox):
    """A BaseSandbox that delegates execution/files to the sandbox sidecar."""

    def __init__(self, url: str, token: str = "", timeout: float = 150.0):
        super().__init__()
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._id = f"remote-{uuid.uuid4().hex[:10]}"

    @property
    def id(self) -> str:
        return self._id

    def _headers(self) -> dict:
        return {"X-Sandbox-Token": self.token} if self.token else {}

    def _session_id(self) -> str:
        try:
            from langgraph.config import get_config

            cfg = get_config()
            return str((cfg or {}).get("configurable", {}).get("thread_id") or "default")
        except Exception:  # noqa: BLE001
            return "default"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                r = client.post(f"{self.url}/execute",
                                json={"command": command, "timeout": int(timeout or 30),
                                      "workspace": self._session_id()},
                                headers=self._headers())
                r.raise_for_status()
                data = r.json()
            return ExecuteResponse(output=str(data.get("output") or ""),
                                   exit_code=data.get("exit_code"),
                                   truncated=bool(data.get("truncated")))
        except Exception as exc:  # noqa: BLE001
            return ExecuteResponse(output=f"sandbox error: {type(exc).__name__}: {exc}",
                                   exit_code=1, truncated=False)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        payload = {"workspace": self._session_id(),
                   "files": [{"path": path,
                              "content_b64": base64.b64encode(data).decode()}
                             for path, data in files]}
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                r = client.post(f"{self.url}/upload", json=payload, headers=self._headers())
                r.raise_for_status()
                return r.json().get("files") or []
        except Exception as exc:  # noqa: BLE001
            return [{"path": p, "error": f"{type(exc).__name__}: {exc}"} for p, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                r = client.post(f"{self.url}/download",
                                json={"paths": paths, "workspace": self._session_id()},
                                headers=self._headers())
                r.raise_for_status()
                out = []
                for item in r.json().get("files") or []:
                    content = item.get("content_b64")
                    out.append({"path": item.get("path"),
                                "content": base64.b64decode(content) if content else None,
                                "error": item.get("error")})
                return out
        except Exception as exc:  # noqa: BLE001
            return [{"path": p, "content": None, "error": f"{type(exc).__name__}: {exc}"}
                    for p in paths]

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=5, trust_env=False) as client:
                return client.get(f"{self.url}/health").status_code == 200
        except Exception:  # noqa: BLE001
            return False
