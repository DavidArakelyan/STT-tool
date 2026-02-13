# Chunk Boundary Transcription Loss — Root Cause Analysis

## Problem

Whole sentences or significant portions of audio are missing from the final transcript at **chunk boundaries** — the start/end of consecutive chunks. The issue was first observed around minute 15–20 of a 65-minute recording.

## Architecture Overview

```mermaid
graph LR
    A[Audio File] --> B[Chunker]
    B --> C[Chunk 0<br>0:00-5:00]
    B --> D[Chunk 1<br>4:50-10:00]
    B --> E[Chunk N<br>...]
    C --> F[Gemini API]
    D --> G[Gemini API]
    E --> H[Gemini API]
    F --> I[Merger]
    G --> I
    H --> I
    I --> J[Final Transcript]
```

## Investigation: Confirmed Gap in Job `2cc47fc3`

Analyzing the chunk-level transcripts from a 65-minute recording (14 chunks at ~5 min each), we found:

| Boundary | Chunk 3 last segment (abs) | Chunk 4 first segment (abs) | Gap |
|---|---|---|---|
| chunk-0003 / chunk-0004 | ends at **1184.8s** | starts at **1214.4s** | **29.5s LOST** |

All other 12 boundaries had healthy ~5s overlaps with no gaps.

**What happened:** Chunk 4 audio covers 1179.9s–1480.1s (300s), but Gemini returned no segments for the first 34.5 seconds of that audio. Its first segment starts at local time 34.5s instead of ~0s. The 5-second overlap only covered 1179.9–1184.9s, so it could not bridge a 29.5s gap.

**Key evidence from logs:**
- `finish_reason: STOP` (not MAX_TOKENS) — Gemini chose to stop, it wasn't truncated
- Token utilization was ~10% of the 32768 limit — plenty of capacity remained
- The prompt explicitly said "Transcribe ALL audio content from the very beginning of this clip (timestamp 0.0)"
- Gemini ignored this instruction and started transcribing at 34.5s

---

## Root Causes Identified

### Round 1 (commit `0c612c7`)

#### Root Cause 1: Prompt instructed Gemini to SKIP overlap content

**File:** `src/stt_service/providers/gemini.py`

The original prompt said:
```
"CRITICAL: DO NOT REPEAT the context provided above. Start transcribing ONLY the new audio."
```

The audio chunk physically contains overlapping audio from the previous chunk. This instruction caused Gemini to aggressively skip the first several seconds (sometimes far more than the overlap region), because it couldn't determine exactly which audio corresponded to the context text.

**Fix (commit `0c612c7`):** Replaced with an instruction to transcribe ALL audio and let the merger handle deduplication:
```
"IMPORTANT: The audio may start with content already captured in the context above —
this is intentional overlap for continuity. Transcribe ALL audio content
from the very beginning of this clip (timestamp 0.0). The system will
automatically handle any overlap during merging."
```

#### Root Cause 2: Overlap was too short (3 seconds)

**Fix (commit `0c612c7`):** Increased from 3.0s to 5.0s.

#### Root Cause 3: No visibility into merger deduplication

**Fix (commit `0c612c7`):** Added `logger.warning` for truncated overlapping segments.

### Round 2 (current fixes)

Despite the Round 1 fixes, Gemini still occasionally skips large portions of chunk audio. The prompt change reduced the frequency but did not eliminate the behavior. Four additional root causes were identified:

#### Root Cause 4: No detection of untranscribed audio gaps

**Files:** `src/stt_service/core/merger.py`, `src/stt_service/workers/tasks.py`

The `_validate_chunk_completeness` method checked for:
- Suspiciously short transcripts (< 100 chars for > 60s audio)
- Last segment missing punctuation
- Fallback regex parsing

It **never** checked:
- Whether the first segment starts near 0.0s (would have caught the 34.5s skip)
- Whether the last segment's end time is close to the chunk duration

Similarly, the worker accepted whatever the provider returned with no coverage validation or retry.

#### Root Cause 5: Silence search window grows unboundedly for later chunks

