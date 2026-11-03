from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import httpx

from scripts.import_windows_folder import (
    UPLOAD_CHUNK_BYTES,
    FolderImportError,
    ImportApiError,
    InventoryEntry,
    KnowledgeImportClient,
    contained_relative,
    inventory_pdfs,
)


class WindowsFolderImportTests(unittest.TestCase):
    def test_inventory_is_pdf_only_root_relative_and_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "nested"
            nested.mkdir()
            payload = b"%PDF-synthetic\n"
            (nested / "Guide.PDF").write_bytes(payload)
            (root / "notes.txt").write_text("not a PDF", encoding="utf-8")

            entries = inventory_pdfs(root)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].relative_path, "nested/Guide.PDF")
            self.assertEqual(entries[0].sha256, hashlib.sha256(payload).hexdigest())
            self.assertEqual(entries[0].size_bytes, len(payload))

    def test_path_outside_explicit_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = (base / "root").resolve()
            root.mkdir()
            outside = base / "outside.pdf"
            outside.write_bytes(b"%PDF-synthetic\n")

            with self.assertRaisesRegex(FolderImportError, "path_outside_root"):
                contained_relative(root, outside)

    def test_resumable_upload_uses_one_mib_non_final_chunks_and_finalizes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.pdf"
            payload = b"a" * UPLOAD_CHUNK_BYTES + b"tail"
            path.write_bytes(payload)
            entry = InventoryEntry(
                path=path,
                relative_path="synthetic.pdf",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                identity=(path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns),
            )
            requests: list[httpx.Request] = []

            def handler(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                self.assertEqual(request.headers["authorization"], "Bearer batch-token")
                if request.method == "POST" and request.url.path.endswith("/files"):
                    return httpx.Response(201, json={"id": "file-1", "nextOffset": UPLOAD_CHUNK_BYTES})
                if request.method == "PUT":
                    self.assertEqual(request.url.params["offset"], str(UPLOAD_CHUNK_BYTES))
                    self.assertEqual(request.content, b"tail")
                    return httpx.Response(200, json={"nextOffset": len(payload)})
                if request.method == "POST" and request.url.path.endswith("/finalize"):
                    return httpx.Response(200, json={"status": "ready"})
                self.fail(f"unexpected request: {request.method} {request.url.path}")

            raw_client = httpx.Client(transport=httpx.MockTransport(handler))
            try:
                client = KnowledgeImportClient("https://knowledge.test", "batch-token", client=raw_client)
                result = client.import_file(entry)
            finally:
                raw_client.close()

            self.assertEqual(result.status, "imported")
            self.assertEqual([request.method for request in requests], ["POST", "PUT", "POST"])

    def test_api_error_does_not_retain_or_emit_response_body(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "batch-token leaked"}})

        raw_client = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            client = KnowledgeImportClient("https://knowledge.test", "batch-token", client=raw_client)
            with self.assertRaisesRegex(ImportApiError, "unauthorized") as raised:
                client._request("POST", "/v1/knowledge/imports/files", body={})
        finally:
            raw_client.close()

        self.assertNotIn("batch-token", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
