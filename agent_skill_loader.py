"""Load project-local skills for the patent agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class LoadedSkill:
    name: str
    path: Path
    content: str
    description: str = ""


def load_agent_skills(project_root: Path | None = None) -> list[LoadedSkill]:
    """Discover and load every project-local skills/*/SKILL.md file."""
    project_root = project_root or Path.cwd()
    skills_root = project_root / "skills"
    priority = {
        "patent-quality-review": 0,
        "patent-writing": 1,
        "interactive-drafting": 2,
        "agent-planning": 3,
        "material-assessment": 4,
        "prior-art-analysis": 5,
    }
    candidates = sorted(
        skills_root.glob("*/SKILL.md"),
        key=lambda path: (priority.get(path.parent.name, 100), path.parent.name),
    )

    skills: list[LoadedSkill] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.expanduser()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        try:
            content = resolved.read_text(encoding="utf-8")
        except OSError:
            continue
        skills.append(
            LoadedSkill(
                name=_frontmatter_value(content, "name") or resolved.parent.name,
                path=resolved,
                content=content,
                description=_frontmatter_value(content, "description"),
            )
        )
    return skills


def _frontmatter_value(content: str, key: str) -> str:
    match = re.match(r"^---\s*\n(.*?)\n---", content, flags=re.DOTALL)
    if not match:
        return ""
    for line in match.group(1).splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def format_skills_for_prompt(skills: list[LoadedSkill], max_chars: int = 32000) -> str:
    blocks = []
    budget = max_chars
    per_skill_limit = max(3000, max_chars // max(len(skills), 1))
    for skill in skills:
        text = skill.content.strip()
        if not text:
            continue
        if len(text) > per_skill_limit:
            text = text[:per_skill_limit].rstrip() + "\n...[skill truncated]"
        block = f"## Skill: {skill.name}\nPath: {skill.path}\n\n{text}"
        if len(block) > budget:
            block = block[:budget].rstrip() + "\n...[truncated]"
        blocks.append(block)
        budget -= len(block)
        if budget <= 0:
            break
    return "\n\n---\n\n".join(blocks) if blocks else "未加载到外部 skill，使用内置专利 agent 规则。"
