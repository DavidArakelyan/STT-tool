# STT Service Architecture

This document provides a high-level overview of the Speech-to-Text (STT) service architecture, explained simply for developers and students.

## The "Speech Processing Factory" Metaphor

Think of this entire application as a factory. You have a **Front Office** where customers drop off audio tapes, and a **Back Office** where workers process them.

### 1. The Front Office (Frontend)
*   **What it is:** A web page (`frontend/index.html` + `app.js`).
*   **Role:** The reception desk.
*   **How it works:**
    *   **Upload:** Customer drops an audio or video file here.
    *   **Settings:** Customer chooses which "worker" (Provider) they want (Gemini, wav.am, HiSpeech, etc.) and what language to use.
    *   **Status Board:** It periodically calls the factory (`polling`) to ask "Is my job done yet?" and shows a progress bar.

### 2. The Factory Manager (API & Orchestrator)
*   **What it is:** A FastAPI application (`src/stt_service/main.py`).
*   **Role:** Receives orders, validates them, and organizes work.
*   **Key Components:**
    *   **API Routes (`/api/routes`):** The doors. `POST /transcribe` accepts files, `POST /transcribe/url` accepts URLs.
    *   **Security Gate:** Validates API keys (timing-safe), enforces rate limits (per-key, via Redis), checks file content against known audio/video signatures (magic bytes), and blocks SSRF attempts on user-supplied URLs.
    *   **Orchestrator (`JobOrchestrator`):** The manager.
        1.  Takes the file and saves it safely in the **Storage Room** (MinIO/S3).
        2.  Creates a **Job Card** in the database (`PostgreSQL`) to track everything.
        3.  Puts a **Ticket** in the **Inbox** (Redis Queue) for the workers.
    *   **Request-ID Middleware:** Tags every request with a unique ID (`X-Request-ID`) for end-to-end tracing across logs.

### 3. The Workers (Celery & Chunker)
*   **What it is:** Background Python processes (`src/stt_service/workers`).
*   **Role:** The heavy lifters who actually do the work.
*   **The Workflow:**
    1.  **Pickup:** A worker sees a new job in the Redis Queue.
    2.  **Video Extraction:** If the file is a video (MP4, MKV, etc.), audio is extracted first using `FFmpeg`.
    3.  **Chunking (`AudioChunker`):** If the audio is longer than 5 minutes, the worker cuts it into smaller pieces using `FFmpeg`.
        *   *Smart Stitching:* Chunks overlap by 10 seconds so no words are cut at boundaries.
    4.  **Outsourcing (`Providers`):** The worker sends each chunk to a powerful external expert (Gemini, wav.am, HiSpeech, etc.).
        *   *Context Injection:* Tells the expert what was said in the *previous* chunk so they understand the context.
        *   *Coverage Check:* After each chunk, verifies the provider actually transcribed the full audio. If a large gap is detected, the chunk is retried.
    5.  **Retry Logic:** Rate limits (429), timeouts, and transient failures are retried with exponential backoff. The job's cancellation status is checked before every retry.
    6.  **Merging (`TranscriptMerger`):** Once all pieces are back, the worker glues the text back together, removing duplicates from the overlapping parts.
    7.  **Error Classification:** If the job fails, the exception is mapped to a user-friendly error code (`rate_limited`, `timeout`, `invalid_audio`, `auth_error`, `provider_unavailable`, `quota_exceeded`) and stored alongside the raw error.
*   **Periodic Tasks:**
    *   **Job Cleanup:** A daily Celery Beat task deletes completed/failed jobs older than the retention period (default: 7 days) from both S3 and PostgreSQL.
*   **Startup Recovery:** When the API starts, any jobs stuck in `PROCESSING` or `UPLOADED` for over 30 minutes are automatically marked as `FAILED` so users can resubmit.

