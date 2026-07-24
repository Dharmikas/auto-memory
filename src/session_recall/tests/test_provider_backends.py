"""Tests for provider backends and fallback parsing behavior."""

from __future__ import annotations

import json
from pathlib import Path

from session_recall.providers.copilot_cli import CopilotCliProvider
from session_recall.providers.file import (
    VSCodeProvider,
    _extract_role,
    _extract_text,
    _is_wsl,
)
from session_recall.providers.file._parse_helpers import _best_summary


def test_vscode_kind1_inputtext_extraction() -> None:
    row = {
        "kind": 1,
        "k": ["inputState", "inputText"],
        "v": "Please fix the auth timeout regression",
    }
    assert _extract_role(row) == "user"
    assert _extract_text(row) == "Please fix the auth timeout regression"


def test_cli_fallback_reads_session_state(monkeypatch, tmp_path: Path) -> None:
    state_root = tmp_path / "session-state"
    session_dir = state_root / "abcd1234-0000-0000-0000-000000000000"
    session_dir.mkdir(parents=True)
    events = session_dir / "events.jsonl"

    payloads = [
        {
            "type": "session.start",
            "data": {
                "sessionId": "abcd1234-0000-0000-0000-000000000000",
                "context": {"cwd": "/tmp/project"},
            },
            "timestamp": "2026-04-22T10:00:00.000Z",
        },
        {
            "type": "user.message",
            "data": {"content": "Investigate auth timeout"},
            "timestamp": "2026-04-22T10:01:00.000Z",
        },
        {
            "type": "assistant.message",
            "data": {"content": "I will inspect recent traces."},
            "timestamp": "2026-04-22T10:01:10.000Z",
        },
    ]
    events.write_text(
        "\n".join(json.dumps(p) for p in payloads) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        "session_recall.providers.copilot_cli._labels.detect_repo_for_cwd",
        lambda cwd: "owner/repo",
    )

    provider = CopilotCliProvider(
        db_path=str(tmp_path / "missing.db"), state_root=str(state_root)
    )

    assert provider.is_available() is True

    sessions = provider.list_sessions(repo="owner/repo", limit=5, days=None)
    assert len(sessions) == 1
    assert sessions[0]["provider"] == "cli"
    assert sessions[0]["repository"] == "owner/repo"
    assert sessions[0]["summary"].startswith("Investigate auth timeout")
    assert sessions[0]["turns_count"] == 2

    session = provider.get_session("abcd1234", turns=5, full=False)
    assert session is not None
    assert session["id"].startswith("abcd1234")
    assert session["repository"] == "owner/repo"
    assert len(session["turns"]) == 2

    results = provider.search("timeout", repo="owner/repo", limit=5, days=None)
    assert len(results) >= 1
    assert results[0]["provider"] == "cli"


def test_cli_fallback_prefers_context_repository_field(tmp_path: Path) -> None:
    state_root = tmp_path / "session-state"
    session_dir = state_root / "efgh5678-0000-0000-0000-000000000000"
    session_dir.mkdir(parents=True)
    events = session_dir / "events.jsonl"

    payloads = [
        {
            "type": "session.start",
            "data": {
                "sessionId": "efgh5678-0000-0000-0000-000000000000",
                "context": {
                    "cwd": "/not/a/repo",
                    "repository": "dezgit2025/auto-memory",
                },
            },
            "timestamp": "2026-04-22T18:00:00.000Z",
        },
        {
            "type": "user.message",
            "data": {"content": "can you fix the broken skills?"},
            "timestamp": "2026-04-22T18:01:00.000Z",
        },
    ]
    events.write_text(
        "\n".join(json.dumps(p) for p in payloads) + "\n", encoding="utf-8"
    )

    provider = CopilotCliProvider(
        db_path=str(tmp_path / "missing.db"), state_root=str(state_root)
    )
    sessions = provider.list_sessions(repo="dezgit2025/auto-memory", limit=5, days=None)

    assert len(sessions) == 1
    assert sessions[0]["repository"] == "dezgit2025/auto-memory"

    results = provider.search(
        "broken skills", repo="dezgit2025/auto-memory", limit=5, days=None
    )
    assert len(results) == 1
    assert results[0]["repository"] == "dezgit2025/auto-memory"


