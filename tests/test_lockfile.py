"""Testes do lockfile de instancia unica."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from nsrecruiter.lockfile import InstanceAlreadyRunningError, LockFile


def test_acquire_creates_lockfile_with_own_pid(tmp_path: Path) -> None:
    lock = LockFile(tmp_path / "nsrecruiter.lock")
    lock.acquire()
    try:
        assert (tmp_path / "nsrecruiter.lock").read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_release_lets_another_instance_acquire(tmp_path: Path) -> None:
    path = tmp_path / "nsrecruiter.lock"
    lock = LockFile(path)
    lock.acquire()
    lock.release()

    with LockFile(path):
        pass


def test_second_acquire_while_first_is_held_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "nsrecruiter.lock"
    with LockFile(path):
        with pytest.raises(InstanceAlreadyRunningError):
            LockFile(path).acquire()


@pytest.mark.parametrize("leftover_pid", [999_999, os.getpid()])
def test_acquire_ignores_leftover_file_whatever_pid_it_holds(tmp_path: Path, leftover_pid: int) -> None:
    """Depois de um corte de energia fica o ficheiro, sem lock. O PID la escrito pode
    ate estar vivo (reutilizado por outro programa apos o reboot): nao pode impedir o arranque."""
    path = tmp_path / "nsrecruiter.lock"
    path.write_text(str(leftover_pid))
    lock = LockFile(path)
    lock.acquire()
    try:
        assert path.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_context_manager_releases_on_exception(tmp_path: Path) -> None:
    path = tmp_path / "nsrecruiter.lock"
    with pytest.raises(RuntimeError):
        with LockFile(path):
            raise RuntimeError("falha proposital")

    with LockFile(path):
        pass
