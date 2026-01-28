#!/usr/bin/env python3
"""Simple API test script for STT Service (no external dependencies)."""

import json
import struct
import sys
import urllib.error
import urllib.request
from io import BytesIO

BASE_URL = "http://localhost:8000"
API_KEY = "dev-test-key"


def make_request(method, path, headers=None, data=None):
    """Make HTTP request and return (status_code, response_data)."""
    url = f"{BASE_URL}{path}"
    headers = headers or {}

    if data and isinstance(data, dict):
        data = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"detail": body}
    except urllib.error.URLError as e:
        raise ConnectionError(f"Could not connect to {BASE_URL}: {e}")


def make_multipart_request(path, headers, files, fields):
    """Make multipart form-data request."""
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    body = BytesIO()

    # Add form fields
    for key, value in fields.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.write(f"{value}\r\n".encode())

    # Add files
    for key, (filename, file_data, content_type) in files.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode()
        )
        body.write(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.write(file_data)
        body.write(b"\r\n")

    body.write(f"--{boundary}--\r\n".encode())

    headers = dict(headers)
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body.getvalue(),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"detail": body}


def test_health():
    """Test health endpoint."""
    print("Testing /health...")
    status, data = make_request("GET", "/health")
    assert status == 200, f"Expected 200, got {status}"
    assert data["status"] == "healthy"
    print(f"  ✓ Health check passed: {data}")


def test_health_ready():
    """Test readiness endpoint."""
    print("Testing /health/ready...")
    status, data = make_request("GET", "/health/ready")
    assert status == 200, f"Expected 200, got {status}"
    assert data["status"] == "ready"
    assert data["database"] == "healthy"
    assert data["redis"] == "healthy"
    assert data["storage"] == "healthy"
    print(f"  ✓ Readiness check passed: {data}")


def test_list_jobs():
    """Test jobs listing endpoint."""
    print("Testing GET /api/v1/jobs...")
    status, data = make_request("GET", "/api/v1/jobs", headers={"X-API-Key": API_KEY})
    assert status == 200, f"Expected 200, got {status}"
    assert "jobs" in data
    assert "total" in data
    print(f"  ✓ Jobs list passed: {data['total']} jobs found")


def test_auth_required():
    """Test that API key is required."""
    print("Testing auth requirement...")
    status, _ = make_request("GET", "/api/v1/jobs")
    assert status == 401, f"Expected 401, got {status}"
    print("  ✓ Auth check passed: 401 returned without API key")


def test_invalid_job():
    """Test getting non-existent job."""
    print("Testing GET /api/v1/jobs/non-existent-uuid...")
    # Use a valid UUID format that doesn't exist
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    status, _ = make_request(
        "GET", f"/api/v1/jobs/{fake_uuid}", headers={"X-API-Key": API_KEY}
    )
    assert status == 404, f"Expected 404, got {status}"
    print("  ✓ Invalid job check passed: 404 returned")


def create_test_wav():
    """Create a minimal WAV file for testing."""
    sample_rate = 8000
    duration = 0.1
    num_samples = int(sample_rate * duration)

    wav_buffer = BytesIO()
    # RIFF header
    wav_buffer.write(b"RIFF")
    wav_buffer.write(struct.pack("<I", 36 + num_samples))
    wav_buffer.write(b"WAVE")
    # fmt chunk
    wav_buffer.write(b"fmt ")
    wav_buffer.write(struct.pack("<I", 16))
    wav_buffer.write(struct.pack("<H", 1))  # PCM
    wav_buffer.write(struct.pack("<H", 1))  # Mono
    wav_buffer.write(struct.pack("<I", sample_rate))
    wav_buffer.write(struct.pack("<I", sample_rate))
    wav_buffer.write(struct.pack("<H", 1))
    wav_buffer.write(struct.pack("<H", 8))
    # data chunk
    wav_buffer.write(b"data")
    wav_buffer.write(struct.pack("<I", num_samples))
    wav_buffer.write(b"\x80" * num_samples)

    return wav_buffer.getvalue()


def test_transcription_with_sample():
    """Test transcription with a sample audio file."""
    print("Testing POST /api/v1/transcribe with sample...")

    wav_data = create_test_wav()
    files = {"audio": ("test.wav", wav_data, "audio/wav")}
    fields = {"config": '{"provider": "gemini", "language": "en"}'}

    status, data = make_multipart_request(
        "/api/v1/transcribe",
        headers={"X-API-Key": API_KEY},
        files=files,
        fields=fields,
    )

    if status == 200:
        print(f"  ✓ Transcription submitted: job_id={data['job_id']}")
        return data["job_id"]
    else:
        # May fail if provider API key not configured
        detail = data.get("detail", str(data))[:200]
        print(f"  ⚠ Transcription returned {status}: {detail}")
        return None


def test_job_progress(job_id):
    """Test job progress endpoint."""
    if not job_id:
        print("Skipping job progress test (no job_id)")
        return

    print(f"Testing GET /api/v1/jobs/{job_id}/progress...")
    status, data = make_request(
        "GET", f"/api/v1/jobs/{job_id}/progress", headers={"X-API-Key": API_KEY}
    )

    if status == 200:
        print(
            f"  ✓ Job progress: status={data['status']}, progress={data['progress_percent']}%"
        )
    else:
        print(f"  ⚠ Progress check returned {status}")


def main():
    """Run all tests."""
    print("=" * 50)
    print("STT Service API Tests")
    print("=" * 50)
    print()

    try:
        test_health()
        test_health_ready()
        test_auth_required()
        test_list_jobs()
        test_invalid_job()
        job_id = test_transcription_with_sample()
        test_job_progress(job_id)

        print()
        print("=" * 50)
        print("All tests passed!")
        print("=" * 50)
        return 0

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return 1
    except ConnectionError as e:
        print(f"\n✗ {e}")
        print("  Make sure the service is running: docker-compose up -d")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
