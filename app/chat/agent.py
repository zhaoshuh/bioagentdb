"""Ask agent —— 疾病域检索/下单对话（任意疾病）。

约束：
- 域内 = 任何疾病；非疾病但生医 → 简短回答；非生医 → 拒绝
- 只读库；新增记录必须"下单"且经用户确认，执行者是真实矿机（确定性引擎）
- 任意主题可下单（疾病为其一）：确认时动态新建主题；记录默认发布进公共目录（L-0225）
- 回答结构化（记录卡片 + 证据），不使用 markdown 记号
"""

from __future__ import annotations

import json
import logging
import re
import uuid

from app.catalog import store
from app.chat import mining
from app.core.config import cfg
from app.core.utils import norm_space
from app.harvest import themes as themes_mod
from app.harvest.llm import chat, chat_json

logger = logging.getLogger(__name__)

# 三种重点病的别名兜底（LLM 漏判/不可用时用；重点病只是"已在库、正在挖"）
_SEED_ALIASES: list[tuple[str, str]] = [
    ("alzheimers-disease",
     r"alzheim|dementia|amyloid|\btau\b|\bmci\b|cognitive impairment|down syndrome"),
    ("als", r"\bals\b|amyotrophic|motor neuron|\bmnd\b|sod1|tdp-?43|c9orf72|frontotemporal"),
    ("parkinsons-disease", r"parkinson|lewy|synuclein|lrrk2|\bgba\b|dopamin"),
]

_YES = re.compile(r"^\s*(yes|yep|yeah|y|ok|okay|sure|go|start|do it|proceed|mine|dig|"
                  r"挖|挖吧|好|好的|可以|行|开始|确认)\s*[.!。]?\s*$", re.I)
_MINING_HINT = re.compile(r"\bmine\b|\bdig\b|\bharvest\b|\bfetch\b|挖|采集|补充", re.I)
# 看起来是"要数据"但没有点名疾病 → 需要澄清；L-0231：词干加 \w*、收缩歧义词（避免 brainstorm/database 误报）
_OMICS_HINT = re.compile(r"\b(omics|datasets?|data|samples?|cohort|rna|scrna|snrna|scatac|"
                         r"spatial|seq|sequenc\w*|transcriptom\w*|proteom\w*|metabolom\w*|"
                         r"lipidom\w*|epigenom\w*|genom\w*|microbiom\w*|virom\w*|metagenom\w*|"
                         r"methylat\w*|brain|csf|cortex|tissue|biops\w*|atlas)\b", re.I)
# 明显是"在要数据"（区别于"这是啥"的定义类问题）
_DATA_HINT = re.compile(r"\b(datasets?|data|samples?|cohort|studies|study|accessions?|find|"
                        r"search|show me|looking for|download|atlas|available)\b", re.I)

_ANSWER_SYSTEM = (
    "You are the assistant of BioAgentDB, a catalog of public omics datasets for diseases. "
    "Answer biomedical or omics questions in at most 3 short sentences of plain text. "
    "No markdown, no bullet lists, no headings. Never invent dataset accessions or numbers. "
    "If the question is not biomedical, say briefly that you only help with biomedical topics."
)


def _intent_prompt() -> str:
    opts = themes_mod.theme_options()
    lines = "\n".join(f'  - {t["slug"]}: {t["name"]}' for t in opts) or "  (none yet)"
    return f"""You are the retrieval agent for BioAgentDB, a catalog of public omics datasets
for DISEASES. It currently holds these disease themes:
{lines}

Classify the user's message. Return JSON ONLY:
{{
 "kind": "disease" | "biomedical" | "other" | "chatty",
 "disease": "canonical disease name if a specific disease is named, else ''",
 "topic": "short topic phrase for a NON-disease biomedical/omics request (e.g. \"gut virome metagenomics\"), else ''",
 "aliases": ["alternative names of that disease"],
 "matched_theme_slug": "one of the existing slugs above if the disease matches it, else ''",
 "queries": ["Europe PMC queries for this disease, each ending with AND HAS_DATA:Y"],
 "query": "short english search terms (disease/tissue/technique)",
 "modality": ["code", ...],
 "organism": "" | "Homo sapiens" | "Mus musculus",
 "tissue": "" | "brain" | "csf" | "blood",
 "wants_mining": false,
 "which_disease": false,
 "reason": "short"
}}
Rules:
- kind="disease" when the message names a specific human disease or condition. ANY disease
  is in scope, not only the existing themes above; never refuse a disease for being new.
- kind="biomedical" when it is a biomedical/omics question but names no specific disease.
- kind="other" for anything unrelated to biology/medicine (weather, stocks, code, politics).
- kind="chatty" for greetings and small talk.
- Set which_disease=true when it is clearly a DATA request (datasets/samples/cohort) that
  names no disease. Provide "queries" only when kind="disease"."""


