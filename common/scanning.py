"""Upload inspection: one function, inspect_upload(file).

It reads the bytes first and the scanner second, and returns:
  sha256        hex digest of the whole file
  size          bytes
  sniffed_type  the type read from the file's own bytes, never from its name
                or what the browser said
  scan_status   "clean", "infected", or "pending" when no scanner is set up

The signature is shared with quality and the vault. Do not change it without
both owners agreeing.

Scanner. The virus scanner choice (task E4) is still open. When CLAMAV_HOST
is set, the file is streamed to a clamd daemon with the INSTREAM command.
When it is not set, scan_status is "pending": nothing is claimed clean that
was not scanned.
"""
import hashlib
import socket
import struct

from django.conf import settings

CHUNK = 64 * 1024

# Magic bytes, most specific first.
_SIGNATURES = [
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole-storage"),  # old Word and Excel
    (b"PK\x03\x04", "application/zip"),  # also docx and xlsx, told apart below
    (b"MZ", "application/x-msdownload"),  # Windows programs
    (b"\x7fELF", "application/x-executable"),  # Linux programs
]

EXECUTABLE_TYPES = {"application/x-msdownload", "application/x-executable"}


def _sniff(head, tail_names):
    for magic, kind in _SIGNATURES:
        if head.startswith(magic):
            if kind == "application/zip":
                if b"word/" in tail_names:
                    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if b"xl/" in tail_names:
                    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            return kind
    return "application/octet-stream"


def _open_chunks(file):
    if hasattr(file, "seek"):
        file.seek(0)
    if hasattr(file, "chunks"):
        yield from file.chunks(CHUNK)
    else:
        while True:
            block = file.read(CHUNK)
            if not block:
                break
            yield block
    if hasattr(file, "seek"):
        file.seek(0)


def _scan_with_clamd(chunks):
    host = getattr(settings, "CLAMAV_HOST", "")
    if not host:
        return "pending"
    port = int(getattr(settings, "CLAMAV_PORT", 3310))
    timeout = float(getattr(settings, "CLAMAV_TIMEOUT_SECONDS", 30))
    try:
        with socket.create_connection((host, port), timeout=timeout) as conn:
            conn.sendall(b"zINSTREAM\0")
            for block in chunks:
                conn.sendall(struct.pack("!L", len(block)) + block)
            conn.sendall(struct.pack("!L", 0))
            reply = conn.recv(4096).strip(b"\0").strip()
    except OSError:
        # The scanner is down: the file stays pending and is not served.
        return "pending"
    if reply.endswith(b"OK"):
        return "clean"
    if reply.endswith(b"FOUND"):
        return "infected"
    return "pending"


def inspect_upload(file):
    digest = hashlib.sha256()
    size = 0
    head = b""
    blocks = []
    for block in _open_chunks(file):
        digest.update(block)
        size += len(block)
        if len(head) < 16:
            head += block[: 16 - len(head)]
        blocks.append(block)
    # Office files are zip archives; the part names sit in the archive's
    # directory, so look for them anywhere in the bytes.
    joined = b"".join(blocks) if head.startswith(b"PK\x03\x04") else b""
    return {
        "sha256": digest.hexdigest(),
        "size": size,
        "sniffed_type": _sniff(head, joined),
        "scan_status": _scan_with_clamd(iter(blocks)),
    }


# Types a declared file extension may be. Used to refuse a file whose bytes
# do not match its name.
EXTENSION_TYPES = {
    "pdf": {"application/pdf"},
    "png": {"image/png"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "doc": {"application/x-ole-storage"},
    "xls": {"application/x-ole-storage"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
}


def disguised(filename, sniffed_type):
    """True when the bytes are a program, or a known type other than the one
    the name claims. Bytes of no known type are not called disguised."""
    if sniffed_type in EXECUTABLE_TYPES:
        return True
    if sniffed_type == "application/octet-stream":
        return False
    extension = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if extension in {"zip"}:
        return sniffed_type != "application/zip" and not sniffed_type.startswith("application/vnd.openxml")
    allowed = EXTENSION_TYPES.get(extension)
    return allowed is not None and sniffed_type not in allowed


def matches_extension(filename, sniffed_type):
    extension = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    allowed = EXTENSION_TYPES.get(extension)
    return bool(allowed) and sniffed_type in allowed
