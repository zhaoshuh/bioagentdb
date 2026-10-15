"""M2 · 沙盒评测与回归门：对未提交的提议在 dev 上评测、在 test 上验证。

- dev/test 确定性划分（md5）
- 指标：union(管线 + 候选扫描) 对 gold
- 门控：dev 上 F1↑ 且 P ≥ 精度下限；或 P 持平且 R ↑ ≥ 阈值
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from app.harvest.candidates import scan_texts
from app.core.vocab import expand_accession


def _accset(items) -> set:
    s: set = set()
    for a in items:
        s.update(expand_accession(a))
    return s


def split(ids: list[str], dev_frac: float = 0.6) -> tuple[set, set]:
    key = lambda x: hashlib.md5(f"dev:{x}".encode()).hexdigest()  # noqa: E731
    s = sorted(ids, key=key)
    k = int(len(s) * dev_frac)
    return set(s[:k]), set(s[k:])


def load_gold(path: Path) -> dict:
    return {r["paper_id"]: _accset(r.get("accessions", []))
            for r in (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
                      if l.strip())}


def load_preds(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if "pred" in r:
            out[r["paper_id"]] = _accset(r["pred"])
        else:
            accs = [l["accession"] for d in r.get("datasets", []) for l in d.get("links", [])
                    if l.get("accession")]
            out[r["paper_id"]] = _accset(accs)
    return out


def union_metrics(extra_patterns, texts: dict, pipe: dict, gold: dict, ids: set) -> dict:
    tp = fp = fn = 0
    for pid in ids:
        cand = scan_texts([("t", texts.get(pid, ""))], extra_patterns=extra_patterns)
        scan = _accset([c["accession"] for c in cand])
        p = scan | pipe.get(pid, set())
        r = gold.get(pid, set())
        tp += len(p & r)
        fp += len(p - r)
        fn += len(r - p)
    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = 2 * prec * rec / max(1e-9, prec + rec)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 4),
            "recall": round(rec, 4), "f1": round(f1, 4)}


def gate(base: dict, new: dict, *, precision_floor: float = 0.97,
         recall_gain: float = 0.02, precision_tol: float = 0.005) -> tuple[bool, dict]:
    f1_up = new["f1"] > base["f1"]
    p_ok = new["precision"] >= precision_floor
    p_flat = new["precision"] >= base["precision"] - precision_tol
    r_gain = (new["recall"] - base["recall"]) >= recall_gain
    accept = (f1_up and p_ok) or (p_flat and r_gain)
    return accept, {"f1_up": f1_up, "p_ok": p_ok, "p_flat": p_flat, "r_gain": r_gain}


def compile_extra(proposals: list[dict]) -> list[tuple[str, re.Pattern]]:
    out = []
    for p in proposals:
        try:
            out.append((p.get("repository") or "Auto", re.compile(p["pattern"], re.I)))
        except (re.error, KeyError):
            continue
    return out
