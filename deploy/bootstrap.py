#!/usr/bin/env python3
"""BioAgentDB — deploy bootstrap (G-line, 2026-10-15).

Prepares everything a fresh clone needs before `docker compose -f deploy/compose.preview.yml up -d`:

  1. `.env`            — copied from `.env.example` when missing (never overwritten; fill in keys later)
  2. data dirs         — deploy/empty-data, deploy/live-data, deploy/release-data
  3. release snapshot  — if `data/releases/_v15/bioagentdb.sqlite3` is present, it is copied
                         (WAL-safe `VACUUM INTO`) to `deploy/release-data/bioagentdb.sqlite3`
                         and its sha256 is checked against the frozen v1.5 digest.

Usage:
  python deploy/bootstrap.py                  # prepare all tiers (safe/idempotent)
  python deploy/bootstrap.py --tier release   # only the release tier
  python deploy/bootstrap.py --force          # re-copy the release snapshot even if present
  python deploy/bootstrap.py --check          # HTTP health probe of running stacks (exit 1 on failure)

Environment overrides: BIOAGENTDB_RELEASE_SNAPSHOT (default data/releases/_v15/bioagentdb.sqlite3),
BIOAGENTDB_PREVIEW_PORTS (default 8081,8082,8083). Stdlib only; no data is ever modified in place.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"

TIER_DIRS = {
    "empty": DEPLOY / "empty-data",
    "live": DEPLOY / "live-data",
    "release": DEPLOY / "release-data",
}

DEFAULT_RELEASE_SNAPSHOT = ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3"
# Frozen v1.5 release DB digest (must stay stable; see docs/paper-plan-ledger.md).
RELEASE_V15_SHA256 = "9B55F8B8AA11C05C8222155656493A78EB196E0061C01CF64DF08F1E867C299E"


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}")


def ensure_env_file() -> None:
    env = ROOT / ".env"
    example = ROOT / ".env.example"
    if env.exists():
        log(".env exists — kept as is")
        return
    if not example.exists():
        log("WARNING: .env.example not found; create .env yourself (see docs/QUICKSTART.md)")
        return
    env.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    log(".env created from .env.example — fill in DEEPSEEK_API_KEY for chat/mining "
        "(browse-only works without it)")


def ensure_dirs(tier: str) -> None:
    tiers = TIER_DIRS if tier == "all" else {tier: TIER_DIRS[tier]}
    for name, path in tiers.items():
        path.mkdir(parents=True, exist_ok=True)
        log(f"data dir ready ({name}): {path.relative_to(ROOT)}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def copy_sqlite(src: Path, dst: Path, force: bool) -> bool:
    """WAL-safe copy of a live SQLite DB (`VACUUM INTO`); skip when dst exists, unless force."""
    if dst.exists() and not force:
        log(f"release snapshot already present: {dst.relative_to(ROOT)} (use --force to refresh)")
        return False
    tmp = dst.with_name(dst.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("VACUUM INTO ?", (str(tmp),))
    finally:
        conn.close()
    try:
        os.replace(tmp, dst)          # atomic; on Windows this fails while the DB is open
    except OSError:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"[bootstrap] cannot replace {dst.relative_to(ROOT)} — stop the release container "
            f"(docker compose -f deploy/compose.preview.yml stop release) and retry")
    return True


def sqlite_summary(path: Path) -> dict:
    """Read-only integrity + row counts (used to compare source vs loaded snapshot)."""
    out: dict = {}
    conn = sqlite3.connect(str(path))
    try:
        out["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        for table in ("papers", "datasets", "dataset_links", "evidence"):
            try:
                out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                pass
    finally:
        conn.close()
    return out


def prepare_release(force: bool) -> None:
    override = os.environ.get("BIOAGENTDB_RELEASE_SNAPSHOT", "").strip()
    src = Path(override) if override else DEFAULT_RELEASE_SNAPSHOT
    dst = TIER_DIRS["release"] / "bioagentdb.sqlite3"
    if not src.is_file():
        log(f"release tier: snapshot not found at {src}")
        log("  -> get the v1.5 release DB (Zenodo) into data/releases/_v15/, or start the "
            "empty tier and mine your own (see docs/QUICKSTART.md)")
        return
    if src == DEFAULT_RELEASE_SNAPSHOT:
        src_digest = sha256_file(src)
        if src_digest != RELEASE_V15_SHA256:
            log(f"WARNING: source {src.relative_to(ROOT)} sha256 {src_digest} does not match the "
                f"frozen v1.5 digest {RELEASE_V15_SHA256} — proceeding, but verify your copy")
        else:
            log(f"source snapshot verified: {src.relative_to(ROOT)} (frozen v1.5 sha256)")
    copy_sqlite(src, dst, force)
    target = sqlite_summary(dst)
    if target.get("integrity") != "ok":
        log(f"WARNING: loaded snapshot failed integrity_check: {target.get('integrity')!r}")
        return
    if src == DEFAULT_RELEASE_SNAPSHOT:
        source = sqlite_summary(src)
        keys = [k for k in ("papers", "datasets", "dataset_links", "evidence") if k in source]
        same = all(source[k] == target.get(k) for k in keys)
        detail = ", ".join(f"{k}={target.get(k)}" for k in keys)
        if same:
            log(f"release snapshot ready: {dst.relative_to(ROOT)} — integrity ok; {detail}; "
                f"row counts match the source (byte layout may differ: VACUUM INTO rewrite)")
        else:
            log(f"WARNING: loaded snapshot row counts differ from source ({detail}); "
                f"source: " + ", ".join(f"{k}={source[k]}" for k in keys))
    else:
        log(f"release snapshot ready: {dst.relative_to(ROOT)} — integrity ok "
            f"(custom source; no digest/row-count comparison)")
    log("  (the source DB was copied read-only via VACUUM INTO; it is never modified)")


def check(ports: list[int]) -> int:
    bad = 0
    for port in ports:
        url = f"http://127.0.0.1:{port}/api/health"
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                body = r.read(120).decode("utf-8", "replace")
            log(f"port {port}: OK  {body}")
        except (urllib.error.URLError, OSError) as exc:
            bad += 1
            log(f"port {port}: UNREACHABLE ({exc})")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="BioAgentDB deploy bootstrap (preview stacks)")
    ap.add_argument("--tier", choices=["all", "empty", "live", "release"], default="all")
    ap.add_argument("--force", action="store_true", help="re-copy the release snapshot even if present")
    ap.add_argument("--check", action="store_true", help="probe /api/health on the preview ports")
    args = ap.parse_args()

    if args.check:
        ports = [int(p) for p in os.environ.get(
            "BIOAGENTDB_PREVIEW_PORTS", "8081,8082,8083").split(",") if p.strip()]
        return check(ports)

    ensure_env_file()
    ensure_dirs(args.tier)
    if args.tier in ("all", "release"):
        prepare_release(args.force)
    log("next: docker compose -f deploy/compose.preview.yml up -d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
