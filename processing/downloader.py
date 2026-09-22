import glob
import logging
import os
import re
import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError, GeoRestrictedError

logger = logging.getLogger("clip_cutter.downloader")

# Cap resolution by default — clips get re-edited anyway, no need to pull a
# huge 4K source file when 1080p is plenty. Override with MAX_HEIGHT env var,
# or set to "" for no cap. Lowering this (e.g. to 720 or 480) is the most
# reliable way to cut download size/time on a slow connection.
MAX_HEIGHT = os.environ.get("MAX_HEIGHT", "480")

YOUTUBE_URL_REGEX = re.compile(
    r"^(https?://)?(www\.|m\.)?(youtube\.com/(watch\?v=|embed/|v/|shorts/)|youtu\.be/)([\w-]{11})([^\s]*)$",
    re.IGNORECASE,
)


def validate_youtube_url(url: str) -> str:
    """Validates that a URL is a syntactically valid YouTube video link."""
    if not url or not isinstance(url, str):
        raise ValueError("No URL provided.")

    trimmed = url.strip()
    if not YOUTUBE_URL_REGEX.match(trimmed):
        # Allow other YouTube domains/parameters if they match general YouTube host
        if "youtube.com" not in trimmed and "youtu.be" not in trimmed:
            raise ValueError(f"Invalid YouTube URL: {trimmed}. Please provide a valid youtube.com or youtu.be link.")

    return trimmed


def _cleanup_partial_files(download_dir: str, job_id: str):
    """Removes leftover incomplete download artifacts for the specified job."""
    for pattern in [f"{job_id}.*", f"{job_id}.*.part", f"{job_id}.*.ytdl"]:
        for filepath in glob.glob(os.path.join(download_dir, pattern)):
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception as e:
                logger.warning(f"Could not remove partial file {filepath}: {e}")


def download_video(url: str, download_dir: str, job_id: str, progress_callback=None):
    """
    Downloads a YouTube video (VOD) using yt-dlp.
    If progress_callback is given, calls it repeatedly as:
      progress_callback(percent: float, speed_str: str, eta_str: str)
    Returns (local_file_path, video_title).
    """
    clean_url = validate_youtube_url(url)
    os.makedirs(download_dir, exist_ok=True)
    output_template = os.path.join(download_dir, f"{job_id}.%(ext)s")

    max_h = os.environ.get("MAX_HEIGHT", MAX_HEIGHT).strip()
    if max_h and max_h.isdigit():
        format_str = f"bv*[height<={max_h}]+ba/b[height<={max_h}]/bv*+ba/b"
    else:
        format_str = "bv*+ba/b"

    def hook(d):
        if not progress_callback:
            return
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total else 0

            speed = d.get("speed")
            speed_str = f"{speed / 1024 / 1024:.1f} MB/s" if speed else ""

            eta = d.get("eta")
            eta_str = f"{eta}s" if eta else ""

            progress_callback(percent, speed_str, eta_str)
        elif status == "finished":
            progress_callback(100, "", "")

    ydl_opts = {
        "format": format_str,
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "progress_hooks": [hook],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            if not info:
                raise RuntimeError("yt-dlp could not retrieve video information.")

            filepath = ydl.prepare_filename(info)
            base, _ = os.path.splitext(filepath)
            mp4_path = base + ".mp4"
            if os.path.exists(mp4_path):
                filepath = mp4_path

            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                raise RuntimeError(f"Downloaded video file was not created or is empty at {filepath}")

            title = info.get("title", "video")

        return filepath, title

    except GeoRestrictedError as e:
        _cleanup_partial_files(download_dir, job_id)
        raise RuntimeError("This video is geo-restricted and cannot be downloaded.") from e
    except ExtractorError as e:
        _cleanup_partial_files(download_dir, job_id)
        raise RuntimeError(f"Failed to extract video: {e}") from e
    except DownloadError as e:
        _cleanup_partial_files(download_dir, job_id)
        msg = str(e)
        if "Private video" in msg:
            raise RuntimeError("Cannot download: This video is private.") from e
        if "Video unavailable" in msg:
            raise RuntimeError("Cannot download: This video is unavailable.") from e
        if "Sign in to confirm" in msg:
            raise RuntimeError("Cannot download: Video is age-restricted or requires authentication.") from e
        raise RuntimeError(f"Video download failed: {msg}") from e
    except Exception as e:
        _cleanup_partial_files(download_dir, job_id)
        raise
