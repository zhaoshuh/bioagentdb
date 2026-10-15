"""调研分析（确定性）：能力矩阵（tool-evolution）+ 故事模式（story-patterns）。

输入: data/survey/{selection.jsonl, manifest.json, papers/*}
输出: data/survey/analysis.jsonl
      docs/paper/survey/tool-evolution.md（能力矩阵 + 样例）
      docs/paper/survey/story-patterns.md（故事模式 + 样例句）
用法: python scripts/survey_analyze.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SURVEY = ROOT / "data" / "survey"
PAPERS = SURVEY / "papers"
OUT_DIR = ROOT / "docs" / "paper" / "survey"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

CAPS: dict[str, str] = {
    "api": r"\bAPI\b|application programming interface|\bREST\b",
    "llm": r"\bLLM\b|large language model|\bGPT-?\d|chatbot|natural[- ]language (interface|query)",
    # 注意: \bMCP\b 必须大小写敏感（10.1074/mcp.* 是 Molecular & Cellular Proteomics 的 DOI 前缀）
    "mcp": r"Model Context Protocol|\bMCP\b",
    "graph": r"\bSPARQL\b|\bRDF\b|knowledge graph",
    "embeddings": r"embedding|vector (search|database)|semantic search|sentence[- ]transformer|faiss",
    "benchmark": r"benchmark|gold standard|ground truth|\bF1\b|precision and recall",
    "living": r"monthly|quarterly|updated (weekly|regularly)|continuously updat|living (resource|database)",
    "docker": r"\bDocker\b|containeriz",
    "cli": r"command[- ]line (tool|interface)|\bCLI\b",
    "web_ui": r"web (interface|server|portal)|user[- ]friendly interface|browser",
    "bulk": r"bulk download|batch download|FTP",
    "license": r"CC[ -]BY|creative commons|license",
    "limitations": r"\blimitation|future (work|direction|development)",
    "availability": r"available (at|from|via)|freely available|accessible (at|via)",
}

STORY: dict[str, str] = {
    "opener_present": r"\bwe (present|introduce|describe|report|develop)\b",
    "opener_here": r"\bhere,? we\b",
    "gap": r"however|remains? (a )?(major |key )?(challenge|gap)|no (existing|single)|lack(ing)? of",
    "contribution": r"we (provide|offer|enable|integrate|curate|collect)",
    "scale": r"\b\d[\d,]{2,}\s+(datasets?|samples?|papers?|records?|genes?|cells?|studies|entries|variants?)",
    "availability_phrase": r"available (at|from|via)|freely available|accessible (at|via)",
    "future": r"future (work|directions?|developments?)|will (be )?(continue|expand|update)",
}

_SENT_RE = re.compile(r"(?<=[.!?])\s+")

# MCP 特殊处理：Model Context Protocol 大小写不敏感；\bMCP\b 仅大写（排除 DOI 前缀）
_MCP_SENT_RE = re.compile(r"Model Context Protocol|\bMCP\b")

# Agent 论文的"AI 语境确认"：agent 一词需与 LLM/AI/自主/工具等语境同句出现
AGENT_AI_RE = re.compile(
    r"(llm|large language model|gpt-?\d|artificial intelligence|\bai\b|multi-?agent|agentic"
    r"|autonomous|chatbot|reinforcement learning|tool[- ]use)[^.]{0,90}\bagents?\b"
    r"|\bagents?\b[^.]{0,90}(llm|large language model|artificial intelligence|\bai\b|autonomous"
    r"|tool|workflow|reasoning|reason)",
    re.I,
)


def _cap_hit(cap: str, text: str) -> bool:
    if cap == "mcp":
        return bool(_MCP_SENT_RE.search(text))
    return bool(re.search(CAPS[cap], text, re.I))


def _cap_sentence(cap: str, text: str) -> str:
    if cap == "mcp":
        for sent in _SENT_RE.split(text):
            if 20 <= len(sent) <= 400 and _MCP_SENT_RE.search(sent):
                return sent.strip()[:300]
        return ""
    return first_sentence(text, CAPS[cap])


def text_for(entry: dict) -> str:
    fname = entry.get("file") or ""
    if not fname:
        return ""
    p = PAPERS / fname
    if not p.exists():
        return ""
    if fname.endswith(".xml"):
        raw = p.read_text(encoding="utf-8", errors="replace")
        text = _WS_RE.sub(" ", _TAG_RE.sub(" ", raw))
        return text[:250000]
    if fname.endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(p))
            parts = []
            for page in reader.pages[:50]:
                try:
                    parts.append(page.extract_text() or "")
                except Exception:  # noqa: BLE001
                    continue
            return _WS_RE.sub(" ", " ".join(parts))[:250000]
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _write_preserving_synthesis(path: Path, content: str) -> None:
    """重写 md 时保留既有的 `## Synthesis` 段（避免复跑覆盖人工综合内容）。"""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    marker = "\n## Synthesis"
    if marker in old:
        content = content.rstrip() + "\n" + old[old.index(marker):]
    path.write_text(content, encoding="utf-8")


def first_sentence(text: str, pattern: str) -> str:
    pat = re.compile(pattern, re.I)
    for sent in _SENT_RE.split(text):
        if 20 <= len(sent) <= 400 and pat.search(sent):
            return sent.strip()[:300]
    return ""


def main() -> None:
    rows = [json.loads(l) for l in (SURVEY / "selection.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    manifest = json.loads((SURVEY / "manifest.json").read_text(encoding="utf-8"))
    by_slug = {}
    for r in rows:
        from scripts.survey_download import slug_for  # noqa: PLC0415
        by_slug[slug_for(r)] = r

    analysis = []
    cap_counts: dict[str, Counter] = {"db": Counter(), "agent": Counter()}
    cap_examples: dict[str, dict[str, str]] = defaultdict(dict)
    story_counts: dict[str, Counter] = {"db": Counter(), "agent": Counter()}
    story_examples: dict[str, dict[str, str]] = defaultdict(dict)
    have_text = 0
    for slug, m in manifest.items():
        r = by_slug.get(slug) or {}
        text = text_for(m)
        if not text:
            continue
        have_text += 1
        kind = r.get("kind") or m.get("kind") or "db"
        ai_agent = bool(AGENT_AI_RE.search(text)) if kind == "agent" else None
        caps_present = []
        for cap in CAPS:
            if _cap_hit(cap, text):
                caps_present.append(cap)
                cap_counts[kind][cap] += 1
                cap_examples[cap].setdefault(kind, _cap_sentence(cap, text))
        story_present = []
        for name, pat in STORY.items():
            sent = first_sentence(text, pat)
            if sent:
                story_present.append(name)
                story_counts[kind][name] += 1
                story_examples[name].setdefault(kind, sent)
        analysis.append({"slug": slug, "kind": kind, "title": r.get("title") or m.get("title"),
                         "year": r.get("year"), "journal": r.get("journal"),
                         "route": m.get("route"), "caps": caps_present,
                         "ai_agent": ai_agent,
                         "story": story_present})
    (SURVEY / "analysis.jsonl").write_text(
        "\n".join(json.dumps(a, ensure_ascii=False) for a in analysis) + "\n", encoding="utf-8")

    n_db = sum(1 for a in analysis if a["kind"] == "db") or 1
    n_ag = sum(1 for a in analysis if a["kind"] == "agent") or 1
    n_ag_conf = sum(1 for a in analysis if a["kind"] == "agent" and a.get("ai_agent"))
    te = ["# Tool evolution — capability matrix (survey)", "",
          f"- analyzed with text: **{have_text}** (db {n_db} · agent {n_ag}，其中 AI 语境确认 {n_ag_conf})", "",
          "| capability | db (n) | agent (n) | db example | agent example |",
          "|---|---|---|---|---|"]
    for cap in CAPS:
        te.append(f"| {cap} | {cap_counts['db'][cap]} | {cap_counts['agent'][cap]} "
                  f"| {(cap_examples[cap].get('db') or '')[:90]} "
                  f"| {(cap_examples[cap].get('agent') or '')[:90]} |")
    (OUT_DIR / "tool-evolution.md").parent.mkdir(parents=True, exist_ok=True)
    _write_preserving_synthesis(OUT_DIR / "tool-evolution.md", "\n".join(te) + "\n")

    sp = ["# Story patterns — survey", "",
          f"- analyzed with text: **{have_text}**（agent 中 AI 语境确认 {n_ag_conf}）", "",
          "| pattern | db (n) | agent (n) | db example | agent example |",
          "|---|---|---|---|---|"]
    for name in STORY:
        sp.append(f"| {name} | {story_counts['db'][name]} | {story_counts['agent'][name]} "
                  f"| {(story_examples[name].get('db') or '')[:100]} "
                  f"| {(story_examples[name].get('agent') or '')[:100]} |")
    _write_preserving_synthesis(OUT_DIR / "story-patterns.md", "\n".join(sp) + "\n")
    print(f"analyzed: {have_text} papers · wrote analysis.jsonl + 2 md files")


if __name__ == "__main__":
    main()
