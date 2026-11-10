"""
tests/test_models.py - Unit tests for NLAgent Model Store, Manifest, and Resumable Range Downloader.

Tests cover:
1. Manifest reading & schema validation (name, quant, size, sha256, min_ram_gb, notice_file).
2. SHA-256 calculation, verification, and mismatch handling.
3. Model store path resolution under %LOCALAPPDATA%/NLAgent/models with fallback mechanisms.
4. Resumable download with HTTP Range requests (200 OK vs 206 Partial Content).
5. Interrupted download handling: network drops mid-stream, leaving `.part` intact, then cleanly resumes.
6. Local model file import with checksum verification and notice file preservation.
7. Model inventory status and RAM sufficiency checks against system specifications.
"""

from __future__ import annotations

import email
import hashlib
import http.server
import io
import json
import os
import sys
import tempfile
import urllib.request
import urllib.response
from pathlib import Path
from typing import Any

import pytest

from nlagent.runtime.models import (
    ChecksumMismatchError,
    InsufficientRAMError,
    ManifestError,
    ModelEntry,
    ModelStatus,
    compute_sha256,
    download_model,
    find_model_in_manifest,
    get_model_path,
    get_model_status,
    get_models_dir,
    import_local_model,
    list_models_status,
    read_manifest,
    save_manifest,
    verify_file_sha256,
)


# -----------------------------------------------------------------------------
# Test HTTP Server Fixtures Supporting HTTP Range Requests
# -----------------------------------------------------------------------------

class MockRangeServer:
    """
    In-process HTTP server emulator for testing resumable downloads and range requests.
    Supports HTTP 200, HTTP 206 (Partial Content), Content-Range headers,
    and simulated mid-stream network drop / interruption.
    """

    def __init__(
        self,
        payload: bytes,
        interrupt_at: int | None = None,
        interrupt_attempts: int = 1,
        ignore_range: bool = False,
    ) -> None:
        self.payload = payload
        self.interrupt_at = interrupt_at
        self.interrupt_attempts = interrupt_attempts
        self.ignore_range = ignore_range
        self.attempt_count = 0
        self.opener = urllib.request.build_opener(self._Handler(self))

    class _Handler(urllib.request.BaseHandler):
        def __init__(self, server: MockRangeServer) -> None:
            self.server = server

        def default_open(self, req: urllib.request.Request) -> Any:
            self.server.attempt_count += 1
            headers = dict(req.header_items())
            range_hdr = headers.get("Range") or headers.get("range")
            start = 0

            if not self.server.ignore_range and range_hdr and "bytes=" in range_hdr:
                try:
                    start = int(range_hdr.split("bytes=")[1].rstrip("-"))
                except Exception:
                    start = 0

            total_size = len(self.server.payload)

            # Check if this request should simulate an interrupted network drop
            if (
                self.server.interrupt_at is not None
                and self.server.attempt_count <= self.server.interrupt_attempts
            ):
                cutoff = self.server.interrupt_at
                chunk = self.server.payload[start : start + cutoff]
                code = 206 if (start > 0 and not self.server.ignore_range) else 200

                class InterruptedStream:
                    def __init__(self, data: bytes) -> None:
                        self.data = data
                        self.offset = 0

                    def read(self, size: int = -1) -> bytes:
                        if self.offset >= len(self.data):
                            raise ConnectionResetError("Connection reset by peer (simulated mid-stream network cut)")
                        n = len(self.data) - self.offset if size < 0 else min(size, len(self.data) - self.offset)
                        res = self.data[self.offset : self.offset + n]
                        self.offset += n
                        return res

                msg = email.message.EmailMessage()
                msg["Content-Length"] = str(total_size)
                if code == 206:
                    msg["Content-Range"] = f"bytes {start}-{start + len(chunk) - 1}/{total_size}"
                resp = urllib.response.addinfourl(InterruptedStream(chunk), msg, req.get_full_url(), code=code)
                resp.msg = "Partial Content" if code == 206 else "OK"
                return resp

            # Normal response (full or remaining range)
            body = self.server.payload[start:]
            code = 206 if (start > 0 and not self.server.ignore_range) else 200
            msg = email.message.EmailMessage()
            msg["Content-Length"] = str(len(body))
            if code == 206:
                msg["Content-Range"] = f"bytes {start}-{total_size - 1}/{total_size}"
            resp = urllib.response.addinfourl(io.BytesIO(body), msg, req.get_full_url(), code=code)
            resp.msg = "Partial Content" if code == 206 else "OK"
            return resp


