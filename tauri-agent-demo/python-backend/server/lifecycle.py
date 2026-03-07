import asyncio
import atexit
import signal

from mcp_tools import register_mcp_tools_from_config
from skills import initialize_skill_cache, set_empty_skill_cache
from tools.pty_manager import get_pty_manager

from .runtime_state import PTY_STREAM_REGISTRY, WS_HUB


async def _init_mcp_tools_background() -> None:
    try:
        await asyncio.to_thread(register_mcp_tools_from_config)
        print("[MCP] Tools registered.")
    except Exception as exc:
        print(f"[MCP] Failed to register MCP tools: {exc}")


async def _init_skills_background() -> None:
    try:
        cached_skills = await asyncio.to_thread(initialize_skill_cache)
        skill_names = [skill.name for skill in cached_skills if skill.name]
        print(f"[Skills] loaded {len(skill_names)} skill(s): {', '.join(skill_names) if skill_names else 'none'}")
    except Exception as exc:
        print(f"[Skills] failed to load skills: {exc}")
        set_empty_skill_cache()


def _close_all_ptys() -> None:
    try:
        count = get_pty_manager().close_all()
        if count:
            print(f"[PTY] closed {count} sessions")
    except Exception:
        pass


def _register_process_cleanup() -> None:
    atexit.register(_close_all_ptys)
    try:
        signal.signal(signal.SIGTERM, lambda *_: _close_all_ptys())
        signal.signal(signal.SIGINT, lambda *_: _close_all_ptys())
    except Exception:
        pass


async def on_startup() -> None:
    try:
        WS_HUB.set_loop(asyncio.get_running_loop())
    except Exception:
        pass
    try:
        PTY_STREAM_REGISTRY.set_loop(asyncio.get_running_loop())
    except Exception:
        pass
    try:
        asyncio.create_task(_init_skills_background())
    except Exception as exc:
        print(f"[Skills] failed to start background load: {exc}")
    try:
        asyncio.create_task(_init_mcp_tools_background())
    except Exception as exc:
        print(f"[MCP] Failed to start background registration: {exc}")


def shutdown_cleanup() -> None:
    try:
        count = get_pty_manager().close_all()
        if count:
            print(f"[PTY] closed {count} sessions on shutdown")
    except Exception:
        pass


_register_process_cleanup()

