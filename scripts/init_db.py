"""初始化数据库（建表 + 主题种子）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import init_db  # noqa: E402
from app.harvest.themes import seed_themes  # noqa: E402


def main() -> None:
    init_db()
    seed_themes()
    print("OK: SQLite catalog initialized (tables + starter themes per config).")


if __name__ == "__main__":
    main()