from fastapi import FastAPI, HTTPException, Response, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
import os
import argparse
import shlex
import base64
from io import BytesIO
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from pathlib import Path
import traceback

from PIL import Image

from models import (
    LLMConfig, LLMConfigCreate, LLMConfigUpdate,
    ChatMessage, ChatMessageCreate,
    ChatSession, ChatSessionCreate, ChatSessionUpdate,
    ChatRequest, ChatResponse, ExportRequest,
    ToolPermissionRequest, ToolPermissionRequestUpdate,
    ChatStopRequest, RollbackRequest, PatchRevertRequest, AstRequest, AstNotifyRequest, AstSettingsRequest
)
from database import db
from llm_client import create_llm_client
from message_processor import message_processor

from agents.executor import create_agent_executor
from agents.prompt_builder import build_agent_prompt_and_tools
from agents.base import AgentStep
from tools.builtin import register_builtin_tools
from tools.base import ToolRegistry
from tools.config import get_tool_config, update_tool_config, get_tool_config_path
from tools.context import set_tool_context, reset_tool_context
from tools.builtin.system_tools import ApplyPatchTool, CodeAstTool
from stream_control import stream_stop_registry
from app_config import get_app_config, update_app_config, get_app_config_path
from ghost_snapshot import restore_snapshot
from code_map import build_code_map_prompt
from ast_index import get_ast_index
from ast_settings import get_ast_settings, update_ast_settings