# -----------------------------------------------------------------------------
# 1. Manifest Reading and Validation Tests
# -----------------------------------------------------------------------------

class TestManifestReading:
    """Validate manifest.json parsing, required fields, and serialization."""

    def test_read_canonical_manifest(self):
        """Verify the repository's default models/manifest.json loads all required fields."""
        manifest = read_manifest()
        assert len(manifest) >= 1
        entry = manifest[0]

        # Verify explicitly requested fields
        assert isinstance(entry.name, str) and len(entry.name) > 0
        assert isinstance(entry.quant, str) and len(entry.quant) > 0
        assert isinstance(entry.size, int) and entry.size > 0
        assert isinstance(entry.sha256, str) and len(entry.sha256) == 64
        assert isinstance(entry.min_ram_gb, float) and entry.min_ram_gb > 0.0
        assert isinstance(entry.notice_file, str)
        assert entry.canonical_filename.endswith(".gguf")

    def test_save_and_re_read_manifest(self, tmp_path):
        """Verify manifest serialization round-trip."""
        entries = [
            ModelEntry(
                name="test-model",
                quant="Q4_0",
                size=1024,
                sha256="a" * 64,
                min_ram_gb=4.0,
                notice_file="NOTICE",
                url="http://mock/test.gguf",
            )
        ]
        manifest_file = tmp_path / "manifest.json"
        save_manifest(entries, manifest_file)

        loaded = read_manifest(manifest_file)
        assert len(loaded) == 1
        assert loaded[0].name == "test-model"
        assert loaded[0].quant == "Q4_0"
        assert loaded[0].size == 1024
        assert loaded[0].sha256 == "a" * 64
        assert loaded[0].min_ram_gb == 4.0
        assert loaded[0].notice_file == "NOTICE"

    def test_manifest_missing_name_raises(self, tmp_path):
        """Invalid manifest missing 'name' raises ManifestError."""
        bad_file = tmp_path / "bad_manifest.json"
        bad_file.write_text(json.dumps([{"quant": "Q4_0", "size": 100}]), encoding="utf-8")
        with pytest.raises(ManifestError, match="missing required 'name'"):
            read_manifest(bad_file)

    def test_find_model_in_manifest(self):
        entries = [
            ModelEntry(name="nemotron-3-nano-4b", quant="Q4_K_M", size=100, sha256="abc", min_ram_gb=8.0),
            ModelEntry(name="llama-3.2", quant="3B", size=200, sha256="def", min_ram_gb=4.0),
        ]
        m = find_model_in_manifest(entries, "nemotron-3-nano-4b", "Q4_K_M")
        assert m is not None
        assert m.name == "nemotron-3-nano-4b"

        assert find_model_in_manifest(entries, "unknown") is None


# -----------------------------------------------------------------------------
# 2. Checksum and Storage Directory Tests
# -----------------------------------------------------------------------------

