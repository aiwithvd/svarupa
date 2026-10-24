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
    crlf = lambda t: t.replace("\n", "\r\n")  # noqa: E731
    assert out[len(mine) :] == crlf(f"\n{BEGIN}\n{BODY}{END}\n")
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


def test_mcp_merge_refuses_files_that_cannot_round_trip() -> None:
    for bad in ('{"a": NaN}', '{"a": -Infinity}', '{"a": 1e400}', '{"a": 1, "a": 2}'):
        with pytest.raises(MergeError):
            merge_mcp(bad, "mcpServers", ENTRY)
    with pytest.raises(MergeError, match="duplicate key 'x'"):
        merge_mcp('{"n": {"x": 1, "x": 2}}', "mcpServers", ENTRY)


def test_mcp_merge_round_trips_nested_unicode_and_big_integers() -> None:
    mine = (
        '{"n": {"deep": [1, {"k": "caf\u00e9 \u2603"}]}, "big": 123456789012345678901234567890}'
    )
    text = merge_mcp(mine, "mcpServers", ENTRY)
    out = json.loads(text)
    assert out["big"] == 123456789012345678901234567890
    assert out["n"] == {"deep": [1, {"k": "caf\u00e9 \u2603"}]}
    assert "\u2603" in text
    assert merge_mcp(text, "mcpServers", ENTRY) == text


def test_a_crlf_file_stays_crlf() -> None:
    mine = "# Mine\r\nline two\r\n"
    out = merge_block(mine, BODY)
    assert out.startswith(mine)
    assert "\n" not in out.replace("\r\n", "")
    assert merge_block(out, BODY) == out
    replaced = merge_block(out.replace("Rules here.", "old"), BODY)
    assert replaced == out


def test_an_identical_mcp_entry_leaves_the_file_byte_identical() -> None:
    mine = '{\n\t"mcpServers": {\n\t\t"svarupa": {"command": "svarupa", "args": ["mcp", ".svarupa"]}\n\t},\n\t"n": 1.50\n}'
    assert merge_mcp(mine, "mcpServers", ENTRY) == mine


def test_mcp_merge_keeps_the_files_indentation() -> None:
    four = '{\n    "keep": 1\n}\n'
    out = merge_mcp(four, "mcpServers", ENTRY)
    assert out.startswith('{\n    "keep": 1,\n    "mcpServers": {\n        "svarupa"')
    tab = '{\n\t"keep": 1\n}\n'
    out = merge_mcp(tab, "mcpServers", ENTRY)
    assert out.startswith('{\n\t"keep": 1,\n\t"mcpServers": {\n\t\t"svarupa"')
    assert merge_mcp(None, "mcpServers", ENTRY).startswith('{\n  "mcpServers"')


def test_a_marker_mentioned_inline_is_not_a_marker() -> None:
    mine = f"Docs: put `{BEGIN}` and `{END}` around the block.\nMore {BEGIN} text\n"
    out = merge_block(mine, BODY)
    assert out.startswith(mine)
    assert out.endswith(f"\n{BEGIN}\n{BODY}{END}\n")
    assert merge_block(out, BODY) == out
