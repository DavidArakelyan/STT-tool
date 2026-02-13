# Architecture Review & Refactoring

This document describes the security hardening, architecture improvements, and operational
enhancements applied to the STT Service during the February 2026 review cycle.

---

## Table of Contents

1. [Security Hardening](#1-security-hardening)
2. [Architecture Gap Fixes](#2-architecture-gap-fixes)
3. [Future Enhancements](#3-future-enhancements)

---

## 1. Security Hardening

> Commit `59b6211` + 108 new tests

### 1.1 API Key Authentication with Timing-Safe Comparison

**Problem:** The original API key check used `==`, which is susceptible to timing side-channel
attacks where an attacker can infer key length and characters by measuring response times.

**Fix:** Replaced the equality check with `hmac.compare_digest()`, which performs constant-time
comparison regardless of where the mismatch occurs.

**File:** `src/stt_service/api/dependencies.py`

```python
# Before
if x_api_key not in api_keys:
    raise HTTPException(...)

# After
if not any(hmac.compare_digest(x_api_key, key) for key in api_keys):
    raise HTTPException(...)
```

### 1.2 CORS Hardening

**Problem:** CORS was configured with `allow_methods=["*"]` and `allow_headers=["*"]`, which
is overly permissive and could expose the API to cross-origin abuse.

**Fix:** Restricted allowed methods to `GET, POST, DELETE, OPTIONS` and allowed headers to
`X-API-Key, Content-Type` -- the only ones the service actually needs.

**File:** `src/stt_service/main.py`

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type"],
)
```

### 1.3 SSRF Protection on User-Supplied URLs

**Problem:** The `/api/v1/transcribe/url` endpoint and the webhook URL field accepted arbitrary
URLs, including internal network addresses (`127.0.0.1`, `10.x.x.x`, `169.254.169.254`, etc.).
An attacker could use this to probe internal infrastructure or access cloud metadata services.

**Fix:** Created `src/stt_service/utils/url_validation.py` with a `validate_external_url()`
function that:

1. Rejects non-HTTP(S) schemes
2. Resolves the hostname to IP addresses
3. Checks every resolved IP against a blocklist of private/reserved networks:
   - `127.0.0.0/8` (loopback)
   - `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (RFC 1918)
   - `169.254.0.0/16` (link-local / cloud metadata)
   - `0.0.0.0/8`, IPv6 loopback, unique-local, link-local

Applied as a Pydantic field validator on both `audio_url` and `webhook_url` fields in the
request schemas.

**Files:**
- `src/stt_service/utils/url_validation.py` (new)
- `src/stt_service/api/schemas/transcription.py`

### 1.4 File Content Validation (Magic Bytes)

**Problem:** File uploads were only validated by extension. An attacker could upload arbitrary
data (e.g., a ZIP bomb or executable) with an `.mp3` extension, and it would be stored in S3
and passed to the transcription provider.

**Fix:** Created `src/stt_service/utils/file_validation.py` with an `is_valid_media_file()`
function that checks the first 12 bytes of every upload against known audio/video magic byte
signatures:

| Signature | Format |
|-----------|--------|
| `RIFF` | WAV / AVI |
| `ID3` | MP3 (ID3v2) |
| `\xff\xfb`, `\xff\xf3`, etc. | MP3 (MPEG frame sync) |
| `\xff\xf1`, `\xff\xf9` | AAC (ADTS) |
| `ftyp` (offset 4) | MP4 / M4A / MOV / 3GP |
| `fLaC` | FLAC |
| `OggS` | OGG / Opus |
| `\x1a\x45\xdf\xa3` | WebM / MKV (EBML) |
| `FLV` | FLV |
| `\x30\x26\xb2\x75...` | ASF (WMA/WMV) |
| `\x00\x00\x01\xba` | MPEG-PS |
| `\x00\x00\x01\xb3` | MPEG-1 |
| `\x47` | MPEG-TS |

Applied to both `/transcribe` (file upload) and `/transcribe/url` (URL download) endpoints.

**Files:**
- `src/stt_service/utils/file_validation.py` (new)
- `src/stt_service/api/routes/transcription.py`

### 1.5 Test Suite

Added 108 tests covering:

- API key authentication (missing key, invalid key, valid key, no-auth dev mode)
- SSRF validation (private IPs, loopback, cloud metadata, valid external URLs)
- File validation (all supported formats, invalid data, truncated headers)
- Rate limiting
- Error classification
- URL validation edge cases

---

## 2. Architecture Gap Fixes

These changes address operational issues that cause data loss, make debugging harder, or leave
the system in inconsistent states.

### 2.1 Stale Job Recovery on Startup

**Problem:** When the API container or Celery worker restarts (crash, deploy, OOM), any jobs
in `PROCESSING` or `UPLOADED` state are left stranded forever. Users see jobs stuck at
"Processing..." indefinitely with no way to recover.

**Fix:** On application startup (in the FastAPI `lifespan` handler), run a bulk UPDATE that
marks all `PROCESSING`/`UPLOADED` jobs older than 30 minutes as `FAILED` with a clear message:
*"Job timed out -- likely interrupted by a service restart. Please resubmit."*

This gives users a clear signal and lets them retry.

**Files:**
- `src/stt_service/db/repositories/job.py` -- added `fail_stale_jobs(stale_minutes=30)`
- `src/stt_service/main.py` -- called in `lifespan()` startup

```python
async def fail_stale_jobs(self, stale_minutes: int = 30) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    stmt = (
        update(Job)
        .where(
            Job.status.in_([JobStatus.PROCESSING, JobStatus.UPLOADED]),
            Job.updated_at < cutoff,
        )
        .values(
            status=JobStatus.FAILED,
            error_message="Job timed out -- likely interrupted by a service restart. Please resubmit.",
        )
    )
    result = await self.session.execute(stmt)
    await self.session.flush()
    return result.rowcount
```

### 2.2 Request-ID Middleware for Correlation Tracking

**Problem:** When a user reports an issue, there was no way to correlate their API request with
specific log lines in the backend. Multiple concurrent requests produce interleaved logs that
are impossible to separate.

**Fix:** Added an HTTP middleware that:

1. Reads `X-Request-ID` from the incoming request header (if provided by the client)
2. Generates a UUID if none is provided
3. Binds the request ID to `structlog.contextvars` so every log line within that request
   automatically includes `request_id=...`
4. Returns the request ID in the `X-Request-ID` response header

Works with the existing `merge_contextvars` processor already configured in the logging setup.

**File:** `src/stt_service/main.py`

```python
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
```

### 2.3 Per-API-Key Rate Limiting

**Problem:** The `/api/v1/transcribe` endpoints had no rate limiting. A single client
(malicious or misconfigured) could flood the system with hundreds of transcription jobs,
overwhelming the Celery queue, burning provider API quota, and degrading service for everyone.

**Fix:** Added a Redis-based sliding-window rate limiter as a FastAPI dependency:

- Uses `INCR` + `EXPIRE` on a per-API-key Redis key
- Configurable via `RATE_LIMIT_RPM` env var (default: 10 requests/minute)
- Returns HTTP 429 with a clear message when exceeded
- **Fail-open design:** if Redis is unavailable, requests are allowed through with a warning log

Applied to both `/transcribe` (file upload) and `/transcribe/url` endpoints.

**Files:**
- `src/stt_service/config.py` -- added `rate_limit_rpm: int = 10`
- `src/stt_service/api/dependencies.py` -- added `check_rate_limit()` dependency and `RateLimit` type alias
- `src/stt_service/api/routes/transcription.py` -- added `_rate_limit: RateLimit` to both endpoints

### 2.4 Structured Error Responses with Error Classification

**Problem:** When a transcription job failed, users saw raw exception messages like
`"google.api_core.exceptions.ResourceExhausted: 429 ..."` or
`"httpx.ReadTimeout: timed out"`. These are meaningless to end users and leak internal details.

**Fix:** Created an error classifier (`src/stt_service/utils/error_classifier.py`) that maps
exceptions to user-friendly `(error_code, message)` tuples via pattern matching:

| Error Code | Triggers | User Message |
|------------|----------|--------------|
| `rate_limited` | `429`, `ResourceExhausted`, `RateLimitError` | "Provider is temporarily rate-limiting requests..." |
| `timeout` | `timeout`, `timed out`, `deadline exceeded` | "The transcription request timed out..." |
| `invalid_audio` | `invalid audio`, `unsupported format`, `corrupt` | "Audio file could not be processed..." |
| `auth_error` | `401`, `403`, `unauthorized`, `forbidden` | "Authentication with provider failed..." |
| `provider_unavailable` | `503`, `502`, `connection refused` | "Provider is currently unavailable..." |
| `quota_exceeded` | `quota`, `billing`, `402` | "Provider API quota exceeded..." |
| `unknown` | Everything else | Original exception message |

Added `error_code` column to the `Job` database model (with an `ALTER TABLE` migration in
`init_db()` for existing databases) and exposed it in the API response schema.

**Files:**
- `src/stt_service/utils/error_classifier.py` (new)
- `src/stt_service/db/models.py` -- added `error_code: Mapped[str | None]`
- `src/stt_service/db/session.py` -- added `ALTER TABLE` migration
- `src/stt_service/db/repositories/job.py` -- `update_status()` accepts `error_code`
- `src/stt_service/workers/tasks.py` -- uses `classify_error()` in error handler
- `src/stt_service/api/schemas/transcription.py` -- added `error_code` field
- `src/stt_service/api/routes/jobs.py` -- returns `error_code` in responses

### 2.5 Automated Job Cleanup (Retention Policy)

**Problem:** Completed and failed jobs accumulate indefinitely in both PostgreSQL and S3.
Over weeks/months of use, this leads to growing storage costs and slower database queries.

**Fix:** Added a Celery Beat periodic task that runs once per day and:

1. Queries jobs in `COMPLETED` or `FAILED` state older than the retention period
2. Lists and deletes all associated S3 files (original audio, chunks, result JSON)
3. Deletes the database record (cascades to chunk records)
4. Logs summary: `"Expired jobs cleaned up" deleted_jobs=N s3_keys_deleted=M`

Configurable via `JOB_RETENTION_DAYS` env var (default: 7 days, set to 0 to disable).

**Files:**
- `src/stt_service/config.py` -- added `job_retention_days: int = 7`
- `src/stt_service/db/repositories/job.py` -- added `get_expired_jobs()`
- `src/stt_service/workers/tasks.py` -- added `cleanup_expired_jobs` task
- `src/stt_service/workers/celery_app.py` -- registered in beat schedule and task routing

```python
beat_schedule={
    "cleanup-expired-jobs": {
        "task": "stt_service.workers.tasks.cleanup_expired_jobs",
        "schedule": 86400.0,  # once per day
    },
},
```

---

## 3. Future Enhancements

The following improvements were identified during the review but are not critical at current
scale. They are documented here for future implementation when the service sees higher load or
larger files.

### 3.1 Circuit Breaker for Provider APIs

**What:** Wrap provider API calls with a circuit breaker pattern. After N consecutive failures,
temporarily stop sending requests to that provider and return a fast-fail error instead of
waiting for timeouts.

**Why deferred:** At current usage levels with a single provider, the retry logic and error
classification provide sufficient resilience. The circuit breaker adds complexity that isn't
justified until the service handles multiple concurrent users or switches between providers
automatically.

**Implementation sketch:**
- Track failure count per provider in Redis
- After 5 consecutive failures, open the circuit for 60 seconds
- During open state, immediately return `provider_unavailable` error
- After cooldown, allow one probe request (half-open state)

### 3.2 System Logs Endpoint Memory Optimization

**What:** The `GET /api/v1/jobs/{id}/system-logs` endpoint reads the entire application log
file into memory (`f.readlines()`) to filter lines by job ID.

**Risk:** As the log file grows (hundreds of MB to GB), each call to this endpoint loads
everything into RAM. Multiple concurrent requests could OOM the container.

**Implementation sketch:**
- Read the file in fixed-size blocks from the end (e.g., 64KB at a time)
- Parse lines in reverse until the requested `limit` is reached
- Alternative: use the per-job log files (`logs/jobs/{job_id}/`) directly, which are already
  scoped and bounded

### 3.3 Download Bundle Streaming

**What:** The `GET /api/v1/jobs/{id}/download-bundle` endpoint loads the full audio file
(up to 500MB) into memory, then creates a ZIP in memory. Peak memory per request can
reach ~1GB+.

**Risk:** A few concurrent downloads of large files could exhaust container memory.

**Implementation sketch:**
- Stream the audio directly from S3 to the ZIP writer
- Use `StreamingResponse` with a generator that yields ZIP chunks
- Or: pre-generate the ZIP in the worker and store it in S3, then redirect to a presigned URL

### 3.4 Bounded Bulk Delete

**What:** The `DELETE /api/v1/jobs` endpoint iterates through up to 1000 jobs synchronously,
making individual S3 delete calls for each one. With many jobs, this can exceed HTTP timeouts.

**Risk:** The request may time out before all jobs are deleted, leaving partial state.

**Implementation sketch:**
- Move bulk deletion to a background Celery task
- Return immediately with a status like `{"message": "Deletion started", "task_id": "..."}`
- Or: paginate with a smaller batch size (e.g., 50) and use S3 batch delete

---

## Summary of Files Changed

| File | Section | Change |
|------|---------|--------|
| `src/stt_service/api/dependencies.py` | 1.1, 2.3 | Timing-safe auth, rate limiter |
| `src/stt_service/api/routes/transcription.py` | 1.4, 2.3 | File validation, rate limit dep |
| `src/stt_service/api/routes/jobs.py` | 2.4 | Error code in responses |
| `src/stt_service/api/schemas/transcription.py` | 1.3, 2.4 | SSRF validator, error_code field |
| `src/stt_service/config.py` | 2.3, 2.5 | rate_limit_rpm, job_retention_days |
| `src/stt_service/db/models.py` | 2.4 | error_code column |
| `src/stt_service/db/repositories/job.py` | 2.1, 2.4, 2.5 | fail_stale_jobs, error_code, get_expired_jobs |
| `src/stt_service/db/session.py` | 2.4 | ALTER TABLE migration |
| `src/stt_service/main.py` | 1.2, 2.1, 2.2 | CORS, stale recovery, request-ID |
| `src/stt_service/utils/error_classifier.py` | 2.4 | New: exception-to-error-code mapper |
| `src/stt_service/utils/file_validation.py` | 1.4 | New: magic byte checker |
| `src/stt_service/utils/url_validation.py` | 1.3 | New: SSRF prevention |
| `src/stt_service/workers/tasks.py` | 2.4, 2.5 | Error classification, cleanup task |
| `src/stt_service/workers/celery_app.py` | 2.5 | Beat schedule, task routing |
| `frontend/index.html` | -- | UI: renamed "Gemini API" tab to "API" |
| `frontend/app.js` | -- | UI: broadened log filter for all providers |
