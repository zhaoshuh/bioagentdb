"""Build the release staging tree OUTSIDE the repo (default D:/BioAgentDB-release/).

Per docs/paper/cbc/DATA-PACKAGING.md (ledger L-0338). Read-only w.r.t. the
repository and the frozen release database (schema dump via sqlite uri=ro).

Outputs:
  github/                        net export for the public repository (single-commit upload)
  zenodo/record1-code/           bioagentdb-v1.5.0.tar.gz (tar of github/)
  zenodo/record2-release/        v1.5 sqlite snapshot + v0.9-v1.5 stats + schema + README + SHA256SUMS
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
V15 = ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3"

ROOT_FILES = ["README.md", "requirements.txt", "environment.yml", "Dockerfile",
              "docker-compose.yml", "start.sh", "start.ps1", ".env.example",
              ".gitignore", ".dockerignore"]
DOCS_FILES = ["QUICKSTART.md", "SELF-HOST.md", "CONFIG.md"]
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
version: "1.5.0"
date-released: "2026-10-15"
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
        if f.name.startswith("_"):
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
    tarp = r1 / "bioagentdb-v1.5.0.tar.gz"
    with tarfile.open(tarp, "w:gz") as tf:
        tf.add(gh, arcname="bioagentdb-v1.5.0")
    write_checksums(r1)
    print(f"  zenodo/record1-code/: {tarp.name} {tarp.stat().st_size} B")

    # record 2: release snapshot + stats + schema + README
    r2 = z / "record2-release"
    r2.mkdir(parents=True, exist_ok=True)
    need(V15)
    copy(V15, r2 / "bioagentdb.sqlite3")
    copy(ROOT / "docs" / "versions", r2 / "versions")
    con = sqlite3.connect(f"file:{V15.as_posix()}?mode=ro", uri=True)
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
    (r2 / "README.md").write_text(RECORD2_README, encoding="utf-8")
    write_checksums(r2)
    print(f"  zenodo/record2-release/: sqlite {V15.stat().st_size} B, sha16 {sha16(V15)}")

    # record 3: evaluation materials (no *.log)
    r3 = z / "record3-evaluation"
    r3.mkdir(parents=True, exist_ok=True)
    copy(ROOT / "data" / "gold", r3 / "gold")
    copy(ROOT / "data" / "eval", r3 / "eval")
    write_checksums(r3)
    n3 = sum(1 for f in r3.rglob("*") if f.is_file())
    print(f"  zenodo/record3-evaluation/: {n3} files")

    # record 4: paper sources (manuscript + figures + evidence)
    r4 = z / "record4-paper-sources"
    r4.mkdir(parents=True, exist_ok=True)
    fin = ROOT / "docs" / "paper" / "final"
    for name in ("paper.md", "paper.pdf", "captions.md", "tables.md", "references.md"):
        src = fin / name
        if src.exists():
            copy(src, r4 / name)
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
    tar_src = z / "record1-code" / "bioagentdb-v1.5.0.tar.gz"
    if tar_src.exists():
        shutil.copy2(tar_src, up / "record1-code.tar.gz")
    print(f"  upload/: {sum(1 for _ in up.iterdir())} files ready to upload")
    for f in sorted(up.iterdir()):
        print(f"    {f.name}  {f.stat().st_size} B")


RECORD2_README = """# BioAgentDB v1.5 — release catalog snapshot (frozen)

Contents
- `bioagentdb.sqlite3` — the frozen v1.5 release database (catalog metadata only:
  dataset records, papers, evidence quotes, links, statuses; no experimental data).
- `schema.sql`, `table-counts.txt` — schema and per-table row counts.
- `versions/` — archived statistics for v0.9 through v1.5 (project history).

Integrity
- The database sha256 is listed in `SHA256SUMS.txt`. The article reports the
  snapshot hash prefix `9B55F8B8…C299E`.

Reproduce (read-only)
```python
import sqlite3
con = sqlite3.connect("file:bioagentdb.sqlite3?mode=ro", uri=True)
print(con.execute("SELECT COUNT(*), SUM(verified) FROM datasets").fetchone())
```

License: the catalog metadata in this snapshot is released under CC BY 4.0.
The software is released under the MIT license (see the code archive record).
"""

UPLOAD_GUIDE = """# Upload guide (staging) — prepared {date}

> Everything below was assembled by `scripts/make_release_staging.py` on top of
> commit `{commit}`. Verify `SHA256SUMS.txt` before uploading. Uploaded BY THE
> USER; the controller only prepares packages (L-0325).

## 0 · Before uploading (author placeholders)

- [ ] Real author names/affiliations → will be filled into the manuscript at
      submission; also replace the placeholder author in `github/CITATION.cff`,
      `github/LICENSE` if desired.
- [ ] Optional (recommended): rotate the LLM provider key that was used during
      development (the old hard-coded value was removed in the portability pass;
      `scripts/_probe_sources.py` now reads the environment).

