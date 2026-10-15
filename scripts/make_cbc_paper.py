"""Build the CBC manuscript from the frozen master + overrides.

Usage:
    python scripts/make_cbc_paper.py [--check]

Reads : docs/paper/final/paper.md            (master, READ-ONLY, byte-frozen)
        docs/paper/cbc/overrides.md          (the single source of differences)
Writes: docs/paper/cbc/cbc-paper.md          (CBC manuscript; skipped with --check)

Pipeline: master --(replace 6 sections)--> core --(string patches)--> core2
          --(anchored inserts)--> out.  Verifications:
  V1 every diff(master, core) opcode lies inside one of the 6 replaced spans
  V2 the six untouched sections of `core` are byte-identical to master
  V3 each patch old-string occurs exactly once (applied); new-string present
  V4 insert blocks land exactly once, at the right side of their anchors
  V5 frozen-number spot check on the final output
"""
from __future__ import annotations

import argparse
import difflib
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
MASTER = ROOT / "docs" / "paper" / "final" / "paper.md"
OVERRIDES = ROOT / "docs" / "paper" / "cbc" / "overrides.md"
OUT = ROOT / "docs" / "paper" / "cbc" / "cbc-paper.md"

KEYS = ("TITLE", "HEADNOTE", "ABSTRACT", "SEC1", "SEC7", "SEC9",
        "ALG1", "DEFS", "SEC6BENCH", "SEC7CONC", "PATCH1")

# inserts: (override key, anchor heading, side)  side = "before" | "after"
INSERTS = (
    ("ALG1", "### 3.2 ", "before"),
    ("DEFS", "### 3.4 ", "before"),
    ("SEC6BENCH", "### 6.1 ", "after"),
    ("SEC7CONC", "## 8. Limitations", "before"),
)

# patches: (override key, list of (old, new) pairs) — exact, unique string edits
PATCH_KEYS = ("PATCH1",)

FROZEN_SPOT = [
    "2,289", "2,204", "21,014", "19,390", "1,307",
    "0.967", "0.904", "0.934", "0.961", "0.928", "0.944",
    "0.820", "0.872", "3,341", "3,526", "185", "0.988", "6.2%",
    "2,076", "128", "85", "0.990", "3,519", "3,555",
]


def parse_overrides(text: str) -> dict:
    pattern = re.compile(r"<!-- BEGIN ([A-Z0-9]+) -->\n(.*?)\n<!-- END \1 -->", re.S)
    blocks = {m.group(1): m.group(2) for m in pattern.finditer(text)}
    missing = [k for k in KEYS if k not in blocks]
    if missing:
        raise SystemExit(f"overrides.md is missing blocks: {missing}")
    return blocks


