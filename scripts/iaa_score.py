"""W1 · IAA 计算与仲裁表（L-0283）。

输入：两份已填标注 CSV（同一 paper_id 集）。
输出：data/gold/iaa/iaa-report.{json,md} + arbitration.csv（分歧逐行）。
指标：announces_data 的 Cohen's κ + 原始一致率；accession 集合的双向 micro P/R/F1 +
逐篇 Jaccard；分歧清单（宣布判定 / 登记号集合）。
用法: python scripts/iaa_score.py A.csv B.csv [--out data/gold/iaa]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def norm(acc: str) -> str:
    from app.core.vocab import canon_accession
    return canon_accession(acc)


def load(path: Path) -> dict:
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            pid = (row.get("paper_id") or "").strip()
            if not pid:
                continue
            ann = (row.get("announces_data(Y/N)") or "").strip().upper()
            accs = []
            for part in (row.get("accessions(semicolon)") or "").split(";"):
                p = part.strip()
                if p and p.upper() != "NONE":
                    a = norm(p)
                    if a:
                        accs.append(a)
            out[pid] = {"announces": ann == "Y", "ann_raw": ann, "accs": set(accs),
                        "notes": (row.get("notes") or "").strip()}
    return out


def cohen_kappa(a: list[bool], b: list[bool]) -> tuple[float, float]:
    n = len(a)
    if not n:
        return 0.0, 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1 = sum(1 for x in a if x) / n
    pb1 = sum(1 for x in b if x) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 0.0
    return po, kappa


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--out", default=str(ROOT / "data" / "gold" / "iaa"))
    args = ap.parse_args()
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    A = load(Path(args.a))
    B = load(Path(args.b))
    pids = sorted(set(A) & set(B))

    a_flags = [A[p]["announces"] for p in pids]
    b_flags = [B[p]["announces"] for p in pids]
    po, kappa = cohen_kappa(a_flags, b_flags)

    tp = sum(len(A[p]["accs"] & B[p]["accs"]) for p in pids)
    fp_ab = sum(len(A[p]["accs"] - B[p]["accs"]) for p in pids)
    fp_ba = sum(len(B[p]["accs"] - A[p]["accs"]) for p in pids)
    prec_ab = tp / max(1, tp + fp_ab)      # treat A as reference, B as prediction
    rec_ab = tp / max(1, tp + fp_ba)
    f1_ab = 2 * prec_ab * rec_ab / max(1e-9, prec_ab + rec_ab)
    prec_ba = tp / max(1, tp + fp_ba)
    rec_ba = tp / max(1, tp + fp_ab)
    f1_ba = 2 * prec_ba * rec_ba / max(1e-9, prec_ba + rec_ba)

    jaccards = []
    for p in pids:
        a, b = A[p]["accs"], B[p]["accs"]
        if a or b:
            jaccards.append(len(a & b) / max(1, len(a | b)))

    rows = []
    for p in pids:
        if A[p]["announces"] != B[p]["announces"]:
            rows.append([p, "announces_data", A[p]["ann_raw"], B[p]["ann_raw"],
                         A[p]["notes"], B[p]["notes"]])
        if A[p]["accs"] != B[p]["accs"]:
            rows.append([p, "accessions", ";".join(sorted(A[p]["accs"])),
                         ";".join(sorted(B[p]["accs"])), A[p]["notes"], B[p]["notes"]])
    with (outdir / "arbitration.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["paper_id", "field", "annotator_A", "annotator_B", "notes_A", "notes_B",
                    "adjudicated", "rule_applied"])
        w.writerows(rows)

    report = {
        "papers": len(pids),
        "announces_data": {"raw_agreement": round(po, 4), "cohen_kappa": round(kappa, 4),
                           "A_Y": sum(a_flags), "B_Y": sum(b_flags)},
        "accessions": {
            "tp": tp, "A_only": fp_ab, "B_only": fp_ba,
            "A_as_ref": {"precision": round(prec_ab, 4), "recall": round(rec_ab, 4),
                         "f1": round(f1_ab, 4)},
            "B_as_ref": {"precision": round(prec_ba, 4), "recall": round(rec_ba, 4),
                         "f1": round(f1_ba, 4)},
            "mean_jaccard": round(sum(jaccards) / max(1, len(jaccards)), 4),
        },
        "disagreements": len(rows),
    }
    (outdir / "iaa-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    md = ["# Inter-annotator agreement", "",
          f"- papers: {len(pids)}",
          f"- announces_data: raw agreement {po:.3f}, Cohen's κ {kappa:.3f} "
          f"(A=Y {sum(a_flags)}, B=Y {sum(b_flags)})",
          f"- accessions (micro): A-ref P/R/F1 {prec_ab:.3f}/{rec_ab:.3f}/{f1_ab:.3f}; "
          f"B-ref {prec_ba:.3f}/{rec_ba:.3f}/{f1_ba:.3f}; mean Jaccard "
          f"{report['accessions']['mean_jaccard']:.3f}",
          f"- disagreements: {len(rows)} rows → arbitration.csv"]
    (outdir / "iaa-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print(f"wrote {outdir}/iaa-report.json + arbitration.csv")


if __name__ == "__main__":
    main()
