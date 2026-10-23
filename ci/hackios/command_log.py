"""Standalone command timing and bounded HackiOS build diagnostics.

Timing records contain sanitized argv, never the environment. A timeout returns
124 (or raises RuntimeError), after killing and waiting for the POSIX process
group. Failure collections have a separate directory and a relative-path
manifest; callers can publish the returned ``manifest`` under ``evidence``.
"""

from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time


TAIL_BYTES = 12000
TIMEOUT_RETURNCODE = 124
MAX_FILE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_LOG_FILES = 48
MAX_SCAN_ENTRIES = 4096
MAX_SCAN_DIRECTORIES = 128
MAX_SCAN_DEPTH = 8
MAX_PATH_LENGTH = 1024
MAX_SKIPPED = 64
TRUNCATION_MARKER = b"[truncated: only the bounded log tail follows]\n"

_PRINT_LOCK = threading.Lock()
_TIMING_LOCK = threading.Lock()
_SECRET_NAME = (
    r"(?:token|passw(?:or)?d|passwd|secret|api[-_]?key|authorization|"
    r"credential|cookie|private[-_]?key|access[-_]?key)"
)
_SECRET_FLAG = re.compile(_SECRET_NAME, re.I)
_HEADER = re.compile(
    r"((?:proxy-)?authorization|(?:set-)?cookie|x-api-key)\s*:\s*[^\r\n]+", re.I
)
_ASSIGNMENT = re.compile(
    r"(?<![\w.-])([\w.-]*" + _SECRET_NAME + r"[\w.-]*\s*(?:=|:)\s*)"
    r"""(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s&,;]+)""",
    re.I,
)
_OPTION = re.compile(
    r"(--?[\w.-]*" + _SECRET_NAME + r"[\w.-]*\s+)"
    r"""(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s]+)""",
    re.I,
)
_URL_USER = re.compile(r"(https?://)[^/\s@]+@", re.I)
_ENV_LINE = re.compile(r"(?m)^((?:export\s+)?[A-Z_][A-Z0-9_]*=)[^\r\n]*")
_UNSAFE_NAME = re.compile(
    r"(?:^|[._-])(?:env|environment|certificates?|certs?|credentials?|"
    r"secrets?|private|keys?|tokens?|passwords?|cookies?)(?:$|[._-])", re.I
)
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _redact(text, secrets=()):
    for value in sorted(set(secrets), key=len, reverse=True):
        if value:
            text = text.replace(value, "[REDACTED]")
    text = _HEADER.sub(lambda match: match[1] + ": [REDACTED]", text)
    text = _ASSIGNMENT.sub(lambda match: match[1] + "[REDACTED]", text)
    text = _OPTION.sub(lambda match: match[1] + "[REDACTED]", text)
    text = _URL_USER.sub(r"\1[REDACTED]@", text)
    return _ENV_LINE.sub(r"\1[REDACTED]", text)


def _safe_command(args, env):
    safe, secrets = [], []
    hide_next = False
    for arg in args:
        if hide_next:
            safe.append("[REDACTED]")
            secrets.append(arg)
            hide_next = False
            continue
        name, separator, value = arg.partition("=")
        if _SECRET_FLAG.search(name) and (name.startswith("-") or separator):
            safe.append(_redact(name) + "=[REDACTED]" if separator else name)
            if separator:
                secrets.append(value)
            else:
                hide_next = True
        elif arg in ("-u", "--user", "--proxy-user"):
            safe.append(arg)
            hide_next = True
        else:
            safe.append(_redact(arg))
            if _HEADER.search(arg):
                secrets.append(arg.partition(":")[2].strip())
    for name, value in (os.environ if env is None else env).items():
        if _SECRET_FLAG.search(str(name)) and value:
            secrets.append(str(value))
    return safe, secrets


def _print(*args):
    with _PRINT_LOCK:
        print(*args, flush=True)


def _append_timing(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, ensure_ascii=True) + "\n").encode("utf-8")
    with _TIMING_LOCK, path.open("ab") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            stream.write(data)
            stream.flush()
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _save_output(source, target, secrets):
    # Spool anonymously so neither argv secrets nor headers reach the saved log.
    source.seek(0)
    with target.open("w", encoding="utf-8") as stream:
        for line in source:
            stream.write(_redact(line.decode("utf-8", errors="replace"), secrets))


