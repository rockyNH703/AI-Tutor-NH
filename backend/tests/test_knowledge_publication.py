from __future__ import annotations

import hashlib
import unittest

from app.knowledge.protocol import ExtractedDocument
from app.knowledge.publication import MAX_CHUNK_CHARS, build_chunks


class PublicationTests(unittest.TestCase):
    def test_structure_and_page_provenance_make_stable_keys(self):
        content = "router ospf 10\n" + ("network 192.0.2.0 area 0 " * 150)
        extracted = ExtractedDocument(
            document_sha256="a" * 64,
            page_count=2,
            parser="synthetic/1",
            elements=(
                {
                    "sequence": 0,
                    "element_type": "code",
                    "content": content,
                    "physical_page": 2,
                    "printed_page_label": "1",
                    "section_path": ["Routing", "OSPF"],
                    "metadata": {"language": "ios"},
                },
            ),
            warnings=(),
            content_sha256=hashlib.sha256(content.encode() + b"\0").hexdigest(),
        )
        first = build_chunks(extracted)
        second = build_chunks(extracted)
        self.assertEqual(first, second)
        self.assertGreater(len(first), 1)
        self.assertTrue(all(len(chunk.content) <= MAX_CHUNK_CHARS for chunk in first))
        self.assertTrue(all(chunk.physical_page_start == 2 for chunk in first))
        self.assertTrue(all(chunk.printed_page_label == "1" for chunk in first))
        self.assertEqual(len({chunk.stable_key for chunk in first}), len(first))

    def test_small_elements_of_one_page_share_a_chunk_within_their_top_section(self):
        fundamentals, access, connectivity = "1.0 Network Fundamentals 20%", "2.0 Network Access 20%", "3.0 IP 25%"
        explain = "1.1 Explain the role and function of network components"
        describe = "1.2 Describe characteristics of network topology architectures"
        vlans = "2.1 Configure and verify VLANs"
        elements = (
            _element(0, "Cisco Public", 1, ()),
            _element(1, fundamentals, 1, (fundamentals,)),
            _element(2, f"{explain}\n1.1.a Routers", 1, (fundamentals, explain)),
            _element(3, describe, 1, (fundamentals, describe)),
            _element(4, access, 1, (access,)),
            _element(5, vlans, 1, (access, vlans)),
            _element(6, connectivity, 2, (connectivity,)),
            _element(7, "x" * 900, 2, (connectivity,)),
            _element(8, "y" * 900, 2, (connectivity,)),
        )

        chunks = build_chunks(_document(elements))

        self.assertEqual(
            [chunk.content for chunk in chunks],
            [
                f"Cisco Public\n{fundamentals}\n{explain}\n1.1.a Routers\n{describe}",
                f"{access}\n{vlans}",
                f"{connectivity}\n{'x' * 900}",
                "y" * 900,
            ],
        )
        self.assertEqual(
            [chunk.section_path for chunk in chunks], [(fundamentals,), (access,), (connectivity,), (connectivity,)]
        )
        self.assertEqual(
            [(chunk.ordinal, chunk.physical_page_start) for chunk in chunks], [(0, 1), (1, 1), (2, 2), (3, 2)]
        )
        self.assertEqual(len({chunk.stable_key for chunk in chunks}), 4)


class PublicationGroupingTests(unittest.TestCase):
    def test_sibling_topics_without_their_domain_share_a_chunk_by_their_major_number(self):
        elements = (
            _element(0, "1.1 Describe the architecture", 1, ("1.1 Describe the architecture",)),
            _element(1, "1.2 Describe the components", 1, ("1.2 Describe the components",)),
            _element(2, "6.4 Describe image management", 1, ("6.4 Describe image management",)),
        )

        chunks = build_chunks(_document(elements))

        self.assertEqual(
            [chunk.content for chunk in chunks],
            ["1.1 Describe the architecture\n1.2 Describe the components", "6.4 Describe image management"],
        )
        self.assertEqual([chunk.section_path for chunk in chunks], [(), ("6.4 Describe image management",)])


def _element(sequence: int, content: str, page: int, section: tuple[str, ...]) -> dict:
    return {
        "sequence": sequence,
        "element_type": "paragraph",
        "content": content,
        "physical_page": page,
        "printed_page_label": None,
        "section_path": list(section),
        "metadata": {},
    }


def _document(elements: tuple[dict, ...]) -> ExtractedDocument:
    return ExtractedDocument(
        document_sha256="b" * 64,
        page_count=2,
        parser="synthetic/1",
        elements=elements,
        warnings=(),
        content_sha256="c" * 64,
    )


if __name__ == "__main__":
    unittest.main()
