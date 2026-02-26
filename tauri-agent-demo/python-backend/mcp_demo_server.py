import base64
import io
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image, ImageDraw


app = FastAPI()

TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the provided text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to echo back."}
            }
        },
        "annotations": {"readOnly": True}
    },
    {
        "name": "image_demo",
        "description": "Generate a small PNG image (base64).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "size": {"type": "number", "description": "Image size in pixels (16-512)."},
                "color": {"type": "string", "description": "Background color, e.g. #5b8def."}
            }
        },
        "annotations": {"readOnly": True}
    }
]


def _rpc_result(req_id: Any, result: Dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "Invalid JSON")

    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, "Invalid Request")

    req_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if method == "tools/list":
        return _rpc_result(req_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}

        if name == "echo":
            text = args.get("text", "")
            return _rpc_result(req_id, {"content": [{"type": "text", "text": str(text)}]})

        if name == "image_demo":
            try:
                size = int(args.get("size", 96) or 96)
            except Exception:
                size = 96
            size = max(16, min(512, size))
            color = str(args.get("color") or "#5b8def")
            img = Image.new("RGB", (size, size), color)
            draw = ImageDraw.Draw(img)
            draw.rectangle([0, 0, size - 1, size - 1], outline="#111111")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = base64.b64encode(buf.getvalue()).decode("ascii")
            return _rpc_result(
                req_id,
                {"content": [{"type": "image", "data": data, "mimeType": "image/png"}]}
            )

        return _rpc_error(req_id, -32601, f"Unknown tool: {name}")

    return _rpc_error(req_id, -32601, f"Unknown method: {method}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
