import os
import stat
import sys
import threading
import time

import pytest

from obb_terminal.fsutil import LockBusy, atomic_write_text, file_lock


def test_writes_and_replaces(tmp_path):
    path = tmp_path / "a" / "b" / "file.json"
    atomic_write_text(path, "one")
    atomic_write_text(path, "two")
    assert path.read_text() == "two"
    assert sorted(p.name for p in path.parent.iterdir()) == ["file.json"]  # no temp files


def test_failure_raises_and_leaves_no_temp(tmp_path, monkeypatch):
    path = tmp_path / "file.json"

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(path, "x")
    assert list(tmp_path.iterdir()) == []


def test_parent_is_a_file_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(OSError):
        atomic_write_text(blocker / "child.json", "x")


def test_lock_creates_private_file_and_is_reentrant_after_release(tmp_path):
    path = tmp_path / "dir" / "targets.json.lock"
    with file_lock(path, timeout_s=1):
        if sys.platform != "win32":  # Windows has no POSIX permission bits
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with file_lock(path, timeout_s=1):
        pass
    assert path.exists()  # never deleted


def test_second_holder_times_out_while_first_holds(tmp_path):
    path = tmp_path / "x.lock"
    with file_lock(path, timeout_s=1):
        started = time.monotonic()
        with pytest.raises(LockBusy), file_lock(path, timeout_s=0.2, poll_s=0.01):
            pass
        assert time.monotonic() - started >= 0.2


def test_lock_released_when_body_raises(tmp_path):
    path = tmp_path / "x.lock"
    with pytest.raises(RuntimeError), file_lock(path, timeout_s=1):
        raise RuntimeError("boom")
    with file_lock(path, timeout_s=0.1):
        pass


def test_waiter_gets_lock_once_released(tmp_path):
    path = tmp_path / "x.lock"
    entered, release = threading.Event(), threading.Event()
    order = []

    def holder():
        with file_lock(path, timeout_s=1):
            entered.set()
            release.wait(2)
            order.append("holder")

    thread = threading.Thread(target=holder)
    thread.start()
    entered.wait(2)
    threading.Timer(0.1, release.set).start()
    with file_lock(path, timeout_s=2, poll_s=0.01):
        order.append("waiter")
    thread.join(2)
    assert order == ["holder", "waiter"]


# --- the Windows branch, run here against a stand-in msvcrt ---------------------------------


@pytest.fixture
def windows_fsutil(monkeypatch):
    """fsutil re-imported as on Windows. The stand-in msvcrt.locking keeps Windows' contract
    (LK_NBLCK raises OSError while another handle holds the byte) on top of flock."""
    if sys.platform == "win32":
        pytest.skip("the stand-in msvcrt needs fcntl; real Windows runs the tests above natively")
    import fcntl
    import importlib
    import types

    from obb_terminal import fsutil

    calls = []

    def locking(fd, mode, nbytes):
        calls.append((mode, nbytes, os.lseek(fd, 0, os.SEEK_CUR)))
        if mode == fake.LK_NBLCK:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PermissionError(13, "Permission denied") from exc
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)

    fake = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking, calls=calls)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    module = importlib.reload(fsutil)
    yield module, fake
    monkeypatch.undo()
    importlib.reload(fsutil)


def test_windows_lock_excludes_a_second_holder(windows_fsutil, tmp_path):
    module, fake = windows_fsutil
    path = tmp_path / "x.lock"
    with module.file_lock(path, timeout_s=1.0):
        with pytest.raises(module.LockBusy):
            with module.file_lock(path, timeout_s=0.1):
                pass
    with module.file_lock(path, timeout_s=0.1):
        pass
    assert {(mode, n, pos) for mode, n, pos in fake.calls} == {(2, 1, 0), (0, 1, 0)}


def test_windows_atomic_write_skips_the_directory_fsync(windows_fsutil, tmp_path, monkeypatch):
    module, _ = windows_fsutil
    real_open = os.open

    def no_dirs(path, flags, *args):
        if os.path.isdir(path):
            raise PermissionError(13, "Windows cannot open a directory")
        return real_open(path, flags, *args)

    monkeypatch.setattr(module.os, "open", no_dirs)
    module.atomic_write_text(tmp_path / "a.json", "{}")
    assert (tmp_path / "a.json").read_text() == "{}"


def test_windows_replace_retries_while_a_reader_holds_the_target(windows_fsutil, tmp_path, monkeypatch):
    module, _ = windows_fsutil
    real_replace, attempts = os.replace, []

    def busy_twice(src, dst):
        attempts.append(dst)
        if len(attempts) < 3:
            raise PermissionError(13, "The process cannot access the file")
        real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", busy_twice)
    monkeypatch.setattr(module, "REPLACE_RETRY_S", 0.0)
    module.atomic_write_text(tmp_path / "b.json", "1")
    assert (tmp_path / "b.json").read_text() == "1" and len(attempts) == 3
