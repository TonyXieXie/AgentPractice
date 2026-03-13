from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from agents.profile import AgentProfile

if TYPE_CHECKING:
    from agents.execution.tool_executor import ToolExecutor
    from repositories.agent_profile_repository import AgentProfileRepository


@dataclass(slots=True)
class HandoffTargetDescriptor:
    profile_id: str
    display_name: str
    description: str
    topics: tuple[str, ...]
    auto_topic: Optional[str]
    is_current: bool
    is_handoff_ready: bool
    unavailable_reason: str = ""


@dataclass(slots=True)
class ResolvedHandoffTarget:
    profile: AgentProfile
    topic: str


def render_tool_availability_text(tool_executor: "ToolExecutor") -> str:
    tool_names = [tool.name for tool in tool_executor.list_tools() if str(tool.name or "").strip()]
    if not tool_names:
        return "No tools are available for this profile."
    return (
        "Available tools for this profile: "
        + ", ".join(tool_names)
        + ". Do not call tools outside this set."
    )


async def describe_handoff_targets(
    profile_repository: Optional["AgentProfileRepository"],
    *,
    current_profile_id: Optional[str] = None,
) -> list[HandoffTargetDescriptor]:
    if profile_repository is None:
        return []
    profiles = await profile_repository.list_all()
    assistant_profiles = sorted(
        (profile for profile in profiles if _normalize_text(profile.agent_type) == "assistant"),
        key=lambda profile: profile.id,
    )
    return [
        _describe_profile(profile, current_profile_id=current_profile_id)
        for profile in assistant_profiles
    ]


async def render_handoff_catalog_text(
    profile_repository: Optional["AgentProfileRepository"],
    *,
    current_profile_id: Optional[str] = None,
) -> str:
    targets = await describe_handoff_targets(
        profile_repository,
        current_profile_id=current_profile_id,
    )
    if not targets:
        return "Handoff target catalog is unavailable."

    lines = [
        "Handoff guidance:",
        "- Use handoff when transferring task ownership to another assistant profile.",
        "- Low-level RPC and event routing are handled internally by the backend.",
        "Handoff targets:",
    ]
    for target in targets:
        status = _format_target_status(target)
        title = f"{target.profile_id} ({target.display_name})"
        if target.description:
            lines.append(f"- {title}: {status}. {target.description}")
        else:
            lines.append(f"- {title}: {status}.")
    return "\n".join(lines)


async def resolve_handoff_target(
    profile_repository: Optional["AgentProfileRepository"],
    *,
    target_profile: str,
    current_profile_id: Optional[str] = None,
) -> ResolvedHandoffTarget:
    if profile_repository is None:
        raise RuntimeError("handoff requires AgentProfileRepository")

    normalized_target = _normalize_text(target_profile)
    if not normalized_target:
        raise RuntimeError("handoff requires target_profile")

    profile = await profile_repository.get(normalized_target)
    if profile is None:
        raise RuntimeError(f"handoff target profile not found: {normalized_target}")
    if _normalize_text(profile.agent_type) != "assistant":
        raise RuntimeError(f"handoff target must be an assistant profile: {normalized_target}")

    normalized_current = _normalize_text(current_profile_id)
    if normalized_current and normalized_current == profile.id:
        raise RuntimeError(
            f"handoff target must differ from current profile: {normalized_target}"
        )

    topics = tuple(_normalize_topics(profile.executable_event_topics))
    if not topics:
        raise RuntimeError(
            f"handoff target profile has no executable event topic: {normalized_target}"
        )
    if len(topics) != 1:
        joined = ", ".join(topics)
        raise RuntimeError(
            "handoff target profile must expose exactly one executable event topic: "
            f"{normalized_target} ({joined})"
        )

    return ResolvedHandoffTarget(profile=profile, topic=topics[0])


def _describe_profile(
    profile: AgentProfile,
    *,
    current_profile_id: Optional[str],
) -> HandoffTargetDescriptor:
    display_name = _normalize_text(profile.display_name) or profile.id
    description = _normalize_text(profile.description)
    topics = tuple(_normalize_topics(profile.executable_event_topics))
    normalized_current = _normalize_text(current_profile_id)
    is_current = bool(normalized_current and normalized_current == profile.id)
    auto_topic = topics[0] if len(topics) == 1 else None
    is_handoff_ready = (not is_current) and auto_topic is not None

    unavailable_reason = ""
    if is_current:
        unavailable_reason = "current profile"
    elif not topics:
        unavailable_reason = "no executable event topic"
    elif len(topics) != 1:
        unavailable_reason = "multiple executable event topics"

    return HandoffTargetDescriptor(
        profile_id=profile.id,
        display_name=display_name,
        description=description,
        topics=topics,
        auto_topic=auto_topic,
        is_current=is_current,
        is_handoff_ready=is_handoff_ready,
        unavailable_reason=unavailable_reason,
    )


def _format_target_status(target: HandoffTargetDescriptor) -> str:
    if target.is_current:
        return "current profile; unavailable as handoff target"
    if target.is_handoff_ready and target.auto_topic:
        return f"handoff-ready via topic `{target.auto_topic}`"
    if target.topics:
        return (
            f"unavailable; {target.unavailable_reason}; topics: "
            + ", ".join(f"`{topic}`" for topic in target.topics)
        )
    return f"unavailable; {target.unavailable_reason}"


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_topics(values: object) -> list[str]:
    if isinstance(values, str):
        text = _normalize_text(values)
        return [text] if text else []
    if not isinstance(values, list):
        return []
    return [text for item in values if (text := _normalize_text(item))]
