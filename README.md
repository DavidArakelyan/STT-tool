# STT Service

A robust Speech-to-Text service with multi-provider support, speaker diarization, and specialized optimizations for Armenian language transcription.

![STT Web UI](/Users/darakelyan/.gemini/antigravity/brain/09fdc2e8-7734-4354-b45b-a040a6b5cfb3/stt_ui_demo_1769579442482.webp)

## Key Features

- **Multi-Provider Support**: Integrate with Google Gemini, ElevenLabs, Whisper, and HiSpeech.
- **Smart Chunking**: Automatically splits long audio files for parallel processing and reliable transcription.
- **Speaker Diarization**: Identifies and labels different speakers in the audio.
- **Armenian Language Mode**: Fine-tuned prompts and configurations for high-quality Armenian transcriptions.
- **Fail-Safe Processing**: Real-time progress tracking and the ability to resume jobs from the last successful chunk.
- **Modern Web Interface**: Clean, responsive UI for easy job management and result visualization.

---

## Stand-alone Service Usage (API)

The service provides a comprehensive RESTful API for automation and integration.

### Authentication
Include your API key in the `X-API-Key` header for all requests.
```bash
# Example Header
X-API-Key: your-api-key
```

### 1. Submit a Transcription Job
You can submit a job by uploading a file or providing a URL.

**Via File Upload:**
```bash
curl -X POST "http://localhost:8000/api/v1/transcribe" \
     -H "X-API-Key: your-api-key" \
     -F "audio=@/path/to/audio.mp3" \
     -F 'config={"provider": "gemini", "language": "hy"}'
```

**Via URL:**
```bash
curl -X POST "http://localhost:8000/api/v1/transcribe/url" \
     -H "X-API-Key: your-api-key" \
     -H "Content-Type: application/json" \
     -d '{
       "audio_url": "https://example.com/audio.wav",
       "provider": "gemini",
       "language": "hy"
     }'
```

### 2. Track Progress
Poll the progress endpoint to get real-time status updates.
```bash
curl -H "X-API-Key: your-api-key" \
     "http://localhost:8000/api/v1/jobs/{job_id}/progress?include_chunks=true"
```

### 3. Retrieve Results
Once the status is `completed`, fetch the final transcript.
```bash
curl -H "X-API-Key: your-api-key" \
     "http://localhost:8000/api/v1/jobs/{job_id}/result"
```

---

## Web UI Usage

The Web interface provides a user-friendly way to interact with the service without using the command line.

### Getting Started
1. **Access the UI**: Navigate to `http://localhost:8000` in your browser.
2. **Set API Key**: Enter your API key in the configuration section at the top.

### Creating Jobs
1. **Upload**: Drag and drop an audio file (MP3, WAV, M4A, etc.) into the upload zone.
2. **Configure**: Select your preferred **Provider** (e.g., Gemini) and **Language** (e.g., Armenian).
3. **Start**: Click "Start Transcription".

### Monitoring & Results
- **Active Jobs**: Watch real-time progress as chunks are processed. You can click **Show Chunks** to see the heartbeat of individual segments.
- **Logs**: Click the **Logs** button to see a detailed audit trail of the job's execution.
- **View Result**: Once finished, click **View Result** to see the speaker-labeled transcript.
- **Download**: Click **Download** to save the transcript as a clean `.txt` file named after your original audio.

---

## Developer Setup (Docker)

Initialize the entire stack with a single command:

```bash
docker-compose up -d --build
```

### Services
- **API**: `http://localhost:8000`
- **PostgreSQL**: Internal DB for job state
- **Redis**: Task queue broker
- **MinIO (S3)**: Audio and result storage (`http://localhost:9001` for console)
- **Celery Workers**: Background processing tasks
