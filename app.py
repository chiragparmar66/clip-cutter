import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

from processing.downloader import download_video, validate_youtube_url
from processing.gemini_analyzer import find_clip_moments
from processing.clipper import cut_clips, get_ffmpeg_path
from processing.watermark import generate_watermark_png

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("clip_cutter.app")

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Job retention TTL in hours
JOB_TTL_HOURS = float(os.environ.get("JOB_TTL_HOURS", "24"))

# In-memory job status store — fine for local/single-instance use.
# job_id -> {"stage": str, "detail": str, "percent": float|None,
#            "done": bool, "error": str|None, "result": dict|None, "created_at": float}
jobs = {}
jobs_lock = threading.Lock()

JOB_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SAFE_FILENAME_REGEX = re.compile(r"^[a-zA-Z0-9_.-]{1,128}\.(mp4|png|srt|webm)$", re.IGNORECASE)


def set_status(job_id: str, **kwargs):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(kwargs)


def cleanup_expired_jobs():
    """Removes output folders and download files older than JOB_TTL_HOURS."""
    cutoff_time = time.time() - (JOB_TTL_HOURS * 3600)
    with jobs_lock:
        expired_ids = [
            jid for jid, jdata in jobs.items()
            if jdata.get("created_at", 0) < cutoff_time
        ]
        for jid in expired_ids:
            jobs.pop(jid, None)

    # Cleanup outputs directory
    if os.path.exists(OUTPUT_DIR):
        for entry in os.listdir(OUTPUT_DIR):
            entry_path = os.path.join(OUTPUT_DIR, entry)
            try:
                if os.path.isdir(entry_path) and os.path.getmtime(entry_path) < cutoff_time:
                    shutil.rmtree(entry_path, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Error cleaning expired output dir {entry_path}: {e}")

    # Cleanup downloads directory
    if os.path.exists(DOWNLOAD_DIR):
        for entry in os.listdir(DOWNLOAD_DIR):
            entry_path = os.path.join(DOWNLOAD_DIR, entry)
            try:
                if os.path.isfile(entry_path) and os.path.getmtime(entry_path) < cutoff_time:
                    os.remove(entry_path)
            except Exception as e:
                logger.warning(f"Error cleaning expired download file {entry_path}: {e}")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    """Healthcheck endpoint reporting system status and dependencies."""
    ffmpeg_bin = get_ffmpeg_path()
    ffmpeg_found = bool(shutil.which(ffmpeg_bin) or os.path.isfile(ffmpeg_bin))
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    gemini_configured = bool(gemini_key and gemini_key != "your_gemini_api_key_here")

    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ffmpeg": {
            "path": ffmpeg_bin,
            "available": ffmpeg_found,
        },
        "gemini": {
            "configured": gemini_configured,
            "model": os.environ.get("GEMINI_MODEL", "gemini-flash-latest"),
        },
    })


def run_job(job_id: str, url: str, max_clips: int, instructions: str, vertical: bool, vertical_mode: str, captions: bool, watermark_text: str):
    """
    Runs the full pipeline in a background thread, updating jobs[job_id]
    as it goes so the frontend can poll for live progress:
      1. Ask Gemini for clip-worthy timestamps AND download the video
         AT THE SAME TIME (they don't depend on each other)
      2. Cut each clip out with ffmpeg (in parallel across clips)
    """
    try:
        analysis_result = {}
        download_result = {}

        def do_analysis():
            try:
                set_status(job_id, stage="analyzing", detail="Asking Gemini to find clip-worthy moments…", percent=None)
                analysis_result["moments"] = find_clip_moments(
                    url, max_clips=max_clips, extra_instructions=instructions,
                    need_captions=captions,
                )
            except Exception as e:
                logger.error(f"Job {job_id} Gemini error: {e}")
                analysis_result["error"] = str(e)

        def on_download_progress(percent, speed_str, eta_str):
            detail = f"Downloading video… {percent:.0f}%"
            if speed_str:
                detail += f" ({speed_str}"
                detail += f", ETA {eta_str})" if eta_str else ")"
            set_status(job_id, stage="downloading", detail=detail, percent=percent)

        def do_download():
            try:
                path, title = download_video(
                    url, DOWNLOAD_DIR, job_id, progress_callback=on_download_progress
                )
                download_result["path"] = path
                download_result["title"] = title
            except Exception as e:
                logger.error(f"Job {job_id} download error: {e}")
                download_result["error"] = str(e)

        set_status(job_id, stage="working", detail="Analyzing with Gemini and downloading video in parallel…")

        t_analysis = threading.Thread(target=do_analysis, name=f"analysis-{job_id}")
        t_download = threading.Thread(target=do_download, name=f"download-{job_id}")
        t_analysis.start()
        t_download.start()
        t_analysis.join()
        t_download.join()

        if "error" in analysis_result:
            set_status(job_id, done=True, error=f"Gemini analysis failed: {analysis_result['error']}")
            return
        if "error" in download_result:
            set_status(job_id, done=True, error=f"Download failed: {download_result['error']}")
            return

        clip_moments = analysis_result.get("moments")
        video_path = download_result["path"]
        video_title = download_result["title"]

        if not clip_moments:
            set_status(job_id, done=True, error="Gemini didn't return any clip suggestions. Try a different video or fewer clips.")
            return

        # 2. Cut clips — progress_callback fires as each clip starts/finishes
        def on_cut_progress(index, total, title):
            set_status(
                job_id,
                stage="cutting",
                detail=f"Cutting clip {index} of {total}: \"{title}\"",
                percent=((index - 1) / total * 100) if total else 0,
            )

        job_output_dir = os.path.join(OUTPUT_DIR, job_id)
        os.makedirs(job_output_dir, exist_ok=True)

        watermark_png = None
        if watermark_text:
            watermark_png = os.path.join(job_output_dir, "_watermark.png")
            generate_watermark_png(watermark_text, watermark_png)

        clips = cut_clips(
            video_path, clip_moments, job_output_dir,
            progress_callback=on_cut_progress,
            vertical=vertical,
            vertical_mode=vertical_mode,
            captions=captions,
            watermark_png=watermark_png,
        )

        if not clips:
            set_status(job_id, done=True, error="No clips could be successfully generated. Check FFmpeg logs.")
            return

        set_status(
            job_id,
            stage="done",
            detail="Done.",
            percent=100,
            done=True,
            result={"video_title": video_title, "clips": clips},
        )
        logger.info(f"Job {job_id} completed successfully with {len(clips)} clips.")

    except Exception as e:
        logger.exception(f"Unhandled exception in job {job_id}: {e}")
        set_status(job_id, done=True, error=str(e))
    finally:
        # Guarantee done=True is set if an unexpected unhandled crash occurred
        with jobs_lock:
            if job_id in jobs and not jobs[job_id].get("done"):
                jobs[job_id]["done"] = True
                if not jobs[job_id].get("error"):
                    jobs[job_id]["error"] = "Job ended unexpectedly."


