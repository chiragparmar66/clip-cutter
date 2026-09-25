import json
import logging
import os
import re
import time

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from processing.clipper import _to_seconds


logger = logging.getLogger("clip_cutter.gemini")


DEFAULT_MODEL_NAME = "gemini-flash-latest"
RETRY_DELAY_SECONDS = 3


def get_gemini_client():
    """Returns an authenticated Gemini client or raises a clear ValueError if the key is missing."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key or api_key == "your_gemini_api_key_here":
        raise ValueError(
            "GEMINI_API_KEY is not configured. "
            "Please set a valid Gemini API key in your .env file or environment."
        )

    return genai.Client(api_key=api_key)


def _extract_json(text: str):
    """
    Extracts and parses JSON from Gemini's response text.

    Handles:
    - Markdown code fences
    - Leading/trailing conversational text
    - JSON arrays
    - JSON objects
    - Objects containing clips/moments/highlights/results
    """

    if not text or not text.strip():
        raise ValueError("Received empty response from Gemini.")

    cleaned = text.strip()

    # If wrapped in markdown fences, extract content within the fences.
    fence_match = re.search(
        r"```(?:json)?\s*([\s\S]*?)\s*```",
        cleaned,
        re.IGNORECASE,
    )

    if fence_match:
        cleaned = fence_match.group(1).strip()

    # Extract JSON array if conversational text is present.
    array_start = cleaned.find("[")
    array_end = cleaned.rfind("]")

    if array_start != -1 and array_end != -1 and array_end > array_start:
        cleaned = cleaned[array_start : array_end + 1]

    else:
        # Otherwise try to extract a JSON object.
        obj_start = cleaned.find("{")
        obj_end = cleaned.rfind("}")

        if obj_start != -1 and obj_end != -1 and obj_end > obj_start:
            cleaned = cleaned[obj_start : obj_end + 1]

    try:
        data = json.loads(cleaned)

    except json.JSONDecodeError as e:
        preview = text[:200] + ("..." if len(text) > 200 else "")

        raise ValueError(
            f"Failed to parse Gemini response as JSON: {e}. "
            f"Raw response snippet: {preview}"
        ) from e

    # If the response is an object wrapping a list,
    # extract the list.
    if isinstance(data, dict):

        for key in ["clips", "moments", "highlights", "results"]:
            if key in data and isinstance(data[key], list):
                return data[key]

        # If the single object itself is a clip.
        if "start" in data and "end" in data:
            return [data]

        raise ValueError(
            f"Unexpected JSON object structure from Gemini: {list(data.keys())}"
        )

    if not isinstance(data, list):
        raise ValueError(
            f"Expected a JSON list from Gemini, got {type(data).__name__}"
        )

    return data


def validate_clip_moments(moments: list, max_clips: int = 12) -> list:
    """
    Validates and sanitizes AI-generated clip timestamps and metadata.

    Rules:
    - Start and end timestamps must exist.
    - Timestamps must be non-negative.
    - Start must be before end.
    - Clip duration must be at least 1 second.
    - Titles and reasons are converted to safe strings.
    - Captions are validated when present.
    - Maximum clip count is respected.
    """

    if not isinstance(moments, list):
        raise ValueError("Clip moments must be a list.")

    valid_clips = []

    for i, item in enumerate(moments):

        if not isinstance(item, dict):
            continue

        raw_start = item.get("start")
        raw_end = item.get("end")

        if raw_start is None or raw_end is None:
            logger.warning(
                f"Skipping clip {i + 1}: missing start or end timestamp."
            )
            continue

        try:
            start_sec = _to_seconds(str(raw_start))
            end_sec = _to_seconds(str(raw_end))

        except (ValueError, TypeError) as e:
            logger.warning(
                f"Skipping clip {i + 1}: "
                f"invalid timestamp format ({raw_start} -> {raw_end}): {e}"
            )
            continue

        if start_sec < 0 or end_sec < 0:
            logger.warning(
                f"Skipping clip {i + 1}: negative timestamp."
            )
            continue

        if end_sec <= start_sec:
            logger.warning(
                f"Skipping clip {i + 1}: "
                f"start ({start_sec}s) >= end ({end_sec}s)."
            )
            continue

        duration = end_sec - start_sec

        if duration < 1.0:
            logger.warning(
                f"Skipping clip {i + 1}: "
                f"duration too short ({duration:.1f}s)."
            )
            continue

        title = str(
            item.get("title") or f"Clip {len(valid_clips) + 1}"
        ).strip()

        reason = str(
            item.get("reason") or ""
        ).strip()

        sanitized_clip = {
            "start": str(raw_start).strip(),
            "end": str(raw_end).strip(),
            "title": title,
            "reason": reason,
        }

        # Validate captions if present.
        raw_captions = item.get("captions")

        if isinstance(raw_captions, list):

            valid_captions = []

            for cap in raw_captions:

                if (
                    isinstance(cap, dict)
                    and cap.get("start")
                    and cap.get("end")
                    and cap.get("text")
                ):
                    try:
                        c_start = _to_seconds(str(cap["start"]))
                        c_end = _to_seconds(str(cap["end"]))

                        if c_start < c_end:
                            valid_captions.append(
                                {
                                    "start": str(cap["start"]).strip(),
                                    "end": str(cap["end"]).strip(),
                                    "text": str(cap["text"]).strip(),
                                }
                            )

                    except Exception:
                        continue

            if valid_captions:
                sanitized_clip["captions"] = valid_captions

        valid_clips.append(sanitized_clip)

        if len(valid_clips) >= max_clips:
            break

    if not valid_clips:
        raise ValueError(
            "No valid clips could be extracted from Gemini's response."
        )

    return valid_clips


def find_clip_moments(
    youtube_url: str,
    max_clips: int = 12,
    extra_instructions: str = "",
    need_captions: bool = False,
):
    """
    Passes the YouTube URL directly to Gemini and asks it to identify
    clip-worthy moments.

    Returns a validated list of clip dictionaries.

    Gemini transient errors such as 429/500/502/503/504 are retried
    using exponential backoff.
    """

    client = get_gemini_client()

    model_name = (
        os.environ.get(
            "GEMINI_MODEL",
            DEFAULT_MODEL_NAME,
        ).strip()
        or DEFAULT_MODEL_NAME
    )

    max_retries = int(
        os.environ.get(
            "GEMINI_MAX_RETRIES",
            "3",
        )
    )

    captions_instruction = ""
    captions_field = ""

    if need_captions:

        captions_instruction = """
