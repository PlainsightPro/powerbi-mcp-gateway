"""Run the server over Streamable HTTP: python -m powerbi_mcp (MCP endpoint at /mcp)."""

from .config import load_settings
from .server import build_server

if __name__ == "__main__":
    settings = load_settings()
    build_server(settings).run(transport="http", host=settings.host, port=settings.port)
