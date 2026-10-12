"""Submission commands, asynchronous state, retry, and repository boundaries."""

from __future__ import annotations

import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.publish import OUTPUT_LIMIT, CommandResult, Publisher, SubmissionBusy, run_git


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PublishTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = PROJECT_ROOT / "tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.webpage = self.root / "webpage"
        (self.webpage / ".git").mkdir(parents=True)
        self.write_lock = threading.RLock()
        self.commands: list[tuple[list[str], Path]] = []
        self.responses: dict[tuple[str, ...], CommandResult] = {}

    def runner(self, command: list[str], directory: Path) -> CommandResult:
        self.commands.append((command, directory))
        if command == ["git", "rev-parse", "--show-toplevel"]:
            return self.responses.get(tuple(command), CommandResult(0, str(self.webpage) + "\n"))
        if command == ["git", "branch", "--show-current"]:
            return self.responses.get(tuple(command), CommandResult(0, "master\n"))
        return self.responses.get(tuple(command), CommandResult(0))

    def completed(self, publisher: Publisher) -> dict[str, object]:
        deadline = time.monotonic() + 5
        while publisher.state()["running"] and time.monotonic() < deadline:
            time.sleep(0.002)
        result = publisher.state()
        self.assertFalse(result["running"], "publisher did not finish")
        return result

    def test_exact_commands_cwd_and_success_state(self) -> None:
        publisher = Publisher(self.root, self.write_lock, self.runner)
        self.assertEqual(publisher.state(), {"running": False, "success": None, "error": None})
        self.assertEqual(publisher.start(), {"running": True, "success": None, "error": None})
        self.assertEqual(self.completed(publisher), {"running": False, "success": True, "error": None})
        self.assertEqual([command for command, _ in self.commands], [
            ["git", "rev-parse", "--show-toplevel"], ["git", "branch", "--show-current"],
            ["git", "add", "."], ["git", "commit", "-m", "automatic update"],
            ["git", "push", "origin", "master"],
        ])
        self.assertTrue(all(directory == self.webpage for _, directory in self.commands))

    def test_busy_rejects_second_start_and_push_does_not_lock_editing(self) -> None:
        pushing = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def blocked_runner(command: list[str], directory: Path) -> CommandResult:
            result = self.runner(command, directory)
            if command == ["git", "push", "origin", "master"]:
                pushing.set()
                self.assertTrue(release.wait(5))
            return result

        publisher = Publisher(self.root, self.write_lock, blocked_runner)
        publisher.start()
        self.assertTrue(pushing.wait(5))
        with self.assertRaises(SubmissionBusy):
            publisher.start()
        self.assertTrue(self.write_lock.acquire(blocking=False))
        self.write_lock.release()
        release.set()
        self.assertTrue(self.completed(publisher)["success"])
        self.assertEqual(sum(command == ["git", "push", "origin", "master"] for command, _ in self.commands), 1)

    def test_staging_waits_for_finished_database_write(self) -> None:
        preparing = threading.Event()
        added = threading.Event()

        def runner(command: list[str], directory: Path) -> CommandResult:
            result = self.runner(command, directory)
            if command == ["git", "branch", "--show-current"]:
                preparing.set()
            if command == ["git", "add", "."]:
                added.set()
            return result

        publisher = Publisher(self.root, self.write_lock, runner)
        with self.write_lock:
            publisher.start()
            self.assertTrue(preparing.wait(5))
            self.assertFalse(added.is_set())
        self.assertTrue(self.completed(publisher)["success"])
        self.assertTrue(added.is_set())

    def test_each_failed_stage_stops_later_commands_and_reports_output(self) -> None:
        for command in (
            ["git", "add", "."], ["git", "commit", "-m", "automatic update"],
            ["git", "push", "origin", "master"],
        ):
            with self.subTest(stage=command[1]), patch("builtins.print"):
                self.commands.clear()
                output = "fatal: test failure"
                if command[1] == "push":
                    output = "x" * (OUTPUT_LIMIT + 100) + "\n" + output
                self.responses = {tuple(command): CommandResult(128, output)}
                publisher = Publisher(self.root, self.write_lock, self.runner)
                publisher.start()
                state = self.completed(publisher)
                self.assertFalse(state["success"])
                self.assertIn(f"git {command[1]}失败", state["error"])
                self.assertIn("fatal: test failure", state["error"])
                self.assertTrue(state["error"].startswith(f"git {command[1]}失败"))
                self.assertLessEqual(len(state["error"]), OUTPUT_LIMIT + 20)
                self.assertEqual(self.commands[-1][0], command)

    def test_retry_pushes_existing_commit_when_index_is_clean(self) -> None:
        publisher = Publisher(self.root, self.write_lock, self.runner)
        self.responses[("git", "push", "origin", "master")] = CommandResult(1, "network unavailable")
        publisher.start()
        self.assertFalse(self.completed(publisher)["success"])
        self.commands.clear()
        self.responses = {
            ("git", "commit", "-m", "automatic update"): CommandResult(1, "nothing to commit, working tree clean"),
            ("git", "diff", "--cached", "--quiet"): CommandResult(0),
        }
        publisher.start()
        self.assertEqual(self.completed(publisher), {"running": False, "success": True, "error": None})
        self.assertEqual(self.commands[-1][0], ["git", "push", "origin", "master"])

    def test_failed_commit_with_staged_changes_is_not_skipped(self) -> None:
        self.responses = {
            ("git", "commit", "-m", "automatic update"): CommandResult(1, "pre-commit hook rejected"),
            ("git", "diff", "--cached", "--quiet"): CommandResult(1),
        }
        publisher = Publisher(self.root, self.write_lock, self.runner)
        publisher.start()
        self.assertIn("pre-commit hook rejected", self.completed(publisher)["error"])
        self.assertFalse(any(command[1] == "push" for command, _ in self.commands))

    def test_parent_repository_and_wrong_branch_are_rejected_before_staging(self) -> None:
        cases = [
            (("git", "rev-parse", "--show-toplevel"), CommandResult(0, str(self.root)), "根目录"),
            (("git", "branch", "--show-current"), CommandResult(0, "feature\n"), "master"),
            (("git", "branch", "--show-current"), CommandResult(0, ""), "master"),
        ]
        for command, response, expected in cases:
            with self.subTest(expected=expected, output=response.output):
                self.commands.clear()
                self.responses = {command: response}
                publisher = Publisher(self.root, self.write_lock, self.runner)
                publisher.start()
                self.assertIn(expected, self.completed(publisher)["error"])
                self.assertFalse(any(command[1] == "add" for command, _ in self.commands))

    def test_missing_independent_git_repository_never_runs_git(self) -> None:
        (self.webpage / ".git").rmdir()
        publisher = Publisher(self.root, self.write_lock, self.runner)
        publisher.start()
        self.assertIn("独立 Git 仓库", self.completed(publisher)["error"])
        self.assertEqual(self.commands, [])

    def test_git_runner_streams_bounded_output_without_a_shell(self) -> None:
        output = "early output\n" + "x" * (OUTPUT_LIMIT + 100) + "\nfatal: final detail\n"
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.stdout = io.StringIO(output)
        process.wait.return_value = 1
        with patch("backend.publish.subprocess.Popen", return_value=process) as popen, patch("sys.stdout", new_callable=io.StringIO) as terminal:
            result = run_git(["git", "push", "origin", "master"], self.webpage)
            self.assertIn(output, terminal.getvalue())
        self.assertEqual(len(result.output), OUTPUT_LIMIT)
        self.assertTrue(result.output.endswith("fatal: final detail\n"))
        self.assertEqual(popen.call_args.args[0], ["git", "push", "origin", "master"])
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(popen.call_args.kwargs["cwd"], self.webpage)

    def test_submission_api_returns_accepted_status_polling_and_conflict(self) -> None:
        from backend import server

        handler = object.__new__(server.AdminHandler)
        handler.command = "POST"
        handler._validate_request_host = Mock()
        handler._path = Mock(return_value=["api", "submit"])
        handler._body = Mock(return_value={})
        handler._json = Mock()
        publisher = Mock()
        publisher.start.return_value = {"running": True, "success": None, "error": None}
        with patch.object(server, "PUBLISHER", publisher):
            handler._handle()
            handler._json.assert_called_once_with(202, publisher.start.return_value)
            handler._json.reset_mock()
            handler.command = "GET"
            publisher.state.return_value = {"running": False, "success": True, "error": None}
            handler._handle()
            handler._json.assert_called_once_with(200, publisher.state.return_value)
            handler._json.reset_mock()
            handler.command = "POST"
            publisher.start.side_effect = SubmissionBusy("提交正在进行，请稍后重试")
            handler._handle()
            handler._json.assert_called_once_with(409, {"error": "提交正在进行，请稍后重试"})


if __name__ == "__main__":
    unittest.main()
