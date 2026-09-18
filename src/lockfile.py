"""Lockfile de instancia unica (impede duas instancias em simultaneo)."""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from types import TracebackType


class InstanceAlreadyRunningError(Exception):
    """Ja existe outra instancia a correr, segundo o lockfile."""


def _is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class LockFile:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._locked = False

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            self._handle_existing_lock()
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        self._locked = True

    def _handle_existing_lock(self) -> None:
        try:
            existing_pid_text = self._path.read_text(encoding="utf-8").strip()
            existing_pid = int(existing_pid_text) if existing_pid_text else -1
        except (OSError, ValueError):
            existing_pid = -1

        if _is_process_alive(existing_pid):
            raise InstanceAlreadyRunningError(
                "Ja existe outra instancia do NSRecruiter em execucao "
                f"(PID {existing_pid}). Termina-a antes de iniciar uma nova -- "
                "as regras da plataforma exigem uma unica instancia por chave."
            )
        self._path.unlink(missing_ok=True)

    def release(self) -> None:
        if not self._locked:
            return
        try:
            current_pid_text = self._path.read_text(encoding="utf-8").strip()
            if current_pid_text == str(os.getpid()):
                self._path.unlink(missing_ok=True)
        except OSError:
            pass
        self._locked = False

    def __enter__(self) -> LockFile:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
