"""Minimal server-side tool registry for workflow skills.

The model never calls a tool directly. The workflow state machine asks the chat
route to execute a registered tool, and the registry enforces the skill's
``allowed_tools`` whitelist.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

from packages.dialogue.store import DialogueStore
from packages.integrations.mailtrap.client import MailtrapMailer
from packages.platform.store import PlatformStore
from packages.skills.models import Skill


@dataclass(frozen=True)
class ToolContext:
    tenant: Any
    tenant_id: int
    session_id: str
    dialogue: DialogueStore
    llm: Any
    mailer: MailtrapMailer
    store: PlatformStore
    skill: Skill
    question: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    message: str
    request_id: str = ""
    contact_request_id: int | None = None
    duplicate: bool = False
    error: str = ""


_TOOLS: dict[str, tuple[str, str]] = {
    "send_contact_request": ("packages.skills.contact_request", "send_contact_request_tool"),
}


def call_tool(
    name: str,
    *,
    skill: Skill,
    payload: dict[str, Any],
    context: ToolContext,
) -> ToolResult:
    if name not in skill.allowed_tools:
        raise PermissionError(f"tool {name} is not allowed for skill {skill.name}")
    target = _TOOLS.get(name)
    if target is None:
        raise KeyError(f"unknown tool {name}")
    module = importlib.import_module(target[0])
    function = getattr(module, target[1])
    result = function(payload=payload, context=context)
    if not isinstance(result, ToolResult):
        raise TypeError(f"tool {name} returned {type(result)!r}")
    return result
