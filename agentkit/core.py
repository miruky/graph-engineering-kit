"""Small, dependency-free primitives shared by the four standalone kits."""
from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import tempfile
import time
import uuid

VERSION = "1.0.0"
MAX_JSON_BYTES = 8 * 1024 * 1024
ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")


class KitError(Exception):
    """An expected input, policy, or state error with a stable diagnostic code."""
    def __init__(self, message: str, code: str = "INVALID_INPUT"):
        super().__init__(message)
        self.code = code


def require(condition, message, code="INVALID_INPUT"):
    if not condition:
        raise KitError(message, code)


def object_fields(value, required=(), optional=(), where="configuration"):
    require(isinstance(value, dict), f"{where}: expected an object")
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    require(not missing, f"{where}: missing fields {sorted(missing)}")
    require(not unknown, f"{where}: unknown fields {sorted(unknown)}")
    return value


def number(value, where, minimum=0, maximum=86400, integer=False):
    require(type(value) in ((int,) if integer else (int, float)), f"{where}: invalid number")
    require(math.isfinite(value) and minimum <= value <= maximum,
            f"{where}: must be between {minimum} and {maximum}")
    return value


def strings(value, where, allow_empty=True):
    require(isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value),
            f"{where}: expected a list of nonempty strings")
    require(allow_empty or value, f"{where}: must not be empty")
    require(len(value) == len(set(value)), f"{where}: duplicate entries")
    return value


def identifier(value, where="id"):
    require(isinstance(value, str) and bool(ID.fullmatch(value)), f"{where}: invalid identifier")
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest_bytes(value: bytes):
    return hashlib.sha256(value).hexdigest()


def digest(value):
    return digest_bytes(canonical(value).encode("utf-8"))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def decode_json(value):
    def invalid_constant(value):
        raise KitError(f"Non-finite JSON constant: {value}")
    try:
        return json.loads(value, object_pairs_hook=_pairs, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise KitError(f"Invalid JSON: {type(exc).__name__}") from exc


def relative_name(value, *, glob=False):
    require(isinstance(value, str) and value and len(value) <= 1024, "Expected a relative path")
    require(not any(ord(x) < 32 for x in value), "Control character in path", "UNSAFE_PATH")
    require(not any(x in value for x in ("\\", ":", "\x00")), "Non-portable path", "UNSAFE_PATH")
    parts = value.split("/")
    require(not value.startswith("/") and all(p not in ("", ".", "..") for p in parts),
            "Path traversal or empty path component", "UNSAFE_PATH")
    require(not any(p.endswith((".", " ")) for p in parts), "Ambiguous path component", "UNSAFE_PATH")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    require(not any(p.split(".")[0].upper() in reserved for p in parts),
            "Reserved device path", "UNSAFE_PATH")
    if not glob:
        require(not any(x in value for x in "*?"), "Wildcard in literal path", "UNSAFE_PATH")
    else:
        require(not any(x in value for x in "{}"), "Only *, ** and ? glob syntax is supported; brackets are literal")
    return value


def _link_like(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def confined(root, relative, *, exists=False):
    root = Path(root).resolve()
    require(root.is_dir(), "Project root must be an existing directory")
    relative_name(relative)
    cursor = root
    for part in relative.split("/"):
        cursor = cursor / part
        require(not _link_like(cursor), f"Link/reparse point refused: {relative}", "UNSAFE_PATH")
    require(cursor.is_relative_to(root), "Path escapes project", "UNSAFE_PATH")
    if exists:
        require(cursor.exists(), f"Missing file: {relative}", "MISSING_FILE")
    return cursor


def read_bytes(root, relative, *, limit=MAX_JSON_BYTES):
    path = confined(root, relative, exists=True)
    require(path.is_file(), f"Not a regular file: {relative}", "UNSAFE_PATH")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode), "Only regular files are readable", "UNSAFE_PATH")
            require(info.st_size <= limit, f"File exceeds {limit} bytes: {relative}", "SIZE_LIMIT")
            data = stream.read(limit + 1)
            require(len(data) <= limit, f"File grew past limit: {relative}", "SIZE_LIMIT")
            return data
    except OSError as exc:
        raise KitError(f"Cannot read {relative}: {exc.__class__.__name__}", "IO_ERROR") from exc


def text(root, relative, *, limit=MAX_JSON_BYTES):
    data = read_bytes(root, relative, limit=limit)
    require(b"\x00" not in data, f"Binary content refused: {relative}")
    try:
        return data.decode("utf-8")
    except UnicodeError as exc:
        raise KitError(f"Expected UTF-8 text: {relative}") from exc


def load_json(root, relative):
    return decode_json(read_bytes(root, relative))


