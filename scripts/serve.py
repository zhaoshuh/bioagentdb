"""启动 BioAgentDB 服务（uvicorn）。用法: python scripts/serve.py [--host H] [--port P]"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402

from app.core.config import cfg  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    if cfg.validation_error:
        sys.exit(f"config invalid (config/bioagentdb.toml): {cfg.validation_error}")
    if cfg.unknown_keys:
        print("WARNING: unknown config keys (ignored): " + ", ".join(cfg.unknown_keys))
    host = args.host or cfg.get("host")
    port = args.port or cfg.int("port", 8000)
    print(f"BioAgentDB listening on http://{host}:{port}")
    uvicorn.run("app.api.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()