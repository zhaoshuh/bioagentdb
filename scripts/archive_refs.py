"""引用包归档：把本地全文/PDF 与元数据整理进 docs/paper/refs/local/。

产出：docs/paper/refs/local/*（副本）、README.md（编号→文件→来源→许可说明）、manifest.json（sha256）。
用法: python scripts/archive_refs.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "refs"
OUT = ROOT / "docs" / "paper" / "refs"
LOCAL = OUT / "local"
VERIFIED = OUT / "verified.json"

ROLE_ORDER = ["burden", "proteinopathy", "landmark", "repository", "mining",
              "agent", "verification", "reproducibility"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    LOCAL.mkdir(parents=True, exist_ok=True)
    items = json.loads(VERIFIED.read_text(encoding="utf-8")) if VERIFIED.exists() else []

    # 编号（与 references.md 一致：按角色顺序）
    numbered = []
    n = 0
    for role in ROLE_ORDER:
        for r in items:
            if r["role"] == role:
                n += 1
                numbered.append((n, r))

    copied, manifest = [], []
    for num, r in numbered:
        files = r.get("files") or {}
        for kind in ("xml", "pdf"):
            src_path = files.get(kind)
            if not src_path:
                continue
            src = Path(src_path)
            if not src.exists():
                continue
            dst = LOCAL / src.name
            shutil.copy2(src, dst)
            manifest.append({"ref": num, "id": r["id"], "kind": kind,
                             "file": dst.name, "sha256": sha256(dst),
                             "bytes": dst.stat().st_size})
            copied.append((num, r, kind, dst.name))

    lines = ["# Local reference library", "",
             f"- entries with local full text: **{len(copied)}** / {len(numbered)}",
             f"- files: {len(manifest)} · "
             f"size: {round(sum(m['bytes'] for m in manifest) / 1e6, 1)} MB",
             "",
             "Sources: Europe PMC open-access full text (XML) and arXiv (PDF). Articles without a",
             "local file are **metadata-only** (paywalled / not in the OA subset); they are still",
             "verified (see `verified.json`). Please respect each publisher's license; files are",
             "kept locally for reference only and are not redistributed with the paper.",
             "",
             "| # | ref | kind | file | source | DOI/PMID |", "|---|---|---|---|---|---|"]
    for num, r, kind, fname in copied:
        ids = " / ".join(x for x in (r.get("doi"), r.get("pmid")) if x)
        lines.append(f"| {num} | {r['id']} | {kind} | `{fname}` | {r['source']} | {ids} |")

    lines += ["", "## Regenerate", "",
              "```bash",
              "python scripts/fetch_refs.py            # verify + download OA full texts",
              "python scripts/archive_refs.py          # rebuild this local library",
              "```"]
    (OUT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "local-manifest.json").write_text(
        json.dumps({"files": manifest, "count": len(manifest)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"archived {len(manifest)} file(s) to {LOCAL}")
    print(f"README: {OUT / 'README.md'}  manifest: {OUT / 'local-manifest.json'}")


if __name__ == "__main__":
    main()