### 4. The Brains (Providers)
*   **What it is:** Code that talks to external AIs (`src/stt_service/providers/*`).
*   **Role:** Translators/Transcribers.
*   **Available Providers:**
    *   **Gemini (`gemini.py`):** Google's multimodal model. Context-aware, supports structured JSON output with speaker diarization.
    *   **wav.am (`wav.py`):** Armenian-optimized transcription service with native diarization.
    *   **HiSpeech (`hispeech.py`):** Armenian language specialist.
    *   **ElevenLabs (`elevenlabs.py`):** High-quality speech processing.
    *   **Whisper (`whisper.py`):** OpenAI's general-purpose model.
*   **Provider Interface:** All providers implement `BaseSTTProvider` with a standard `transcribe(audio_data, config)` method, making them interchangeable. New providers can be registered via `ProviderFactory.register_provider()`.

### 5. Infrastructure
*   **PostgreSQL:** The filing cabinet for job records, chunk progress, and results.
*   **Redis:** The message queue (Celery broker) and rate-limit counter store.
*   **MinIO/S3:** The warehouse for audio files, chunk files, and result JSON.

---

## Architecture Diagram

```
+-------------------------------------------------------------+
|                        USER                                  |
+--------------------------+----------------------------------+
                           |
                           v
+--------------------------+----------------------------------+
|                  THE FRONT OFFICE (Frontend)                |
|  [ index.html + app.js ]                                    |
|                                                             |
|  1. "Here is my audio/video file (Upload)"                  |
|  2. "I want this provider (Gemini / wav.am / HiSpeech)"    |
|  3. "Is it done yet? (Polling)"                             |
+--------------------------+----------------------------------+
                           |
                           v
+--------------------------+----------------------------------+
|               THE FACTORY MANAGER (API Backend)             |
|  [ FastAPI - main.py ]                                      |
|                                                             |
|  Security: API key auth | Rate limiter | SSRF blocker       |
|            File magic-byte validation                       |
|  Tracing:  X-Request-ID on every request                    |
|                                                             |
|  1. Receives audio -> Validates -> Saves to MinIO           |
|  2. Writes "Job Card" -> PostgreSQL                         |
|  3. Puts "Task Ticket" -> Redis Queue                       |
+-------------------------------------------------------------+
                           |
                           v
+--------------------------+----------------------------------+
|                   THE WORKERS (Background)                  |
|  [ Celery Worker - tasks.py ]                               |
|                                                             |
|  1. Picks up ticket from Redis                              |
|  2. Video? -> Extract audio (FFmpeg)                        |
|  3. Long audio? -> Split into 5-min chunks                  |
|       [ AudioChunker ] -> with 10s overlap                  |
|                                                             |
|  4. Transcribe -> Send to Provider                          |
|       +---------------------------------------------+       |
|       | [ Gemini ]  [ wav.am ]  [ HiSpeech ]        |       |
|       | [ ElevenLabs ]          [ Whisper ]          |       |
|       +---------------------------------------------+       |
|                                                             |
|  5. Merge chunks -> [ TranscriptMerger ]                    |
|  6. Save result -> MinIO + PostgreSQL                       |
|  7. Webhook? -> Notify callback URL                         |
+-------------------------------------------------------------+
```

## Key Concept: Hybrid Overlapping Stitching

To prevent cut-off words at chunk boundaries (e.g., "Hello wor-" | "-ld"), we use a "Hybrid" approach:

1.  **Overlap:** Each chunk overlaps with the previous one by 10 seconds.
2.  **Context Injection:** We pass the last few transcript segments of Chunk N as metadata to Chunk N+1, so the provider has continuity.
3.  **Intelligent Merging:** The `TranscriptMerger` uses text similarity to find where the overlap matches and glues it seamlessly, removing duplicates.

---

## Data Flow Summary

1.  **Job Creation:** `User` -> `Frontend` -> `API` (validate + rate-limit) -> `Redis` / `DB` / `MinIO`
2.  **Job Processing:** `Redis` -> `Worker` -> `Chunker` -> `Provider` (with retries) -> `Merger` -> `DB` / `MinIO`
3.  **Result Retrieval:** `User` -> `Frontend` -> `API` (reads DB) -> `User`
4.  **Webhook (optional):** `Worker` -> `POST` callback URL with result
5.  **Cleanup (daily):** `Celery Beat` -> delete expired jobs from `DB` + `MinIO`
