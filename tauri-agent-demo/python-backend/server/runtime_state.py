from pty_stream_registry import get_pty_stream_registry
from stream_registry import get_stream_registry
from ws_hub import get_ws_hub


STREAM_REGISTRY = get_stream_registry()
PTY_STREAM_REGISTRY = get_pty_stream_registry()
WS_HUB = get_ws_hub()

