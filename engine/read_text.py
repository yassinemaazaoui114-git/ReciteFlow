"""Reads the text of a file for the text box: a .txt file, a PDF, or a photo of a page (OCR).

  py -3.12 engine/read_text.py <file> [--pages 3-4]
      -> one JSON line on stdout: {"text": "...", "ocr": true/false}, {"pages": n} (a PDF of more than 2 pages with
         no pages chosen), or {"error": "<plain words>"}

The window starts it once per file, so the OCR's memory (~0.6 GB while it reads) is given back as soon as it is done
and the speech engine is never touched. OCR = RapidOCR (PP-OCR models run with onnxruntime on the CPU, no GPU); it
read the test photos best of the four OCRs tried (engine/check_ocr.py). Photos Pillow cannot open (HEIC from a
phone) are decoded by Windows. PDFs are read with PyMuPDF: the typed text directly, the pictures in it with the
OCR. Everything stays on this PC.
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


_ocr_engine = None


def ocr(rgb):
    """RapidOCR: pieces of text as (4 corners, text). Loaded once per process (a PDF may need it for several
    pictures)."""
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr import RapidOCR
        _ocr_engine = RapidOCR(params={"Global.log_level": "error"})
    res = _ocr_engine(rgb[:, :, ::-1].copy())   # it takes BGR, as OpenCV
    if res.boxes is None:
        return []
    return [([(float(x), float(y)) for x, y in box], text) for box, text in zip(res.boxes, res.txts)]


def assemble(pieces, text_only=False):
    """Pieces -> plain text. The page may be tilted: the pieces are first turned by the text's usual angle, then
    pieces on the same row become one line (left to right), rows top to bottom; a larger gap than usual between rows
    starts a new paragraph; a word cut by a hyphen at a line end is joined again. text_only: a graph's labels are
    left out (text_lines)."""
    rows = ocr_rows(pieces)
    if text_only:
        rows = [r for r, keep in zip(rows, text_lines([r["pieces"] for r in rows])) if keep]
    return join_rows(rows)


def ocr_rows(pieces):
    """Pieces -> rows {"y", "h", "line"}, top to bottom, in the page's upright frame."""
    pieces = [(q, t.strip()) for q, t in pieces if t.strip()]
    if not pieces:
        return []
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
    out = []
    for r in rows:
        pieces = [t for _, t in sorted(r["parts"])]
        out.append({"y": r["y"], "h": r["h"], "line": " ".join(pieces), "pieces": pieces})
    return out


