import glob
import logging
import os
import re

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError, GeoRestrictedError


logger = logging.getLogger("clip_cutter.downloader")


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
        if (
            "youtube.com" not in trimmed
            and "youtu.be" not in trimmed
        ):
            raise ValueError(
                f"Invalid YouTube URL: {trimmed}. "
                "Please provide a valid youtube.com or youtu.be link."
            )

    return trimmed


def _cleanup_partial_files(download_dir: str, job_id: str):
    """Removes leftover incomplete download artifacts."""

    for pattern in [
        f"{job_id}.*",
        f"{job_id}.*.part",
        f"{job_id}.*.ytdl",
    ]:
        for filepath in glob.glob(
            os.path.join(download_dir, pattern)
        ):
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)

            except Exception as e:
                logger.warning(
                    f"Could not remove partial file "
                    f"{filepath}: {e}"
                )


def download_video(
    url: str,
    download_dir: str,
    job_id: str,
    progress_callback=None,
):
    """
    Downloads a YouTube video using yt-dlp.

    Returns:
        (local_file_path, video_title)
    """

    clean_url = validate_youtube_url(url)

    logger.info(
        f"Starting yt-dlp download for job {job_id}: "
        f"{clean_url}"
    )

    os.makedirs(
        download_dir,
        exist_ok=True,
    )

    output_template = os.path.join(
        download_dir,
        f"{job_id}.%(ext)s",
    )

    max_h = os.environ.get(
        "MAX_HEIGHT",
        MAX_HEIGHT,
    ).strip()

    if max_h and max_h.isdigit():

        format_str = (
            f"bv*[height<={max_h}]+ba/"
            f"b[height<={max_h}]/"
            f"bv*+ba/b"
        )

    else:
        format_str = "bv*+ba/b"

    logger.info(
        f"yt-dlp format: {format_str}"
    )

    def hook(d):

        if not progress_callback:
            return

        status = d.get("status")

        if status == "downloading":

            total = (
                d.get("total_bytes")
                or d.get("total_bytes_estimate")
            )

            downloaded = d.get(
                "downloaded_bytes",
                0,
            )

            percent = (
                downloaded / total * 100
                if total
                else 0
            )

            speed = d.get("speed")

            speed_str = (
                f"{speed / 1024 / 1024:.1f} MB/s"
                if speed
                else ""
            )

            eta = d.get("eta")

            eta_str = (
                f"{eta}s"
                if eta
                else ""
            )

            progress_callback(
                percent,
                speed_str,
                eta_str,
            )

        elif status == "finished":

            progress_callback(
                100,
                "",
                "",
            )

    ydl_opts = {
        "format": format_str,
        "outtmpl": output_template,
        "merge_output_format": "mp4",

        # TEMPORARY DIAGNOSTIC MODE
        # We need the real YouTube/yt-dlp error in Render logs.
        "quiet": False,
        "no_warnings": False,

        "noplaylist": True,
        "progress_hooks": [hook],

        # Send yt-dlp logs through our application logger.
        "logger": logger,
    }

    logger.info(
        "yt-dlp options configured. "
        "Calling extract_info()..."
    )

    try:

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:

            info = ydl.extract_info(
                clean_url,
                download=True,
            )

            logger.info(
                f"yt-dlp extract_info() completed "
                f"for job {job_id}"
            )

            if not info:
                raise RuntimeError(
                    "yt-dlp could not retrieve video information."
                )

            filepath = ydl.prepare_filename(info)

            base, _ = os.path.splitext(
                filepath
            )

            mp4_path = base + ".mp4"

            if os.path.exists(mp4_path):
                filepath = mp4_path

            if (
                not os.path.exists(filepath)
                or os.path.getsize(filepath) == 0
            ):
                raise RuntimeError(
                    "Downloaded video file was not created "
                    f"or is empty at {filepath}"
                )

            title = info.get(
                "title",
                "video",
            )

            logger.info(
                f"Video downloaded successfully: "
                f"{filepath}"
            )

        return filepath, title

    except GeoRestrictedError as e:

        logger.error(
            f"yt-dlp GeoRestrictedError for job "
            f"{job_id}: {e}"
        )

        _cleanup_partial_files(
            download_dir,
            job_id,
        )

        raise RuntimeError(
            "This video is geo-restricted and cannot be downloaded."
        ) from e

    except ExtractorError as e:

        logger.error(
            f"yt-dlp ExtractorError for job "
            f"{job_id}: {e}"
        )

        _cleanup_partial_files(
            download_dir,
            job_id,
        )

        raise RuntimeError(
            f"Failed to extract video: {e}"
        ) from e

    except DownloadError as e:

        _cleanup_partial_files(
            download_dir,
            job_id,
        )

        msg = str(e)

        # IMPORTANT:
        # Log the REAL yt-dlp error before converting it.
        logger.error(
            f"FULL yt-dlp DownloadError for job "
            f"{job_id}: {msg}"
        )

        if "Private video" in msg:

            raise RuntimeError(
                "Cannot download: This video is private."
            ) from e

        if "Video unavailable" in msg:

            raise RuntimeError(
                "Cannot download: This video is unavailable."
            ) from e

        # DO NOT convert this into "age restricted".
        # We want to see exactly what YouTube returned.
        if "Sign in to confirm" in msg:

            raise RuntimeError(
                f"YouTube authentication/bot verification "
                f"required. Raw yt-dlp error: {msg}"
            ) from e

        raise RuntimeError(
            f"Video download failed: {msg}"
        ) from e

    except Exception as e:

        logger.error(
            f"Unexpected downloader error for job "
            f"{job_id}: "
            f"{type(e).__name__}: {e}"
        )

        _cleanup_partial_files(
            download_dir,
            job_id,
        )

        raise