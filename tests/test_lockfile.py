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


def test_release_removes_lockfile(tmp_path: Path) -> None:
    path = tmp_path / "nsrecruiter.lock"
    lock = LockFile(path)
    lock.acquire()
    lock.release()
    assert not path.exists()


def test_second_acquire_with_live_pid_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "nsrecruiter.lock"
    path.write_text(str(os.getpid()))
    lock = LockFile(path)
    with pytest.raises(InstanceAlreadyRunningError):
        lock.acquire()


def test_acquire_recovers_from_stale_lock(tmp_path: Path) -> None:
    path = tmp_path / "nsrecruiter.lock"
    stale_pid = 999_999
    path.write_text(str(stale_pid))
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
    assert not path.exists()
