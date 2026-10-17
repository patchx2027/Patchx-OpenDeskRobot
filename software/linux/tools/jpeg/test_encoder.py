#!/usr/bin/env python3
"""Decode real JPEG output independently and test byte-budget/ownership guards."""
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from PIL import Image

PROGRAM = sys.argv.pop(1)


class EncoderTests(unittest.TestCase):
    def run_frame(self, data, budget=49000, extra=(), existing=False):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.nv12"
            output = Path(directory) / "output.jpg"
            source.write_bytes(data)
            if existing:
                output.write_bytes(b"owned-by-other")
            result = subprocess.run([PROGRAM, "--input", str(source), "--output", str(output),
                "--width", "640", "--height", "480", "--stride", "640", "--max-bytes", str(budget),
                *extra], capture_output=True, timeout=5)
            return result, output.read_bytes() if output.exists() else None

    def test_uniform_gray_has_correct_dimensions_and_pixels(self):
        result, data = self.run_frame(bytes([128]) * 460800)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        image = Image.open(io.BytesIO(data)).convert("RGB")
        self.assertEqual(image.size, (640, 480))
        self.assertEqual(image.getpixel((300, 200)), (128, 128, 128))
        self.assertEqual(receipt["jpeg_bytes"], len(data))
        self.assertEqual(receipt["quality"], 90)

    def test_nv12_uv_order_and_full_range_matrix(self):
        colors = [(76, 85, 255), (150, 44, 21), (29, 255, 107), (128, 128, 128)]
        y = b"".join(bytes([item[0]]) * 160 for item in colors) * 480
        uv = b"".join(bytes(item[1:]) * 80 for item in colors) * 240
        result, data = self.run_frame(y + uv)
        self.assertEqual(result.returncode, 0, result.stderr)
        image = Image.open(io.BytesIO(data)).convert("RGB")
        for x, expected in zip((80, 240, 400, 560), ((254, 0, 0), (0, 255, 1), (0, 0, 254), (128, 128, 128))):
            self.assertTrue(all(abs(a - b) <= 4 for a, b in zip(image.getpixel((x, 200)), expected)))

    def test_quality_steps_fit_budget_without_resizing(self):
        random_source = random.Random(123)
        raw = random_source.randbytes(460800)
        result, data = self.run_frame(raw, budget=45000)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertLess(receipt["quality"], 90)
        self.assertLessEqual(len(data), 45000)
        self.assertEqual(Image.open(io.BytesIO(data)).size, (640, 480))

    def test_rejected_inputs_never_create_or_replace_output(self):
        raw = bytes([128]) * 460800
        for data, budget, extra in ((raw[:-1], 49000, ()), (raw + b"x", 49000, ()),
                                   (raw, 128, ()), (raw, 49000, ("--width", "639")),
                                   (raw, 49000, ("--stride", "638")),
                                   (raw, 49000, ("--height", "abc"))):
            result, output = self.run_frame(data, budget, extra)
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
        result, output = self.run_frame(raw, existing=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, b"owned-by-other")


if __name__ == "__main__":
    unittest.main()
