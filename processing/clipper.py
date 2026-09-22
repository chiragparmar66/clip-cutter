import glob
import logging
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("clip_cutter.clipper")

# Stream copy (no re-encode) is dramatically faster than re-encoding, at the
# cost of cut points snapping to the nearest keyframe (~1-2s imprecision).
# Fine for raw clips you're re-editing yourself. Set to "0" to force
# re-encoding for frame-accurate cuts instead (slower).
USE_STREAM_COPY = os.environ.get("STREAM_COPY", "1") != "0"

# How many ffmpeg processes to run at once. Stream-copy is lightweight, so
# scaling with available CPU cores (capped at 8) works well on most machines.
MAX_PARALLEL_CUTS = int(os.environ.get("MAX_PARALLEL_CUTS", min(os.cpu_count() or 4, 8)))


def get_ffmpeg_path() -> str:
    """
    Resolves the FFmpeg executable across different environments:
      1. FFMPEG_PATH environment variable (if explicitly set)
      2. Bundled executable in project directory (e.g., ffmpeg-*-essentials_build/bin/ffmpeg.exe)
      3. System PATH fallback (via shutil.which)
      4. Default "ffmpeg"
    """
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    # Check for bundled local builds in the project root
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    bundled_patterns = [
        os.path.join(project_root, "ffmpeg-*", "bin", "ffmpeg.exe"),
        os.path.join(project_root, "ffmpeg-*", "bin", "ffmpeg"),
        os.path.join(project_root, "bin", "ffmpeg.exe"),
        os.path.join(project_root, "bin", "ffmpeg"),
        os.path.join(project_root, "ffmpeg.exe"),
        os.path.join(project_root, "ffmpeg"),
    ]
    for pattern in bundled_patterns:
        matches = glob.glob(pattern)
        if matches and os.path.isfile(matches[0]):
            return os.path.abspath(matches[0])

    # Check system PATH
    system_ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if system_ffmpeg:
        return system_ffmpeg

    return "ffmpeg"


def _run_ffmpeg_cmd(cmd: list, description: str = "FFmpeg command"):
    """Runs an FFmpeg command list, extracting stderr on failure for clean debugging."""
    try:
        return subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        stderr_text = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else "Unknown error"
        logger.error(f"{description} failed (code {e.returncode}): {stderr_text}")
        raise RuntimeError(f"{description} failed: {stderr_text}") from e


def _sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\s-]", "", name).strip()
    name = re.sub(r"[\s]+", "_", name)
    return name[:60] if name else "clip"


def _to_seconds(timestamp) -> float:
    """Accepts HH:MM:SS, MM:SS, or raw seconds (float/int/str) and returns seconds as float."""
    if isinstance(timestamp, (int, float)):
        return max(0.0, float(timestamp))

    if not isinstance(timestamp, str):
        raise ValueError(f"Invalid timestamp format: {timestamp}")

    parts = timestamp.strip().split(":")
    numeric_parts = []
    for p in parts:
        try:
            numeric_parts.append(float(p))
        except ValueError:
            raise ValueError(f"Invalid numeric timestamp part: {p}")

    while len(numeric_parts) < 3:
        numeric_parts.insert(0, 0.0)
    h, m, s = numeric_parts
    return max(0.0, h * 3600.0 + m * 60.0 + s)


def _seconds_to_srt_time(seconds: float) -> str:
    seconds = max(seconds, 0)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        s += 1
        ms -= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(captions: list, clip_start_sec: float, clip_duration: float, srt_path: str) -> bool:
    """
    Writes an SRT file with clip-relative timestamps (Gemini gives captions
    on the original video's absolute clock, so we shift everything back by
    clip_start_sec). Returns True if any usable caption lines were written.
    """
    if not isinstance(captions, list):
        return False

    idx = 1
    with open(srt_path, "w", encoding="utf-8") as f:
        for cap in captions:
            if not isinstance(cap, dict):
                continue
            cap_start, cap_end = cap.get("start"), cap.get("end")
            text = (cap.get("text") or "").strip()
            if cap_start is None or cap_end is None or not text:
                continue

            try:
                rel_start = _to_seconds(cap_start) - clip_start_sec
                rel_end = _to_seconds(cap_end) - clip_start_sec
            except Exception:
                continue

            rel_start = max(0.0, min(rel_start, clip_duration))
            rel_end = max(0.0, min(rel_end, clip_duration))
            if rel_end <= rel_start:
                continue

            f.write(f"{idx}\n")
            f.write(f"{_seconds_to_srt_time(rel_start)} --> {_seconds_to_srt_time(rel_end)}\n")
            f.write(f"{text}\n\n")
            idx += 1

    return idx > 1