**File:** `src/stt_service/core/chunker.py` lines 251–252

```python
search_start = target_end * 0.8    # BUG
search_end = target_end * 1.1      # BUG
```

These use multiplicative factors on the **absolute** timestamp. The search window widens with each chunk:

| Chunk | target_end | Window width |
|---|---|---|
| 0 | 300s | 90s |
| 5 | 1780s | 534s |
| 12 | 3840s | 1152s |

For late chunks, a silence point many minutes away from the target could be selected, producing chunks of unexpected length.

#### Root Cause 6: Overlap still insufficient at 5 seconds

The chunk-0003/0004 gap was 29.5 seconds. Even with the Round 1 increase to 5s overlap, the gap was far too large to bridge.

---

## Fixes Implemented

### Fix 1: Coverage gap detection + automatic retry (HIGH priority)

**File:** `src/stt_service/workers/tasks.py`

Added `_check_coverage_gap()` — after each chunk is transcribed, measures the largest untranscribed gap by comparing segment timestamps against the known chunk duration:
- **Start gap:** `first_segment.start_time` — audio skipped at the beginning
- **End gap:** `chunk.duration - last_segment.end_time` — audio not covered at the end

If the gap exceeds 15 seconds, the chunk is retried up to 2 additional times. The retry keeps the best result (smallest gap). If the gap persists after all retries, an error is logged and the best available result is used.

```
# Log signals to watch for:
"Coverage gap detected, retrying chunk"     — retry triggered
"Coverage gap resolved after retry"         — retry succeeded
"Coverage gap persists after retries"       — all retries failed
```

### Fix 2: Merger-level gap validation (HIGH priority)

**File:** `src/stt_service/core/merger.py`

Added two new checks to `_validate_chunk_completeness`:
- **Check 4:** First segment starts > 15s into the chunk → logs ERROR "Provider skipped audio at chunk start"
- **Check 5:** Last segment ends > 15s before chunk duration → logs ERROR "Provider stopped early before chunk end"

These provide post-hoc visibility even if the worker retry didn't fully resolve the gap.

### Fix 3: Fixed silence search window calculation (MEDIUM priority)

**File:** `src/stt_service/core/chunker.py`

Changed from absolute scaling to relative offset:
```python
# Before (window grows with absolute position):
search_start = target_end * 0.8
search_end = target_end * 1.1

# After (consistent window width):
search_start = target_end - 0.2 * max_chunk_duration
search_end = target_end + 0.1 * max_chunk_duration
```

Window is now a consistent ~90s wide (for 300s chunks) regardless of position in the audio.

### Fix 4: Increased overlap to 10 seconds

**Files:** `src/stt_service/config.py`, `.env`

Doubled from 5.0s to 10.0s. This increases the safety margin at boundaries. On its own it cannot cover a 34.5s Gemini skip, which is why Fix 1 (retry) is the primary defense.

---

## Settings History

| Setting | Original | Round 1 | Round 2 (current) |
|---|---|---|---|
| `overlap_duration` | 3.0s | 5.0s | **10.0s** |
| `overlap_similarity_threshold` | 0.7 | 0.8 | 0.8 |
| Gemini prompt | "DO NOT REPEAT" | "Transcribe ALL" | "Transcribe ALL" |
| Coverage gap detection | none | none | **15s threshold + 2 retries** |
| Merger gap validation | none | none | **15s threshold + ERROR log** |
| Silence search window | `target * 0.8 / 1.1` | `target * 0.8 / 1.1` | **`target ± relative offset`** |

---

## Verification Plan

### Log Monitoring
After reprocessing audio with these fixes:
1. Check job logs for `"Coverage gap detected"` / `"Coverage gap resolved"` messages
2. Check for `"Provider skipped audio at chunk start"` / `"Provider stopped early"` in merger output
3. Compare chunk-level JSON files — verify first segment starts near 0.0s and last segment ends near chunk duration

### Manual Verification
- Re-process the 65-minute recording that exhibited the 29.5s gap
- Compare the chunk-0003/0004 boundary in the new output
- Verify no new gaps appear at other boundaries
