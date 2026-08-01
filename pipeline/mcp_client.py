"""
Thin MCP client wrapper around mcp_server/app.py.

Keeps the LangGraph graph free of MCP protocol details — graph.py just
calls call_tool("identify_clauses", {...}) and gets back plain Python
data, same as it would with a regular function.
"""

import json
import os
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:8002/mcp")


@asynccontextmanager
async def mcp_session():
    """Opens an MCP client session against the contract review server."""
    async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _parse_block(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


async def call_tool(session: ClientSession, tool_name: str, arguments: dict):
    """
    Calls an MCP tool and returns its result as parsed Python data.

    Prefers `result.structuredContent`, which FastMCP populates from the
    tool's return type annotation (e.g. `-> list[str]`, `-> dict`) per the
    MCP spec. This avoids a real ambiguity in the plain-text-block fallback
    below: a *single* content block can mean either "a scalar/dict result"
    or "a one-item list" (e.g. detect_missing_clauses returning exactly one
    missing clause) — those two cases are indistinguishable once they've
    been reduced to "how many text blocks came back", which previously
    caused a one-item list to be silently unwrapped into a bare string.

    Falls back to parsing `result.content` text blocks only if
    structuredContent isn't present (e.g. an older server/tool without an
    output schema).
    """
    result = await session.call_tool(tool_name, arguments)
    if result.isError:
        raise RuntimeError(f"MCP tool '{tool_name}' failed: {result.content}")

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        # FastMCP wraps non-object results (e.g. a bare list or string) in
        # {"result": ...} since MCP structured output must be a JSON object;
        # dict-returning tools come through as the dict itself.
        if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
            return structured["result"]
        return structured

    texts = [getattr(block, "text", "") for block in result.content]
    if len(texts) == 1:
        return _parse_block(texts[0])
    return [_parse_block(t) for t in texts]
