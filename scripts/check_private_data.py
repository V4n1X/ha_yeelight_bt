"""Block commits and pushes that contain personal data.

Checks for credentials, private network details and real Bluetooth addresses.
Personal patterns that must not be published themselves (your own MACs,
hostnames, names) go into the untracked file .git/info/private-patterns,
one regular expression per line.

usage:
  check_private_data.py --staged            files staged for commit (pre-commit hook)
  check_private_data.py --range A..B        commits being pushed (pre-push hook)
  check_private_data.py --all-files         all tracked files (CI)
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

GENERIC = {
    "token": r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|gh[opsu]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "private ip": r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b",
    "local hostname": r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:lan|home|internal|fritz\.box)(?![\w.-])",
    "user path": r"[A-Za-z]:\\Users\\[^\\\s]+|/mnt/c/Users/[^/\s]+|/home/[a-z][a-z0-9_-]*/",
    "mac address": r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b",
    "esphome device": r"\besp32-bluetooth-proxy-[0-9a-f]{6}\b",
}
# Public example addresses used in the tests and docs
ALLOWED_MACS = {
    "F8:24:41:E6:3E:39",  # upstream example lamp
    "F8:24:41:C0:0E:D2",  # from a public upstream issue log
    "AA:BB:CC:DD:EE:FF",
    "00:00:00:00:00:01",  # adapter of the HA test fixtures
}
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip")
SELF = "scripts/check_private_data.py"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout


def load_patterns() -> dict[str, re.Pattern[str]]:
    patterns = {kind: re.compile(p, re.I) for kind, p in GENERIC.items()}
    private = Path(git("rev-parse", "--git-path", "info/private-patterns").strip())
    if private.is_file():
        for n, line in enumerate(private.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip() and not line.lstrip().startswith("#"):
                patterns[f"private pattern #{n}"] = re.compile(line.strip(), re.I)
    return patterns


def scan(text: str, where: str, patterns: dict[str, re.Pattern[str]]) -> list[str]:
    found = []
    for kind, pattern in patterns.items():
        for match in pattern.finditer(text):
            value = match.group(0)
            if kind == "mac address" and value.upper() in ALLOWED_MACS:
                continue
            line = text.count("\n", 0, match.start()) + 1
            # never echo a finding in full, CI logs of public repos are public
            if kind == "mac address":
                shown = value[:8] + ":…"  # vendor part only
            elif kind == "private ip":
                shown = ".".join(value.split(".")[:2]) + ".…"
            else:
                shown = value[:4] + "…"
            found.append(f"{where}:{line}: {kind}: {shown}")
    return found


def staged() -> list[tuple[str, str]]:
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split(
        "\0"
    )
    return [(n, git("show", f":{n}")) for n in names if n]


def pushed(rev_range: str) -> list[tuple[str, str]]:
    items = []
    for commit in git("rev-list", *rev_range.split()).split():
        items.append(
            (
                f"commit {commit[:7]} message",
                git("log", "-1", "--format=%an %ae%n%B", commit),
            )
        )
        names = git(
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "--diff-filter=ACMR",
            "-z",
            commit,
        )
        for name in filter(None, names.split("\0")):
            items.append((f"{commit[:7]}:{name}", git("show", f"{commit}:{name}")))
    return items


def tracked() -> list[tuple[str, str]]:
    items = []
    for name in filter(None, git("ls-files", "-z").split("\0")):
        path = Path(name)
        if path.is_file() and not name.endswith(SKIP_SUFFIXES):
            items.append((name, path.read_text(encoding="utf-8", errors="replace")))
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--range")
    group.add_argument("--all-files", action="store_true")
    args = parser.parse_args()

    if args.staged:
        items = staged()
    elif args.range:
        items = pushed(args.range)
    else:
        items = tracked()

    patterns = load_patterns()
    problems = []
    for where, text in items:
        path = where.split(":", 1)[-1]
        if path.endswith(SKIP_SUFFIXES) or path == SELF:
            continue
        problems += scan(text, where, patterns)

    if problems:
        print(
            "Possible personal data found, nothing was committed/pushed:",
            file=sys.stderr,
        )
        for p in problems:
            print("  " + p, file=sys.stderr)
        print(
            "Replace it with a placeholder. If it is a public example, add it to "
            f"ALLOWED_MACS in {SELF}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
