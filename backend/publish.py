"""Publish the canonical webpage repository without blocking data editing."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


OUTPUT_LIMIT = 8000


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    output: str = ""


class SubmissionBusy(RuntimeError):
    """Another submission already owns the publishing worker."""


class PublishError(RuntimeError):
    """A Git operation could not finish successfully."""


def run_git(command: Sequence[str], directory: Path) -> CommandResult:
    """Stream combined Git output to the backend terminal and retain its tail."""
    print(f"[Submit] $ {' '.join(command)}", flush=True)
    output = ""
    with subprocess.Popen(
        list(command), cwd=directory, shell=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", bufsize=1,
    ) as process:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            output = (output + line)[-OUTPUT_LIMIT:]
        return CommandResult(process.wait(), output)


class Publisher:
    """Run one asynchronous add/commit/push sequence at a time."""

    def __init__(
        self, root: Path, write_lock: threading.RLock,
        runner: Callable[[Sequence[str], Path], CommandResult] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.directory = self.root / "webpage"
        self.write_lock = write_lock
        self.runner = runner or run_git
        self._lock = threading.Lock()
        self._state: dict[str, object] = {"running": False, "success": None, "error": None}

    def state(self) -> dict[str, object]:
        with self._lock:
            return dict(self._state)

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._state["running"]:
                raise SubmissionBusy("提交正在进行，请稍后重试")
            self._state = {"running": True, "success": None, "error": None}
            started = dict(self._state)
            try:
                threading.Thread(target=self._worker, name="webpage-submit", daemon=True).start()
            except RuntimeError:
                self._state = {"running": False, "success": False, "error": "无法启动提交任务"}
                raise
            return started

    def _command(self, arguments: Sequence[str], stage: str) -> CommandResult:
        try:
            result = self.runner(["git", *arguments], self.directory)
        except OSError as exc:
            raise PublishError(f"{stage}失败：{str(exc)[-OUTPUT_LIMIT:]}") from exc
        return CommandResult(result.returncode, result.output[-OUTPUT_LIMIT:])

    @staticmethod
    def _check(result: CommandResult, stage: str) -> None:
        if result.returncode:
            detail = result.output.strip() or f"Git 退出码 {result.returncode}"
            raise PublishError(f"{stage}失败：\n{detail}")

    def _publish(self) -> None:
        # Do not accidentally stage a parent repository or a linked external directory.
        if not self.directory.is_dir() or self.directory.resolve() != self.directory:
            raise PublishError("提交失败：webpage 必须是项目内的独立目录")
        if not (self.directory / ".git").exists():
            raise PublishError("提交失败：webpage 尚未配置独立 Git 仓库")
        top = self._command(["rev-parse", "--show-toplevel"], "检查仓库")
        self._check(top, "检查仓库")
        if Path(top.output.strip()).resolve() != self.directory:
            raise PublishError("提交失败：Git 仓库根目录必须是 webpage")
        branch = self._command(["branch", "--show-current"], "检查分支")
        self._check(branch, "检查分支")
        if branch.output.strip() != "master":
            raise PublishError("提交失败：webpage 必须位于 master 分支")

        # Staging sees a completed database write; slow commit and push leave editing free.
        with self.write_lock:
            added = self._command(["add", "."], "git add")
            self._check(added, "git add")
        committed = self._command(["commit", "-m", "automatic update"], "git commit")
        if committed.returncode == 1:
            # A retry after a rejected/offline push can have nothing new to commit.
            staged = self._command(["diff", "--cached", "--quiet"], "检查暂存内容")
            if staged.returncode == 0:
                print("[Submit] 没有新的暂存修改，继续推送已有提交。", flush=True)
            else:
                self._check(committed, "git commit")
        else:
            self._check(committed, "git commit")
        pushed = self._command(["push", "origin", "master"], "git push")
        self._check(pushed, "git push")

    def _worker(self) -> None:
        error: str | None = None
        try:
            self._publish()
            print("[Submit] 提交完成。", flush=True)
        except Exception as exc:
            error = str(exc) if isinstance(exc, PublishError) else f"提交失败：{str(exc)[-OUTPUT_LIMIT:]}"
            print(f"[Submit] {error}", flush=True)
        finally:
            with self._lock:
                self._state = {"running": False, "success": error is None, "error": error}
