"""清理文本中的 HTML 残留（<i>、<sup> 等），并加写库前的清洗函数。"""

import re
import sys
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import db

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_html(text: str) -> str:
    """反复 unescape + 剥标签（处理 &lt;sup&gt; 这类多级编码），再归一空白。"""
    if not text:
        return text
    s = text
    for _ in range(3):
        prev = s
        s = _TAG_RE.sub(" ", unescape(s))
        if s == prev:
            break
    return _WS_RE.sub(" ", s).strip()


def clean_all() -> None:
    with db() as conn:
        for tbl, cols in {
            "papers": ("title", "abstract", "da_text", "journal", "authors"),
            "datasets": ("title", "description"),
            "evidence": ("quote",),
        }.items():
            rows = conn.execute(f"SELECT id, {','.join(cols)} FROM {tbl}").fetchall()
            n = 0
            for r in rows:
                changed = False
                for c in cols:
                    v = r[c]
                    if v and ("<" in v or "&" in v):
                        cv = clean_html(v)
                        if cv != v:
                            conn.execute(f"UPDATE {tbl} SET {c}=? WHERE id=?", (cv, r["id"]))
                            changed = True
                if changed:
                    n += 1
            print(f"{tbl}: cleaned {n} rows")


if __name__ == "__main__":
    clean_all()
    print("done")