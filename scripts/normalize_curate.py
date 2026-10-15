"""归一化策展子 agent（N1–N2）：raw → 证据 → 规范值，提案制 + 校验 + 评测 + 应用。

用法:
  python scripts/normalize_curate.py --batch                 # 扫描词表未覆盖的 raw → 提案入库
  python scripts/normalize_curate.py --eval --holdout 12     # hold-out 评测（隐藏规范词）
  python scripts/normalize_curate.py --accept-valid          # 将校验通过的 pending 标为 accepted
  python scripts/normalize_curate.py --apply                 # 应用 accepted → 词表版本 +1 → 幂等重跑

设计: agent 只提案；确定性校验器先过闸（引文逐字 / 复用词必须存在 / 新词命名规范 / 去重）；
评测与应用都落库留痕（normalization_proposals）。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import normalize_records as nr  # noqa: E402

MAIN_DB = ROOT / "data" / "bioagentdb.sqlite3"
LEXICON = ROOT / "data" / "norm" / "lexicon.json"
HISTORY = ROOT / "data" / "norm" / "history"
OUT_DIR = ROOT / "docs" / "expansion"

THEMES = ["alzheimers-disease", "als", "parkinsons-disease", "frontotemporal-dementia",
          "huntingtons-disease", "prion-disease", "progressive-supranuclear-palsy",
          "dementia-with-lewy-bodies", "multiple-system-atrophy", "spinocerebellar-ataxia",
          "breast-cancer"]

CURATOR_PROMPT = (
    "You are a normalization curator for a biomedical dataset catalog. Given a field "
    "(disease | organism | modality), a raw value found in a paper, the paper text, and the "
    "current controlled vocabulary, return JSON ONLY: "
    '{"kind": "reuse"|"new_term"|"reject", "canonical": str, "quote": str, '
    '"rationale": str, "themes": [str]}. '
    "Rules: reuse = an existing canonical fits (canonical must be EXACTLY one of the given "
    "canonicals); new_term = none fits and the value is a genuine disease/organism/modality "
    "(clean canonical name, sentence case, no annotations/strain numbers/parentheses); "
    "reject = noise (strain codes, annotations, non-biological). quote must be copied "
    "VERBATIM from the provided paper text. Never invent values. themes: catalog slugs when "
    "applicable: " + ", ".join(THEMES) + "."
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(MAIN_DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS normalization_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, field TEXT NOT NULL, raw TEXT NOT NULL,
        part TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL, canonical TEXT NOT NULL DEFAULT '',
        quote TEXT NOT NULL DEFAULT '', rationale TEXT NOT NULL DEFAULT '',
        themes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
        valid INTEGER NOT NULL DEFAULT 0, validation TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'catalog', eval_tag TEXT NOT NULL DEFAULT '',
        norm_version TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        decided_at TEXT NOT NULL DEFAULT '')""")
    return c


# ---------------------------------------------------------------- texts

def texts_for_raw(c: sqlite3.Connection, field: str, raw: str) -> list[tuple[str, str]]:
    row = c.execute(f"""
        SELECT p.title, p.abstract, p.da_text, p.fulltext
        FROM datasets d
        LEFT JOIN dataset_papers dp ON dp.dataset_id = d.id
        LEFT JOIN papers p ON p.id = dp.paper_id
        WHERE d.{field} = ? LIMIT 1""", (raw,)).fetchone()
    if row is None:
        return []
    texts = [("title", row["title"] or ""), ("abstract", row["abstract"] or ""),
             ("da", row["da_text"] or "")]
    ft = ""
    if row["fulltext"]:
        try:
            from app.harvest.fulltext import decompress_text
            ft = decompress_text(row["fulltext"])
        except Exception:  # noqa: BLE001
            ft = ""
    texts.append(("fulltext", ft))
    return texts


# ---------------------------------------------------------------- agent

def propose(field: str, raw: str, texts: list[tuple[str, str]],
            lexicon_reduced: dict) -> dict:
    canonicals = sorted(lexicon_reduced.keys())
    paper_text = "\n".join(t for _z, t in texts if t)[:5000]
    try:
        from app.harvest.llm import available as llm_available, chat_json
        if not llm_available():
            return {"kind": "reject", "canonical": "", "quote": "",
                    "rationale": "LLM unavailable", "themes": []}
        data = chat_json([
            {"role": "system", "content": CURATOR_PROMPT},
            {"role": "user", "content": json.dumps(
                {"field": field, "raw": raw, "canonicals": canonicals,
                 "paper_text": paper_text}, ensure_ascii=False)},
        ], model=None, timeout=60)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        return {"kind": "reject", "canonical": "", "quote": "",
                "rationale": f"LLM error: {exc}"[:120], "themes": []}


