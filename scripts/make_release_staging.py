"""Build the release staging tree OUTSIDE the repo (default D:/BioAgentDB-release/).

Per docs/paper/cbc/DATA-PACKAGING.md (ledger L-0338). Read-only w.r.t. the
repository and the frozen release database (schema dump via sqlite uri=ro).

Outputs:
  github/                        net export for the public repository (single-commit upload)
  zenodo/record1-code/           bioagentdb-v1.6.0.tar.gz (tar of github/)
  zenodo/record2-release/        v1.6 sqlite snapshot + v0.9-v1.6 stats + schema + README + SHA256SUMS
  zenodo/record3-evaluation/     gold + eval materials (*.log excluded) + SHA256SUMS
  zenodo/record4-paper-sources/  manuscript sources + figures + evidence + SHA256SUMS
  SANITIZE-REPORT.md             hard/soft secret scan (hard target 0) + exclusion notes
  UPLOAD-GUIDE.md                step-by-step + ready-to-paste record metadata
  SHA256SUMS.txt                 checksums over the whole staging tree

Usage: python scripts/make_release_staging.py [--out D:/BioAgentDB-release]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sqlite3
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V15 = ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3"  # legacy ref (not shipped)
V16 = ROOT / "data" / "releases" / "_v16" / "bioagentdb.sqlite3"
VERSION = "1.6"
VERSION_FULL = "1.6.0"
DATE = "2026-10-16"

ROOT_FILES = ["README.md", "requirements.txt", "environment.yml", "Dockerfile",
              "docker-compose.yml", "start.sh", "start.ps1", ".env.example",
              ".gitignore", ".dockerignore"]
DOCS_FILES = ["QUICKSTART.md", "SELF-HOST.md", "CONFIG.md", "NOACC-REVIEW.md"]
TREES = ["app", "web", "learned", "config"]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", "*.log")

LICENSE_MIT = """MIT License

Copyright (c) 2026 zhaoshuh and BioAgentDB contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

CITATION_CFF = """cff-version: 1.2.0
message: "If you use this software or the released catalog, please cite the article and the archived release."
title: "BioAgentDB: an adversarial, machine-verified pipeline for literature-mined catalogs of announced multi-omics datasets"
type: software
license: MIT
repository-code: "https://github.com/zhaoshuh/bioagentdb"
keywords:
  - bioinformatics
  - literature mining
  - data availability
  - multi-omics datasets
  - large language models
authors:
  - name: "BioAgentDB project (maintainer: GitHub @zhaoshuh)"
version: "1.6.0"
date-released: "2026-10-16"
"""

HARD_PATTERNS = [
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI-style key"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"ghp_[A-Za-z0-9]{36}", "GitHub token"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
]
SOFT_PATTERNS = [
    (r"(?i)(api[_-]?key|apikey)\s*=\s*['\"]?[A-Za-z0-9\-_]{16,}", "possible api key assignment"),
    (r"(?i)(password|passwd|pwd)\s*=\s*['\"]?[^\s'\"]{8,}", "possible password assignment"),
    (r"(?i)(secret|token)\s*=\s*['\"]?[A-Za-z0-9\-_\.]{20,}", "possible secret/token assignment"),
    (r"(?i)bearer\s+[A-Za-z0-9._\-]{24,}", "bearer token"),
]
TEXT_EXT = {".py", ".md", ".txt", ".json", ".jsonl", ".yml", ".yaml", ".toml",
            ".sh", ".ps1", ".cff", ".example", ".html", ".js", ".css", ".csv", ".sql"}


def sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, ignore=IGNORE, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def need(p: Path) -> None:
    if not p.exists():
        sys.exit(f"required source missing: {p}")


def write_checksums(base: Path, name: str = "SHA256SUMS.txt") -> Path:
    rows = []
    for f in sorted(base.rglob("*")):
        if f.is_file() and f != (base / name):
            rows.append(f"{sha256(f)}  {f.relative_to(base).as_posix()}")
    out = base / name
    out.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return out


