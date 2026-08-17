"""Verificación: recalling_knowledge ignora index=delta → busca en ckg."""
def test_agent_call_ignores_index():
    from features.main.cli_interface.mcp_adapter import _handle_request
    import json
    resp = _handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "recalling_knowledge", "arguments": {
            "_project_id": "react-native-frontend", "index": "delta", "query": "navigation"}},
    })
    content = (resp.get("result") or {}).get("content") or []
    text = content[0].get("text", "") if content else ""
    try:
        data = json.loads(text)
        d = data.get("data") or {}
        result = d.get("result") or {}
        print(f"\n[DIAG] ok={d.get('ok')} index={result.get('index')} total={result.get('total')}")
        assert result.get("index") == "ckg", f"debe buscar en ckg, buscó {result.get('index')}"
    except Exception as e:
        print(f"\n[DIAG] parse err {e} — {text[:150]}")
        raise