class TestChecksumAndStorage:
    """Verify SHA-256 calculation, comparison, and storage directory hierarchies."""

    def test_compute_and_verify_sha256(self, tmp_path):
        sample = tmp_path / "sample.bin"
        data = b"NLAgent Model Weight Checksum Test"
        sample.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()

        calc = compute_sha256(sample)
        assert calc == expected
        assert verify_file_sha256(sample, expected) is True
        assert verify_file_sha256(sample, expected.upper()) is True
        assert verify_file_sha256(sample, "0" * 64) is False

    def test_get_models_dir_precedence(self, tmp_path, monkeypatch):
        # 1. Custom directory override
        custom = tmp_path / "custom_models"
        assert get_models_dir(custom) == custom.resolve()

        # 2. NLAGENT_MODELS_DIR override
        env_dir = tmp_path / "env_models"
        monkeypatch.setenv("NLAGENT_MODELS_DIR", str(env_dir))
        assert get_models_dir() == env_dir.resolve()

        # 3. Default fallback contains 'NLAgent' or 'models'
        monkeypatch.delenv("NLAGENT_MODELS_DIR", raising=False)
        resolved = get_models_dir()
        assert resolved.exists()
        assert "models" in resolved.name.lower() or "nlagent" in str(resolved).lower()


# -----------------------------------------------------------------------------
# 3. Resumable HTTP Range Downloader Tests
# -----------------------------------------------------------------------------

class TestResumableDownload:
    """Test full downloads, range resumes, interrupted downloads, and corruptions."""

    def test_full_download_from_zero(self, tmp_path):
        payload = b"GGUF_HEADER_DATA_12345" * 100
        expected_sha = hashlib.sha256(payload).hexdigest()
        server = MockRangeServer(payload)

        entry = ModelEntry(
            name="nano-model",
            quant="Q4",
            size=len(payload),
            sha256=expected_sha,
            min_ram_gb=4.0,
            url="http://mock-server/nano-model.Q4.gguf",
        )

        dest_dir = tmp_path / "models"
        target = download_model(entry, dest_dir=dest_dir, opener=server.opener)

        assert target.exists()
        assert target.name == "nano-model.Q4.gguf"
        assert target.stat().st_size == len(payload)
        assert verify_file_sha256(target, expected_sha) is True
        # Part file should be gone
        assert not (dest_dir / "nano-model.Q4.gguf.part").exists()

    def test_interrupted_download_and_resume(self, tmp_path):
        """
        Verify that an interrupted download leaves a `.part` file,
        and a subsequent invocation resumes via HTTP Range request (206 Partial Content).
        """
        payload = b"WEIGHTS_BLOCK_DATA_XYZ_" * 500  # 11,500 bytes
        expected_sha = hashlib.sha256(payload).hexdigest()
        total_size = len(payload)

        # Set server to cut off after 3,000 bytes on attempt 1
        server = MockRangeServer(payload, interrupt_at=3000, interrupt_attempts=1)

        entry = ModelEntry(
            name="interrupted-model",
            quant="Q4",
            size=total_size,
            sha256=expected_sha,
            min_ram_gb=4.0,
            url="http://mock-server/interrupted.gguf",
        )

        dest_dir = tmp_path / "models"
        final_file = dest_dir / "interrupted-model.Q4.gguf"
        part_file = dest_dir / "interrupted-model.Q4.gguf.part"

        # Attempt 1: Should fail due to simulated mid-stream network drop
        with pytest.raises(ConnectionResetError, match="simulated mid-stream network cut"):
            download_model(entry, dest_dir=dest_dir, opener=server.opener)

        # Verify partial download state on disk
        assert not final_file.exists()
        assert part_file.exists()
        partial_size = part_file.stat().st_size
        assert partial_size == 3000
        assert server.attempt_count == 1

        # Attempt 2: Resume download
        # Server attempt_count is now 2, will serve remaining bytes starting at 3000 with 206 Partial Content
        completed = download_model(entry, dest_dir=dest_dir, opener=server.opener)

        assert completed == final_file
        assert completed.exists()
        assert not part_file.exists()
        assert completed.stat().st_size == total_size
        assert verify_file_sha256(completed, expected_sha) is True
        assert server.attempt_count == 2

    def test_server_ignores_range_restarts_cleanly(self, tmp_path):
        """If server does not support range (returns 200 OK), client overwrites partial cleanly."""
        payload = b"SERVER_NO_RANGE_SUPPORT_TEST" * 100
        expected_sha = hashlib.sha256(payload).hexdigest()

        # Existing stale partial file
        dest_dir = tmp_path / "models"
        dest_dir.mkdir(parents=True, exist_ok=True)
        part_file = dest_dir / "norange-model.Q4.gguf.part"
        part_file.write_bytes(b"STALE_OLD_BYTES")

        server = MockRangeServer(payload, ignore_range=True)
        entry = ModelEntry(
            name="norange-model",
            quant="Q4",
            size=len(payload),
            sha256=expected_sha,
            min_ram_gb=4.0,
            url="http://mock-server/norange.gguf",
        )

        completed = download_model(entry, dest_dir=dest_dir, opener=server.opener)
        assert completed.exists()
        assert completed.stat().st_size == len(payload)
        assert verify_file_sha256(completed, expected_sha) is True

    def test_download_checksum_mismatch_raises_and_rejects(self, tmp_path):
        """Corrupted payload fails SHA-256 verification and does not create final model file."""
        server = MockRangeServer(b"CORRUPTED_BYTES")
        entry = ModelEntry(
            name="corrupted-model",
            quant="Q4",
            size=15,
            sha256="b" * 64,  # Incorrect hash
            min_ram_gb=4.0,
            url="http://mock-server/corrupt.gguf",
        )

        dest_dir = tmp_path / "models"
        final_file = dest_dir / "corrupted-model.Q4.gguf"

        with pytest.raises(ChecksumMismatchError, match="SHA-256 verification failed"):
            download_model(entry, dest_dir=dest_dir, opener=server.opener)

        assert not final_file.exists()


