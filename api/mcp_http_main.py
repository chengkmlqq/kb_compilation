"""MCP streamable-http ASGI entrypoint — for the kb-mcp-server container.

Runs the official MCP SDK's streamable-http transport on top of the same
code path as the stdio server (api.mcp_server.create_mcp_server).

Start:
    uvicorn api.mcp_http_main:app --host 0.0.0.0 --port 8001

Endpoints (SDK-managed, stateless-friendly):
    POST /mcp   JSON-RPC (initialize / tools/list / tools/call)
    GET  /mcp   SSE stream (optional; clients may use POST-only w/ json_response)
"""
from api.mcp_server import create_mcp_server

# streamable-http transport, bind 0.0.0.0 inside the container.
# json_response=True → plain JSON responses (simplest for stateless clients,
# no separate GET SSE channel required); stateless_http=False keeps MCP
# sessions (progress notifications etc.) coherent per session-id header.
app = create_mcp_server().streamable_http_app(
    host="0.0.0.0",
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
)