def _escape_filter_path(path: str) -> str:
    """
    ffmpeg's filtergraph syntax treats ':' as a special character, which
    collides head-on with Windows drive letters (C:\\...). Converting
    backslashes to forward slashes and escaping the colon is the standard
    fix so the subtitles filter can find the file on Windows.
    """
    p = os.path.abspath(path).replace("\\", "/")
    p = p.replace(":", "\\:")
    p = p.replace("'", r"\'")
    return p


def _apply_overlays(input_path: str, output_path: str, srt_path: str = None, watermark_png: str = None):
    """
    Burns captions (from srt_path) and/or a watermark image onto input_path,
    writing the result to output_path. Re-encoding is unavoidable here.
    """
    if not srt_path and not watermark_png:
        shutil.copy(input_path, output_path)
        return

    ffmpeg_bin = get_ffmpeg_path()
    inputs = ["-i", input_path]
    if watermark_png:
        inputs += ["-i", watermark_png]

    filter_chain = []
    video_label = "0:v"

    if srt_path:
        srt_escaped = _escape_filter_path(srt_path)
        style = (
            "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
            "Alignment=2,MarginV=40"
        )
        filter_chain.append(f"[{video_label}]subtitles='{srt_escaped}':force_style='{style}'[sub]")
        video_label = "sub"

    if watermark_png:
        filter_chain.append(f"[{video_label}][1:v]overlay=W-w-24:H-h-24[wm]")
        video_label = "wm"

    cmd = [ffmpeg_bin, "-y"] + inputs + [
        "-filter_complex", ";".join(filter_chain),
        "-map", f"[{video_label}]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac",
        output_path,
    ]
    _run_ffmpeg_cmd(cmd, "Apply overlays")


def _cut_one(video_path: str, start_sec: float, duration: float, output_path: str):
    ffmpeg_bin = get_ffmpeg_path()
    if USE_STREAM_COPY:
        cmd = [
            ffmpeg_bin, "-y",
            "-ss", f"{start_sec:.3f}",
            "-i", video_path,
            "-t", f"{duration:.3f}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return
        except subprocess.CalledProcessError:
            # Some codecs/containers don't support clean stream copy at
            # arbitrary cut points — fall back to re-encoding automatically.
            pass

    cmd = [
        ffmpeg_bin, "-y",
        "-ss", f"{start_sec:.3f}",
        "-i", video_path,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-preset", "fast",
        output_path,
    ]
    _run_ffmpeg_cmd(cmd, "Cut clip")


def _make_vertical(input_path: str, output_path: str, mode: str = "blur"):
    """
    Reformats a clip into 9:16 (1080x1920).
    mode="blur" (default): keeps the full original frame, scaled to fit,
      centered over a blurred/zoomed copy of itself filling the bars —
      the look most Shorts/Reels/TikTok auto-clippers use.
    mode="crop": simple center crop to 9:16 — loses whatever's off to the
      sides, but is a touch faster and avoids any blur softness.
    Re-encoding is unavoidable here (filters can't stream-copy).
    """
    ffmpeg_bin = get_ffmpeg_path()
    if mode == "crop":
        vf = "crop=ih*9/16:ih,scale=1080:1920,setsar=1"
        cmd = [
            ffmpeg_bin, "-y",
            "-i", input_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            output_path,
        ]
    else:
        filter_complex = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,gblur=sigma=20[bg];"
            "[0:v]scale=1080:-2:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
        )
        cmd = [
            ffmpeg_bin, "-y",
            "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            output_path,
        ]

    _run_ffmpeg_cmd(cmd, f"Make vertical ({mode})")