def parse_patch(block: str) -> list[tuple[str, str]]:
    segments = block.split("\n---\n")
    if len(segments) % 2 != 0:
        raise SystemExit("patch block must contain an even number of --- separators")
    return [(segments[i], segments[i + 1]) for i in range(0, len(segments), 2)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only; do not write output")
    args = ap.parse_args()

    master = MASTER.read_text(encoding="utf-8")
    ov = parse_overrides(OVERRIDES.read_text(encoding="utf-8"))

    spans = []  # (name, start, end, replacement, separator_suffix)

    # 1) title: first line
    nl = master.index("\n")
    spans.append(("TITLE", 0, nl, ov["TITLE"].rstrip("\n"), ""))

    # 2) head-note: contiguous blockquote run immediately before "## Abstract"
    abs_i = master.index("## Abstract")
    pre_lines = master[:abs_i].split("\n")
    q_idx = [i for i, l in enumerate(pre_lines) if l.startswith(">")]
    if not q_idx:
        raise SystemExit("head-note blockquote not found")
    last = q_idx[-1]
    first = last
    while first - 1 >= 0 and pre_lines[first - 1].startswith(">"):
        first -= 1

    def line_offset(k: int) -> int:
        return sum(len(l) + 1 for l in pre_lines[:k])

    s = line_offset(first)
    e = line_offset(last) + len(pre_lines[last])
    spans.append(("HEADNOTE", s, e, ov["HEADNOTE"].rstrip("\n"), ""))

    # 3-6) big sections, bounded by the next "## " heading
    for name, heading, stop in (
        ("ABSTRACT", "## Abstract", "## 1. Introduction"),
        ("SEC1", "## 1. Introduction", "## 2. Related work"),
        ("SEC7", "## 7. Discussion", "## 8. Limitations"),
        ("SEC9", "## 9. Data and code availability", "## Supplementary data"),
    ):
        s = master.index(heading)
        e = master.index(stop, s)
        spans.append((name, s, e, ov[name].rstrip("\n"), "\n\n"))

    # apply from the end to keep offsets valid; ensure non-overlap
    spans.sort(key=lambda t: t[1], reverse=True)
    prev_start = len(master)
    for name, s, e, repl, suf in spans:
        if e > prev_start:
            raise SystemExit(f"overlapping spans at {name}")
        prev_start = s

    core = master
    for name, s, e, repl, suf in spans:
        core = core[:s] + repl + suf + core[e:]

    # V1: every difflib opcode must lie inside one of the six replaced spans
    sm = difflib.SequenceMatcher(None, master, core, autojunk=False)
    changed = [(tag, i1, i2, j1, j2) for tag, i1, i2, j1, j2 in sm.get_opcodes()
               if tag != "equal"]
    print(f"result: master {len(master):,} -> core {len(core):,} chars")
    print(f"changed opcodes (difflib, minimal): {len(changed)}")
    span_ranges = {name: (s, e) for name, s, e, _repl, _suf in spans}
    touched = set()
    stray = []
    for tag, i1, i2, j1, j2 in changed:
        for name, (s, e) in span_ranges.items():
            if s <= i1 <= i2 <= e:
                touched.add(name)
                break
        else:
            stray.append((tag, i1, i2))
    print(f"span-level regions touched: {len(touched)}/6 -> {sorted(touched)}")
    if stray:
        raise SystemExit(f"diff outside override spans: {stray[:5]}")

    # V2: untouched sections of the core are byte-identical to master
    for heading, stop in (("## 2. Related work", "## 3. The BioAgentDB agent"),
                          ("## 3. The BioAgentDB agent", "## 4. The agent's database"),
                          ("## 4. The agent's database", "## 5. Using BioAgentDB"),
                          ("## 5. Using BioAgentDB", "## 6. Evaluation"),
                          ("## 6. Evaluation", "## 7. Discussion"),
                          ("## 8. Limitations", "## 9. Data and code availability")):
        a = master[master.index(heading):master.index(stop, master.index(heading))]
        b = core[core.index(heading):core.index(stop, core.index(heading))]
        assert a == b, f"unexpected change in {heading}"
    print("untouched-section integrity (core): 6/6 byte-identical")

    # V3: patches (exact unique string edits)
    core2 = core
    n_patches = 0
    for key in PATCH_KEYS:
        for old, new in parse_patch(ov[key]):
            cnt = core2.count(old)
            if cnt != 1:
                raise SystemExit(f"patch old-string occurs {cnt} times (need 1): {old[:60]!r}")
            core2 = core2.replace(old, new)
            if core2.count(new) < 1:
                raise SystemExit(f"patch new-string missing: {new[:60]!r}")
            n_patches += 1
    print(f"patches applied: {n_patches}")

    # V4: insertions land exactly once, on the right side of their anchors
    out = core2
    for key, anchor, side in INSERTS:
        if out.count(anchor) != 1:
            raise SystemExit(f"anchor for {key} not unique: {anchor!r}")
        block = ov[key].rstrip("\n")
        if out.count(block) != 0:
            raise SystemExit(f"insert {key} already present")
        pos = out.index(anchor)
        if side == "after":
            line_end = out.index("\n", pos)
            out = out[:line_end + 1] + block + "\n\n" + out[line_end + 1:]
        else:
            out = out[:pos] + block + "\n\n" + out[pos:]
    for key, _anchor, _side in INSERTS:
        block = ov[key].rstrip("\n")
        if out.count(block) != 1:
            raise SystemExit(f"insert {key} not present exactly once in output")
    print("insertion blocks placed:", [k for k, _a, _s in INSERTS])

    # V5: frozen-number spot check on the final output
    missing = [k for k in FROZEN_SPOT if k not in out]
    print(f"frozen-number spot check: {len(FROZEN_SPOT) - len(missing)}/{len(FROZEN_SPOT)} present"
          + (f" | MISSING: {missing}" if missing else ""))
    if missing:
        raise SystemExit(1)

    if not args.check:
        OUT.write_text(out, encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}")
    else:
        print("(--check: no write)")


if __name__ == "__main__":
    main()
