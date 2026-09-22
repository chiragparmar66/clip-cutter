import os
import shutil
import tempfile
import unittest
from PIL import Image

from processing.watermark import generate_watermark_png


class WatermarkTests(unittest.TestCase):
    def test_generate_watermark_png(self):
        temp_dir = tempfile.mkdtemp()
        output_png = os.path.join(temp_dir, "test_wm.png")

        try:
            res_path = generate_watermark_png("@mychannel", output_png, font_size=32, opacity=180)
            self.assertEqual(res_path, output_png)
            self.assertTrue(os.path.exists(output_png))

            # Verify image properties
            with Image.open(output_png) as img:
                self.assertEqual(img.format, "PNG")
                self.assertEqual(img.mode, "RGBA")
                self.assertGreater(img.width, 10)
                self.assertGreater(img.height, 10)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_generate_watermark_empty_text(self):
        temp_dir = tempfile.mkdtemp()
        output_png = os.path.join(temp_dir, "test_empty_wm.png")

        try:
            res_path = generate_watermark_png("", output_png)
            self.assertTrue(os.path.exists(res_path))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
