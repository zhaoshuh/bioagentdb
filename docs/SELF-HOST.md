# SELF-HOST — third-party deployment in 10 steps

A complete, verifiable path from a clean clone to a self-hosted BioAgentDB instance. Written for
someone who has never seen this repository. Total time: ~5–10 minutes (browse-only) plus mining time.

Legend: `$` = any shell (bash on Linux/macOS/WSL, or PowerShell on Windows where noted).

---

## 1. Get the code (and know what is optional)

```bash
git clone <repo-url> bioagentdb
cd bioagentdb
```

Optional components that are **not** part of the clone:

| Optional piece | Why you may want it | How to get it |
|---|---|---|
| `vendor/omics-discovery` (branch `lite`) | extended accession regexes + deep T3 verification; the app degrades gracefully without it (a reduced regex set is used) | `git clone -b lite https://github.com/zhaoshuh/omics-discovery vendor/omics-discovery` then `pip install -e vendor/omics-discovery` (or mount it into the image) |
| v1.6 release DB snapshot (`data/releases/_v16/bioagentdb.sqlite3`, sha256 `15C067D8…94958`) | browse the exact catalog published with the paper (2,289 dataset records) | download from the paper's data release (Zenodo, `10.5281/zenodo.23433277`) and place it at that path |

Everything else you need is in the clone. Verify: `ls app web deploy scripts requirements.txt Dockerfile`.

## 2. Check runtime prerequisites

```bash
docker --version && docker compose version      # Compose v2.24+ (env_file.required)
```

No Docker? Use the conda/venv path in step 5 instead.

## 3. Configure (secrets are optional at first)

```bash
cp .env.example .env
```

Edit `.env`:

- `DEEPSEEK_API_KEY=…` — **required for chat and mining**; leave empty to browse/export only.
- `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` — any OpenAI-compatible endpoint.
- `NCBI_EMAIL` / `NCBI_API_KEY` — optional; polite/raised rate limits for literature APIs.
- Export keys (`BIOAGENTDB_DATA_DIR`, `BIOAGENTDB_HOST`, `BIOAGENTDB_PORT`, …) — see `docs/CONFIG.md` §5.
- `.env` is gitignored and never baked into the image.

## 4. Start the service

```bash
docker compose up -d --build        # app + UI on http://localhost:8000
# or: ./start.sh          (bash)     .\start.ps1          (Windows PowerShell)
# mining profile:         ./start.sh --with-mining      /  .\start.ps1 -WithMining
```

Verify:

```bash
docker compose ps                                   # app = healthy
curl -s http://localhost:8000/api/health            # {"status":"ok",...}
curl -s http://localhost:8000/api/stats | head -c 300
```

## 5. (No Docker) run from Python

```bash
conda env create -f environment.yml && conda activate bioagentdb
# or: python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python scripts/cli.py init
python scripts/cli.py serve          # http://127.0.0.1:8000
```

## 6. Load data — blank or release snapshot

**Blank (grow your own catalog):** a fresh DB is empty by design (`seed_starter_themes=0`).

**Release snapshot (paper-aligned browsing):**

```bash
# 1) download the release package (Zenodo: 10.5281/zenodo.23433277; it ships as split parts -
#    join them in order per its README), place the DB at data/releases/_v16/bioagentdb.sqlite3, then either
python deploy/bootstrap.py --tier release         # copies it (WAL-safe VACUUM INTO) to deploy/release-data/
docker compose -f deploy/compose.preview.yml up -d
#    → release stack on http://127.0.0.1:8083 (empty on 8081, live on 8082)
# 2) or serve it directly:
BIOAGENTDB_DB_PATH=data/releases/_v16/bioagentdb.sqlite3 python scripts/cli.py serve
```

`deploy/bootstrap.py` verifies the sha256 when the source is the default `_v16` snapshot.

## 7. Mine your first theme

**From the site** (needs `DEEPSEEK_API_KEY` + a running scheduler: `--with-mining` / `--profile mining`):
open the home-page chat (or `/ask` → Ming), describe the topic, confirm the mining task,
watch `mining.html`. On a blank DB the prewarm profile creates its configured themes on demand.

**From the CLI** (no scheduler):

```bash
python scripts/collect.py --disease "spinocerebellar ataxia" --limit 100 --workers 3
```

Outputs land in `../bioagentdb-records/<slug>/` and in the catalog DB. Each record carries a verbatim
paper quote (T1) and a checked data address (T2).

## 8. Verify the deployment

```bash
python scripts/cli.py status                       # miner/throughput/pool snapshot
python scripts/_content_audit.py                   # catalog content self-audit (read-only; non-zero exit on P0)
curl -s -X POST http://localhost:8000/api/records -H 'Content-Type: application/json' \
     -d '{"q":"","limit":3}' | head -c 400          # listing works
python deploy/bootstrap.py --check                  # preview stacks health (if you run them)
```

## 9. Backups and upgrades

```bash
python scripts/backup.py            # data/backups/, keeps the last 7 (VACUUM INTO, WAL-safe)
docker compose down                 # stop
git pull && docker compose up -d --build    # upgrade (rebuild needed when requirements.txt changes)
```

Data survives rebuilds: it lives in `data/` (bind-mounted), not in the image.

## 10. Security and public exposure

Defaults are conservative: the Python service binds `127.0.0.1`; the compose app publishes 8000 on the
host. Before exposing the site:

- Put it behind a reverse proxy with TLS; don't publish the SQLite file.
- For a read-only public demo set `BIOAGENTDB_PUBLIC_DEMO=1` (write actions → 403).
- Keep `.env` out of any backup you share; rotate the LLM key if it leaks.
- Mining spends LLM tokens: bound it with `mining_task_max_active_tasks`,
  `mining_task_papers_per_round`, `scheduler_global_max_miners` (see `docs/CONFIG.md`).

---

### What is intentionally not shipped

- The v1.6 release snapshot and any mined data (Zenodo / your own mining).
- `vendor/omics-discovery` (optional external component; see step 1).
- Development/paper-review scripts under `scripts/_r*` and `scripts/_probe_*`; they are internal
  tooling tied to the original authoring machine and are not required for deployment.
