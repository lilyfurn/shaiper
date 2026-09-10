import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workers.geometry.camera import orientation_transform
from workers.geometry.errors import GeometryError
from workers.geometry.images import prepare_image, working_size

HAS_PILLOW = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(HAS_PILLOW, "Pillow is not installed")
class ImageTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = Image.new("RGB", (42, 56))
        self.image.putdata([(x * 4, y * 3, (x + y) * 2) for y in range(56) for x in range(42)])

    def test_all_exif_orientations_match_color_coordinates(self):
        from PIL import Image
        for orientation in range(1, 9):
            with self.subTest(orientation=orientation):
                source = self.root / f"source-{orientation}.png"
                exif = Image.Exif()
                exif[274] = orientation
                exif[270] = "private camera note"
                self.image.save(source, exif=exif)
                original = source.read_bytes()
                result = prepare_image(source, self.root / f"working-{orientation}.png")
                matrix, size = orientation_transform(orientation, 42, 56)
                self.assertEqual((result.width, result.height), size)
                with Image.open(result.path) as saved:
                    self.assertFalse(saved.getexif())
                    self.assertNotIn("exif", saved.info)
                    for u, v in ((0, 0), (41, 0), (0, 55), (41, 55), (13, 24)):
                        x = matrix[0][0] * u + matrix[0][1] * v + matrix[0][2]
                        y = matrix[1][0] * u + matrix[1][1] * v + matrix[1][2]
                        self.assertEqual(saved.getpixel((x, y)), self.image.getpixel((u, v)))
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(result.source["sha256"], hashlib.sha256(original).hexdigest())

    def test_jpeg_is_accepted_by_content_and_original_is_unchanged(self):
        source = self.root / "photo.data"
        self.image.save(source, format="JPEG")
        before = source.read_bytes()
        result = prepare_image(source, self.root / "working.png")
        self.assertEqual(result.source["format"], "JPEG")
        self.assertEqual(before, source.read_bytes())

    def test_disguised_file_signature_fails(self):
        source = self.root / "image.jpg"
        source.write_bytes(b"not a photograph")
        with self.assertRaisesRegex(GeometryError, "signature"):
            prepare_image(source, self.root / "working.png")

    def test_truncated_png_fails_before_output(self):
        source = self.root / "image.png"
        self.image.save(source)
        source.write_bytes(source.read_bytes()[:40])
        with self.assertRaises(GeometryError):
            prepare_image(source, self.root / "working.png")
        self.assertFalse((self.root / "working.png").exists())

    def test_pixel_and_byte_limits_are_checked(self):
        source = self.root / "image.png"
        self.image.save(source)
        with patch("workers.geometry.images.MAX_INPUT_PIXELS", 50), self.assertRaises(GeometryError):
            prepare_image(source, self.root / "working.png")
        with patch("workers.geometry.images.MAX_INPUT_BYTES", 10), self.assertRaises(GeometryError):
            prepare_image(source, self.root / "working.png")

    def test_animated_png_is_refused(self):
        source = self.root / "animated.png"
        self.image.save(source, save_all=True, append_images=[self.image.transpose(0)], duration=100)
        with self.assertRaisesRegex(GeometryError, "single-frame"):
            prepare_image(source, self.root / "working.png")

    def test_transparency_is_explicitly_composited(self):
        from PIL import Image
        source = self.root / "transparent.png"
        Image.new("RGBA", (42, 56), (255, 0, 0, 0)).save(source)
        result = prepare_image(source, self.root / "working.png")
        self.assertTrue(result.source["preprocessing"]["alphaCompositedOnWhite"])
        self.assertEqual(result.rgb[:3], b"\xff\xff\xff")

    def test_resize_is_tracked_and_no_undistortion_is_claimed(self):
        from PIL import Image
        source = self.root / "large.png"
        Image.new("RGB", (1200, 800), (20, 40, 60)).save(source)
        result = prepare_image(source, self.root / "working.png", 504)
        self.assertEqual((result.width, result.height), (504, 336))
        transform = result.source["preprocessing"]["T_working_from_oriented_pixels"]
        self.assertAlmostEqual(transform[0][0], .42)
        self.assertAlmostEqual(transform[0][2], -.29)
        self.assertFalse(result.source["preprocessing"]["undistortionApplied"])

    def test_invalid_exif_orientation_fails(self):
        from PIL import Image
        exif = Image.Exif()
        exif[274] = 9
        source = self.root / "image.png"
        self.image.save(source, exif=exif)
        with self.assertRaises(GeometryError):
            prepare_image(source, self.root / "working.png")


class WorkingSizeTests(unittest.TestCase):
    def test_extreme_aspect_ratio_and_invalid_resolution_fail(self):
        for values in ((10000, 28, 504), (500, 500, 512), (500, 500, 2016)):
            with self.subTest(values=values), self.assertRaises(GeometryError):
                working_size(*values)