def run_command(args, *, cwd=None, env=None, log=None, allow_failure=False,
                timeout=None, timing_path=None) -> int:
    """Run argv without a shell; retain terminal output when log is None.

    Logs combine stdout/stderr and redact credentials. The saved log is
    finalized when the command exits. Timing JSONL uses ``command`` (argv),
    ``cwd``, ``start``, ``end``, ``elapsed_seconds``, ``returncode``, ``timed_out``.
    Spawn failures retain their OSError and record a null returncode.
    """
    if isinstance(args, (str, bytes)):
        raise TypeError("args must be an argv sequence, not a shell string")
    args = [str(arg) for arg in args]
    if not args:
        raise ValueError("args must not be empty")
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be positive")
    safe_args, secrets = _safe_command(args, env)
    _print("RUN", " ".join(safe_args))
    start = datetime.now(timezone.utc)
    clock = time.monotonic()
    returncode, timed_out = None, False
    log_path = Path(log) if log is not None else None
    try:
        with ExitStack() as stack:
            output = None
            if log_path is not None:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                output = stack.enter_context(tempfile.TemporaryFile())
            process = subprocess.Popen(
                args, cwd=cwd, env=env, stdout=output,
                stderr=subprocess.STDOUT if output is not None else None,
                start_new_session=timeout is not None,
            )
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                # SIGKILL also handles children that ignore or trap SIGTERM.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                returncode = TIMEOUT_RETURNCODE
            finally:
                if output is not None:
                    _save_output(output, log_path, secrets)
    finally:
        end = datetime.now(timezone.utc)
        if timing_path is not None:
            _append_timing(timing_path, {
                "command": safe_args,
                "cwd": _redact(str(Path(cwd or Path.cwd()).absolute()), secrets),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "elapsed_seconds": time.monotonic() - clock,
                "returncode": returncode,
                "timed_out": timed_out,
            })
    if returncode and log_path is not None:
        with log_path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - TAIL_BYTES))
            _print(stream.read(TAIL_BYTES).decode("utf-8", errors="replace"))
    if returncode and not allow_failure:
        reason = f"timeout after {timeout}s" if timed_out else f"exit {returncode}"
        raise RuntimeError(f"{reason}: {' '.join(safe_args)}")
    return returncode


def _text_log(data):
    if b"\0" in data:
        return False
    if any(byte < 9 or 13 < byte < 32 for byte in data):
        return False
    upper = data.upper()
    return not any(marker in upper for marker in (
        b"PRIVATE KEY-----", b"BEGIN CERTIFICATE", b"BEGIN OPENSSH",
        b"!<ARCH>\n",
    ))


def _log_name(name):
    lower = name.lower()
    return not _UNSAFE_NAME.search(name) and (
        lower.endswith((".log", ".err", ".errors"))
        or lower in ("errors", "warnings", "build.txt", "compile.txt",
                     "error.txt", "errors.txt", "stdout.txt", "stderr.txt")
    )


