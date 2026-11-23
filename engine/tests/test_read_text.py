"""read_text.py: text files in any usual encoding, the line joining of OCR pieces, a photo read end to end."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import read_text
from read_text import Unreadable, assemble

ENGINE = Path(__file__).resolve().parents[1]


def _box(x, y, w, h, text):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], text


def test_txt_encodings(tmp_path):
    text = "Café au lait,\r\nnaïve “words”."
    for enc in ("utf-8", "utf-8-sig", "utf-16", "cp1252"):
        f = tmp_path / f"{enc}.txt"
        f.write_bytes(text.encode(enc))
        assert read_text.read(str(f)) == {"text": "Café au lait,\nnaïve “words”.", "ocr": False}


def test_refusals(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n")
    binary = tmp_path / "data.txt"
    binary.write_bytes(b"\x00\x01\x02abc")
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a picture")
    other = tmp_path / "notes.docx"
    other.write_bytes(b"x")
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"%PDF-1.7\nnot a pdf")
    for path, words in ((empty, "no text"), (binary, "does not look like text"), (broken, "Could not open"),
                        (other, "PDFs and photos only"), (tmp_path / "gone.txt", "no longer there"),
                        (bad_pdf, "Could not open this PDF")):
        with pytest.raises(Unreadable, match=words):
            read_text.read(str(path))


def test_assemble_rows_paragraphs_hyphens():
    pieces = [_box(300, 100, 200, 20, "world,"), _box(10, 100, 250, 20, "Hello"),   # one row, two pieces
              _box(10, 130, 400, 20, "a long exam-"), _box(10, 160, 300, 20, "ple here."),
              _box(10, 230, 300, 20, "New paragraph.")]
    assert assemble(pieces) == "Hello world, a long example here.\n\nNew paragraph."
    assert assemble([]) == ""


def test_assemble_tilted_page_keeps_row_order():
    # long lines tilted by 6 degrees: by plain height the second row's left end is above the first row's right end
    import math
    a = math.radians(6)
    pieces = []
    for i, text in enumerate(["first row of text", "second row of text", "third row of text"]):
        x0, y0, w, h = 0, 100 + 40 * i, 1000, 24
        q = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
        pieces.append(([(x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)) for x, y in q], text))
    assert assemble(pieces[::-1]) == "first row of text second row of text third row of text"


def test_text_lines_leaves_out_graph_labels():
    chart = [["Sales 2024"], ["40"], ["30"], ["20"], ["10"], ["0"], ["Jan", "Feb", "Mar", "Apr", "May"],
             ["Bars show the sales of each month."]]
    assert read_text.text_lines(chart) == [False] * 7 + [True]
    poem = [["The woods are lovely,"], ["dark and deep,"], ["But I have promises"], ["to keep."]]
    assert read_text.text_lines(poem) == [True] * 4


def test_parse_pages():
    assert read_text.parse_pages("3", 12) == [3]
    assert read_text.parse_pages(" 4-3 ", 12) == [3, 4]
    assert read_text.parse_pages("9, 4", 12) == [4, 9]
    for spec, words in (("3-5", "at most 2"), ("1, 2, 3", "at most 2"), ("13", "pages 1 to 12"), ("0", "pages 1"),
                        ("a", "Write the pages"), ("", "Write the pages")):
        with pytest.raises(Unreadable, match=words):
            read_text.parse_pages(spec, 12)


def test_pdf_pages_and_lock(tmp_path):
    import pymupdf
    doc = pymupdf.open()
    for i in range(1, 4):
        doc.new_page().insert_text((72, 72), f"This is the text of page {i}.")
    path = tmp_path / "three.pdf"
    doc.save(path)
    assert read_text.read(str(path)) == {"pages": 3}
    assert read_text.read(str(path), "1, 3") == {"text": "This is the text of page 1.\n\nThis is the text of page 3.",
                                                 "ocr": False}
    locked = tmp_path / "locked.pdf"
    doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="a", owner_pw="b")
    with pytest.raises(Unreadable, match="locked with a password"):
        read_text.read(str(locked))


def test_photo_end_to_end(tmp_path):
    """A page drawn with a Windows font, saved as a JPEG, read by the script as the window runs it."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (900, 200), "white")
    font = ImageFont.truetype("arial.ttf", 30)
    d = ImageDraw.Draw(img)
    d.text((40, 40), "The shop is open from eight to six,", font=font, fill="black")
    d.text((40, 90), "and it is closed on Sundays.", font=font, fill="black")
    photo = tmp_path / "page.jpg"
    img.save(photo, quality=85)
    out = subprocess.run([sys.executable, str(ENGINE / "read_text.py"), str(photo)], capture_output=True,
                         encoding="utf-8", timeout=120)
    assert json.loads(out.stdout) == {"text": "The shop is open from eight to six, and it is closed on Sundays.",
                                      "ocr": True}


def test_windows_decoder_reads_upright(tmp_path):
    """The fallback for photos Pillow cannot open (HEIC): Windows decodes them, turned as the camera held them."""
    from PIL import Image
    img = Image.new("RGB", (40, 20), (200, 10, 10))
    exif = Image.Exif()
    exif[0x0112] = 6   # orientation: turned 90 degrees
    photo = tmp_path / "turned.jpg"
    img.save(photo, exif=exif, quality=95)
    rgb = read_text.windows_decode(str(photo))
    assert rgb.shape == (40, 20, 3)
    assert abs(int(rgb[10, 10, 0]) - 200) < 20
