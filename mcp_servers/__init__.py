"""In-process MCP servers for LocallyAI's chat plugin pipeline.

Each subpackage exports a server.py module with the contract documented in
api/plugins.py:309-371 (`builtin_tool_defs` + `dispatch_builtin_tool`).
There is intentionally NO subprocess / stdio MCP transport here — the
servers are pure Python modules invoked directly from the chat handler.
"""
