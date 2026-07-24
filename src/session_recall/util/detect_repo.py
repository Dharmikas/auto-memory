"""Detect current repository from git remote or environment."""

import subprocess  # nosec B404
import re
import shutil


_GIT_BIN = shutil.which("git")


def parse_repo_url(url: str) -> str | None:
    """Parse owner/repo from common git remote URL formats."""
    if not url:
        return None
    m = re.match(r"git@[^:]+:(.+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    m = re.match(r"https?://[^/]+/(.+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    return None


def detect_repo_for_cwd(cwd: str, timeout: int = 5) -> str | None:
    """Return owner/repo for a specific working directory path."""
    if not _GIT_BIN:
        return None
    try:
        # Safe subprocess usage: constant argv list and shell=False.
        url = subprocess.run(  # nosec B603
            [_GIT_BIN, "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        ).stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError):
        return None
    return parse_repo_url(url)


def detect_repo() -> str | None:
    """Return 'owner/repo' from git remote origin, or None."""
    if not _GIT_BIN:
        return None
    try:
        # Safe subprocess usage: constant argv list and shell=False.
        url = subprocess.run(  # nosec B603
            [_GIT_BIN, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError):
        return None
    return parse_repo_url(url)
