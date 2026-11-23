"""How well is a PDF read for the text box, and how fast?

  py -3.12 engine/check_pdf.py make    # makes the test PDFs in engine/clips/ocr/pdf/ (needs check_ocr.py make first)
  py -3.12 engine/check_pdf.py run     # reads each one as the window does, scores it, writes the report

Test PDFs: typed text (2 pages); scanned pages (a clean scan and a phone photo, no typed text); a scan with a hidden
text layer (must not come out twice); a mixed page (typed paragraph, a picture of a paragraph, a picture of a bar
chart and a drawn bar chart: only the two paragraphs must come out); a 12-page PDF read at pages 4 and 9 (and asked
for its pages first); a locked PDF and a broken one (plain-words messages). Each is read by read_text.py in its own
process, as the window runs it: time and words wrong (as check_ocr.py). Report in test_out/pdf/report.txt.
"""
import json
import os
import subprocess
import sys
import time

from check_ocr import GEN, OCR_DIR, texts, wer

HERE = os.path.dirname(os.path.abspath(__file__))
PDF = os.path.join(OCR_DIR, "pdf")
OUT = os.path.join(os.path.dirname(HERE), "test_out", "pdf")
A4 = (595, 842)


def typed_page(doc, text, size=12):
    import pymupdf
    page = doc.new_page(width=A4[0], height=A4[1])
    left = page.insert_textbox(pymupdf.Rect(60, 60, A4[0] - 60, A4[1] - 60), text, fontsize=size, fontname="tiro")
    assert left >= 0, "text does not fit the page"
    return page


def picture_page(doc, picture, text_layer=None):
    """A page that is one picture (a scan); text_layer = the same words typed invisibly over it (searchable scan)."""
    import pymupdf
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_image(page.rect, filename=picture, keep_proportion=True)
    if text_layer:
        page.insert_textbox(pymupdf.Rect(60, 60, A4[0] - 60, A4[1] - 60), text_layer, fontsize=12, render_mode=3)
    return page


def bar_chart_picture(path):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (900, 560), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype("arial.ttf", 24)
    d.text((330, 14), "Sales 2024", font=font, fill="black")
    d.line((90, 470, 860, 470), fill="black", width=3)
    d.line((90, 70, 90, 470), fill="black", width=3)
    for i, v in enumerate((0, 10, 20, 30, 40)):
        d.text((40, 460 - i * 95), str(v), font=font, fill="black")
    for i, (month, h) in enumerate(zip(("Jan", "Feb", "Mar", "Apr", "May", "Jun"), (220, 310, 180, 360, 270, 330))):
        x = 130 + i * 120
        d.rectangle((x, 470 - h, x + 70, 470), fill=(70, 110, 200))
        d.text((x + 10, 485), month, font=font, fill="black")
    img.save(path)


def drawn_bar_chart(page, top):
    import pymupdf
    x0, y0, x1, y1 = 120, top, 480, top + 200
    page.draw_line((x0, y1), (x1, y1))
    page.draw_line((x0, y0), (x0, y1))
    for i, v in enumerate((0, 25, 50, 75)):
        page.insert_text((x0 - 24, y1 - i * 60 + 4), str(v), fontsize=9)
    for i, (name, h) in enumerate(zip(("North", "South", "East", "West", "Center"), (120, 160, 90, 175, 140))):
        x = x0 + 20 + i * 70
        page.draw_rect(pymupdf.Rect(x, y1 - h, x + 40, y1), color=(0.2, 0.4, 0.7), fill=(0.3, 0.5, 0.8))
        page.insert_text((x + 2, y1 + 14), name, fontsize=9)
    page.insert_text((x0 + 120, y0 - 8), "Visitors per region", fontsize=10)