app = FastAPI(title="Tauri Agent Chat Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_builtin_tools()

# ==================== AST Cache ====================

def _schedule_ast_scan(work_path: Optional[str]) -> None:
    if not work_path:
        return
    try:
        get_ast_index().ensure_root(work_path)
    except Exception:
        # Avoid breaking requests if AST cache fails.
        pass

# ==================== Local File Read ====================

@app.get("/local-file")
def read_local_file(path: str = Query(...), max_bytes: int = Query(2_000_000)):
    if not path:
        raise HTTPException(status_code=400, detail="Missing path")
    safe_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(safe_path):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        size = os.path.getsize(safe_path)
        if max_bytes and size > max_bytes:
            raise HTTPException(status_code=413, detail="File too large")
        with open(safe_path, "rb") as file:
            raw = file.read(max_bytes + 1 if max_bytes else None)
        if max_bytes and len(raw) > max_bytes:
            raise HTTPException(status_code=413, detail="File too large")
        content = raw.decode("utf-8", errors="replace")
        return {"content": content}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {exc}")

@app.get("/local-file-exists")
def local_file_exists(path: str = Query(...)):
    if not path:
        raise HTTPException(status_code=400, detail="Missing path")
    safe_path = os.path.abspath(os.path.expanduser(path))
    return {"exists": os.path.isfile(safe_path)}

# ==================== Title Generation ====================

TITLE_MAX_CHARS = 40
TITLE_FALLBACK_CHARS = 20
TITLE_REQUEST_TIMEOUT = 15.0


def _truncate_text(text: str, max_chars: int) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip()


def _fallback_title(user_message: str) -> str:
    base = (user_message or "").strip().splitlines()[0] if user_message else ""
    if not base:
        return "New Chat"
    if len(base) > TITLE_FALLBACK_CHARS:
        return base[:TITLE_FALLBACK_CHARS].rstrip() + "..."
    return base


def _clean_title(raw_title: str) -> str:
    title = (raw_title or "").strip().strip('"').strip("'")
    title = title.splitlines()[0].strip() if title else ""
    for prefix in (
        "\u6807\u9898\uff1a",
        "\u6807\u9898:",
        "Title:",
        "title:",
        "\u9898\u76ee\uff1a",
        "\u9898\u76ee:",
        "\u4e3b\u9898\uff1a",
        "\u4e3b\u9898:"
    ):
        if title.lower().startswith(prefix.lower()):
            title = title[len(prefix):].strip()
            break
    title = title.rstrip(" .,!?:;" + "\uFF0C\u3002\uFF01\uFF1F\uFF1B\uFF1A")
    if len(title) > TITLE_MAX_CHARS:
        title = title[:TITLE_MAX_CHARS].rstrip() + "..."
    return title


def _strip_json_fence(text: str) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return cleaned


def _extract_json_slice(text: str) -> str:
    if not text:
        return ""
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return ""


def _parse_title_json(raw: str) -> str:
    if not raw:
        return ""
    import json as _json
    candidate = _strip_json_fence(raw)
    for chunk in (candidate, _extract_json_slice(candidate)):
        if not chunk:
            continue
        try:
            data = _json.loads(chunk)
            if isinstance(data, dict):
                title = data.get("title")
                if isinstance(title, str) and title.strip():
                    return title.strip()
        except Exception:
            continue
    return ""


def _extract_command_name(command: str) -> str:
    if not command:
        return ""
    try:
        parts = shlex.split(command, posix=False)
    except Exception:
        parts = command.strip().split()
    if not parts:
        return ""
    first = parts[0].strip().strip('"').strip("'")
    base = os.path.basename(first).lower()
    if base.endswith(".exe") or base.endswith(".cmd") or base.endswith(".bat"):
        base = os.path.splitext(base)[0]
    return base


def _looks_like_title(raw: str) -> bool:
    if not raw:
        return False
    text = raw.strip()
    if not text:
        return False
    if "\n" in text or "\r" in text:
        return False
    if len(text) > (TITLE_MAX_CHARS + 5):
        return False
    bad_markers = [
        "\u5206\u6790",  # 分析
        "\u6b65\u9aa4",  # 步骤
        "\u6700\u7ec8",  # 最终
        "\u7ed3\u8bba",  # 结论
        "Reasoning",
        "analysis",
        "step",
        "Title:",
        "\u6807\u9898",
        "\u9009\u9879"   # 选项
    ]
    for marker in bad_markers:
        if marker in text:
            return False
    return True


def _split_data_url(value: str) -> Tuple[Optional[str], str]:
    if not value:
        return None, ""
    if value.startswith("data:") and "," in value:
        header, payload = value.split(",", 1)
        mime = header[5:].split(";")[0].strip() if ";" in header else header[5:].strip()
        return mime or None, payload
    return None, value


def _prepare_attachment_input(item: Any) -> Optional[Dict[str, Any]]:
    if not item:
        return None
    raw_data = getattr(item, "data_base64", None) or ""
    inferred_mime, payload = _split_data_url(raw_data)
    mime = (getattr(item, "mime", None) or inferred_mime or "application/octet-stream").strip()
    payload = payload.strip()
    if not payload:
        return None
    try:
        decoded = base64.b64decode(payload)
    except Exception:
        return None

    width = getattr(item, "width", None)
    height = getattr(item, "height", None)
    if (width is None or height is None) and mime.startswith("image/"):
        try:
            with Image.open(BytesIO(decoded)) as img:
                width, height = img.size
        except Exception:
            pass

    size = getattr(item, "size", None)
    if size is None:
        size = len(decoded)

    return {
        "name": getattr(item, "name", None),
        "mime": mime,
        "data": decoded,
        "width": width,
        "height": height,
        "size": size
    }


def _convert_image_for_llm(data: bytes, mime: str) -> Optional[Tuple[str, bytes]]:
    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            output = BytesIO()
            if has_alpha:
                if img.mode not in ("RGBA", "LA"):
                    img = img.convert("RGBA")
                img.save(output, format="PNG")
                return "image/png", output.getvalue()
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(output, format="JPEG", quality=92)
            return "image/jpeg", output.getvalue()
    except Exception:
        return None


def _build_llm_user_content(text: str, image_urls: List[str]) -> Any:
    if not image_urls:
        return text
    items: List[Dict[str, Any]] = []
    if text:
        items.append({"type": "text", "text": text})
    for url in image_urls:
        items.append({"type": "image_url", "image_url": {"url": url}})
    return items


def _collect_prepared_attachments(attachments: Optional[List[Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    prepared_items: List[Dict[str, Any]] = []
    llm_image_urls: List[str] = []
    if not attachments:
        return prepared_items, llm_image_urls

    for item in attachments:
        prepared = _prepare_attachment_input(item)
        if not prepared:
            continue
        prepared_items.append(prepared)

        converted = _convert_image_for_llm(prepared.get("data") or b"", prepared.get("mime") or "")
        if not converted:
            raw_mime = (prepared.get("mime") or "").lower()
            if raw_mime in ("image/png", "image/jpeg", "image/jpg"):
                mime = "image/jpeg" if raw_mime == "image/jpg" else raw_mime
                converted = (mime, prepared.get("data") or b"")
        if converted:
            mime, out_data = converted
            data_url = f"data:{mime};base64,{base64.b64encode(out_data).decode('ascii')}"
            llm_image_urls.append(data_url)

    return prepared_items, llm_image_urls


def _save_prepared_attachments(message_id: int, prepared_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    saved_meta: List[Dict[str, Any]] = []
    for prepared in prepared_items:
        saved = db.save_message_attachment(
            message_id=message_id,
            name=prepared.get("name"),
            mime=prepared.get("mime"),
            data=prepared.get("data") or b"",
            width=prepared.get("width"),
            height=prepared.get("height"),
            size=prepared.get("size")
        )
        saved_meta.append(saved)
    return saved_meta


def _build_thumbnail(data: bytes, max_size: int = 360) -> Optional[Tuple[str, bytes]]:
    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            img.thumbnail((max_size, max_size))
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            output = BytesIO()
            if has_alpha:
                if img.mode not in ("RGBA", "LA"):
                    img = img.convert("RGBA")
                img.save(output, format="PNG")
                return "image/png", output.getvalue()
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(output, format="JPEG", quality=85)
            return "image/jpeg", output.getvalue()
    except Exception:
        return None


CONTEXT_SUMMARY_PROMPT = (
    "你是对话摘要助手。请将对话压缩为可供后续继续对话的简明摘要。\n"
    "- 只总结用户与助手之间的对话内容\n"
    "- 保留关键目标、已做结论、关键事实、约束、待办、代码/文件/命令\n"
    "- 不要包含系统提示词或工具调用过程\n"
    "- 输出纯摘要文本，不要添加标题或前缀"
)

CONTEXT_SUMMARY_MARKER = "[Context Summary]"
CONTEXT_COMPRESS_KEEP_RECENT_CALLS = 10
CONTEXT_COMPRESS_STEP_CALLS = 5


def _estimate_tokens_for_text(text: str) -> int:
    if not text:
        return 0
    ascii_count = 0
    non_ascii = 0
    for ch in text:
        if ord(ch) <= 0x7F:
            ascii_count += 1
        else:
            non_ascii += 1
    return (ascii_count + 3) // 4 + non_ascii


def _estimate_tokens_for_messages(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        total += 4
        total += _estimate_tokens_for_text(str(msg.get("content") or ""))
    return total


def _format_dialogue_for_summary(messages: List[Dict[str, Any]]) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role")
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            prefix = "User"
        elif role == "assistant":
            prefix = "Assistant"
        else:
            continue
        lines.append(f"{prefix}: {content}")
    return "\n".join(lines)


def _build_context_summary_request(summary: str, dialogue_text: str) -> List[Dict[str, str]]:
    parts = []
    if summary:
        parts.append(f"已有摘要：\n{summary}")
    if dialogue_text:
        parts.append(f"新增对话：\n{dialogue_text}")
    combined = "\n\n".join(parts).strip()
    if not combined:
        combined = "请生成摘要。"
    user_prompt = f"{combined}\n\n请输出更新后的摘要，只输出摘要正文。"
    return [
        {"role": "system", "content": CONTEXT_SUMMARY_PROMPT},
        {"role": "user", "content": user_prompt}
    ]


async def _run_context_summary(
    llm_client: Any,
    summary: str,
    dialogue_messages: List[Dict[str, Any]]
) -> Optional[str]:
    dialogue_text = _format_dialogue_for_summary(dialogue_messages)
    if not dialogue_text and not summary:
        return None
    request_messages = _build_context_summary_request(summary, dialogue_text)
    try:
        result = await llm_client.chat(request_messages)
    except Exception as exc:
        print(f"[Context Compress] LLM request failed: {exc}")
        return None
    content = str(result.get("content") or "").strip()
    return content or None


async def _maybe_compress_context(
    session_id: str,
    config: LLMConfig,
    app_config: Dict[str, Any],
    llm_client: Any,
    current_summary: str,
    last_compressed_call_id: Optional[int],
    current_user_message_id: int,
    current_user_text: str
) -> Tuple[str, Optional[int], Optional[int], bool]:
    context_cfg = app_config.get("context", {}) if isinstance(app_config, dict) else {}
    if not context_cfg.get("compression_enabled"):
        return current_summary, last_compressed_call_id, None, False

    try:
        start_pct = int(context_cfg.get("compress_start_pct", 75))
    except (TypeError, ValueError):
        start_pct = 75
    try:
        target_pct = int(context_cfg.get("compress_target_pct", 55))
    except (TypeError, ValueError):
        target_pct = 55
    try:
        min_keep_messages = int(context_cfg.get("min_keep_messages", 1))
    except (TypeError, ValueError):
        min_keep_messages = 1
    try:
        keep_recent_calls = int(context_cfg.get("keep_recent_calls", CONTEXT_COMPRESS_KEEP_RECENT_CALLS))
    except (TypeError, ValueError):
        keep_recent_calls = CONTEXT_COMPRESS_KEEP_RECENT_CALLS
    try:
        step_calls = int(context_cfg.get("step_calls", CONTEXT_COMPRESS_STEP_CALLS))
    except (TypeError, ValueError):
        step_calls = CONTEXT_COMPRESS_STEP_CALLS

    if keep_recent_calls < 0:
        keep_recent_calls = 0
    if step_calls < 1:
        step_calls = 1

    max_tokens = getattr(config, "max_context_tokens", 0) or 0
    if max_tokens <= 0:
        return current_summary, last_compressed_call_id, None, False

    summary = current_summary or ""
    last_call_id = int(last_compressed_call_id or 0)
    last_message_id = db.get_max_message_id_for_llm_call(session_id, last_call_id) if last_call_id else None

    def build_uncompressed_messages(after_id: Optional[int]) -> List[Dict[str, Any]]:
        messages = db.get_dialogue_messages_after(session_id, after_id)
        return [msg for msg in messages if msg.get("id") != current_user_message_id]

    uncompressed = build_uncompressed_messages(last_message_id)
    initial_tokens = _estimate_tokens_for_text(summary) + _estimate_tokens_for_messages(uncompressed)
    if current_user_text:
        initial_tokens += _estimate_tokens_for_text(current_user_text)
    if initial_tokens < (start_pct / 100.0) * max_tokens:
        return summary, last_compressed_call_id, last_message_id, False

    keep_window = keep_recent_calls
    did_compress = False

    while True:
        calls_after = db.get_llm_call_metas_after(session_id, last_call_id)
        if len(calls_after) <= keep_window:
            break

        protected_calls = calls_after[-keep_window:] if keep_window > 0 else []
        protected_message_ids = {call["message_id"] for call in protected_calls if call.get("message_id")}
        compressible_calls = calls_after[:-keep_window] if keep_window > 0 else calls_after

        boundary_call = None
        for call in reversed(compressible_calls):
            message_id = call.get("message_id")
            if message_id and message_id not in protected_message_ids:
                boundary_call = call
                break
        if not boundary_call:
            break

        boundary_call_id = int(boundary_call["id"])
        boundary_message_id = db.get_max_message_id_for_llm_call(session_id, boundary_call_id)
        if not boundary_message_id:
            break

        messages_between = db.get_dialogue_messages_between(
            session_id,
            (last_message_id or 0) + 1,
            boundary_message_id
        )
        if not messages_between:
            break

        compressible_assistant_ids = {
            call["message_id"]
            for call in compressible_calls
            if call.get("message_id") and call["id"] <= boundary_call_id and call["message_id"] not in protected_message_ids
        }
        if not compressible_assistant_ids:
            break

        id_to_index = {msg["id"]: idx for idx, msg in enumerate(messages_between)}
        compressible_message_ids = set()
        for assistant_id in compressible_assistant_ids:
            idx = id_to_index.get(assistant_id)
            if idx is None:
                continue
            compressible_message_ids.add(assistant_id)
            for back_idx in range(idx - 1, -1, -1):
                if messages_between[back_idx]["role"] == "user":
                    compressible_message_ids.add(messages_between[back_idx]["id"])
                    break

        compressible_message_ids.discard(current_user_message_id)
        if not compressible_message_ids:
            break

        compress_messages = [
            msg for msg in messages_between if msg["id"] in compressible_message_ids
        ]
        if not compress_messages:
            break

        uncompressed_after = build_uncompressed_messages(boundary_message_id)
        if len(uncompressed_after) < min_keep_messages:
            break

        new_summary = await _run_context_summary(llm_client, summary, compress_messages)
        if not new_summary:
            break

        summary = new_summary
        last_call_id = boundary_call_id
        last_message_id = boundary_message_id
        did_compress = True

        uncompressed_after = build_uncompressed_messages(last_message_id)
        token_count = _estimate_tokens_for_text(summary) + _estimate_tokens_for_messages(uncompressed_after)
        if current_user_text:
            token_count += _estimate_tokens_for_text(current_user_text)
        if token_count <= (target_pct / 100.0) * max_tokens:
            break

        if keep_window <= 0:
            break
        keep_window = max(0, keep_window - step_calls)

    return summary, last_call_id if did_compress else last_compressed_call_id, last_message_id, did_compress


async def _generate_title(
    config: LLMConfig,
    user_message: str,
    assistant_message: str,
    session_id: Optional[str] = None,
    message_id: Optional[int] = None
) -> Optional[str]:
    system_role = "developer" if config.api_profile == "openai" else "system"
    system_prompt = (
        "You generate concise chat titles. "
        "Output only the title. "
        "Use the user's language. "
        "3-12 words or <=20 Chinese characters. "
        "No quotes, no emojis, no trailing punctuation."
    )
    user_excerpt = _truncate_text(user_message, 600)
    assistant_excerpt = _truncate_text(assistant_message, 800)
    user_prompt = (
        "User message:\n"
        f"{user_excerpt}\n\n"
        "Assistant reply:\n"
        f"{assistant_excerpt}\n\n"
        "Title:"
    )
    messages = [
        {"role": system_role, "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    client = create_llm_client(config)
    client.timeout = TITLE_REQUEST_TIMEOUT
    request_overrides = None
    if session_id:
        request_overrides = {
            "_debug": {
                "session_id": session_id,
                "message_id": message_id,
                "agent_type": "title",
                "iteration": 0
            }
        }
    result = await client.chat(messages, request_overrides)
    raw_content = result.get("content", "") if isinstance(result, dict) else ""
    parsed_title = _parse_title_json(raw_content)
    if parsed_title:
        return _clean_title(parsed_title)
    if _looks_like_title(raw_content):
        return _clean_title(raw_content)
    return ""


async def _maybe_update_session_title(
    session_id: str,
    config: LLMConfig,
    user_message: str,
    assistant_message: str,
    is_first_turn: bool,
    assistant_message_id: Optional[int] = None
) -> None:
    if not is_first_turn:
        return
    current = db.get_session(session_id)
    if not current or not is_first_turn:
        return
    current_title = (current.title or "").strip()
    provisional_title = _fallback_title(user_message)
    if current_title not in ("New Chat", provisional_title):
        return
    title = ""
    try:
        title = await _generate_title(
            config,
            user_message,
            assistant_message,
            session_id=session_id,
            message_id=assistant_message_id
        )
    except Exception:
        title = ""
    if not title:
        title = _fallback_title(user_message)
    if title and title != current.title:
        db.update_session(session_id, ChatSessionUpdate(title=title))

# ==================== Base routes ====================

@app.get("/")
def read_root():
    return {"status": "FastAPI is running!", "version": "2.2", "app_config": True}

@app.get("/__debug/info")
def debug_info():
    tool_config = get_tool_config()
    return {
        "file": __file__,
        "cwd": os.getcwd(),
        "routes": [route.path for route in app.routes],
        "tool_config_path": get_tool_config_path(),
        "app_config_path": get_app_config_path(),
        "tools_enabled": tool_config.get("enabled", {}),
        "tool_names": [tool.name for tool in ToolRegistry.get_all()]
    }

# ==================== LLM Configs ====================

@app.get("/configs", response_model=List[LLMConfig])
def get_configs():
    return db.get_all_configs()

@app.get("/configs/default", response_model=LLMConfig)
def get_default_config():
    config = db.get_default_config()
    if not config:
        configs = db.get_all_configs()
        if configs:
            return configs[0]
        raise HTTPException(status_code=404, detail="No config available")
    return config

@app.get("/configs/{config_id}", response_model=LLMConfig)
def get_config(config_id: str):
    config = db.get_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    return config

@app.post("/configs", response_model=LLMConfig)
def create_config(config: LLMConfigCreate):
    return db.create_config(config)

@app.put("/configs/{config_id}", response_model=LLMConfig)
def update_config(config_id: str, update: LLMConfigUpdate):
    config = db.update_config(config_id, update)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    return config

@app.delete("/configs/{config_id}")
def delete_config(config_id: str):
    sessions = db.get_all_sessions()
    if any(s.config_id == config_id for s in sessions):
        raise HTTPException(status_code=400, detail="Config is in use by sessions")

    if db.delete_config(config_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="Config not found")

# ==================== Sessions ====================

@app.get("/sessions", response_model=List[ChatSession])
def get_sessions():
    return db.get_all_sessions()

@app.get("/sessions/{session_id}", response_model=ChatSession)
def get_session(session_id: str):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session

@app.post("/sessions", response_model=ChatSession)
def create_session(session: ChatSessionCreate):
    config = db.get_config(session.config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    created = db.create_session(session)
    _schedule_ast_scan(created.work_path)
    return created

@app.put("/sessions/{session_id}", response_model=ChatSession)
def update_session(session_id: str, update: ChatSessionUpdate):
    if update.config_id is not None:
        config = db.get_config(update.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")
    session = db.update_session(session_id, update)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if update.work_path is not None:
        _schedule_ast_scan(session.work_path)
    return session

@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    if db.delete_session(session_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="Session not found")

@app.get("/sessions/{session_id}/messages", response_model=List[ChatMessage])
def get_session_messages(session_id: str, limit: Optional[int] = None):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return db.get_session_messages(session_id, limit)

@app.get("/sessions/{session_id}/llm_calls")
def get_session_llm_calls(session_id: str):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return db.get_session_llm_calls(session_id)

@app.get("/sessions/{session_id}/agent_steps")
def get_session_agent_steps(session_id: str):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return db.get_session_agent_steps(session_id)

@app.post("/sessions/{session_id}/rollback")
def rollback_session(session_id: str, request: RollbackRequest):
    target = db.get_message(session_id, request.message_id)
    if not target:
        raise HTTPException(status_code=404, detail="Message not found in session")
    if target.get("role") != "user":
        raise HTTPException(status_code=400, detail="Rollback target must be a user message.")

    snapshot = db.get_snapshot_for_rollback(session_id, request.message_id)
    if snapshot:
        try:
            restore_snapshot(snapshot.get("tree_hash"), snapshot.get("work_path"))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Snapshot restore failed: {str(e)}")

    result = db.rollback_session(session_id, request.message_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Message not found in session")
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    if snapshot:
        try:
            db.delete_file_snapshots_from(session_id, request.message_id)
        except Exception:
            pass
        result["snapshot_restored"] = True
    else:
        result["snapshot_restored"] = False

    try:
        db.update_session_context(session_id, None, None)
    except Exception:
        pass

    return result

# ==================== Attachments ====================

@app.get("/attachments/{attachment_id}")
def get_attachment(attachment_id: int, thumbnail: bool = False, max_size: int = 360):
    attachment = db.get_attachment(attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    data = attachment.get("data") or b""
    if isinstance(data, memoryview):
        data = data.tobytes()
    mime = attachment.get("mime") or "application/octet-stream"
    if thumbnail:
        thumb = _build_thumbnail(data, max_size=max_size)
        if thumb:
            mime, data = thumb
    return Response(content=data, media_type=mime)

# ==================== Chat ====================

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        new_session_created = False
        if request.session_id:
            session = db.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            if request.agent_profile is not None and request.agent_profile != getattr(session, "agent_profile", None):
                session = db.update_session(session.id, ChatSessionUpdate(agent_profile=request.agent_profile)) or session
        else:
            config_id = request.config_id
            if not config_id:
                default_config = db.get_default_config()
                if not default_config:
                    configs = db.get_all_configs()
                    if not configs:
                        raise HTTPException(status_code=400, detail="No config available")
                    config_id = configs[0].id
                else:
                    config_id = default_config.id

            session = db.create_session(ChatSessionCreate(
                title="New Chat",
                config_id=config_id,
                work_path=request.work_path,
                agent_profile=request.agent_profile
            ))
            new_session_created = True
            _schedule_ast_scan(session.work_path)
        is_first_turn = (session.message_count or 0) == 0

        config = db.get_config(session.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        processed_message = message_processor.preprocess_user_message(request.message)
        if new_session_created:
            provisional_title = _fallback_title(processed_message)
            if provisional_title and provisional_title != session.title:
                db.update_session(session.id, ChatSessionUpdate(title=provisional_title))

        prepared_attachments, llm_image_urls = _collect_prepared_attachments(request.attachments)
        user_content = _build_llm_user_content(processed_message, llm_image_urls)

        user_msg = db.create_message(ChatMessageCreate(
            session_id=session.id,
            role="user",
            content=processed_message
        ))
        _save_prepared_attachments(user_msg.id, prepared_attachments)

        history = db.get_session_messages(session.id, limit=20)
        history_for_llm = [
            {"role": msg.role, "content": msg.content}
            for msg in history[:-1]
        ]

        system_role = "developer" if config.api_profile == "openai" else "system"
        llm_messages = message_processor.build_messages_for_llm(
            user_message=processed_message,
            history=history_for_llm,
            system_prompt="You are a helpful AI assistant.",
            system_role=system_role
        )
        if llm_image_urls:
            llm_messages[-1]["content"] = user_content

        raw_request_data = {
            "model": config.model,
            "messages": llm_messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "api_format": config.api_format,
            "api_profile": config.api_profile
        }

        llm_client = create_llm_client(config)
        llm_overrides = {}
        llm_overrides["_debug"] = {
            "session_id": session.id,
            "message_id": user_msg.id,
            "agent_type": "simple",
            "iteration": 0
        }
        llm_result = await llm_client.chat(llm_messages, llm_overrides)

        llm_response = llm_result["content"]
        raw_response_data = llm_result["raw_response"]

        processed_response = message_processor.postprocess_llm_response(llm_response)

        assistant_msg = db.create_message(ChatMessageCreate(
            session_id=session.id,
            role="assistant",
            content=processed_response,
            raw_request=raw_request_data,
            raw_response=raw_response_data
        ))
        llm_call_id = llm_result.get("llm_call_id")
        if llm_call_id:
            db.update_llm_call_processed(llm_call_id, {"content": processed_response})

        await _maybe_update_session_title(
            session_id=session.id,
            config=config,
            user_message=processed_message,
            assistant_message=processed_response,
            is_first_turn=is_first_turn,
            assistant_message_id=assistant_msg.id
        )

        return ChatResponse(
            reply=processed_response,
            session_id=session.id,
            message_id=assistant_msg.id
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat error: {str(e)}")

@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    try:
        new_session_created = False
        if request.session_id:
            session = db.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            if request.agent_profile is not None and request.agent_profile != getattr(session, "agent_profile", None):
                session = db.update_session(session.id, ChatSessionUpdate(agent_profile=request.agent_profile)) or session
        else:
            config_id = request.config_id if request.config_id else db.get_all_configs()[0].id
            session = db.create_session(ChatSessionCreate(
                title="New Chat",
                config_id=config_id,
                work_path=request.work_path,
                agent_profile=request.agent_profile
            ))
            new_session_created = True
            _schedule_ast_scan(session.work_path)
        is_first_turn = (session.message_count or 0) == 0

        config = db.get_config(session.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        processed_message = message_processor.preprocess_user_message(request.message)
        if new_session_created:
            provisional_title = _fallback_title(processed_message)
            if provisional_title and provisional_title != session.title:
                db.update_session(session.id, ChatSessionUpdate(title=provisional_title))

        prepared_attachments, llm_image_urls = _collect_prepared_attachments(request.attachments)
        user_content = _build_llm_user_content(processed_message, llm_image_urls)

        history = db.get_session_messages(session.id, limit=20)
        history_for_llm = [
            {"role": msg.role, "content": msg.content}
            for msg in history
        ]

        system_role = "developer" if config.api_profile == "openai" else "system"
        llm_messages = message_processor.build_messages_for_llm(
            user_message=processed_message,
            history=history_for_llm,
            system_prompt="You are a helpful AI assistant.",
            system_role=system_role
        )
        if llm_image_urls:
            llm_messages[-1]["content"] = user_content

        raw_request_data = {
            "model": config.model,
            "messages": llm_messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True,
            "api_format": config.api_format,
            "api_profile": config.api_profile
        }

        user_msg = db.create_message(ChatMessageCreate(
            session_id=session.id,
            role="user",
            content=processed_message,
            raw_request=raw_request_data
        ))
        saved_attachments = _save_prepared_attachments(user_msg.id, prepared_attachments)

        async def generate():
            yield f"data: {json.dumps({'session_id': session.id, 'user_message_id': user_msg.id, 'user_attachments': saved_attachments})}\n\n"
            full_response = ""
            try:
                llm_client = create_llm_client(config)
                llm_overrides = {}
                llm_overrides["_debug"] = {
                    "session_id": session.id,
                    "message_id": user_msg.id,
                    "agent_type": "simple",
                    "iteration": 0
                }

                async for chunk in llm_client.chat_stream(llm_messages, llm_overrides):
                    full_response += chunk
                    yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"

                processed_response = message_processor.postprocess_llm_response(full_response)

                assistant_msg = db.create_message(ChatMessageCreate(
                    session_id=session.id,
                    role="assistant",
                    content=processed_response,
                    raw_response={
                        "content": processed_response,
                        "model": config.model,
                        "finish_reason": "stop"
                    }
                ))
                llm_call_id = llm_overrides.get("_debug", {}).get("llm_call_id")
                if llm_call_id:
                    db.update_llm_call_processed(llm_call_id, {"content": processed_response})

                await _maybe_update_session_title(
                    session_id=session.id,
                    config=config,
                    user_message=processed_message,
                    assistant_message=processed_response,
                    is_first_turn=is_first_turn,
                    assistant_message_id=assistant_msg.id
                )

                yield f"data: {json.dumps({'done': True, 'message_id': assistant_msg.id})}\n\n"
            except Exception as e:
                if full_response:
                    db.create_message(ChatMessageCreate(
                        session_id=session.id,
                        role="assistant",
                        content=full_response + "\n\n[stream interrupted]",
                        metadata={"error": str(e), "partial": True}
                    ))
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== Export ====================

@app.post("/export")
def export_chat_history(request: ExportRequest):
    try:
        if request.session_id:
            session = db.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            sessions = [session]
        else:
            sessions = db.get_all_sessions()

        export_data = []
        for session in sessions:
            messages = db.get_session_messages(session.id)
            config = db.get_config(session.config_id)

            session_data = {
                "session": {
                    "id": session.id,
                    "title": session.title,
                    "created_at": session.created_at,
                    "context_summary": getattr(session, "context_summary", None),
                    "last_compressed_llm_call_id": getattr(session, "last_compressed_llm_call_id", None),
                    "config": {
                        "name": config.name if config else "unknown",
                        "model": config.model if config else "unknown"
                    }
                },
                "messages": [
                    {
                        "role": msg.role,
                        "content": msg.content,
                        "timestamp": msg.timestamp
                    }
                    for msg in messages
                ]
            }
            export_data.append(session_data)

        if request.format == "json":
            content = json.dumps(export_data, ensure_ascii=False, indent=2)
            media_type = "application/json"
            filename = f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        elif request.format == "txt":
            lines = []
            for session_data in export_data:
                lines.append(f"========== {session_data['session']['title']} ==========")
                lines.append(f"Created: {session_data['session']['created_at']}")
                lines.append(f"Config: {session_data['session']['config']['name']} ({session_data['session']['config']['model']})")
                if session_data["session"].get("context_summary"):
                    lines.append("Context Summary:")
                    lines.append(session_data["session"]["context_summary"])
                    lines.append("")
                lines.append("")
                for msg in session_data['messages']:
                    role_name = "User" if msg['role'] == "user" else "Assistant"
                    lines.append(f"[{msg['timestamp']}] {role_name}:")
                    lines.append(msg['content'])
                    lines.append("")
                lines.append("\n")
            content = "\n".join(lines)
            media_type = "text/plain"
            filename = f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        elif request.format == "markdown":
            lines = []
            for session_data in export_data:
                lines.append(f"# {session_data['session']['title']}")
                lines.append(f"\n**Created:** {session_data['session']['created_at']}")
                lines.append(f"**Config:** {session_data['session']['config']['name']} ({session_data['session']['config']['model']})")
                if session_data["session"].get("context_summary"):
                    lines.append("\n**Context Summary:**")
                    lines.append(session_data["session"]["context_summary"])
                lines.append("\n---\n")
                for msg in session_data['messages']:
                    role_name = "User" if msg['role'] == "user" else "Assistant"
                    lines.append(f"## {role_name}")
                    lines.append(f"*{msg['timestamp']}*\n")
                    lines.append(msg['content'])
                    lines.append("\n")
                lines.append("\n---\n")
            content = "\n".join(lines)
            media_type = "text/markdown"
            filename = f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        else:
            raise HTTPException(status_code=400, detail="Unsupported export format")

        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export error: {str(e)}")

# ==================== Agent Chat (Streaming) ====================

@app.post("/chat/agent/stream")
async def chat_agent_stream(request: ChatRequest):
    try:
        new_session_created = False
        if request.session_id:
            session = db.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            if request.agent_profile is not None and request.agent_profile != getattr(session, "agent_profile", None):
                session = db.update_session(session.id, ChatSessionUpdate(agent_profile=request.agent_profile)) or session
        else:
            config_id = request.config_id or db.get_default_config().id
            session = db.create_session(ChatSessionCreate(
                title="New Chat",
                config_id=config_id,
                work_path=request.work_path,
                agent_profile=request.agent_profile
            ))
            new_session_created = True
        is_first_turn = (session.message_count or 0) == 0

        config = db.get_config(session.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        processed_message = message_processor.preprocess_user_message(request.message)
        if new_session_created:
            provisional_title = _fallback_title(processed_message)
            if provisional_title and provisional_title != session.title:
                db.update_session(session.id, ChatSessionUpdate(title=provisional_title))

        prepared_attachments, llm_image_urls = _collect_prepared_attachments(request.attachments)
        user_content = _build_llm_user_content(processed_message, llm_image_urls)

        user_msg = db.create_message(ChatMessageCreate(
            session_id=session.id,
            role="user",
            content=processed_message
        ))
        saved_attachments = _save_prepared_attachments(user_msg.id, prepared_attachments)

        context_summary = getattr(session, "context_summary", None) or ""
        last_compressed_call_id = getattr(session, "last_compressed_llm_call_id", None)
        last_compressed_message_id = db.get_max_message_id_for_llm_call(
            session.id,
            int(last_compressed_call_id or 0)
        ) if last_compressed_call_id else None

        agent_type = request.agent_type_override if hasattr(request, 'agent_type_override') else getattr(session, 'agent_type', 'react')
        profile_id = request.agent_profile or getattr(session, "agent_profile", None)
        include_tools = agent_type != "simple"
        system_prompt, tools, resolved_profile_id, ability_ids = build_agent_prompt_and_tools(
            profile_id,
            ToolRegistry.get_all(),
            include_tools=include_tools,
            exclude_ability_ids=["code_map"]
        )
        if resolved_profile_id and resolved_profile_id != getattr(session, "agent_profile", None):
            db.update_session(session.id, ChatSessionUpdate(agent_profile=resolved_profile_id))

        llm_client = create_llm_client(config)
        app_config = get_app_config()
        agent_config = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
        context_config = app_config.get("context", {}) if isinstance(app_config, dict) else {}
        ast_enabled = bool(agent_config.get("ast_enabled", True))
        code_map_cfg = agent_config.get("code_map", {}) if isinstance(agent_config, dict) else {}
        code_map_enabled = bool(code_map_cfg.get("enabled", True))
        code_map_prompt = None
        if "code_map" in ability_ids and ast_enabled and code_map_enabled:
            code_map_prompt = build_code_map_prompt(
                session.id,
                request.work_path or getattr(session, "work_path", None)
            )
        react_max_iterations = agent_config.get("react_max_iterations", 50)
        try:
            react_max_iterations = int(react_max_iterations)
        except (TypeError, ValueError):
            react_max_iterations = 50

        try:
            executor = create_agent_executor(
                agent_type=agent_type,
                llm_client=llm_client,
                tools=tools,
                max_iterations=react_max_iterations,
                system_prompt=system_prompt
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        def build_history_for_llm(
            summary: str,
            after_message_id: Optional[int],
            code_map: Optional[str]
        ) -> List[Dict[str, str]]:
            messages = db.get_dialogue_messages_after(session.id, after_message_id)
            filtered = [msg for msg in messages if msg.get("id") != user_msg.id]
            history = [{"role": msg.get("role"), "content": msg.get("content")} for msg in filtered]
            if summary:
                history.insert(0, {"role": "assistant", "content": f"{CONTEXT_SUMMARY_MARKER}\n{summary}"})
            if code_map:
                insert_index = 1 if summary else 0
                history.insert(insert_index, {"role": "assistant", "content": code_map})
            return history

        def stream_text_chunks(text: str, chunk_size: int = 1):
            if not text:
                return
            for i in range(0, len(text), chunk_size):
                yield text[i:i + chunk_size]

        async def event_generator():
            nonlocal context_summary, last_compressed_call_id, last_compressed_message_id
            sequence = 0
            final_answer = None
            assistant_msg_id = None
            saw_delta = False

            try:
                temp_assistant_msg = db.create_message(ChatMessageCreate(
                    session_id=session.id,
                    role="assistant",
                    content=""
                ))
                assistant_msg_id = temp_assistant_msg.id
                stop_event = stream_stop_registry.create(assistant_msg_id)
                yield f"data: {json.dumps({'session_id': session.id, 'user_message_id': user_msg.id, 'assistant_message_id': assistant_msg_id, 'user_attachments': saved_attachments})}\n\n"
                request_overrides = {
                    "_debug": {
                        "session_id": session.id,
                        "message_id": assistant_msg_id
                    },
                    "work_path": request.work_path or getattr(session, 'work_path', None)
                }
                request_overrides["prompt_truncation"] = {
                    "enabled": bool(context_config.get("truncate_long_data", True)),
                    "threshold": int(context_config.get("long_data_threshold", 4000) or 4000),
                    "head_chars": int(context_config.get("long_data_head_chars", 1200) or 1200),
                    "tail_chars": int(context_config.get("long_data_tail_chars", 800) or 800)
                }
                if request.extra_work_paths:
                    request_overrides["extra_work_paths"] = [str(p) for p in request.extra_work_paths if p]
                request_overrides["_stop_event"] = stop_event
                if llm_image_urls:
                    request_overrides["user_content"] = user_content
                if request.agent_mode is not None:
                    request_overrides["agent_mode"] = request.agent_mode
                if request.shell_unrestricted is not None:
                    request_overrides["shell_unrestricted"] = request.shell_unrestricted

                updated_summary, updated_call_id, updated_message_id, did_compress = await _maybe_compress_context(
                    session_id=session.id,
                    config=config,
                    app_config=app_config,
                    llm_client=llm_client,
                    current_summary=context_summary,
                    last_compressed_call_id=last_compressed_call_id,
                    current_user_message_id=user_msg.id,
                    current_user_text=processed_message
                )
                if did_compress:
                    context_summary = updated_summary
                    last_compressed_call_id = updated_call_id
                    last_compressed_message_id = updated_message_id
                    try:
                        db.update_session_context(session.id, context_summary, last_compressed_call_id)
                    except Exception as exc:
                        print(f"[Context Compress] Failed to update session context: {exc}")

                    compress_step = AgentStep(
                        step_type="observation",
                        content="正在进行上下文压缩...",
                        metadata={"context_compress": True}
                    )
                    db.save_agent_step(
                        message_id=assistant_msg_id,
                        step_type=compress_step.step_type,
                        content=compress_step.content,
                        sequence=sequence,
                        metadata=compress_step.metadata
                    )
                    yield f"data: {json.dumps(compress_step.to_dict())}\n\n"
                    sequence += 1

                history_for_llm = build_history_for_llm(context_summary, last_compressed_message_id, code_map_prompt)

                async for step in executor.run(
                    user_input=processed_message,
                    history=history_for_llm,
                    session_id=session.id,
                    request_overrides=request_overrides if request_overrides else None
                ):
                    if step.step_type.endswith("_delta"):
                        saw_delta = True
                        yield f"data: {json.dumps(step.to_dict())}\n\n"
                        continue

                    db.save_agent_step(
                        message_id=assistant_msg_id,
                        step_type=step.step_type,
                        content=step.content,
                        sequence=sequence,
                        metadata=step.metadata
                    )

                    if step.step_type == "action" and "tool" in step.metadata:
                        db.save_tool_call(
                            message_id=assistant_msg_id,
                            tool_name=step.metadata["tool"],
                            tool_input=step.metadata.get("input", ""),
                            tool_output=""
                        )

                    if step.step_type == "answer":
                        final_answer = step.content
                        if not saw_delta:
                            for chunk in stream_text_chunks(step.content, chunk_size=1):
                                yield f"data: {json.dumps({'step_type': 'answer_delta', 'content': chunk, 'metadata': step.metadata})}\n\n"
                        yield f"data: {json.dumps(step.to_dict())}\n\n"
                        sequence += 1
                        continue

                    if step.step_type == "error":
                        final_answer = step.content

                    yield f"data: {json.dumps(step.to_dict())}\n\n"
                    sequence += 1

                if final_answer and assistant_msg_id:
                    conn = db.get_connection()
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE chat_messages
                        SET content = ?
                        WHERE id = ?
                    ''', (final_answer, assistant_msg_id))
                    conn.commit()
                    conn.close()

                    await _maybe_update_session_title(
                        session_id=session.id,
                        config=config,
                        user_message=processed_message,
                        assistant_message=final_answer,
                        is_first_turn=is_first_turn,
                        assistant_message_id=assistant_msg_id
                    )

                yield f"data: {json.dumps({'done': True, 'session_id': session.id})}\n\n"

            except Exception as e:
                error_step = AgentStep(
                    step_type="error",
                    content=f"Agent failed: {str(e)}",
                    metadata={"error": str(e), "traceback": traceback.format_exc()}
                )
                yield f"data: {json.dumps(error_step.to_dict())}\n\n"
            finally:
                if assistant_msg_id:
                    stream_stop_registry.clear(assistant_msg_id)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")

# ==================== App Config ====================

@app.get("/app/config")
def get_app_config_route():
    return get_app_config()

@app.put("/app/config")
def set_app_config(payload: Dict[str, Any]):
    try:
        updated = update_app_config(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return updated

# ==================== Patch Revert ====================

@app.post("/patch/revert")
async def revert_patch(request: PatchRevertRequest):
    session = db.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    snapshot_restored = False
    snapshot_error = None
    if request.message_id:
        snapshot = db.get_file_snapshot(session.id, request.message_id)
        if snapshot:
            try:
                restore_snapshot(snapshot.get("tree_hash"), snapshot.get("work_path"))
                snapshot_restored = True
            except Exception as e:
                snapshot_error = str(e)

    result_text = ""
    result: Dict[str, Any] = {"ok": False}

    if not snapshot_restored:
        token = set_tool_context({
            "shell_unrestricted": False,
            "agent_mode": "default",
            "session_id": session.id,
            "work_path": session.work_path
        })
        try:
            tool = ApplyPatchTool()
            result_text = await tool.execute(json.dumps({"patch": request.revert_patch}))
        finally:
            reset_tool_context(token)

        try:
            result = json.loads(result_text)
        except Exception:
            result = {"ok": False, "error": result_text}

        if not result.get("ok"):
            detail = result.get("error", "Patch revert failed")
            if snapshot_error:
                detail = f"{detail} (snapshot restore failed: {snapshot_error})"
            raise HTTPException(status_code=400, detail=detail)
    else:
        result = {"ok": True, "snapshot_restored": True}
        result_text = json.dumps(result, ensure_ascii=False)

    user_msg = db.create_message(ChatMessageCreate(
        session_id=session.id,
        role="user",
        content="撤销最近一次 apply_patch 修改",
        metadata={"action": "revert_patch"}
    ))

    assistant_msg = db.create_message(ChatMessageCreate(
        session_id=session.id,
        role="assistant",
        content="已撤销最近一次修改。"
    ))

    db.save_agent_step(
        message_id=assistant_msg.id,
        step_type="observation",
        content=result_text,
        sequence=0,
        metadata={"tool": "apply_patch" if not snapshot_restored else "snapshot_restore", "patch_event": "revert"}
    )
    db.save_agent_step(
        message_id=assistant_msg.id,
        step_type="answer",
        content="已撤销最近一次修改。",
        sequence=1,
        metadata={"patch_event": "revert"}
    )

    return {
        "ok": True,
        "result": result,
        "user_message_id": user_msg.id,
        "assistant_message_id": assistant_msg.id
    }

# ==================== Tools ====================

@app.get("/tools")
def get_tools():
    tools = ToolRegistry.get_all()
    return [tool.to_dict() for tool in tools]

@app.post("/tools/ast")
async def run_ast(request: AstRequest):
    session = None
    if request.session_id:
        session = db.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

    work_path = request.work_path or (session.work_path if session else None)
    token = set_tool_context({
        "shell_unrestricted": False,
        "agent_mode": request.agent_mode or "default",
        "session_id": session.id if session else request.session_id,
        "work_path": work_path,
        "extra_work_paths": request.extra_work_paths
    })
    try:
        tool = CodeAstTool()
        payload: Dict[str, Any] = {"path": request.path}
        if request.mode:
            payload["mode"] = request.mode
        if request.language:
            payload["language"] = request.language
        if request.extensions:
            payload["extensions"] = request.extensions
        if request.max_files is not None:
            payload["max_files"] = request.max_files
        if request.max_symbols is not None:
            payload["max_symbols"] = request.max_symbols
        if request.max_nodes is not None:
            payload["max_nodes"] = request.max_nodes
        if request.max_depth is not None:
            payload["max_depth"] = request.max_depth
        if request.max_bytes is not None:
            payload["max_bytes"] = request.max_bytes
        if request.include_positions is not None:
            payload["include_positions"] = request.include_positions
        if request.include_text is not None:
            payload["include_text"] = request.include_text

        result_text = await tool.execute(json.dumps(payload))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AST tool error: {exc}")
    finally:
        reset_tool_context(token)

    try:
        return json.loads(result_text)
    except Exception:
        return {"ok": False, "error": result_text}

@app.post("/ast/notify")
def notify_ast(request: AstNotifyRequest):
    if not request.root:
        raise HTTPException(status_code=400, detail="Missing root")
    paths = request.paths or []
    try:
        updated = get_ast_index().notify_paths(request.root, paths)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AST notify failed: {exc}")
    return {"ok": True, "updated": updated}

@app.get("/ast/settings")
def get_ast_settings_route(root: str = Query(...)):
    if not root:
        raise HTTPException(status_code=400, detail="Missing root")
    if not Path(root).expanduser().exists():
        raise HTTPException(status_code=404, detail="Root path not found")
    try:
        settings = get_ast_settings(root)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AST settings error: {exc}")
    return {"ok": True, "root": settings.get("root"), "settings": settings}

@app.put("/ast/settings")
def update_ast_settings_route(request: AstSettingsRequest):
    if not request.root:
        raise HTTPException(status_code=400, detail="Missing root")
    if not Path(request.root).expanduser().exists():
        raise HTTPException(status_code=404, detail="Root path not found")
    patch = request.dict(exclude={"root"}, exclude_none=True)
    try:
        settings = update_ast_settings(request.root, patch)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AST settings update failed: {exc}")
    try:
        get_ast_index().ensure_root(request.root)
    except Exception:
        pass
    return {"ok": True, "root": settings.get("root"), "settings": settings}

@app.get("/ast/cache")
def get_ast_cache(root: str = Query(...), path: Optional[str] = None, include_payload: bool = False):
    if not root:
        raise HTTPException(status_code=400, detail="Missing root")
    if not Path(root).expanduser().exists():
        raise HTTPException(status_code=404, detail="Root path not found")
    try:
        app_config = get_app_config()
        agent_cfg = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
        ast_enabled = bool(agent_cfg.get("ast_enabled", True))
        if path:
            payload = get_ast_index().get_file_payload(root, path)
            if isinstance(payload, dict) and not ast_enabled:
                payload.setdefault("disabled", True)
            return payload
        entries = get_ast_index().get_root_entries(root, include_payload=include_payload)
        return {"ok": True, "root": root, "files": entries, "disabled": not ast_enabled}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AST cache error: {exc}")

@app.get("/ast/code-map")
def get_code_map(session_id: str = Query(...), root: str = Query(...)):
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")
    if not root:
        raise HTTPException(status_code=400, detail="Missing root")
    if not Path(root).expanduser().exists():
        raise HTTPException(status_code=404, detail="Root path not found")
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        prompt = build_code_map_prompt(session_id, root)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Code map error: {exc}")
    return {"ok": True, "prompt": prompt or ""}

@app.post("/chat/stop")
def stop_chat(request: ChatStopRequest):
    stopped = stream_stop_registry.stop(request.message_id)
    return {"stopped": stopped}

@app.get("/tools/config")
def get_tools_config():
    return get_tool_config()

@app.put("/tools/config")
def set_tools_config(payload: Dict[str, Any]):
    try:
        updated = update_tool_config(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ToolRegistry.clear()
    register_builtin_tools()
    return updated

@app.get("/tools/permissions", response_model=List[ToolPermissionRequest])
def get_tool_permissions(status: Optional[str] = None):
    return db.get_permission_requests(status=status)

@app.put("/tools/permissions/{request_id}", response_model=ToolPermissionRequest)
def update_tool_permission(request_id: int, update: ToolPermissionRequestUpdate):
    updated = db.update_permission_request(request_id, update.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Permission request not found")
    if update.status == "approved" and updated.get("tool_name") == "run_shell":
        cmd_name = _extract_command_name(updated.get("path") or "")
        if cmd_name:
            cfg = get_tool_config()
            allowlist = list(cfg.get("shell", {}).get("allowlist", []) or [])
            allowset = {str(item).lower() for item in allowlist}
            if cmd_name.lower() not in allowset:
                allowlist.append(cmd_name)
                try:
                    update_tool_config({"shell": {"allowlist": allowlist}})
                except Exception:
                    pass
    return updated

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tauri Agent Backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    print("Starting FastAPI server...")
    print("Supported LLMs: OpenAI, ZhipuAI, Deepseek")
    print(f"Database: SQLite ({os.getenv('TAURI_AGENT_DB_PATH', 'chat_app.db')})")
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
