"""Generate small, original CCNA-shaped PDFs for parser and ingestion tests.

All words, diagrams and configurations are synthetic. No vendor book content is copied.
"""

from __future__ import annotations

import hashlib
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
    manifest = {"license": "CC0-1.0", "generated": True, "fixtures": fixtures}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    generate(Path(__file__).resolve().parent / "generated")
