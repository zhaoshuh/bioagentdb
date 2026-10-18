"""BioAgentDB —— 多组学数据记录目录库 API + 静态前端。"""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.catalog import store
from app.core.db import init_db
from app.harvest import runner, themes

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
PAPER_DIR = Path(__file__).resolve().parents[2] / "docs" / "paper" / "final"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    themes.seed_themes(force=True)  # facets 以代码为准（force 覆盖旧种子）
    try:
        from app.chat import mining

        n = mining.reconcile()  # 结算服务器重启前遗留的 running 订单
        if n:
            logger.info("reconciled %d stale mining order(s)", n)
    except Exception:  # noqa: BLE001
        logger.exception("order reconcile failed")
    yield


app = FastAPI(
    title="BioAgentDB",
    version="0.1.0",
    description="Multi-omics dataset-record catalog built from papers (Alzheimer's-first).",
    lifespan=lifespan,
)


@app.middleware("http")
async def _public_demo_guard(request, call_next):
    """演示模式（BIOAGENTDB_PUBLIC_DEMO=1）：只放行只读与 chat，其余写操作 403。"""
    import os as _os

    if _os.environ.get("BIOAGENTDB_PUBLIC_DEMO") == "1" and request.method in (
            "POST", "PUT", "PATCH", "DELETE"):
        path = request.url.path
        read_only = {"/api/health/check", "/api/datasets/export", "/api/records",
                     "/api/datasets", "/api/papers", "/api/mining/analyze"}
        allowed = path.startswith("/api/chat") or path in read_only
        if not allowed:
            return JSONResponse({"detail": "disabled in public demo mode"}, status_code=403)
    return await call_next(request)


# ---------------------------------------------------------------- models

class DatasetQuery(BaseModel):
    q: str = ""
    theme: str = ""
    modality: str = ""
    organism: str = ""
    source: str = ""
    status: str = ""
    statuses: str = ""
    year_min: int | None = None
    year_max: int | None = None
    limit: int = Field(200, ge=1, le=500)
    offset: int = Field(0, ge=0)
    sort: str = "updated_at"


class HarvestRequest(BaseModel):
    theme: str = "alzheimers-disease"
    max_papers: int | None = Field(None, ge=1, le=5000)
    queries: list[str] | None = None


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class SettingsPut(BaseModel):
    deepseek_api_key: str | None = None
    deepseek_model: str | None = None
    deepseek_scoring_model: str | None = None
    http_proxy: str | None = None
    host: str | None = None
    port: str | None = None
    min_confidence: str | None = None
    api_token: str | None = None
    go_api_key: str | None = None
    go_base_url: str | None = None
    vision_model: str | None = None


# ---------------------------------------------------------------- datasets

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "BioAgentDB"}


@app.post("/api/health/check")
def health_check():
    """实测各外部源的连通性（写 source_health 表）。"""
    from app.core import utils

    targets = [
        ("EuropePMC", "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=cancer&format=json&pageSize=1"),
        ("NCBI_Eutils", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi"),
        ("ENA", "https://www.ebi.ac.uk/ena/portal/api/search?result=study&fields=study_accession&format=json&limit=1"),
        ("PRIDE", "https://www.ebi.ac.uk/pride/ws/archive/v2/search/projects?keyword=cancer&pageSize=1"),
        ("MetaboLights", "https://www.ebi.ac.uk/ebisearch/ws/rest/metabolights?query=cancer&format=json&size=1"),
        ("BioStudies", "https://www.ebi.ac.uk/biostudies/api/v1/search?query=cancer&pageSize=1"),
        ("CELLxGENE", "https://api.cellxgene.cziscience.com/curation/v1/collections?visibility=PUBLIC"),
        ("Zenodo", "https://zenodo.org/api/records?q=cancer&size=1"),
        ("NGDC_CNCB", "https://ngdc.cncb.ac.cn/"),
    ]
    results = []
    with __import__("app.core.db", fromlist=["db"]).db() as conn:
        for name, url in targets:
            t0 = time.time()
            resp = utils._request("GET", url, retries=1)
            ms = round((time.time() - t0) * 1000)
            ok = resp is not None and resp.status_code < 400
            detail = f"HTTP {resp.status_code}" if resp is not None else "unreachable"
            conn.execute(
                "INSERT INTO source_health(source, ok, latency_ms, detail, checked_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET ok=excluded.ok, "
                "latency_ms=excluded.latency_ms, detail=excluded.detail, checked_at=excluded.checked_at",
                (name, 1 if ok else 0, ms, detail, utils.utcnow()))
            results.append({"source": name, "ok": ok, "latency_ms": ms, "detail": detail})
    return {"checked_at": utils.utcnow(), "sources": results}


@app.get("/api/source-health")
def source_health():
    with __import__("app.core.db", fromlist=["db"]).db() as conn:
        rows = conn.execute("SELECT * FROM source_health ORDER BY source").fetchall()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/stats")
def stats():
    return store.stats()


@app.get("/api/throughput")
def throughput():
    return store.throughput()


@app.get("/api/facets")
def facets():
    return store.facets()


class RecordQuery(BaseModel):
    q: str = ""
    theme: str = ""
    modality: str = ""
    organism: str = ""
    status: str = ""
    statuses: str = ""
    limit: int = Field(60, ge=1, le=200)
    offset: int = Field(0, ge=0)
    sort: str = "year"


@app.post("/api/datasets")
def datasets(req: DatasetQuery):
    return store.list_datasets(**req.model_dump())


@app.post("/api/records")
def records(req: RecordQuery):
    return store.list_records(**req.model_dump())


@app.get("/api/records/{record_id}")
def record_detail(record_id: str):
    item = store.get_record(record_id)
    if item is None:
        raise HTTPException(404, "record not found")
    return item


@app.post("/api/datasets/export")
def datasets_export(req: DatasetQuery | None = None, status: str | None = None):
    """导出当前筛选的数据集 CSV（过滤条件与 /api/datasets 一致）。"""
    filters = dict(req.model_dump()) if req is not None else {}
    for k in ("limit", "offset", "sort"):
        filters.pop(k, None)
    if status:
        filters["status"] = status
    csv_text = store.export_csv(**filters)
    return PlainTextResponse(
        "\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=bioagentdb-datasets.csv"},
    )


