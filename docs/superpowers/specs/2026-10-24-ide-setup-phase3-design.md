# Svarupa: One-Command Setup for AI Coding Tools (phase 3)

Date: 2026-10-24
Status: design approved, spec written
Parent: `2026-10-03-system-health-umbrella-design.md` (phase 3); builds on
1d (agent rules, `svarupa check`) and phase 2 (CI gate)

## Goal

Whatever AI coding tool a team uses, one command teaches it Svarupa's
workflow and this repository's rules, and connects it to Svarupa's MCP
tools, without touching anything the team wrote.

## Decisions (maintainer, 2026-10-24)

| Question | Decision |
|---|---|
| Tools | Claude Code, Cursor, GitHub Copilot (VS Code), Windsurf, and AGENTS.md readers (Codex and others); `svarupa setup ide` detects and installs |
| Rules text | The fixed workflow plus a generated section with this repository's rules; `--update` refreshes it |
| Existing files | A marked block in Markdown files; a merged `svarupa` entry in MCP JSON; invalid JSON is refused |

## 1. Targets

| Target | Files |
|---|---|
| `agents` | `AGENTS.md` (marked block) |
| `claude` | `CLAUDE.md` (marked block), `.claude/skills/svarupa/SKILL.md` (the existing skill), `.mcp.json` (merged `mcpServers.svarupa`) |
| `cursor` | `.cursor/rules/svarupa.mdc` (front matter `alwaysApply: true`), `.cursor/mcp.json` (merged `mcpServers.svarupa`) |
| `copilot` | `.github/copilot-instructions.md` (marked block), `.vscode/mcp.json` (merged `servers.svarupa` with `"type": "stdio"`) |
| `windsurf` | `.windsurf/rules/svarupa.md` (front matter `trigger: always_on`); the MCP config is global, so the next steps print the snippet |
| `ide` | `agents` always, plus each detected tool: `claude` when `CLAUDE.md` or `.claude/` exists, `cursor` for `.cursor/`, `copilot` for `.vscode/` or `.github/copilot-instructions.md`, `windsurf` for `.windsurf/`; `--all` installs every target |

The MCP server entry is `{"command": "svarupa", "args": ["mcp", ".svarupa"]}`.
Global configs (Windsurf, Codex `~/.codex/config.toml`) are never edited;
the next steps print what to add. Next steps also say to run `svarupa .`
first and to install `svarupa[mcp]`.

## 2. Rules text

The same body in every file:

- the fixed "Before and after you write code" workflow, the same text as
  the skill's section (one source in code, so they cannot drift);
- a generated "This repository's rules" section, built in memory the way
  `svarupa check` analyzes a tree (nothing is written to `.svarupa/`):
  each unit with its style and forbidden import directions (or "no style
  accepted yet"), the accepted exceptions with their reasons, and every
  health check as one line (`- query-in-loop (performance, major): Load
  everything the loop needs in one query before the loop...`).

## 3. Editing files safely

- Markdown files the user may own (`AGENTS.md`, `CLAUDE.md`,
  `.github/copilot-instructions.md`): the content goes between
  `<!-- svarupa:begin -->` and `<!-- svarupa:end -->`. A missing block is
  appended after one blank line; an existing block is replaced in place;
  text outside the markers is never changed. A file with a begin marker
  and no end marker is refused (SVA-S diagnostic), never guessed.
- Files Svarupa owns (`.cursor/rules/svarupa.mdc`,
  `.windsurf/rules/svarupa.md`, the skill): written whole, with the
  existing differs-means-refuse rule and `--force`.
- MCP JSON files: parsed, `svarupa` added or replaced under the tool's key,
  every other key and server kept, written back with 2-space indentation
  and a trailing newline. A file that is not a JSON object is refused,
  never rewritten.
- The existing symlink and escape guards apply to every path.
- `--update` refreshes marked blocks and Svarupa-owned files only; it is
  what `--force` is for owned files, and never needed for MCP merges.
- `--dry-run` prints each path with `create`, `update` or `unchanged` and
  writes nothing.

## Testing

- Each target in an empty repository writes exactly its files.
- Running a target twice changes nothing the second time.
- A marked block is replaced in place; text before and after survives byte
  for byte; a begin marker without an end marker is refused.
- An MCP merge keeps other servers and keys; invalid JSON or a JSON array
  is refused; VS Code uses `servers` and `"type": "stdio"`.
- `ide` detection picks the expected targets; `--all` picks every target.
- The generated section lists a layered fixture's forbidden directions and
  all 30 checks; the workflow text equals the skill's section.
- `--dry-run` writes nothing.
- Mutations: text outside the markers is changed; another MCP server is
  dropped; detection ignores `.cursor/`.

## Out of scope

Editing global or home-directory configs; JetBrains and other IDE plugins;
new MCP tools; keeping rules files fresh automatically (the user re-runs
`--update`, and the CI gate catches whatever slips through).
