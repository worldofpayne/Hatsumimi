"""Subtitle file helpers: types, archives and text encodings."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePosixPath

SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".vtt"}
ARCHIVE_EXTS = {".zip", ".7z", ".rar"}


def ext_of(name: str) -> str:
    return PurePosixPath(name).suffix.lower()


def is_subtitle(name: str) -> bool:
    return ext_of(name) in SUBTITLE_EXTS


def is_archive(name: str) -> bool:
    return ext_of(name) in ARCHIVE_EXTS


def is_image_sub_pack(name: str) -> bool:
    """Packs like 'Show 01 [BDRip].sup.7z' hold image (PGS) subtitles - no text."""
    return bool(re.search(r"\.(sup|sub|idx)\.(7z|zip|rar)$", name, re.IGNORECASE))


def archive_members(name: str, data: bytes) -> dict[str, bytes]:
    """Subtitle files inside a .zip or .7z, keyed by base filename."""
    ext = ext_of(name)
    if ext == ".zip":
        return zip_members(data)
    if ext == ".7z":
        return sevenzip_members(data)
    return {}  # .rar would need the external unrar tool


MAX_SUB_BYTES = 20 * 1024 * 1024  # no real subtitle file is anywhere near this


def sevenzip_members(data: bytes) -> dict[str, bytes]:
    import py7zr  # imported lazily: only needed for .7z packs
    from py7zr.io import BytesIOFactory

    out: dict[str, bytes] = {}
    with py7zr.SevenZipFile(io.BytesIO(data)) as z:
        wanted = [n for n in z.getnames() if is_subtitle(n)]
        if not wanted:
            return out
        factory = BytesIOFactory(MAX_SUB_BYTES)
        z.extract(targets=wanted, factory=factory)  # in memory, nothing on disk
        for path, product in factory.products.items():
            product.seek(0)
            out[PurePosixPath(path.replace("\\", "/")).name] = product.read()
    return out


def zip_members(data: bytes) -> dict[str, bytes]:
    """Subtitle files inside a .zip, keyed by their base filename."""
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # Old Japanese zips store names in cp932 without the UTF-8 flag;
            # Python then decodes them as cp437, producing mojibake. Undo that.
            name = info.filename
            if not info.flag_bits & 0x800:
                try:
                    name = name.encode("cp437").decode("cp932")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            base = PurePosixPath(name).name
            if is_subtitle(base):
                out[base] = zf.read(info)
    return out


def to_utf8_text(data: bytes) -> str:
    """Decode subtitle bytes whatever their encoding, with \\n line endings."""
    text = _decode(data)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _decode(data: bytes) -> str:
    """Decode subtitle bytes whatever their encoding.

    Japanese subtitles are commonly UTF-8, Shift-JIS (cp932) or UTF-16.
    Order matters: BOMs are definitive, UTF-8 is strict enough to rarely
    false-positive, while cp932 will happily "decode" many wrong inputs.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    for enc in ("utf-8", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best is not None:
            return str(best)
    except ImportError:
        pass
    return data.decode("utf-8", errors="replace")
