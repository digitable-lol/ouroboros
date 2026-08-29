"""Снимает список средств живым разговором с сервером по стандартному вводу-выводу.

Запускает `ouroboros-mcp` как отдельный процесс, проводит initialize и tools/list
через настоящий клиент MCP и печатает то, что сервер объявил, в JSON.
"""
from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(
        command=sys.argv[1] if len(sys.argv) > 1 else "ouroboros-mcp",
        args=sys.argv[2:],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            out = {
                "server_name": init.serverInfo.name,
                "server_version": init.serverInfo.version,
                "protocol_version": init.protocolVersion,
                "instructions": init.instructions,
                "capabilities": init.capabilities.model_dump(exclude_none=True),
                "tool_count": len(tools.tools),
                "tools": [t.model_dump(exclude_none=True) for t in tools.tools],
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))


asyncio.run(main())
