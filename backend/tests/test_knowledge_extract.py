from __future__ import annotations

import hashlib
import io
import json
import time
import unittest
from pathlib import Path

from app.knowledge import extract
from app.knowledge.extract import records
from app.knowledge.protocol import parse_ndjson

# Synthetic pages shaped like a printed book's contents, back index, tables and config listings (no book text).
INDEX_PAGE = "\n".join(
    ["Symbols", "OSPF (Open Shortest Path First)"]
    + [f"topic number {n}, {100 + 7 * n}" for n in range(42)]
    + ["areas, 551–553", "DR election, 456, 460", "wrapped entry continues here"]
)
INDEX_TAIL = "\n".join([f"last topic {n}, {600 + n}" for n in range(9)] + ["zone, 640"])
CONTENTS_PAGE = "\n".join(["Contents"] + [f"Chapter {n} Routing Fundamentals {4 + 20 * n}" for n in range(1, 12)])
DOTTED_CONTENTS = "\n".join(["Contents"] + [f"{n}.1 Routing Fundamentals ........ {3 + 9 * n}" for n in range(1, 12)])
CONFIG_PAGE = "\n".join(
    [
        "vlan 10",
        " name Sales",
        "vlan 20",
        "vlan 5",
        "router ospf 1",
        " network 10.1.1.0 0.0.0.255 area 0",
        "interface Vlan 10",
        " ip ospf priority 100",
        " ip ospf cost 5",
        "spanning-tree vlan 10 priority 4096",
        "spanning-tree vlan 20 priority 8192",
        " switchport access vlan 30",
        " switchport voice vlan 3",
    ]
)
PORTS_PAGE = "\n".join(
    ["Table 5-3 Well-known ports"]
    + [
        f"Protocol{name} TCP {port}"
        for name, port in zip("ABCDEFGHIJKL", (20, 21, 22, 23, 25, 53, 67, 80, 110, 143, 443, 514), strict=True)
    ]
)
DATES_PAGE = "\n".join(f"Release {n} shipped on July {n + 1}, 2020" for n in range(12))
FIREWALL_PAGE = "\n".join(f"Service{n} TCP {20 + n}, {21 + n}" for n in range(12))
PROSE_PAGE = "\n".join(f"Sentence {n} explains how routers forward packets toward a destination." for n in range(10))