def atomic_write(root, relative, data: bytes, *, exclusive=False):
    path = confined(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    confined(root, relative)
    fd, tmp = tempfile.mkstemp(prefix=".agentkit-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            # link() gives no-clobber publication; unlike exists()+replace(), it is atomic.
            try:
                os.link(tmp, path)
            except FileExistsError as exc:
                raise KitError(f"Refusing to overwrite {relative}", "ALREADY_EXISTS") from exc
            os.unlink(tmp)
        else:
            os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def write_json(root, relative, value, *, exclusive=False):
    atomic_write(root, relative, (json.dumps(value, ensure_ascii=False, indent=2,
                                           allow_nan=False) + "\n").encode(), exclusive=exclusive)


def file_hash(root, relative):
    # Large binaries can be traced without loading them into the model or memory.
    path = confined(root, relative, exists=True)
    require(path.is_file(), f"Not a regular file: {relative}", "UNSAFE_PATH")
    h = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "Not a regular file", "UNSAFE_PATH")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _glob_regex(pattern):
    relative_name(pattern, glob=True)
    result, pos = "^", 0
    while pos < len(pattern):
        if pattern[pos:pos + 3] == "**/":
            result += "(?:.*/)?"
            pos += 3
        elif pattern[pos:pos + 2] == "**":
            result += ".*"
            pos += 2
        elif pattern[pos] == "*":
            result += "[^/]*"
            pos += 1
        elif pattern[pos] == "?":
            result += "[^/]"
            pos += 1
        else:
            result += re.escape(pattern[pos])
            pos += 1
    return re.compile(result + "$")


def expand(root, patterns, *, exclude=(), required=True, max_files=20000):
    strings(patterns, "file patterns", allow_empty=not required)
    strings(list(exclude), "exclude patterns")
    matchers = [_glob_regex(p) for p in patterns]
    exclusions = [_glob_regex(p) for p in exclude]
    found, counts, scanned = set(), [0] * len(patterns), 0
    scan_roots = []
    for pattern in patterns:
        stop = min([pattern.find(c) for c in "*?" if c in pattern] or [len(pattern)])
        fixed = pattern[:stop]
        scan_roots.append(fixed.rsplit("/", 1)[0] if "/" in fixed else "")
    def relevant(directory):
        return any(not prefix or directory == prefix or directory.startswith(prefix + "/")
                   or prefix.startswith(directory + "/") for prefix in scan_roots)
    root = Path(root).resolve()
    for directory, dirs, files in os.walk(root, followlinks=False):
        rel_dir = Path(directory).relative_to(root).as_posix()
        dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__")
                         and relevant((Path(directory) / d).relative_to(root).as_posix()) and not (
            (Path(directory) / d).relative_to(root).as_posix().startswith(
                (".agentkit/state", ".agentkit/output", ".agentkit/tools"))))
        for name in sorted(dirs + files):
            rel = name if rel_dir == "." else rel_dir + "/" + name
            if any(p.fullmatch(rel) for p in exclusions):
                if name in dirs:
                    dirs.remove(name)
                continue
            if _link_like(Path(directory) / name):
                if any(p.fullmatch(rel) for p in matchers):
                    raise KitError(f"Selected link/reparse point: {rel}", "UNSAFE_PATH")
                if name in dirs:
                    dirs.remove(name)
                continue
            if name not in files:
                continue
            scanned += 1
            require(scanned <= max_files, "Project inventory exceeds file limit", "SIZE_LIMIT")
            for index, matcher in enumerate(matchers):
                if matcher.fullmatch(rel):
                    require(Path(directory, name).is_file(), "Non-regular file", "UNSAFE_PATH")
                    relative_name(rel)
                    found.add(rel)
                    counts[index] += 1
    if required:
        require(all(counts), "Patterns matched no files: " + ", ".join(
            pattern for pattern, count in zip(patterns, counts) if not count), "MISSING_FILE")
    return sorted(found)


def snapshot(root, patterns, *, required=True):
    return {p: file_hash(root, p) for p in expand(root, patterns, required=required)}


def state_path(kind, name="state"):
    identifier(kind)
    identifier(name)
    return f".agentkit/state/{kind}/{name}.json"


def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class ProjectLock:
    """Single local writer; never silently steals a lock left by a killed process."""
    def __init__(self, root, kind):
        self.root, self.kind = Path(root).resolve(), identifier(kind)
        self.relative = f".agentkit/state/{kind}/writer.lock"
        self.token = uuid.uuid4().hex
        self.acquired = False

    def __enter__(self):
        path = confined(self.root, self.relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        confined(self.root, self.relative)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise KitError("Another writer or an interrupted run holds the lock. "
                           "Inspect it; use unlock with its exact token only after that process stops.",
                           "LOCKED") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"token": self.token, "pid": os.getpid(), "host": socket.gethostname(),
                       "created_at": now()}, stream)
        self.acquired = True
        return self

    def __exit__(self, *args):
        if self.acquired:
            data = load_json(self.root, self.relative)
            if data.get("token") == self.token:
                confined(self.root, self.relative).unlink()


def unlock(root, kind, token):
    path = f".agentkit/state/{identifier(kind)}/writer.lock"
    owner = load_json(root, path)
    require(owner.get("token") == token, "Lock token does not match", "LOCKED")
    require(owner.get("host") == socket.gethostname(), "Lock belongs to another host", "LOCKED")
    pid = number(owner.get("pid"), "lock pid", 1, 2**31, integer=True)
    alive = process_alive(pid)
    require(not alive, "Lock owner still exists; refusing to unlock", "LOCKED")
    require(load_json(root, path).get("token") == token, "Lock changed", "LOCKED")
    confined(root, path).unlink()
    return {"unlocked": True}


def process_alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE; no terminate permission.
        if not handle:
            return ctypes.get_last_error() != 87  # Access denied is not proof of absence.
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?im)(?:api[_-]?key|access[_-]?token|password|secret)[\"']?\s*[=:]\s*[\"']?[^\s\"']{12,}"),
]


def contains_secret(value):
    return any(p.search(value) for p in SECRET_PATTERNS)


def redact(value, root=None, secrets=()):
    for secret in secrets:
        if secret and len(secret) >= 6:
            value = value.replace(secret, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    if root:
        for spelling in sorted({str(root), str(Path(root).absolute()), str(Path(root).resolve())}, key=len, reverse=True):
            value = value.replace(spelling, "<project>")
            value = value.replace(json.dumps(spelling)[1:-1], "<project>")
    for spelling in {str(Path.home()), str(Path.home().resolve())}:
        value = value.replace(spelling, "<home>")
        value = value.replace(json.dumps(spelling)[1:-1], "<home>")
    return value


def check_fingerprint(stored, current, what="inputs"):
    require(stored == current, f"{what} changed; previous evidence is stale", "STALE_INPUTS")
