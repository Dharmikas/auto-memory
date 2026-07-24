"""Text extraction, role detection, and turn parsing for file-backed providers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_HEXISH_RE = re.compile(r"^[0-9a-fA-F]{6,}$")
_JSON_ARTIFACT_RE = re.compile(r"^[0-9a-fA-F-]{16,}\.(?:json|jsonl)$", re.IGNORECASE)
_UNIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])((?:\./|\.\./|/)[^\s'\"`<>|:]+)")
_REL_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)")
_WINDOWS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]:\\\\[^\s'\"`<>|]+)")
_BASENAME_FILE_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8})(?![A-Za-z0-9_])")
_COMMON_FILE_NAMES = {
    "dockerfile",
    "makefile",
    "readme",
    "license",
    "changelog",
    "pyproject.toml",
}
_COMMON_EXTENSIONS = {
    "py",
    "md",
    "txt",
    "json",
    "jsonl",
    "yaml",
    "yml",
    "toml",
    "ini",
    "cfg",
    "sh",
    "zsh",
    "bash",
    "js",
    "jsx",
    "ts",
    "tsx",
    "go",
    "java",
    "kt",
    "rb",
    "rs",
    "c",
    "cc",
    "cpp",
    "h",
    "hpp",
    "cs",
    "sql",
    "xml",
    "html",
    "css",
    "scss",
}
_PATH_KEYS = {"path", "file_path", "filepath", "old_path", "new_path"}


def _is_summary_noise(line: str) -> bool:
    if len(line) < 4:
        return True
    if line in {"@", "```", "---"}:
        return True
    if line.startswith("#"):
        return True
    if line.startswith("<<") and "UNTRUSTED" in line:
        return True
    if _HEXISH_RE.match(line):
        return True
    if _JSON_ARTIFACT_RE.match(line):
        return True
    return False


def _extract_role(obj: object) -> str:
    if not isinstance(obj, dict):
        return "assistant"
    if obj.get("kind") == 1:
        k = obj.get("k")
        if (
            isinstance(k, list)
            and len(k) >= 2
            and k[0] == "inputState"
            and k[1] == "inputText"
        ):
            return "user"
    role = obj.get("role")
    if isinstance(role, str):
        return role.lower()
    kind = obj.get("type")
    if isinstance(kind, str):
        return "user" if "user" in kind.lower() else "assistant"
    return "assistant"


def _extract_text(obj: object) -> str:
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return " ".join(x for x in (_extract_text(i) for i in obj) if x)
    if not isinstance(obj, dict):
        return ""

    if obj.get("kind") == 1:
        k = obj.get("k")
        v = obj.get("v")
        if (
            isinstance(k, list)
            and len(k) >= 2
            and k[0] == "inputState"
            and k[1] == "inputText"
        ):
            return v if isinstance(v, str) else ""

    if obj.get("kind") == 2 and isinstance(obj.get("v"), list):
        for item in obj.get("v"):
            if not isinstance(item, dict):
                continue
            message = item.get("message")
            if isinstance(message, str) and message.strip():
                return message

    candidates = [
        obj.get("text"),
        obj.get("content"),
        obj.get("message"),
        obj.get("value"),
    ]
    for c in candidates:
        t = _extract_text(c)
        if t:
            return t

    for key in ("messages", "parts", "items", "payload"):
        if key in obj:
            t = _extract_text(obj[key])
            if t:
                return t
    return ""


def _best_summary(turns: list[dict], fallback: str) -> str:
    """Pick the first meaningful user/assistant line for list summaries."""
    for turn in turns:
        for key in ("user", "assistant"):
            raw = str(turn.get(key) or "").strip()
            if not raw:
                continue
            for line in raw.splitlines():
                candidate = line.strip()
                if _is_summary_noise(candidate):
                    continue
                return candidate[:120]
    fallback_line = str(fallback or "").strip().splitlines()[0].strip() if fallback else ""
    if _is_summary_noise(fallback_line):
        return "(untitled)"
    return fallback_line[:120] if fallback_line else "(untitled)"


def _normalize_candidate_path(raw: str) -> str:
    candidate = raw.strip().strip("`\"'")
    candidate = candidate.rstrip(".,:;)]}")
    return candidate.replace("\\\\", "/")


def _looks_like_file_path(candidate: str) -> bool:
    if not candidate:
        return False
    low = candidate.lower()
    if "://" in low:
        return False
    if "/workspaceStorage/" in candidate and "/chatSessions/" in candidate:
        return False

    name = Path(candidate).name.lower()
    if _JSON_ARTIFACT_RE.match(name):
        return False
    if re.match(r"^n[0-9a-fA-F-]{16,}\.(?:json|jsonl)$", name):
        return False
    if name in _COMMON_FILE_NAMES:
        return True
    if "." not in name:
        return False
    ext = name.rsplit(".", 1)[-1]
    return ext in _COMMON_EXTENSIONS


def extract_touched_files(turns: list[dict], limit: int = 10) -> list[str]:
    """Extract likely touched file paths from user/assistant turn text."""
    seen: set[str] = set()
    out: list[str] = []
    for turn in turns:
        blobs = [str(turn.get("user") or ""), str(turn.get("assistant") or "")]
        for text in blobs:
            if not text:
                continue
            for regex in (
                _WINDOWS_PATH_RE,
                _UNIX_PATH_RE,
                _REL_PATH_RE,
                _BASENAME_FILE_RE,
            ):
                for match in regex.finditer(text):
                    cand = _normalize_candidate_path(match.group(1))
                    if not _looks_like_file_path(cand):
                        continue
                    key = cand[2:] if cand.startswith("./") else cand
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(key)
                    if len(out) >= limit:
                        return out
    return out


def _iter_json_records(
    file_path: Path, max_parse_lines: int, max_line_chars: int
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f):
                if idx >= max_parse_lines:
                    break
                line = line.strip()
                if not line or len(line) > max_line_chars:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
    except OSError:
        return []
    return records


def _workspace_relative(candidate: str, workspace_path: str | None) -> str | None:
    if not workspace_path:
        return candidate
    root = workspace_path.rstrip("/")
    if not root:
        return candidate
    if candidate == root:
        return None
    prefix = root + "/"
    if candidate.startswith(prefix):
        return candidate[len(prefix) :]
    # For file listings we only want files from this workspace when root is known.
    if candidate.startswith("/"):
        return None
    return candidate


def _collect_paths_from_obj(
    obj: Any,
    workspace_path: str | None,
    seen: set[str],
    out: list[str],
    limit: int,
) -> None:
    if len(out) >= limit:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if len(out) >= limit:
                return
            key_l = str(key).lower()
            if key_l in _PATH_KEYS and isinstance(value, str):
                cand = _normalize_candidate_path(value)
                rel = _workspace_relative(cand, workspace_path)
                if rel and _looks_like_file_path(rel):
                    dedupe_key = rel[2:] if rel.startswith("./") else rel
                    if dedupe_key not in seen:
                        seen.add(dedupe_key)
                        out.append(dedupe_key)
            _collect_paths_from_obj(value, workspace_path, seen, out, limit)
        return
    if isinstance(obj, list):
        for item in obj:
            if len(out) >= limit:
                return
            _collect_paths_from_obj(item, workspace_path, seen, out, limit)


def extract_touched_files_from_jsonl(
    file_path: Path,
    workspace_path: str | None,
    max_parse_lines: int,
    max_line_chars: int,
    limit: int = 10,
) -> list[str]:
    """Extract touched files from raw JSONL records (tool payloads, args, metadata)."""
    records = _iter_json_records(file_path, max_parse_lines, max_line_chars)
    seen: set[str] = set()
    out: list[str] = []
    for record in records:
        _collect_paths_from_obj(record, workspace_path, seen, out, limit)
        if len(out) >= limit:
            break
    return out


def parse_turns(
    file_path: Path, max_parse_lines: int, max_line_chars: int
) -> list[dict]:
    """Parse JSONL file into turn dicts with deduplication."""
    turns: list[dict] = []
    last_text = None
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f):
                if idx >= max_parse_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                if len(line) > max_line_chars:
                    continue
                try:
                    obj = json.loads(line)
                except (
                    json.JSONDecodeError,
                    RecursionError,
                    TypeError,
                    ValueError,
                ):
                    continue
                text = _extract_text(obj)
                if not text:
                    continue
                text = text.strip()
                if not text or text == last_text:
                    continue
                last_text = text
                role = _extract_role(obj)
                turns.append(
                    {
                        "idx": idx,
                        "user": text if role == "user" else "",
                        "assistant": text if role != "user" else "",
                        "timestamp": (
                            obj.get("timestamp") if isinstance(obj, dict) else None
                        ),
                    }
                )
    except OSError:
        return []
    return turns