class KnowledgeStructureTests(unittest.TestCase):
    def test_contents_in_front_and_the_index_at_the_back_navigate_and_tables_do_not(self):
        pages = [
            CONTENTS_PAGE,  # 0, front matter
            DOTTED_CONTENTS,  # 1, front matter, dot leaders
            PROSE_PAGE,
            CONFIG_PAGE,
            PORTS_PAGE,  # rising port numbers in the body
            PROSE_PAGE,
            PROSE_PAGE,
            PROSE_PAGE,
            PROSE_PAGE,
            DATES_PAGE,  # back half, ", 2020" is no page reference
            FIREWALL_PAGE,  # back half, ", 21" endings but table-sized and not after an index page
            INDEX_PAGE,  # 11
            INDEX_TAIL,  # 12, a short last index page
            "Contents xiii\nChapter 1 Introduction 4",  # too short to judge
        ]
        labels = ["xi", "xii", *map(str, range(1, 10)), "880", "881", "882"]

        self.assertEqual(extract._navigation(pages, labels), {0, 1, 11, 12})

    def test_a_running_head_carries_the_page_number_and_may_name_the_chapter(self):
        head = extract._running_head
        chapter = "Chapter 22: Implementing Basic OSPF Features"
        book = "CCNA 200-301 Official Cert Guide, Volume 1"

        self.assertEqual(head(f"{chapter} 573", "573"), chapter)
        self.assertEqual(head(f"568 {book}", "568"), book)
        self.assertEqual(head(f"xviii {book}", "xviii"), book)
        self.assertEqual(head("42    Chapter 2: Cisco SD-WAN Components", "42"), "Chapter 2: Cisco SD-WAN Components")
        self.assertIsNone(head("CHAPTER 1", "1"))  # a chapter opener, not a running head
        self.assertIsNone(head("Web servers listen on TCP port 443", "443"))  # text that ends in the page number
        self.assertIsNone(head(f"{chapter} 573", "574"))
        self.assertIsNone(head(f"{chapter} 573", None))

    def test_long_first_and_last_lines_are_read_in_linear_time(self):
        hostile = ["a" + " " * 20_000 + "b", "," * 20_000 + " 573", "a" + ". " * 20_000 + "x"]
        started = time.perf_counter()

        extract._pages(["\n".join([line, "body", line]) for line in hostile] * 3, ["573"] * 9)

        self.assertLess(time.perf_counter() - started, 1.0)

    def test_pages_lose_running_heads_and_navigation_and_keep_the_chapter(self):
        texts = [
            "Chapter 22: Implementing Basic OSPF Features 573\nOSPF elects a designated router on each segment.",
            "574 CCNA 200-301 Official Cert Guide, Volume 1\nWhen priorities tie, the highest router ID wins.",
            INDEX_PAGE,
        ]

        cleaned, chapters, navigation = extract._pages(texts, ["573", "574", "880"])

        self.assertEqual(cleaned[:2], ["OSPF elects a designated router on each segment.", texts[1].splitlines()[1]])
        self.assertEqual(chapters, ["Chapter 22: Implementing Basic OSPF Features", None, None])
        self.assertEqual(navigation, {2})

    def test_a_chapter_line_is_a_title_not_a_sentence(self):
        title = extract._chapter_title

        self.assertEqual(
            title("Chapter 22: Implementing Basic OSPF Features 573"), "Chapter 22: Implementing Basic OSPF Features"
        )
        self.assertEqual(title("APPENDIX a: Answers to the Quizzes"), "Appendix A: Answers to the Quizzes")
        self.assertIsNone(title("Chapter 3"))  # a label inside a figure, not a heading
        self.assertIsNone(title("Chapter 28: This new chapter contains the final topic of former Chapter 25"))
        self.assertIsNone(title("Chapter 17: (Formerly Chapter 12) Split SNMP,"))
        self.assertIsNone(title("Chapter 5 covers the details of trunking"))
        long_head = "Chapter 7: Implementing and Troubleshooting Advanced Spanning Tree Features in Campus Networks"
        self.assertEqual(title(long_head, strict=False), long_head)  # a running head is a title by its place

    def test_exam_domains_with_their_weight_hold_their_own_topics(self):
        pages = [
            "25% 1.0 Network Infrastructure and Connectivity\n1.1 Diagnose interface and cable issues\n"
            "1.1.a Collisions, errors, duplex\n1.2 Compare TCP to UDP\n"
            "20% 3.0 IP Routing\n3.1 Interpret a routing table",
            "3.2 Determine how a router makes a forwarding decision\n"
            "4.0 IP Services 10%\n4.1 Configure inside source NAT",
        ]

        paths = [path for _, path, _ in extract._segments(pages, [], [None, None])]

        infrastructure, routing = "1.0 Network Infrastructure and Connectivity", "3.0 IP Routing"
        self.assertEqual(
            paths,
            [
                (infrastructure,),
                (infrastructure, "1.1 Diagnose interface and cable issues"),
                (infrastructure, "1.2 Compare TCP to UDP"),
                (routing,),
                (routing, "3.1 Interpret a routing table"),
                (routing, "3.2 Determine how a router makes a forwarding decision"),
                ("4.0 IP Services",),
                ("4.0 IP Services", "4.1 Configure inside source NAT"),
            ],
        )

    def test_without_its_domain_a_topic_is_not_filed_under_another_one(self):
        paths = [path for _, path, _ in extract._segments(["2.0 Network Access\n2.1 VLANs\n3.1 Routing tables"], [])]

        self.assertEqual(paths, [("2.0 Network Access",), ("2.0 Network Access", "2.1 VLANs"), ("3.1 Routing tables",)])

    def test_answer_keys_contents_lines_and_measurements_are_body_text(self):
        pages = [
            "Answers to the quiz\n1 B 2 A 3 A, E 4 B 5 B 6 A, D\n24 Hours Before Your Exam 582",
            "1.1 Explain the role of network components\n2.4 GHz band channels overlap in dense deployments",
        ]

        segments = list(extract._segments(pages, [], [None, None]))

        self.assertEqual([path for _, path, _ in segments], [(), ("1.1 Explain the role of network components",)])
        self.assertIn("2.4 GHz band", segments[1][2])

    def test_a_topic_runs_on_only_while_its_numbered_list_does(self):
        listing = "1.11 Describe wireless principles\n1.12 Explain virtualization\n1.13 Describe switching concepts"
        after = list(extract._segments([listing, "How to use this book"], [], [None, None]))
        self.assertEqual(after[-1][1], ())  # the introduction after an exam-topics table is not topic 1.13

        continued = list(extract._segments([listing, "1.13.a MAC learning\n1.14 Describe routing", "Prose"], []))
        self.assertEqual(continued[3][1], ("1.13 Describe switching concepts",))  # the list goes on: top of page 2
        self.assertEqual(continued[-1][1], ())

        chapter = "Chapter 16: First Hop Redundancy Protocols"
        opener = "Covers the following exam topics:\n3.0 IP Connectivity\n3.5 Describe the purpose of FHRP"
        pages = list(extract._segments([opener, "HSRP elects an active router."], [], [chapter, chapter]))
        self.assertEqual(pages[-1][1], (chapter,))  # the chapter's text is the chapter's, not topic 3.5

    def test_a_chapter_opener_starts_its_chapter_and_figure_labels_or_answer_keys_do_not(self):
        basic, optional = (
            "Chapter 22: Implementing Basic OSPF Features",
            "Chapter 23: Implementing Optional OSPF Features",
        )
        answers = "Appendix A: Answers to Chapter Review Questions"
        pages = [
            "OSPF text",
            "Figure I-7 Part review\nChapter 1\nReview\nChapter 2\nReview",
            "CHAPTER 23\nImplementing Optional OSPF Features\nThis chapter covers the following exam topics:",
            "Do I Know This Already? Quiz",
            "Optional OSPF text",
            "Chapter 1\n1. A, B, D. The three controllers ...",
            "Chapter 2\n1. A. The controllers ...",
        ]
        chapters = [basic, None, None, None, optional, answers, None]

        paths = [path for _, path, _ in extract._segments(pages, [], chapters)]

        self.assertEqual(paths, [(basic,), (basic,), (optional,), (optional,), (optional,), (answers,), (answers,)])

    def test_the_book_reader_leaves_navigation_out_and_names_it_in_a_warning(self):
        texts = [
            CONTENTS_PAGE,
            "Chapter 22: Implementing Basic OSPF Features 573\nOSPF elects a designated router.",
            "574 CCNA 200-301 Official Cert Guide, Volume 1\nWhen priorities tie, the highest router ID wins.",
            INDEX_PAGE,
        ]

        elements, cleaned, warnings = extract._book_elements(texts, ["xi", "573", "574", "880"], [])

        self.assertEqual([element["page"] for element in elements], [1, 2])
        self.assertEqual(
            [element["section"] for element in elements], [("Chapter 22: Implementing Basic OSPF Features",)] * 2
        )
        self.assertEqual(warnings, ["no_outline", "navigation_pages:2 (1 4)"])
        self.assertEqual(len(cleaned), 4)  # what the book says about itself is still read from every page


