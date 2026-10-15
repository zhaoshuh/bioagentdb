"""M1 · 错误挖掘：对 gold 算指标、分类 FP/FN、抽取"疑似未识别 token 簇"。

输出 error_report.json（供 propose 使用）。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from app.core.vocab import canon_accession, expand_accession, iter_accession_patterns

# 像登记号的 token：字母(≤8) + 数字(3..10)，或纯数字(4..6)；排除年份
_SHAPE = re.compile(r"\b([A-Za-z]{0,8}\d{3,10})\b")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_KEY = re.compile(
    r"deposit|availab|accession|repository|archiv|dataset|supplement|publicly|"
    r"under (?:the )?(?:id|identifier|accession)|brows|download|portal|study|data\b", re.I)


def _accset(items) -> set:
    s: set = set()
    for a in items:
        s.update(expand_accession(a))
    return s


def metrics(preds: dict, gold: dict) -> dict:
    tp = fp = fn = 0
    fp_list, fn_list = [], []
    for pid in set(preds) | set(gold):
        p, r = preds.get(pid, set()), gold.get(pid, set())
        tp += len(p & r)
        fp += len(p - r)
        fn += len(r - p)
        for a in sorted(p - r):
            fp_list.append({"paper_id": pid, "accession": a})
        for a in sorted(r - p):
            fn_list.append({"paper_id": pid, "accession": a})
    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = 2 * prec * rec / max(1e-9, prec + rec)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 4),
            "recall": round(rec, 4), "f1": round(f1, 4),
            "fp_list": fp_list, "fn_list": fn_list}


def unmatched_tokens(texts: dict[str, str]) -> list[dict]:
    """找"像登记号、在数据语境、但不被任何已知模式匹配"的 token 簇。"""
    known = [p for _, p in iter_accession_patterns()]
    known_hits: set[str] = set()
    shapes: Counter = Counter()
    examples: dict[str, list[str]] = {}
    for pid, text in texts.items():
        if not text:
            continue
        for m in _SHAPE.finditer(text):
            tok = m.group(1)
            if _YEAR.match(tok):
                continue
            if any(p.fullmatch(tok) for p in known):
                known_hits.add(tok.lower())
                continue
            # 数据语境
            i = m.start()
            sent = text[max(0, text.rfind(".", 0, i)):i + 60]
            if not _KEY.search(sent):
                continue
            prefix = re.match(r"^[A-Za-z]*", tok).group(0)
            ndig = len(re.sub(r"\D", "", tok))
            key = f"{prefix.upper() or '_'}:{ndig}"
            shapes[key] += 1
            examples.setdefault(key, [])
            if len(examples[key]) < 6:
                examples[key].append(tok)
    out = []
    for key, n in shapes.most_common():
        if n >= 2:
            prefix, ndig = key.split(":")
            out.append({"shape": key, "prefix": prefix, "digits": int(ndig),
                        "count": n, "examples": examples[key]})
    return out


def report(gold_path: Path, preds_path: Path, sample_texts: dict[str, str]) -> dict:
    gold = {r["paper_id"]: _accset(r.get("accessions", []))
            for r in (json.loads(l) for l in gold_path.read_text(encoding="utf-8").splitlines()
                      if l.strip())}
    preds = _load_preds(preds_path)
    rep = {"metrics": {k: v for k, v in metrics(preds, gold).items()
                       if k not in ("fp_list", "fn_list")},
           "fp": metrics(preds, gold)["fp_list"],
           "fn": metrics(preds, gold)["fn_list"],
           "unmatched_token_clusters": unmatched_tokens(sample_texts)}
    return rep


def _load_preds(path: Path) -> dict:
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