Also transcribe the spoken dialogue for EACH selected clip, broken into short
caption-sized lines (aim for about 3-8 words per line, like real subtitles —
not full sentences dumped into one line). Each caption line needs its own
start/end timestamp, still on the SAME absolute video clock as the clip's
own start/end (not relative to the clip).
"""

        captions_field = """,
    "captions": [
      {"start": "HH:MM:SS", "end": "HH:MM:SS", "text": "short caption line"}
    ]"""

    prompt = f"""
You are analyzing a YouTube VOD to find the funniest and best moments to turn
into short-form clips (like TikTok/YouTube Shorts/Reels).

Watch the entire video and pull out every moment that's genuinely funny, a
strong reaction, a big laugh, or an unexpectedly great highlight.

Prioritize:
- Funny moments, jokes, and reactions above everything else
- Surprising, high-energy, or emotionally strong beats
- Self-contained moments that make sense without extra context
- A strong hook in the first couple seconds of the segment

IMPORTANT:
Do not force a specific number of clips.

Only include moments that are ACTUALLY funny or clip-worthy.

If the video only has 3 great moments, return 3.
If it has 15, return 15.

Never pad the list with mediocre or average moments just to hit a count.

Quality over quantity, always.

Cap it at {max_clips} clips maximum, picking the best {max_clips}
if there are more than that.

Each clip should be between 15 and 90 seconds long, and should start
and end at natural points (not mid-sentence, not mid-laugh).

{captions_instruction}

{"Additional guidance from the user: " + extra_instructions if extra_instructions else ""}

Respond with ONLY a JSON array, no other text, no markdown fences.

Format:
[
  {{
    "start": "HH:MM:SS",
    "end": "HH:MM:SS",
    "title": "Short catchy title for the clip",
    "reason": "One sentence on why this moment is genuinely funny/great"{captions_field}
  }}
]
"""

    logger.info(
        f"Asking Gemini ({model_name}) to analyze {youtube_url}..."
    )

    last_error = None

    # These are transient errors where retrying makes sense.
    #
    # 408 = request timeout
    # 429 = rate limit
    # 500 = internal server error
    # 502 = bad gateway
    # 503 = service unavailable / temporary overload
    # 504 = gateway timeout
    retryable_codes = {
        408,
        429,
        500,
        502,
        503,
        504,
    }

    for attempt in range(1, max_retries + 1):

        try:

            logger.info(
                f"Gemini analysis attempt "
                f"{attempt}/{max_retries} "
                f"using model {model_name}"
            )

            response = client.models.generate_content(
                model=model_name,
                contents=types.Content(
                    parts=[
                        types.Part(
                            file_data=types.FileData(
                                file_uri=youtube_url
                            )
                        ),
                        types.Part(
                            text=prompt
                        ),
                    ]
                ),
            )

            raw_text = response.text or ""

            raw_moments = _extract_json(raw_text)

            return validate_clip_moments(
                raw_moments,
                max_clips=max_clips,
            )

        except genai_errors.ClientError as e:

            last_error = e

            error_code = getattr(
                e,
                "code",
                None,
            )

            # Retry only temporary/transient API errors.
            if (
                error_code in retryable_codes
                and attempt < max_retries
            ):

                delay = RETRY_DELAY_SECONDS * (
                    2 ** (attempt - 1)
                )

                logger.warning(
                    f"Gemini returned {error_code} "
                    f"(attempt {attempt}/{max_retries}) — "
                    f"retrying in {delay}s..."
                )

                time.sleep(delay)

                continue

            # Permanent error or final retry exhausted.
            logger.error(
                f"Gemini request failed with code "
                f"{error_code} on attempt "
                f"{attempt}/{max_retries}: {e}"
            )

            raise

        except Exception as e:

            last_error = e

            # Network/SDK-level temporary errors may not always
            # appear as ClientError, so give them a limited retry.
            if attempt < max_retries:

                delay = RETRY_DELAY_SECONDS * (
                    2 ** (attempt - 1)
                )

                logger.warning(
                    f"Gemini request encountered a temporary "
                    f"error (attempt {attempt}/{max_retries}) — "
                    f"retrying in {delay}s: {e}"
                )

                time.sleep(delay)

                continue

            logger.error(
                f"Gemini request failed after "
                f"{attempt}/{max_retries} attempts: {e}"
            )

            raise

    raise last_error if last_error else RuntimeError(
        "Gemini analysis failed after retries."
    )