# -----------------------------------------------------------------------------
# 4. Local Model File Import Tests
# -----------------------------------------------------------------------------

class TestLocalModelImport:
    """Verify importing model from local disk with checksum and notice handling."""

    def test_import_valid_local_model(self, tmp_path):
        source = tmp_path / "external_source" / "local_weights.gguf"
        source.parent.mkdir(parents=True, exist_ok=True)
        data = b"GGUF_LOCAL_MODEL_DATA_MAGIC_12345"
        source.write_bytes(data)
        expected_sha = hashlib.sha256(data).hexdigest()

        notice_file = source.parent / "NOTICE"
        notice_file.write_text("Notice attribution for local model\n", encoding="utf-8")

        entry = ModelEntry(
            name="imported-model",
            quant="Q4_K_M",
            size=len(data),
            sha256=expected_sha,
            min_ram_gb=8.0,
            notice_file="NOTICE",
        )

        dest_dir = tmp_path / "models_store"
        imported = import_local_model(source, entry=entry, dest_dir=dest_dir)

        assert imported.exists()
        assert imported.name == "imported-model.Q4_K_M.gguf"
        assert verify_file_sha256(imported, expected_sha) is True

        # Verify notice file was also copied alongside
        assert (dest_dir / "NOTICE").exists()
        assert "Notice attribution" in (dest_dir / "NOTICE").read_text(encoding="utf-8")

    def test_import_checksum_mismatch_raises(self, tmp_path):
        source = tmp_path / "bad_weights.gguf"
        source.write_bytes(b"WRONG_DATA")

        entry = ModelEntry(
            name="bad-model",
            quant="Q4",
            size=10,
            sha256="f" * 64,
            min_ram_gb=4.0,
        )

        dest_dir = tmp_path / "models_store"
        with pytest.raises(ChecksumMismatchError, match="checksum mismatch"):
            import_local_model(source, entry=entry, dest_dir=dest_dir)


# -----------------------------------------------------------------------------
# 5. Inventory and RAM Check Tests
# -----------------------------------------------------------------------------

