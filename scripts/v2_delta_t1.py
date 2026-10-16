"""P3 delta driver — T1 v1-vs-v2 re-gating over the frozen release (_v15).

Read-only w.r.t. the database. Produces:
    docs/paper/cbc/v2/delta-report.md      (T1 section + G1 thresholds + T2 note)
    docs/paper/cbc/v2/delta-t1.jsonl       (per-unit rows: link- and candidate-level)
    docs/paper/cbc/v2/checks/delta-meta.json (hashes / as-of / counts)

Units
-----
* link level:   dataset_links.is_primary = 1  (3,555 primary links; paper T1 baseline 3,519)
* candidate:    candidates table (4,980; v1 verdict + quote/zone from payload.checks)

Both v1 (app.core.utils.acc_in_text) and v2 (app.harvest.t1_v2) are run on the
**same sources** per unit, so differences are attributable to the matcher only.

G1 thresholds (PLAN v2.2): removed datasets <= 2% * 2,289 and added <= 2% * 2,289.

Usage: python scripts/v2_delta_t1.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

V15 = ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3"
OUT_REPORT = ROOT / "docs" / "paper" / "cbc" / "v2" / "delta-report.md"
OUT_JSONL = ROOT / "docs" / "paper" / "cbc" / "v2" / "delta-t1.jsonl"
OUT_META = ROOT / "docs" / "paper" / "cbc" / "v2" / "checks" / "delta-meta.json"

_DETAIL = re.compile(r"zone=(?P<zone>[\w-]+);\s*quote:\s*(?P<quote>.*)$", re.S)


def _sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main() -> None:
    from app.core.utils import acc_in_text          # v1 matcher
    from app.harvest import t1_v2                   # v2 matcher

    con = sqlite3.connect(f"file:{V15.as_posix()}?mode=ro", uri=True)

    # ---------- sources ----------
    ev: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for ds, zone, quote in con.execute("select dataset_id, zone, quote from evidence"):
        if quote:
            ev[ds].append((zone or "abstract", quote))

    links = list(con.execute(
        "select id, dataset_id, accession from dataset_links where is_primary=1"))

    candidates = list(con.execute(
        "select id, paper_id, source, accession, verdict, payload from candidates"))

    rows: list[dict] = []

    # ---------- link level ----------
    link_total = Counter()
    l_flip_fail: list[dict] = []       # v1 pass -> v2 fail
    l_flip_pass: list[dict] = []       # v1 fail -> v2 pass
    ds_affect: set[str] = set()
    for lid, dsid, acc in links:
        if not acc:
            continue
        sources = ev.get(dsid) or []
        v1 = any(acc_in_text(acc, q) for _z, q in sources)
        v2v = t1_v2.check(acc, [(z, q) for z, q in sources]) if sources else None
        v2 = v2v is not None
        link_total["units"] += 1
        link_total["v1_pass" if v1 else "v1_fail"] += 1
        link_total["v2_pass" if v2 else "v2_fail"] += 1
        flip = None
        if v1 and not v2:
            flip = "pass->fail"
            link_total["flip_fail"] += 1
            ds_affect.add(dsid)
            l_flip_fail.append({"link_id": lid, "dataset_id": dsid, "accession": acc,
                                "n_quotes": len(sources)})
        elif (not v1) and v2:
            flip = "fail->pass"
            link_total["flip_pass"] += 1
            l_flip_pass.append({"link_id": lid, "dataset_id": dsid, "accession": acc,
                                "tier": v2v["tier"], "zone": v2v.get("zone")})
        rows.append({"level": "link", "id": lid, "dataset_id": dsid, "accession": acc,
                     "v1": v1, "v2": v2,
                     "tier": (v2v or {}).get("tier"), "zone": (v2v or {}).get("zone"),
                     "flip": flip})

    # secondary source check for pass->fail cases (informational only)
    papers: dict[str, tuple[str, str]] = {}
    for pid, ab, da in con.execute("select id, abstract, da_text from papers"):
        papers[pid] = (ab or "", da or "")
    # link -> paper: via candidates (same accession+dataset) or dataset_papers
    ds_papers: dict[str, list[str]] = defaultdict(list)
    for dsid, pid in con.execute("select dataset_id, paper_id from dataset_papers"):
        ds_papers[dsid].append(pid)
    for f in l_flip_fail:
        pids = ds_papers.get(f["dataset_id"], [])
        alt = False
        for pid in pids[:3]:
            ab, da = papers.get(pid, ("", ""))
            if acc_in_text(f["accession"], ab) or acc_in_text(f["accession"], da):
                alt = True
                break
        f["paper_text_grounds"] = alt

    # ---------- candidate level ----------
    cand_total = Counter()
    c_flip_fail: list[dict] = []
    c_flip_pass: list[dict] = []
    for cid, pid, src, acc, verdict, payload in candidates:
        if not acc:
            continue
        quote = zone = None
        try:
            p = json.loads(payload) if payload else {}
            for chk in p.get("checks", []):
                if chk.get("name") == "t1_paper_grounding":
                    det = chk.get("detail") or ""
                    m = _DETAIL.search(det)
                    if m:
                        zone, quote = m.group("zone"), m.group("quote").strip()
        except Exception:
            pass
        if quote:
            v1 = acc_in_text(acc, quote)
            v2v = t1_v2.check(acc, [(zone or "abstract", quote)])
        else:
            ab, da = papers.get(pid, ("", ""))
            v1 = acc_in_text(acc, ab) or acc_in_text(acc, da)
            v2v = t1_v2.check(acc, [("abstract", ab), ("data_availability", da)])
        v2 = v2v is not None
        cand_total["units"] += 1
        cand_total["v1_pass" if v1 else "v1_fail"] += 1
        cand_total[f"verdict_{verdict}"] += 1
        flip = None
        if v1 and not v2:
            flip = "pass->fail"
            cand_total["flip_fail"] += 1
            c_flip_fail.append({"candidate_id": cid, "accession": acc,
                                "verdict": verdict, "source": src,
                                "has_quote": bool(quote)})
        elif (not v1) and v2:
            flip = "fail->pass"
            cand_total["flip_pass"] += 1
            c_flip_pass.append({"candidate_id": cid, "accession": acc,
                                "verdict": verdict, "source": src,
                                "tier": v2v["tier"]})
        rows.append({"level": "candidate", "id": cid, "paper_id": pid, "source": src,
                     "accession": acc, "verdict": verdict, "v1": v1, "v2": v2,
                     "tier": (v2v or {}).get("tier"), "flip": flip})

    con.close()

    # ---------- admission projection ----------
    # datasets whose every primary link flips v1-pass -> v2-fail (conservative removal)
    ds_links: dict[str, list[bool]] = defaultdict(list)
    for rid, rrow in [(r["dataset_id"], r) for r in rows if r["level"] == "link"]:
        ds_links[rid].append(rrow["v2"])
    removed_ds = [d for d, oks in ds_links.items() if oks and not any(oks) and d in ds_affect]
    added_projection = sum(1 for f in c_flip_pass if f["verdict"] == "rejected")

    N_DS = 2289
    thr = 0.02 * N_DS
    g1_removed_ok = len(removed_ds) <= thr
    g1_added_ok = added_projection <= thr

    # ---------- outputs ----------
    OUT_JSONL.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                         encoding="utf-8")
    learned = ROOT / "learned" / "patterns.json"
    meta = {
        "as_of": "2026-10-16",
        "db": {"path": str(V15.relative_to(ROOT)), "sha16": _sha16(V15)},
        "learned_sha16": _sha16(learned) if learned.exists() else None,
        "script": {"path": "scripts/v2_delta_t1.py", "sha16": _sha16(Path(__file__))},
        "link_level": dict(link_total),
        "candidate_level": dict(cand_total),
        "projection": {"removed_datasets": len(removed_ds), "added_rejected_candidates": added_projection},
    }
    OUT_META.parent.mkdir(parents=True, exist_ok=True)
    OUT_META.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    def _lst(items, key, n=12):
        return "\n".join(f"  - `{i.get(key)}` ({i.get('accession')})" for i in items[:n])

    report = f"""# P3 T1 差分报告（v1 → v2 再门控，`_v15` 冻结库）

