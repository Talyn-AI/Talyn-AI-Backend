"""Malware scanning via ClamAV's `clamd` protocol, over a plain socket.

Why ClamAV: it is the standard open-source scanner, runs happily in a
container beside the app, and `clamd` speaks a trivial line protocol — so
this needs no Python client library and no extra dependency to keep current.

What "scanning" can and cannot promise, stated plainly:

  * ClamAV is signature-based. It catches known malware well and novel
    threats not at all. It is one layer, not a guarantee.
  * The `INSTREAM` command reads exactly `size` bytes. Sending less than
    that hangs the daemon, so the declared size must be exact — hence the
    guard below that refuses rather than guessing.
  * Every outcome is recorded, including "scanner unavailable". An asset
    that was never looked at should not look identical to one that was
    found clean.

Nothing here raises into request handling. The caller decides whether an
unavailable scanner blocks uploads (see `upload_scanning_required`).
"""
import logging
import socket
from enum import Enum

from app import config as config_module

log = logging.getLogger("talyn.scan")


class Verdict(str, Enum):
    CLEAN = "clean"
    INFECTED = "infected"
    ERROR = "error"

    # Not a verdict on the file — a statement about the scanner.
    UNAVAILABLE = "unavailable"


# clamd's chunked INSTREAM framing.
CHUNK_SIZE = 64 * 1024
CONNECT_TIMEOUT = 5.0
SCAN_TIMEOUT = 120.0

# clamd caps a single stream at StreamMaxLength (default 25 MB). Anything
# larger needs a different approach (scan a prefix, or rely on size caps).
DEFAULT_STREAM_MAX = 25 * 1024 * 1024


def available() -> bool:
    return config_module.settings.scanner_enabled


def scan_bytes(data: bytes) -> tuple[Verdict, str]:
    """Scan an in-memory file. Returns (verdict, detail)."""
    if not available():
        return Verdict.UNAVAILABLE, "no scanner configured"
    if len(data) > DEFAULT_STREAM_MAX:
        # Truncating silently would understate the risk, so say so and let
        # the caller decide. Video is capped far above this in practice only
        # if the admin raises the limit knowingly.
        return (
            Verdict.ERROR,
            f"file exceeds the {DEFAULT_STREAM_MAX // (1024 * 1024)}MB scan limit",
        )
    return _send(data, len(data))


def scan_file(path: str) -> tuple[Verdict, str]:
    """Scan a file already on local disk (used by the orphan sweep)."""
    import os

    try:
        size = os.path.getsize(path)
    except OSError as e:
        return Verdict.ERROR, f"could not stat file: {e}"
    if size > DEFAULT_STREAM_MAX:
        return (
            Verdict.ERROR,
            f"file exceeds the {DEFAULT_STREAM_MAX // (1024 * 1024)}MB scan limit",
        )
    try:
        with open(path, "rb") as handle:
            return _send_stream(handle, size)
    except OSError as e:
        return Verdict.ERROR, f"could not read file: {e}"


def _send(data: bytes, size: int) -> tuple[Verdict, str]:
    import io

    return _send_stream(io.BytesIO(data), size)


def _send_stream(handle, size: int) -> tuple[Verdict, str]:
    """Speak INSTREAM: a size header, then size bytes, then a zero chunk."""
    settings = config_module.settings
    try:
        with socket.create_connection(
            (settings.clamav_host, settings.clamav_port),
            timeout=CONNECT_TIMEOUT,
        ) as sock:
            sock.settimeout(SCAN_TIMEOUT)
            sock.sendall(b"zINSTREAM\0")

            remaining = size
            while remaining > 0:
                chunk = handle.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    # Short read. Abort rather than leave clamd waiting on a
                    # stream that will never reach its declared length.
                    return Verdict.ERROR, "file shrank while being scanned"
                sock.sendall(b"%d\r\n" % len(chunk))
                sock.sendall(chunk)
                remaining -= len(chunk)

            sock.sendall(b"0\r\n")
            # Reply is either "<name>: <verdict> FOUND\0" or "stream: OK\0".
            reply = b""
            while not reply.endswith(b"\0"):
                block = sock.recv(4096)
                if not block:
                    break
                reply += block
    except socket.timeout:
        return Verdict.UNAVAILABLE, "scanner timed out"
    except OSError as e:
        return Verdict.UNAVAILABLE, f"scanner unreachable: {e}"

    return _parse(reply.decode("utf-8", "replace").strip("\0").strip())


def _parse(reply: str) -> tuple[Verdict, str]:
    if not reply:
        return Verdict.ERROR, "empty reply from scanner"
    if reply.endswith("FOUND"):
        # clamd answers "<path>: <SIGNATURE> FOUND" — e.g.
        # "stream: Win.Test.EICAR_HDB-1 FOUND". The signature is the useful
        # part; the path before the colon is just the stream name.
        after_colon = reply.split(":", 1)[1].strip() if ":" in reply else ""
        signature = after_colon.rsplit(" ", 1)[0].strip() or "malware"
        return Verdict.INFECTED, signature
    if "OK" in reply.upper():
        return Verdict.CLEAN, "no threats found"
    # "UNKNOWN COMMAND", "INSTREAM size limit exceeded", and friends.
    return Verdict.ERROR, reply
