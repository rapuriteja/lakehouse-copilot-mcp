"""Fail if the repository or its git history contains anything that looks like a secret.

    python scripts/scan_secrets.py            # files in the working tree + full git history
    python scripts/scan_secrets.py --no-history

Run before every push (and in CI). It also checks that .env is git-ignored and that no
.env file is tracked. Matches are printed masked, so the scan never leaks what it finds.
Exit code 1 means something was found: do not push.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Patterns are built from pieces so this file never matches itself.
PATTERNS: dict[str, re.Pattern[str]] = {
    "OpenRouter key": re.compile("sk" + "-or-"),
    "OpenAI-style key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "Private key": re.compile("-----BEGIN [A-Z ]*" + "PRIVATE KEY-----"),
    "Azure storage key": re.compile(r"AccountKey=[A-Za-z0-9+/]{40,}={0,2}"),
    "JSON web token": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\."),
    "Key-like assignment": re.compile(
        r"(?i)(?:api[_-]?key|secret|token|password|passwd)[a-z_]*[\"']?\s*[:=]\s*[\"']?"
        r"([A-Za-z0-9+/_\-]{24,})"
    ),
}

BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".whl", ".parquet"}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, errors="replace"
    ).stdout


def mask(text: str) -> str:
    return text[:6] + "*" * max(len(text) - 6, 4) if len(text) > 6 else "****"


def find(text: str) -> list[tuple[str, str]]:
    found = []
    for name, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            value = match.group(match.lastindex or 0)
            # Real keys mix letters and digits; names like azurerm_search_service do not.
            if name == "Key-like assignment" and not (
                re.search(r"\d", value) and re.search(r"[A-Za-z]", value)
            ):
                continue
            found.append((name, mask(match.group(0))))
    return found


def scan_tree() -> list[str]:
    problems = []
    for path in _git("ls-files").splitlines():
        if Path(path).suffix.lower() in BINARY_SUFFIXES or not Path(path).is_file():
            continue
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            problems += [f"{path}:{lineno}: {name}: {m}" for name, m in find(line)]
    return problems


def scan_history() -> list[str]:
    problems, commit = [], "?"
    log = _git("log", "--all", "-p", "--no-color", "--no-ext-diff", "--format=commit %H")
    for line in log.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1][:10]
        elif line.startswith("+") and not line.startswith("+++"):
            problems += [f"commit {commit}: {name}: {m}" for name, m in find(line)]
    return problems


def check_env_files() -> list[str]:
    problems = []
    if subprocess.run(["git", "check-ignore", "-q", ".env"]).returncode != 0:
        problems.append(".env is not in .gitignore")
    for path in _git("ls-files").splitlines():
        if re.search(r"(^|/)\.env(\.[^/]*)?$", path) and not path.endswith(".example"):
            problems.append(f"{path} is tracked by git")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-history", action="store_true", help="skip the git history")
    args = parser.parse_args(argv)

    problems = check_env_files() + scan_tree()
    if not args.no_history:
        problems += scan_history()
    if problems:
        print(f"Secret scan FAILED ({len(problems)} findings). Do not push:")
        for problem in sorted(set(problems)):
            print("  " + problem)
        return 1
    scope = "working tree" if args.no_history else "working tree and full git history"
    print(f"Secret scan passed: nothing key-like in the {scope}; .env is ignored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