def test_cli_fallback_uses_tool_path_to_infer_repository(
    monkeypatch, tmp_path: Path
) -> None:
    state_root = tmp_path / "session-state"
    session_dir = state_root / "toolpath-0000-0000-0000-000000000000"
    session_dir.mkdir(parents=True)
    events = session_dir / "events.jsonl"

    payloads = [
        {
            "type": "session.start",
            "data": {
                "sessionId": "toolpath-0000-0000-0000-000000000000",
                "context": {"cwd": "/tmp"},
            },
            "timestamp": "2026-04-22T18:10:00.000Z",
        },
        {
            "type": "tool.execution_start",
            "data": {"arguments": {"path": "/work/auto-memory/src"}},
            "timestamp": "2026-04-22T18:10:05.000Z",
        },
        {
            "type": "user.message",
            "data": {"content": "trace skills fixes"},
            "timestamp": "2026-04-22T18:10:10.000Z",
        },
    ]
    events.write_text(
        "\n".join(json.dumps(p) for p in payloads) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        "session_recall.providers.copilot_cli._labels.detect_repo_for_cwd",
        lambda cwd: (
            "dezgit2025/auto-memory"
            if cwd == "/work/auto-memory/src" or cwd == "/work/auto-memory"
            else None
        ),
    )

    provider = CopilotCliProvider(
        db_path=str(tmp_path / "missing.db"), state_root=str(state_root)
    )
    sessions = provider.list_sessions(repo="dezgit2025/auto-memory", limit=5, days=None)

    assert len(sessions) == 1
    assert sessions[0]["repository"] == "dezgit2025/auto-memory"


def test_cli_fallback_reads_nested_session_state_jsonl(tmp_path: Path) -> None:
    state_root = tmp_path / "session-state"
    nested = state_root / "sessions" / "abcd1234-0000-0000-0000-000000000001"
    nested.mkdir(parents=True)
    events = nested / "events.jsonl"

    payloads = [
        {
            "type": "session.start",
            "data": {
                "sessionId": "abcd1234-0000-0000-0000-000000000001",
                "context": {"repository": "owner/repo"},
            },
            "timestamp": "2026-04-22T11:00:00.000Z",
        },
        {
            "type": "user.message",
            "data": {"content": "Find recent failing tests"},
            "timestamp": "2026-04-22T11:01:00.000Z",
        },
    ]
    events.write_text(
        "\n".join(json.dumps(p) for p in payloads) + "\n", encoding="utf-8"
    )

    provider = CopilotCliProvider(
        db_path=str(tmp_path / "missing.db"), state_root=str(state_root)
    )

    sessions = provider.list_sessions(repo="owner/repo", limit=5, days=None)
    assert len(sessions) == 1
    assert sessions[0]["summary"].startswith("Find recent failing tests")


