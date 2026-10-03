import base64
import glob
import logging
import os
import re

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError, GeoRestrictedError


logger = logging.getLogger("clip_cutter.downloader")

MAX_HEIGHT = os.environ.get("MAX_HEIGHT", "480")
COOKIES_FILE = "/tmp/youtube_cookies.txt"


YOUTUBE_URL_REGEX = re.compile(
    r"^(https?://)?"
    r"(www\.|m\.)?"
    r"(youtube\.com/(watch\?v=|embed/|v/|shorts/)|youtu\.be/)"
    r"([\w-]{11})([^\s]*)$",
    re.IGNORECASE,
)


def _prepare_cookies():
    """
    Decode YOUTUBE_COOKIES_B64 from the Render environment
    and create a temporary Netscape cookies file.
    """

    cookies_b64 = os.environ.get(
        "YOUTUBE_COOKIES_B64",
        "",
    ).strip()

    if not cookies_b64:
        logger.warning(
            "YOUTUBE_COOKIES_B64 is not configured."
        )
        return None

    try:
        cookie_data = base64.b64decode(
            cookies_b64,
            validate=True,
        )

        if not cookie_data:
            raise ValueError(
                "Decoded cookie file is empty."
            )

        with open(
            COOKIES_FILE,
            "wb",
        ) as cookie_file:
            cookie_file.write(cookie_data)

        # Restrict permissions because this file contains
        # authentication/session information.
        os.chmod(
            COOKIES_FILE,
            0o600,
        )

        logger.info(
            "YouTube cookies loaded successfully."
        )

        return COOKIES_FILE

    except Exception as e:
        logger.error(
            "Failed to decode YOUTUBE_COOKIES_B64: "
            f"{type(e).__name__}: {e}"
        )
        return None


def validate_youtube_url(url: str) -> str:
    if not url or not isinstance(url, str):
        raise ValueError(
            "No URL provided."
        )

    trimmed = url.strip()

    if not YOUTUBE_URL_REGEX.match(trimmed):
        if (
            "youtube.com" not in trimmed
            and "youtu.be" not in trimmed
        ):
            raise ValueError(
                f"Invalid YouTube URL: {trimmed}. "
                "Please provide a valid "
                "youtube.com or youtu.be link."
            )

    return trimmed


