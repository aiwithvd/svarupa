"""One setup target per AI coding tool, and `ide`, which installs the ones a
repository uses. Every tool gets the same rules text; tools with a project
MCP config get Svarupa's server merged in next to the user's own."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from svarupa.setup.base import Merge, Target
from svarupa.setup.rules import rules_body
from svarupa.setup.skill import SKILL_MD

__all__ = [
    "AgentsTarget",
    "ClaudeTarget",
    "CopilotTarget",
    "CursorTarget",
    "IdeTarget",
    "WindsurfTarget",
]

ENTRY: dict[str, object] = {"command": "svarupa", "args": ["mcp", ".svarupa"]}
_FIRST = (
    "Run `svarupa .` so `.svarupa/graph.json` exists, and install the MCP extra "
    "(`pip install 'svarupa[mcp]'`) so the server can start."
)
_REFRESH = "After the design changes, run `svarupa setup ide --update`."


class _ToolTarget(Target):
    """A target whose files carry the shared rules text, computed at most once."""

    def __init__(self, root: Path = Path(), body: str | None = None) -> None:
        super().__init__(root)
        self._body = body

    def _rules(self) -> str:
        if self._body is None:
            self._body = rules_body(self.root)
        return self._body


class AgentsTarget(_ToolTarget):
    name: ClassVar[str] = "agents"
    summary: ClassVar[str] = "rules for AGENTS.md readers (Codex and others), as a marked block"

    def files(self) -> tuple[tuple[str, str], ...]:
        return ()

    def merges(self) -> tuple[Merge, ...]:
        return (Merge("AGENTS.md", "block", body=self._rules()),)

    def next_steps(self) -> tuple[str, ...]:
        snippet = '[mcp_servers.svarupa]\ncommand = "svarupa"\nargs = ["mcp", ".svarupa"]'
        return (
            "Commit AGENTS.md.",
            _REFRESH,
            f"For Codex MCP, add to ~/.codex/config.toml:\n{snippet}",
        )


class ClaudeTarget(_ToolTarget):
    name: ClassVar[str] = "claude"
    summary: ClassVar[str] = "Claude Code: CLAUDE.md block, the skill, and .mcp.json"

    def files(self) -> tuple[tuple[str, str], ...]:
        return ((".claude/skills/svarupa/SKILL.md", SKILL_MD),)

    def merges(self) -> tuple[Merge, ...]:
        return (
            Merge("CLAUDE.md", "block", body=self._rules()),
            Merge(".mcp.json", "mcp", key="mcpServers", entry=ENTRY),
        )

    def next_steps(self) -> tuple[str, ...]:
        return (
            "Commit CLAUDE.md, .claude/skills/svarupa/SKILL.md and .mcp.json.",
            _FIRST,
            _REFRESH,
        )


class CursorTarget(_ToolTarget):
    name: ClassVar[str] = "cursor"
    summary: ClassVar[str] = "Cursor: .cursor/rules/svarupa.mdc and .cursor/mcp.json"

    def files(self) -> tuple[tuple[str, str], ...]:
        head = "---\ndescription: Svarupa architecture and health rules for this repository\nalwaysApply: true\n---\n\n"
        return ((".cursor/rules/svarupa.mdc", head + self._rules()),)

    def merges(self) -> tuple[Merge, ...]:
        return (Merge(".cursor/mcp.json", "mcp", key="mcpServers", entry=ENTRY),)

    def next_steps(self) -> tuple[str, ...]:
        return ("Commit .cursor/rules/svarupa.mdc and .cursor/mcp.json.", _FIRST, _REFRESH)


class CopilotTarget(_ToolTarget):
    name: ClassVar[str] = "copilot"
    summary: ClassVar[str] = (
        "GitHub Copilot: copilot-instructions.md block and .vscode/mcp.json"
    )

    def files(self) -> tuple[tuple[str, str], ...]:
        return ()

    def merges(self) -> tuple[Merge, ...]:
        return (
            Merge(".github/copilot-instructions.md", "block", body=self._rules()),
            Merge(".vscode/mcp.json", "mcp", key="servers", entry={"type": "stdio", **ENTRY}),
        )

    def next_steps(self) -> tuple[str, ...]:
        return (
            "Commit .github/copilot-instructions.md and .vscode/mcp.json.",
            _FIRST,
            _REFRESH,
        )


class WindsurfTarget(_ToolTarget):
    name: ClassVar[str] = "windsurf"
    summary: ClassVar[str] = "Windsurf: .windsurf/rules/svarupa.md (MCP config is global)"

    def files(self) -> tuple[tuple[str, str], ...]:
        return (
            (".windsurf/rules/svarupa.md", "---\ntrigger: always_on\n---\n\n" + self._rules()),
        )

    def next_steps(self) -> tuple[str, ...]:
        snippet = (
            '{\n  "mcpServers": {\n    "svarupa": {\n      "command": "svarupa",\n'
            '      "args": ["mcp", ".svarupa"]\n    }\n  }\n}'
        )
        return (
            "Commit .windsurf/rules/svarupa.md.",
            f"For Windsurf MCP, add to ~/.codeium/windsurf/mcp_config.json:\n{snippet}",
            _FIRST,
            _REFRESH,
        )


_TOOLS: tuple[type[_ToolTarget], ...] = (
    ClaudeTarget,
    CursorTarget,
    CopilotTarget,
    WindsurfTarget,
)


def _detected(root: Path, tool: type[_ToolTarget]) -> bool:
    signs = {
        "claude": ("CLAUDE.md", ".claude"),
        "cursor": (".cursor",),
        "copilot": (".vscode", ".github/copilot-instructions.md"),
        "windsurf": (".windsurf",),
    }[tool.name]
    return any((root / s).exists() for s in signs)


class IdeTarget(Target):
    name: ClassVar[str] = "ide"
    summary: ClassVar[str] = (
        "every AI coding tool this repository uses (AGENTS.md always; --all for all)"
    )

    def __init__(self, root: Path = Path(), every: bool = False) -> None:
        super().__init__(root)
        self.every = every
        self._selected: list[Target] | None = None

    def selected(self) -> list[Target]:
        """The sub-targets, built once and sharing one analysis of the repository."""
        if self._selected is None:
            body = rules_body(self.root)
            tools = [
                t(self.root, body) for t in _TOOLS if self.every or _detected(self.root, t)
            ]
            self._selected = [AgentsTarget(self.root, body), *tools]
        return self._selected

    def files(self) -> tuple[tuple[str, str], ...]:
        return tuple(f for t in self.selected() for f in t.files())

    def merges(self) -> tuple[Merge, ...]:
        return tuple(m for t in self.selected() for m in t.merges())

    def next_steps(self) -> tuple[str, ...]:
        seen: list[str] = []
        for t in self.selected():
            seen += [s for s in t.next_steps() if s not in seen]
        return tuple(seen)
