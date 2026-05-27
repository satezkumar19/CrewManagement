"""
Skills loader for specialist agents.

Each specialist agent owns a folder under backend/agents/skills/<agent_name>/
containing:
- system_prompt.md - the agent's primary role and operating procedure.
- Any number of additional *.md files - individual skills.

A shared/ folder under backend/agents/skills/ holds skill files that
every specialist should load (glossary, error conventions, etc.).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

_SKILLS_ROOT = Path(__file__).resolve().parent
_SHARED_DIR = _SKILLS_ROOT / "shared"

_SECTION_SEPARATOR = "\n\n---\n\n"


def _read_markdown_files(directory: Path, exclude: Iterable[str] = ()) -> List[str]:
    if not directory.exists():
        return []
    excluded = set(exclude)
    chunks: List[str] = []
    for path in sorted(directory.glob("*.md")):
        if path.name in excluded:
            continue
        chunks.append(path.read_text(encoding="utf-8").strip())
    return chunks


def build_instructions(agent_name: str) -> str:
    """Assemble system prompt + agent skills + shared skills into one string."""
    agent_dir = _SKILLS_ROOT / agent_name
    if not agent_dir.is_dir():
        raise FileNotFoundError(
            f"Skills folder not found for agent '{agent_name}': {agent_dir}"
        )
    system_prompt_path = agent_dir / "system_prompt.md"
    if not system_prompt_path.is_file():
        raise FileNotFoundError(
            f"system_prompt.md missing for agent '{agent_name}' at {system_prompt_path}"
        )
    parts: List[str] = [system_prompt_path.read_text(encoding="utf-8").strip()]
    parts.extend(_read_markdown_files(agent_dir, exclude={"system_prompt.md"}))
    parts.extend(_read_markdown_files(_SHARED_DIR))
    return _SECTION_SEPARATOR.join(parts)


def list_agents() -> List[str]:
    """Return the names of all agent skill folders (excluding 'shared')."""
    return sorted(
        p.name
        for p in _SKILLS_ROOT.iterdir()
        if p.is_dir() and p.name != "shared" and not p.name.startswith("__")
    )


def list_skill_files(agent_name: str) -> List[str]:
    """Return markdown skill file basenames for an agent (no .md extension).

    Excludes system_prompt.md because that's the role definition, not a skill.
    Used by the monitoring API to surface skills as UI tags.
    """
    agent_dir = _SKILLS_ROOT / agent_name
    if not agent_dir.is_dir():
        return []
    return [
        p.stem
        for p in sorted(agent_dir.glob("*.md"))
        if p.name != "system_prompt.md"
    ]


if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "crew_matching"
    print(f"=== Instructions for '{name}' ===\n")
    print(build_instructions(name))
