import unittest
from unittest.mock import MagicMock, patch

from processing.gemini_analyzer import (
    _extract_json,
    validate_clip_moments,
    find_clip_moments,
    get_gemini_client,
)


class GeminiAnalyzerTests(unittest.TestCase):
    def test_extract_json_clean(self):
        raw = '[{"start": "00:01:00", "end": "00:01:30", "title": "Great Moment"}]'
        result = _extract_json(raw)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Great Moment")

    def test_extract_json_markdown_fenced(self):
        raw = """```json
[
  {
    "start": "00:02:10",
    "end": "00:02:45",
    "title": "Funny Joke",
    "reason": "Very funny"
  }
]
```"""
        result = _extract_json(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Funny Joke")

    def test_extract_json_with_preamble_and_trailing(self):
        raw = """Here are the top moments I found in this video:
[
  {"start": "00:00:15", "end": "00:00:45", "title": "Intro Highlight"}
]
Hope you enjoy these clips!"""
        result = _extract_json(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Intro Highlight")

    def test_extract_json_wrapped_in_dict(self):
        raw = '{"clips": [{"start": "00:05:00", "end": "00:05:30", "title": "Clips Key"}]}'
        result = _extract_json(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Clips Key")

    def test_extract_json_empty_or_invalid(self):
        with self.assertRaises(ValueError):
            _extract_json("")
        with self.assertRaises(ValueError):
            _extract_json("not valid json at all")

    def test_validate_clip_moments_valid(self):
        moments = [
            {"start": "00:01:00", "end": "00:01:30", "title": "Valid Clip", "reason": "Good beat"},
            {"start": "00:02:00", "end": "00:02:40", "title": "Second Clip"},
        ]
        validated = validate_clip_moments(moments, max_clips=5)
        self.assertEqual(len(validated), 2)
        self.assertEqual(validated[0]["start"], "00:01:00")
        self.assertEqual(validated[0]["end"], "00:01:30")

    def test_validate_clip_moments_invalid_timestamps(self):
        moments = [
            {"start": "00:02:00", "end": "00:01:00", "title": "Inverted"}, # inverted
            {"start": "-00:01:00", "end": "00:01:00", "title": "Negative"}, # negative
            {"start": "00:01:00", "end": "00:01:00.5", "title": "Too short"}, # < 1s
            {"start": "invalid", "end": "00:01:00", "title": "Bad format"},
            {"start": "00:05:00", "end": "00:05:20", "title": "Good Clip"},
        ]
        validated = validate_clip_moments(moments, max_clips=5)
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0]["title"], "Good Clip")

    def test_validate_clip_moments_max_clips_limit(self):
        moments = [
            {"start": f"00:{i:02d}:00", "end": f"00:{i:02d}:30", "title": f"Clip {i}"}
            for i in range(10)
        ]
        validated = validate_clip_moments(moments, max_clips=3)
        self.assertEqual(len(validated), 3)

    def test_validate_clip_moments_captions(self):
        moments = [
            {
                "start": "00:01:00",
                "end": "00:01:30",
                "title": "With Captions",
                "captions": [
                    {"start": "00:01:00", "end": "00:01:05", "text": "Hello world"},
                    {"start": "00:01:05", "end": "00:01:10", "text": "This is a clip"},
                    {"start": "00:01:20", "end": "00:01:15", "text": "Inverted caption"}, # Should be skipped
                ],
            }
        ]
        validated = validate_clip_moments(moments, max_clips=5)
        self.assertEqual(len(validated), 1)
        self.assertIn("captions", validated[0])
        self.assertEqual(len(validated[0]["captions"]), 2)

    @patch.dict("os.environ", {"GEMINI_API_KEY": ""})
    def test_get_gemini_client_missing_key(self):
        with self.assertRaises(ValueError) as ctx:
            get_gemini_client()
        self.assertIn("GEMINI_API_KEY is not configured", str(ctx.exception))

    @patch("processing.gemini_analyzer.get_gemini_client")
    def test_find_clip_moments_mocked(self, mock_client_factory):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '[{"start": "00:01:00", "end": "00:01:30", "title": "Mock Clip", "reason": "Funny"}]'
        mock_client.models.generate_content.return_value = mock_response
        mock_client_factory.return_value = mock_client

        results = find_clip_moments("https://www.youtube.com/watch?v=12345678901", max_clips=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Mock Clip")


if __name__ == "__main__":
    unittest.main()
