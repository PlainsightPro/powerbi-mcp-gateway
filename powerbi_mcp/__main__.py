"""Run the server over Streamable HTTP: python -m powerbi_mcp (MCP endpoint at /mcp)."""

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "evaluate":
        from .evaluation import main
        raise SystemExit(main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] in ("init", "validate"):
        from .authoring import main
        raise SystemExit(main(sys.argv[1:]))
    from .config import load_settings
    from .server import build_server

    settings = load_settings()
    build_server(settings).run(transport="http", host=settings.host, port=settings.port)
