"""Read-only skill sources shared by the Workbench agents.

Skills live on disk under ``skills/`` and are exposed to DeepAgents through a
virtual ``/skills`` route.  The agent may read a SKILL.md, but it may not write
to the skill tree; the route is backed by a FilesystemBackend so the files are
the single source of truth (no prompt copy-paste).
"""
from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"


def skill_backend():
    """Return a CompositeBackend with a read-only /skills route.

    Imported lazily so the optional DeepAgents dependency stays optional.
    """
    from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

    return CompositeBackend(
        default=StateBackend(),
        routes={"/skills/": FilesystemBackend(root_dir=str(SKILLS_DIR), virtual_mode=True)},
    )


def skill_permissions():
    """Deny writes under /skills; reads remain available to the agent."""
    from deepagents import FilesystemPermission

    return [FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny")]


def skill_sources() -> list[str]:
    return ["/skills/"]
