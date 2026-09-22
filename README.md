# Clip Bay (Clip Cutter)

> **AI-assisted video clipping tool**: Paste a YouTube VOD link, let Google Gemini detect the funniest and best highlight moments, download the source video, and cut clean, ready-to-publish clips with FFmpeg — including 9:16 vertical conversions, burned-in subtitles, and watermarks.

---

## Features

- 🧠 **AI Highlight Detection**: Uses Google Gemini (`gemini-flash-latest` or your choice of model) to watch YouTube links directly and detect self-contained, high-energy, or funny moments.
- ⚡ **Concurrent Pipeline**: Gemini analysis and yt-dlp video downloading run simultaneously in background worker threads.
- ✂️ **High-Speed Clipping**: Fast FFmpeg stream copying (with automatic fallback to frame-accurate re-encoding when needed) and multi-core parallel slicing.
- 📱 **9:16 Vertical Formatting**:
  - **Blurred Background**: Keeps the original 16:9 frame centered over a zoomed, blurred 9:16 background (the standard TikTok / Shorts / Reels look).
  - **Center Crop**: Fast 9:16 center crop.
- 💬 **Burned-in Styled Subtitles**: Transcribes speech into styled subtitles burned directly onto the video.
- 🏷️ **Custom Branding / Watermark**: Generates an overlay pill with customizable text and opacity.
- 📊 **Real-Time Progress**: Polling-based progress bar and status updates for downloading and cutting stages.
- 🔒 **Hardened Security**: Protected against path traversal, command injection, and arbitrary file access.
- 🐳 **Production & Cloud Ready**: Fully containerized with Docker, Gunicorn, and configurable port support.

---

## Architecture

```
                                  ┌─────────────────────────────┐
                                  │   Browser UI (HTML/JS)      │
                                  └──────────────┬──────────────┘
                                                 │ POST /api/start
                                                 ▼
                                  ┌─────────────────────────────┐
                                  │    Flask / Gunicorn App     │
                                  └──────┬───────────────┬──────┘
                                         │               │
                     ┌───────────────────┘               └───────────────────┐
                     ▼                                                       ▼
        ┌─────────────────────────┐                             ┌─────────────────────────┐
        │     Google Gemini       │                             │         yt-dlp          │
        │   (Content Analysis)    │                             │    (Video Downloader)   │
        └────────────┬────────────┘                             └────────────┬────────────┘
                     │ Returns clip timestamps                               │ Saves source MP4
                     └───────────────────┬───────────────┬───────────────────┘
                                         ▼               ▼
                                  ┌─────────────────────────────┐
                                  │       FFmpeg Clipper        │
                                  │   - Slice & Cut             │
                                  │   - 9:16 Blur / Crop        │
                                  │   - Subtitle Burn-in (SRT)  │
                                  │   - Watermark Overlay (PIL) │
                                  └──────────────┬──────────────┘
                                                 │
                                                 ▼
                                  ┌─────────────────────────────┐
                                  │   Clips (outputs/<job_id>)  │
                                  └─────────────────────────────┘
```

---

## Requirements

