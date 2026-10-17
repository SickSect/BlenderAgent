"""asset_core — Blender-independent core of the AI asset assistant.

Everything here is plain stdlib Python so it can run in three places:
inside the repo CLI, inside the sidecar worker process spawned by the
Blender add-on, and (later) next to other DCC tools.
"""

from .session import ChatSession, build_messages
from .transport import BackendConfig, TransportError, stream_chat

__all__ = [
    "BackendConfig",
    "ChatSession",
    "TransportError",
    "build_messages",
    "stream_chat",
]
