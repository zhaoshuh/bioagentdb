"""Ming 挖掘对话（L-0234/L-0235）：与 Research 同构的自由对话。

每轮由 LLM 裁定"继续追问 / 直接提议"（携全对话 + 已提取画像）；问不问、问多少由
agent 判断（**无固定轮数**；`mining_ming_max_rounds` 仅为防跑飞安全阀）。
用户回 any/skip/直接挖 → 立即收敛；LLM 失败 → 确定性兜底（缺 organism/技术各问一次后收敛）。

事件流（SSE data 行，JSON）：token / proposal / error / done。
消息落库（chat_messages；meta.kind = clarify | proposal），历史会话可回放。
"""

from __future__ import annotations

import json
import logging
import re

from app.chat import workbench as wb
from app.core.config import cfg
from app.core.utils import norm_space
from app.harvest import themes as themes_mod
from app.harvest.llm import chat_json

logger = logging.getLogger(__name__)

MAX_ROUNDS = cfg.int("mining_ming_max_rounds", 12)   # 防跑飞安全阀默认（L-0235）；实际逐轮读配置


def _max_rounds() -> int:
    """逐轮读配置：改 `mining_ming_max_rounds` 免重启生效（R53-M P3）。"""
    try:
        return int(cfg.int("mining_ming_max_rounds", MAX_ROUNDS))
    except Exception:  # noqa: BLE001
        return MAX_ROUNDS

_SKIP_RE = re.compile(
    r"^\s*(?:any|anything|skip|skip\s+it|none|no\s+preference|whatever|don'?t\s+care|n/?a|"
    r"都行|随便|都可以|无所谓|你定|直接挖|直接开|直接开始|就这样|开挖|挖吧|"
    r"mine\s+it|mine\s+it\s+now|just\s+mine(\s+it)?|start\s+mining|go\s+ahead)\s*"
    r"[.!。;；]*\s*$", re.I)

_ORG_RE = re.compile(
    r"human|homo sapiens|mouse|mice|mus musculus|rat|patient|clinical|drosophila|zebrafish|"
    r"soil|marine|ocean|environment|plant", re.I)
_TECH_RE = re.compile(
    r"metagenom|16s|shotgun|single[- ]cell|scrna|snrna|scatac|spatial|transcriptom|proteom|"
    r"metabolom|lipidom|epigenom|methylat|sequenc|rna[- ]?seq|microarray|atac|multi[- ]?omics",
    re.I)

_TOPIC_PREFIX = re.compile(
    r"^(?:i\s+(?:want|need|would like)|please|find|get|search|show me|looking for|give me)\b\W*|"
    r"^(?:mine|dig|fetch)\s+(?:me|us|some|a|an|the)\b\W*", re.I)
_TOPIC_TAIL = re.compile(
    r"^(?:(?:mine|dig|fetch|harvest|get|give|find|search|show)\b|me\b|us\b)[\s:,-]*", re.I)
_TOPIC_STOP = {"data", "dataset", "datasets", "samples", "sample", "studies", "study",
               "public data", "some data", "any data", "anything", "any", "all",
               "it", "this", "that", "stuff"}

_SYSTEM = """You are "Ming", the intake assistant for BioAgentDB's mining mode.
The user wants a background task that mines papers for omics datasets about a topic.
Hold a short conversation to sharpen the goal, then hand over a mining proposal.

Rules:
- Ask ONLY questions whose answers materially change what gets searched or mined.
- At most 2 questions per reply; usually 1 is enough. Never repeat an answered question.
- Stop as soon as the goal is mineable — topic + optionally organism/system + technique/omics
  + scope hints. "Mineable" does NOT require exhaustive detail; when in doubt after a couple
  of answers, conclude.
- If the user says any / skip / whatever / "just mine" or similar, conclude immediately
  (ready=true).
- Plain English, 1-4 short sentences, friendly and concise. Never use option lists or
  multiple-choice formatting.
- Never invent dataset accessions or numbers; this step is planning only.

Return JSON only:
{
 "ready": false,             // true when you can propose the mining task now
 "reply": "your next message to the user",
 "topic": "short noun phrase for the mining theme, e.g. 'human gut virome metagenomics'",
 "organism": "", "tissue": "",
 "keywords": ["extra facets: cohorts, sample sizes, populations, data types ..."],
 "modalities": ["technique/omics mentioned, e.g. metagenomics, 16s, single-cell"],
 "questions": []             // only if ready=false: the questions you asked
}"""


