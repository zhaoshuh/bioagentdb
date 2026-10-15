"""记录字段归一化（论文为锚，norm_v1）：确定性映射 + 依据引文 + raw 保留。

用法:
  python scripts/normalize_records.py            # dry-run（默认）：出报告，不落库
  python scripts/normalize_records.py --apply    # 落库（record_normalization 表）

产物:
  docs/expansion/normalization-report.md / normalization-report.json（dry-run）
  主库 record_normalization 表（apply；raw 原样保留在 datasets）
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LEXICON = ROOT / "data" / "norm" / "lexicon.json"
MAIN_DB = ROOT / "data" / "bioagentdb.sqlite3"
OUT_DIR = ROOT / "docs" / "expansion"
VERSION = "norm_v1"

_SPLIT_RE = re.compile(r"\s*[;,/、]\s*|\s+and\s+", re.I)
_PAREN_RE = re.compile(r"\([^)]*\)")


def _norm(s: str) -> str:
    t = (s or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .;,")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_lexicon() -> dict:
    return json.loads(LEXICON.read_text(encoding="utf-8"))


def build_index(field: dict) -> dict[str, str]:
    """alias → canonical。"""
    idx: dict[str, str] = {}
    for canonical, spec in field.items():
        for a in [canonical] + list(spec.get("aliases") or []):
            idx[_norm(a)] = canonical
    return idx


def protected_phrases(field: dict) -> list[str]:
    return [a for canonical, spec in field.items()
            if spec.get("protect") for a in (spec.get("aliases") or [])]


def split_parts(raw: str, protected: list[str], strip_parens: bool = False) -> list[str]:
    t = raw or ""
    if strip_parens:
        t = _PAREN_RE.sub(" ", t)
    for i, p in enumerate(protected):
        t = re.sub(re.escape(p), f"§P{i}§", t, flags=re.I)
    parts = [p.strip() for p in _SPLIT_RE.split(t) if p.strip()]
    for i, p in enumerate(protected):
        parts = [x.replace(f"§P{i}§", p) for x in parts]
    return parts or ([raw.strip()] if raw.strip() else [])


def _aliases_of(canonical: str, field: dict) -> list[str]:
    spec = field.get(canonical) or {}
    return [canonical] + list(spec.get("aliases") or [])


def match_part(part: str, idx: dict[str, str]) -> tuple[str, str]:
    """返回 (canonical, method)；未匹配 canonical=''。"""
    n = _norm(part)
    if not n:
        return "", "empty"
    if n in idx:
        return idx[n], "alias"
    stripped = _norm(_PAREN_RE.sub(" ", part))
    if stripped and stripped in idx:
        return idx[stripped], "alias"
    # 前缀匹配（长分类串，如 "cryptococcus neoformans var. grubii …"）
    for alias, canonical in idx.items():
        if len(alias) >= 8 and n.startswith(alias):
            return canonical, "prefix"
    # 连字符拆分兜底（"als-ftd" → als + ftd）
    if "-" in n and not n.startswith("-"):
        subs = [s for s in n.split("-") if s]
        hits = [idx.get(s) for s in subs]
        hits = [h for h in hits if h]
        if hits:
            uniq = list(dict.fromkeys(hits))
            return "|".join(uniq), "hyphen-split"
    return "", "unmapped"


_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def quote_for(needle: str, texts: list[tuple[str, str]]) -> str:
    n = (needle or "").strip().lower()
    if not n:
        return ""
    pat = re.compile(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", re.I)
    for _zone, t in texts:
        if not t:
            continue
        for sent in _SENT_RE.split(t):
            if pat.search(sent):
                return sent.strip()[:400]
    # 子串兜底仅用于较长 needle（避免 "human"→"humanized" 之类弱匹配）
    if len(n) >= 6:
        for _zone, t in texts:
            if not t:
                continue
            for sent in _SENT_RE.split(t):
                if n in sent.lower():
                    return sent.strip()[:400]
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    lex = load_lexicon()
    global VERSION
    VERSION = str(lex.get("version") or "norm_v1")
    idx = {f: build_index(lex[f]) for f in ("disease", "organism", "modality")}
    prot = {f: protected_phrases(lex[f]) for f in ("disease", "organism", "modality")}

    c = sqlite3.connect(MAIN_DB)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute("""
        SELECT d.id, d.disease, d.organism, d.modality,
               p.title, p.abstract, p.da_text, p.fulltext,
               (SELECT group_concat(e.quote, ' ||| ') FROM evidence e
                 WHERE e.dataset_id = d.id) AS quotes
        FROM datasets d
        LEFT JOIN dataset_papers dp ON dp.dataset_id = d.id
        LEFT JOIN papers p ON p.id = dp.paper_id
        GROUP BY d.id""")]

    out_rows: list[dict] = []
    unmapped = Counter()
    per_canonical = Counter()
    per_field = Counter()
    multi = 0
    with_quote = 0
    for r in rows:
        _ft = {"done": False, "text": ""}

        def _fulltext() -> str:
            if not _ft["done"]:
                _ft["done"] = True
                if r.get("fulltext"):
                    try:
                        from app.harvest.fulltext import decompress_text
                        _ft["text"] = decompress_text(r["fulltext"])[:300000]
                    except Exception:  # noqa: BLE001
                        _ft["text"] = ""
            return _ft["text"]

        base_texts = [("evidence", r.get("quotes") or ""), ("da", r.get("da_text") or ""),
                      ("title", r.get("title") or ""), ("abstract", r.get("abstract") or "")]
        for field in ("disease", "organism", "modality"):
            raw = (r.get(field) or "").strip()
            if not raw:
                continue
            per_field[field] += 1
            seen: set[str] = set()
            parts = split_parts(raw, prot[field],
                                strip_parens=(field in ("organism", "modality")))
            mapped_parts = 0
            for part in parts:
                canonical, method = match_part(part, idx[field])
                for canon in [x for x in canonical.split("|") if x]:
                    if canon in seen:
                        continue
                    seen.add(canon)
                    mapped_parts += 1
                    needles = [part] + _aliases_of(canon, lex[field])
                    quote = ""
                    for nd in needles:
                        quote = quote_for(nd, base_texts)
                        if quote:
                            break
                    if not quote:
                        for nd in needles:
                            quote = quote_for(nd, [("fulltext", _fulltext())])
                            if quote:
                                break
                    if quote:
                        with_quote += 1
                    per_canonical[canon] += 1
                    out_rows.append({"record_id": r["id"], "field": field, "raw": raw,
                                     "canonical": canon, "part": part, "method": method,
                                     "quote": quote, "version": VERSION})
                if not canonical:
                    unmapped[_norm(part)] += 1
                    out_rows.append({"record_id": r["id"], "field": field, "raw": raw,
                                     "canonical": "", "part": part, "method": "unmapped",
                                     "quote": "", "version": VERSION})
            if mapped_parts >= 2:
                multi += 1

    # 报告
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    unmapped_list = [{"part": k, "count": v} for k, v in unmapped.most_common()]
    report = {
        "version": VERSION, "generated_at": _utcnow(),
        "datasets_total": len(rows),
        "non_empty": {f: per_field[f] for f in ("disease", "organism", "modality")},
        "mapped_rows": sum(1 for r in out_rows if r["canonical"]),
        "unmapped_rows": sum(1 for r in out_rows if not r["canonical"]),
        "multi_value_records": multi,
        "rows_with_quote": with_quote,
        "per_canonical": dict(per_canonical.most_common()),
        "unmapped": unmapped_list,
    }
    (OUT_DIR / "normalization-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# Normalization report (norm_v1, paper-anchored)", "",
          f"- generated: {report['generated_at']} · datasets: {len(rows)}",
          f"- non-empty raw: disease {per_field['disease']} · organism {per_field['organism']}"
          f" · modality {per_field['modality']}",
          f"- mapped rows: {report['mapped_rows']} · unmapped rows: {report['unmapped_rows']}"
          f" · multi-value records: {multi} · rows with quote: {with_quote}",
          "", "## Unmapped parts", "", "| part | count |", "|---|---|"]
    for u in unmapped_list:
        md.append(f"| {u['part']} | {u['count']} |")
    md += ["", "## Canonical distribution (top 30)", "", "| canonical | count |", "|---|---|"]
    for k, v in per_canonical.most_common(30):
        md.append(f"| {k} | {v} |")
    md += ["", "## Sample mappings (first 40)", "",
           "| record | field | raw | canonical | method | quote |", "|---|---|---|---|---|---|"]
    for r in [x for x in out_rows if x["canonical"]][:40]:
        q = (r["quote"] or "")[:80].replace("|", " ")
        md.append(f"| {r['record_id']} | {r['field']} | {r['raw'][:40]} | {r['canonical']} "
                  f"| {r['method']} | {q} |")
    (OUT_DIR / "normalization-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"dry-run: mapped={report['mapped_rows']} unmapped={report['unmapped_rows']} "
          f"multi={multi} quotes={with_quote}")
    print(f"unmapped distinct: {len(unmapped_list)} -> "
          f"{[u['part'] for u in unmapped_list[:12]]}")

    if args.apply:
        c.execute("""
            CREATE TABLE IF NOT EXISTS record_normalization(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              record_id TEXT NOT NULL, field TEXT NOT NULL,
              raw TEXT NOT NULL DEFAULT '', canonical TEXT NOT NULL DEFAULT '',
              part TEXT NOT NULL DEFAULT '', method TEXT NOT NULL DEFAULT '',
              quote TEXT NOT NULL DEFAULT '', norm_version TEXT NOT NULL,
              created_at TEXT NOT NULL)""")
        # 单版本表：应用时清空重写（历史由 data/norm/history/lexicon-*.json + 提案表保留）
        c.execute("DELETE FROM record_normalization")
        now = _utcnow()
        c.executemany("""
            INSERT INTO record_normalization(record_id, field, raw, canonical, part,
                                             method, quote, norm_version, created_at)
            VALUES(:record_id, :field, :raw, :canonical, :part, :method, :quote,
                   :version, :created_at)""",
                      [{**r, "created_at": now} for r in out_rows])
        c.commit()
        print(f"APPLIED: {len(out_rows)} rows into record_normalization ({VERSION})")
    else:
        print("dry-run only (use --apply to write)")
    c.close()


if __name__ == "__main__":
    main()
