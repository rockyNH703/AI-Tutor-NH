"""Profile photos: validated, resized and re-encoded on the server.

A client uploads the original file (JPEG, PNG or WebP, up to AVATAR_MAX_BYTES). Nothing it
sends is stored as sent:

- the declared type must match what the bytes actually are;
- the pixel count is checked from the header BEFORE the image is decoded, so a small file
  that would decode to gigabytes (a decompression bomb) is refused cheaply;
- the image is turned upright (EXIF orientation), cropped to a centred square, scaled to
  AVATAR_EDGE pixels and written as a new WebP. Writing a new file drops every piece of
  metadata the original carried, including camera GPS coordinates;
- the result is at most 64 KB (the database CHECK), and its SHA-256 names it, which is what
  makes cached copies safe (app/auth/router.py serves `?v=<hash>` as immutable).

Decoding is CPU work: it runs in a worker thread, and at most AVATAR_DECODES of them run at
once (a semaphore the app creates, `app.state.avatar_decodes`), so a burst of uploads cannot
starve the event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.errors import PayloadTooLarge, UnsupportedMediaType, ValidationFailed

# Declared content type -> the format Pillow must detect in the bytes.
ACCEPTED = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
AVATAR_EDGE = 256
MIN_EDGE = 32
MAX_PIXELS = 40_000_000  # e.g. 8000 x 5000; phone photos are well below
# What may be decoded at full size: PNG and WebP always are, a JPEG only after draft() (which can
# scale it down by up to 8). AVATAR_DECODES of them at once, each with its copies, must fit in the
# API's memory (MemoryMax=900M): 16 MP is 64 MB in RGBA.
MAX_DECODED_PIXELS = 16_000_000
MAX_STORED_BYTES = 65_536
AVATAR_DECODES = 2
STORED_TYPE = "image/webp"


@dataclass(frozen=True)
class ProcessedAvatar:
    image: bytes
    content_type: str
    hash: str  # 32 hex characters of the SHA-256 of `image`


def _reject(reason: str, message: str) -> ValidationFailed:
    return ValidationFailed(message, fields=[{"field": "image", "reason": reason}])


def process(data: bytes, declared_type: str, max_bytes: int) -> ProcessedAvatar:
    """Validate and re-encode one upload. Pure and synchronous; call through `process_async`."""
    expected = ACCEPTED.get((declared_type or "").split(";")[0].strip().lower())
    if expected is None:
        raise UnsupportedMediaType("Upload a JPEG, PNG or WebP image.")
    if len(data) > max_bytes:
        raise PayloadTooLarge(f"The image must be at most {max_bytes // (1024 * 1024)} MB.")
    if not data:
        raise _reject("empty", "The file is empty.")

    detected_format = None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        detected_format = "PNG"
    elif data.startswith(b"\xff\xd8\xff"):
        detected_format = "JPEG"
    elif len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        detected_format = "WEBP"
    if detected_format is not None and detected_format != expected:
        raise _reject("type_mismatch", "The file is not the kind of image it claims to be.")

    with warnings.catch_warnings():
        # Pillow warns (rather than refuses) between MAX_IMAGE_PIXELS and twice that. Refuse.
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            # Restrict plugin probing and decoding to the media type already accepted at the HTTP boundary.
            with Image.open(io.BytesIO(data), formats=(expected,)) as source:
                if source.format != expected:
                    raise _reject("type_mismatch", "The file is not the kind of image it claims to be.")
                width, height = source.size
                if width * height > MAX_PIXELS:
                    raise _reject("too_many_pixels", "The image is too large. Use a smaller photo.")
                if min(width, height) < MIN_EDGE:
                    raise _reject("too_small", f"The image must be at least {MIN_EDGE} pixels on each side.")
                if expected == "JPEG":
                    # Decode at a reduced scale when the file allows it: less memory, same result.
                    source.draft("RGB", (AVATAR_EDGE * 2, AVATAR_EDGE * 2))
                decoded_width, decoded_height = source.size  # after draft(): what decoding will allocate
                if decoded_width * decoded_height > MAX_DECODED_PIXELS:
                    raise _reject("too_many_pixels", "The image is too large. Use a smaller photo.")
                source.seek(0)  # animated images: the first frame
                upright = ImageOps.exif_transpose(source)
                has_alpha = upright.mode in ("RGBA", "LA") or (upright.mode == "P" and "transparency" in upright.info)
                square = ImageOps.fit(
                    upright.convert("RGBA" if has_alpha else "RGB"),
                    (AVATAR_EDGE, AVATAR_EDGE),
                    method=Image.Resampling.LANCZOS,
                )
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise _reject("too_many_pixels", "The image is too large. Use a smaller photo.") from error
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
            raise _reject("unreadable", "The file could not be read as an image.") from error

    for quality in (82, 70, 55):
        out = io.BytesIO()
        square.save(out, "WEBP", quality=quality, method=4)  # a new file: no EXIF, no XMP, no GPS
        encoded = out.getvalue()
        if len(encoded) <= MAX_STORED_BYTES:
            return ProcessedAvatar(encoded, STORED_TYPE, hashlib.sha256(encoded).hexdigest()[:32])
    raise _reject("unreadable", "The image could not be compressed enough.")  # pragma: no cover - 256 px WebP


async def process_async(data: bytes, declared_type: str, max_bytes: int, decodes: asyncio.Semaphore) -> ProcessedAvatar:
    async with decodes:
        return await asyncio.to_thread(process, data, declared_type, max_bytes)
