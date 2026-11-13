"""requirements-sort-checker-cli: check requirements.txt entries are sorted, fail CI when they are not.

Reads one or more requirements.txt files (or folders of them), checks
the package entries in each file are in alphabetical order, and
reports every entry that sits out of order, with the file and the
line. Report-only: it never rewrites a file. Exit 0 when every file
is sorted, 1 when any entry is out of order, 2 for a usage error.

Ordering is case-insensitive and compares package names only, the
way pip compares them (PEP 503): case does not matter, and runs of
'-', '_' and '.' count as one '-'. So `Flask` sorts before
`requests`, `requests[security]` sorts as `requests`, and
`flask==2.0` sorts as `flask` — the version never affects the order.
Comments, blank lines, and option lines (`-r`, `-c`, `-e`, and the
rest) are ignored. A repeated entry is not this tool's job: a
package listed twice in sorted position passes here, because
duplicates are checked by requirements-duplicate-checker-cli.
"""

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

__version__ = "1.0.0"

NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
SEPARATOR_RE = re.compile(r"[-_.]+")


class Problem:
    """One problem in one file: path, line number, plain reason."""

    def __init__(self, path, line, reason):
        self.path = path
        self.line = line
        self.reason = reason

    def text(self):
        return f"{self.path}:{self.line}: {self.reason}"

    def as_dict(self):
        return {"file": self.path, "line": self.line, "reason": self.reason}


def normalize_name(name):
    """The sort key of a package name, the way pip compares names.

    Lowercase, and every run of '-', '_' and '.' becomes one '-'.
    """
    return SEPARATOR_RE.sub("-", name).lower()


def strip_comment(line):
    """Remove a trailing comment: a '#' at the start, or after a space."""
    if line.startswith("#"):
        return ""
    for i, ch in enumerate(line):
        if ch == "#" and line[i - 1] in " \t":
            return line[:i]
    return line


def join_continuations(text):
    """Join lines ending in a backslash, keeping the first line number.

    Returns a list of (lineno, logical_line).
    """
    logical = []
    current = None
    start = 0
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.rstrip()
        if current is None:
            current = stripped
            start = lineno
        else:
            current += stripped
        if stripped.endswith("\\") and stripped != "\\":
            current = current[:-1]
            continue
        logical.append((start, current))
        current = None
    if current is not None:
        logical.append((start, current))
    return logical


def parse_requirement_line(line):
    """Parse one logical requirements line.

    Returns (sort_key, shown_name) for a package entry, or None for
    a line that is not a package entry (blank, comment, option line,
    or a bare URL/VCS line). The sort key is the package name in its
    canonical form; the version, extras, and markers are dropped,
    because ordering compares names only.
    """
    stripped = line.strip()
    if not stripped:
        return None
    stripped = strip_comment(stripped).strip()
    if not stripped:
        return None
    if stripped.startswith("-"):
        return None  # option line: -r, -c, -e, --index-url, and the rest
    if stripped.startswith(("git+", "hg+", "svn+", "bzr+", "http://", "https://", "file:")):
        return None  # a bare URL or VCS line has no package name
    match = NAME_RE.match(stripped)
    if not match:
        return None
    shown = match.group(0)
    rest = stripped[match.end():].lstrip()
    if rest.startswith("["):
        end = rest.find("]")
        if end == -1:
            return None  # a broken extras list is not a package entry
    return (normalize_name(shown), shown)


def scan_text(text, label):
    """Check one requirements document (as text) and return its problems.

    Order is checked within one file only. An entry is a problem
    when its name sorts before the entry directly above it — that
    is the exact test for a sorted list, so every out-of-order
    entry is reported once, at its own line, naming the entry it
    should have come before and that entry's line. A repeated
    entry in sorted position is not a problem here: equal names
    are in order, and duplicates are another tool's job.
    """
    problems = []
    previous = None  # (sort_key, shown_name, lineno) of the entry above
    for lineno, logical in join_continuations(text):
        entry = parse_requirement_line(logical)
        if entry is None:
            continue
        sort_key, shown = entry
        if previous is not None and sort_key < previous[0]:
            problems.append(Problem(
                label, lineno,
                f"'{shown}' is out of order — it should come before "
                f"'{previous[1]}' (line {previous[2]})"))
        previous = (sort_key, shown, lineno)
    return problems


def check_file(path):
    """Check one file and return its problems."""
    label = str(path)
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return [Problem(label, 1, f"cannot read file: {exc.strerror or exc}")]
    if b"\x00" in data:
        return [Problem(label, 1, "file contains a null byte, not a requirements file")]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [Problem(label, 1, "file is not valid UTF-8")]
    return scan_text(text, label)


def is_requirements_name(path):
    """True when a file name looks like a requirements file."""
    return path.name.lower().startswith("requirements") and path.name.lower().endswith(".txt")


def collect(paths, recursive=True, exclude=()):
    """Gather the files to check and their problems.

    A file given directly is always checked, whatever its name. A
    folder is walked for files named `requirements*.txt`, recursively
    by default. `.git` folders are never walked, and directory links
    are never followed.
    """
    checked = []
    problems = []
    for given in paths:
        path = Path(given)
        if path.is_file():
            checked.append(path)
            problems.extend(check_file(path))
        elif path.is_dir():
            for root, dirs, files in os.walk(path, followlinks=False):
                dirs[:] = [d for d in dirs
                           if d != ".git"
                           and not any(fnmatch.fnmatch(d, pat) for pat in exclude)]
                for name in sorted(files):
                    candidate = Path(root) / name
                    if not is_requirements_name(candidate):
                        continue
                    if any(fnmatch.fnmatch(name, pat) for pat in exclude):
                        continue
                    checked.append(candidate)
                    problems.extend(check_file(candidate))
                if not recursive:
                    break
        else:
            problems.append(Problem(str(given), 1, "no such file or folder"))
    return checked, problems


def main(argv=None):
    """Entry point. Returns the exit code: 0 sorted, 1 out of order, 2 usage."""
    parser = argparse.ArgumentParser(
        prog="requirements_sort_checker",
        description="Check requirements.txt entries are sorted alphabetically.")
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="requirements file or folder to check")
    parser.add_argument("--stdin", action="store_true",
                        help="check one requirements file read from standard input")
    parser.add_argument("--json", action="store_true",
                        help="print the result as JSON (human lines go to standard error)")
    parser.add_argument("--no-recursive", action="store_true",
                        help="do not walk into subfolders")
    parser.add_argument("--exclude", action="append", default=[],
                        metavar="PATTERN",
                        help="skip files and folders matching this name pattern")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.stdin:
        if args.paths:
            parser.error("--stdin takes no paths")
        text = sys.stdin.read()
        checked = ["<stdin>"]
        problems = scan_text(text, "<stdin>")
    else:
        if not args.paths:
            parser.error("give at least one file or folder, or use --stdin")
        checked, problems = collect(args.paths,
                                    recursive=not args.no_recursive,
                                    exclude=args.exclude)

    files_with_problems = len({p.path for p in problems})
    if args.json:
        result = {
            "files_checked": len(checked),
            "problems": [p.as_dict() for p in problems],
            "files_with_problems": files_with_problems,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        for problem in problems:
            print(problem.text(), file=sys.stderr)
    else:
        for problem in problems:
            print(problem.text())
        if problems:
            print(f"Checked {len(checked)} file(s), "
                  f"{len(problems)} problem(s) in {files_with_problems} file(s).")
        else:
            print(f"OK — {len(checked)} file(s) checked, "
                  f"all requirements files are clean.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