def _convo(sid: str) -> list[dict]:
    out: list[dict] = []
    for m in wb.get_messages(sid):
        role = m.get("role")
        content = str(m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content[:1500]})
    return out


def _decide(history: list[dict], profile: dict, state: dict) -> dict | None:
    """LLM 裁定"继续问/可提议"；失败返回 None（调用方走确定性兜底）。"""
    ctx = {"extraction": {
        "topic": profile.get("topic") or "",
        "organism": profile.get("organism") or "",
        "tissue": profile.get("tissue") or "",
        "keywords": profile.get("keywords") or [],
        "modalities": profile.get("modalities") or [],
    }, "clarify_rounds": int(state.get("rounds") or 0)}
    try:
        data = chat_json([
            {"role": "system", "content": _SYSTEM + "\n\nState so far: "
             + json.dumps(ctx, ensure_ascii=False)},
            *history[-16:],
        ], model=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ming decide failed: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _fallback(goal_text: str, asked: set[str]) -> dict:
    """LLM 不可用时的确定性兜底：缺 organism/技术各问一次，随后收敛。"""
    if "organism" not in asked and not _ORG_RE.search(goal_text):
        return {"ready": False, "questions": ["organism"],
                "reply": "Which organism or system should I focus on? (reply \"any\" to skip)"}
    if "technique" not in asked and not _TECH_RE.search(goal_text):
        return {"ready": False, "questions": ["technique"],
                "reply": "Any technique or omics emphasis, e.g. metagenomics, 16S, "
                         "single-cell? (reply \"any\" to skip)"}
    return {"ready": True, "reply": "Got it — here is the mining plan."}


def _merge_profile(profile: dict, decision: dict) -> dict:
    out = dict(profile)
    for key in ("organism", "tissue"):
        val = norm_space(decision.get(key))
        if val:
            out[key] = val[:80]
    kws = [norm_space(k)[:80] for k in (decision.get("keywords") or [])
           if isinstance(k, str) and norm_space(k)]
    old = [str(x) for x in (out.get("keywords") or [])]
    for k in kws:
        if k not in old:
            old.append(k)
    out["keywords"] = old[:8]
    mods = [norm_space(m)[:40] for m in (decision.get("modalities") or [])
            if isinstance(m, str) and norm_space(m)]
    oldm = [str(x) for x in (out.get("modalities") or [])]
    for m in mods:
        if m not in oldm:
            oldm.append(m)
    out["modalities"] = oldm[:6]
    return out


def _clean_topic(raw: str) -> str:
    t = norm_space(raw)
    if not t:
        return ""
    t = re.split(r"[;；,，\n]|(?<=[.!?])\s+", t)[0].strip()
    t = _TOPIC_PREFIX.sub("", t).strip()
    if len(t) > 80:
        t = t[:80].rsplit(" ", 1)[0].strip() or t[:80]
    t = t.strip(" -\u2013\u2014:;,.")
    # R53 修复（C-2/F-R53-1）：剥动词后再探一次——只剩 data 类/代词 → 视作无主题
    tail = t
    for _ in range(4):
        nxt = _TOPIC_TAIL.sub("", tail).strip()
        if nxt == tail:
            break
        tail = nxt
    if len(tail) < 4 or tail.lower() in _TOPIC_STOP:
        return ""
    return t


def _proposal(topic: str, profile: dict) -> dict:
    slug = themes_mod.slugify(topic)
    existing = themes_mod.get_theme(slug)
    if existing:
        kind = existing.get("kind") or "disease"
        label = existing.get("name") or topic
    else:
        kind, label = "topic", topic
    qprofile = {"topic": topic, "organism": profile.get("organism") or "",
                "tissue": profile.get("tissue") or "",
                "keywords": profile.get("keywords") or [],
                "modalities": profile.get("modalities") or []}
    # v1.6（L-0292）：未知知识（赛事/项目/平台等）→ 联网查证 → queries 改写
    research: dict | None = None
    try:
        from app.chat import web_research
        if (web_research.enabled()
                and web_research.looks_like_unknown_topic(topic)):
            research = web_research.brief(topic)
    except Exception:  # noqa: BLE001
        research = None
    try:
        queries = wb.expand_queries(qprofile, {}, research=research)[:8]
    except Exception:  # noqa: BLE001
        queries = [f'"{topic}" AND HAS_DATA:Y']
    # 守卫（L-0295）：即使 expand_queries 退化/旧 seam，查证词也必须并入（不静默丢失）
    if research and (research.get("search_terms") or []):
        have = {str(q).casefold() for q in queries}
        head = [str(t) for t in research["search_terms"] if str(t).casefold() not in have]
        if head:
            queries = (head + queries)[:8]
    out = {"theme": slug, "theme_label": label, "kind": kind, "existing": bool(existing),
           "queries": queries,
           "papers_per_round": cfg.int("mining_task_papers_per_round", 200),
           "cadence_hours": cfg.int("mining_task_cadence_hours", 24)}
    if research:
        out["research"] = {"summary": research.get("summary") or "",
                           "search_terms": research.get("search_terms") or [],
                           "sources": research.get("sources") or []}
    return out


def run_turn(sid: str, message: str):
    """Ming 一轮对话：yield SSE 事件（token / proposal / done）。

    R53-C C-1：先流式输出、**正常跑完才落库**——客户端 Stop/断连导致生成器被关闭时，
    本轮不落库、state 不推进（回放不会出现"停掉了却又冒出回复"）。
    """
    message = (message or "").strip()
    if not message:
        yield {"event": "error", "message": "message required"}
        yield {"event": "done"}
        return

    state = wb._state(sid)
    profile = dict(state.get("profile") or {})
    rounds = int(state.get("rounds") or 0)
    asked = set(state.get("asked") or [])

    history = _convo(sid)
    user_msgs = [m["content"] for m in history if m["role"] == "user"]
    goal = state.get("goal") or (user_msgs[0] if user_msgs else message)
    convo = history + [{"role": "user", "content": message}]
    user_msgs = user_msgs + [message]

    # ① 用户叫停 → 立即收敛；② 防跑飞安全阀（L-0235）
    forced = (len(user_msgs) >= 2 and _SKIP_RE.match(message)) or rounds >= _max_rounds()
    if forced:
        decision: dict = {"ready": True, "reply": "OK — here is the mining plan."}
    else:
        decision = _decide(convo, profile, state) or _fallback(
            " \n ".join(user_msgs), asked)

    profile = _merge_profile(profile, decision)
    topic = (_clean_topic(str(decision.get("topic") or ""))
             or _clean_topic(str(profile.get("topic") or ""))
             or _clean_topic(str(goal)))
    ready = bool(decision.get("ready"))
    if ready and not topic:
        ready = False
        decision = {"ready": False, "questions": ["topic"],
                    "reply": "What topic should I mine? A few keywords are enough."}
    reply = norm_space(decision.get("reply") or "")[:800] or (
        "Here is the mining plan." if ready else "Tell me a bit more about what to mine.")
    if topic:
        profile["topic"] = topic

    payload: dict = {}
    if ready:
        payload = _proposal(topic, profile)

    for i in range(0, len(reply), 8):
        yield {"event": "token", "text": reply[i:i + 8]}

    # —— 流式输出完成（未被断连关闭）→ 落库 + 推进 state ——
    wb.add_message(sid, "user", message)
    if ready:
        wb.add_message(sid, "assistant", reply,
                       meta={"kind": "proposal", "proposal": payload})
    else:
        rounds += 1
        qs = [str(q).strip() for q in (decision.get("questions") or []) if str(q).strip()]
        for q in qs:
            if q.lower() in ("organism", "technique", "topic"):
                asked.add(q.lower())
        wb.add_message(sid, "assistant", reply, meta={"kind": "clarify", "questions": qs})

    wb._set_state(sid, {"goal": str(goal)[:300], "profile": profile, "rounds": rounds,
                        "asked": sorted(asked), "last_ready": ready})

    if ready:
        yield {"event": "proposal", "proposal": payload}
    yield {"event": "done"}
