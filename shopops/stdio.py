"""Run the MCP server over stdio (Claude Desktop / Claude Code local). The caller is the local operator."""

from shopops.server import mcp

if __name__ == "__main__":
    mcp.run("stdio")
