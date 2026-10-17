"""Chat history helpers shared by the CLI and the Blender sidecar."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

Message = Dict[str, str]


def build_messages(
    history: List[Message],
    system_prompt: str,
    max_chars: int = 24000,
) -> List[Message]:
    """Turn history into the message list sent to the model.

    The newest messages are always kept (at least two, so a follow-up still
    has its question); older ones are dropped once the character budget is
    spent.
    """
    picked: List[Message] = []
    total = len(system_prompt)
    for message in reversed(history):
        size = len(message.get("content", "")) + 40  # roles + json overhead
        if len(picked) >= 2 and total + size > max_chars:
            break
        picked.append(message)
        total += size
    return [{"role": "system", "content": system_prompt}, *reversed(picked)]


@dataclass
class ChatSession:
    """Minimal conversation holder (used by tests and the CLI)."""

    messages: List[Message] = field(default_factory=list)

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    def clear(self) -> None:
        self.messages.clear()

    def build(
        self, system_prompt: str, max_chars: int = 24000
    ) -> List[Message]:
        return build_messages(self.messages, system_prompt, max_chars)
