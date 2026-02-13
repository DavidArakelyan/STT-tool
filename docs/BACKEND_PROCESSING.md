# Backend Processing Workflow

## 1. Architecture Overview

*   **API Service (FastAPI)**: Entry point. Handles uploads, validation, security (auth, rate limiting, SSRF, file content checks), and request tracing.
*   **Database (PostgreSQL)**: Source of truth for job status, chunk progress, and metadata.
*   **Storage (MinIO/S3)**: S3-compatible object storage for audio files, chunks, and result JSON.
*   **Message Broker (Redis)**: Task queue for asynchronous processing and rate-limit counters.
*   **Worker Service (Celery)**: Background workers for transcription, webhooks, and periodic cleanup.

## 2. Step-by-Step Flow

### Step 1: Submission (API Layer)
**Files**: `src/stt_service/api/routes/transcription.py`, `src/stt_service/api/dependencies.py`

When a user submits a file via `POST /api/v1/transcribe` (or a URL via `POST /api/v1/transcribe/url`):
1.  **Authentication**: API key is verified using timing-safe comparison (`hmac.compare_digest`).
2.  **Rate Limiting**: A per-API-key counter in Redis enforces the configured RPM limit (default: 10/min). Exceeding the limit returns HTTP 429.
3.  **Validation**:
    *   File extension must be a supported audio or video format.
    *   File size must be under the configured limit (default: 500 MB).
    *   File content is checked against known magic byte signatures to reject renamed non-media files.
    *   For URL submissions, the URL is validated against SSRF blocklists (private IPs, cloud metadata, etc.).
4.  **Creation**: `orchestrator.create_job()` creates a `PENDING` record in the database.
5.  **Upload**: `orchestrator.upload_audio()` streams the file to MinIO.
6.  **Queueing**: `orchestrator.submit_job()` calls `process_transcription_job.delay(job_id)`.
    *   This pushes a message to the **Redis** queue.
    *   The API returns `200 OK` immediately with the `job_id`.

### Step 2: Queueing (Redis)
The job exists as a message in Redis, waiting for an available worker slot. This decouples acceptance from processing.

### Step 3: Worker Pickup (Celery)
**File**: `src/stt_service/workers/celery_app.py`

The Celery worker polls Redis. When a slot is free:
1.  It reserves the task message.
2.  It executes `process_transcription_job` in a new event loop (`run_async`).

### Step 4: Execution (Worker Layer)
**File**: `src/stt_service/workers/tasks.py`

1.  **State Check**: The worker loads the job from the DB. If it's been deleted or cancelled, it aborts immediately.
2.  **Context Binding**: `job_id` is bound to the structured logger for tracing. A per-job log file is created under `logs/jobs/{job_id}/`.
3.  **Download**: Audio is downloaded from MinIO to a temporary local directory.
4.  **Video Extraction** (if applicable): If the file is a video format (MP4, MKV, AVI, etc.), audio is extracted to WAV using `FFmpeg`.
5.  **Normalization**: Audio files are converted to WAV format for consistent processing.
6.  **Chunking** (`AudioChunker`): If the audio exceeds the max chunk duration (default: 5 minutes), it is split into overlapping chunks:
    *   Chunks overlap by 10 seconds to prevent word loss at boundaries.
    *   Chunk records are created in the database for progress tracking.
7.  **Provider Processing**: Each chunk is sent to the configured STT provider (Gemini, wav.am, HiSpeech, ElevenLabs, or Whisper):
    *   **Context Injection**: For chunk N+1, the last few transcript segments from chunk N are passed as context so the provider maintains continuity.
    *   **Retry Logic**: Transient errors (rate limits, timeouts, 5xx) are retried with exponential backoff. Before each retry, the job's cancellation status is checked in the DB.
    *   **Coverage Validation**: After transcription, the worker checks if the provider covered the full chunk duration. If a gap > 15 seconds is detected, the chunk is retried up to 2 additional times.
    *   Each chunk result is saved as a JSON file for debugging (`logs/jobs/{job_id}/chunk-XXXX.json`).
8.  **Merging** (`TranscriptMerger`): Once all chunks are transcribed, segments are merged into a single transcript. Overlapping regions are deduplicated using text similarity matching.
9.  **Error Classification**: If the job fails, the exception is classified into a user-friendly error code (e.g., `rate_limited`, `timeout`, `invalid_audio`, `auth_error`, `provider_unavailable`, `quota_exceeded`) and stored in the DB alongside the raw error message.

### Step 5: Completion
1.  **Result**: Final transcript JSON is saved to MinIO under `jobs/{job_id}/result/transcript.json`.
2.  **Status**: DB updated to `COMPLETED` with a timestamp.
3.  **Webhook** (optional): If a `webhook_url` was provided, a `send_webhook` task is dispatched to POST the result to the callback URL (with its own retry logic).
4.  **Ack**: Redis message acknowledged and removed.

### Step 6: Lifecycle Management

*   **Stale Job Recovery**: On API startup, any `PROCESSING`/`UPLOADED` jobs older than 30 minutes are automatically marked `FAILED` with a descriptive message.
*   **Job Cleanup**: A daily Celery Beat task deletes completed/failed jobs older than the retention period (default: 7 days) from both S3 and PostgreSQL.
*   **Cancel / Retry**: Users can cancel running jobs or retry failed ones via the API. The worker checks cancellation status before each retry attempt.

## 3. Component Interaction Table

| Component | Responsibility | Timing |
| :--- | :--- | :--- |
| **FastAPI** | Authenticates, validates, rate-limits, uploads to MinIO, enqueues in Redis | Immediate (T=0) |
| **Redis** | Persists task messages, stores rate-limit counters | Waiting Room |
| **Celery Worker** | Downloads, extracts audio, chunks, transcribes, merges, classifies errors | Asynchronous |
| **PostgreSQL** | Tracks state changes, stores results and error codes | Continuous |
| **MinIO/S3** | Stores original files, chunks, and result JSON | Persistent |
| **Celery Beat** | Triggers daily job cleanup | Periodic (24h) |
