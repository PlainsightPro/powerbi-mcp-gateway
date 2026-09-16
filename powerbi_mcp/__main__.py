"""Run the server over Streamable HTTP, or check a skills folder.

python -m powerbi_mcp                       # MCP endpoint at /mcp
python -m powerbi_mcp --check-skills [DIR]  # validate a skills folder and exit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .config import DEFAULT_SKILLS_DIR

# Libraries that narrate every HTTP exchange at INFO: the Azure SDK dumps request and response
# headers for each table read the OAuth state store makes, which would bury the one-line-per-call
# tool log (observability.py) that support relies on. They speak only when the level is DEBUG.
CHATTY_LOGGERS = ("azure", "httpx", "httpcore")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    library_level = logging.DEBUG if level.upper() == "DEBUG" else logging.WARNING
    for name in CHATTY_LOGGERS:
        logging.getLogger(name).setLevel(library_level)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"init", "validate"}:
        from .authoring import main as author

        return author(argv)
    parser = argparse.ArgumentParser(prog="python -m powerbi_mcp", description="Power BI MCP Gateway")
    parser.add_argument(
        "--check-skills",
        nargs="?",
        const="",
        metavar="DIR",
        help="validate a skills folder (default: PBIMCP_SKILLS_DIR or the bundled example) and exit",
    )
    args = parser.parse_args(argv)

    if args.check_skills is not None:
        from .validate import main as check

        folder = args.check_skills or os.environ.get("PBIMCP_SKILLS_DIR") or str(DEFAULT_SKILLS_DIR)
        return check(Path(folder))

    from .config import load_settings
    from .server import build_server

    settings = load_settings()
    configure_logging(settings.log_level)
    build_server(settings).run(transport="http", host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
