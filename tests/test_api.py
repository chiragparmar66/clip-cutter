import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from app import app, jobs, jobs_lock, OUTPUT_DIR


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        with jobs_lock:
            jobs.clear()

    def test_health_endpoint(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("ffmpeg", data)
        self.assertIn("gemini", data)

    def test_index_endpoint(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"CLIP BAY", res.data)

    def test_start_missing_url(self):
        res = self.client.post("/api/start", json={})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)

    def test_start_invalid_url(self):
        res = self.client.post("/api/start", json={"url": "https://notyoutube.com/something"})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)

    @patch("app.threading.Thread")
    def test_start_valid_url(self, mock_thread):
        mock_instance = mock_thread.return_value
        res = self.client.post("/api/start", json={
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "max_clips": 5,
            "vertical": True,
            "vertical_mode": "blur",
            "captions": False,
            "watermark_text": "@test",
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("job_id", data)
        self.assertTrue(mock_instance.start.called)

        # Check job in memory
        job_id = data["job_id"]
        status_res = self.client.get(f"/api/status/{job_id}")
        self.assertEqual(status_res.status_code, 200)
        status_data = status_res.get_json()
        self.assertEqual(status_data["stage"], "queued")

    def test_status_unknown_job(self):
        res = self.client.get("/api/status/nonexistent123")
        self.assertEqual(res.status_code, 404)
        data = res.get_json()
        self.assertIn("error", data)

    def test_status_invalid_job_format(self):
        res = self.client.get("/api/status/invalid/slash")
        self.assertEqual(res.status_code, 404)

    def test_serve_clip_security_traversal(self):
        # Attempt path traversal
        res = self.client.get("/clips/../../app.py")
        self.assertIn(res.status_code, [400, 403, 404])

        res = self.client.get("/clips/%2e%2e%2f%2e%2e%2fapp.py/test.mp4")
        self.assertIn(res.status_code, [400, 403, 404])

    def test_serve_clip_hidden_file(self):
        res = self.client.get("/clips/job123/_watermark.png")
        self.assertIn(res.status_code, [400, 403])

    def test_serve_clip_valid_file(self):
        # Create a mock clip output
        test_job_id = "testjob1"
        test_job_dir = os.path.join(OUTPUT_DIR, test_job_id)
        os.makedirs(test_job_dir, exist_ok=True)
        test_file = os.path.join(test_job_dir, "01_test_clip.mp4")
        with open(test_file, "wb") as f:
            f.write(b"dummy mp4 content")

        try:
            res = self.client.get(f"/clips/{test_job_id}/01_test_clip.mp4")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.data, b"dummy mp4 content")
        finally:
            shutil.rmtree(test_job_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
