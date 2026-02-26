import json
import uuid
from typing import Any, Dict, List, Optional

import httpx


DEFAULT_TIMEOUT_SEC = 30.0
DEFAULT_PROTOCOL_VERSIONS = ["2024-11-05", "2025-11-25"]
DEFAULT_CLIENT_INFO = {"name": "tauri-agent", "version": "1.0"}


def _coerce_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not headers:
        return {}
    result: Dict[str, str] = {}
    for key, value in headers.items():
        if key is None:
            continue
        key_text = str(key).strip()
        if not key_text:
            continue
        result[key_text] = "" if value is None else str(value)
    return result


def _ensure_default_headers(headers: Dict[str, str]) -> Dict[str, str]:
    has_accept = any(str(key).lower() == "accept" for key in headers.keys())
    if not has_accept:
        headers["Accept"] = "application/json, text/event-stream"
    has_content_type = any(str(key).lower() == "content-type" for key in headers.keys())
    if not has_content_type:
        headers["Content-Type"] = "application/json"
    return headers


def _extract_sse_json(text: str) -> Dict[str, Any]:
    if not text:
        raise RuntimeError("MCP response is empty")
    events = [chunk.strip() for chunk in text.split("\n\n") if chunk.strip()]
    for event in events:
        data_lines: List[str] = []
        for line in event.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip())
        if not data_lines:
            continue
        payload = "\n".join(data_lines).strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            parsed = json.loads(payload)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError("MCP response missing JSON data in event stream")


def _parse_response_data(response: httpx.Response) -> Dict[str, Any]:
    content_type = (response.headers.get("content-type") or "").lower()
    if "text/event-stream" in content_type:
        return _extract_sse_json(response.text)
    try:
        return response.json()
    except Exception as exc:
        raise RuntimeError(f"MCP response is not valid JSON: {exc}") from exc


def _extract_session_id(headers: Any) -> str:
    if headers is None:
        return ""
    try:
        value = headers.get("MCP-Session-Id") or headers.get("mcp-session-id") or ""
        return str(value) if value else ""
    except Exception:
        return ""


def _build_mcp_headers(
    server_cfg: Optional[Dict[str, Any]],
    protocol_version: Optional[str] = None,
    include_session: bool = True
) -> Dict[str, str]:
    headers = _coerce_headers(server_cfg.get("headers") if isinstance(server_cfg, dict) else None)
    version = protocol_version or (server_cfg.get("protocol_version") if isinstance(server_cfg, dict) else None)
    if version:
        headers.setdefault("MCP-Protocol-Version", str(version))
    if include_session and isinstance(server_cfg, dict):
        session_id = server_cfg.get("session_id")
        if session_id:
            headers.setdefault("MCP-Session-Id", str(session_id))
    return headers


def _protocol_candidates(server_cfg: Dict[str, Any]) -> List[str]:
    value = server_cfg.get("protocol_version")
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    value_list = server_cfg.get("protocol_versions")
    if isinstance(value_list, list):
        cleaned = [str(item).strip() for item in value_list if str(item).strip()]
        if cleaned:
            return cleaned
    return list(DEFAULT_PROTOCOL_VERSIONS)


def _is_method_not_found(error_obj: Any) -> bool:
    if not isinstance(error_obj, dict):
        return False
    code = error_obj.get("code")
    message = str(error_obj.get("message") or "").lower()
    return code == -32601 or "method not found" in message


def _is_invalid_session_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "invalid session" in message or "sessionid" in message or "session id" in message


async def _post_json(
    server_url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, Any]],
    timeout_sec: Optional[float]
) -> Any:
    timeout = timeout_sec if timeout_sec is not None else DEFAULT_TIMEOUT_SEC
    final_headers = _ensure_default_headers(_coerce_headers(headers))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(server_url, json=payload, headers=final_headers)
        response.raise_for_status()
        data = _parse_response_data(response)
    return data, response.headers


def _post_json_sync(
    server_url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, Any]],
    timeout_sec: Optional[float]
) -> Any:
    timeout = timeout_sec if timeout_sec is not None else DEFAULT_TIMEOUT_SEC
    final_headers = _ensure_default_headers(_coerce_headers(headers))
    with httpx.Client(timeout=timeout) as client:
        response = client.post(server_url, json=payload, headers=final_headers)
        response.raise_for_status()
        data = _parse_response_data(response)
    return data, response.headers


