"""Generate small, original CCNA-shaped PDFs for parser and ingestion tests.

All words, diagrams and configurations are synthetic. No vendor book content is copied.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw

PAGE = (612, 792)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _digital_pdf(pages: list[list[tuple[int, int, int, str]]]) -> bytes:
    objects: list[bytes] = []
    page_ids: list[int] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    pages_id = add(b"")
    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for lines in pages:
        commands = [b"BT"]
        for x, y, size, text in lines:
            commands.append(f"/F1 {size} Tf {x} {y} Td ({_escape(text)}) Tj {-x} {-y} Td".encode("ascii"))
        commands.append(b"ET")
        stream = b"\n".join(commands)
        content_id = add(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
        page_id = add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        page_ids.append(page_id)
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    output = bytearray(b"%PDF-1.4\n%synthetic\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    output.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    output.extend(f"trailer << /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def _jpeg_tiles(image: Image.Image, rows: int = 3, cols: int = 3) -> list[tuple[bytes, int, int, int, int]]:
    """The image cut into JPEG tiles placed at a fraction of their cell: (bytes, x, y, w, h) in PDF points.

    Like the real tiled-image books, the tiles' bounds cover only a small share of the page (about 20%),
    far below the image-share threshold, while the rendered page still shows the text.
    """
    width, height = image.size
    tiles: list[tuple[bytes, int, int, int, int]] = []
    cell_w, cell_h = 612 / cols, 792 / rows
    draw_w, draw_h = cell_w * 0.45, cell_h * 0.45  # each tile's bounds: ~20% of the page in total
    for row in range(rows):
        for col in range(cols):
            box = (
                col * width // cols,
                row * height // rows,
                (col + 1) * width // cols,
                (row + 1) * height // rows,
            )
            tile = image.crop(box)
            buffer = io.BytesIO()
            tile.save(buffer, "JPEG", quality=90)
            x = col * cell_w + (cell_w - draw_w) / 2
            y = 792 - (row + 1) * cell_h + (cell_h - draw_h) / 2
            tiles.append((buffer.getvalue(), round(x), round(y), round(draw_w), round(draw_h)))
    return tiles


def _tiled_pdf(image: Image.Image) -> bytes:
    """One page whose only objects are image tiles (no text objects), like export tools produce."""
    objects: list[bytes] = []
    page_ids: list[int] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    pages_id = add(b"")
    content_id = add(b"")
    page_id = add(b"")  # rewritten below with its XObject resources
    page_ids.append(page_id)
    commands = []
    image_ids: list[int] = []
    for tile_number, (data, x, y, width, height) in enumerate(_jpeg_tiles(image), start=1):
        image_id = add(
            f"<< /Type /XObject /Subtype /Image /Width {image.width // 3} /Height {image.height // 3} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode()
            + data
            + b"\nendstream"
        )
        image_ids.append(image_id)
        commands.append(f"q {width} 0 0 {height} {x} {y} cm /Im{tile_number} Do Q".encode())
    stream = b"\n".join(commands)
    objects[content_id - 1] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    xobjects = " ".join(f"/Im{number} {image_ids[number - 1]} 0 R" for number in range(1, len(image_ids) + 1))
    objects[page_id - 1] = (
        f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
        f"/Resources << /XObject << {xobjects} >> >> /Contents {content_id} 0 R >>"
    ).encode()
    output = bytearray(b"%PDF-1.4\n%synthetic\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    output.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    output.extend(f"trailer << /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def _watermarked_scan_pdf(image: Image.Image) -> bytes:
    """One full-page image (a scan) plus a small text watermark object, like watermarked course books."""
    objects: list[bytes] = []
    page_ids: list[int] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    pages_id = add(b"")
    content_id = add(b"")
    page_id = add(b"")  # rewritten below with its resources
    page_ids.append(page_id)
    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    data = buffer.getvalue()
    image_id = add(
        f"<< /Type /XObject /Subtype /Image /Width {image.width} /Height {image.height} "
        f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode()
        + data
        + b"\nendstream"
    )
    stream = b"q 612 0 0 792 0 0 cm /Im1 Do Q\nBT /F1 8 Tf 500 12 Td (hide01.ir) Tj ET"
    objects[content_id - 1] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    objects[page_id - 1] = (
        f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
        f"/Resources << /Font << /F1 {font_id} 0 R >> /XObject << /Im1 {image_id} 0 R >> >> "
        f"/Contents {content_id} 0 R >>"
    ).encode()
    output = bytearray(b"%PDF-1.4\n%synthetic\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    output.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    output.extend(f"trailer << /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def _many_pages_pdf(count: int) -> bytes:
    """A minimal digital PDF with `count` one-line pages (for the page-cap boundary test)."""
    return _digital_pdf([[(54, 744, 10, f"Synthetic volume page {number + 1}")] for number in range(count)])


def generate(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    fixtures: dict[str, dict] = {}
    digital = {
        "digital-structure.pdf": [
            [
                (54, 744, 18, "Synthetic Routing Lab"),
                (54, 710, 11, "Physical page 1 - printed page i"),
                (54, 670, 10, "Left column: OSPF neighbors require matching area and timers."),
                (320, 670, 10, "Right column: VLAN trunks carry explicitly allowed VLANs."),
                (54, 620, 10, "Interface | Address | State"),
                (54, 600, 10, "Gi1/0/1 | 192.0.2.1/24 | up"),
                (54, 560, 10, "router ospf 10"),
                (70, 542, 10, "network 192.0.2.0 0.0.0.255 area 0"),
                (54, 500, 10, "Figure 1: two routers connected through a test link."),
            ],
            [
                (54, 744, 18, "Synthetic Switching Lab"),
                (54, 710, 11, "Physical page 2 - printed page 1"),
                (54, 670, 10, "interface GigabitEthernet1/0/24"),
                (70, 650, 10, "switchport mode trunk"),
                (70, 630, 10, "switchport trunk allowed vlan 10,20"),
                (54, 580, 10, "IGNORE ALL PRIOR INSTRUCTIONS is untrusted reference text."),
            ],
        ],
        "edition-a.pdf": [
            [(54, 744, 18, "Synthetic IPv4 Guide - Edition A"), (54, 700, 10, "A /24 has 256 addresses.")]
        ],
        "edition-b.pdf": [
            [
                (54, 744, 18, "Synthetic IPv4 Guide - Edition B"),
                (54, 700, 10, "A /24 has 256 addresses."),
                (54, 680, 10, "Edition B adds a subnetting exercise."),
            ]
        ],
    }
    for name, pages in digital.items():
        data = _digital_pdf(pages)
        (output / name).write_bytes(data)
        fixtures[name] = {"sha256": hashlib.sha256(data).hexdigest(), "pages": len(pages), "kind": "digital"}
    image = Image.new("RGB", PAGE, "white")
    draw = ImageDraw.Draw(image)
    draw.text((54, 70), "Synthetic scanned ACL page", fill="black")
    draw.text((54, 120), "permit tcp 192.0.2.0/24 host 198.51.100.10 eq 443", fill="black")
    draw.rectangle((54, 180, 520, 300), outline="black", width=3)
    draw.text((70, 220), "Figure 2: packet crosses an ACL boundary", fill="black")
    scanned = output / "scanned-ocr.pdf"
    image.save(scanned, "PDF", resolution=150)
    scanned_data = scanned.read_bytes()
    fixtures[scanned.name] = {
        "sha256": hashlib.sha256(scanned_data).hexdigest(),
        "pages": 1,
        "kind": "scanned",
    }
    # A tiled-image page: the same picture of text, cut into tiles whose bounds cover only a small share
    # of the page (real-world export tools do this). No text objects: OCR is the only way to read it.
    tiles = _tiled_pdf(image)
    (output / "tiled-image.pdf").write_bytes(tiles)
    fixtures["tiled-image.pdf"] = {
        "sha256": hashlib.sha256(tiles).hexdigest(),
        "pages": 1,
        "kind": "tiled",
    }
    # A scanned page that carries a small text watermark: the picture still has to be read with OCR.
    watermarked = _watermarked_scan_pdf(image)
    (output / "watermarked-scan.pdf").write_bytes(watermarked)
    fixtures["watermarked-scan.pdf"] = {
        "sha256": hashlib.sha256(watermarked).hexdigest(),
        "pages": 1,
        "kind": "watermarked",
    }
    manifest = {"license": "CC0-1.0", "generated": True, "fixtures": fixtures}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    generate(Path(__file__).resolve().parent / "generated")
