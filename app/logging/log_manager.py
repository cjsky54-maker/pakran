"""Rotating file logging for the whole application.

NOTE: this package is ``app.logging``; stdlib ``logging`` is imported via
absolute imports and is unaffected.
"""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import sys
import threading

LOGGER_NAME = "DriverThermalQC"
_MAX_BYTES = 2_000_000
_BACKUP_COUNT = 3

_configured = False
_crash_configured = False
# Keep references so the file objects are never garbage-collected while
# faulthandler / the interpreter still need them.
_crash_sink = None
_stderr_sink = None


def setup_logging(logs_dir: str, console: bool = False) -> logging.Logger:
    """Configure the application logger. Safe to call more than once."""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured and logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    os.makedirs(logs_dir, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(logs_dir, "app.log"),
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    if console:
        stream = logging.StreamHandler()
        stream.setLevel(logging.INFO)
        stream.setFormatter(fmt)
        logger.addHandler(stream)
    logger.propagate = False
    _configured = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Namespaced child logger (e.g. get_logger('camera'))."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


# -- crash evidence ---------------------------------------------------------------
#
# A native crash (access violation inside a camera driver, DirectShow or
# Media Foundation) kills the process INSTANTLY with no Python exception
# and therefore no app.log line - to the user "the app suddenly closes
# while connecting". These hooks make sure the next crash leaves evidence:

def install_crash_diagnostics(logs_dir: str, version: str = "") -> str:
    """Install best-effort crash evidence collection. Idempotent.

    1. ``faulthandler`` writes the faulting thread's Python stack trace to
       ``crash.log`` before a native crash takes the process down.
    2. In a windowed (no console) PyInstaller build ``sys.stderr`` is
       ``None``; tracebacks printed by Qt/PySide6 right before it aborts
       the app would vanish - redirect them to ``stderr.log``.
    3. ``sys.excepthook`` / ``threading.excepthook`` route uncaught
       exceptions into the application log.

    Returns the crash log path ("" when unavailable).
    """
    global _crash_configured, _crash_sink, _stderr_sink
    if _crash_configured:
        return getattr(install_crash_diagnostics, "_crash_path", "")
    _crash_configured = True
    try:
        os.makedirs(logs_dir, exist_ok=True)
    except OSError:
        pass

    crash_path = os.path.join(logs_dir, "crash.log")
    try:
        # Keep the file bounded: a crashed session leaves a partial line,
        # the next session starts fresh.
        if os.path.exists(crash_path) and os.path.getsize(crash_path) > 1_000_000:
            os.replace(crash_path, crash_path + ".old")
        _crash_sink = open(crash_path, "a", encoding="utf-8", buffering=1)
        _crash_sink.write(
            f"--- session start v{version} pid={os.getpid()} ---\n"
        )
        _crash_sink.flush()
        faulthandler.enable(file=_crash_sink, all_threads=True)
    except Exception:  # pragma: no cover - diagnostics must never break startup
        crash_path = ""
    install_crash_diagnostics._crash_path = crash_path

    if sys.stderr is None:  # windowed exe - keep Qt's fatal tracebacks
        try:
            _stderr_sink = open(
                os.path.join(logs_dir, "stderr.log"), "a", encoding="utf-8",
                buffering=1,
            )
            sys.stderr = _stderr_sink
        except Exception:  # pragma: no cover
            pass

    crash_logger = get_logger("crash")
    _previous_hook = sys.excepthook

    def _sys_hook(exc_type, exc_value, exc_tb):
        try:
            if not issubclass(exc_type, KeyboardInterrupt):
                crash_logger.critical(
                    "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
                )
        finally:
            _previous_hook(exc_type, exc_value, exc_tb)

    def _thread_hook(args):
        if args.exc_type is SystemExit:
            return
        crash_logger.critical(
            "Uncaught exception in thread %s (%s)",
            args.thread.name if args.thread else "?",
            args.thread.ident if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    try:
        sys.excepthook = _sys_hook
        threading.excepthook = _thread_hook
    except Exception:  # pragma: no cover
        pass
    return crash_path
