import glob
import logging
import os
import re

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError, GeoRestrictedError


logger = logging.getLogger("clip_cutter.downloader")


MAX_HEIGHT = os.environ.get("MAX_HEIGHT", "480")


YOUTUBE_URL_REGEX = re.compile(
    r"^(https?://)?"
    r"(www\.|m\.)?"
    r"(youtube\.com/(watch\?v=|embed/|v/|shorts/)|youtu\.be/)"
    r"([\w-]{11})([^\s]*)$",
    re.IGNORECASE,
)


def validate_youtube_url(url: str) -> str:
    """
    Validate a YouTube URL.
    """

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
    """
    Remove incomplete download files after a failed download.
    """

    patterns = [
        f"{job_id}.*",
        f"{job_id}.*.part",
        f"{job_id}.*.ytdl",
    ]

    for pattern in patterns:
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
    Download a YouTube video using yt-dlp.

    Returns:
        tuple[str, str]:
            (downloaded_file_path, video_title)
    """

    clean_url = validate_youtube_url(url)

    logger.info(
        f"Starting YouTube download "
        f"for job {job_id}: {clean_url}"
    )

    os.makedirs(
        download_dir,
        exist_ok=True,
    )

    output_template = os.path.join(
        download_dir,
        f"{job_id}.%(ext)s",
    )

    # ---------------------------------------------------------
    # FORMAT
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # PROGRESS HOOK
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
    # YT-DLP OPTIONS
    # ---------------------------------------------------------

    ydl_opts = {
        "format": format_str,

        "outtmpl": output_template,

        "merge_output_format": "mp4",

        "quiet": False,

        "no_warnings": False,

        "noplaylist": True,

        "progress_hooks": [
            hook
        ],

        "logger": logger,

        # -----------------------------------------------------
        # YouTube client configuration
        # -----------------------------------------------------
        #
        # mweb is required for the PO-token provider.
        #
        "extractor_args": {

            "youtube": {

                "player_client": [
                    "mweb",
                ],

            },

            # -------------------------------------------------
            # bgutil PO-token HTTP provider
            # -------------------------------------------------
            #
            # The bgutil server runs inside the same container
            # on port 4416.
            #
            "youtubepot-bgutilhttp": {

                "base_url":
                    "http://127.0.0.1:4416",

            },
        },
    }

    logger.info(
        "yt-dlp configured with bgutil PO-token provider "
        "at http://127.0.0.1:4416"
    )

    # ---------------------------------------------------------
    # DOWNLOAD
    # ---------------------------------------------------------

    try:

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:

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
            # Find downloaded file
            # -------------------------------------------------

            filepath = ydl.prepare_filename(info)

            base, _ = os.path.splitext(
                filepath
            )

            mp4_path = (
                base + ".mp4"
            )

            if os.path.exists(mp4_path):
                filepath = mp4_path

            # -------------------------------------------------
            # Validate file
            # -------------------------------------------------

            if (
                not os.path.exists(filepath)
                or os.path.getsize(filepath) == 0
            ):
                raise RuntimeError(
                    "Downloaded video file was not "
                    f"created or is empty at {filepath}"
                )

            title = info.get(
                "title",
                "video",
            )

            logger.info(
                "Video downloaded successfully: "
                f"{filepath}"
            )

        return filepath, title

    # ---------------------------------------------------------
    # GEO RESTRICTION
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
    # EXTRACTOR ERROR
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
    # DOWNLOAD ERROR
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

        # -----------------------------------------------------
        # Private video
        # -----------------------------------------------------

        if "Private video" in msg:

            raise RuntimeError(
                "Cannot download: "
                "This video is private."
            ) from e

        # -----------------------------------------------------
        # Video unavailable
        # -----------------------------------------------------

        if "Video unavailable" in msg:

            raise RuntimeError(
                "Cannot download: "
                "This video is unavailable."
            ) from e

        # -----------------------------------------------------
        # YouTube bot/authentication check
        # -----------------------------------------------------

        if (
            "Sign in to confirm" in msg
            or "not a bot" in msg
            or "authentication" in msg.lower()
        ):

            raise RuntimeError(
                "YouTube authentication/bot "
                "verification required. "
                f"Raw yt-dlp error: {msg}"
            ) from e

        # -----------------------------------------------------
        # Generic download error
        # -----------------------------------------------------

        raise RuntimeError(
            f"Video download failed: {msg}"
        ) from e

    # ---------------------------------------------------------
    # UNKNOWN ERROR
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