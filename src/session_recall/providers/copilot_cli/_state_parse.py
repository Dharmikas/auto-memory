"""JSONL state session parsing for Copilot CLI."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..common import short_id, utc_iso_from_ts
from ...util.recall_hygiene import recall_derivation_score
from ._labels import _detect_repo_for_path, _local_workspace_label


_PATH_HINT_KEYS = {
    "path",
    "cwd",
    "filePath",
    "workspaceFolder",
    "dirPath",
    "resourcePath",
    "includePattern",
}
_SID_RE = re.compile(r"^[0-9a-fA-F-]{4,}$")


def _flatten_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        chunks = [c for item in value if (c := _flatten_text(item))]
        if chunks:
            return "\n".join(chunks)
    if isinstance(value, dict):
        # Common rich content/message payload keys.
        for key in ("text", "content", "message", "value", "prompt", "query"):
            if key in value:
                flattened = _flatten_text(value[key])
                if flattened:
                    return flattened
    return None


def _iter_generic_messages(obj: object) -> list[dict]:
    out: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            role = node.get("role") or node.get("author") or node.get("sender")
            content = _flatten_text(
                node.get("content")
                or node.get("message")
                or node.get("text")
                or node.get("prompt")
                or node.get("query")
            )
            ts = node.get("timestamp") or node.get("createdAt") or node.get("time")
            if isinstance(role, str) and content:
                role_l = role.lower()
                if role_l in {"user", "assistant", "system", "tool"}:
                    out.append({"role": role_l, "text": content, "timestamp": ts})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return out


def _normalize_session_id(raw_sid: str, file_path: Path) -> str:
    sid = str(raw_sid or "").strip()
    if sid and _SID_RE.match(sid) and sid.replace("-", ""):
        return sid
    digest = hashlib.sha256(f"{file_path}:{sid}".encode("utf-8")).hexdigest()
    return digest[:32]


def _extract_path_candidates(obj: object) -> list[str]:
    paths: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if (
                key in _PATH_HINT_KEYS
                and isinstance(value, str)
                and value.strip().startswith(("/", "~"))
            ):
                paths.append(value.strip())
            paths.extend(_extract_path_candidates(value))
    elif isinstance(obj, list):
        for item in obj:
            paths.extend(_extract_path_candidates(item))
    return paths


def _dedupe_paths(paths: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def parse_state_session(provider_id: str, file_path: Path) -> dict:
    """Parse an events.jsonl state file into a session dict."""
    session_id = file_path.parent.name
    created_at = None
    cwd = None
    git_root = None
    repo_from_context = None
    candidate_paths: list[str] = []
    turns: list[dict] = []
    last_text = None
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                etype = event.get("type")
                data = event.get("data") or {}
                ts = event.get("timestamp")

                if etype == "session.start":
                    session_id = data.get("sessionId") or session_id
                    created_at = created_at or ts
                    context = data.get("context") or {}
                    if isinstance(context, dict):
                        cwd = context.get("cwd") or cwd
                        git_root = context.get("gitRoot") or git_root
                        if isinstance(context.get("repository"), str):
                            repo_from_context = (
                                context.get("repository") or repo_from_context
                            )
                elif etype == "tool.execution_start":
                    candidate_paths.extend(
                        _extract_path_candidates(data.get("arguments"))
                    )

                text = None
                role = None
                if etype == "user.message":
                    text = data.get("content") or data.get("transformedContent")
                    role = "user"
                elif etype == "assistant.message":
                    text = data.get("content")
                    role = "assistant"

                if not text:
                    continue
                text = str(text).strip()
                if not text or text == last_text:
                    continue
                last_text = text
                turns.append(
                    {
                        "idx": len(turns),
                        "user": text if role == "user" else "",
                        "assistant": text if role == "assistant" else "",
                        "timestamp": ts,
                    }
                )
    except OSError:
        pass

    # Fallback for non-events JSON formats (newer Copilot state snapshots).
    if not turns:
        try:
            raw = file_path.read_text(encoding="utf-8", errors="replace")
            blob = json.loads(raw)
            generic_msgs = _iter_generic_messages(blob)
            seen_text: set[str] = set()
            for msg in generic_msgs:
                text = (msg.get("text") or "").strip()
                if not text or text in seen_text:
                    continue
                seen_text.add(text)
                role = msg.get("role") or "user"
                turns.append(
                    {
                        "idx": len(turns),
                        "user": text if role == "user" else "",
                        "assistant": text if role == "assistant" else "",
                        "timestamp": msg.get("timestamp"),
                    }
                )

            if isinstance(blob, dict):
                session_id = (
                    blob.get("sessionId")
                    or blob.get("id")
                    or blob.get("conversationId")
                    or session_id
                )
                created_at = (
                    created_at
                    or blob.get("createdAt")
                    or blob.get("timestamp")
                    or blob.get("lastUpdated")
                )
                context = blob.get("context")
                if isinstance(context, dict):
                    cwd = context.get("cwd") or cwd
                    git_root = context.get("gitRoot") or git_root
                    if isinstance(context.get("repository"), str):
                        repo_from_context = context.get("repository") or repo_from_context
                candidate_paths.extend(_extract_path_candidates(blob))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    summary = ""
    for t in turns:
        if t.get("user"):
            summary = t["user"].splitlines()[0][:120]
            break
    if not summary:
        summary = file_path.name

    recall_score = recall_derivation_score(
        [
            summary,
            *[
                f"{t.get('user') or ''}\n{t.get('assistant') or ''}"
                for t in turns
            ],
        ]
    )

    created_str = ""
    if isinstance(created_at, str) and created_at:
        created_str = created_at
    else:
        created_str = utc_iso_from_ts(file_path.stat().st_mtime)

    repository = repo_from_context
    if not repository:
        for maybe_repo_path in (git_root, cwd, *_dedupe_paths(candidate_paths)):
            if not isinstance(maybe_repo_path, str) or not maybe_repo_path:
                continue
            repository = _detect_repo_for_path(maybe_repo_path)
            if repository:
                break
    if not repository:
        repository = _local_workspace_label(
            git_root or cwd or next(iter(_dedupe_paths(candidate_paths)), None)
        )

    normalized_id = _normalize_session_id(session_id, file_path)

    return {
        "provider": provider_id,
        "id_short": short_id(normalized_id),
        "id_full": normalized_id,
        "repository": repository or "unknown",
        "branch": "unknown",
        "summary": summary,
        "date": created_str[:10] if created_str else "",
        "created_at": created_str,
        "turns_count": len(turns),
        "files_count": 0,
        "_trust_level": "trusted_first_party",
        "_recall_derived": recall_score > 0,
        "_turns": turns,
        "_path": str(file_path),
    }
