r"""Import PDFs from an explicit Windows folder through the Knowledge Library import API.

The script never discovers a default drive or folder. Supply an explicit ``--root`` and a
short-lived batch token created by an authorised Knowledge Library administrator. It inventories
only PDFs contained by that resolved root, uploads them sequentially in resumable 1 MiB chunks,
and prints one JSON result per file without printing credentials or API responses.

The upload routes are not proxied by the web app (only the batch is created there, by a signed-in
administrator), so they are reached through an SSH port-forward to the API's loopback port. In one
PowerShell window, left open while the import runs:

    ssh -N -L 4400:127.0.0.1:4400 root@10.1.50.94

Then, in another (PowerShell):

    $env:NETSEC_KNOWLEDGE_IMPORT_TOKEN = '<batch token>'
    .\backend\.venv\Scripts\python.exe backend\scripts\import_windows_folder.py `
      --root 'D:\\staged-books' --api-base-url 'http://127.0.0.1:4400' `
      --token-env NETSEC_KNOWLEDGE_IMPORT_TOKEN

For a token file, keep it outside the books folder, restrict its ACL and delete it after the import:

    .\backend\.venv\Scripts\python.exe backend\scripts\import_windows_folder.py `
      --root 'D:\\staged-books' --api-base-url 'http://127.0.0.1:4400' `
      --token-file 'D:\\import-token.txt'

The worker then extracts and embeds each book. Nothing is searchable until an administrator publishes
it with backend/scripts/knowledge_admin.py on the host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

UPLOAD_CHUNK_BYTES = 1024 * 1024
TOKEN_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


class FolderImportError(Exception):
    """A safe, machine-readable folder-import failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ImportApiError(FolderImportError):
    """An API failure represented without retaining response content."""


@dataclass(frozen=True)
class InventoryEntry:
    """A stable PDF inventory record with a root-relative display path."""

    path: Path
    relative_path: str
    size_bytes: int
    sha256: str
    identity: tuple[int, int, int]


@dataclass(frozen=True)
class ImportResult:
    """The safe, per-file result emitted by the command-line client."""

    relative_path: str
    size_bytes: int
    sha256: str
    status: str
    code: str | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "file": self.relative_path,
            "sha256": self.sha256,
            "sizeBytes": self.size_bytes,
            "status": self.status,
        }
        if self.code is not None:
            result["code"] = self.code
        return result


def _identity(details: os.stat_result) -> tuple[int, int, int]:
    return details.st_ino, details.st_size, details.st_mtime_ns


def resolve_root(value: str | Path) -> Path:
    """Resolve an explicitly supplied directory and reject missing/non-directory roots."""

    try:
        root = Path(value).expanduser().resolve(strict=True)
    except OSError as error:
        raise FolderImportError("invalid_root") from error
    if not root.is_dir():
        raise FolderImportError("invalid_root")
    return root


def contained_relative(root: Path, candidate: Path) -> tuple[Path, str]:
    """Resolve a candidate and prove it remains inside the resolved explicit root."""

    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise FolderImportError("path_outside_root") from error
    if not relative.parts:
        raise FolderImportError("invalid_file_path")
    return resolved, relative.as_posix()


def _digest_file(path: Path, expected: tuple[int, int, int] | None = None) -> tuple[int, str, tuple[int, int, int]]:
    """Read a regular file once and reject a replacement or concurrent change."""

    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise FolderImportError("not_regular_file")
            before_identity = _identity(before)
            if expected is not None and before_identity != expected:
                raise FolderImportError("file_changed")
            digest = hashlib.sha256()
            size = 0
            for block in iter(lambda: handle.read(UPLOAD_CHUNK_BYTES), b""):
                digest.update(block)
                size += len(block)
            after_identity = _identity(os.fstat(handle.fileno()))
    except FolderImportError:
        raise
    except OSError as error:
        raise FolderImportError("file_unreadable") from error
    if before_identity != after_identity:
        raise FolderImportError("file_changed")
    return size, digest.hexdigest(), before_identity


def inventory_pdfs(root: str | Path) -> list[InventoryEntry]:
    """Build a deterministic SHA-256 inventory of regular, root-contained PDFs only."""

    resolved_root = resolve_root(root)
    records: list[InventoryEntry] = []
    try:
        candidates = sorted(resolved_root.rglob("*"), key=lambda path: path.as_posix().casefold())
    except OSError as error:
        raise FolderImportError("root_unreadable") from error
    for candidate in candidates:
        if candidate.suffix.lower() != ".pdf":
            continue
        resolved, relative_path = contained_relative(resolved_root, candidate)
        try:
            details = resolved.stat()
        except OSError as error:
            raise FolderImportError("file_unreadable") from error
        if not stat.S_ISREG(details.st_mode):
            continue
        size_bytes, digest, identity = _digest_file(resolved, _identity(details))
        records.append(
            InventoryEntry(
                path=resolved,
                relative_path=relative_path,
                size_bytes=size_bytes,
                sha256=digest,
                identity=identity,
            )
        )
    return records


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"https", "http"} or not hostname or parsed.username or parsed.password:
        raise FolderImportError("invalid_api_base_url")
    if parsed.query or parsed.fragment:
        raise FolderImportError("invalid_api_base_url")
    if parsed.scheme == "http" and hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise FolderImportError("insecure_api_base_url")
    return value.rstrip("/")


