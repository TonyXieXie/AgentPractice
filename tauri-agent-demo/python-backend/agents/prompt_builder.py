from typing import Any, Dict, List, Optional, Tuple
import re

from app_config import get_app_config
from tools.base import Tool

PROMPT_MODULE_ORDER = [
    "persona",
    "domain_knowledge",
    "constraints",
    "tooling",
    "tool_policy",
    "workflow",
    "output_format",
    "localization",
    "examples"
]

PROMPT_MODULE_TITLES = {
    "persona": "Persona",
    "domain_knowledge": "Domain Knowledge",
    "constraints": "Constraints",
    "tooling": "Tools",
    "tool_policy": "Tool Usage",
    "workflow": "Workflow",
    "output_format": "Output Requirements",
    "localization": "Localization",
    "examples": "Examples"
}

_TEMPLATE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _render_template(text: str, context: Dict[str, Any]) -> str:
    if not text:
        return ""

    def replacer(match: re.Match) -> str:
        key = match.group(1)
        value = context.get(key)
        if value is None:
            return match.group(0)
        return str(value)

    return _TEMPLATE_PATTERN.sub(replacer, text)


def _resolve_profile(agent_config: Dict[str, Any], profile_id: Optional[str]) -> Tuple[Dict[str, Any], Optional[str]]:
    profiles = _as_list(agent_config.get("profiles"))
    chosen: Optional[Dict[str, Any]] = None
    resolved_id: Optional[str] = None

    if profile_id:
        for profile in profiles:
            if isinstance(profile, dict) and profile.get("id") == profile_id:
                chosen = profile
                resolved_id = profile_id
                break

    if chosen is None:
        default_id = agent_config.get("default_profile")
        if default_id:
            for profile in profiles:
                if isinstance(profile, dict) and profile.get("id") == default_id:
                    chosen = profile
                    resolved_id = default_id
                    break

    if chosen is None and profiles:
        for profile in profiles:
            if isinstance(profile, dict):
                chosen = profile
                resolved_id = profile.get("id")
                break

    return chosen or {}, resolved_id


