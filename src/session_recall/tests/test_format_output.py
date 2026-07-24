"""Tests for human output formatting helpers."""

from session_recall.util.format_output import fmt_human_sessions


def test_fmt_human_sessions_unwraps_untrusted_summary() -> None:
    sessions = [
        {
            "id_short": "vscode:c",
            "date": "2026-07-24",
            "repository": "unknown",
            "turns_count": 3,
            "summary": (
                "<<UNTRUSTED-FILE-BACKED-CONTENT>>\n"
                "Fix the summary for vscode\n"
                "<<END-UNTRUSTED-FILE-BACKED-CONTENT>>"
            ),
        }
    ]

    out = fmt_human_sessions(sessions)
    assert "UNTRUSTED-FILE-BACKED-CONTENT" not in out
    assert "Fix the summary for vscode" in out
