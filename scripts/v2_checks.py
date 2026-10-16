"""C1-v2 / G4 checker — v1.6 manuscript gate (PLAN D5', P7).

Checks the v1.6 manuscript (`docs/paper/cbc/cbc-paper.md`) against the frozen
v1.6 sources:

  1. required v1.6 numbers present (wave + gates + re-gating universe);
  2. stale phrasings absent (pre-upgrade gate wording / retired future-work item);
  3. structure: the new §6.12 subsection exists and is cross-referenced >= 2x;
  4. abstract word count <= 250;
  5. spot consistency with NUMBER-SOURCES-v1.6.json (selected entries).

Writes docs/paper/cbc/v2/checks/v2-checks.json; exit 0 iff everything passes.

Usage: python scripts/v2_checks.py [--md PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "docs" / "paper" / "cbc" / "cbc-paper.md"
NS = ROOT / "docs" / "paper" / "cbc" / "v2" / "NUMBER-SOURCES-v1.6.json"
OUT = ROOT / "docs" / "paper" / "cbc" / "v2" / "checks" / "v2-checks.json"

REQUIRED = [
    "3,519/3,555", "3,526", "4,980", "1,729", "1,689", "1,461", "223",
    "95", "246", "254", "99/100", "54 + 54",
]
REQUIRED_ANY = [  # at least one of each group must appear
    ["slot rule", "slot-based"],
    ["three-valued"],
]
STALE = [
    "substring containment (T1)",
    "tightening the boundary measures",
    "Gate v2:",
    "post-release",
    "upgraded after the release",
    "have since",
    "over the release cycle",
    "earlier release",
    "historical pipeline",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=str(MD))
    args = ap.parse_args()
    md_path = Path(args.md)
    if not md_path.exists():
        sys.exit(f"manuscript not found: {md_path}")
    text = md_path.read_text(encoding="utf-8")

    checks = []

    missing = [s for s in REQUIRED if s not in text]
    checks.append({"name": "numbers_required", "ok": not missing, "missing": missing})

    missing_any = [group for group in REQUIRED_ANY if not any(s in text for s in group)]
    checks.append({"name": "wording_upgraded", "ok": not missing_any, "missing_groups": missing_any})

    stale = [s for s in STALE if s in text]
    checks.append({"name": "stale_absent", "ok": not stale, "present": stale})

    sec = re.search(r"^### 6\.12 .*$", text, re.M)
    xrefs = len(re.findall(r"§6\.12", text))
    checks.append({"name": "section_6_12", "ok": bool(sec) and xrefs >= 2,
                   "title": sec.group(0) if sec else None, "xrefs": xrefs})

    m = re.search(r"## Abstract\n\n(.+?)\n\n", text, re.S)
    words = len((m.group(1) if m else "").split())
    checks.append({"name": "abstract_words", "ok": 0 < words <= 250, "words": words})

    caps = ROOT / "docs" / "paper" / "cbc" / "v2" / "captions-v1.6.md"
    caps_stale = []
    if caps.exists():
        ct = caps.read_text(encoding="utf-8")
        caps_stale = [s for s in ("single annotator", "AI proxy", "batches of 50") if s in ct]
        checks.append({"name": "captions_stale", "ok": not caps_stale, "present": caps_stale})

    ns = json.loads(NS.read_text(encoding="utf-8"))
    entries = {e["id"]: e for e in ns["entries"]}
    spot = [
        ("t2_wave.carried", "1,689"),
        ("t2_wave.rechecked", "3,526"),
        ("gates.fixtures", "54 + 54"),
    ]
    spot_bad = []
    for eid, fmt in spot:
        e = entries.get(eid)
        if e is None or fmt not in text:
            spot_bad.append(eid)
    checks.append({"name": "number_sources_spot", "ok": not spot_bad, "bad": spot_bad})

    ok = all(c["ok"] for c in checks)
    try:
        rel = str(md_path.relative_to(ROOT))
    except ValueError:
        rel = str(md_path)
    report = {"manuscript": rel, "ok": ok, "checks": checks}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for c in checks:
        print(("PASS " if c["ok"] else "FAIL ") + c["name"], {k: v for k, v in c.items() if k not in ("name", "ok")})
    print("ALL PASS" if ok else "G4 FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