def _seed_alias(message: str) -> str:
    t = (message or "").lower()
    for slug, pattern in _SEED_ALIASES:
        if re.search(pattern, t):
            return slug
    return ""


class ChatSession:
    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or uuid.uuid4().hex[:16]
        self.history: list[dict] = []
        self.pending: dict | None = None
        self.last_order: str | None = None

    # ------------------------------------------------------------- intent
    def parse(self, message: str) -> dict:
        data = {}
        try:
            data = chat_json([
                {"role": "system", "content": _intent_prompt()},
                *self.history[-6:],
                {"role": "user", "content": message},
            ], model=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("intent parse failed: %s", exc)
        data = data if isinstance(data, dict) else {}

        kind = str(data.get("kind") or "").strip().lower()
        disease = norm_space(data.get("disease"))
        topic = norm_space(data.get("topic"))
        matched = str(data.get("matched_theme_slug") or "").strip()

        slug, name, is_new = self._resolve(disease, matched, message)
        if kind not in ("disease", "biomedical", "other", "chatty"):
            # 规则兜底：命中重点病别名 → disease；否则看生医/闲聊特征
            if slug:
                kind = "disease"
            elif re.search(r"[?？]", message) and _OMICS_HINT.search(message):
                kind = "biomedical"
            else:
                kind = "biomedical"
        if kind == "disease" and not slug:
            kind = "biomedical"  # 说是有病但没解析出病名 → 当作生医处理（澄清/简答）

        aliases = [a for a in (data.get("aliases") or []) if isinstance(a, str)][:8]
        queries = [q for q in (data.get("queries") or []) if isinstance(q, str)][:10]
        modality = [m for m in (data.get("modality") or []) if isinstance(m, str)][:3]
        which_disease = bool(data.get("which_disease"))
        # 仅当 LLM 不可用时兜底：像"在要数据"却没点名疾病 → 澄清
        if not data and _OMICS_HINT.search(message) and _DATA_HINT.search(message):
            which_disease = True
        return {
            "kind": kind,
            "slug": slug,
            "name": name,
            "topic": topic,
            "is_new": is_new,
            "disease": disease or name,
            "aliases": aliases,
            "queries": queries,
            "query": norm_space(data.get("query")) or norm_space(message),
            "modality": modality,
            "organism": norm_space(data.get("organism")),
            "tissue": norm_space(data.get("tissue")),
            "wants_mining": bool(data.get("wants_mining")) or bool(_MINING_HINT.search(message)),
            "which_disease": which_disease,
            "reason": norm_space(data.get("reason")),
        }

    def _resolve(self, disease: str, matched: str, message: str) -> tuple[str, str, bool]:
        """病名 → (slug, 展示名, 是否新主题)。"""
        try:
            existing = {t["slug"]: t for t in themes_mod.list_themes()}
        except Exception:  # noqa: BLE001
            existing = {}
        if matched and matched in existing:
            return matched, existing[matched]["name"], False
        seed = _seed_alias(message)
        if seed and seed in existing:
            return seed, existing[seed]["name"], False
        if disease:
            s = themes_mod.slugify(disease)
            if s in existing:
                return s, existing[s]["name"], False
            for slug, t in existing.items():
                if themes_mod.slugify(t.get("name") or "") == s:
                    return slug, t["name"], False
            return s, disease, True
        return "", "", True

    # ------------------------------------------------------------- turn
    def turn(self, message: str) -> dict:
        message = (message or "").strip()
        if not message:
            return {"kind": "text", "text": "What data are you looking for?"}
        self.history.append({"role": "user", "content": message})

        # 1) 已有提案且用户确认 → 下单
        if self.pending and _YES.match(message):
            return self._start_order()
        self.pending = None  # 新话题到来，作废旧提案

        intent = self.parse(message)
        self.history.append({"role": "assistant",
                             "content": f"[intent] {json.dumps(intent, ensure_ascii=False)}"})
        kind = intent["kind"]

        # 2a) 计数型追问：LLM 可能把 "how many are there?" 判成 biomedical；
        # 只要没有新的疾病/主题，就用上一轮检索结果或目录总数作答（L-0393）。
        count_q = re.search(r"\b(how many|how much|count|number of)\b", message, re.I)
        follow_shape = (len(message.split()) <= 4
                        or bool(re.search(r"\b(there|them|those|these|that|results?)\b",
                                          message, re.I)))
        if (count_q and follow_shape and not intent.get("disease")
                and kind in ("chatty", "biomedical")):
            s = store.stats()
            return {
                "kind": "text",
                "text": f"The catalog currently holds {s['datasets_total']:,} dataset "
                        f"records ({s['datasets_verified']:,} verified) from "
                        f"{s['papers_total']:,} papers ({s['papers_with_data']:,} with data).",
            }

        # 2) 闲聊 / 追问
        if kind == "chatty":
            if re.search(r"\b(how many|how much|count|number of)\b", message, re.I):
                s = store.stats()
                return {
                    "kind": "text",
                    "text": f"The catalog currently holds {s['datasets_total']:,} dataset "
                            f"records ({s['datasets_verified']:,} verified) from "
                            f"{s['papers_total']:,} papers ({s['papers_with_data']:,} with data).",
                }
            return {
                "kind": "text",
                "text": "I'm the BioAgentDB retrieval agent. I search a catalog of public omics "
                        "datasets for any disease, answer short biomedical questions, and can "
                        "order a fresh mining round when something is missing. What are you "
                        "looking for?",
            }

        # 3) 非生医 → 拒绝
        if kind == "other":
            return self._refusal(intent)

        # 4) 生医但非疾病 → 挖掘意图走"任意主题"提案；否则澄清/简答（L-0225 B）
        if kind == "biomedical":
            if intent["wants_mining"] and _OMICS_HINT.search(message):
                return self._propose_topic(intent, message)
            if intent["which_disease"] and _OMICS_HINT.search(message):
                return self._clarify()
            return self._answer(message)

        # 5) 疾病 → 查库
        result = self._search(intent)
        items = result["items"]
        label = intent["name"]
        if items:
            matched = (f" matching \u201c{intent['query']}\u201d"
                       if intent["query"] and not result.get("relaxed") else "")
            return {
                "kind": "records",
                "theme": intent["slug"],
                "text": f"{result['total']} paper(s) with data for {label}{matched}"
                        + ". Showing the top matches with their evidence and data links.",
                "records": items,
                "total": result["total"],
            }

        # 6) 库内无 → 提案（需确认才下单；新疾病确认时动态建主题）
        budget = cfg.int("mining_task_papers_per_round", 200)
        cadence = cfg.int("mining_task_cadence_hours", 24)
        queries = intent["queries"] or self._default_queries(intent["name"], intent["aliases"])
        self.pending = {"theme": intent["slug"], "name": label, "budget": budget,
                        "papers_per_round": budget, "cadence_hours": cadence,
                        "queries": queries, "query": intent["query"]}
        papers, records = mining._counts(intent["slug"])
        if intent["is_new"]:
            lead = (f"{label} is not in the catalog yet. I can create a theme for it and keep a "
                    f"mining task running: {budget} papers per round, first until the literature "
                    f"is exhausted")
        else:
            lead = (f"Nothing in the catalog matches yet for {label}. The catalog currently holds "
                    f"{records} record(s) for this disease ({papers} papers read). I can keep a "
                    f"mining task running: {budget} papers per round, first until the literature "
                    f"is exhausted")
        return {
            "kind": "proposal",
            "theme": intent["slug"],
            "text": lead + f"; after that it patrols for new papers every {cadence}h. "
                    "Verified records are published to the public catalog.",
            "proposal": {
                "theme": intent["slug"],
                "theme_label": label,
                "budget": budget,
                "papers_per_round": budget,
                "cadence_hours": cadence,
                "query": intent["query"],
                "is_new_theme": intent["is_new"],
                "in_scope": True,
            },
        }

    # ------------------------------------------------------------- confirm
    def _propose_topic(self, intent: dict, message: str) -> dict:
        """任意主题的挖掘提案（L-0225 B；L-0230 修复 F1：主题取 LLM 短词组/首分句，绝不用整句）。"""
        raw = norm_space(intent.get("topic") or intent.get("query") or "")
        if not raw:
            raw = norm_space(re.split(r"[;；,，\n]|(?<=[.!?])\s+", message)[0])
        _prefix = re.compile(r"^(?:i\s+(?:want|need|would like)|please|find|get|search|"
                             r"show me|looking for|give me)\b\W*|^(?:mine|dig|fetch)\s+"
                             r"(?:me|us|some|a|an|the)\b\W*", re.I)
        _stop = {"data", "dataset", "datasets", "samples", "sample", "studies", "study"}
        cand = _prefix.sub("", raw).strip()
        if len(cand) < 4 or cand.lower() in _stop:
            cand = _prefix.sub("", norm_space(message)).strip()   # 兜底：整句（同样剥前缀）
        topic = cand.strip()
        if len(topic) > 80:
            topic = topic[:80].rsplit(" ", 1)[0].strip() or topic[:80]
        topic = topic.strip(" -\u2013\u2014:;,.")
        # V-R52-1（L-0236）：剥冠词前缀后再探一次——"mine/dig/fetch/get me + data 类" → 澄清
        _tail = topic
        for _ in range(4):
            _t2 = re.sub(r"^(?:(?:mine|dig|fetch|harvest|get|give|find|search|show)\b"
                         r"|me\b|us\b)[\s:,-]*", "", _tail, flags=re.I).strip()
            if _t2 == _tail:
                break
            _tail = _t2
        if len(_tail) < 4 or _tail.lower() in {"data", "dataset", "datasets", "samples", "sample",
                                               "studies", "study", "public data", "some data"}:
            return self._clarify()
        if (not topic or len(topic) < 4
                or topic.lower() in {"data", "dataset", "datasets", "samples", "sample",
                                     "studies", "study", "public data", "some data"}):
            # 纯"要数据"没有主题 → 走澄清（给主题 chips），不硬造主题
            return self._clarify()
        slug = themes_mod.slugify(topic)
        try:
            existing = {t["slug"] for t in themes_mod.list_themes()}
        except Exception:  # noqa: BLE001
            existing = set()
        budget = cfg.int("mining_task_papers_per_round", 200)
        cadence = cfg.int("mining_task_cadence_hours", 24)
        queries = intent.get("queries") or self._default_queries(topic, [])
        self.pending = {"theme": slug, "name": topic, "budget": budget,
                        "papers_per_round": budget, "cadence_hours": cadence,
                        "queries": queries, "query": intent.get("query") or message[:120],
                        "theme_kind": "topic"}
        return {
            "kind": "proposal",
            "theme": slug,
            "text": f"I can keep a mining task running on \"{topic}\": {budget} papers per "
                    f"round, first until the literature is exhausted; then it patrols for new "
                    f"papers every {cadence}h. Verified records are published to the public "
                    f"catalog.",
            "proposal": {"theme": slug, "theme_label": topic, "budget": budget,
                          "papers_per_round": budget, "cadence_hours": cadence,
                          "query": intent.get("query"), "is_new_theme": slug not in existing,
                          "in_scope": True, "theme_kind": "topic"},
        }

    def _start_order(self) -> dict:
        p = self.pending or {}
        self.pending = None
        task = mining.start_task(p.get("theme", ""),
                                 papers_per_round=p.get("papers_per_round"),
                                 cadence_hours=p.get("cadence_hours"),
                                 name=p.get("name", ""), queries=p.get("queries"),
                                 session_id=self.session_id, note=p.get("query", ""),
                                 theme_kind=p.get("theme_kind", ""))
        if not task.get("ok"):
            return {"kind": "refusal", "text": task.get("message", "Cannot start mining.")}
        self.last_order = task["task_id"]
        return {
            "kind": "order",
            "order_id": task["task_id"],
            "task_id": task["task_id"],
            "theme": task["theme"],
            "text": f"Mining task started for {task['theme_label']}: {task['papers_per_round']} "
                    f"papers per round until the literature is exhausted, then a patrol every "
                    f"{task['cadence_hours']}h. Verified records are published to the public "
                    f"catalog \u2014 open My mining to watch the run.",
            "order": {"order_id": task["task_id"], "task_id": task["task_id"],
                      "theme": task["theme"], "theme_label": task["theme_label"],
                      "budget": task["papers_per_round"],
                      "papers_per_round": task["papers_per_round"],
                      "cadence_hours": task["cadence_hours"]},
        }

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _default_queries(name: str, aliases: list[str]) -> list[str]:
        names = [name] + [a for a in aliases if a]
        qs: list[str] = []
        for n in names[:3]:
            qs.append(f'"{n}" AND HAS_DATA:Y')
        for n in names[:2]:
            qs.append(f'"{n}" AND ("single-cell" OR "single-nucleus" OR "spatial") AND HAS_DATA:Y')
        if name:
            qs.append(f'"{name}" AND (proteomics OR transcriptomics OR genomics) AND HAS_DATA:Y')
        return list(dict.fromkeys(qs))

    def _answer(self, message: str) -> dict:
        """生医但非疾病：1–3 句简短回答（不检索、不下单）。"""
        text = ""
        try:
            out = chat([{"role": "system", "content": _ANSWER_SYSTEM},
                        {"role": "user", "content": message}],
                       temperature=0.2, timeout=60)
            text = norm_space(out.get("content"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("biomedical answer failed: %s", exc)
        if not text:
            text = ("I can answer short biomedical questions and search the catalog for disease "
                    "omics datasets. Which disease are you interested in?")
        return {"kind": "answer", "text": text[:600]}

    def _clarify(self) -> dict:
        """要数据但没点名主题：先问清挖什么（疾病只是主题之一；L-0231 候选全量）。"""
        try:
            allt = themes_mod.list_themes()
            allt.sort(key=lambda t: (-(t.get("records_count") or 0), t.get("name") or ""))
            allowed = [{"slug": t["slug"], "label": t["name"]} for t in allt[:6]]
        except Exception:  # noqa: BLE001
            allowed = []
        return {
            "kind": "clarify",
            "text": "Happy to help \u2014 which topic should I search? Name a disease, a biological "
                    "process or a technique \u2014 or pick one of the topics already in the catalog.",
            "refusal": {"clarify": True, "allowed": allowed, "nearest": "",
                        "reason": "no disease named"},
        }

    def _refusal(self, intent: dict) -> dict:
        return {
            "kind": "refusal",
            "text": "That is outside what I do. BioAgentDB covers disease and biomedical omics "
                    "data \u2014 ask about any disease, or a biomedical topic, and I will help.",
            "refusal": {
                "allowed": [],
                "nearest": "",
                "reason": intent.get("reason", ""),
            },
        }

    def _search(self, intent: dict) -> dict:
        """Progressive relaxation: a verbose LLM query (e.g. 'Alzheimer proteomics
        brain CSF datasets') must not turn a populated theme into 'no matches'.
        Try the full query, then theme+modality, then theme, then disease name."""
        modality = (intent["modality"] or [""])[0] if intent["modality"] else ""
        theme = "" if intent["is_new"] else intent["slug"]
        attempts = [
            dict(q=intent["query"], theme=theme, modality=modality,
                 organism=intent["organism"]),
            dict(q="", theme=theme, modality=modality, organism=intent["organism"]),
            dict(q="", theme=theme, modality=modality, organism=""),
            dict(q="", theme=theme, modality="", organism=""),
            dict(q=intent.get("disease") or "", theme="", modality=modality, organism=""),
            dict(q=intent.get("name") or "", theme="", modality="", organism=""),
        ]
        seen: set[tuple] = set()
        last: dict | None = None
        for i, kw in enumerate(attempts):
            key = tuple(sorted(kw.items()))
            if key in seen:
                continue
            seen.add(key)
            last = store.list_records(limit=6, offset=0, sort="year", **kw)
            if last["items"]:
                last["relaxed"] = i > 0
                return last
        if last is not None:
            last["relaxed"] = True
        return last or store.list_records(limit=6, offset=0, sort="year", theme=theme)


_sessions: dict[str, ChatSession] = {}


def get_session(session_id: str | None = None) -> ChatSession:
    if session_id and session_id in _sessions:
        return _sessions[session_id]
    s = ChatSession(session_id)
    _sessions[s.session_id] = s
    return s


def confirm_order(session_id: str) -> dict:
    """显式确认（按钮）——与输入 yes 等价。"""
    s = get_session(session_id)
    if not s.pending:
        return {"kind": "text", "text": "There is no pending mining proposal."}
    return s._start_order()
