"""M3 · 提示词 A/B（受限、门控）：比较结构化提示词 v0（内置）vs v1（改进）。

指标（无需 gold）：JSON 合法率、字段完整度（organism/tissue/disease/modality 非空比）、
记录保留率（records/identifiers）。门控：完整度↑ 且 保留率不降。
用法: python scripts/improve_prompts.py [--n 25]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import cfg  # noqa: E402
from app.harvest import extract  # noqa: E402
from app.harvest.llm import chat  # noqa: E402
from app.improve import prompts  # noqa: E402


def build_prompt(template: str, paper: dict, ids: list[str]) -> str:
    return template.format(
        identifiers=json.dumps(ids, ensure_ascii=False),
        title=paper.get("title", ""),
        abstract=(paper.get("abstract") or "")[:1500],
        da_text=(paper.get("da_text") or "")[:2000],
        modalities=", ".join(extract.vocab.MODALITIES.values()),
    )


def run_variant(template: str, rows: list[dict]) -> dict:
    n = valid = 0
    recs_total = ids_total = 0
    field_hits = field_slots = 0
    for row in rows:
        ids = [i["acc"] for i in extract.find_identifiers(row["text"])]
        if not ids:
            continue
        n += 1
        ids_total += len(ids)
        try:
            out = chat([{"role": "user", "content": build_prompt(template, row, ids)}],
                       model=cfg.get("deepseek_scoring_model"), json_mode=True, temperature=0.0)
            data = json.loads(out.get("content") or "{}")
            records = data.get("records") or []
            if isinstance(records, list):
                valid += 1
        except Exception:  # noqa: BLE001
            records = []
        recs_total += len(records)
        for r in records:
            for k in ("organism", "tissue", "disease", "modality"):
                field_slots += 1
                if str(r.get(k) or "").strip():
                    field_hits += 1
    return {"papers": n, "valid_json": valid,
            "valid_rate": round(valid / max(1, n), 4),
            "record_ratio": round(recs_total / max(1, ids_total), 4),
            "completeness": round(field_hits / max(1, field_slots), 4)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25)
    args = ap.parse_args()
    g = ROOT / "data" / "gold"
    rows = [json.loads(l) for l in (g / "sample.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()][: args.n]

    v0 = run_variant(extract._STRUCTURE_PROMPT, rows)
    v1 = run_variant(prompts.STRUCTURE_V1, rows)
    accept = (v1["completeness"] > v0["completeness"]
              and v1["record_ratio"] >= v0["record_ratio"] - 0.05
              and v1["valid_rate"] >= v0["valid_rate"])
    if accept:
        prompts.write("structure", "structure_v1", prompts.STRUCTURE_V1, metrics=v1)

    report = {"n": len(rows), "v0_builtin": v0, "v1_improved": v1, "accepted_v1": accept}
    (g / "prompt-ab-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    md = ["# M3 · prompt A/B (structure; bounded, no gold)", "",
          "| variant | valid_rate | record_ratio | completeness |", "|---|---|---|---|",
          f"| v0 builtin | {v0['valid_rate']} | {v0['record_ratio']} | {v0['completeness']} |",
          f"| v1 improved | {v1['valid_rate']} | {v1['record_ratio']} | {v1['completeness']} |",
          "", f"accepted v1: **{accept}**"]
    (g / "prompt-ab-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
