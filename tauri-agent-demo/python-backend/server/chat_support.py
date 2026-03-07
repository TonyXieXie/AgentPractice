import base64
import json
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from app_config import get_app_config
from llm_client import create_llm_client
from models import ChatSessionUpdate, LLMConfig
from repositories import chat_repository, session_repository


TITLE_MAX_CHARS = 40
TITLE_FALLBACK_CHARS = 20
TITLE_REQUEST_TIMEOUT = 15.0


def truncate_text(text: str, max_chars: int) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip()


def fallback_title(user_message: str) -> str:
    base = (user_message or "").strip().splitlines()[0] if user_message else ""
    if not base:
        return "New Chat"
    if len(base) > TITLE_FALLBACK_CHARS:
        return base[:TITLE_FALLBACK_CHARS].rstrip() + "..."
    return base


def clean_title(raw_title: str) -> str:
    title = (raw_title or "").strip().strip('"').strip("'")
    title = title.splitlines()[0].strip() if title else ""
    for prefix in (
        "标题：",
        "标题:",
        "Title:",
        "title:",
        "题目：",
        "题目:",
        "主题：",
        "主题:",
    ):
        if title.lower().startswith(prefix.lower()):
            title = title[len(prefix):].strip()
            break
    title = title.rstrip(" .,!?:;" + "，。！？；：")
    if len(title) > TITLE_MAX_CHARS:
        title = title[:TITLE_MAX_CHARS].rstrip() + "..."
    return title


def strip_json_fence(text: str) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return cleaned


def extract_json_slice(text: str) -> str:
    if not text:
        return ""
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return ""


def parse_title_json(raw: str) -> str:
    if not raw:
        return ""
    candidate = strip_json_fence(raw)
    for chunk in (candidate, extract_json_slice(candidate)):
        if not chunk:
            continue
        try:
            data = json.loads(chunk)
            if isinstance(data, dict):
                title = data.get("title")
                if isinstance(title, str) and title.strip():
                    return title.strip()
        except Exception:
            continue
    return ""


def looks_like_title(raw: str) -> bool:
    if not raw:
        return False
    text = raw.strip()
    if not text:
        return False
    if "\n" in text or "\r" in text:
        return False
    if len(text) > (TITLE_MAX_CHARS + 5):
        return False
    bad_markers = ["分析", "步骤", "最终", "结论", "Reasoning", "analysis", "step", "Title:", "标题", "选项"]
    for marker in bad_markers:
        if marker in text:
            return False
    return True


def split_data_url(value: str) -> Tuple[Optional[str], str]:
    if not value:
        return None, ""
    if value.startswith("data:") and "," in value:
        header, payload = value.split(",", 1)
        mime = header[5:].split(";")[0].strip() if ";" in header else header[5:].strip()
        return mime or None, payload
    return None, value


def prepare_attachment_input(item: Any) -> Optional[Dict[str, Any]]:
    if not item:
        return None
    raw_data = getattr(item, "data_base64", None) or ""
    inferred_mime, payload = split_data_url(raw_data)
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
        "size": size,
    }


def convert_image_for_llm(data: bytes, mime: str) -> Optional[Tuple[str, bytes]]:
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


def build_llm_user_content(text: str, image_urls: List[str]) -> Any:
    if not image_urls:
        return text
    items: List[Dict[str, Any]] = []
    if text:
        items.append({"type": "text", "text": text})
    for url in image_urls:
        items.append({"type": "image_url", "image_url": {"url": url}})
    return items


def collect_prepared_attachments(attachments: Optional[List[Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    prepared_items: List[Dict[str, Any]] = []
    llm_image_urls: List[str] = []
    if not attachments:
        return prepared_items, llm_image_urls

    for item in attachments:
        prepared = prepare_attachment_input(item)
        if not prepared:
            continue
        prepared_items.append(prepared)

        converted = convert_image_for_llm(prepared.get("data") or b"", prepared.get("mime") or "")
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


def save_prepared_attachments(message_id: int, prepared_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    saved_meta: List[Dict[str, Any]] = []
    for prepared in prepared_items:
        saved = chat_repository.save_message_attachment(
            message_id=message_id,
            name=prepared.get("name"),
            mime=prepared.get("mime"),
            data=prepared.get("data") or b"",
            width=prepared.get("width"),
            height=prepared.get("height"),
            size=prepared.get("size"),
        )
        saved_meta.append(saved)
    return saved_meta


async def generate_title(
    config: LLMConfig,
    user_message: str,
    assistant_message: str,
    session_id: Optional[str] = None,
    message_id: Optional[int] = None,
) -> Optional[str]:
    system_role = "developer" if config.api_profile == "openai" else "system"
    system_prompt = (
        "You generate concise chat titles. "
        "Output only the title. "
        "Use the user's language. "
        "3-12 words or <=20 Chinese characters. "
        "No quotes, no emojis, no trailing punctuation."
    )
    user_excerpt = truncate_text(user_message, 600)
    assistant_excerpt = truncate_text(assistant_message, 800)
    user_prompt = (
        "User message:\n"
        f"{user_excerpt}\n\n"
        "Assistant reply:\n"
        f"{assistant_excerpt}\n\n"
        "Title:"
    )
    messages = [
        {"role": system_role, "content": system_prompt},
        {"role": "user", "content": user_prompt},
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
                "iteration": 0,
            }
        }
    result = await client.chat(messages, request_overrides)
    raw_content = result.get("content", "") if isinstance(result, dict) else ""
    parsed_title = parse_title_json(raw_content)
    if parsed_title:
        return clean_title(parsed_title)
    if looks_like_title(raw_content):
        return clean_title(raw_content)
    return ""


async def maybe_update_session_title(
    session_id: str,
    config: LLMConfig,
    user_message: str,
    assistant_message: str,
    is_first_turn: bool,
    assistant_message_id: Optional[int] = None,
) -> None:
    if not is_first_turn:
        return
    current = session_repository.get_session(session_id)
    if not current or not is_first_turn:
        return
    current_title = (current.title or "").strip()
    provisional_title = fallback_title(user_message)
    if current_title not in ("New Chat", provisional_title):
        return
    app_cfg = get_app_config()
    llm_app_config = app_cfg.get("llm", {}) if isinstance(app_cfg, dict) else {}
    auto_title_enabled = llm_app_config.get("auto_title_enabled", True)
    if not auto_title_enabled:
        if provisional_title and provisional_title != current.title:
            session_repository.update_session(session_id, ChatSessionUpdate(title=provisional_title))
        return
    title = ""
    try:
        title = await generate_title(
            config,
            user_message,
            assistant_message,
            session_id=session_id,
            message_id=assistant_message_id,
        )
    except Exception:
        title = ""
    if not title:
        title = fallback_title(user_message)
    if title and title != current.title:
        session_repository.update_session(session_id, ChatSessionUpdate(title=title))


__all__ = [
    "build_llm_user_content",
    "clean_title",
    "collect_prepared_attachments",
    "fallback_title",
    "generate_title",
    "looks_like_title",
    "maybe_update_session_title",
    "parse_title_json",
    "save_prepared_attachments",
    "split_data_url",
    "truncate_text",
]
