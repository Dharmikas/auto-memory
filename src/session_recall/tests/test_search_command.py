"""Tests for commands/search.py ranking behavior."""

from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch


def test_search_deprioritizes_recall_derived_results() -> None:
    class _FakeProvider:
        provider_id = "cli"

        def schema_problems(self):
            return []

        def search(self, query, repo=None, limit=5, days=None):
            _ = (query, repo, days)
            return [
                {
                    "provider": "cli",
                    "session_id": "newer001",
                    "session_id_full": "newer001-0000-0000-0000-000000000000",
                    "source_type": "turn",
                    "summary": "session-recall recap",
                    "repository": "owner/repo",
                    "date": "2026-04-22",
                    "excerpt": "session-recall list output paraphrase",
                    "_recall_derived": True,
                },
                {
                    "provider": "cli",
                    "session_id": "older002",
                    "session_id_full": "older002-0000-0000-0000-000000000000",
                    "source_type": "turn",
                    "summary": "auth bug investigation",
                    "repository": "owner/repo",
                    "date": "2026-04-21",
                    "excerpt": "fixed auth timeout race",
                    "_recall_derived": False,
                },
            ][:limit]

    with (
        patch(
            "session_recall.commands.search.get_active_providers",
            return_value=[_FakeProvider()],
        ),
        patch("session_recall.commands.search.detect_repo", return_value="owner/repo"),
    ):
        from session_recall.commands.search import run

        args = SimpleNamespace(
            query="auth", repo=None, limit=5, days=30, json=True, provider="cli"
        )
        buf = StringIO()
        with patch("sys.stdout", buf):
            code = run(args)

    payload = json.loads(buf.getvalue())
    assert code == 0
    assert payload["results"][0]["summary"] == "auth bug investigation"
