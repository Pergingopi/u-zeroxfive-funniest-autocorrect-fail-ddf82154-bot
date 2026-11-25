#!/usr/bin/env python3
"""Funniest Autocorrect Fail — writes one single-line entry to entry.txt.

Deterministic, Python 3.13 stdlib only. The entry is written atomically:
a temp file is created with O_EXCL in the output directory, written,
fsynced, read back and verified, then moved into place with os.replace,
and the directory is fsynced so the rename is durable. The published
file is verified a final time and must carry mode 0644.

No trailing newline is written: the gate counts characters, and the
entry is exactly the text below.

Layout of the module:

* ``ENTRY`` — the single line that is submitted, nothing else.
* ``EntryError`` — the one exception type raised for any problem with
  the entry text or the output directory.
* ``validate_entry`` — every gate rule for the entry text, in one place.
* ``resolve_output_dir`` — works out where entry.txt goes and makes
  sure that directory exists (creating it when needed).
* ``atomic_write`` — the temp-file / replace / verify dance.
* ``main`` — wires the pieces together and turns errors into a
  one-line message on stderr plus exit code 1.
"""

import os
import sys
import tempfile
import time

ENTRY = "Please find the orgasm chart attached for Friday's meeting."

# How many times to retry removing a stale temp file, and how long to
# wait between attempts, in seconds.
CLEANUP_RETRIES = 3
CLEANUP_RETRY_DELAY = 0.05


class EntryError(Exception):
    """Raised when the entry text or the output directory is unusable."""


def validate_entry(text: str) -> None:
    """Check ``text`` against every rule of the competition gate.

    The gate accepts UTF-8 text of 1 to 199 characters that is not only
    whitespace. On top of that the entry must be a single line with no
    control characters, so a stray newline or tab can never slip in.

    Raises:
        EntryError: with a message naming the rule that failed.
    """
    if not isinstance(text, str):
        raise EntryError("entry must be text, not %s" % type(text).__name__)
    if not 1 <= len(text) <= 199:
        raise EntryError(
            "entry length %d is outside the gate range 1-199" % len(text)
        )
    if not text.strip():
        raise EntryError("entry is only whitespace")
    if "\n" in text or "\r" in text:
        raise EntryError("entry must be a single line")
    for ch in text:
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            raise EntryError(
                "entry contains control character U+%04X" % code
            )


def resolve_output_dir() -> str:
    """Return the directory entry.txt is written to, creating it if needed.

    ``MAKEFUN_OUTPUT_DIR`` names the directory when it is set to a
    non-empty value; a value that is empty or only whitespace is treated
    exactly as if the variable were unset, and the default ``/output``
    is used. The directory is always created with
    ``os.makedirs(..., exist_ok=True)`` when it does not already exist —
    a missing directory is never an error and the write is never
    silently redirected somewhere else.

    Raises:
        EntryError: if the directory cannot be created or is not a
            directory at all (for example a file sits at that path).
    """
    raw = os.environ.get("MAKEFUN_OUTPUT_DIR")
    out_dir = raw.strip() if raw else ""
    if not out_dir:
        out_dir = "/output"
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise EntryError(
            "cannot create output directory %r: %s" % (out_dir, exc)
        ) from exc
    if not os.path.isdir(out_dir):
        raise EntryError("output path %r is not a directory" % out_dir)
    return out_dir


def _remove_stale_temp(tmp_path: str) -> None:
    """Best-effort removal of a temp file left behind by a failed write.

    Retries a few times because a just-closed file can briefly resist
    removal on some filesystems. Every failure is swallowed: cleanup
    must never turn a successful write into a reported error, and it
    must never delete anything other than our own temp file.
    """
    for attempt in range(CLEANUP_RETRIES):
        try:
            os.unlink(tmp_path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt + 1 < CLEANUP_RETRIES:
                time.sleep(CLEANUP_RETRY_DELAY)
    # Last attempt already failed; give up quietly.


def atomic_write(out_dir: str, filename: str, data: bytes) -> str:
    """Write ``data`` to ``filename`` inside ``out_dir``, atomically.

    A temp file is created with O_EXCL, chmodded to 0644, written and
    fsynced, read back and compared byte for byte, then moved into
    place with os.replace. The directory is fsynced afterwards so the
    rename survives a crash. Returns the full path of the published
    file.

    Raises:
        EntryError: if the read-back check or the final mode check fails.
        OSError: if the filesystem itself refuses the write.
    """
    target = os.path.join(out_dir, filename)
    fd, tmp_path = tempfile.mkstemp(prefix=".entry-", suffix=".tmp", dir=out_dir)
    try:
        try:
            os.chmod(tmp_path, 0o644)
            with os.fdopen(fd, "wb") as f:
                fd = -1
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            # Read-back verification before replacing.
            with open(tmp_path, "rb") as f:
                if f.read() != data:
                    raise EntryError("read-back verification failed")
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, target)
            os.chmod(target, 0o644)
        finally:
            if fd != -1:
                os.close(fd)
    finally:
        # Remove the stale temp file when the replace did not happen;
        # never delete the output directory itself.
        _remove_stale_temp(tmp_path)

    # Fsync the directory so the rename is durable.
    try:
        dir_fd = os.open(out_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass

    return target


def verify_published(target: str, data: bytes) -> None:
    """Confirm the published file holds exactly ``data`` with mode 0644.

    Raises:
        EntryError: if the contents or the mode do not match.
    """
    with open(target, "rb") as f:
        if f.read() != data:
            raise EntryError("final verification failed")
    if (os.stat(target).st_mode & 0o777) != 0o644:
        raise EntryError("entry.txt mode is not 0644")


def main() -> int:
    try:
        validate_entry(ENTRY)
        out_dir = resolve_output_dir()
        data = ENTRY.encode("utf-8")
        target = atomic_write(out_dir, "entry.txt", data)
        verify_published(target, data)
    except EntryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print("filesystem error: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