@app.get("/api/datasets/{dataset_id}")
def dataset_detail(dataset_id: str):
    item = store.get_dataset(dataset_id)
    if item is None:
        raise HTTPException(404, "dataset not found")
    return item


# ---------------------------------------------------------------- papers / runs

class PaperQuery(BaseModel):
    q: str = ""
    year_min: int | None = None
    year_max: int | None = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


@app.post("/api/papers")
def papers(q: str = "", year_min: int | None = None, year_max: int | None = None,
           limit: int = 100, offset: int = 0, req: PaperQuery | None = None):
    """Paper-level listing. Accepts the same parameters as query strings **or** as a JSON
    body (previously a JSON body was silently ignored)."""
    if req is not None:
        q, year_min, year_max = req.q, req.year_min, req.year_max
        limit, offset = req.limit, req.offset
    return store.list_papers(q=q, year_min=year_min, year_max=year_max,
                             limit=limit, offset=offset)


@app.get("/api/runs")
def runs():
    return {"items": store.list_runs()}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    with __import__("app.core.db", fromlist=["db"]).db() as conn:
        row = conn.execute("SELECT * FROM harvest_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "run not found")
    out = dict(row)
    out["counters"] = json.loads(out.get("counters") or "{}")
    out["candidates"] = store.list_candidates(run_id)
    return out


# ---------------------------------------------------------------- themes

@app.get("/api/themes")
def themes_list(include_hidden: bool = False):
    # L-0225（B）：主题全量公开（疾病=kind 之一）；kind 字段随行返回
    # L-0250：隐藏主题默认过滤（include_hidden=1 供 My mining 管理区）
    items = themes.list_themes(include_hidden=include_hidden)
    for t in items:
        if isinstance(t.get("facets"), str):
            try:
                t["facets"] = json.loads(t["facets"])
            except Exception:
                t["facets"] = {}
    return {"items": items}


@app.post("/api/themes/{slug}/harvest")
def theme_harvest(slug: str, req: HarvestRequest | None = None):
    try:
        run_id = runner.start_harvest(slug, max_papers=(req.max_papers if req else None),
                                      queries=(req.queries if req else None))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc))
    return {"run_id": run_id}


class ThemePatch(BaseModel):
    name: str = ""
    description: str | None = None


@app.patch("/api/themes/{slug}")
def theme_update(slug: str, req: ThemePatch):
    """L-0250：改主题显示名（slug 不变）。"""
    out = themes.rename_theme(slug, req.name, req.description)
    if not out.get("ok"):
        raise HTTPException(404 if out.get("reason") == "not_found" else 400,
                            out.get("message") or "cannot update theme")
    return out