class KnowledgeExtractorAdapterTests(unittest.TestCase):
    def test_repository_fixture_round_trips(self):
        path = Path(__file__).parent / "fixtures" / "knowledge" / "generated" / "digital-structure.pdf"
        data = path.read_bytes()
        output = records(data)
        raw = b"".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n" for record in output
        )

        extracted = parse_ndjson(io.BytesIO(raw))

        self.assertEqual(extracted.document_sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(extracted.page_count, 2)
        self.assertGreater(len(extracted.elements), 0)

    def test_a_tiled_image_page_with_no_text_objects_is_read_with_ocr(self):
        # Tiled-image PDFs (real export tools) cover far less of the page than the image-share threshold
        # with each tile's bounds; before the fix such a page was never OCR'd and the book had no chunks.
        path = Path(__file__).parent / "fixtures" / "knowledge" / "generated" / "tiled-image.pdf"
        if not path.exists():  # the fixture is generated; regenerate when missing
            from tests.fixtures.knowledge.generate import generate

            generate(path.parent)
        data = path.read_bytes()
        output = records(data)
        raw = b"".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n" for record in output
        )

        extracted = parse_ndjson(io.BytesIO(raw))

        self.assertEqual(extracted.page_count, 1)
        self.assertGreater(len(extracted.elements), 0)
        self.assertIn("ocr_used", extracted.warnings)
        joined = "\n".join(element["content"] for element in extracted.elements)
        self.assertIn("permit", joined.lower())  # the OCR actually read the tile text

    def test_a_scanned_page_with_a_text_watermark_is_still_read_with_ocr(self):
        # Watermarked course books carry a small text object (a site watermark) on an image page: the
        # picture still has to be OCR'd. A first version of the tiled fix stopped counting image coverage
        # at the first text object and silently dropped these pages back to unread.
        path = Path(__file__).parent / "fixtures" / "knowledge" / "generated" / "watermarked-scan.pdf"
        if not path.exists():  # the fixture is generated; regenerate when missing
            from tests.fixtures.knowledge.generate import generate

            generate(path.parent)
        data = path.read_bytes()
        output = records(data)
        raw = b"".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n" for record in output
        )

        extracted = parse_ndjson(io.BytesIO(raw))

        self.assertEqual(extracted.page_count, 1)
        self.assertGreater(len(extracted.elements), 0)
        self.assertIn("ocr_used", extracted.warnings)
        joined = "\n".join(element["content"] for element in extracted.elements)
        self.assertIn("permit", joined.lower())  # the OCR actually read the scanned text


if __name__ == "__main__":
    unittest.main()
