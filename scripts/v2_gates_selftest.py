"""Self-test for the v2 gates (G0/G0r evidence).

Runs the fixture gold set in `docs/paper/cbc/v2/gates-gold/` against the v2 gate
implementations:
  - `t1_cases.jsonl` -> `app.harvest.t1_v2.check_text` / `check` (tier + family + zone)
  - `t2_cases.jsonl` -> `app.harvest.t2_v2.check_url` with injected mock transport
    (status classes, retries, redirects, soft-404, WAF tiers, params)
Plus a **freeze check**: fixture count + sha256 against `FREEZE.json` (count
sentinel and cross-clone reproducibility; G0r findings R1 / ver-2).

Writes `docs/paper/cbc/v2/checks/gates-selftest.json`.
Exit code 0 iff all cases pass and the freeze check passes.

Usage (works from a plain checkout without PYTHONPATH):
    python scripts/v2_gates_selftest.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # clean-copy usage (G0 finding, L-0352)
    sys.path.insert(0, str(ROOT))

GOLD = ROOT / "docs" / "paper" / "cbc" / "v2" / "gates-gold"
OUT = ROOT / "docs" / "paper" / "cbc" / "v2" / "checks"
AS_OF = "2026-10-16T00:00:00Z"


def _freeze() -> dict:
    return json.loads((GOLD / "FREEZE.json").read_text(encoding="utf-8"))


def freeze_check() -> dict:
    """Count sentinel + sha verification against FREEZE.json."""
    fz = _freeze()
    out = {"ok": True, "files": {}}
    for name, meta in fz.get("files", {}).items():
        p = GOLD / name
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        n = len([l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()])
        ok = (h == meta.get("sha256")) and (n == meta.get("cases"))
        out["files"][name] = {"ok": ok, "sha256_match": h == meta.get("sha256"),
                              "cases": n, "expected_cases": meta.get("cases")}
        out["ok"] = out["ok"] and ok
    return out


def run_t1() -> dict:
    from app.harvest import t1_v2

    cases = [json.loads(l) for l in (GOLD / "t1_cases.jsonl").read_text(encoding="utf-8").splitlines()
             if l.strip()]
    fails = []
    rows = []
    for c in cases:
        if "zones" in c:
            v = t1_v2.check(c["accession"], [(z, t) for z, t in c["zones"]])
            got = {"tier": v["tier"] if v else None,
                   "family": t1_v2.family_of(c["accession"]),
                   "zone": (v or {}).get("zone")}
            ok = (got["tier"] == c["expect_tier"] and got["family"] == c["expect_family"]
                  and got["zone"] == c.get("expect_zone"))
            expect = {"tier": c["expect_tier"], "family": c["expect_family"],
                      "zone": c.get("expect_zone")}
        else:
            v = t1_v2.check_text(c["accession"], c["text"])
            got = {"tier": v["tier"] if v else None,
                   "family": t1_v2.family_of(c["accession"])}
            ok = (got["tier"] == c["expect_tier"] and got["family"] == c["expect_family"])
            expect = {"tier": c["expect_tier"], "family": c["expect_family"]}
        row = {"id": c["id"], "ok": ok, "expect": expect, "got": got,
               "note": c.get("note", "")}
        rows.append(row)
        if not ok:
            fails.append(row)
    return {"total": len(cases), "passed": len(cases) - len(fails), "rows": rows, "fails": fails}


def _mk_fetch(mock: dict):
    from app.harvest.t2_v2 import Resp

    state = {"i": 0}
    seq = mock.get("seq")

    def fetch(url: str, method: str) -> Resp:
        if seq is not None:
            i = min(state["i"], len(seq) - 1)
            state["i"] += 1
            spec = seq[i]
        else:
            spec = mock.get(method.lower())
            if spec is None:
                if any(k in mock for k in ("status", "error", "body", "ct")):
                    spec = mock
                else:
                    spec = mock.get("get") or mock.get("head") or {}
        if spec.get("error"):
            raise ConnectionError(spec["error"])
        return Resp(status=spec.get("status", 200), ct=spec.get("ct", "text/html"),
                    body=spec.get("body", ""), final_url=spec.get("final"),
                    hops=spec.get("hops", 0), method=method)
    return fetch


def run_t2() -> dict:
    from app.harvest import t2_v2

    cases = [json.loads(l) for l in (GOLD / "t2_cases.jsonl").read_text(encoding="utf-8").splitlines()
             if l.strip()]
    fails = []
    rows = []
    for c in cases:
        params = c.get("params", {})
        v = t2_v2.check_url(c["url"], fetch=_mk_fetch(c["mock"]), sleep=lambda _s: None,
                            must_contain=c.get("must_contain"), as_of=AS_OF,
                            sniff=params.get("sniff", True),
                            max_retries=params.get("max_retries", 2))
        ok = (v["status"] == c["expect"]["status"] and v["reason"] == c["expect"]["reason"])
        if v.get("as_of") != AS_OF:
            ok = False
        if c.get("expect_attempts") is not None and v["attempts"] != c["expect_attempts"]:
            ok = False
        for field in ("expect_ct", "expect_hops", "expect_signal"):
            key = field[len("expect_"):]
            if field in c and v.get(key) != c[field]:
                ok = False
        row = {"id": c["id"], "ok": ok,
               "expect": {**c["expect"], "attempts": c.get("expect_attempts"),
                          "ct": c.get("expect_ct"), "hops": c.get("expect_hops"),
                          "signal": c.get("expect_signal")},
               "got": {"status": v["status"], "reason": v["reason"], "attempts": v["attempts"],
                       "ct": v.get("ct"), "hops": v.get("hops"), "signal": v.get("signal")}}
        rows.append(row)
        if not ok:
            fails.append(row)
    return {"total": len(cases), "passed": len(cases) - len(fails), "rows": rows, "fails": fails}


def main() -> None:
    report: dict = {"suite": "v2 gates self-test"}
    fz = freeze_check()
    t1 = run_t1()
    t2 = run_t2()
    count_ok = (t1["total"] == _freeze()["files"]["t1_cases.jsonl"]["cases"]
                and t2["total"] == _freeze()["files"]["t2_cases.jsonl"]["cases"])
    report["freeze"] = {**fz, "counts_ok": count_ok}
    report["t1"] = t1
    report["t2"] = t2
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "gates-selftest.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
    print(f"freeze: {'OK' if fz['ok'] and count_ok else 'FAIL'} "
          f"(counts_ok={count_ok}, sha_ok={fz['ok']})")
    print(f"t1 cases: {t1['passed']}/{t1['total']} passed")
    print(f"t2 cases: {t2['passed']}/{t2['total']} passed")
    for f in (t1["fails"] + t2["fails"])[:25]:
        print("  FAIL", f["id"], f["expect"], "->", f["got"])
    bad = bool(t1["fails"] or t2["fails"]) or not (fz["ok"] and count_ok)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
