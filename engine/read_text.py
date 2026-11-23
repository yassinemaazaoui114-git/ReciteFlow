"""Reads the text of a file for the text box: a .txt file, or a photo of a page (OCR).

  py -3.12 engine/read_text.py <file>   -> one JSON line on stdout: {"text": "..."} or {"error": "<plain words>"}

The window starts it once per file, so the OCR's memory (~0.6 GB while it reads) is given back as soon as it is done
and the speech engine is never touched. OCR = RapidOCR (PP-OCR models run with onnxruntime on the CPU, no GPU); it
read the test photos best of the four OCRs tried (engine/check_ocr.py). Photos Pillow cannot open (HEIC from a
phone) are decoded by Windows. Everything stays on this PC.
"""
import json
import math
import os
import sys

TEXT_EXT = (".txt",)
PHOTO_EXT = (".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".bmp", ".dib", ".gif", ".tif", ".tiff", ".webp", ".avif",
             ".heic", ".heif")
MAX_TEXT_BYTES = 2_000_000   # a text to recite is far smaller; the recitation itself takes at most ~1 MB


class Unreadable(Exception):
    """A file that cannot be read; the message is shown to the user as it is."""


# ---- Text files ----
def read_txt(path):
    if os.path.getsize(path) > MAX_TEXT_BYTES:
        raise Unreadable("This text file is too big to recite (more than 2 MB).")
    raw = open(path, "rb").read()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")   # older Windows text files
    if "\x00" in text:
        raise Unreadable("This file does not look like text.")
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


# ---- Photos ----
def load_photo(path):
    """The photo as RGB pixels (numpy), turned the way the camera held it."""
    import numpy as np
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        with Image.open(path) as img:
            img.seek(0)   # the first frame of a GIF or a multi-page TIFF
            return np.asarray(ImageOps.exif_transpose(img).convert("RGB"))
    except (UnidentifiedImageError, OSError):
        pass
    try:
        return windows_decode(path)
    except Exception:
        raise Unreadable("Could not open this picture (it may be damaged, or not a picture).") from None


def windows_decode(path):
    """Decode with Windows (HEIC and other formats Windows has a codec for), turned upright."""
    import asyncio
    import numpy as np
    from winrt.windows.graphics.imaging import (BitmapAlphaMode, BitmapDecoder, BitmapPixelFormat, BitmapTransform,
                                                ColorManagementMode, ExifOrientationMode)
    from winrt.windows.storage import FileAccessMode, StorageFile

    async def decode():
        f = await StorageFile.get_file_from_path_async(os.path.abspath(path))
        dec = await BitmapDecoder.create_async(await f.open_async(FileAccessMode.READ))
        data = await dec.get_pixel_data_async(
            BitmapPixelFormat.RGBA8, BitmapAlphaMode.IGNORE, BitmapTransform(),
            ExifOrientationMode.RESPECT_EXIF_ORIENTATION, ColorManagementMode.DO_NOT_COLOR_MANAGE)
        pixels = data.detach_pixel_data()
        return np.frombuffer(bytes(pixels), np.uint8).reshape(
            dec.oriented_pixel_height, dec.oriented_pixel_width, 4)[:, :, :3]
    return asyncio.run(decode())


def ocr(rgb):
    """RapidOCR: pieces of text as (4 corners, text)."""
    from rapidocr import RapidOCR
    engine = RapidOCR(params={"Global.log_level": "error"})
    res = engine(rgb[:, :, ::-1].copy())   # it takes BGR, as OpenCV
    if res.boxes is None:
        return []
    return [([(float(x), float(y)) for x, y in box], text) for box, text in zip(res.boxes, res.txts)]


def assemble(pieces):
    """Pieces -> plain text. The page may be tilted: the pieces are first turned by the text's usual angle, then
    pieces on the same row become one line (left to right), rows top to bottom; a larger gap than usual between rows
    starts a new paragraph; a word cut by a hyphen at a line end is joined again."""
    pieces = [(q, t.strip()) for q, t in pieces if t.strip()]
    if not pieces:
        return ""
    angles = sorted(math.atan2(q[1][1] - q[0][1], q[1][0] - q[0][0]) for q, _ in pieces)
    a = angles[len(angles) // 2]
    ca, sa = math.cos(-a), math.sin(-a)
    boxes = []
    for q, t in pieces:
        cx, cy = sum(p[0] for p in q) / 4, sum(p[1] for p in q) / 4
        h = (math.dist(q[0], q[3]) + math.dist(q[1], q[2])) / 2
        boxes.append((cx * ca - cy * sa, cx * sa + cy * ca, h, t))   # center turned upright, height, text
    boxes.sort(key=lambda b: b[1])
    rows = []
    for x, y, h, t in boxes:
        if rows and abs(y - rows[-1]["y"]) < 0.5 * min(h, rows[-1]["h"]):
            rows[-1]["parts"].append((x, t))
        else:
            rows.append({"y": y, "h": h, "parts": [(x, t)]})
    heights = sorted(r["h"] for r in rows)
    usual = heights[len(heights) // 2]
    steps = sorted(rows[i + 1]["y"] - rows[i]["y"] for i in range(len(rows) - 1))
    usual_step = steps[len(steps) // 2] if steps else 0
    out = ""
    for i, r in enumerate(rows):
        line = " ".join(t for _, t in sorted(r["parts"]))
        if i:
            if r["y"] - rows[i - 1]["y"] > usual_step + 0.8 * usual:
                out += "\n\n"
            elif out.endswith("-") and line[:1].islower():
                out = out[:-1]
            else:
                out += " "
        out += line
    return out


def read_photo(path):
    text = assemble(ocr(load_photo(path)))
    if not text.strip():
        raise Unreadable("No text was found in this picture.")
    return text


def read(path):
    """The text of a file, or Unreadable with a plain-words reason."""
    if not os.path.isfile(path):
        raise Unreadable("This file is no longer there.")
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXT:
        text = read_txt(path)
    elif ext in PHOTO_EXT:
        text = read_photo(path)
    else:
        raise Unreadable("ReciteFlow opens text files (.txt) and photos only.")
    if not text:
        raise Unreadable("This file has no text in it.")
    return text


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        out = {"text": read(sys.argv[1])}
    except Unreadable as e:
        out = {"error": str(e)}
    except Exception as e:
        out = {"error": f"Could not read this file ({type(e).__name__}: {e})."}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