@app.post("/api/themes/{slug}/hide")
def theme_hide(slug: str):
    """L-0250：隐藏主题（软删；可 Unhide 恢复）。"""
    out = themes.set_hidden(slug, True)
    if not out.get("ok"):
        raise HTTPException(404, out.get("message") or "theme not found")
    return out


@app.post("/api/themes/{slug}/unhide")
def theme_unhide(slug: str):
    out = themes.set_hidden(slug, False)
    if not out.get("ok"):
        raise HTTPException(404, out.get("message") or "theme not found")
    return out


# ---------------------------------------------------------------- chat (SSE)

@app.post("/api/chat")
def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(400, "message required")
    from app.chat.agent import get_session

    def gen():
        try:
            session = get_session(req.session_id)
            result = session.turn(req.message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("chat failed")
            yield _sse({"type": "token", "text": f"The agent hit an error: {exc}"})
            yield _sse({"type": "meta", "session_id": req.session_id or ""})
            yield _sse({"type": "done"})
            return

        text = result.get("text") or ""
        for i in range(0, len(text), 8):
            yield _sse({"type": "token", "text": text[i:i + 8]})
        kind = result.get("kind")
        if kind == "records":
            yield _sse({"type": "records", "theme": result.get("theme"),
                        "total": result.get("total"), "records": result.get("records", [])[:6]})
        elif kind == "proposal":
            yield _sse({"type": "proposal", "proposal": result.get("proposal")})
        elif kind == "refusal":
            yield _sse({"type": "refusal", "refusal": result.get("refusal")})
        elif kind == "clarify":
            yield _sse({"type": "refusal", "refusal": result.get("refusal")})
        elif kind == "order":
            yield _sse({"type": "order", "order": result.get("order")})
        yield _sse({"type": "meta", "session_id": session.session_id, "kind": kind})
        yield _sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")


class ChatConfirm(BaseModel):
    session_id: str


@app.post("/api/chat/confirm")
def chat_confirm(req: ChatConfirm):
    from app.chat.agent import confirm_order

    result = confirm_order(req.session_id)
    return result


# ---------------------------------------------------------------- chat workbench (v1.4)

class WorkbenchSessionCreate(BaseModel):
    title: str = ""
    kind: str = "research"          # L-0234：research | ming


class WorkbenchMessage(BaseModel):
    message: str


@app.post("/api/chat/sessions")
def wb_session_create(req: WorkbenchSessionCreate):
    from app.chat import workbench

    return workbench.create_session(req.title, kind=req.kind)


@app.get("/api/chat/sessions")
def wb_sessions(limit: int = 30, kind: str | None = None):
    from app.chat import workbench

    kind = kind or None
    if kind not in (None, "research", "ming"):
        raise HTTPException(400, "kind must be research|ming")
    return {"sessions": workbench.list_sessions(limit, kind=kind)}


@app.get("/api/chat/sessions/{sid}")
def wb_session_detail(sid: str):
    from app.chat import workbench

    sess = workbench.get_session(sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    return {"session": sess, "messages": workbench.get_messages(sid)}


@app.post("/api/chat/sessions/{sid}/message")
def wb_message(sid: str, body: WorkbenchMessage):
    if not body.message.strip():
        raise HTTPException(400, "message required")
    from app.chat import workbench

    sess = workbench.get_session(sid)
    if sess is None:
        raise HTTPException(404, "session not found")

    def gen():
        if (sess.get("kind") or "research") == "ming":
            from app.chat import ming

            for ev in ming.run_turn(sid, body.message):
                yield _sse(ev)
            return
        steps: list[dict] = []
        for ev in workbench.run_turn(sid, body.message):
            if ev.get("event") in ("step", "progress"):
                steps.append(ev)
            yield _sse(ev)
        workbench.stamp_steps(sid, steps)

    return StreamingResponse(gen(), media_type="text/event-stream")


class VerifyCandidate(BaseModel):
    title: str = ""
    doi: str = ""
    pmid: str = ""
    landing_url: str = ""
    oa_url: str = ""
    is_preprint: bool = False


@app.post("/api/chat/verify-candidate")
def wb_verify_candidate(body: VerifyCandidate):
    """现场核验单篇候选论文：解析 PMC 全文 → 抽取 announced accessions → 检查链接。"""
    from app.chat import workbench

    return workbench.verify_candidate_live(body.model_dump())


@app.get("/api/orders/{order_id}")
def order_status(order_id: str):
    from app.chat import mining

    st = mining.order_status(order_id)
    if st is None:
        raise HTTPException(404, "order not found")
    return st


@app.get("/api/orders")
def orders_list(limit: int = 20):
    from app.core.db import db as _db
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM mining_requests WHERE kind='quick' "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return {"items": [dict(r) for r in rows]}


# ---------------------------------------------------------------- mining tasks (L-0209)

class MiningTaskCreate(BaseModel):
    theme: str = ""
    name: str = ""
    title: str = ""
    queries: list[str] | None = None
    papers_per_round: int | None = None
    cadence_hours: int | None = None
    session_id: str = ""
    note: str = ""
    theme_kind: str = ""          # L-0223/0225：'topic' = 任意研究主题（默认发布进公共目录）


@app.post("/api/mining_tasks")
def mining_task_create(req: MiningTaskCreate):
    from app.mining import tasks as task_engine

    out = task_engine.create_task(
        req.theme, title=req.title, name=req.name, queries=req.queries,
        papers_per_round=req.papers_per_round, cadence_hours=req.cadence_hours,
        session_id=req.session_id, note=req.note, theme_kind=req.theme_kind)
    if not out.get("ok"):
        raise HTTPException(400, out.get("message") or out.get("reason") or "cannot create task")
    return out


@app.get("/api/mining_tasks")
def mining_tasks_list(session_id: str = "", limit: int = 50):
    from app.mining import tasks as task_engine

    items = task_engine.list_tasks(session_id=session_id, limit=limit)
    return {"items": items, "total": task_engine.count_tasks(session_id=session_id)}


@app.get("/api/mining_tasks/{task_id}")
def mining_task_detail(task_id: str):
    from app.mining import tasks as task_engine

    t = task_engine.get_task(task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    return t


@app.post("/api/mining_tasks/{task_id}/{action}")
def mining_task_action(task_id: str, action: str):
    from app.mining import tasks as task_engine

    if action not in ("pause", "resume", "stop", "restart"):
        raise HTTPException(400, "unknown action")
    out = task_engine.set_task_state(task_id, action)
    if not out.get("ok"):
        raise HTTPException(400, out.get("message") or "action failed")
    return out


@app.get("/api/mining_tasks/{task_id}/records")
def mining_task_records(task_id: str, limit: int = 200, offset: int = 0):
    from app.mining import tasks as task_engine

    if task_engine.get_task(task_id) is None:
        raise HTTPException(404, "task not found")
    items = task_engine.records(task_id, limit=limit, offset=offset)
    counts = task_engine.task_record_counts(task_id)
    return {"items": items, "total": counts["total"]}


@app.get("/api/mining_tasks/{task_id}/export")
def mining_task_export(task_id: str, fmt: str = "jsonl"):
    from app.mining import tasks as task_engine

    name, ctype, text = task_engine.export_task(task_id, fmt=fmt)
    if not name:
        raise HTTPException(404, "task not found")
    return PlainTextResponse(text, media_type=ctype,
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


class MiningAnalyze(BaseModel):
    text: str = ""
    organism: str = ""
    tissue: str = ""
    history: list[dict] | None = None    # L-0233：追问历史 [{q, a}]（无状态）


@app.post("/api/mining/analyze")
def mining_analyze(req: MiningAnalyze):
    """意图优先（L-0223；L-0233 追问）：一句话 → 澄清问题或提议（只读，不建任何东西）。"""
    from app.mining import analyze as _analyze

    out = _analyze.analyze_request(req.text, organism=req.organism, tissue=req.tissue,
                                   history=req.history)
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return out


# ---------------------------------------------------------------- settings

@app.get("/api/settings")
def settings_get():
    from app.core.config import cfg
    return cfg.public_dict(with_sources=True)


@app.put("/api/settings")
def settings_put(req: SettingsPut):
    """只读（L-0208）：配置一律走文件，改后重启生效。"""
    raise HTTPException(
        403, "Settings are file-based now (L-0208): edit config/bioagentdb.toml "
             "or .env and restart. This endpoint is read-only.")


# ---------------------------------------------------------------- miner / audit

@app.get("/api/miner/status")
def miner_status():
    from app.core.config import cfg as _cfg
    # L-0230 修复 C-2：发布模式下任务矿机也是目录增长 → 计入；私有模式仍隐藏任务行
    wsql = "" if _cfg.bool("mining_task_publish_records", True) else "WHERE task_id='' "
    with __import__("app.core.db", fromlist=["db"]).db() as conn:
        procs = [dict(r) for r in conn.execute(
            f"SELECT * FROM miner_processes {wsql}"
            "ORDER BY started_at DESC LIMIT 30").fetchall()]
        for p in procs:
            p["counters"] = json.loads(p.get("counters") or "{}")
            # Liveness: a row marked 'running' whose heartbeat is older than 3 min is stale.
            if p.get("status") == "running" and p.get("last_heartbeat"):
                try:
                    hb = datetime.fromisoformat(p["last_heartbeat"])
                    if hb.tzinfo is None:
                        hb = hb.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - hb).total_seconds() > 180:
                        p["status"] = "stale"
                except ValueError:
                    pass
        audits = conn.execute(
            f"SELECT COUNT(*) FROM paper_audits {wsql}").fetchone()[0]
        by_theme = {}
        for r in conn.execute(
                f"SELECT theme, COUNT(*) n, SUM(duration_ms) total_ms "
                f"FROM paper_audits {wsql}GROUP BY theme").fetchall():
            by_theme[r["theme"]] = {"count": r["n"], "total_ms": r["total_ms"]}
    return {"processes": procs, "audits_total": audits, "by_theme": by_theme}


@app.get("/api/audits")
def audits(theme: str = "", paper_id: str = "", limit: int = 100, offset: int = 0):
    from app.core.config import cfg as _cfg
    # L-0231：与条件化端点口径统一（发布模式计入任务审计；私有模式隐藏）
    where = [] if _cfg.bool("mining_task_publish_records", True) else ["a.task_id = ''"]
    args = []
    if theme:
        where.append("a.theme = ?")
        args.append(theme)
    if paper_id:
        where.append("a.paper_id = ?")
        args.append(paper_id)
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    with __import__("app.core.db", fromlist=["db"]).db() as conn:
        rows = conn.execute(
            f"""SELECT a.*, p.title AS paper_title, p.pmid, p.doi, p.theme AS p_theme
                FROM paper_audits a LEFT JOIN papers p ON p.id = a.paper_id
                {wsql} ORDER BY a.created_at DESC LIMIT ? OFFSET ?""",
            args + [limit, offset]).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["findings"] = json.loads(d.get("findings") or "{}")
        d["records"] = json.loads(d.get("records") or "{}")
        d["timings_ms"] = json.loads(d.get("timings_ms") or "{}")
        items.append(d)
    return {"items": items}


@app.get("/api/query-stats")
def query_stats(theme: str = "", limit: int = 200):
    from app.core.config import cfg as _cfg
    # L-0231：与条件化端点口径统一（发布模式计入；私有模式隐藏）
    where = [] if _cfg.bool("mining_task_publish_records", True) else ["task_id = ''"]
    args = []
    if theme:
        where.append("theme = ?")
        args.append(theme)
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    with __import__("app.core.db", fromlist=["db"]).db() as conn:
        rows = conn.execute(
            f"SELECT theme, query, added, duration_ms, created_at FROM query_stats "
            f"{wsql} ORDER BY created_at DESC LIMIT ?", args + [limit]).fetchall()
    return {"items": [dict(r) for r in rows]}


# ---------------------------------------------------------------- manuscript (L-0312)

# 论文挂载：仅放行白名单产物（正文 HTML、PDF、figures/ 图），reviews/evidence 等内部目录不暴露。


def _image_media_type(name: str) -> str | None:
    return {"png": "image/png", "svg": "image/svg+xml", "jpg": "image/jpeg",
            "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}.get(
                name.rsplit(".", 1)[-1].lower())


@app.api_route("/paper", methods=["GET", "HEAD"], include_in_schema=False)
def paper_index():
    return RedirectResponse(url="/paper/")


@app.api_route("/paper/", methods=["GET", "HEAD"], include_in_schema=False)
def paper_view():
    f = PAPER_DIR / "paper.html"
    if not f.is_file():
        raise HTTPException(status_code=404, detail="manuscript not available")
    return FileResponse(str(f), media_type="text/html")


@app.api_route("/paper/paper.pdf", methods=["GET", "HEAD"], include_in_schema=False)
def paper_pdf():
    f = PAPER_DIR / "paper.pdf"
    if not f.is_file():
        raise HTTPException(status_code=404, detail="manuscript PDF not available")
    return FileResponse(str(f), media_type="application/pdf",
                        filename="paper.pdf", content_disposition_type="inline")


@app.api_route("/paper/figures/{name}", methods=["GET", "HEAD"], include_in_schema=False)
def paper_figure(name: str):
    figures = (PAPER_DIR / "figures").resolve()
    target = (figures / name).resolve()
    if target.parent != figures or not target.is_file():
        raise HTTPException(status_code=404, detail="figure not found")
    return FileResponse(str(target), media_type=_image_media_type(target.name))


# ---------------------------------------------------------------- static web

app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")