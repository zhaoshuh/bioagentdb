"""T2 v2 — link reachability with three-valued verdicts, soft-404 / WAF
detection and full signal recording (PLAN v2.2, D3/D14/D15).

Philosophy unchanged: link status is **recorded, not blocking**. What changes vs
the legacy `app.core.utils.http_status` is *classification precision*:

- status in {reachable, unknown, not_found} + `reason` + signals recorded:
  ok / soft-404 / blocked-waf / redirect-suspect / blocked-login / blocked-auth /
  blocked-forbidden / blocked-method / rate-limited / server-error /
  network-error / redirect-unresolved / not-found / http-1xx / http-4xx
- content-type is **recorded** (`ct`) and governs sniffing only (HTML or missing
  ct enters the body sniff; JSON/PDF etc. are legitimate link targets and are
  not downgraded — G0 revision, L-0352)
- soft-404 sniffing over the first 64 KB (patterns + boundary-aware
  `must_contain` token presence; **all** supplied tokens must appear)
- WAF / anti-bot challenge pages (200) -> `unknown` / `blocked-waf`.
  Two-tier matching (G0r hardening, L-0354): a **strong** signature set
  (`just a moment`, `checking your browser`, `cf-browser-verification`, `cf_chl`,
  `verify you are human`) is searched over the whole sniff window; a **weak**
  set (`attention required`, `access denied`, `captcha`, `enable javascript`,
  `are you a robot`) only counts when it appears inside `<title>` or `<h1>`,
  so prose mentioning these words does not trigger a false positive.
- redirect inspection (final URL / hop count; homepage, /home[/], /index[.htm(l)],
  /search, /login redirected links are `unknown`)
- HEAD -> GET fallback (401/403/405/406/501); bounded retries for 429/5xx with
  injectable sleep; one GET ("sniff pass") for 2xx HTML responses, whose status
  is adopted when it arrived (HEAD-200 + GET-404 becomes `not_found`)
- raw 3xx that were not followed -> `unknown` / `redirect-unresolved`
- request ceiling per URL: **5** (HEAD + GET fallback + <=2 retries + sniff GET);
  per-request timeout 8 s (default transport)

The transport is injectable (`fetch`) so the mock fixtures exercise every class
offline and deterministically; the default transport uses httpx.

API:
    check_url(url, *, fetch=None, sniff=True, max_retries=2, sleep=time.sleep,
              must_contain=None, as_of=None) -> dict
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

_SNIFF_WINDOW = 65536

_SOFT404 = re.compile(
    r"(?:page|record|dataset|file|study|resource)\s+not\s+found"
    r"|not\s+found(?:\.|,|!|$|</)"
    r"|could\s+not\s+be\s+found"
    r"|has\s+been\s+removed"
    r"|no\s+longer\s+available"
    r"|page\s+unavailable"
    r"|does\s+not\s+exist",
    re.I,
)

_WAF_STRONG = re.compile(
    r"just\s+a\s+moment"
    r"|checking\s+your\s+browser"
    r"|cf-browser-verification"
    r"|cf_chl"
    r"|verify\s+you\s+are\s+human",
    re.I,
)

_WAF_WEAK = re.compile(
    r"attention\s+required"
    r"|access\s+denied"
    r"|captcha"
    r"|enable\s+javascript"
    r"|are\s+you\s+a\s+robot",
    re.I,
)

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)

_RETRY_GET = {401, 403, 405, 406, 501}
_RETRY_STATUS = {429, 500, 502, 503, 504}
_SUSPECT_PATHS = {"/", "/home", "/index", "/index.htm", "/index.html", "/search"}


@dataclass
class Resp:
    """Minimal response record shared by the default transport and mocks."""

    status: int = 200
    ct: str = "text/html"
    body: str = ""
    final_url: str | None = None
    hops: int = 0
    method: str = "HEAD"
    error: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_UA = "BioAgentDB/1.6 (https://github.com/zhaoshuh/bioagentdb; mailto:zshbdfdc@163.com)"


def _default_fetch(url: str, method: str) -> Resp:
    import httpx  # lazy: only needed for live checks

    with httpx.Client(follow_redirects=True, timeout=8.0,
                      headers={"User-Agent": _UA}) as client:
        r = client.request(method, url)
        body = r.text if method == "GET" else ""
    return Resp(status=r.status_code, ct=r.headers.get("content-type", ""), body=body,
                final_url=str(r.url), hops=len(r.history), method=method)


def _redirect_suspect(final: str | None, orig: str) -> bool:
    if not final:
        return False
    fp = urlsplit(final)
    op = urlsplit(orig)
    fpath = (fp.path or "/").lower().rstrip("/") or "/"
    opath = (op.path or "/").lower().rstrip("/") or "/"
    if fpath == opath:
        return False
    if fpath in _SUSPECT_PATHS:
        return True
    if "/search" in (fp.path or "").lower() and "/search" not in (op.path or "").lower():
        return True
    return False


def _contains_token(hay: str, token: str) -> bool:
    """Boundary-aware presence check (``GSE12345`` must not match inside
    ``GSE123456``)."""
    h = hay.casefold()
    t = token.casefold()
    if not t:
        return True
    pos = h.find(t)
    while pos >= 0:
        end = pos + len(t)
        left = h[pos - 1] if pos > 0 else ""
        right = h[end] if end < len(h) else ""
        if not (left.isalnum() or right.isalnum()):
            return True
        pos = h.find(t, pos + 1)
    return False


def _classify(url: str, r: Resp, sniff: bool, must_contain: list[str] | None,
              report_tokens: list[str] | None = None) -> dict:
    fin = r.final_url or url
    ct_l = (r.ct or "").lower()
    base = {"http": r.status if r.status else None, "final_url": fin, "hops": r.hops,
            "ct": ct_l.split(";")[0].strip() or None}
    st = r.status
    if st == 0:
        return {**base, "ct": None, "status": "unknown", "reason": "network-error",
                "signal": r.error[:120]}
    if st in (404, 410):
        return {**base, "status": "not_found", "reason": "not-found"}
    if st == 401:
        return {**base, "status": "unknown", "reason": "blocked-auth"}
    if st == 403:
        return {**base, "status": "unknown", "reason": "blocked-forbidden"}
    if st in (405, 406, 501):
        return {**base, "status": "unknown", "reason": "blocked-method"}
    if st == 429:
        return {**base, "status": "unknown", "reason": "rate-limited"}
    if st >= 500:
        return {**base, "status": "unknown", "reason": "server-error"}
    if 300 <= st < 400:
        return {**base, "status": "unknown", "reason": "redirect-unresolved"}
    if st < 200 or st >= 400:
        return {**base, "status": "unknown", "reason": f"http-{st}"}
    low_final = fin.lower()
    if "login" in low_final or "signin" in low_final or "sign-in" in low_final:
        return {**base, "status": "unknown", "reason": "blocked-login"}
    if _redirect_suspect(fin, url):
        return {**base, "status": "unknown", "reason": "redirect-suspect"}
    if sniff and r.body and ((not ct_l) or "html" in ct_l):
        window = r.body[:_SNIFF_WINDOW]
        if _WAF_STRONG.search(window):
            return {**base, "status": "unknown", "reason": "blocked-waf", "signal": "waf-strong"}
        head_text = " ".join(m.group(1) for m in _TITLE.finditer(window)) + " " \
                    + " ".join(m.group(1) for m in _H1.finditer(window))
        if head_text.strip() and _WAF_WEAK.search(head_text):
            return {**base, "status": "unknown", "reason": "blocked-waf", "signal": "waf-title"}
        if _SOFT404.search(window):
            return {**base, "status": "unknown", "reason": "soft-404", "signal": "pattern"}
        if must_contain:
            if not all(_contains_token(r.body, tok) for tok in must_contain):
                return {**base, "status": "unknown", "reason": "soft-404", "signal": "token-absent"}
    out = {**base, "status": "reachable", "reason": "ok"}
    if report_tokens and r.body:
        out["tokens_present"] = {t: _contains_token(r.body, t) for t in report_tokens}
    return out


def check_url(url: str, *, fetch=None, sniff: bool = True, max_retries: int = 2,
              sleep=time.sleep, must_contain: list[str] | None = None,
              report_tokens: list[str] | None = None, as_of: str | None = None) -> dict:
    """Deterministic three-valued reachability verdict for one URL."""
    fetch = fetch or _default_fetch
    attempts = 0

    def do(method: str) -> Resp:
        nonlocal attempts
        attempts += 1
        try:
            return fetch(url, method)
        except Exception as exc:  # noqa: BLE001 — transport-agnostic by design
            return Resp(status=0, error=str(exc), method=method)

    r = do("HEAD")
    if r.status == 0 or r.status in _RETRY_GET:
        prev = r
        r = do("GET")
        if r.status == 0 and prev.status != 0:
            r = prev  # keep the informative HEAD verdict over a failed GET
    n_retry = 0
    while r.status in _RETRY_STATUS and n_retry < max_retries:
        sleep(2 * (n_retry + 1))
        prev = r
        r = do("GET")
        if r.status == 0 and prev.status != 0:
            r = prev
        n_retry += 1
    # sniff pass: HEAD bodies are empty, so a 2xx HTML needs one GET; adopt the
    # GET response whenever it arrived (a differing status is more informative:
    # HEAD-200 + GET-404 must classify as not_found -- G0 finding, L-0352).
    if sniff and 200 <= r.status < 300 and not r.body and "html" in (r.ct or "text/html").lower():
        g = do("GET")
        if g.status != 0:
            r = g
    verdict = _classify(url, r, sniff, must_contain, report_tokens)
    verdict.update({"url": url, "attempts": attempts, "as_of": as_of or _now()})
    return verdict