def scan_secrets(base: Path) -> tuple[list[str], list[str]]:
    hard, soft = [], []
    for f in sorted(base.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in TEXT_EXT:
            continue
        if f.name == "SANITIZE-REPORT.md":
            continue  # never scan the report itself (self-referential hits)
        if f.stat().st_size > 5_000_000:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat, label in HARD_PATTERNS:
            for m in re.finditer(pat, text):
                hard.append(f"{f.relative_to(base).as_posix()}: [{label}] {m.group(0)[:40]}")
        for pat, label in SOFT_PATTERNS:
            for m in re.finditer(pat, text):
                soft.append(f"{f.relative_to(base).as_posix()}: [{label}] {m.group(0)[:60]}")
    return hard, soft


def build_github(out: Path) -> Path:
    gh = out / "github"
    if gh.exists():
        shutil.rmtree(gh)
    for name in ROOT_FILES:
        src = ROOT / name
        need(src) if name in ("README.md", "requirements.txt", "environment.yml",
                              "Dockerfile", "docker-compose.yml", "start.sh",
                              "start.ps1", ".env.example", ".gitignore") else None
        if src.exists():
            copy(src, gh / name)
    for tree in TREES:
        src = ROOT / tree
        if src.exists():
            copy(src, gh / tree)
    dep = gh / "deploy"
    dep.mkdir(parents=True, exist_ok=True)
    for name in ("compose.preview.yml", "README.md", "bootstrap.py"):
        copy(ROOT / "deploy" / name, dep / name)
    n_scripts = 0
    for f in sorted((ROOT / "scripts").glob("*.py")):
        if f.name.startswith("_") and f.name not in {"_content_audit.py"}:
            continue
        copy(f, gh / "scripts" / f.name)
        n_scripts += 1
    (gh / "docs").mkdir(parents=True, exist_ok=True)
    for name in DOCS_FILES:
        src = ROOT / "docs" / name
        if src.exists():
            copy(src, gh / "docs" / name)
        else:
            print(f"  WARNING: docs/{name} missing (G-line doc)")
    if (ROOT / "docs" / "versions").exists():
        copy(ROOT / "docs" / "versions", gh / "docs" / "versions")
    (gh / "LICENSE").write_text(LICENSE_MIT, encoding="utf-8")
    (gh / "CITATION.cff").write_text(CITATION_CFF, encoding="utf-8")
    write_checksums(gh, "CHECKSUMS.txt")
    print(f"  github/: {sum(1 for _ in gh.rglob('*') if _.is_file())} files, {n_scripts} scripts")
    return gh


def build_zenodo(out: Path, gh: Path) -> None:
    z = out / "zenodo"
    # record 1: code tar
    r1 = z / "record1-code"
    r1.mkdir(parents=True, exist_ok=True)
    tarp = r1 / f"bioagentdb-v{VERSION_FULL}.tar.gz"
    with tarfile.open(tarp, "w:gz") as tf:
        tf.add(gh, arcname=f"bioagentdb-v{VERSION_FULL}")
    write_checksums(r1)
    print(f"  zenodo/record1-code/: {tarp.name} {tarp.stat().st_size} B")

    # record 2: release snapshot + stats + schema + README
    r2 = z / "record2-release"
    r2.mkdir(parents=True, exist_ok=True)
    (r2 / "record2-release.zip").unlink(missing_ok=True)  # never re-zip a moved-in archive
    need(V16)
    copy(V16, r2 / "bioagentdb.sqlite3")
    copy(ROOT / "docs" / "versions", r2 / "versions")
    con = sqlite3.connect(f"file:{V16.as_posix()}?mode=ro", uri=True)
    ddl = [r[0] for r in con.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name")]
    counts = {}
    for (t,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        counts[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    con.close()
    (r2 / "schema.sql").write_text("\n\n".join(ddl) + "\n", encoding="utf-8")
    (r2 / "table-counts.txt").write_text(
        "\n".join(f"{k}\t{v}" for k, v in counts.items()) + "\n", encoding="utf-8")
    h = sha256(V16)
    (r2 / "README.md").write_text(
        RECORD2_README.format(version=VERSION, hash=f"{h[:8]}…{h[-5:]}".upper()),
        encoding="utf-8")
    write_checksums(r2)
    print(f"  zenodo/record2-release/: sqlite {V16.stat().st_size} B, sha16 {sha16(V16)}")

    # record 3: evaluation materials (no *.log)
    r3 = z / "record3-evaluation"
    r3.mkdir(parents=True, exist_ok=True)
    copy(ROOT / "data" / "gold", r3 / "gold")
    copy(ROOT / "data" / "eval", r3 / "eval")
    v2 = ROOT / "docs" / "paper" / "cbc" / "v2"
    for rel in ("gates-gold", "checks"):
        if (v2 / rel).exists():
            copy(v2 / rel, r3 / "v2" / rel)
    for name in ("p4-report.md", "delta-report.md", "delta-flip-audit.md",
                 "t2-mapping.md", "t2v2_checks.sql", "NUMBER-SOURCES-v1.6.md",
                 "NUMBER-SOURCES-v1.6.json", "t2-recheck.jsonl"):
        srcv = v2 / name
        if srcv.exists():
            copy(srcv, r3 / "v2" / name)
    write_checksums(r3)
    n3 = sum(1 for f in r3.rglob("*") if f.is_file())
    print(f"  zenodo/record3-evaluation/: {n3} files")

    # record 4: paper sources (manuscript + figures + evidence)
    r4 = z / "record4-paper-sources"
    r4.mkdir(parents=True, exist_ok=True)
    fin = ROOT / "docs" / "paper" / "final"
    cbc = ROOT / "docs" / "paper" / "cbc"
    for src, dst in ((cbc / "cbc-paper.md", "manuscript.md"),
                     (cbc / "cbc-paper.pdf", "manuscript.pdf"),
                     (cbc / "v2" / "captions-v1.6.md", "captions.md"),
                     (cbc / "v2" / "tables-v1.6.md", "tables.md"),
                     (cbc / "overrides.md", "overrides.md"),
                     (fin / "references.md", "references.md")):
        if src.exists():
            copy(src, r4 / dst)
    for tree in ("figures", "evidence"):
        if (fin / tree).exists():
            copy(fin / tree, r4 / tree)
    write_checksums(r4)
    n4 = sum(1 for f in r4.rglob("*") if f.is_file())
    print(f"  zenodo/record4-paper-sources/: {n4} files")

    make_upload_zips(out, z)


def make_upload_zips(out: Path, z: Path) -> None:
    """Zenodo takes files, not folders: one upload-ready zip per record
    (+ loose README/SHA256SUMS copies that the record page can display)."""
    up = out / "upload"
    if up.exists():
        shutil.rmtree(up)
    up.mkdir(parents=True)
    for name in ("record2-release", "record3-evaluation", "record4-paper-sources"):
        src = z / name
        if not src.exists():
            continue
        shutil.make_archive(str(up / name), "zip", root_dir=str(z), base_dir=name)
        for extra in ("README.md", "SHA256SUMS.txt"):
            p = src / extra
            if p.exists():
                shutil.copy2(p, up / f"{name}-{extra}")
    tar_src = z / "record1-code" / f"bioagentdb-v{VERSION_FULL}.tar.gz"
    if tar_src.exists():
        shutil.copy2(tar_src, up / "record1-code.tar.gz")
    # split oversized zips for reliable upload (Zenodo accepts multiple files;
    # e.g. record2-release.zip ~24 MB -> 6 x ~4 MB parts). The joined zip moves to
    # the zenodo/ tree and the upload side gets parts + sums + a join note.
    SPLIT_THRESHOLD = 8_000_000
    SPLIT_CHUNK = 4_006_816
    for zp in sorted(up.glob("*.zip")):
        if zp.stat().st_size <= SPLIT_THRESHOLD:
            continue
        data = zp.read_bytes()
        n = max(1, (len(data) + SPLIT_CHUNK - 1) // SPLIT_CHUNK)
        base = (len(data) + n - 1) // n
        part_rows = []
        for i in range(n):
            chunk = data[i * base:(i + 1) * base]
            pth = up / f"{zp.name}.{i + 1:03d}"
            pth.write_bytes(chunk)
            part_rows.append(f"{hashlib.sha256(chunk).hexdigest()}  {pth.name}")
        full = hashlib.sha256(data).hexdigest()
        stem = zp.stem
        (up / f"{stem}-SHA256SUMS.txt").write_text(
            f"# {stem} (upload set) — sha256\n" + "\n".join(part_rows)
            + f"\n{full}  {zp.name}   (joined from parts .001-.{n:03d}, in order)\n",
            encoding="utf-8")
        note = (f"# NOTE — this record is uploaded split into {n} parts\n\n"
                f"Files `{zp.name}.001` … `{zp.name}.{n:03d}` are consecutive parts of\n"
                f"`{zp.name}`. Join them in order and verify sha256 `{full}` before unzipping\n"
                f"(Windows: copy /b; Linux/macOS: cat; see {stem}-SHA256SUMS.txt).\n\n---\n\n")
        loose_readme = up / f"{stem}-README.md"
        if loose_readme.exists():
            t = loose_readme.read_text(encoding="utf-8")
            if "uploaded split into" not in t:
                loose_readme.write_text(note + t, encoding="utf-8")
        else:
            (up / f"{stem}-JOIN-NOTE.md").write_text(note, encoding="utf-8")
        zp.replace(out / "zenodo" / stem / zp.name)   # keep the joined zip in zenodo/
    print(f"  upload/: {sum(1 for _ in up.iterdir())} files ready to upload")
    for f in sorted(up.iterdir()):
        print(f"    {f.name}  {f.stat().st_size} B")


RECORD2_README = """# BioAgentDB v{version} — release catalog snapshot (frozen)

Contents
- `bioagentdb.sqlite3` — the frozen v{version} release database (catalog metadata only:
  dataset records, papers, evidence quotes, links, statuses; no experimental data).
  v1.6 additionally carries `t2v2_checks`: the per-link rows of the 2026-10
  re-verification wave under the v2 link gate (status, reason, signal, carry-forward flag).
- `schema.sql`, `table-counts.txt` — schema and per-table row counts.
- `versions/` — archived statistics for v0.9 through v{version} (project history).

Integrity
- The database sha256 is listed in `SHA256SUMS.txt`. The article reports the
  snapshot hash prefix `{hash}`.

Reproduce (read-only)
```python
import sqlite3
con = sqlite3.connect("file:bioagentdb.sqlite3?mode=ro", uri=True)
print(con.execute("SELECT status, COUNT(*) FROM datasets GROUP BY status").fetchall())
```

License: the catalog metadata in this snapshot is released under CC BY 4.0.
The software is released under the MIT license (see the code archive record).
"""


UPLOAD_GUIDE = """# Upload guide (staging) — prepared {date}

> Everything below was assembled by `scripts/make_release_staging.py` on top of
> commit `{commit}`. Verify `SHA256SUMS.txt` before uploading. Uploaded BY THE
> USER; the controller only prepares packages (L-0325).

## 0 · Before uploading

- [ ] Skim the record descriptions below; they are ready to paste as new versions.

## 1 · GitHub (public repo `zhaoshuh/bioagentdb` — already exists)

1. Push the **contents** of `github/` as a single new commit to the existing
   repository (web UI "Upload files", or a local `git init && git add -A &&
   git commit` + force-push per your access method).
2. Create the GitHub **release** `v{VERSION_FULL}` from that commit.

## 2 · Zenodo (upload NEW VERSIONS of the existing records)

> **Zenodo takes files, not folders.** On each existing record page choose
> "New version", then upload the ready-to-go file(s) from `upload/`.

### Record 1 — code archive  (or link the GitHub release)
- **Title**: BioAgentDB v{VERSION_FULL} — code archive (adversarial, machine-verified literature-mining pipeline)
- **Files**: `record1-code.tar.gz` — adds the v2 verification gates (`t1_v2` slot rule with
  normalization tiers; `t2_v2` three-valued link resolution with soft-404 / anti-bot sniffing
  and bounded retries) and the re-verification drivers; the frozen fixture set (54 + 54 cases) ships with the evaluation materials (record 3).

### Record 2 — release catalog snapshot
- **Title**: BioAgentDB v{VERSION_FULL} — frozen release catalog of announced multi-omics datasets
- **Files**: `record2-release.zip` + loose README/SHA256SUMS — the frozen v1.6 snapshot (the
  catalog is unchanged from v1.5: 2,289 records from 21,014 papers) plus `t2v2_checks`, the
  3,526-row 2026-10 re-verification telemetry, and statistics for v0.9-v{VERSION}.
  (For reliable upload the zip ships **split into ~4 MB parts** — `record2-release.zip.001` …
  join them in order per the README before unzipping.)

### Record 3 — evaluation materials
- **Title**: BioAgentDB — evaluation materials (human annotation, baselines, ablations, audits, gate-v2 evidence)
- **Files**: `record3-evaluation.zip` + SHA256SUMS — the 250-paper annotation and reports,
  **plus the gate-v2 evidence subset** (`v2/`: frozen fixtures, self-test reports, the
  2026-10 per-link re-check rows and the delta / P4 reports).

### Record 4 — paper sources  → uploaded LATER (after the e-series freeze, P10)
- The staging tree builds `record4-paper-sources/` for completeness; upload it as a new
  version only when the controller confirms (post-e2).

## 3 · After uploading — send back

- GitHub release URL; the new-version DOIs for records 1-3 (concept DOIs; record 4 follows after the e-series freeze).

The controller then re-runs the release checks and prepares the final submission package.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="D:/BioAgentDB-release")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("== github export ==")
    gh = build_github(out)
    print("== zenodo packages ==")
    build_zenodo(out, gh)

    print("== secret scan ==")
    hard, soft = scan_secrets(out)
    for h in hard[:40]:
        print("  HARD:", h)
    print(f"  hard hits: {len(hard)} | soft hits: {len(soft)}")

    import subprocess
    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        commit = "unknown"

    v16h = sha256(V16) if V16.exists() else ""
    (out / "SANITIZE-REPORT.md").write_text(SANITIZE_TEMPLATE.format(
        date=DATE, commit=commit, hard_count=len(hard),
        hard=("\n".join(f"- `{h}`" for h in hard) or "- (none)"),
        soft=("\n".join(f"- `{s}`" for s in soft) or "- (none)"),
        v16hash=f"{v16h[:8]}…{v16h[-5:]}".upper() if v16h else "n/a",
    ), encoding="utf-8")
    (out / "UPLOAD-GUIDE.md").write_text(
        UPLOAD_GUIDE.format(date=DATE, commit=commit, VERSION_FULL=VERSION_FULL, VERSION=VERSION), encoding="utf-8")
    write_checksums(out)
    print("== done ==")
    print(f"staging root: {out}")
    for f in sorted(out.rglob("*")):
        if f.is_file() and f.stat().st_size > 1_000_000:
            print(f"  big: {f.relative_to(out).as_posix()} {f.stat().st_size}")


SANITIZE_TEMPLATE = """# SANITIZE-REPORT — release staging (prepared {date}, repo commit `{commit}`)

## Secret scan

Hard patterns (sk-/AKIA/private keys/ghp_/xox): **{hard_count} hits** (target 0)
{hard}

Soft patterns (api key / password / secret / bearer assignments, reviewed):
{soft}

## Exclusions (by design; see docs/paper/cbc/DATA-PACKAGING.md)

- `.env` (never committed; not exported), `deploy/*-data/` runtime volumes
- `data/{{backups,pilot,scratch,llm_cache,logs,refs,reviews,web_cache,workbench_cache}}`
- `docs/{{reviews, paper-plan*, expansion, process plans}}`, `docs/paper/cbc/v2/reviews/**`,
  paper reviews/ledger/SIGNOFF
- `scripts/_*` one-off probes; `vendor/omics-discovery` (submodule reference only);
  `tools/cloudflared.exe` (local binary)
- `*.log` anywhere (including evaluation logs)

## Notes

- Provider keys are read from the environment since the portability pass.
- Soft hits of the `token=eyJ...` class inside `data/gold/**` are verbatim quotes from
  cited papers; not operator credentials.
- The frozen v1.6 database was opened read-only for the schema dump; its bytes were copied,
  never modified (article hash prefix `{v16hash}`).
"""
if __name__ == "__main__":
    main()