def read_batch_token(*, token_file: str | None, token_environment: str | None) -> str:
    """Read a batch credential without placing it in a command line or emitted result."""

    if bool(token_file) == bool(token_environment):
        raise FolderImportError("token_source_required")
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise FolderImportError("token_unreadable") from error
    else:
        if token_environment is None or not TOKEN_ENVIRONMENT_NAME.fullmatch(token_environment):
            raise FolderImportError("invalid_token_environment")
        token = os.environ.get(token_environment, "").strip()
    if not token or any(character.isspace() for character in token):
        raise FolderImportError("invalid_token")
    return token


def _response_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as error:
        raise ImportApiError("invalid_api_response") from error
    if not isinstance(body, dict):
        raise ImportApiError("invalid_api_response")
    return body


def _api_error_code(response: httpx.Response) -> str:
    try:
        error = _response_object(response).get("error")
    except ImportApiError:
        return "api_error"
    if isinstance(error, dict) and isinstance(error.get("code"), str) and ERROR_CODE.fullmatch(error["code"]):
        return error["code"]
    return "api_error"


class KnowledgeImportClient:
    """Minimal bearer-token client for the resumable Knowledge Library import API."""

    def __init__(
        self,
        api_base_url: str,
        batch_token: str,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_base_url = _validate_base_url(api_base_url)
        self._batch_token = batch_token
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> KnowledgeImportClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, *, body: dict[str, object] | bytes | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._batch_token}"}
        request_args: dict[str, object] = {"headers": headers}
        if isinstance(body, bytes):
            headers["Content-Type"] = "application/octet-stream"
            request_args["content"] = body
        elif body is not None:
            request_args["json"] = body
        try:
            response = self._client.request(method, f"{self._api_base_url}{path}", **request_args)
        except httpx.HTTPError as error:
            raise ImportApiError("api_unavailable") from error
        if not 200 <= response.status_code < 300:
            raise ImportApiError(_api_error_code(response))
        return _response_object(response)

    def import_file(self, entry: InventoryEntry) -> ImportResult:
        created = self._request(
            "POST",
            "/v1/knowledge/imports/files",
            body={"filename": entry.path.name, "sizeBytes": entry.size_bytes, "sha256": entry.sha256},
        )
        if created.get("duplicateOfFileId"):
            return ImportResult(entry.relative_path, entry.size_bytes, entry.sha256, "duplicate")
        file_id = created.get("id")
        offset = created.get("nextOffset")
        if not isinstance(file_id, str) or not isinstance(offset, int) or not 0 <= offset <= entry.size_bytes:
            raise ImportApiError("invalid_api_response")
        if offset < entry.size_bytes and offset % UPLOAD_CHUNK_BYTES:
            raise ImportApiError("invalid_resume_offset")

        with entry.path.open("rb") as handle:
            before = _identity(os.fstat(handle.fileno()))
            if before != entry.identity:
                raise FolderImportError("file_changed")
            handle.seek(offset)
            while offset < entry.size_bytes:
                payload = handle.read(min(UPLOAD_CHUNK_BYTES, entry.size_bytes - offset))
                if not payload:
                    raise FolderImportError("file_changed")
                uploaded = self._request(
                    "PUT",
                    f"/v1/knowledge/imports/files/{file_id}/content?offset={offset}",
                    body=payload,
                )
                next_offset = uploaded.get("nextOffset")
                expected_offset = offset + len(payload)
                if not isinstance(next_offset, int) or next_offset != expected_offset:
                    raise ImportApiError("invalid_api_response")
                offset = next_offset
            if _identity(os.fstat(handle.fileno())) != before:
                raise FolderImportError("file_changed")

        finalized = self._request("POST", f"/v1/knowledge/imports/files/{file_id}/finalize")
        if finalized.get("status") != "ready":
            raise ImportApiError("invalid_api_response")
        return ImportResult(entry.relative_path, entry.size_bytes, entry.sha256, "imported")


def import_folder(
    root: str | Path, client: KnowledgeImportClient, *, fail_fast: bool = False
) -> Iterator[ImportResult]:
    """Import a fresh inventory one file at a time, preserving useful results after failures."""

    for entry in inventory_pdfs(root):
        try:
            yield client.import_file(entry)
        except FolderImportError as error:
            yield ImportResult(entry.relative_path, entry.size_bytes, entry.sha256, "failed", error.code)
            if fail_fast:
                return
        except OSError:
            yield ImportResult(entry.relative_path, entry.size_bytes, entry.sha256, "failed", "file_unreadable")
            if fail_fast:
                return


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="explicit Windows folder containing PDFs")
    parser.add_argument("--api-base-url", required=True, help="HTTPS API origin, or loopback HTTP for local testing")
    token_source = parser.add_mutually_exclusive_group(required=True)
    token_source.add_argument("--token-file", help="path to a one-line batch token file")
    token_source.add_argument("--token-env", help="environment variable holding the batch token")
    parser.add_argument("--fail-fast", action="store_true", help="stop after the first per-file failure")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        token = read_batch_token(token_file=args.token_file, token_environment=args.token_env)
        with KnowledgeImportClient(args.api_base_url, token) as client:
            results = list(import_folder(args.root, client, fail_fast=args.fail_fast))
    except FolderImportError as error:
        print(json.dumps({"status": "failed", "code": error.code}, separators=(",", ":")), file=sys.stderr)
        return 1
    for result in results:
        print(json.dumps(result.as_dict(), sort_keys=True, separators=(",", ":")))
    summary = {"files": len(results), "failed": sum(result.status == "failed" for result in results)}
    print(json.dumps({"summary": summary}, separators=(",", ":")))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
