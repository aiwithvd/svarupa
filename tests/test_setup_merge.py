"""Editing files the user may own: a marked block, a merged MCP entry."""

from __future__ import annotations

import json

import pytest

from svarupa.setup.merge import BEGIN, END, MergeError, merge_block, merge_mcp

BODY = "# Svarupa\n\nRules here.\n"
ENTRY = {"command": "svarupa", "args": ["mcp", ".svarupa"]}


def test_a_new_file_is_just_the_block() -> None:
    assert merge_block(None, BODY) == f"{BEGIN}\n{BODY}{END}\n"
    assert merge_block("", BODY) == f"{BEGIN}\n{BODY}{END}\n"


def test_block_append_keeps_the_user_text_byte_for_byte() -> None:
    mine = "# My rules\r\n\r\nUse tabs.\r\n"
    out = merge_block(mine, BODY)
    assert out.startswith(mine)
    assert out[len(mine) :] == f"\n{BEGIN}\n{BODY}{END}\n"
    no_newline = "Use tabs."
    assert merge_block(no_newline, BODY) == f"Use tabs.\n\n{BEGIN}\n{BODY}{END}\n"


def test_an_existing_block_is_replaced_in_place() -> None:
    before, after = "intro\n\n", "\n## Later\nkept\n"
    old = f"{before}{BEGIN}\nold rules\n{END}\n{after}"
    out = merge_block(old, BODY)
    assert out == f"{before}{BEGIN}\n{BODY}{END}\n{after}"
    assert merge_block(out, BODY) == out


def test_a_begin_marker_without_an_end_is_refused() -> None:
    with pytest.raises(MergeError, match="no end marker"):
        merge_block(f"intro\n{BEGIN}\nhalf\n", BODY)


def test_a_second_begin_marker_is_refused() -> None:
    text = f"{BEGIN}\na\n{END}\n```\n{BEGIN}\n```\n"
    with pytest.raises(MergeError, match="more than one"):
        merge_block(text, BODY)


def test_mcp_merge_adds_svarupa_and_keeps_others() -> None:
    mine = json.dumps({"mcpServers": {"other": {"command": "x"}}, "keep": 1})
    out = json.loads(merge_mcp(mine, "mcpServers", ENTRY))
    assert out == {"mcpServers": {"other": {"command": "x"}, "svarupa": ENTRY}, "keep": 1}
    text = merge_mcp(mine, "mcpServers", ENTRY)
    assert text.endswith("}\n") and '\n  "mcpServers"' in text
    assert merge_mcp(text, "mcpServers", ENTRY) == text


def test_mcp_merge_creates_a_new_file() -> None:
    assert json.loads(merge_mcp(None, "servers", ENTRY)) == {"servers": {"svarupa": ENTRY}}


def test_mcp_merge_refuses_invalid_json_and_arrays() -> None:
    for bad in ("{not json", "[]", "null", '"x"'):
        with pytest.raises(MergeError, match="JSON object"):
            merge_mcp(bad, "mcpServers", ENTRY)


def test_mcp_merge_refuses_a_non_object_server_map() -> None:
    with pytest.raises(MergeError, match="mcpServers"):
        merge_mcp(json.dumps({"mcpServers": []}), "mcpServers", ENTRY)