def collect_failure_logs(work: Path, evidence: Path) -> dict:
    """Copy bounded text diagnostics, without following source symlinks.

    Only kernel/xnu-deps/*.log and kernel/xnu-src/xnu-*/BUILD are searched.
    MAX_TOTAL_BYTES includes a reserved, independently bounded JSON manifest.
    Missing files/directories are normal; skipped paths and scan limits are
    recorded. Copies preserve workspace-relative paths and never replace logs.
    """
    work, evidence = Path(work).resolve(), Path(evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix="failure-logs-", dir=evidence))
    report = {
        "files": [], "skipped": [], "total_bytes": 0,
        "limits": {
            "file_bytes": MAX_FILE_BYTES, "total_bytes": MAX_TOTAL_BYTES,
            "files": MAX_LOG_FILES, "scan_entries": MAX_SCAN_ENTRIES,
            "scan_directories": MAX_SCAN_DIRECTORIES, "scan_depth": MAX_SCAN_DEPTH,
        },
        "scan_limited": False,
        "manifest": str((destination / "manifest.json").relative_to(evidence)),
    }
    scanned, directories = 0, 0
    budget = max(0, MAX_TOTAL_BYTES - MAX_MANIFEST_BYTES)

    def skip(relative, reason):
        if len(report["skipped"]) < MAX_SKIPPED:
            report["skipped"].append({"source": _redact(str(relative)), "reason": reason})

    def copy_log(parent, name, relative):
        nonlocal budget
        if len(report["files"]) >= MAX_LOG_FILES or budget <= len(TRUNCATION_MARKER):
            report["scan_limited"] = True
            return
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as source:
                metadata = os.fstat(source.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    skip(relative, "not a single-link regular file")
                    return
                prefix = source.read(4096)
                if not _text_log(prefix):
                    skip(relative, "binary or credential material")
                    return
                limit = min(MAX_FILE_BYTES, budget)
                truncated = metadata.st_size > limit
                content_limit = limit - len(TRUNCATION_MARKER) if truncated else limit
                source.seek(max(0, metadata.st_size - content_limit))
                data = source.read(content_limit)
            if not _text_log(data):
                skip(relative, "binary or credential material")
                return
            data = _redact(data.decode("utf-8", errors="replace")).encode("utf-8")
            if len(data) > content_limit:
                data = data[-content_limit:]
                truncated = True
            if truncated:
                # Always reserve the marker even when replacement text expanded.
                data = TRUNCATION_MARKER + data[-(limit - len(TRUNCATION_MARKER)):]
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(data)
            copied = len(data)
            budget -= copied
            report["total_bytes"] += copied
            report["files"].append({
                "source": str(relative),
                "destination": str(target.relative_to(evidence)),
                "original_bytes": metadata.st_size,
                "copied_bytes": copied,
                "truncated": truncated,
            })
        except OSError:
            skip(relative, "unreadable, missing, or symlink")

    def scan(parent, relative, depth, mode):
        nonlocal scanned, directories
        if directories >= MAX_SCAN_DIRECTORIES:
            report["scan_limited"] = True
            return
        directories += 1
        try:
            with os.scandir(parent) as entries:
                for entry in entries:
                    if scanned >= MAX_SCAN_ENTRIES:
                        report["scan_limited"] = True
                        return
                    scanned += 1
                    child = relative / entry.name
                    if len(str(child)) > MAX_PATH_LENGTH:
                        skip(relative, "path length limit")
                        continue
                    if entry.is_symlink():
                        skip(child, "symlink")
                        continue
                    if _UNSAFE_NAME.search(entry.name) or _SECRET_FLAG.search(entry.name):
                        skip(child, "sensitive name")
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth >= MAX_SCAN_DEPTH:
                            report["scan_limited"] = True
                            continue
                        next_mode = None
                        if mode == "versions" and entry.name.startswith("xnu-"):
                            next_mode = "version"
                        elif mode == "version" and entry.name == "BUILD":
                            next_mode = "build"
                        elif mode == "build" and not entry.name.startswith("."):
                            next_mode = "build"
                        if next_mode is not None:
                            try:
                                fd = os.open(entry.name, _DIR_FLAGS, dir_fd=parent)
                                try:
                                    scan(fd, child, depth + 1, next_mode)
                                finally:
                                    os.close(fd)
                            except OSError:
                                skip(child, "unreadable, missing, or symlink")
                    elif (mode == "deps" and entry.name.endswith(".log")
                          or mode == "build" and _log_name(entry.name)):
                        copy_log(parent, entry.name, child)
        except OSError:
            skip(relative, "unreadable or missing directory")

    try:
        root = os.open(work, _DIR_FLAGS)
        try:
            kernel = os.open("kernel", _DIR_FLAGS, dir_fd=root)
            try:
                for name, mode in (("xnu-deps", "deps"), ("xnu-src", "versions")):
                    try:
                        fd = os.open(name, _DIR_FLAGS, dir_fd=kernel)
                        try:
                            scan(fd, Path("kernel") / name, 0, mode)
                        finally:
                            os.close(fd)
                    except OSError:
                        skip(Path("kernel") / name, "unreadable, missing, or symlink")
            finally:
                os.close(kernel)
        finally:
            os.close(root)
    except OSError:
        pass
    manifest = destination / "manifest.json"
    encoded = (json.dumps(report, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise RuntimeError("failure log manifest exceeds its reserved budget")
    with manifest.open("xb") as stream:
        stream.write(encoded)
    return report