@app.route("/api/start", methods=["POST"])
def start():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url") or "").strip()

    # Validate YouTube URL
    try:
        url = validate_youtube_url(url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Validate max_clips
    try:
        raw_max = data.get("max_clips") or 12
        max_clips = max(1, min(int(raw_max), 30))
    except (ValueError, TypeError):
        max_clips = 12

    instructions = str(data.get("instructions") or "").strip()[:1000]
    vertical = bool(data.get("vertical"))
    vertical_mode = str(data.get("vertical_mode") or "blur").strip().lower()
    if vertical_mode not in ("blur", "crop"):
        vertical_mode = "blur"

    captions = bool(data.get("captions"))
    watermark_text = str(data.get("watermark_text") or "").strip()[:60]

    # Trigger lightweight cleanup of expired jobs
    try:
        cleanup_expired_jobs()
    except Exception as e:
        logger.warning(f"Error during expired jobs cleanup: {e}")

    job_id = uuid.uuid4().hex[:8]
    with jobs_lock:
        jobs[job_id] = {
            "stage": "queued",
            "detail": "Starting…",
            "percent": None,
            "done": False,
            "error": None,
            "result": None,
            "created_at": time.time(),
        }

    thread = threading.Thread(
        target=run_job,
        args=(job_id, url, max_clips, instructions, vertical, vertical_mode, captions, watermark_text),
        daemon=True,
        name=f"job-{job_id}",
    )
    thread.start()
    logger.info(f"Started job {job_id} for URL: {url}")

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def status(job_id: str):
    if not job_id or not JOB_ID_REGEX.match(job_id):
        return jsonify({"error": "Invalid job_id format"}), 400

    with jobs_lock:
        job = jobs.get(job_id)

    if job is None:
        return jsonify({"error": "Unknown job_id"}), 404

    # Return a copy without internal created_at timestamp
    response_data = dict(job)
    response_data.pop("created_at", None)
    return jsonify(response_data)


@app.route("/clips/<job_id>/<filename>")
def serve_clip(job_id: str, filename: str):
    # Strict validation to prevent path traversal
    if not job_id or not JOB_ID_REGEX.match(job_id):
        return jsonify({"error": "Invalid job_id"}), 400

    if not filename or not SAFE_FILENAME_REGEX.match(filename):
        return jsonify({"error": "Invalid filename"}), 400

    # Ensure filename matches secure_filename and is not hidden
    if filename.startswith((".", "_")) or secure_filename(filename) != filename:
        return jsonify({"error": "Access denied"}), 403

    job_dir = os.path.abspath(os.path.join(OUTPUT_DIR, job_id))
    file_path = os.path.abspath(os.path.join(job_dir, filename))

    # Verify canonical path stays strictly inside job_dir
    if not file_path.startswith(job_dir + os.sep):
        return jsonify({"error": "Access denied"}), 403

    if not os.path.isfile(file_path):
        return jsonify({"error": "Clip not found"}), 404

    return send_from_directory(job_dir, filename, as_attachment=False)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true")

    logger.info(f"Starting Clip Bay on http://{host}:{port} (debug={debug})")
    app.run(host=host, port=port, debug=debug, threaded=True)