def make():
    import pymupdf
    if not os.path.isdir(GEN):
        sys.exit("No test pictures: run `check_ocr.py make` first.")
    os.makedirs(PDF, exist_ok=True)
    ts = dict(texts())
    daily = [n for n in ts if n.startswith("daily_")]
    truths = {}

    def save(doc, name, truth):
        doc.save(os.path.join(PDF, name))
        truths[name] = truth

    doc = pymupdf.open()
    for n in daily[:2]:
        typed_page(doc, ts[n])
    save(doc, "text.pdf", "\n\n".join(ts[n] for n in daily[:2]))

    doc = pymupdf.open()
    picture_page(doc, os.path.join(GEN, daily[2] + "-scan.png"))
    picture_page(doc, os.path.join(GEN, daily[3] + "-photo.jpg"))
    save(doc, "scanned.pdf", ts[daily[2]] + "\n\n" + ts[daily[3]])

    doc = pymupdf.open()
    picture_page(doc, os.path.join(GEN, daily[4] + "-scan.png"), text_layer=ts[daily[4]])
    save(doc, "scanned-text-layer.pdf", ts[daily[4]])

    # mixed: typed paragraph, picture of a paragraph, a bar chart picture, a drawn bar chart
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_textbox(pymupdf.Rect(60, 50, A4[0] - 60, 170), ts[daily[5]], fontsize=11, fontname="tiro")
    page.insert_image(pymupdf.Rect(60, 180, A4[0] - 60, 400), filename=os.path.join(GEN, daily[6] + "-scan.png"))
    chart = os.path.join(PDF, "chart.png")
    bar_chart_picture(chart)
    page.insert_image(pymupdf.Rect(130, 410, 465, 618), filename=chart)
    drawn_bar_chart(page, 640)
    os.remove(chart)
    save(doc, "mixed.pdf", ts[daily[5]] + "\n\n" + ts[daily[6]])

    doc = pymupdf.open()
    for i in range(12):
        typed_page(doc, ts[daily[i % len(daily)]])
    save(doc, "long.pdf", ts[daily[3 % len(daily)]] + "\n\n" + ts[daily[8 % len(daily)]])   # pages 4 and 9

    doc = pymupdf.open()
    typed_page(doc, ts[daily[0]])
    doc.save(os.path.join(PDF, "locked.pdf"), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret",
             owner_pw="owner")
    with open(os.path.join(PDF, "broken.pdf"), "wb") as f:
        f.write(b"%PDF-1.7\nthis is not really a PDF\n")
    for name, truth in truths.items():
        with open(os.path.join(PDF, name[:-4] + ".txt"), "w", encoding="utf-8") as f:
            f.write(truth + "\n")
    print(f"{len(truths) + 2} PDFs in {PDF}")


def read(name, pages=None):
    args = [sys.executable, os.path.join(HERE, "read_text.py"), os.path.join(PDF, name)]
    if pages:
        args += ["--pages", pages]
    t = time.perf_counter()
    out = subprocess.run(args, capture_output=True, encoding="utf-8", timeout=300)
    return json.loads(out.stdout.strip().splitlines()[-1]), time.perf_counter() - t


def run():
    if not os.path.isdir(PDF):
        sys.exit("No test PDFs: run `check_pdf.py make` first.")
    rows, failed = [], 0

    def check(ok, text):
        nonlocal failed
        failed += not ok
        rows.append(f"{'PASS' if ok else 'FAIL'}  {text}")
        print(rows[-1], flush=True)

    for name, pages in (("text.pdf", None), ("scanned.pdf", None), ("scanned-text-layer.pdf", None),
                        ("mixed.pdf", None), ("long.pdf", "4, 9")):
        truth = open(os.path.join(PDF, name[:-4] + ".txt"), encoding="utf-8").read()
        res, s = read(name, pages)
        got = res.get("text", "")
        w = wer(truth, got)
        ocr_expected = name not in ("text.pdf", "long.pdf", "scanned-text-layer.pdf")
        ok = "text" in res and w <= (0.05 if ocr_expected else 0.0) and res.get("ocr") == ocr_expected
        check(ok, f"{name}{' pages ' + pages if pages else ''}: {100 * w:.1f}% words wrong, {s:.1f} s, "
                  f"OCR used: {res.get('ocr')}" + (f", error: {res['error']}" if "error" in res else ""))
        with open(os.path.join(OUT, name[:-4] + ".got.txt"), "w", encoding="utf-8") as f:
            f.write(got + "\n")
    res, s = read("long.pdf")
    check(res == {"pages": 12}, f"long.pdf with no pages chosen: {res} in {s:.1f} s")
    for pages, words in (("3-5", "at most 2"), ("13", "pages 1 to 12"), ("x", "Write the pages")):
        res, _ = read("long.pdf", pages)
        check(words in res.get("error", ""), f"long.pdf pages {pages!r}: {res}")
    for name, words in (("locked.pdf", "locked with a password"), ("broken.pdf", "Could not open this PDF")):
        res, _ = read(name)
        check(words in res.get("error", ""), f"{name}: {res}")
    rows.append(f"{failed} FAILED" if failed else "ALL PASS")
    open(os.path.join(OUT, "report.txt"), "w", encoding="utf-8").write("\n".join(rows) + "\n")
    print(rows[-1], "- report:", os.path.join(OUT, "report.txt"))


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    if step == "make":
        make()
    elif step == "run":
        os.makedirs(OUT, exist_ok=True)
        run()
    else:
        sys.exit(__doc__)
