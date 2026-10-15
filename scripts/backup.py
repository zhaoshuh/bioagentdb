"""每日备份：VACUUM INTO 到 data/backups，保留最近 7 份。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import cfg  # noqa: E402
from app.core.utils import utcnow  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()
    src = cfg.db_path
    if not src.exists():
        print("no database yet; nothing to back up")
        return
    stamp = utcnow().replace(":", "-").replace("T", "_")
    dst = cfg.backup_dir / f"bioagentdb_{stamp}.sqlite3"
    import sqlite3
    conn = sqlite3.connect(str(src))
    try:
        conn.execute(f"VACUUM INTO '{dst}'")
    finally:
        conn.close()
    backups = sorted(cfg.backup_dir.glob("bioagentdb_*.sqlite3"))
    for old in backups[:-args.keep]:
        old.unlink()
    print(f"backup written: {dst}  (kept {len(backups)} total)")


if __name__ == "__main__":
    main()