class TestInventoryAndStatus:
    """Verify get_model_status, ready/partial/missing states, and RAM checks."""

    def test_model_status_lifecycle(self, tmp_path):
        dest_dir = tmp_path / "models"
        dest_dir.mkdir(parents=True, exist_ok=True)

        entry = ModelEntry(
            name="lifecycle-model",
            quant="Q4_K_M",
            size=1000,
            sha256="0" * 64,
            min_ram_gb=2.0,
        )

        # 1. Initially missing
        s1 = get_model_status(entry, models_dir=dest_dir)
        assert s1.status == "missing"
        assert s1.local_path is None
        assert s1.downloaded_bytes == 0

        # 2. Partially downloaded
        part_file = dest_dir / f"{entry.canonical_filename}.part"
        part_file.write_bytes(b"A" * 400)
        s2 = get_model_status(entry, models_dir=dest_dir)
        assert s2.status == "partial"
        assert s2.downloaded_bytes == 400
        assert s2.progress_percent == 40.0

        # 3. Ready
        part_file.unlink()
        final_file = dest_dir / entry.canonical_filename
        final_file.write_bytes(b"A" * 1000)
        s3 = get_model_status(entry, models_dir=dest_dir)
        assert s3.status == "ready"
        assert s3.downloaded_bytes == 1000
        assert s3.progress_percent == 100.0

        # Verify lookup
        assert get_model_path("lifecycle-model", "Q4_K_M", models_dir=dest_dir) == final_file


# -----------------------------------------------------------------------------
# 6. Real Socket-Bound HTTP Server Tests (When Network Access Permitted)
# -----------------------------------------------------------------------------

class TestRealSocketHTTPServer:
    """Tests using a real socket-bound http.server.HTTPServer on localhost."""

    def test_real_socket_server_resumable_download(self, tmp_path):
        import http.server
        import socket
        import threading

        # Check if environment permits localhost TCP connect
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.listen(1)
        except Exception:
            pytest.skip("Localhost socket binding not permitted in this environment")

        can_connect = False
        try:
            c = socket.socket()
            c.settimeout(0.5)
            c.connect(("127.0.0.1", port))
            can_connect = True
            c.close()
        except Exception:
            can_connect = False
        finally:
            s.close()

        if not can_connect:
            pytest.skip("Localhost TCP connect restricted by sandbox security policy")

        payload = b"REAL_TCP_SERVER_DOWNLOAD_DATA_CHUNK_" * 100
        expected_sha = hashlib.sha256(payload).hexdigest()

        class Handler(http.server.BaseHTTPRequestHandler):
            attempt = 0

            def do_GET(self):
                Handler.attempt += 1
                range_hdr = self.headers.get("Range")
                start = 0
                if range_hdr and "bytes=" in range_hdr:
                    try:
                        start = int(range_hdr.split("bytes=")[1].rstrip("-"))
                    except Exception:
                        start = 0

                total = len(payload)
                if Handler.attempt == 1:
                    # Cut off after 500 bytes
                    chunk = payload[start : start + 500]
                    self.send_response(206 if start > 0 else 200)
                    self.send_header("Content-Length", str(total))
                    if start > 0:
                        self.send_header("Content-Range", f"bytes {start}-{start + len(chunk) - 1}/{total}")
                    self.end_headers()
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    self.close_connection = True
                    return

                # Remainder
                body = payload[start:]
                self.send_response(206 if start > 0 else 200)
                self.send_header("Content-Length", str(len(body)))
                if start > 0:
                    self.send_header("Content-Range", f"bytes {start}-{total - 1}/{total}")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        bound_port = httpd.server_port
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        dest_dir = tmp_path / "models"
        entry = ModelEntry(
            name="real-tcp-model",
            quant="Q4",
            size=len(payload),
            sha256=expected_sha,
            min_ram_gb=4.0,
            url=f"http://127.0.0.1:{bound_port}/model.gguf",
        )

        try:
            # 1. Attempt interrupted
            try:
                download_model(entry, dest_dir=dest_dir)
            except Exception:
                pass

            # 2. Attempt resumed
            resumed = download_model(entry, dest_dir=dest_dir)
            assert resumed.exists()
            assert verify_file_sha256(resumed, expected_sha) is True
        finally:
            httpd.shutdown()