## 1 · GitHub (public repo `zhaoshuh/bioagentdb`)

1. Create a new public repository `zhaoshuh/bioagentdb` (no README/license file
   — they are in the package).
2. Upload the **contents** of `github/` as a single commit
   (web UI "Add file → Upload files", or `git init && git add -A && git commit`
   in a local copy of `github/`).
3. Recommended: create a GitHub **release** `v1.5.0` from that commit (Zenodo
   can mint the code DOI automatically if the repository is linked; otherwise
   upload `zenodo/record1-code/` manually).

## 2 · Zenodo records (create 2-4 records; paste the metadata below)

> **Zenodo accepts files, not folders.** Upload the ready-to-go files from
> `upload/`: the zips below (one per record) plus the code tar. The loose
> `*-README.md` / `*-SHA256SUMS.txt` files travel alongside their zip so the
> record page can display them directly.

### Record A — code archive  (or link the GitHub release)
- **Title**: BioAgentDB v1.5.0 — code archive (adversarial, machine-verified literature-mining pipeline)
- **Description**: Source code and deployment files for BioAgentDB, the pipeline that mines
  published papers for announced multi-omics datasets and admits a record only when the
  accession is grounded in the announcing sentence and the link is checked. Includes the
  deterministic paper-build chain and a self-hosting quickstart.
- **Keywords**: bioinformatics; literature mining; large language models; data curation
- **License**: MIT
- **Files**: `upload/record1-code.tar.gz`

### Record B — release catalog snapshot
- **Title**: BioAgentDB v1.5 — frozen release catalog of announced multi-omics datasets
- **Description**: The frozen v1.5 release database (2,289 paper-grounded dataset records
  from 21,014 papers; metadata, verbatim evidence quotes and checked links only — never the
  data itself), with schema, per-table counts and archived statistics for v0.9-v1.5.
- **Keywords**: multi-omics datasets; data availability; metadata catalog; machine verification
- **License**: CC BY 4.0 (metadata)
- **Files**: `upload/record2-release.zip` + `record2-release-README.md` + `record2-release-SHA256SUMS.txt`

### Record C — evaluation materials
- **Title**: BioAgentDB — evaluation materials (human annotation, baselines, ablations, audits)
- **Description**: The 250-paper human annotation, LLM/RAG baseline predictions and reports,
  ablation and self-improvement evaluations, reviewer statistics, the refusal-audit sheet
  (CSV) and downstream question-set materials, as referenced by the article's evaluation
  protocol (sections 6.1-6.11).
- **Keywords**: evaluation; reproducibility; benchmarking; annotation
- **License**: CC BY 4.0
- **Files**: `upload/record3-evaluation.zip` + `record3-evaluation-SHA256SUMS.txt`

### Record D — paper sources (optional)
- **Title**: BioAgentDB — manuscript sources (figures, captions, tables, evidence captures)
- **Files**: `upload/record4-paper-sources.zip` + `record4-paper-sources-SHA256SUMS.txt`

## 3 · After uploading — send back to the project

- GitHub repository URL
- Zenodo DOIs (code / release / evaluation [+ paper sources])

The controller will then run the backfill round (8 placeholder points in the
manuscript, plus the planned "code and" scope fix), re-run the release checks and
prepare the final submission package.
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

    (out / "SANITIZE-REPORT.md").write_text(SANITIZE_TEMPLATE.format(
        date="2026-10-15", commit=commit, hard_count=len(hard),
        hard=("\n".join(f"- `{h}`" for h in hard) or "- (none)"),
        soft=("\n".join(f"- `{s}`" for s in soft) or "- (none)"),
    ), encoding="utf-8")
    (out / "UPLOAD-GUIDE.md").write_text(
        UPLOAD_GUIDE.format(date="2026-10-15", commit=commit), encoding="utf-8")
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
- `docs/{{reviews, paper-plan*, expansion, process plans}}`, paper reviews/ledger/SIGNOFF
- `scripts/_*` one-off probes (364 files); `vendor/omics-discovery` (submodule reference only;
  upstream license note pending); `tools/cloudflared.exe` (local binary, 55 MB)
- `*.log` anywhere (including evaluation logs)

## Notes

- The historical hard-coded provider key in `scripts/_probe_sources.py` was removed in the
  portability pass (now read from the environment). Rotation of the old key remains an
  optional step for the operator (see UPLOAD-GUIDE §0).
- Soft hits of the `token=eyJ...` class inside `data/gold/**` are **verbatim quotes from
  cited papers** (their own tokenized repository links, reproduced exactly as published).
  They are part of the evidence-quote design and are already present in the released
  catalog; they are not operator credentials.
- The frozen v1.5 database was opened read-only for the schema dump; its bytes were copied,
  never modified (article hash prefix `9B55F8B8…C299E`).
"""


if __name__ == "__main__":
    main()
