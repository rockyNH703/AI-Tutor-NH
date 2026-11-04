from __future__ import annotations

import unittest

from app.knowledge.content import RangeNotSatisfiable, parse_single_range


class RangeTests(unittest.TestCase):
    def test_single_open_and_suffix_ranges(self):
        self.assertEqual(parse_single_range("bytes=2-5", 10), (2, 5))
        self.assertEqual(parse_single_range("bytes=7-", 10), (7, 9))
        self.assertEqual(parse_single_range("bytes=-4", 10), (6, 9))
        self.assertEqual(parse_single_range(None, 10), None)

    def test_invalid_and_multiple_ranges_are_416(self):
        for value in ("bytes=10-", "bytes=5-2", "bytes=0-1,4-5", "items=0-1", "bytes=-0"):
            with self.subTest(value=value), self.assertRaises(RangeNotSatisfiable) as raised:
                parse_single_range(value, 10)
            self.assertEqual(raised.exception.headers["Content-Range"], "bytes */10")


if __name__ == "__main__":
    unittest.main()