async def _ensure_mcp_session(server_cfg: Dict[str, Any]) -> None:
    if not isinstance(server_cfg, dict):
        return
    if server_cfg.get("session_id"):
        return
    server_url = server_cfg.get("server_url")
    if not server_url:
        return

    last_exc: Optional[Exception] = None
    for version in _protocol_candidates(server_cfg):
        params = {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": DEFAULT_CLIENT_INFO
        }
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "initialize",
            "params": params
        }
        try:
            data, response_headers = await _post_json(
                server_url,
                payload,
                headers=_build_mcp_headers(server_cfg, protocol_version=version, include_session=False),
                timeout_sec=None
            )
        except Exception as exc:
            last_exc = exc
            continue

        if not isinstance(data, dict):
            last_exc = RuntimeError("MCP response is not an object")
            continue
        if data.get("error"):
            error_obj = data.get("error")
            if _is_method_not_found(error_obj):
                return
            last_exc = RuntimeError(
                f"MCP error: {json.dumps(error_obj, ensure_ascii=False)}"
            )
            continue
        if "result" not in data:
            last_exc = RuntimeError("MCP response missing result")
            continue

        session_id = _extract_session_id(response_headers)
        if session_id:
            server_cfg["session_id"] = session_id
        if isinstance(data.get("result"), dict):
            negotiated = data["result"].get("protocolVersion") or data["result"].get("protocol_version")
        else:
            negotiated = None
        if negotiated:
            server_cfg["protocol_version"] = str(negotiated)
        elif "protocol_version" not in server_cfg and version:
            server_cfg["protocol_version"] = version

        try:
            notify_payload = {"jsonrpc": "2.0", "method": "initialized", "params": {}}
            await _post_json(
                server_url,
                notify_payload,
                headers=_build_mcp_headers(server_cfg, protocol_version=version, include_session=bool(session_id)),
                timeout_sec=None
            )
        except Exception:
            pass
        return

    if last_exc:
        raise last_exc


def _ensure_mcp_session_sync(server_cfg: Dict[str, Any]) -> None:
    if not isinstance(server_cfg, dict):
        return
    if server_cfg.get("session_id"):
        return
    server_url = server_cfg.get("server_url")
    if not server_url:
        return

    last_exc: Optional[Exception] = None
    for version in _protocol_candidates(server_cfg):
        params = {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": DEFAULT_CLIENT_INFO
        }
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "initialize",
            "params": params
        }
        try:
            data, response_headers = _post_json_sync(
                server_url,
                payload,
                headers=_build_mcp_headers(server_cfg, protocol_version=version, include_session=False),
                timeout_sec=None
            )
        except Exception as exc:
            last_exc = exc
            continue

        if not isinstance(data, dict):
            last_exc = RuntimeError("MCP response is not an object")
            continue
        if data.get("error"):
            error_obj = data.get("error")
            if _is_method_not_found(error_obj):
                return
            last_exc = RuntimeError(
                f"MCP error: {json.dumps(error_obj, ensure_ascii=False)}"
            )
            continue
        if "result" not in data:
            last_exc = RuntimeError("MCP response missing result")
            continue

        session_id = _extract_session_id(response_headers)
        if session_id:
            server_cfg["session_id"] = session_id
        if isinstance(data.get("result"), dict):
            negotiated = data["result"].get("protocolVersion") or data["result"].get("protocol_version")
        else:
            negotiated = None
        if negotiated:
            server_cfg["protocol_version"] = str(negotiated)
        elif "protocol_version" not in server_cfg and version:
            server_cfg["protocol_version"] = version

        try:
            notify_payload = {"jsonrpc": "2.0", "method": "initialized", "params": {}}
            _post_json_sync(
                server_url,
                notify_payload,
                headers=_build_mcp_headers(server_cfg, protocol_version=version, include_session=bool(session_id)),
                timeout_sec=None
            )
        except Exception:
            pass
        return

    if last_exc:
        raise last_exc


async def mcp_rpc(
    server_url: str,
    method: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, Any]] = None,
    timeout_sec: Optional[float] = None
) -> Dict[str, Any]:
    if not server_url:
        raise ValueError("MCP server_url is required")
    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": method,
        "params": params or {}
    }
    data, _ = await _post_json(server_url, payload, headers=headers, timeout_sec=timeout_sec)
    if not isinstance(data, dict):
        raise RuntimeError("MCP response is not an object")
    if data.get("error"):
        raise RuntimeError(f"MCP error: {json.dumps(data.get('error'), ensure_ascii=False)}")
    if "result" not in data:
        raise RuntimeError("MCP response missing result")
    result = data.get("result")
    if not isinstance(result, dict):
        return {"value": result}
    return result