def cut_clips(
    video_path: str,
    clip_moments: list,
    output_dir: str,
    progress_callback=None,
    vertical: bool = False,
    vertical_mode: str = "blur",
    captions: bool = False,
    watermark_png: str = None,
):
    """
    Cuts each {start, end, title, captions?} range out of video_path using
    ffmpeg, running up to MAX_PARALLEL_CUTS cuts at the same time.

    - vertical=True also generates a 9:16 version of each clip.
    - captions=True burns in Gemini's per-clip transcript (moment["captions"])
      as styled subtitles.
    - watermark_png, if given, is overlaid in the bottom-right corner of
      every clip (both horizontal and vertical).

    If progress_callback is given, calls it as
    progress_callback(index: int, total: int, title: str) as each clip starts.
    Returns a list of dicts with filename (+ vertical_filename if requested)
    and metadata for the frontend, in the same order as clip_moments.
    """
    total = len(clip_moments)
    results = [None] * total

    def process_one(i, moment):
        start = moment.get("start")
        end = moment.get("end")
        title = moment.get("title", f"clip_{i+1}")
        reason = moment.get("reason", "")

        if not start or not end:
            return None

        if progress_callback:
            progress_callback(i + 1, total, title)

        start_sec = _to_seconds(start)
        end_sec = _to_seconds(end)
        duration = max(end_sec - start_sec, 0.5)

        safe_title = _sanitize_filename(title)
        filename = f"{i+1:02d}_{safe_title}.mp4"
        output_path = os.path.join(output_dir, filename)
        raw_path = os.path.join(output_dir, f"{i+1:02d}_raw.mp4")

        _cut_one(video_path, start_sec, duration, raw_path)

        # Burn captions/watermark onto each final output SEPARATELY (rather
        # than baking them into the horizontal clip and then reformatting to
        # vertical) — otherwise the vertical blur pass picks up the already-
        # burned text too, producing a duplicated "ghost" echo in the
        # blurred background.
        srt_path = None
        if captions and moment.get("captions"):
            candidate_srt = os.path.join(output_dir, f"{i+1:02d}_captions.srt")
            if _write_srt(moment["captions"], start_sec, duration, candidate_srt):
                srt_path = candidate_srt

        has_overlay = bool(srt_path) or bool(watermark_png)

        if has_overlay:
            _apply_overlays(raw_path, output_path, srt_path=srt_path, watermark_png=watermark_png)
        else:
            os.replace(raw_path, output_path)

        result = {
            "filename": filename,
            "title": title,
            "reason": reason,
            "start": start,
            "end": end,
        }

        if vertical:
            vertical_filename = f"{i+1:02d}_{safe_title}_vertical.mp4"
            vertical_path = os.path.join(output_dir, vertical_filename)
            # Always reformat from the clean, un-overlaid raw clip
            source_for_vertical = raw_path if has_overlay else output_path

            if has_overlay:
                vertical_raw = os.path.join(output_dir, f"{i+1:02d}_vertical_raw.mp4")
                _make_vertical(source_for_vertical, vertical_raw, mode=vertical_mode)
                _apply_overlays(vertical_raw, vertical_path, srt_path=srt_path, watermark_png=watermark_png)
                if os.path.exists(vertical_raw):
                    os.remove(vertical_raw)
            else:
                _make_vertical(source_for_vertical, vertical_path, mode=vertical_mode)

            result["vertical_filename"] = vertical_filename

        if has_overlay and os.path.exists(raw_path):
            os.remove(raw_path)

        return result

    workers = max(1, min(MAX_PARALLEL_CUTS, total))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(process_one, i, moment): i
            for i, moment in enumerate(clip_moments)
        }
        for future in as_completed(futures):
            i = futures[future]
            try:
                results[i] = future.result()
            except Exception as e:
                logger.error(f"Error processing clip {i+1}: {e}")
                results[i] = None

    return [r for r in results if r is not None]
