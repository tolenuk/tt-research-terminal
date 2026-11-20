import subprocess

from obb_terminal.alerts.notify import DesktopNotifier, pango_escape


def test_pango_escape_order():
    assert pango_escape("a & <b> && >") == "a &amp; &lt;b&gt; &amp;&amp; &gt;"
    assert pango_escape("&lt;") == "&amp;lt;"


def test_argv_is_a_list_with_end_of_options_and_escaped_body():
    calls = []
    notifier = DesktopNotifier(runner=lambda argv: calls.append(argv) or 0, which=lambda n: "/usr/bin/" + n)
    assert notifier.notify("TT Research Terminal", "-AT&T <b>: P/E ≤ 25 hit")
    assert calls == [["notify-send", "--app-name", "TT Research Terminal", "--", "TT Research Terminal",
                      "-AT&amp;T &lt;b&gt;: P/E ≤ 25 hit"]]


def test_missing_binary_logs_once_and_returns_false(caplog):
    calls = []
    notifier = DesktopNotifier(runner=lambda argv: calls.append(argv) or 0, which=lambda n: None)
    assert not notifier.notify("t", "b")
    assert not notifier.notify("t", "b")
    assert calls == []
    assert caplog.text.count("notify-send not found") == 1


def test_failures_return_false_and_never_raise():
    def timeout(argv):
        raise subprocess.TimeoutExpired(argv, 3)

    def oserror(argv):
        raise OSError("exec failed")

    found = lambda n: "/usr/bin/notify-send"  # noqa: E731
    assert not DesktopNotifier(runner=lambda argv: 1, which=found).notify("t", "b")
    assert not DesktopNotifier(runner=timeout, which=found).notify("t", "b")
    assert not DesktopNotifier(runner=oserror, which=found).notify("t", "b")


# --- Windows toasts --------------------------------------------------------------------------

import base64  # noqa: E402

from obb_terminal.alerts import notify as notify_mod  # noqa: E402
from obb_terminal.alerts.notify import WindowsToastNotifier, default_notifier  # noqa: E402


def _script(argv):
    assert argv[:5] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden"]
    assert argv[5] == "-EncodedCommand" and len(argv) == 7
    return base64.b64decode(argv[6]).decode("utf-16-le")


def test_toast_carries_title_and_body_only_as_base64_data():
    calls = []
    toast = WindowsToastNotifier(runner=lambda argv: calls.append(argv) or 0, which=lambda n: "C:/ps.exe")
    title, body = "SHEL.L: all targets hit", "Price 27.10 <= 28 & \"quoted\" '$(evil)' `n"
    assert toast.notify(title, body)
    script = _script(calls[0])
    for text in (title, body):
        assert base64.b64encode(text.encode("utf-8")).decode("ascii") in script
        assert text not in script  # never spliced into PowerShell source
    assert "CreateTextNode" in script and "ToastNotificationManager" in script


def test_toast_failures_return_false_and_never_raise():
    found = lambda n: "C:/ps.exe"  # noqa: E731

    def boom(argv):
        raise OSError("no")

    assert not WindowsToastNotifier(runner=lambda argv: 1, which=found).notify("t", "b")
    assert not WindowsToastNotifier(runner=boom, which=found).notify("t", "b")
    assert not WindowsToastNotifier(runner=lambda argv: 0, which=lambda n: None).notify("t", "b")


def test_default_notifier_follows_the_platform(monkeypatch):
    monkeypatch.setattr(notify_mod, "IS_WINDOWS", True)
    assert isinstance(default_notifier(), WindowsToastNotifier)
    monkeypatch.setattr(notify_mod, "IS_WINDOWS", False)
    monkeypatch.setattr(notify_mod, "IS_MACOS", False)
    assert isinstance(default_notifier(), DesktopNotifier)


# --- macOS ------------------------------------------------------------------------------------

import sys  # noqa: E402

import pytest  # noqa: E402

from obb_terminal.alerts.notify import MacNotifier, osascript_argv  # noqa: E402


def test_osascript_passes_title_and_body_as_arguments_never_as_script():
    title, body = "AT&T: all targets hit", 'Price 27.10 <= 28 "quoted" & (do shell script "x")'
    argv = osascript_argv(title, body)
    assert argv[0] == "osascript" and argv[-2:] == [title, body]
    script = " ".join(argv[1:-2])
    assert title not in script and body not in script
    assert "display notification (item 2 of argv) with title (item 1 of argv)" in script


def test_osascript_title_never_reads_as_an_option():
    assert osascript_argv("-x", "b")[-2:] == [" -x", "b"]


def test_mac_notifier_sends_and_never_raises(caplog):
    calls = []
    assert MacNotifier(runner=lambda argv: calls.append(argv) or 0, which=lambda n: "/usr/bin/osascript").notify("t", "b")
    assert calls == [osascript_argv("t", "b")]

    def boom(argv):
        raise subprocess.TimeoutExpired(argv, 3)

    found = lambda n: "/usr/bin/osascript"  # noqa: E731
    assert not MacNotifier(runner=lambda argv: 1, which=found).notify("t", "b")
    assert not MacNotifier(runner=boom, which=found).notify("t", "b")
    missing = MacNotifier(runner=lambda argv: 0, which=lambda n: None)
    assert not missing.notify("t", "b") and not missing.notify("t", "b")
    assert caplog.text.count("osascript not found") == 1


def test_default_notifier_on_macos(monkeypatch):
    monkeypatch.setattr(notify_mod, "IS_WINDOWS", False)
    monkeypatch.setattr(notify_mod, "IS_MACOS", True)
    assert isinstance(default_notifier(), MacNotifier)


@pytest.mark.skipif(sys.platform != "darwin", reason="needs the real osascript")
def test_real_osascript_receives_the_arguments_verbatim():
    title, body = "SHEL.L", '-AT&T "x" ≤ 25'
    argv = osascript_argv(title, body)
    script = [a.replace("display notification (item 2 of argv) with title (item 1 of argv)",
                        'return (item 1 of argv) & "|" & (item 2 of argv)') for a in argv[:-2]]
    done = subprocess.run([*script, *argv[-2:]], capture_output=True, text=True, timeout=10, check=True)
    assert done.stdout.rstrip("\n") == f"{title}|{body}"