async def list_tools(server_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    server_url = server_cfg.get("server_url")
    await _ensure_mcp_session(server_cfg)
    headers = _build_mcp_headers(server_cfg)
    try:
        result = await mcp_rpc(server_url, "tools/list", params={}, headers=headers)
    except Exception as exc:
        if _is_invalid_session_error(exc):
            server_cfg.pop("session_id", None)
            await _ensure_mcp_session(server_cfg)
            headers = _build_mcp_headers(server_cfg)
            result = await mcp_rpc(server_url, "tools/list", params={}, headers=headers)
        else:
            raise
    tools = result.get("tools")
    if isinstance(tools, list):
        return [tool for tool in tools if isinstance(tool, dict)]
    raise RuntimeError("MCP tools/list result missing tools list")


async def call_tool(server_cfg: Dict[str, Any], tool_name: str, arguments: Dict[str, Any]) -> str:
    server_url = server_cfg.get("server_url")
    await _ensure_mcp_session(server_cfg)
    headers = _build_mcp_headers(server_cfg)
    params = {"name": tool_name, "arguments": arguments or {}}
    try:
        result = await mcp_rpc(server_url, "tools/call", params=params, headers=headers)
    except Exception as exc:
        if _is_invalid_session_error(exc):
            server_cfg.pop("session_id", None)
            await _ensure_mcp_session(server_cfg)
            headers = _build_mcp_headers(server_cfg)
            result = await mcp_rpc(server_url, "tools/call", params=params, headers=headers)
        else:
            raise
    content = result.get("content")
    if isinstance(content, list):
        outputs: List[str] = []
        for item in content:
            if isinstance(item, dict):
                item_type = str(item.get("type") or "").lower()
                if item_type in ("text", "output_text"):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        outputs.append(str(text))
                elif item_type in ("image", "output_image", "image_url", "input_image"):
                    data_url = _extract_image_data_url(item)
                    if data_url:
                        outputs.append(data_url)
            elif isinstance(item, str):
                outputs.append(item)
        if outputs:
            return "\n".join(outputs).strip()
    return json.dumps(result, ensure_ascii=False)


def _extract_image_data_url(item: Dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""
    data = item.get("data") or item.get("b64_json")
    if isinstance(data, str) and data.strip():
        data_text = data.strip()
        if data_text.startswith("data:"):
            return data_text
        mime = item.get("mimeType") or item.get("mime_type") or item.get("mime") or "image/png"
        return f"data:{mime};base64,{data_text}"

    url = ""
    image_url = item.get("image_url")
    if isinstance(image_url, dict):
        url = image_url.get("url") or image_url.get("source") or ""
    elif isinstance(item.get("url"), str):
        url = item.get("url") or ""
    if isinstance(url, str) and url.strip():
        return url.strip()

    return ""


def mcp_rpc_sync(
    server_url: str,
    method: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, Any]] = None,
    timeout_sec: Optional[float] = None
) -> Dict[str, Any]:
    if not server_url:
        raise ValueError("MCP server_url is required")
    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": method,
        "params": params or {}
    }
    data, _ = _post_json_sync(server_url, payload, headers=headers, timeout_sec=timeout_sec)
    if not isinstance(data, dict):
        raise RuntimeError("MCP response is not an object")
    if data.get("error"):
        raise RuntimeError(f"MCP error: {json.dumps(data.get('error'), ensure_ascii=False)}")
    if "result" not in data:
        raise RuntimeError("MCP response missing result")
    result = data.get("result")
    if not isinstance(result, dict):
        return {"value": result}
    return result


def list_tools_sync(server_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    server_url = server_cfg.get("server_url")
    _ensure_mcp_session_sync(server_cfg)
    headers = _build_mcp_headers(server_cfg)
    try:
        result = mcp_rpc_sync(server_url, "tools/list", params={}, headers=headers)
    except Exception as exc:
        if _is_invalid_session_error(exc):
            server_cfg.pop("session_id", None)
            _ensure_mcp_session_sync(server_cfg)
            headers = _build_mcp_headers(server_cfg)
            result = mcp_rpc_sync(server_url, "tools/list", params={}, headers=headers)
        else:
            raise
    tools = result.get("tools")
    if isinstance(tools, list):
        return [tool for tool in tools if isinstance(tool, dict)]
    raise RuntimeError("MCP tools/list result missing tools list")
