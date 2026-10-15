"""试点真值抽检：对每条 verified 记录自动核对
  1) 证据引用必须是论文 abstract/DA 原文的子串（逐字）
  2) 登记号必须在论文原文逐字出现
  3) 输出核对矩阵，供人工复核
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import db
from app.harvest.fulltext import decompress_text


def main() -> None:
    ok_ev = ok_acc = 0
    bad: list[dict] = []
    with db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT d.id dsid, d.title, d.fingerprint, l.repository, l.accession,
                      e.quote, e.zone, p.id pid, p.abstract, p.da_text, p.fulltext
               FROM datasets d
               JOIN dataset_links l ON l.dataset_id=d.id AND l.is_primary=1
               JOIN dataset_papers dp ON dp.dataset_id=d.id
               JOIN papers p ON p.id=dp.paper_id
               JOIN evidence e ON e.dataset_id=d.id AND e.paper_id=p.id
               WHERE d.status IN ('verified','paper_verified')""").fetchall()
    print(f"{len(rows)} record-evidence pairs to check\n")
    for r in rows:
        quote = r["quote"] or ""
        acc = (r["accession"] or "").upper()
        paper_text = f"\n{(r['abstract'] or '')}\n{(r['da_text'] or '')}"
        if r["fulltext"]:
            try:
                paper_text += "\n" + decompress_text(r["fulltext"])
            except Exception:  # noqa: BLE001
                pass
        ev_ok = bool(quote and quote in paper_text)
        acc_ok = len(acc) >= 4 and paper_text.upper().find(acc) >= 0
        if ev_ok and acc_ok:
            ok_ev += 1
            ok_acc += 1
            print(f"  ✓ {r['accession']}@{r['repository']:14} zone={r['zone']:16} quote-ok acc-ok")
        else:
            bad.append(dict(r))
            print(f"  x {r['accession']}@{r['repository']:14} zone={r['zone']:16} "
                  f"quote_ok={ev_ok} acc_ok={acc_ok} (quote_len={len(quote)})")
            print(f"      quote head: {(quote or '')[:100]}")
    total = max(len(rows), 1)
    print(f"\n通过率(证据子串): {ok_ev}/{len(rows)}  通过率(登记号逐字): {ok_acc}/{len(rows)}")
    if bad:
        print("问题记录数:", len(bad))


if __name__ == "__main__":
    main()