"""A host with no model in it: start the server, ask what it offers, call search_flights.

    python3 client.py

This is everything an MCP host does before a model is involved: launch the server as a child
process, discover its tools and their schemas, and call one by name with JSON arguments.
Claude Code does the same thing, then hands the schemas to the model.

No API key and no network are needed. Until the SearchApi adapter exists, the valid call
below ends in the placeholder's UPSTREAM_UNAVAILABLE error; that is expected.
"""
import asyncio
import json
import sys
from datetime import date, timedelta

from mcp import Client, StdioServerParameters


async def main():
    server = StdioServerParameters(command=sys.executable, args=["server.py"])
    async with Client(server) as client:
        listed = await client.list_tools()
        for t in listed.tools:
            print(f"\n{t.name}: {t.description}")
            print("  schema:", json.dumps(t.input_schema.get("properties", {})))

        depart = (date.today() + timedelta(days=60)).isoformat()
        request = {"origin": ["JFK"], "destination": ["LIS"],
                   "depart_date": depart, "cabin": "economy"}

        print("\n--- search_flights, valid one-way request")
        result = await client.call_tool("search_flights", request)
        print("is_error:", result.is_error, "|", result.content[0].text[:300])

        print("\n--- search_flights with adults=10 (the schema says 1 to 9)")
        result = await client.call_tool("search_flights", {**request, "adults": 10})
        print("is_error:", result.is_error, "|", result.content[0].text[:200])


if __name__ == "__main__":
    asyncio.run(main())
