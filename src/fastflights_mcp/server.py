"""One MCP application for local hosts, HTTP hosting, and the packaged command."""
import argparse
import logging
from collections.abc import Sequence

from mcp.server import MCPServer

# Tool modules will register on this instance; they must not create another server.
mcp = MCPServer(
    name="FastFlightsMCP",
    instructions="Search flight data using the registered tools. Never invent fares.",
)


def main(argv: Sequence[str] | None = None) -> None:
    """Start stdio by default, or a local streamable HTTP endpoint with --http."""
    parser = argparse.ArgumentParser(description="Start the FastFlights MCP server.")
    parser.add_argument(
        "--http", action="store_true",
        help="Serve streamable HTTP on http://127.0.0.1:8000/mcp (development).",
    )
    args = parser.parse_args(argv)
    # stderr is the logging default. stdout belongs to the stdio MCP protocol.
    logging.basicConfig(level=logging.INFO)
    if args.http:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)
    else:
        mcp.run(transport="stdio")
