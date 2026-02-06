# STT Service Architecture

This document provides a high-level overview of the Speech-to-Text (STT) service architecture, explained simply for developers and students.

## The "Speech Processing Factory" Metaphor

Think of this entire application as a factory. You have a **Front Office** where customers drop off audio tapes, and a **Back Office** where workers process them.

### 1. The Front Office (Frontend)
*   **What it is:** A web page (`frontend/index.html` + `app.js`).
*   **Role:** The reception desk.
*   **How it works:**
    *   **Upload:** Customer drops an audio file here.
    *   **Settings:** Customer chooses which "worker" (Provider) they want (Gemini, HiSpeech, etc.) and what language to use.
    *   **Status Board:** It periodically calls the factory (`polling`) to ask "Is my job done yet?" and shows a progress bar.

### 2. The Factory Manager (API & Orchestrator)
*   **What it is:** A FastAPI application (`src/stt_service/main.py`).
*   **Role:** Receives orders and organizes work.
*   **Key Components:**
    *   **API Routes (`/api/routes`):** The doors. `POST /transcribe` accepts files.
    *   **Orchestrator (`JobOrchestrator`):** The manager.
        1.  Takes the file and saves it safely in the **Storage Room** (MinIO/S3).
        2.  Creates a **Job Card** in the database (`PostgreSQL`) to track everything.
        3.  Puts a **Ticket** in the **Inbox** (Redis Queue) for the workers.

### 3. The Workers (Celery & Chunker)
*   **What it is:** Background Python processes (`src/stt_service/workers`).
*   **Role:** The heavy lifters who actually do the work.
*   **The Workflow:**
    1.  **Pickup:** A worker sees a new job in the Redis Queue.
    2.  **Chunking (`AudioChunker`):** If the file is huge (e.g., a 2-hour meeting), the worker cuts it into smaller 10-minute pieces using `FFmpeg`.
        *   *Smart Stitching:* Cuts at silence points and makes them overlap slightly so no words are cut in half.
    3.  **Outsourcing (`Providers`):** The worker sends each chunk to a powerful external expert (Gemini, OpenAI, HiSpeech).
        *   *Memory:* Tells the expert what was said in the *previous* chunk so they understand the context.
    4.  **Merging (`TranscriptMerger`):** Once all pieces are back, the worker glues the text back together, removing duplicates from the overlapping parts.

### 4. The Brains (Providers)
*   **What it is:** Code that talks to external AIs (`src/stt_service/providers/*`).
*   **Role:** Translators/Transcribers.
*   **Examples:**
    *   **Gemini (`gemini.py`):** Smart, understands context.
    *   **HiSpeech (`hispeech.py`):** Expert in Armenian.
    *   **Whisper (`whisper.py`):** Reliable standard.

### 5. Infrastructure
*   **PostgreSQL:** The filing cabinet for records.
*   **Redis:** The message queue (Inbox).
*   **MinIO/S3:** The warehouse for audio files.

---

## Architecture Diagram

```
+-------------------------------------------------------------+
|                     USER (Student)                          |
+--------------------------+----------------------------------+
                           |
                           v
+--------------------------+----------------------------------+
|                  THE FRONT OFFICE (Frontend)                |
|  [ index.html + app.js ]                                    |
|                                                             |
|  1. "Here is my audio tape (Upload)"                        |
|  2. "I want the generic worker (Gemini)"                    |
|  3. "Is it done yet? (Polling)"                             |
+--------------------------+----------------------------------+
                           |
                           v
+--------------------------+----------------------------------+
|               THE FACTORY MANAGER (API Backend)             |
|  [ FastAPI - main.py ]                                    |
|                                                             |
|  1. Receives audio -> Saves to Storage Room (MinIO)         |
|  2. Writes "Job Card" -> Filing Cabinet (PostgreSQL)        |
|  3. Puts "Task Ticket" -> Inbox Tray (Redis Queue)          |
+-------------------------------------------------------------+
                           |
                           v
+--------------------------+----------------------------------+
|                   THE WORKERS (Background)                  |
|  [ Celery Worker - tasks.py ]                               |
|                                                             |
|  1. Picks up ticket from Inbox (Redis)                      |
|  2. Is file huge? -> Call "The Cutter" (Chunker)            |
|       [ AudioChunker ] -> Splits into 10min pieces          |
|                          (with slight overlap!)             |
|                                                             |
|  3. Needs translation? -> Send to "The Experts" (Provider)  |
|       +---------------------------------------------+       |
|       | [ Gemini ]   [ HiSpeech ]    [ Whisper ]    |       |
|       |   (Smart)      (Armenian)      (Standard)   |       |
|       +---------------------------------------------+       |
|                                                             |
|  4. Glue it back? -> Call "The Gluer" (Merger)              |
|       [ TranscriptMerger ] -> Stitches text together        |
|                                                             |
|  5. Done! -> Update Filing Cabinet (DB)                     |
+-------------------------------------------------------------+
```

## Key Concept: Hybrid Overlapping Stitching

To prevent cut-off words at chunk boundaries (e.g., "Hello wor-" | "-ld"), we use a "Hybrid" approach:

1.  **Overlap:** Each chunk starts 3 seconds earlier than the previous split.
2.  **Context Injection:** We pass the transcript of Chunk N as metadata to Chunk N+1.
3.  **Intelligent Merging:** We use text similarity to find where the overlap matches and glue it seamlessly.

---

## Data Flow Summary

1.  **Job Creation:** `User` -> `Frontend` -> `API` -> `Redis` / `DB` / `MinIO`
2.  **Job Processing:** `Redis` -> `Worker` -> `Chunker` -> `Provider` -> `Merger` -> `DB`
3.  **Result Retrieval:** `User` -> `Frontend` -> `API` (reads DB) -> `User`
