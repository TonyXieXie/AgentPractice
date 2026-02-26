from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re


@dataclass
class Skill:
    name: str
    description: str
    content: str
    path: str
    source: str
    enabled: bool = True


_INVOCATION_PATTERN = re.compile(r"(^|\s)\\([A-Za-z0-9_-]+)")


def _get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _coerce_enabled(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("false", "0", "no", "off", "disabled"):
            return False
        if lowered in ("true", "1", "yes", "on", "enabled"):
            return True
    return True


def _parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end_index = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_index = idx
            break
    if end_index is None:
        return {}, text
    meta_lines = lines[1:end_index]
    body = "\n".join(lines[end_index + 1:]).lstrip()
    meta: Dict[str, str] = {}
    for raw_line in meta_lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(("|", ">")):
            value = value[1:].strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1].strip()
        meta[key] = value
    return meta, body


def _fallback_name(path: Path, source_root: Path) -> str:
    parent = path.parent
    if parent == source_root:
        return path.stem
    if parent.name.lower() in ("agent", "skill"):
        return path.stem
    return parent.name or path.stem


def _load_skill(path: Path, source: str, source_root: Path) -> Optional[Skill]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    meta, body = _parse_frontmatter(raw)
    name = meta.get("name", "").strip() or _fallback_name(path, source_root)
    description = meta.get("description", "").strip()
    enabled = _coerce_enabled(meta.get("enabled"))
    return Skill(
        name=name,
        description=description,
        content=body.strip(),
        path=str(path),
        source=source,
        enabled=enabled
    )


def _discover_in_dir(root: Path, source: str) -> List[Skill]:
    if not root.exists() or not root.is_dir():
        return []
    skills: List[Skill] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.lower() != "skill.md":
            continue
        skill = _load_skill(path, source, root)
        if skill:
            skills.append(skill)
    return skills


def dedupe_skills(skills: List[Skill]) -> List[Skill]:
    deduped: Dict[str, Skill] = {}
    for skill in skills:
        key = skill.name.strip().lower()
        if not key:
            continue
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = skill
            continue
        if existing.source != "agent" and skill.source == "agent":
            deduped[key] = skill
    return list(deduped.values())


def discover_skills() -> List[Skill]:
    root = _get_project_root()
    discovered: List[Skill] = []
    discovered.extend(_discover_in_dir(root / "Skill", "Skill"))
    discovered.extend(_discover_in_dir(root / "agent", "agent"))
    return dedupe_skills(discovered)


def get_enabled_skills() -> List[Skill]:
    return [skill for skill in discover_skills() if skill.enabled]


def list_skills(enabled_only: bool = True) -> List[Dict[str, Any]]:
    skills = discover_skills()
    if enabled_only:
        skills = [skill for skill in skills if skill.enabled]
    skills.sort(key=lambda item: item.name.lower())
    return [
        {
            "name": skill.name,
            "description": skill.description,
            "source": skill.source,
            "path": skill.path
        }
        for skill in skills
    ]


def get_skill_by_name(name: str, enabled_only: bool = True) -> Optional[Skill]:
    if not name:
        return None
    target = name.strip().lower()
    for skill in discover_skills():
        if skill.name.strip().lower() == target:
            if enabled_only and not skill.enabled:
                return None
            return skill
    return None


def extract_skill_invocations(text: str) -> List[str]:
    if not text:
        return []
    matches = _INVOCATION_PATTERN.finditer(text)
    seen: set = set()
    ordered: List[str] = []
    for match in matches:
        name = match.group(2)
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return ordered


def build_skill_prompt_sections(enabled_skills: List[Skill], invoked_skills: List[Skill]) -> str:
    sections: List[str] = []
    if enabled_skills:
        skill_lines = ["## Skills"]
        for skill in sorted(enabled_skills, key=lambda item: item.name.lower()):
            desc = skill.description.strip()
            if desc:
                skill_lines.append(f"- {skill.name}: {desc}")
            else:
                skill_lines.append(f"- {skill.name}")
        sections.append("\n".join(skill_lines))

    if invoked_skills:
        active_blocks: List[str] = []
        for skill in invoked_skills:
            content = skill.content.strip()
            if content:
                active_blocks.append(f"### {skill.name}\n{content}")
            else:
                active_blocks.append(f"### {skill.name}")
        sections.append("## Active Skills\n" + "\n\n".join(active_blocks))

    return "\n\n".join([section for section in sections if section]).strip()