def _cleanup_partial_files(
    download_dir: str,
    job_id: str,
):
    patterns = [
        f"{job_id}.*",
        f"{job_id}.*.part",
        f"{job_id}.*.ytdl",
    ]

    for pattern in patterns:
        for filepath in glob.glob(
            os.path.join(
                download_dir,
                pattern,
            )
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
    clean_url = validate_youtube_url(url)

    logger.info(
        f"Starting YouTube download for job "
        f"{job_id}: {clean_url}"
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

    # ---------------------------------------------------------
    # Format selection
    #
    # Prefer an already-merged format within MAX_HEIGHT.
    # If unavailable, fall back to best available format.
    # Finally, allow yt-dlp's best video+audio combination.
    # ---------------------------------------------------------

    if max_h and max_h.isdigit():
        format_str = (
            f"best[height<={max_h}]/"
            f"best/"
            f"bv*+ba/b"
        )
    else:
        format_str = (
            "best/"
            "bv*+ba/b"
        )

    logger.info(
        f"yt-dlp format: {format_str}"
    )

    # ---------------------------------------------------------
    # Prepare YouTube cookies
    # ---------------------------------------------------------

    cookies_file = _prepare_cookies()

    if cookies_file:
        logger.info(
            "yt-dlp will use authenticated "
            "YouTube cookies."
        )
    else:
        logger.warning(
            "yt-dlp will run without YouTube cookies."
        )

    # ---------------------------------------------------------
    # Progress hook
    # ---------------------------------------------------------

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

            if total:
                percent = (
                    downloaded / total
                ) * 100
            else:
                percent = 0

            speed = d.get("speed")

            if speed:
                speed_str = (
                    f"{speed / 1024 / 1024:.1f} MB/s"
                )
            else:
                speed_str = ""

            eta = d.get("eta")

            if eta:
                eta_str = f"{eta}s"
            else:
                eta_str = ""

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

    # ---------------------------------------------------------
    # yt-dlp configuration
    # ---------------------------------------------------------

    ydl_opts = {
        "format": format_str,

        "outtmpl": output_template,

        "merge_output_format": "mp4",

        "quiet": False,

        "verbose": True,

        "no_warnings": False,

        "noplaylist": True,

        "progress_hooks": [
            hook
        ],

        "logger": logger,
    }

    # ---------------------------------------------------------
    # Add cookies when available
    # ---------------------------------------------------------

    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    logger.info(
        "Starting yt-dlp extraction for job "
        f"{job_id}..."
    )

    # ---------------------------------------------------------
    # Download
    # ---------------------------------------------------------

    try:

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            logger.info(
                "Calling yt-dlp extract_info()..."
            )

            info = ydl.extract_info(
                clean_url,
                download=True,
            )

            logger.info(
                "yt-dlp extract_info() completed "
                f"for job {job_id}"
            )

            if not info:
                raise RuntimeError(
                    "yt-dlp could not retrieve "
                    "video information."
                )

            # -------------------------------------------------
            # Determine downloaded filename
            # -------------------------------------------------

            filepath = ydl.prepare_filename(
                info
            )

            base, _ = os.path.splitext(
                filepath
            )

            mp4_path = (
                base + ".mp4"
            )

            if os.path.exists(
                mp4_path
            ):
                filepath = mp4_path

            # -------------------------------------------------
            # Verify downloaded file
            # -------------------------------------------------

            if (
                not os.path.exists(filepath)
                or os.path.getsize(filepath) == 0
            ):
                raise RuntimeError(
                    "Downloaded video file was not "
                    "created or is empty at "
                    f"{filepath}"
                )

            title = info.get(
                "title",
                "video",
            )

            logger.info(
                "Video downloaded successfully: "
                f"{filepath}"
            )

            logger.info(
                f"Video title: {title}"
            )

        return filepath, title

    # ---------------------------------------------------------
    # Geo restriction
    # ---------------------------------------------------------

    except GeoRestrictedError as e:

        logger.error(
            "yt-dlp GeoRestrictedError "
            f"for job {job_id}: {e}"
        )

        _cleanup_partial_files(
            download_dir,
            job_id,
        )

        raise RuntimeError(
            "This video is geo-restricted "
            "and cannot be downloaded."
        ) from e

    # ---------------------------------------------------------
    # Extraction error
    # ---------------------------------------------------------

    except ExtractorError as e:

        logger.error(
            "yt-dlp ExtractorError "
            f"for job {job_id}: {e}"
        )

        _cleanup_partial_files(
            download_dir,
            job_id,
        )

        raise RuntimeError(
            f"Failed to extract video: {e}"
        ) from e

    # ---------------------------------------------------------
    # Download error
    # ---------------------------------------------------------

    except DownloadError as e:

        _cleanup_partial_files(
            download_dir,
            job_id,
        )

        msg = str(e)

        logger.error(
            "FULL yt-dlp DownloadError "
            f"for job {job_id}: {msg}"
        )

        if "Private video" in msg:

            raise RuntimeError(
                "Cannot download: "
                "This video is private."
            ) from e

        if "Video unavailable" in msg:

            raise RuntimeError(
                "Cannot download: "
                "This video is unavailable."
            ) from e

        if (
            "Sign in to confirm" in msg
            or "not a bot" in msg
            or "authentication" in msg.lower()
            or "LOGIN_REQUIRED" in msg
        ):

            raise RuntimeError(
                "YouTube authentication/bot "
                "verification required. "
                f"Raw yt-dlp error: {msg}"
            ) from e

        if (
            "Requested format is not available"
            in msg
        ):

            raise RuntimeError(
                "YouTube did not provide a "
                "compatible video format for "
                "the current client/cookies. "
                f"Raw yt-dlp error: {msg}"
            ) from e

        if "HTTP Error 403" in msg:

            raise RuntimeError(
                "YouTube rejected the media "
                "request with HTTP 403. "
                f"Raw yt-dlp error: {msg}"
            ) from e

        raise RuntimeError(
            f"Video download failed: {msg}"
        ) from e

    # ---------------------------------------------------------
    # Unexpected error
    # ---------------------------------------------------------

    except Exception as e:

        logger.error(
            "Unexpected downloader error "
            f"for job {job_id}: "
            f"{type(e).__name__}: {e}"
        )

        _cleanup_partial_files(
            download_dir,
            job_id,
        )

        raise