def validate(field: str, prop: dict, texts: list[tuple[str, str]],
             lexicon: dict) -> tuple[bool, str]:
    errors: list[str] = []
    kind = str(prop.get("kind") or "")
    canon = str(prop.get("canonical") or "").strip()
    quote = str(prop.get("quote") or "").strip()
    if kind not in ("reuse", "new_term", "reject"):
        errors.append("bad kind")
    if kind == "reuse" and canon not in lexicon:
        errors.append("reuse canonical not in lexicon")
    if kind == "new_term":
        if not re.fullmatch(r"[A-Z][A-Za-z0-9'\- ]{2,59}", canon):
            errors.append("canonical naming")
        existing = {c.lower() for c in lexicon}
        if canon.lower() in existing:
            errors.append("duplicate canonical")
    if kind in ("reuse", "new_term"):
        hay = re.sub(r"\s+", " ", " ".join(t for _z, t in texts if t))
        if not quote or re.sub(r"\s+", " ", quote) not in hay:
            errors.append("quote not verbatim")
    if kind == "reject" and not str(prop.get("rationale") or "").strip():
        errors.append("reject needs rationale")
    return (not errors), "; ".join(errors)


# ---------------------------------------------------------------- modes

def uncovered(c: sqlite3.Connection, lex: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for field in ("disease", "organism", "modality"):
        idx = nr.build_index(lex[field])
        prot = nr.protected_phrases(lex[field])
        seen: set[str] = set()
        for r in c.execute(f"SELECT DISTINCT {field} AS v FROM datasets WHERE {field} != ''"):
            for part in nr.split_parts(r["v"], prot,
                                       strip_parens=(field in ("organism", "modality"))):
                canon, _m = nr.match_part(part, idx)
                if not canon and nr._norm(part) not in seen:
                    seen.add(nr._norm(part))
                    out.append((field, part))
    return out


def run_batch(c: sqlite3.Connection, lex: dict) -> int:
    items = uncovered(c, lex)
    print(f"uncovered raw parts: {len(items)}")
    saved = 0
    for field, part in items[:50]:
        texts = texts_for_raw(c, field, part)
        prop = propose(field, part, texts, lex[field])
        ok, msg = validate(field, prop, texts, lex[field])
        c.execute("""INSERT INTO normalization_proposals(field, raw, part, kind, canonical,
            quote, rationale, themes, status, valid, validation, source, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (field, part, part, str(prop.get("kind") or ""),
                   str(prop.get("canonical") or ""), str(prop.get("quote") or ""),
                   str(prop.get("rationale") or ""),
                   json.dumps(prop.get("themes") or [], ensure_ascii=False),
                   "pending", 1 if ok else 0, msg, "catalog", _utcnow()))
        saved += 1
        print(f"  [{field}] {part!r} -> {prop.get('kind')} {prop.get('canonical')!r} "
              f"valid={ok} {msg}")
    c.commit()
    return saved


def _same(a: str, b: str) -> bool:
    a2, b2 = a.strip().lower(), b.strip().lower()
    if a2 == b2:
        return True
    return a2.rstrip("s") == b2.rstrip("s")


def run_eval(c: sqlite3.Connection, lex: dict, holdout: int) -> dict:
    c.execute("DELETE FROM normalization_proposals WHERE status='eval'")
    rng = random.Random(20261010)
    picks: list[tuple[str, str]] = []
    per_field = {"disease": max(1, holdout // 2), "organism": max(1, holdout // 4),
                 "modality": max(1, holdout // 4)}
    for field, n in per_field.items():
        cands = [r["canonical"] for r in c.execute(
            "SELECT DISTINCT canonical FROM record_normalization WHERE field=? AND canonical != '' "
            "ORDER BY canonical", (field,))]
        cands = [x for x in cands if x in lex[field]]
        picks.extend((field, x) for x in rng.sample(cands, min(n, len(cands))))
    results = []
    hits = 0
    hits_lenient = 0
    for field, canonical in picks:
        raw_row = c.execute(
            "SELECT COALESCE(NULLIF(part,''), raw) AS p, COUNT(*) n "
            "FROM record_normalization WHERE field=? AND canonical=? "
            "GROUP BY p ORDER BY n DESC LIMIT 1", (field, canonical)).fetchone()
        raw = raw_row["p"] if raw_row else canonical
        texts = texts_for_raw(c, field, raw)
        reduced = {k: v for k, v in lex[field].items() if k != canonical}
        prop = propose(field, raw, texts, reduced)
        ok, msg = validate(field, prop, texts, reduced)
        got = str(prop.get("canonical") or "").strip()
        strict = got == canonical
        hit = (prop.get("kind") == "reuse" and strict) or \
              (prop.get("kind") == "new_term" and _same(got, canonical))
        hits += int(strict)
        hits_lenient += int(hit)
        results.append({"field": field, "hidden": canonical, "raw": raw,
                        "kind": prop.get("kind"), "canonical": got,
                        "hit": bool(hit), "strict": bool(strict),
                        "valid": ok, "validation": msg,
                        "quote": str(prop.get("quote") or "")[:160]})
        c.execute("""INSERT INTO normalization_proposals(field, raw, part, kind, canonical,
            quote, rationale, themes, status, valid, validation, source, eval_tag, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (field, raw, raw, str(prop.get("kind") or ""), got,
                   str(prop.get("quote") or ""), str(prop.get("rationale") or ""),
                   json.dumps(prop.get("themes") or [], ensure_ascii=False),
                   "eval", 1 if ok else 0, msg, "catalog", f"holdout:{canonical}",
                   _utcnow()))
    c.commit()
    report = {"holdout": holdout, "picked": len(picks), "hits": hits,
              "accuracy": round(hits / max(1, len(picks)), 3),
              "hits_lenient": hits_lenient,
              "accuracy_lenient": round(hits_lenient / max(1, len(picks)), 3),
              "results": results}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "norm-curator-eval.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# Normalization curator — hold-out evaluation", "",
          f"- picked: {len(picks)} · strict hits: {hits} ({report['accuracy']}) · "
          f"lenient hits: {hits_lenient} ({report['accuracy_lenient']})", "",
          "| field | hidden canonical | raw | agent kind | agent canonical | strict | lenient | valid |",
          "|---|---|---|---|---|---|---|---|"]
    for r in results:
        md.append(f"| {r['field']} | {r['hidden']} | {r['raw'][:36]} | {r['kind']} "
                  f"| {r['canonical']} | {'Y' if r['strict'] else 'N'} "
                  f"| {'Y' if r['hit'] else 'N'} "
                  f"| {'ok' if r['valid'] else r['validation']} |")
    (OUT_DIR / "norm-curator-eval.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return report


def accept_valid(c: sqlite3.Connection) -> int:
    cur = c.execute("UPDATE normalization_proposals SET status='accepted', decided_at=? "
                    "WHERE status='pending' AND valid=1 AND eval_tag=''", (_utcnow(),))
    c.commit()
    return cur.rowcount


def apply_accepted(c: sqlite3.Connection, lex: dict) -> int:
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM normalization_proposals WHERE status='accepted' AND eval_tag=''")]
    if not rows:
        print("no accepted proposals")
        return 0
    merged = 0
    for r in rows:
        if r["kind"] == "reject":
            c.execute("UPDATE normalization_proposals SET status='rejected', decided_at=? "
                      "WHERE id=?", (_utcnow(), r["id"]))
            continue
        field, canon, raw = r["field"], r["canonical"], r["raw"]
        spec = lex[field].setdefault(canon, {"aliases": []})
        aliases = spec.setdefault("aliases", [])
        if raw.lower() not in [str(a).lower() for a in aliases] and raw.lower() != canon.lower():
            aliases.append(raw)
        themes = json.loads(r.get("themes") or "[]")
        if themes:
            spec["themes"] = sorted(set((spec.get("themes") or []) + themes))
        merged += 1
    old_version = lex.get("version", "norm_v1")
    m = re.match(r"norm_v(\d+)", old_version)
    new_version = f"norm_v{(int(m.group(1)) + 1) if m else 2}"
    HISTORY.mkdir(parents=True, exist_ok=True)
    (HISTORY / f"lexicon-{old_version}.json").write_text(
        json.dumps(lex, ensure_ascii=False, indent=2), encoding="utf-8")
    lex["version"] = new_version
    lex["updated"] = _utcnow()
    LEXICON.write_text(json.dumps(lex, ensure_ascii=False, indent=2), encoding="utf-8")
    c.execute("UPDATE normalization_proposals SET status='applied', norm_version=?, decided_at=? "
              "WHERE status='accepted' AND eval_tag='' AND kind != 'reject'",
              (new_version, _utcnow()))
    c.commit()
    print(f"lexicon {old_version} -> {new_version}; merged {merged} proposal(s)")
    py = sys.executable
    subprocess.run([py, str(ROOT / "scripts" / "normalize_records.py"), "--apply"],
                   cwd=str(ROOT), check=False)
    return merged


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--holdout", type=int, default=12)
    ap.add_argument("--accept-valid", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    lex = nr.load_lexicon()
    c = _db()
    if args.batch:
        n = run_batch(c, lex)
        print(f"saved {n} proposal(s)")
    if args.eval:
        rep = run_eval(c, lex, args.holdout)
        print(f"eval: picked={rep['picked']} hits={rep['hits']} accuracy={rep['accuracy']}")
    if args.accept_valid:
        n = accept_valid(c)
        print(f"accepted {n} valid proposal(s)")
    if args.apply:
        n = apply_accepted(c, lex)
        print(f"applied {n}")
    if not any((args.batch, args.eval, args.accept_valid, args.apply)):
        ap.print_help()
    c.close()


if __name__ == "__main__":
    main()