- as_of: 2026-10-16 · `_v15` sha16 `{meta['db']['sha16']}` · learned sha16 `{meta['learned_sha16']}` · 脚本 sha16 `{meta['script']['sha16']}`
- 单位：**link 级**（primary links，论文 T1 基线 3,519/3,555）与 **candidate 级**（4,980）；两级均在**同一来源**上跑 v1/v2（差异只能归因于匹配器）。

## Link 级（primary links）
- units: {link_total['units']} · v1 pass {link_total['v1_pass']} / fail {link_total['v1_fail']}
- v2 pass {link_total['v2_pass']} / fail {link_total['v2_fail']}
- **pass→fail {link_total['flip_fail']}** · **fail→pass {link_total['flip_pass']}**
- pass→fail 清单（{len(l_flip_fail)} 条，前 12）：
{_lst(l_flip_fail, 'link_id') or '  （无）'}
- fail→pass 清单（{len(l_flip_pass)} 条，前 12）：
{_lst(l_flip_pass, 'link_id') or '  （无）'}

## Candidate 级
- units: {cand_total['units']} · v1 pass {cand_total['v1_pass']} / fail {cand_total['v1_fail']}
  （verdict: verified {cand_total.get('verdict_verified', 0)} / paper_verified {cand_total.get('verdict_paper_verified', 0)} / rejected {cand_total.get('verdict_rejected', 0)}）
- **pass→fail {cand_total['flip_fail']}** · **fail→pass {cand_total['flip_pass']}**
- pass→fail 清单（{len(c_flip_fail)} 条，前 12）：
{_lst(c_flip_fail, 'candidate_id') or '  （无）'}
- fail→pass 清单（{len(c_flip_pass)} 条，前 12）：
{_lst(c_flip_pass, 'candidate_id') or '  （无）'}

## 准入投影与 G1 门槛
- 受影响数据集（≥1 条 primary link pass→fail）：{len(ds_affect)}
- **移除投影**（全 primary links v2 均不过）：{len(removed_ds)}（阈值 ≤{thr:.0f} → {'PASS' if g1_removed_ok else 'FAIL'}）
- **新增投影**（rejected 候选 fail→pass）：{added_projection}（阈值 ≤{thr:.0f} → {'PASS' if g1_added_ok else 'FAIL'}）
- **G1（T1 部分）**：{'PASS' if (g1_removed_ok and g1_added_ok) else 'FAIL —— 复核规格'}

## T2 说明
- T2 的实判随网络（P4 全量 3,526 重检）；P3 仅完成映射投影（`t2-mapping.md`，按类型镜像、零非解释翻转）。G1 的 T2 翻转阈值在 P4 结算。
- 截图规则（D8）判定点：若本报告移除/新增均为 0 且裁决数值无变化 → 屏显数字未变 → 截图保留；反之按 A 重拍。
"""
    OUT_REPORT.write_text(report, encoding="utf-8")
    print(report[:2400])
    print("...")
    print("meta:", json.dumps({k: v for k, v in meta.items() if k in ('link_level', 'candidate_level', 'projection')}, ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()
