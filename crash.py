"""Global crash handler: writes a report next to the activity log and tells the user where it is."""
import sys
import platform
import threading
import traceback
from datetime import datetime
from pathlib import Path


def write_report(log_dir, app_name, version, exc_type, exc, tb):
    """Append a crash report; returns its path (None if the disk refused)."""
    path = Path(log_dir) / "crash.log"
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    block = (f"==== {datetime.now():%Y-%m-%d %H:%M:%S} · {app_name} v{version} · "
             f"{platform.system()} {platform.release()} · Python {platform.python_version()} ====\n{text}\n")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(block)
        return path
    except OSError:
        return None


def install(log_dir, app_name, version, on_crash=None):
    """Route uncaught exceptions (main thread and plain threads) to write_report + optional UI callback."""
    def handle(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        path = write_report(log_dir, app_name, version, exc_type, exc, tb)
        sys.__excepthook__(exc_type, exc, tb)
        if on_crash:
            try:
                on_crash(path, exc_type, exc)
            except Exception:
                pass
    sys.excepthook = handle
    threading.excepthook = lambda a: handle(a.exc_type, a.exc_value, a.exc_traceback)
    return handle
