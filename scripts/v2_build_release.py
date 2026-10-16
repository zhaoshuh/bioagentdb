"""P5 — build the v1.6 release snapshot (_v16) from _v15 + v2 gate telemetry.

Steps
-----
1. `VACUUM INTO` a clean copy: data/releases/_v15 -> data/releases/_v16/bioagentdb.sqlite3
   (single file, no wal/shm; source opened read-only).
2. Apply docs/paper/cbc/v2/t2v2_checks.sql DDL.
3. Import docs/paper/cbc/v2/t2-recheck.jsonl into t2v2_checks.
4. Integrity: every table of _v15 must have identical row counts in _v16
   (t2v2_checks is the only new table); t2v2_checks rows == JSONL lines;
   sha256 of the _v16 file -> docs/paper/cbc/v2/checks/v16-build.json.
5. With --stats: run scripts/resource_stats.py --db <_v16> --out docs/versions/v1.6-stats
   (the same generator used for v1.5 stats).

Usage:
    python scripts/v2_build_release.py [--stats] [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

V15 = ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3"
V16_DIR = ROOT / "data" / "releases" / "_v16"
V16 = V16_DIR / "bioagentdb.sqlite3"
DDL = ROOT / "docs" / "paper" / "cbc" / "v2" / "t2v2_checks.sql"
JSONL = ROOT / "docs" / "paper" / "cbc" / "v2" / "t2-recheck.jsonl"
OUT = ROOT / "docs" / "paper" / "cbc" / "v2" / "checks" / "v16-build.json"


def sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="also regenerate v1.6-stats")
    ap.add_argument("--force", action="store_true", help="overwrite an existing _v16")
    args = ap.parse_args()

    if V16.exists() and not args.force:
        sys.exit(f"_v16 already exists ({V16}); use --force to rebuild")
    V16_DIR.mkdir(parents=True, exist_ok=True)
    if V16.exists():
        V16.unlink()

    # 1. clean copy
    src = sqlite3.connect(f"file:{V15.as_posix()}?mode=ro", uri=True)
    src.execute(f"VACUUM INTO '{V16.as_posix()}'")
    src.close()
    print(f"copied: {V16} ({V16.stat().st_size} bytes)")

    con = sqlite3.connect(V16)
    # 2. DDL
    con.executescript(DDL.read_text(encoding="utf-8"))
    # 3. import JSONL
    n = 0
    with JSONL.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            con.execute(
                """INSERT INTO t2v2_checks
                   (link_id, dataset_id, accession, url, host, checked_at, attempts,
                    http_status, status, reason, signal, hops, content_type,
                    v1_class, v1_status, carried_status, tokens_present)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r.get("link_id"), r.get("dataset_id"), r.get("accession"), r.get("url"),
                 r.get("host"), r.get("as_of"), r.get("attempts"), r.get("http"),
                 r.get("v2_status"), r.get("v2_reason"), r.get("signal"), r.get("hops"),
                 r.get("ct"), r.get("v1_class"), r.get("v1_status"), r.get("carried_status"),
                 json.dumps(r.get("tokens_present"), ensure_ascii=False)
                 if r.get("tokens_present") is not None else None))
            n += 1
    con.commit()
    print(f"imported t2v2_checks rows: {n}")

    # 4. integrity
    t15 = [r[0] for r in src_conn_tables(V15)]
    mismatches = []
    counts15 = _counts(V15)
    counts16 = _counts(V16)
    for t in t15:
        if t == "sqlite_sequence":
            continue  # SQLite housekeeping; row count follows AUTOINCREMENT table count
        if counts15[t] != counts16.get(t):
            mismatches.append({"table": t, "v15": counts15[t], "v16": counts16.get(t)})
    t2rows = counts16.get("t2v2_checks")
    jsonl_lines = sum(1 for l in JSONL.read_text(encoding="utf-8").splitlines() if l.strip())
    con.close()

    report = {
        "as_of": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "v15": {"sha16": sha16(V15)},
        "v16": {"path": str(V16.relative_to(ROOT)), "sha16": sha16(V16),
                "bytes": V16.stat().st_size},
        "t2v2_rows": t2rows,
        "jsonl_lines": jsonl_lines,
        "count_mismatches": mismatches,
        "core_ok": not mismatches,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1)[:900])
    if mismatches or t2rows != jsonl_lines:
        sys.exit("INTEGRITY FAILED")

    # 5. stats
    if args.stats:
        out_dir = ROOT / "docs" / "versions" / "v1.6-stats"
        cmd = [sys.executable, str(ROOT / "scripts" / "resource_stats.py"),
               "--db", str(V16), "--out", str(out_dir)]
        print("running:", " ".join(cmd))
        subprocess.run(cmd, check=True)


def src_conn_tables(db: Path):
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    rows = list(con.execute("select name from sqlite_master where type='table' order by name"))
    con.close()
    return rows


def _counts(db: Path) -> dict[str, int]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    out = {}
    for (t,) in con.execute("select name from sqlite_master where type='table'"):
        out[t] = con.execute(f'select count(*) from "{t}"').fetchone()[0]
    con.close()
    return out


if __name__ == "__main__":
    main()