def join_rows(rows):
    """Rows {"y", "h", "line"} top to bottom -> text: a larger gap than usual between rows starts a new paragraph, a
    row with "para" is a paragraph of its own, a word cut by a hyphen at a line end is joined again."""
    if not rows:
        return ""
    heights = sorted(r["h"] for r in rows if not r.get("para")) or [0]
    usual = heights[len(heights) // 2]
    steps = sorted(b["y"] - a["y"] for a, b in zip(rows, rows[1:]) if not a.get("para") and not b.get("para"))
    usual_step = steps[len(steps) // 2] if steps else 0
    out = ""
    for i, r in enumerate(rows):
        line = r["line"]
        if i:
            if r.get("para") or rows[i - 1].get("para") or r["y"] - rows[i - 1]["y"] > usual_step + 0.8 * usual:
                out += "\n\n"
            elif out.endswith("-") and line[:1].islower():
                out = out[:-1]
            else:
                out += " "
        out += line
    return out


def text_lines(lines):
    """Which lines are text to recite, for the lines found inside a picture or a drawing. A line is given as its
    pieces (bits of text with a wide gap between them). A label is a piece of one or two words, or of mostly numbers
    and signs; a line of labels only (an axis, a row of a table) is a label line. When most of the words are in label
    lines, it is a graph or a table, and only its sentences (4 words or more, not labels) are kept; otherwise every
    line is kept (a scanned page, a poem)."""
    def label(words):
        return len(words) <= 2 or sum(any(c.isalpha() for c in w) for w in words) < len(words) / 2
    labels = [all(label(p.split()) for p in pieces) for pieces in lines]
    sizes = [sum(len(p.split()) for p in pieces) for pieces in lines]
    if sum(n for n, lab in zip(sizes, labels) if lab) < sum(sizes) / 2:
        return [True] * len(lines)
    return [n >= 4 and not lab for n, lab in zip(sizes, labels)]


def read_photo(path):
    text = assemble(ocr(load_photo(path)))
    if not text.strip():
        raise Unreadable("No text was found in this picture.")
    return text


# ---- PDFs ----
MAX_PDF_PAGES = 2     # a recitation is a short part: at most 2 pages are read
PDF_DPI = 200         # pictures in a PDF are drawn at this size for the OCR (long side at most 3000 pixels)
GRAPH_PT = 50         # drawings at least this wide and tall (points, 72 per inch) may be a graph or a table
LABEL_PT = 24         # how far around such a drawing its labels are looked for
SMALL_PT = 30         # pictures smaller than this (an icon, a logo) are not read


def open_pdf(path):
    import pymupdf
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception:
        raise Unreadable("Could not open this PDF (it may be damaged, or not a PDF).") from None
    if doc.needs_pass:
        raise Unreadable("This PDF is locked with a password.")
    if not doc.page_count:
        raise Unreadable("This PDF has no pages.")
    return doc


def parse_pages(spec, count):
    """'3', '3-4' or '3, 7' -> page numbers (from 1), at most MAX_PDF_PAGES, all inside the PDF."""
    pages = []
    for part in spec.replace(" ", "").split(","):
        a, _, b = part.partition("-")
        if not a.isdigit() or (b and not b.isdigit()):
            raise Unreadable("Write the pages as 3, or 3-4, or 3, 7.")
        first, last = int(a), int(b or a)
        if first > last:
            first, last = last, first
        if last - first + 1 > MAX_PDF_PAGES:
            raise Unreadable(f"Pick at most {MAX_PDF_PAGES} pages.")
        pages += [p for p in range(first, last + 1) if p not in pages]
    if len(pages) > MAX_PDF_PAGES:
        raise Unreadable(f"Pick at most {MAX_PDF_PAGES} pages.")
    if any(p < 1 or p > count for p in pages):
        raise Unreadable(f"This PDF has pages 1 to {count}.")
    return sorted(pages)


def same_words(a, b):
    """How much of text a is also in text b (0 to 1), word by word."""
    from collections import Counter
    wa = Counter(w.lower().strip(".,;:!?\"'()") for w in a.split())
    wb = Counter(w.lower().strip(".,;:!?\"'()") for w in b.split())
    return sum((wa & wb).values()) / max(1, sum(wa.values()))


def read_pdf_page(page):
    """One page -> (text, used the OCR). Both ways: the typed text is read directly, and every picture on the page
    is read with the OCR (a scanned page is one big picture); a picture whose words are already typed on it (a
    scan with a hidden text layer) is not added twice. Inside a picture or a large drawing, a graph's or a table's
    labels are left out (text_lines); only text is kept."""
    import numpy as np
    import pymupdf
    area = page.rect
    pictures = []
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & area
        if r.width >= SMALL_PT and r.height >= SMALL_PT and all(r != p for p in pictures):
            pictures.append(r)
    # a drawing's labels sit just outside its lines (the numbers left of an axis, the names under the bars)
    drawings = [(r + (-LABEL_PT, -LABEL_PT, LABEL_PT, LABEL_PT)) & area for r in page.cluster_drawings()
                if r.width >= GRAPH_PT and r.height >= GRAPH_PT]
    regions = pictures + [d for d in drawings if not any(p.contains(d) for p in pictures)]

    # typed lines, in the page's reading order, each tied to the picture or drawing it lies in (or none); a line's
    # pieces are its bits of text with more than a letter's width between them
    flags = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
    lines = []
    for block in page.get_text("dict", flags=flags, sort=True)["blocks"]:
        for ln in block.get("lines", []):
            pieces, last = [], None
            for s in ln["spans"]:
                if not s["text"].strip():
                    continue
                if last is None or s["bbox"][0] - last["bbox"][2] > 0.6 * s["size"]:
                    pieces.append("")
                pieces[-1] += s["text"]
                last = s
            pieces = [" ".join(p.split()) for p in pieces]
            if not pieces:
                continue
            box = pymupdf.Rect(ln["bbox"])
            mid = pymupdf.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
            inside = next((i for i, r in enumerate(regions) if r.contains(mid)), None)
            lines.append({"y": mid.y, "h": box.height, "line": " ".join(pieces), "pieces": pieces, "region": inside})
    for i in range(len(regions)):
        mine = [ln for ln in lines if ln["region"] == i]
        for ln, keep in zip(mine, text_lines([ln["pieces"] for ln in mine])):
            ln["keep"] = keep
    rows = [ln for ln in lines if ln.get("keep", True)]
    typed = " ".join(ln["line"] for ln in lines)

    # the pictures, read with the OCR at PDF_DPI (smaller when the picture is very large)
    used_ocr = False
    for r in pictures:
        zoom = min(PDF_DPI / 72, 3000 / max(r.width, r.height))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=r, colorspace=pymupdf.csRGB, alpha=False)
        rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
        text = assemble(ocr(rgb), text_only=True)
        if not text.strip() or (typed and same_words(text, typed) >= 0.6):   # already typed on the page
            continue
        used_ocr = True
        # where the picture is on the page: before the first typed line below its top (columns keep their order)
        at = next((k for k, ln in enumerate(rows) if not ln.get("para") and ln["y"] > r.y0), len(rows))
        rows.insert(at, {"y": r.y0, "h": 0, "line": text, "para": True})
    return join_rows(rows), used_ocr


def read_pdf(path, pages=None):
    """{"text", "ocr"} of the chosen pages, or {"pages": n} when a PDF has more pages than can be read at once
    and none were chosen."""
    doc = open_pdf(path)
    if pages is None:
        if doc.page_count > MAX_PDF_PAGES:
            return {"pages": doc.page_count}
        pages = list(range(1, doc.page_count + 1))
    else:
        pages = parse_pages(pages, doc.page_count)
    texts, used_ocr = [], False
    for p in pages:
        text, o = read_pdf_page(doc[p - 1])
        used_ocr |= o
        if text.strip():
            texts.append(text)
    if not texts:
        raise Unreadable("No text was found on " + ("this page." if len(pages) == 1 else "these pages."))
    return {"text": "\n\n".join(texts), "ocr": used_ocr}


def read(path, pages=None):
    """The text of a file as {"text", "ocr": read by the OCR}, {"pages": n} for a long PDF with no pages chosen,
    or Unreadable with a plain-words reason."""
    if not os.path.isfile(path):
        raise Unreadable("This file is no longer there.")
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXT:
        out = {"text": read_txt(path), "ocr": False}
    elif ext in PHOTO_EXT:
        out = {"text": read_photo(path), "ocr": True}
    elif ext == ".pdf":
        out = read_pdf(path, pages)
    else:
        raise Unreadable("ReciteFlow opens text files (.txt), PDFs and photos only.")
    if "text" in out and not out["text"]:
        raise Unreadable("This file has no text in it.")
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    pages = args[args.index("--pages") + 1] if "--pages" in args else None
    try:
        out = read(args[0], pages)
    except Unreadable as e:
        out = {"error": str(e)}
    except Exception as e:
        out = {"error": f"Could not read this file ({type(e).__name__}: {e})."}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
