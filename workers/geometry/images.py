"""Bounded local decoding and a reproducible, metadata-free working image."""

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import warnings

from .camera import orientation_transform
from .errors import GeometryError

MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_INPUT_PIXELS = 40_000_000
PATCH_SIZE = 14


@dataclass
class PreparedImage:
    path: Path
    width: int
    height: int
    rgb: bytes
    source: dict
    warnings: list


def working_size(width: int, height: int, max_edge: int) -> tuple:
    if max_edge < 140 or max_edge > 1008 or max_edge % PATCH_SIZE:
        raise GeometryError("invalid_settings", "Maximum edge must be a multiple of 14 between 140 and 1008.")
    scale = min(1., max_edge / max(width, height))
    size = tuple(int(value * scale / PATCH_SIZE + .5) * PATCH_SIZE for value in (width, height))
    if min(size) < PATCH_SIZE * 2:
        raise GeometryError("image_too_narrow", "Working image must contain at least two 14-pixel patches on each axis.")
    return size


def prepare_image(path: Path, output: Path, max_edge: int = 504) -> PreparedImage:
    try:
        from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise GeometryError("missing_dependency", "Pillow is required to decode JPEG/PNG images.") from exc

    if not path.is_file():
        raise GeometryError("input_not_found", "Input must be an existing local JPEG or PNG file.")
    with path.open("rb") as stream:
        data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise GeometryError("input_too_large", "Input exceeds the 25 MiB byte limit.")
    detected_format = ("PNG" if data.startswith(b"\x89PNG\r\n\x1a\n") else
                       "JPEG" if data.startswith(b"\xff\xd8\xff") else None)
    if detected_format is None:
        raise GeometryError("unsupported_image", "File signature is not JPEG or PNG; renaming a file does not convert it.")
    notes = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as check:
                if check.format != detected_format or getattr(check, "n_frames", 1) != 1:
                    raise GeometryError("unsupported_image", "Only single-frame JPEG/PNG photographs are supported.")
                raw_size = check.size
                if min(raw_size) < 28 or raw_size[0] * raw_size[1] > MAX_INPUT_PIXELS:
                    raise GeometryError("image_dimensions", "Image must be at least 28 pixels per side and at most 40 megapixels.")
                check.verify()
            with Image.open(BytesIO(data)) as original:
                orientation = original.getexif().get(274, 1)
                if not isinstance(orientation, int):
                    raise GeometryError("invalid_orientation", "EXIF orientation is not an integer.")
                orient_matrix, oriented_size = orientation_transform(orientation, *raw_size)
                original.load()
                oriented = ImageOps.exif_transpose(original)
                if oriented.size != oriented_size:
                    raise GeometryError("orientation_mismatch", "Decoded EXIF transform did not match its recorded geometry.")
                had_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
                alpha = oriented.convert("RGBA").getchannel("A") if had_alpha else None
                profile = original.info.get("icc_profile")
                if profile:
                    profile_image = oriented if oriented.mode in ("RGB", "CMYK", "LAB", "L") else oriented.convert("RGB")
                    rgb_image = ImageCms.profileToProfile(
                        profile_image, ImageCms.ImageCmsProfile(BytesIO(profile)),
                        ImageCms.createProfile("sRGB"), outputMode="RGB")
                    color_conversion = "embedded_icc_to_srgb"
                else:
                    rgb_image = oriented.convert("RGB")
                    color_conversion = "pillow_rgb_assumed_srgb"
                    notes.append("No embedded color profile; RGB is treated as sRGB.")
                alpha_composited = alpha is not None and alpha.getextrema()[0] < 255
                if alpha_composited:
                    background = Image.new("RGB", rgb_image.size, (255, 255, 255))
                    background.paste(rgb_image, mask=alpha)
                    rgb_image = background
                    notes.append("Transparency was explicitly composited on white; no object mask was inferred.")
                size = working_size(*oriented_size, max_edge)
                resized = rgb_image.resize(size, Image.Resampling.LANCZOS)
                pixels = resized.tobytes()
                # Recreate from pixels so EXIF, GPS, text chunks and ICC data cannot leak.
                clean = Image.frombytes("RGB", size, pixels)
                with output.open("xb") as stream:
                    clean.save(stream, format="PNG")
    except GeometryError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning, ImageCms.PyCMSError) as exc:
        raise GeometryError("invalid_image", f"Image decoding or color conversion failed ({type(exc).__name__}).") from exc

    sx, sy = size[0] / oriented_size[0], size[1] / oriented_size[1]
    checksum = hashlib.sha256(data).hexdigest()
    source = {
        "imageId": checksum[:32], "sha256": checksum, "byteSize": len(data),
        "storageGeneration": None, "format": detected_format,
        "originalSize": list(raw_size), "workingSize": list(size),
        "preprocessing": {
            "exifOrientation": orientation, "T_oriented_from_original_pixels": orient_matrix,
            "orientedSize": list(oriented_size),
            "T_working_from_oriented_pixels": [[sx, 0, (sx - 1) / 2], [0, sy, (sy - 1) / 2], [0, 0, 1]],
            "pixelConvention": "integer_pixel_centers", "resizeFilter": "lanczos",
            "colorConversion": color_conversion, "alphaCompositedOnWhite": alpha_composited,
            "crop": None, "padding": None, "undistortionApplied": False,
            "metadataStripped": True,
        },
    }
    return PreparedImage(output, *size, pixels, source, notes)
