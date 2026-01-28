# Backend Processing Workflow

## 1. Architecture Overview

*   **API Service (FastAPI)**: Synchronous entry point, handles uploads and validation.
*   **Database (PostgreSQL)**: Source of truth for job status and metadata.
*   **Storage (MinIO)**: S3-compatible object storage for audio files.
*   **Message Broker (Redis)**: Task queue for asynchronous processing.
*   **Worker Service (Celery)**: Background worker for heavy transcription tasks.

## 2. Step-by-Step Flow

### Step 1: Submission (API Layer)
**File**: `src/stt_service/api/routes/transcription.py`

When a user submits a file via `POST /api/v1/transcribe`:
1.  **Validation**: API validates file format and size.
2.  **Creation**: `orchestrator.create_job()` creates a `PENDING` record in the database.
3.  **Upload**: `orchestrator.upload_audio()` streams the file to MinIO.
4.  **Queueing**: `orchestrator.submit_job()` calls `process_transcription_job.delay(job_id)`.
    *   This pushes a message to the **Redis** list (queue).
    *   The API returns `200 OK` immediately with the `job_id`.

### Step 2: Queueing (Redis)
The job exists as a message in Redis, waiting for an available worker slot. This decouples acceptance from processing.

### Step 3: Worker Pickup (Celery)
**File**: `src/stt_service/workers/celery_app.py`

The Celery worker (`stt-tool-worker-1`) polls Redis. When a slot is free:
1.  It reserves the task message.
2.  It spawns a child process to execute `process_transcription_job`.

### Step 4: Execution (Worker Layer)
**File**: `src/stt_service/workers/tasks.py`

1.  **Zombie Check**: The worker checks the DB. If the job is deleted/cancelled, it aborts immediately.
2.  **Context**: `job_id` is bound to the logger for tracing.
3.  **Download**: Audio is downloaded from MinIO to a temporary local path.
4.  **Chunking**: `AudioChunker` analyzes duration and splits long files using `ffmpeg`.
5.  **Processing**:
    *   Iterates through chunks.
    *   Sends audio to **Gemini API**.
    *   **Retry Logic**: Handles Rate Limits (429) with exponential backoff. Checks DB cancellation status before every retry.
6.  **Merging**: Stitches text segments into a final transcript.

### Step 5: Completion
1.  **Result**: Final JSON is saved to MinIO.
2.  **Status**: DB updated to `COMPLETED`.
3.  **Ack**: Redis message acknowledged and removed.

## 3. Component Interaction Table

| Component | Responsibility | Timing |
| :--- | :--- | :--- |
| **FastAPI** | Validates, Uploads to MinIO, Enqueues in Redis | Immediate (T=0) |
| **Redis** | Persists task message | Waiting Room |
| **Celery** | Downloads, Transcribes, Saves Result | Asynchronous |
| **PostgreSQL**| Tracks state changes | Continuous |
