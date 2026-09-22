import os
import shutil
import tempfile
import unittest

from processing.clipper import (
    _to_seconds,
    _seconds_to_srt_time,
    _write_srt,
    _sanitize_filename,
    _escape_filter_path,
    get_ffmpeg_path,
)


class ClipperTests(unittest.TestCase):
    def test_to_seconds_formats(self):
        self.assertEqual(_to_seconds("00:01:23"), 83.0)
        self.assertEqual(_to_seconds("01:23"), 83.0)
        self.assertEqual(_to_seconds("83"), 83.0)
        self.assertEqual(_to_seconds(83.5), 83.5)
        self.assertEqual(_to_seconds("00:00:05.500"), 5.5)

    def test_to_seconds_invalid(self):
        with self.assertRaises(ValueError):
            _to_seconds("not_a_time")

    def test_seconds_to_srt_time(self):
        self.assertEqual(_seconds_to_srt_time(0), "00:00:00,000")
        self.assertEqual(_seconds_to_srt_time(83.5), "00:01:23,500")
        self.assertEqual(_seconds_to_srt_time(3665.123), "01:01:05,123")

    def test_sanitize_filename(self):
        self.assertEqual(_sanitize_filename("Funny Moment #1!"), "Funny_Moment_1")
        self.assertEqual(_sanitize_filename("What??? <Crazy> / \\"), "What_Crazy")
        self.assertEqual(_sanitize_filename("   "), "clip")

    def test_escape_filter_path(self):
        escaped = _escape_filter_path(r"C:\Users\test\captions.srt")
        self.assertNotIn("\\", escaped.replace(r"\'", ""))
        self.assertIn(":", escaped)

    def test_get_ffmpeg_path(self):
        path = get_ffmpeg_path()
        self.assertIsInstance(path, str)
        self.assertTrue(len(path) > 0)

    def test_write_srt(self):
        temp_dir = tempfile.mkdtemp()
        srt_path = os.path.join(temp_dir, "test.srt")
        captions = [
            {"start": "00:01:10", "end": "00:01:15", "text": "First subtitle line"},
            {"start": "00:01:16", "end": "00:01:20", "text": "Second subtitle line"},
        ]
        clip_start_sec = 70.0  # 00:01:10
        clip_duration = 20.0

        try:
            success = _write_srt(captions, clip_start_sec, clip_duration, srt_path)
            self.assertTrue(success)
            self.assertTrue(os.path.exists(srt_path))
            with open(srt_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("00:00:00,000 --> 00:00:05,000", content)
            self.assertIn("First subtitle line", content)
            self.assertIn("00:00:06,000 --> 00:00:10,000", content)
            self.assertIn("Second subtitle line", content)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
