"""VS Code file-backed session provider."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from ._base import _FileSessionProvider


def _is_wsl() -> bool:
    """Detect Windows Subsystem for Linux via /proc/version."""
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


class VSCodeProvider(_FileSessionProvider):
    provider_name = "VS Code"

    def __init__(self, root_override: str | None = None) -> None:
        home = Path.home()
        if root_override:
            roots = [Path(root_override).expanduser()]
        else:
            roots = [
                home / ".config" / "Code" / "User" / "workspaceStorage",
                home
                / "Library"
                / "Application Support"
                / "Code"
                / "User"
                / "workspaceStorage",
                home
                / ".var"
                / "app"
                / "com.visualstudio.code"
                / "config"
                / "Code"
                / "User"
                / "workspaceStorage",
                home
                / "snap"
                / "code"
                / "current"
                / ".config"
                / "Code"
                / "User"
                / "workspaceStorage",
                home / ".vscode-server" / "data" / "User" / "workspaceStorage",
            ]
        super().__init__("vscode", roots, ["**/chatSessions/*.jsonl"])

    def _infer_repository(self, file_path: Path) -> str:
        """Infer workspace path/URI from sibling workspace.json for VS Code logs."""
        # File path shape: <workspaceStorage>/<hash>/chatSessions/<session>.jsonl
        workspace_meta = file_path.parent.parent / "workspace.json"
        try:
            with workspace_meta.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError, TypeError):
            return "unknown"

        if not isinstance(payload, dict):
            return "unknown"

        raw = payload.get("folder") or payload.get("workspace")
        if not isinstance(raw, str) or not raw.strip():
            return "unknown"

        value = raw.strip()
        parsed = urlparse(value)
        if parsed.scheme == "file":
            path = unquote(parsed.path or "")
            host = unquote(parsed.netloc or "")
            if host:
                # UNC-style path: file://server/share/path -> //server/share/path
                return f"//{host}{path}" if path else f"//{host}"
            # Windows drive-letter file URI: /C:/Users/... -> C:/Users/...
            if re.match(r"^/[A-Za-z]:/", path):
                path = path[1:]
            return path or "unknown"

        if parsed.scheme == "vscode-remote":
            # Keep host plus decoded path for a stable, human-readable label.
            host = unquote(parsed.netloc or "")
            path = unquote(parsed.path or "")
            remote_label = f"{host}{path}".strip()
            return remote_label or value

        return unquote(value)
