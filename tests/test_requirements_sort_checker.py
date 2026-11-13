"""Tests for requirements-sort-checker-cli. Standard library only."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src" / "requirements_sort_checker.py"

sys.path.insert(0, str(ROOT / "src"))
import requirements_sort_checker as rsc
from requirements_sort_checker import (
    Problem, check_file, collect, join_continuations,
    normalize_name, parse_requirement_line, scan_text,
)


def problems_as_tuples(text):
    return [(p.line, p.reason) for p in scan_text(text, "test.txt")]


class NormalizeNameTests(unittest.TestCase):
    def test_lowercase(self):
        self.assertEqual(normalize_name("Requests"), "requests")
        self.assertEqual(normalize_name("DJANGO"), "django")

    def test_separators_collapse(self):
        self.assertEqual(normalize_name("my-package"), "my-package")
        self.assertEqual(normalize_name("my_package"), "my-package")
        self.assertEqual(normalize_name("my.package"), "my-package")
        self.assertEqual(normalize_name("my--package"), "my-package")
        self.assertEqual(normalize_name("my_-package"), "my-package")
        self.assertEqual(normalize_name("my_.package"), "my-package")

    def test_digits_kept(self):
        self.assertEqual(normalize_name("boto3"), "boto3")
        self.assertEqual(normalize_name("h11"), "h11")


class ParseLineTests(unittest.TestCase):
    def test_pinned(self):
        self.assertEqual(parse_requirement_line("requests==2.31.0"),
                         ("requests", "requests"))

    def test_unpinned(self):
        self.assertEqual(parse_requirement_line("flask"),
                         ("flask", "flask"))

    def test_version_operators(self):
        self.assertEqual(parse_requirement_line("numpy>=1.24,<2"),
                         ("numpy", "numpy"))
        self.assertEqual(parse_requirement_line("Django~=4.2"),
                         ("django", "Django"))
        self.assertEqual(parse_requirement_line("celery!=5.3.0"),
                         ("celery", "celery"))

    def test_extras(self):
        self.assertEqual(parse_requirement_line("requests[security]==2.31.0"),
                         ("requests", "requests"))
        self.assertEqual(parse_requirement_line("uvicorn[standard]"),
                         ("uvicorn", "uvicorn"))
        self.assertEqual(parse_requirement_line("requests [security] == 2.31.0"),
                         ("requests", "requests"))

    def test_marker_stripped(self):
        self.assertEqual(parse_requirement_line('requests==2.31.0; python_version > "3.8"'),
                         ("requests", "requests"))
        self.assertEqual(parse_requirement_line("tomli; python_version < '3.11'"),
                         ("tomli", "tomli"))

    def test_direct_reference(self):
        self.assertEqual(
            parse_requirement_line(
                "requests @ https://example.com/requests-2.31.tar.gz"),
            ("requests", "requests"))

    def test_comments_and_blanks(self):
        self.assertIsNone(parse_requirement_line(""))
        self.assertIsNone(parse_requirement_line("   "))
        self.assertIsNone(parse_requirement_line("# a comment"))
        self.assertEqual(parse_requirement_line("requests==2.31.0  # pinned"),
                         ("requests", "requests"))
        self.assertEqual(parse_requirement_line("flask  # web framework"),
                         ("flask", "flask"))

    def test_option_lines(self):
        self.assertIsNone(parse_requirement_line("-r requirements-base.txt"))
        self.assertIsNone(parse_requirement_line("-c constraints.txt"))
        self.assertIsNone(parse_requirement_line("-e ."))
        self.assertIsNone(parse_requirement_line("--index-url https://example.com/simple"))
        self.assertIsNone(parse_requirement_line("-r"))
        self.assertIsNone(parse_requirement_line("--no-binary :all:"))

    def test_bare_url_and_vcs_lines(self):
        self.assertIsNone(parse_requirement_line("https://example.com/pkg-1.0.tar.gz"))
        self.assertIsNone(parse_requirement_line("git+https://example.com/repo.git"))
        self.assertIsNone(parse_requirement_line("file:./local-pkg"))

    def test_broken_extras(self):
        self.assertIsNone(parse_requirement_line("requests[security==2.31.0"))

    def test_single_character_name(self):
        self.assertEqual(parse_requirement_line("a==1"), ("a", "a"))
        self.assertEqual(parse_requirement_line("z"), ("z", "z"))

    def test_leading_whitespace(self):
        self.assertEqual(parse_requirement_line("  requests==2.31.0"),
                         ("requests", "requests"))


class ContinuationTests(unittest.TestCase):
    def test_join_continuations(self):
        joined = join_continuations("requests==2.31.0 \\\n  ; python_version > '3.8'\nflask\n")
        self.assertEqual(len(joined), 2)
        self.assertEqual(joined[0][0], 1)
        self.assertIn("requests==2.31.0", joined[0][1])

    def test_continuation_reported_at_first_line(self):
        text = "requests==2.31.0 \\\n  ; python_version > '3.8'\nDjango==4.2.7\n"
        problems = problems_as_tuples(text)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], 3)
        self.assertIn("'Django'", problems[0][1])


class ScanTests(unittest.TestCase):
    def test_sorted_passes(self):
        self.assertEqual(problems_as_tuples("Django==4.2.7\nflask\nnumpy>=1.24\nrequests==2.31.0\n"), [])

    def test_one_entry_out_of_order(self):
        problems = problems_as_tuples("requests==2.31.0\nDjango==4.2.7\n")
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], 2)
        self.assertEqual(
            problems[0][1],
            "'Django' is out of order — it should come before 'requests' (line 1)")

    def test_case_insensitive_order(self):
        # Flask sorts before requests whatever the case.
        self.assertEqual(problems_as_tuples("Flask\nrequests\n"), [])
        self.assertEqual(problems_as_tuples("FLASK\nRequests\n"), [])
        self.assertEqual(problems_as_tuples("Requests\nflask\n"), [(2, "'flask' is out of order — it should come before 'Requests' (line 1)")])

    def test_case_does_not_change_position(self):
        # "requests" sorts before "urllib3" in any case.
        self.assertEqual(problems_as_tuples("REQUESTS\nurllib3\n"), [])
        self.assertEqual(problems_as_tuples("Urllib3\nrequests\n"), [(2, "'requests' is out of order — it should come before 'Urllib3' (line 1)")])

    def test_version_ignored_in_ordering(self):
        # flask==2.0 sorts as flask, so it still comes after Django.
        self.assertEqual(problems_as_tuples("Django\nflask==2.0\n"), [])
        # And a big version does not save a late name.
        problems = problems_as_tuples("requests==9.9.9\nflask==1.0.0\n")
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], 2)

    def test_extras_sorted_by_name(self):
        self.assertEqual(problems_as_tuples("requests[security]\nurllib3\n"), [])
        problems = problems_as_tuples("urllib3[security]\nflask\n")
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], 2)

    def test_comments_and_blanks_ignored(self):
        text = "# header comment\n\nrequests==2.31.0\n# Django is next\nDjango==4.2.7\n\n"
        problems = problems_as_tuples(text)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], 5)

    def test_include_lines_ignored(self):
        text = "-r requirements-base.txt\nDjango\n-r other.txt\nflask\n"
        self.assertEqual(problems_as_tuples(text), [])

    def test_duplicate_in_sorted_position_passes(self):
        # Duplicates are another tool's job; equal names are in order.
        self.assertEqual(problems_as_tuples("requests==2.28.0\nrequests==2.31.0\n"), [])
        self.assertEqual(problems_as_tuples("Django\nDjango\nflask\n"), [])

    def test_duplicate_out_of_order_is_a_sort_error(self):
        problems = problems_as_tuples("requests\nDjango\nDjango\n")
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], 2)

    def test_multiple_out_of_order_entries(self):
        text = "requests==2.31.0\nDjango==4.2.7\nurllib3>=1.26\nflask\nnumpy>=1.24\ncelery==5.3.4\n"
        problems = problems_as_tuples(text)
        self.assertEqual([line for line, _ in problems], [2, 4, 6])
        self.assertIn("'Django'", problems[0][1])
        self.assertIn("'flask'", problems[1][1])
        self.assertIn("'celery'", problems[2][1])

    def test_empty_file(self):
        self.assertEqual(problems_as_tuples(""), [])
        self.assertEqual(problems_as_tuples("\n\n"), [])

    def test_comments_only_file(self):
        self.assertEqual(problems_as_tuples("# nothing here\n# just comments\n"), [])

    def test_single_entry(self):
        self.assertEqual(problems_as_tuples("requests==2.31.0\n"), [])

    def test_separator_names_sort_together(self):
        self.assertEqual(problems_as_tuples("my-package\nmy_package\n"), [])
        problems = problems_as_tuples("my_package\nmy-package\n")
        self.assertEqual(problems, [])

    def test_problem_text_format(self):
        problem = scan_text("b==1\na==2\n", "test.txt")[0]
        self.assertEqual(problem.text(),
                         "test.txt:2: 'a' is out of order — it should come before 'b' (line 1)")
        self.assertEqual(problem.as_dict(), {
            "file": "test.txt", "line": 2,
            "reason": "'a' is out of order — it should come before 'b' (line 1)",
        })

    def test_marker_line_sorts_by_name(self):
        text = 'requests==2.31.0; python_version > "3.8"\nflask\n'
        problems = problems_as_tuples(text)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], 2)


class CheckFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_null_byte(self):
        path = self.root / "requirements.txt"
        path.write_bytes(b"requests==1\n\x00\n")
        problems = check_file(path)
        self.assertEqual(len(problems), 1)
        self.assertIn("null byte", problems[0].reason)

    def test_non_utf8(self):
        path = self.root / "requirements.txt"
        path.write_bytes(b"requests==1\n\xff\xfe\n")
        problems = check_file(path)
        self.assertEqual(len(problems), 1)
        self.assertIn("not valid UTF-8", problems[0].reason)

    def test_unreadable_path(self):
        problems = check_file(self.root / "missing.txt")
        self.assertEqual(len(problems), 1)
        self.assertIn("cannot read file", problems[0].reason)


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_file_given_directly_is_checked_whatever_its_name(self):
        path = self.write("deps.txt", "b==1\na==2\n")
        checked, problems = collect([str(path)])
        self.assertEqual(len(checked), 1)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].line, 2)

    def test_sorted_file_passes(self):
        path = self.write("requirements.txt", "a==1\nb==2\n")
        checked, problems = collect([str(path)])
        self.assertEqual(len(checked), 1)
        self.assertEqual(problems, [])

    def test_folder_walk_only_requirements_names(self):
        self.write("requirements.txt", "a==1\n")
        self.write("requirements-dev.txt", "b==1\na==2\n")
        self.write("other.txt", "b==1\na==2\n")
        self.write("notes.md", "b\na\n")
        checked, problems = collect([str(self.root)])
        self.assertEqual(len(checked), 2)
        self.assertEqual(len(problems), 1)

    def test_folder_walk_recursive(self):
        self.write("requirements.txt", "a==1\n")
        self.write("configs/requirements.txt", "b==1\na==2\n")
        checked, problems = collect([str(self.root)])
        self.assertEqual(len(checked), 2)
        self.assertEqual(len(problems), 1)

    def test_no_recursive(self):
        self.write("requirements.txt", "a==1\n")
        self.write("configs/requirements.txt", "b==1\na==2\n")
        checked, problems = collect([str(self.root)], recursive=False)
        self.assertEqual(len(checked), 1)
        self.assertEqual(problems, [])

    def test_git_folder_skipped(self):
        self.write(".git/requirements.txt", "b==1\na==2\n")
        self.write("requirements.txt", "a==1\n")
        checked, problems = collect([str(self.root)])
        self.assertEqual(len(checked), 1)
        self.assertEqual(problems, [])

    def test_exclude(self):
        self.write("requirements.txt", "a==1\n")
        self.write("requirements-dev.txt", "b==1\na==2\n")
        checked, problems = collect([str(self.root)], exclude=["*dev*"])
        self.assertEqual(len(checked), 1)
        self.assertEqual(problems, [])

    def test_missing_path(self):
        checked, problems = collect([str(self.root / "nope.txt")])
        self.assertEqual(checked, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("no such file or folder", problems[0].reason)


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def run_cli(self, *args, stdin=None):
        return subprocess.run(
            [sys.executable, str(SRC), *args],
            capture_output=True, text=True, input=stdin)

    def test_clean_exit_0(self):
        path = self.write("requirements.txt", "a==1\nb==2\n")
        result = self.run_cli(str(path))
        self.assertEqual(result.returncode, 0)
        self.assertIn("OK — 1 file(s) checked", result.stdout)

    def test_unsorted_exit_1(self):
        path = self.write("requirements.txt", "b==1\na==2\n")
        result = self.run_cli(str(path))
        self.assertEqual(result.returncode, 1)
        self.assertIn("requirements.txt:2: 'a' is out of order", result.stdout)
        self.assertIn("Checked 1 file(s), 1 problem(s) in 1 file(s).", result.stdout)

    def test_no_args_exit_2(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)

    def test_missing_path_exit_1(self):
        result = self.run_cli(str(self.root / "nope.txt"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such file or folder", result.stdout)

    def test_stdin(self):
        result = self.run_cli("--stdin", stdin="b==1\na==2\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("<stdin>:2: 'a' is out of order", result.stdout)

    def test_stdin_with_paths_is_usage_error(self):
        result = self.run_cli("--stdin", "requirements.txt")
        self.assertEqual(result.returncode, 2)

    def test_json(self):
        path = self.write("requirements.txt", "b==1\na==2\n")
        result = self.run_cli(str(path), "--json")
        self.assertEqual(result.returncode, 1)
        data = json.loads(result.stdout)
        self.assertEqual(data["files_checked"], 1)
        self.assertEqual(data["files_with_problems"], 1)
        self.assertEqual(len(data["problems"]), 1)
        self.assertEqual(data["problems"][0]["line"], 2)
        self.assertIn("'a' is out of order", data["problems"][0]["reason"])
        self.assertIn("'a' is out of order", result.stderr)

    def test_json_clean(self):
        path = self.write("requirements.txt", "a==1\n")
        result = self.run_cli(str(path), "--json")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["problems"], [])

    def test_version(self):
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn("1.0.0", result.stdout)

    def test_bad_flag_exit_2(self):
        result = self.run_cli("--nope")
        self.assertEqual(result.returncode, 2)

    def test_input_files_unchanged(self):
        path = self.write("requirements.txt", "b==1\na==2\n")
        before = path.read_text()
        self.run_cli(str(path))
        self.assertEqual(path.read_text(), before)

    def test_folder_run(self):
        self.write("requirements.txt", "a==1\n")
        self.write("configs/requirements.txt", "b==1\na==2\n")
        result = self.run_cli(str(self.root))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Checked 2 file(s), 1 problem(s) in 1 file(s).", result.stdout)


class ExamplesTests(unittest.TestCase):
    def test_run_example_output_matches_sample(self):
        result = subprocess.run(
            ["sh", "examples/run-example.sh"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0)
        sample = (ROOT / "examples" / "sample-output.md").read_text()
        expected = "```text\n" + result.stdout + "```\n\n"
        self.assertTrue(sample.startswith(expected),
                        "sample-output.md does not match a fresh run")

    def test_examples_checked(self):
        checked, problems = collect([str(ROOT / "examples")])
        self.assertEqual(len(checked), 4)
        self.assertEqual(len(problems), 4)
        self.assertEqual(len({p.path for p in problems}), 2)

    def test_readme_quoted_outputs_are_real(self):
        readme = (ROOT / "README.md").read_text()
        for block in [
            "OK — 1 file(s) checked, all requirements files are clean.",
            "examples/requirements-unsorted.txt:4: 'Django' is out of order — "
            "it should come before 'requests' (line 2)",
            "examples/requirements-unsorted.txt:8: 'celery' is out of order — "
            "it should come before 'numpy' (line 7)",
            "examples/configs/requirements.txt:2: 'awscli' is out of order — "
            "it should come before 'boto3' (line 1)",
            "Checked 4 file(s), 4 problem(s) in 2 file(s).",
        ]:
            self.assertIn(block, readme, f"README is missing real output: {block!r}")

    def test_readme_json_block_is_real(self):
        result = subprocess.run(
            [sys.executable, str(SRC), "examples/configs/requirements.txt", "--json"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 1)
        data = json.loads(result.stdout)
        readme = (ROOT / "README.md").read_text()
        start = readme.index("```json\n") + len("```json\n")
        end = readme.index("```", start)
        quoted = json.loads(readme[start:end])
        self.assertEqual(quoted, data)


class HygieneTests(unittest.TestCase):
    def test_gitignore_first_line_is_env(self):
        lines = (ROOT / ".gitignore").read_text().splitlines()
        self.assertEqual(lines[0], ".env")

    def test_no_banned_words_outside_tests(self):
        banned = ["TODO", "FIXME", "XXX", "lorem", "coming soon"]
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if "tests" in path.parts:
                continue
            text = path.read_text(errors="replace")
            for word in banned:
                self.assertNotIn(word, text,
                                 f"{word} found in {path.relative_to(ROOT)}")

    def test_no_absolute_paths(self):
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if "tests" in path.parts:
                continue
            text = path.read_text(errors="replace")
            self.assertNotIn("/home/hatch", text,
                             f"absolute path in {path.relative_to(ROOT)}")
            self.assertNotIn("/Users/", text,
                             f"absolute path in {path.relative_to(ROOT)}")
            self.assertNotIn("/tmp/", text,
                             f"absolute path in {path.relative_to(ROOT)}")

    def test_ci_yml_markers(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for marker in ["actions/checkout@v4", "actions/setup-python@v5",
                       '"3.12"', "tests/test_requirements_sort_checker.py",
                       "push", "pull_request"]:
            self.assertIn(marker, text, f"ci.yml is missing {marker!r}")

    def test_stdlib_only_imports(self):
        allowed_src = {
            "argparse", "fnmatch", "json", "os", "re", "sys", "pathlib",
        }
        allowed_tests = allowed_src | {
            "io", "subprocess", "tempfile", "unittest", "ast",
            "requirements_sort_checker",
        }
        import ast
        for path, allowed in [(SRC, allowed_src),
                              (Path(__file__), allowed_tests)]:
            tree = ast.parse(path.read_text())
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imported.add(node.module.split(".")[0])
            self.assertTrue(imported <= allowed,
                            f"{path.name} imports non-stdlib: "
                            f"{imported - allowed}")
            self.assertNotIn("yaml", imported)

    def test_no_yaml_anywhere_in_source(self):
        text = SRC.read_text()
        self.assertNotIn("yaml", text)

    def test_no_secrets_in_repo(self):
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if "tests" in path.parts:
                continue
            text = path.read_text(errors="replace").lower()
            for marker in ["api_key", "api-key", "password =", "secret ="]:
                self.assertNotIn(marker, text,
                                 f"possible secret in {path.relative_to(ROOT)}")


if __name__ == "__main__":
    unittest.main()
