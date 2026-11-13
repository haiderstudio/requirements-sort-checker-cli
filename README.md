# requirements-sort-checker-cli

Check requirements.txt entries are sorted alphabetically, fail CI when one is out of order.

[![CI](https://github.com/haiderstudio/requirements-sort-checker-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/haiderstudio/requirements-sort-checker-cli/actions/workflows/ci.yml) [![Licence: MIT](https://img.shields.io/badge/Licence-MIT-yellow.svg)](LICENSE)

## What it does

`requirements_sort_checker` reads a requirements.txt file
and checks its package entries are in alphabetical order,
and reports every entry that sits out of order, with the
file and the line. It exits 1 when any entry is out of
order, so it can fail a CI run. A sorted file exits 0.

- Ordering is case-insensitive and compares package names
  only, the way pip compares them (PEP 503): case does not
  matter, and runs of `-`, `_` and `.` count as one `-`. So
  `Flask` sorts before `requests`, `my-package` and
  `my_package` sort as the same name, and `flask==2.0`
  sorts as `flask` — the version never affects the order.
  Extras do not change the sort either: `requests[security]`
  sorts as `requests`.
- Every out-of-order entry is reported, not just the first:
  each one names the entry it should have come before and
  that entry's line, so you can fix the whole file in one
  pass.
- Blank lines, comments, and option lines (`-r`, `-c`,
  `-e`, `--index-url`, and the rest) are skipped. An `-r`
  include is not followed — the file you point at is the
  file that is checked. A line ending in a backslash is
  joined with the next line, the way pip reads it, and
  reported at its first line. A direct reference
  (`name @ https://...`) still counts as the package.
- A repeated entry in sorted position is not a problem
  here: equal names are in order, and duplicates are
  another tool's job — see "What it does NOT do" below.
- A file given directly on the command line is always
  checked, whatever its name. A folder is walked for files
  named `requirements*.txt` (`requirements.txt`,
  `requirements-dev.txt`, and the like), recursively by
  default (`--no-recursive` stops at the top level). `.git`
  folders are never walked, and directory links are never
  followed. `--exclude` skips files and folders by name
  pattern.
- `--json` prints the result as JSON (the human-readable
  lines go to standard error), and `--stdin` checks one
  requirements file read from standard input.

## Who it is for

Anyone who keeps a requirements.txt — Python developers,
teams that pin dependencies in version control, and anyone
who has ever merged a dependency change and hit a conflict
because two people added a package in different places.
Sorted dependencies give cleaner diffs, fewer merge
conflicts, and make a duplicate entry obvious at a glance.

## What it does NOT do

- It does not check whether a package is listed twice —
  that is Day 55's requirements-duplicate-checker-cli. A
  file can be fully sorted and still list a package twice;
  a file can be unsorted and still have no duplicates.
  Sorting and duplicates are two different checks on the
  same file.
- It does not check whether a version is *pinned* — that
  is Day 28's requirements-pin-checker-cli.
- It does not sort or rewrite anything. It is report-only,
  and it never touches the network.

## How this differs

- There is no rival that checks requirements sorting: a
  GitHub search for `"requirements-sort-checker-cli"`
  returns 0 results, and an unquoted search for
  `requirements sort checker` returns only noise (an FTC
  robotics SDK, a Minecraft crash log) — checked Nov 12,
  2026. The field is empty for this job.

## Install

Python 3.10 or newer, nothing else to install.

```sh
git clone https://github.com/haiderstudio/requirements-sort-checker-cli.git
cd requirements-sort-checker-cli
```

## Usage

Check one sorted file:

```sh
python3 src/requirements_sort_checker.py examples/requirements.txt
```

```text
OK — 1 file(s) checked, all requirements files are clean.
```

Check a file with entries out of order:

```sh
python3 src/requirements_sort_checker.py examples/requirements-unsorted.txt
```

```text
examples/requirements-unsorted.txt:4: 'Django' is out of order — it should come before 'requests' (line 2)
examples/requirements-unsorted.txt:6: 'flask' is out of order — it should come before 'urllib3' (line 5)
examples/requirements-unsorted.txt:8: 'celery' is out of order — it should come before 'numpy' (line 7)
Checked 1 file(s), 3 problem(s) in 1 file(s).
```

The exit code is 1, so a CI run fails.

Check a whole folder:

```sh
python3 src/requirements_sort_checker.py examples
```

```text
examples/requirements-unsorted.txt:4: 'Django' is out of order — it should come before 'requests' (line 2)
examples/requirements-unsorted.txt:6: 'flask' is out of order — it should come before 'urllib3' (line 5)
examples/requirements-unsorted.txt:8: 'celery' is out of order — it should come before 'numpy' (line 7)
examples/configs/requirements.txt:2: 'awscli' is out of order — it should come before 'boto3' (line 1)
Checked 4 file(s), 4 problem(s) in 2 file(s).
```

Check one file from standard input:

```sh
printf 'requests==2.31.0\nDjango==4.2.7\n' | python3 src/requirements_sort_checker.py --stdin
```

```text
<stdin>:2: 'Django' is out of order — it should come before 'requests' (line 1)
Checked 1 file(s), 1 problem(s) in 1 file(s).
```

Machine-readable output:

```sh
python3 src/requirements_sort_checker.py examples/configs/requirements.txt --json
```

```json
{
  "files_checked": 1,
  "problems": [
    {
      "file": "examples/configs/requirements.txt",
      "line": 2,
      "reason": "'awscli' is out of order — it should come before 'boto3' (line 1)"
    }
  ],
  "files_with_problems": 1
}
```

## Examples

- `examples/requirements.txt` — a sorted requirements
  file. Passes.
- `examples/requirements-unsorted.txt` — three problems in
  one file: a case-only difference (`Django` after
  `requests`), an extras variant (`urllib3[security]`
  before `flask`), and a plain late entry (`celery` after
  `numpy`), with a comment and a blank line that are
  correctly ignored. Fails with exit 1.
- `examples/requirements-dev.txt` — a sorted dev
  requirements file. Passes.
- `examples/configs/requirements.txt` — one entry out of
  order, in a subfolder. Fails with exit 1.
- `sh examples/run-example.sh` runs the checker on the
  whole `examples/` folder. Its output is in
  `examples/sample-output.md`.

## How it is tested

```sh
python3 tests/test_requirements_sort_checker.py
```

The tests cover: name matching (case, separators, extras,
markers, direct references, single-character names),
skipped lines (blank, comment, option, URL, VCS, broken
extras), line continuations, every sort shape with exact
line numbers (sorted pass, one entry out of order,
case-insensitive order, versions ignored in ordering,
extras sorted by name, several out-of-order entries,
duplicates in sorted position passing, duplicates out
of order flagged as a sort error), empty and comments-only
files, folder walking (requirements names only, `.git`
skipped, `--no-recursive`, `--exclude`), `--stdin`,
`--json`, exit codes 0/1/2, unreadable and non-UTF-8
files, and the bundled examples (the sample output is
compared byte-for-byte against a fresh run, and the
outputs quoted in this README are checked against real
runs).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT — see [LICENSE](LICENSE).

## Original work

This tool was written from scratch for this repo. Package
name matching follows PEP 503, the rule pip itself uses.
The idea of linting a requirements file is a common one;
the code, the wording, and the tests are original.
