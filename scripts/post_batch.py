"""批次收尾例程（A）：归一化 → 策展提案 → 批次报告。

用法: python scripts/post_batch.py [--skip-curate]
在挖矿批次结束后（或快照冻结前）运行；幂等（可重复跑）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_DB = ROOT / "data" / "bioagentdb.sqlite3"
REPORT = ROOT / "docs" / "expansion" / "post-batch-report.md"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run(script: str, *args: str) -> str:
    cmd = [sys.executable, str(ROOT / "scripts" / script), *args]
    print(f"$ {' '.join(cmd[1:])}")
    out = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    tail = (out.stdout or "") + (out.stderr or "")
    print(tail.strip().splitlines()[-2] if tail.strip() else "(no output)")
    return tail


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-curate", action="store_true")
    args = ap.parse_args()

    norm_out = _run("normalize_records.py", "--apply")
    curate_out = "" if args.skip_curate else _run("normalize_curate.py", "--batch")

    c = sqlite3.connect(MAIN_DB)
    c.row_factory = sqlite3.Row
    by_field = [dict(r) for r in c.execute(
        "SELECT field, COUNT(*) n, SUM(CASE WHEN canonical!='' THEN 1 ELSE 0 END) mapped, "
        "SUM(CASE WHEN quote!='' THEN 1 ELSE 0 END) quoted FROM record_normalization "
        "GROUP BY field")]
    norm_versions = [dict(r) for r in c.execute(
        "SELECT norm_version, COUNT(*) n FROM record_normalization GROUP BY norm_version")]
    pending = [dict(r) for r in c.execute(
        "SELECT id, field, raw, kind, canonical, valid FROM normalization_proposals "
        "WHERE status='pending' ORDER BY id DESC LIMIT 20")]
    pending_n = c.execute(
        "SELECT COUNT(*) FROM normalization_proposals WHERE status='pending'").fetchone()[0]
    c.close()

    lex = json.loads((ROOT / "data" / "norm" / "lexicon.json").read_text(encoding="utf-8"))

    md = [f"# Post-batch report — {_utcnow()}", "",
          f"- lexicon version: **{lex.get('version')}**",
          f"- normalization rows: " +
          " · ".join(f"{r['field']} {r['n']} (mapped {r['mapped']}, quoted {r['quoted']})"
                     for r in by_field),
          f"- versions: " + " · ".join(f"{r['norm_version']} {r['n']}" for r in norm_versions),
          f"- pending proposals: **{pending_n}**", "",
          "## Pending proposals (latest 20)", "",
          "| id | field | raw | kind | canonical | valid |", "|---|---|---|---|---|---|"]
    for p in pending:
        md.append(f"| {p['id']} | {p['field']} | {p['raw'][:40]} | {p['kind']} "
                  f"| {p['canonical']} | {'ok' if p['valid'] else 'invalid'} |")
    if not pending:
        md.append("| — | — | (none) | — | — | — |")
    md += ["", "## Next steps", "",
           "1. 专审三对抗（按批次）：`docs/reviews/norm-curator-<batch>-{veracity,coverage,metadata}.md`",
           "2. `python scripts/normalize_curate.py --accept-valid` → `--apply`（词表升版 + 幂等重跑）",
           "3. 新 raw 值若被 reject，确认理由后从提案中关闭", "",
           "## Raw outputs (tails)", "", "```",
           norm_out.strip()[-500:], "```", ""]
    if curate_out:
        md += ["```", curate_out.strip()[-500:], "```", ""]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(md), encoding="utf-8")
    print(f"wrote {REPORT} · pending={pending_n}")


if __name__ == "__main__":
    main()
