# BioAgentDB — Multi-Omics Data Record Catalog (paper-mined)

A catalog of **omics data records** (metadata only, never the data itself) built by an
agent that mines **scientific papers** — Abstract, Data Availability statements, and
supplementary file listings. Each record stores where the actual data lives
(repository + accession + link), the exact paper evidence (verbatim quote), and the
verification outcome.

- **Theme-anchored**: proteinopathies across neurodegeneration — Alzheimer's, ALS,
  Parkinson's, and on-demand themes (FTD, Huntington's, prion/CJD, PSP, DLB, MSA, …).
- **Deterministic engine**: a content-addressed LLM cache avoids repeated model calls.
- **Archived & citable**: the code snapshot is archived on Zenodo
  ([10.5281/zenodo.23417034](https://doi.org/10.5281/zenodo.23417034)); the released catalog
  (v1.6) and the evaluation materials are archived separately — see
  [`docs/versions/`](docs/versions/) for version statistics v0.9–v1.6.
- **Paper-driven**: records come from papers only; repositories are treated as where
  the data physically lives.
- **Verified**: every record must be grounded in the paper text (T1), and its data
  address is checked to resolve (T2); optional deep verification via GEO SOFT /
  repository manifests (T3, reusing `omics-discovery`).
- **Conversational**: a chat agent answers data-finding questions over the catalog,
  researches unfamiliar topics online (competitions, projects, platforms) to adjust
  its paper search, and can launch fresh harvests when coverage is missing.
- **Read-only catalog**: records are never edited/deleted from the UI; corrections
  happen by re-harvesting.

## Stack

| Piece | Tech |
|---|---|
| Server | Python 3.12 (conda env `bioagentdb`, any location), FastAPI + uvicorn |
| Storage | SQLite (WAL), single file `data/bioagentdb.sqlite3`, daily backup |
| Literature | Europe PMC REST (full text XML, supplementary bundle listing), NCBI E-utilities, OpenAlex-ready |
| LLM | DeepSeek (OpenAI-compatible, via `app/harvest/llm.py`) |
| Reused | `omics-discovery` @ branch `lite` (`vendor/omics-discovery`): DA-first extraction, SOFT verify, source adapters, knowledge |

## Quick start (Docker)

```bash
cp .env.example .env      # set DEEPSEEK_API_KEY
./start.sh                # service + UI      → http://localhost:8000
./start.sh --with-mining  # also start the mining agent
```

Windows PowerShell: `.\start.ps1` (add `-WithMining` for the mining profile).
Fresh deployments start blank (`seed_starter_themes=0`); mine your first theme with
the chat agent on the site or `python scripts/collect.py --disease "<name>"`.
Step-by-step guides: [`docs/QUICKSTART.md`](docs/QUICKSTART.md) ·
[`docs/SELF-HOST.md`](docs/SELF-HOST.md). A compose-based preview (empty/live/release
stacks on ports 8081–8083) lives in [`deploy/`](deploy/README.md).

Code is volume-mapped and config lives in `.env` (and in code), so edits take effect on
restart — no image rebuild. Stop with `docker compose down`.

Without Docker (conda env `bioagentdb`):

```bash
python scripts/cli.py init     # tables + themes
python scripts/cli.py serve    # http://127.0.0.1:8000
```

Unified CLI: `init · serve · mine · supervise · stop · pilot · status · cache`.

## User CLI — collect records for a disease

The low-barrier, command-line way to use the agent: point it at a disease, and it mines
in the background, writing the collected records into a **sibling folder** of the project
(`../bioagentdb-records/`). It keeps going until the disease is *mined out*, then writes a
summary report. Uses no cache.

```bash
python scripts/collect.py --disease "spinocerebellar ataxia"
python scripts/collect.py --disease "huntingtons-disease" --limit 100 --workers 3
python scripts/collect.py --disease "frontotemporal dementia,multiple system atrophy"
```

Output per disease:

```
../bioagentdb-records/<disease-slug>/
├── records.jsonl     # one line per paper: datasets + evidence + links
├── datasets.jsonl    # flat dataset records
├── REPORT.md         # summary (modalities / repositories / sample records)
└── miner.log
../bioagentdb-records/SUMMARY.md
```

Flags: `--out` (output root) · `--limit` (papers per round) · `--workers` ·
`--rounds` (round cap) · `--empty-rounds` (stop after N consecutive empty rounds).

## My mining — long-running mining tasks (user-facing)

On the site, talk to the agent — home-page chat or the workbench **Ming** panel — and confirm a
**mining task** for any research topic (diseases are just one kind): the agent derives the theme,
queries, budget and cadence, then a resident scheduler mines in the background — first until the
literature is exhausted, then a patrol. **Verified dataset records are published to the public
catalog** (`mining_task_publish_records=0` restores the private-only mode) and are also tracked per
task in `/mining.html` (exportable as JSONL/CSV). With `seed_starter_themes=0` (default) a fresh
deployment starts blank and grows with what you mine. The same T1/T2 verification gates apply.

```bash
python scripts/scheduler.py            # resident scheduler (poll interval from config)
python scripts/scheduler.py --once     # one pass to idle (tests)
```

Docker: `./start.sh --with-mining` starts it as the `scheduler` service (mining profile).

Configuration is **file-based**: `config/bioagentdb.example.toml` (copy to `config/bioagentdb.toml`)
carries every tunable — task bounds, scheduler & guardrails, miner, sources, verification, workbench,
collect — and `.env` keeps secrets/deployment. See [`docs/CONFIG.md`](docs/CONFIG.md).

## Pipeline

```
theme → Europe PMC sweep (dedupe by PMID/DOI/PMCID)
      → full text XML (OA) + Data Availability section + supplementary file listing (name+url only)
      → identifier extraction (GSE/PXD/PRJ.. / MTBLS / Zenodo / figshare / URLs) from abstract + DA
      → LLM structured record (modality/organism/tissue/disease/sample count; grounded fields only)
      → T1 quote grounding · T2 canonical link reachability + repository metadata cross-check
      → ingest (dedupe by repository+accession; merge papers/evidence)
```

Evidence-first: every record in the detail view shows the verbatim paper quote, the
zone it came from (abstract / Data Availability), the linked papers, and all data
addresses with reachability status.

## API (English only)

`GET /api/health` · `POST /api/health/check` · `GET /api/stats` · `GET /api/facets` ·
`POST /api/datasets` (filters) · `GET /api/datasets/{id}` · `POST /api/datasets/export` ·
`POST /api/papers` · `GET /api/runs` · `GET /api/runs/{id}` · `GET /api/themes` ·
`POST /api/themes/{slug}/harvest` · `POST /api/chat` (SSE) · `GET/PUT /api/settings`

## Configuration

`.env` (gitignored; also editable in UI Settings → stored in the DB settings table,
which takes precedence):

```
DEEPSEEK_API_KEY=…          DEEPSEEK_MODEL=deepseek-v4-pro
HTTP_PROXY=http://127.0.0.1:7890   (optional; fallback only — networking is direct-first)
NCBI_EMAIL=…                NCBI_API_KEY=…
HARVEST_MAX_PAPERS_PER_RUN=500     HARVEST_CONCURRENCY=6
```

## Layout

```
app/core        config · sqlite · utils · vocabulary (repo link templates)
app/harvest     themes · sweep · fulltext · extract · verify · ingest · runner · llm
app/catalog     read-only query layer (datasets/papers/runs/export)
app/chat        conversational retrieval agent
app/api         FastAPI routes + static web mount
web             English single-page UI (Catalog / Chat / Themes / Runs / Health / Settings)
vendor/omics-discovery   optional reused library for deep verification (not shipped; the core runs without it)
scripts         init_db · serve · harvest (CLI) · backup · check · install_service
```

## Known notes

- Europe PMC `sort` parameter currently returns 503 — the sweep pages without sort.
- Supplementary files are **listed only** (name + URL); contents are never parsed.
- PubMed/NCBI is reached by Python httpx directly (no Schannel revocation issue).