- **Python**: 3.10, 3.11, 3.12, or 3.13
- **FFmpeg**: Required for cutting and formatting video clips.
- **Google Gemini API Key**: Free tier or paid key from [Google AI Studio](https://aistudio.google.com/apikey).

---

## FFmpeg Resolution Strategy

The application automatically locates FFmpeg in the following order:
1. `FFMPEG_PATH` environment variable (if explicitly defined in `.env`).
2. Bundled local builds in the project root (e.g. `ffmpeg-*/bin/ffmpeg.exe` or `bin/ffmpeg`).
3. System `PATH` (`ffmpeg` on Linux/macOS/Windows).

### Installing FFmpeg
- **Windows**: Download from [gyan.dev / ffmpeg.org](https://www.gyan.dev/ffmpeg/builds/) and extract into PATH or place in the project root.
- **macOS**: `brew install ffmpeg`
- **Linux (Ubuntu/Debian)**: `sudo apt-get update && sudo apt-get install -y ffmpeg`

---

## Quick Start (Local Setup)

### 1. Clone & Navigate
```bash
git clone <repo-url>
cd clip-cutter
```

### 2. Set Up Virtual Environment
```bash
python -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS / Linux:
source venv/bin/activate
```

### 3. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env` and configure your API key:
```bash
cp .env.example .env
```
Edit `.env`:
```ini
GEMINI_API_KEY=your_actual_gemini_api_key
```

### 5. Run the Application

#### Development Server
```bash
python app.py
```
Open **[http://localhost:5000](http://localhost:5000)** in your browser.

#### Production Server (Gunicorn on Linux/macOS)
```bash
gunicorn --bind 0.0.0.0:5000 --workers 2 --threads 4 --timeout 600 app:app
```

---

## Configuration Reference (`.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | *(Required)* | Google Gemini API key from AI Studio |
| `GEMINI_MODEL` | `gemini-flash-latest` | Gemini model name for video analysis |
| `GEMINI_MAX_RETRIES`| `3` | Maximum retries for transient API errors |
| `MAX_HEIGHT` | `480` | Downloader resolution cap (e.g., 480, 720, 1080) |
| `STREAM_COPY` | `1` | `1` for ultrafast cuts, `0` for frame-accurate re-encoding |
| `MAX_PARALLEL_CUTS` | `min(CPU, 8)` | Max concurrent FFmpeg slice processes |
| `FFMPEG_PATH` | *(Auto)* | Custom absolute path to FFmpeg binary |
| `PORT` | `5000` | HTTP port |
| `HOST` | `0.0.0.0` | Bind host address |
| `FLASK_DEBUG` | `0` | `1` for debug mode, `0` for production |
| `JOB_TTL_HOURS` | `24` | Auto-cleanup threshold for temporary media |

---

## Docker Deployment

Build and run the containerized application:

### Build Docker Image
```bash
docker build -t clip-bay .
```

### Run Container
```bash
docker run -d \
  -p 5000:5000 \
  -e GEMINI_API_KEY="your_api_key_here" \
  --name clip-bay \
  clip-bay
```

---

## Cloud Deployment Guide

Clip Bay is ready for deployment to any Linux container hosting service (e.g. Railway, Render, Fly.io, Cloud Run, VPS).

### Key Deployment Considerations:
1. **Request Timeouts**: Video analysis and downloading can take 30–120 seconds. The Gunicorn configuration uses `--timeout 600`.
2. **Ephemeral Disk Storage**: `downloads/` and `outputs/` are written to local disk. For cloud platforms with ephemeral storage, ensure your container instance has at least 2GB of disk space.
3. **Environment Secrets**: Set `GEMINI_API_KEY` securely in your cloud provider's environment variables dashboard.

---

## Automated Tests

Run the full automated test suite:
```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

### Test Coverage:
- `test_api.py`: Route validation, status polling, security and path traversal protection.
- `test_gemini_analyzer.py`: Markdown fence stripping, JSON parsing, timestamp sanitization, and caption validation.
- `test_downloader.py`: URL syntax validation, error recovery, and yt-dlp handling.
- `test_clipper.py`: Timestamp conversion, SRT subtitle generation, filename sanitization, and FFmpeg binary resolution.
- `test_watermark.py`: PIL watermark generation, opacity, and font fallback.

---

## Troubleshooting

- **"GEMINI_API_KEY is not configured"**: Make sure `.env` exists and contains a valid key from Google AI Studio.
- **FFmpeg not found**: Ensure FFmpeg is installed or set `FFMPEG_PATH` in `.env`.
- **"Private video / Video unavailable"**: Ensure the video is public and accessible in your region.
- **Slow downloading**: Lower `MAX_HEIGHT=480` in `.env` to download a lower resolution source file.
