"""Diagnóstico 8: ver el total exacto post-fix."""
def test_mcp_recalling_total():
    from protocols.mcp.mcp_adapter import _handle_request
    import json
    for idx in ("cpg", "ckg", "delta", "trace"):
        resp = _handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "recalling_knowledge", "arguments": {
                "_project_id": "walker", "text": "walker", "_index": idx}},
        })
        content = (resp.get("result") or {}).get("content") or []
        text = content[0].get("text", "") if content else ""
        try:
            data = json.loads(text)
            result = (data.get("data") or {}).get("result") or {}
            print(f"\n[DIAG] MCP {idx}: total={result.get('total')}")
        except Exception as e:
            print(f"\n[DIAG] MCP {idx}: parse error {e} — {text[:100]}")
