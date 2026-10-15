"""Supplementary Table S2（record schema）——直接从发布库 PRAGMA 生成，保证与库一致。

生成 S2 区块并就地替换 `docs/paper/final/tables.md` 中 `## Supplementary Table S2` 到
`## Supplementary Table S3` 之间的内容；**幂等**，重跑不再产生差异。

用法: python scripts/make_tables_schema.py [--out docs/paper/final/tables.md]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "paper" / "final" / "tables.md"
import os  # noqa: E402

DB = Path(os.environ.get("BIOAGENTDB_EVAL_DB")
          or ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3")
START = "## Supplementary Table S2"
NEXT = "## Supplementary Table S3"

TABLES = [
    ("papers", "paper-level record: title, venue, identifiers, mining status"),
    ("datasets", "dataset-level record: modality, organism, tissue, disease, sample count, status"),
    ("dataset_links", "canonical links: repository, accession, url, is_primary, reachability (T2)"),
    ("dataset_papers", "dataset ↔ paper association (role)"),
    ("evidence", "the sentence that announces the dataset (zone-tagged)"),
    ("paper_themes", "disease-theme membership per paper"),
    ("record_normalization", "paper-anchored raw–evidence–canonical triples"),
    ("candidates", "every candidate with reviewer verdict"),
    ("paper_audits", "audit trail of fixes/rejections per paper"),
    ("normalization_proposals", "curator-agent proposals for unseen raw values"),
]


def build() -> str:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        lines = [f"{START} · Record schema (generated from the catalog database)", "",
                 "*A core subset of the catalog schema — record, link, evidence and audit-trail tables; operational tables are omitted.*",
                 "", "| table | purpose | columns |", "|---|---|---|"]
        for tb, purpose in TABLES:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({tb})")]
            lines.append(f"| `{tb}` | {purpose} | " + ", ".join(f"`{c}`" for c in cols) + " |")
    finally:
        conn.close()
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--print", dest="to_stdout", action="store_true")
    args = ap.parse_args()
    block = build()
    if args.to_stdout:
        try:
            sys.stdout.reconfigure(encoding="utf-8")   # 默认 GBK 控制台会因 ↔ / – 报错
        except Exception:  # noqa: BLE001
            pass
        print(block)
        return
    out = Path(args.out)
    text = out.read_text(encoding="utf-8")
    i, j = text.find(START), text.find(NEXT)
    if i < 0 or j < 0 or j < i:
        raise SystemExit(f"anchors not found in {out}")
    out.write_text(text[:i] + block + "\n" + text[j:], encoding="utf-8")
    print(f"wrote {out} (S2 block)")


if __name__ == "__main__":
    main()
