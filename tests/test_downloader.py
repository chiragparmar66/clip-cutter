import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from yt_dlp.utils import DownloadError
from processing.downloader import validate_youtube_url, download_video


class DownloaderTests(unittest.TestCase):
    def test_validate_youtube_url_valid(self):
        valid_urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "http://youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        ]
        for url in valid_urls:
            self.assertEqual(validate_youtube_url(url), url)

    def test_validate_youtube_url_invalid(self):
        invalid_urls = [
            "",
            "https://vimeo.com/123456",
            "https://google.com",
            "not a url",
            None,
        ]
        for url in invalid_urls:
            with self.assertRaises(ValueError):
                validate_youtube_url(url)

    @patch("yt_dlp.YoutubeDL")
    def test_download_video_success(self, mock_ydl_cls):
        temp_dir = tempfile.mkdtemp()
        job_id = "test_job_down"
        mock_filepath = os.path.join(temp_dir, f"{job_id}.mp4")

        with open(mock_filepath, "wb") as f:
            f.write(b"mock video data")

        mock_instance = MagicMock()
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.extract_info.return_value = {"title": "Test Title"}
        mock_instance.prepare_filename.return_value = mock_filepath
        mock_ydl_cls.return_value = mock_instance

        try:
            path, title = download_video(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                temp_dir,
                job_id,
            )
            self.assertEqual(path, mock_filepath)
            self.assertEqual(title, "Test Title")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @patch("yt_dlp.YoutubeDL")
    def test_download_video_private_error(self, mock_ydl_cls):
        temp_dir = tempfile.mkdtemp()
        mock_instance = MagicMock()
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.extract_info.side_effect = DownloadError("Private video: Sign in if you've been granted access")
        mock_ydl_cls.return_value = mock_instance

        try:
            with self.assertRaises(RuntimeError) as ctx:
                download_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ", temp_dir, "job_err")
            self.assertIn("Private", str(ctx.exception))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