def test_cli_fallback_reads_command_history_state_json(tmp_path: Path) -> None:
    copilot_root = tmp_path / ".copilot"
    state_root = copilot_root / "session-state"
    state_root.mkdir(parents=True)

    command_history = copilot_root / "command-history-state.json"
    command_history.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "role": "user",
                        "content": "Summarize commits from yesterday",
                        "timestamp": "2026-04-22T12:00:00.000Z",
                    },
                    {
                        "role": "assistant",
                        "content": "I found 6 commits touching auth and docs.",
                        "timestamp": "2026-04-22T12:00:05.000Z",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    provider = CopilotCliProvider(
        db_path=str(tmp_path / "missing.db"), state_root=str(state_root)
    )

    sessions = provider.list_sessions(repo="all", limit=10, days=None)
    assert len(sessions) == 1
    assert sessions[0]["summary"].startswith("Summarize commits")

    results = provider.search("commits", repo="all", limit=5, days=None)
    assert len(results) >= 1


def test_cli_fallback_labels_non_repo_session_as_local_workspace(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "session-state"
    session_dir = state_root / "local-0000-0000-0000-000000000000"
    session_dir.mkdir(parents=True)
    events = session_dir / "events.jsonl"

    payloads = [
        {
            "type": "session.start",
            "data": {
                "sessionId": "local-0000-0000-0000-000000000000",
                "context": {"cwd": "/home/jshessen/.config/squad"},
            },
            "timestamp": "2026-04-22T18:20:00.000Z",
        },
        {
            "type": "user.message",
            "data": {"content": "can you fix the broken skills?"},
            "timestamp": "2026-04-22T18:20:05.000Z",
        },
    ]
    events.write_text(
        "\n".join(json.dumps(p) for p in payloads) + "\n", encoding="utf-8"
    )

    provider = CopilotCliProvider(
        db_path=str(tmp_path / "missing.db"), state_root=str(state_root)
    )
    sessions = provider.list_sessions(repo="all", limit=5, days=None)

    assert len(sessions) == 1
    assert sessions[0]["repository"] == "local:/home/jshessen/.config/squad"


def test_vscode_provider_includes_results_when_repo_filter_is_set(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspaceStorage"
    chat_dir = root / "abc" / "chatSessions"
    chat_dir.mkdir(parents=True)
    fp = chat_dir / "session.jsonl"

    rows = [
        {
            "kind": 1,
            "k": ["inputState", "inputText"],
            "v": "Continue API refactor plan",
        },
        {"kind": 2, "v": [{"message": "I'll pick up where we left off."}]},
    ]
    fp.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    provider = VSCodeProvider(root_override=str(root))
    sessions = provider.list_sessions(repo="owner/repo", limit=5, days=None)

    assert len(sessions) == 1
    assert sessions[0]["provider"] == "vsc"
    assert "Continue API refactor" in sessions[0]["summary"]


def test_vscode_provider_skips_pathological_jsonl_lines(tmp_path: Path) -> None:
    root = tmp_path / "workspaceStorage"
    chat_dir = root / "xyz" / "chatSessions"
    chat_dir.mkdir(parents=True)
    fp = chat_dir / "session.jsonl"

    huge_line = "{" + ('"x":' + '"' + ("a" * 600_000) + '"') + "}"
    valid_row = {"kind": 1, "k": ["inputState", "inputText"], "v": "normal prompt text"}
    fp.write_text(huge_line + "\n" + json.dumps(valid_row) + "\n", encoding="utf-8")

    provider = VSCodeProvider(root_override=str(root))
    sessions = provider.list_sessions(repo="all", limit=5, days=None)

    assert len(sessions) == 1
    assert sessions[0]["turns_count"] == 1


def test_vscode_provider_includes_wsl_server_path() -> None:
    provider = VSCodeProvider()
    wsl_path = Path.home() / ".vscode-server" / "data" / "User" / "workspaceStorage"
    assert wsl_path in provider._roots


def test_is_wsl_returns_false_when_proc_version_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "builtins.open",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("No such file")),
    )
    assert _is_wsl() is False


def test_best_summary_skips_markdown_heading_line() -> None:
    turns = [
        {"user": "# Home\nFix summary extraction", "assistant": "", "idx": 0},
    ]
    assert _best_summary(turns, fallback="fallback") == "Fix summary extraction"


def test_best_summary_uses_untitled_for_filename_like_fallback() -> None:
    turns: list[dict] = []
    assert (
        _best_summary(turns, fallback="a1a61d4a-dbf4-4270-932a-0344107f75e6.json")
        == "(untitled)"
    )
