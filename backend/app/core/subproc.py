"""Safe external-process execution: argv lists only (no shell), timeouts and resource limits."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import ProcessingError, ToolUnavailable


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str


def which(cmd: str) -> str | None:
    if os.path.isabs(cmd):
        return cmd if os.access(cmd, os.X_OK) else None
    return shutil.which(cmd)


def _limits(memory_mb: int | None) -> Callable[[], None] | None:
    if sys.platform == "win32":
        return None

    def apply() -> None:
        import resource

        os.setsid()  # own process group so the whole tree can be killed on timeout
        if memory_mb:
            limit = memory_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            except (ValueError, OSError):
                pass
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ValueError, OSError):
            pass

    return apply


def run(argv: Sequence[str | Path], *, timeout: int | None = None, cwd: Path | None = None,
        memory_mb: int | None = None, env: dict[str, str] | None = None,
        tool_name: str | None = None, check: bool = True, limit_address_space: bool = True) -> RunResult:
    """Run argv. `limit_address_space=False` is for runtimes (JVM, LibreOffice) that reserve large
    virtual memory up front; bound those with their own heap options and the timeout instead."""
    settings = get_settings()
    args = [str(a) for a in argv]
    exe = which(args[0])
    if exe is None:
        raise ToolUnavailable(f"Required executable '{args[0]}' was not found. See docs/troubleshooting.md.",
                              details={"tool": tool_name or args[0]})
    args[0] = exe
    run_env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "HOME": os.environ.get("HOME", str(cwd or "/tmp"))}
    if "TESSDATA_PREFIX" in os.environ:
        run_env["TESSDATA_PREFIX"] = os.environ["TESSDATA_PREFIX"]
    if env:
        run_env.update(env)
    proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
        args, cwd=str(cwd) if cwd else None, env=run_env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        preexec_fn=_limits((memory_mb or settings.subprocess_memory_mb) if limit_address_space else None),  # noqa: PLW1509
    )
    try:
        out, err = proc.communicate(timeout=timeout or settings.subprocess_timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, AttributeError):
            proc.kill()
        proc.communicate()
        raise ProcessingError(f"{tool_name or Path(args[0]).name} exceeded its time limit.") from None
    res = RunResult(proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))
    if check and res.returncode != 0:
        raise ProcessingError(f"{tool_name or Path(args[0]).name} failed (exit code {res.returncode}).",
                              details={"stderr_tail": res.stderr[-400:]})
    return res