def _collect_abilities(agent_config: Dict[str, Any], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    ability_ids = _as_list(profile.get("abilities"))
    abilities = _as_list(agent_config.get("abilities"))
    ability_map: Dict[str, Dict[str, Any]] = {}
    for ability in abilities:
        if isinstance(ability, dict) and ability.get("id"):
            ability_map[str(ability.get("id"))] = ability

    resolved: List[Dict[str, Any]] = []
    for ability_id in ability_ids:
        ability = ability_map.get(str(ability_id))
        if ability:
            resolved.append(ability)
    return resolved


def _resolve_tool_list(abilities: List[Dict[str, Any]], all_tools: List[Tool], include_tools: bool) -> List[Tool]:
    if not include_tools:
        return []

    include_all = False
    tool_names: List[str] = []

    for ability in abilities:
        tools = ability.get("tools")
        if not isinstance(tools, list):
            continue
        for name in tools:
            if not isinstance(name, str):
                continue
            normalized = name.strip()
            if not normalized:
                continue
            if normalized in ("*", "all"):
                include_all = True
                continue
            if normalized not in tool_names:
                tool_names.append(normalized)

    if include_all:
        return list(all_tools)

    if not tool_names:
        return []

    tool_map = {tool.name: tool for tool in all_tools}
    return [tool_map[name] for name in tool_names if name in tool_map]


def _build_tool_lines(tools: List[Tool]) -> List[str]:
    if not tools:
        return []
    lines = ["Available tools:"]
    for tool in tools:
        desc = _normalize_text(getattr(tool, "description", ""))
        if desc:
            lines.append(f"- {tool.name}: {desc}")
        else:
            lines.append(f"- {tool.name}")
    lines.append("Tool definitions are provided separately via the API tools field.")
    return lines


def _build_prompt_context(
    profile: Dict[str, Any],
    tools: List[Tool],
    extra_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    tool_names = ", ".join([tool.name for tool in tools]) if tools else "(no tools available)"
    tool_list = "\n".join(_build_tool_lines(tools)) if tools else ""
    profile_name = _normalize_text(profile.get("name") or profile.get("id"))
    context = {
        "tool_names": tool_names,
        "tool_list": tool_list,
        "tool_count": len(tools),
        "profile_name": profile_name
    }
    if isinstance(extra_context, dict):
        context.update(extra_context)
    return context


def build_system_prompt(
    agent_config: Dict[str, Any],
    profile: Dict[str, Any],
    abilities: List[Dict[str, Any]],
    tools: List[Tool],
    include_tools: bool = True,
    extra_context: Optional[Dict[str, Any]] = None
) -> str:
    base_prompt = _normalize_text(agent_config.get("base_system_prompt"))
    has_tools = include_tools and bool(tools)

    prompt_context = _build_prompt_context(profile, tools, extra_context)
    profile_params = _as_dict(profile.get("params"))

    module_chunks: Dict[str, List[str]] = {}
    for ability in abilities:
        prompt_text = _normalize_text(ability.get("prompt"))
        if not prompt_text:
            continue
        ability_type = _normalize_text(ability.get("type")) or "misc"
        ability_params = _as_dict(ability.get("params"))
        context = {**prompt_context, **profile_params, **ability_params}
        rendered = _render_template(prompt_text, context).strip()
        if rendered:
            module_chunks.setdefault(ability_type, []).append(rendered)

    lines: List[str] = []
    if base_prompt:
        lines.append(base_prompt)

    def append_module(module_type: str, content: str) -> None:
        if not content:
            return
        title = PROMPT_MODULE_TITLES.get(module_type)
        if not title:
            title = module_type.replace("_", " ").title()
        lines.append(f"## {title}\n{content}")

    for module_type in PROMPT_MODULE_ORDER:
        if module_type == "tooling":
            if not include_tools or not has_tools:
                continue
            tool_prompts = module_chunks.get(module_type, [])
            tool_lines = _build_tool_lines(tools)
            if tool_prompts:
                tool_lines.append("")
                tool_lines.append("\n".join(tool_prompts))
            append_module(module_type, "\n".join(tool_lines).strip())
            continue

        if module_type == "tool_policy" and (not include_tools or not has_tools):
            continue

        prompts = module_chunks.get(module_type, [])
        if prompts:
            append_module(module_type, "\n".join(prompts).strip())

    for module_type, prompts in module_chunks.items():
        if module_type in PROMPT_MODULE_ORDER:
            continue
        if module_type == "tool_policy" and (not include_tools or not has_tools):
            continue
        if module_type == "tooling":
            continue
        if prompts:
            append_module(module_type, "\n".join(prompts).strip())

    return "\n\n".join([line for line in lines if line]).strip()


def build_agent_prompt_and_tools(
    profile_id: Optional[str],
    all_tools: List[Tool],
    include_tools: bool = True,
    extra_context: Optional[Dict[str, Any]] = None,
    exclude_ability_ids: Optional[List[str]] = None
) -> Tuple[str, List[Tool], Optional[str], List[str]]:
    agent_config = _as_dict(get_app_config().get("agent"))
    profile, resolved_id = _resolve_profile(agent_config, profile_id)
    abilities = _collect_abilities(agent_config, profile)
    ability_ids = [
        str(ability.get("id"))
        for ability in abilities
        if isinstance(ability, dict) and ability.get("id")
    ]
    exclude = set([str(item) for item in (exclude_ability_ids or []) if item])
    prompt_abilities = [
        ability
        for ability in abilities
        if str(ability.get("id")) not in exclude
    ]
    tools = _resolve_tool_list(abilities, all_tools, include_tools)
    tools = [tool for tool in tools if tool.name != "code_ast"]
    prompt = build_system_prompt(
        agent_config,
        profile,
        prompt_abilities,
        tools,
        include_tools=include_tools,
        extra_context=extra_context
    )
    return prompt, tools, resolved_id, ability_ids
