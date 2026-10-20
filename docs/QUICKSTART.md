# QUICKSTART — from a clean machine to your first mined record

BioAgentDB is a catalog of **omics data records** (metadata + verbatim paper evidence + links) built by
mining scientific papers. This guide takes a fresh machine to a running site, then to the first mined
records. Everything below runs locally; nothing is uploaded anywhere.

> Clean-clone, step-by-step third-party setup (10 steps, with verification commands):
> see [`SELF-HOST.md`](SELF-HOST.md). Configuration reference: [`CONFIG.md`](CONFIG.md).

## 0. Requirements

| Path | Needs |
|---|---|
| Docker (recommended) | Docker Desktop or Docker Engine + Compose v2, ~2 GB disk |
| No Docker | Python 3.12 (conda or venv) |

An LLM key is **optional to start**: browsing/export work without it; chat and mining ask for one.
The default LLM endpoint is DeepSeek (OpenAI-compatible); any compatible `DEEPSEEK_BASE_URL` works.

## 1. Start the service (Docker)

```bash
git clone <repo-url> bioagentdb && cd bioagentdb
cp .env.example .env        # optional now; edit later to add DEEPSEEK_API_KEY
./start.sh                  # build + start app and UI
```

Windows PowerShell: `.\start.ps1` (mining later with `.\start.ps1 -WithMining`).
Without the helper scripts: `docker compose up -d --build`.

Check it is up:

```bash
curl http://localhost:8000/api/health      # {"status":"ok","service":"BioAgentDB"}
```

Open <http://localhost:8000> — the catalog UI (empty until you mine, or browse-only until you add data).
The service listens on **8081/8082/8083** only for the optional preview stacks (step 4).

## 2. First mining

A fresh deployment starts **blank** (`seed_starter_themes=0`): themes appear as you mine them.
There are two equivalent ways to mine:

**A. From the site (chat / My mining)** — needs `DEEPSEEK_API_KEY` in `.env` and a running scheduler:

```bash
./start.sh --with-mining        # app + prewarm miner + task scheduler (mining profile)
```

Then open the home page chat (or `/ask` → **Ming**), describe a topic or disease, confirm the
mining task, and watch progress on `mining.html`. On a blank database the prewarm profile creates its
configured themes on demand.

**B. From the command line (no scheduler needed)**:

```bash
python scripts/collect.py --disease "spinocerebellar ataxia" --limit 100 --workers 3
```

Records/evidence/links land in `../bioagentdb-records/<slug>/records.jsonl` (+ `REPORT.md`); the
`records` are also written to the catalog DB, so they show up in the UI.

> Mining calls the LLM and public literature APIs (Europe PMC / NCBI). Every record is grounded in a
> verbatim quote from the paper (T1) and its data address is checked to resolve (T2).

## 3. Browse the catalog

- Catalog page: free-text search, theme/modality/organism/status filters, paper ↔ dataset views.
- A record shows the evidence quote, the zone it came from (abstract / Data Availability), linked
  papers, and every data address with reachability status.
- Export the filtered dataset table as CSV from the catalog toolbar.
- `guide.html` has the in-site user guide; `/api/stats` gives machine-readable totals.

## 4. Optional: the three preview stacks (empty / live / release)

`deploy/compose.preview.yml` runs three isolated deployments side by side, including the **v1.6
release stack** whose numbers match the paper:

```bash
python deploy/bootstrap.py       # create .env if missing + data dirs; load the _v16 release snapshot when present
docker compose -f deploy/compose.preview.yml up -d
python deploy/bootstrap.py --check            # health-probe all three ports
```

| Stack | URL | Data |
|---|---|---|
| empty | http://127.0.0.1:8081 | fresh, zero-seed |
| live | http://127.0.0.1:8082 | your own snapshot (`deploy/live-data/`), optional |
| release | http://127.0.0.1:8083 | copy of the frozen `_v16` release DB (paper-aligned) |

The release snapshot (`data/releases/_v16/bioagentdb.sqlite3`, sha256 `15C067D8…94958`) is not shipped
in the repo; obtain it from the paper's data release (Zenodo: `10.5281/zenodo.23433277` (split parts; join per its README)) or run the release stack empty. If your
machine uses a proxy for containers, see `deploy/README.md` (default: direct; set `BIOAGENTDB_CONTAINER_PROXY=http://host.docker.internal:<port>` in `deploy/.env` or the environment if your network needs a proxy).

## 5. Without Docker (conda / venv)

```bash
conda env create -f environment.yml && conda activate bioagentdb   # or: pip install -r requirements.txt
python scripts/cli.py init        # create the DB + apply config
python scripts/cli.py serve       # http://127.0.0.1:8000
```

The service binds localhost by default; set `BIOAGENTDB_HOST=0.0.0.0` to expose it (see §7).

## 6. Configuration and data

- `.env` — secrets and deployment keys (`DEEPSEEK_API_KEY`, `NCBI_EMAIL`, host/port, data dir).
- `config/bioagentdb.toml` — policy parameters (copy from `config/bioagentdb.example.toml`); every key,
  default and env-var override is listed in [`CONFIG.md`](CONFIG.md).
- Data lives in `data/` (SQLite + caches + logs); override the location with `BIOAGENTDB_DATA_DIR`.
- Backups: `python scripts/backup.py` (VACUUM INTO `data/backups/`, keeps 7).

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `docker compose` fails: no `.env` | `cp .env.example .env` (or `python deploy/bootstrap.py`) |
| UI loads but chat says it cannot answer | set `DEEPSEEK_API_KEY` in `.env`, restart: `docker compose restart app` |
| Mining never starts | the scheduler must run: `./start.sh --with-mining`; check `docker compose logs -f scheduler` |
| Outbound requests fail inside containers | disable/adjust the container proxy: `BIOAGENTDB_CONTAINER_PROXY=` (env or `deploy/.env`), then `up -d` |
| Port 8000 busy | set `BIOAGENTDB_PORT` in `.env` and rebuild mapping, or run the preview stacks on 8081–8083 |
| Want read-only public demo | set `BIOAGENTDB_PUBLIC_DEMO=1` (all write actions return 403) |
