"""Which OCR reads a photo of a page best, with the least time, memory and GPU?

  py -3.12 engine/check_ocr.py make    # makes the test pictures in engine/clips/ocr/generated/
  py -3.12 engine/check_ocr.py run     # reads every picture with each OCR, scores it, writes the report

Pictures: the practice texts drawn as a page (fonts, sizes), then made to look like a clean scan, a phone photo
(tilt, perspective, shadow, blur, noise, JPEG), a dim photo, a far-away (small) photo and a dark-mode screenshot.
Real photos: put them in engine/clips/ocr/real/ with the true text next to each (same name, .txt).
Each OCR runs in its own process, one after the other (never two GPU jobs at once): model load time, time per
picture, peak RAM of that process, peak VRAM added (whole card, nvidia-smi), and words wrong (word error rate after
the same clean-up for all: lower case, no punctuation). Results in test_out/ocr/report.txt and results.json.
The app reads photos with RapidOCR (read_text.py). Tesseract (UB Mannheim build) and EasyOCR were tried and then
removed; `--engines tesseract,easyocr` needs them installed again (pip pytesseract, easyocr).
"""
import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time

from read_text import assemble

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLIPS = os.path.join(HERE, "clips")
OCR_DIR = os.path.join(CLIPS, "ocr")
GEN = os.path.join(OCR_DIR, "generated")
REAL = os.path.join(OCR_DIR, "real")
OUT = os.path.join(ROOT, "test_out", "ocr")
FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
ENGINES = ["windows", "windows_x2", "rapidocr"]   # also: "tesseract", "easyocr" (when installed)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".jfif", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".heic", ".heif")


# ---- Test pictures ----
def texts():
    """The practice texts (never para1-3): the 11 daily texts and the 3 long public ones."""
    out = []
    tts = os.path.join(CLIPS, "practice_tts")
    for name in sorted(os.listdir(tts)):
        if name.startswith("daily_") and name.endswith("_ok.txt"):
            out.append((name[:-7], open(os.path.join(tts, name), encoding="utf-8").read().strip()))
    long_dir = os.path.join(CLIPS, "public", "long")
    for name in sorted(os.listdir(long_dir)):
        if name.endswith(".txt") and ".mistakes" not in name:
            words = open(os.path.join(long_dir, name), encoding="utf-8").read().split()
            # two paragraphs, sentence case on the first word, as a printed page would be
            half = len(words) // 2
            paras = [" ".join(words[:half]), " ".join(words[half:])]
            out.append((name[:-4], "\n\n".join(p[0].upper() + p[1:] for p in paras)))
    return out


def wrap(draw, text, font, width):
    lines = []
    for para in text.split("\n\n"):
        line = ""
        for word in para.split():
            test = (line + " " + word).strip()
            if draw.textlength(test, font=font) <= width or not line:
                line = test
            else:
                lines.append(line)
                line = word
        lines.append(line)
        lines.append("")
    return lines[:-1]


def page(text, font_file, size, width=1400, ink=(25, 25, 25), paper=(255, 255, 255), margin=90):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(os.path.join(FONTS, font_file), size)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = wrap(probe, text, font, width - 2 * margin)
    step = int(size * 1.45)
    img = Image.new("RGB", (width, 2 * margin + step * len(lines)), paper)
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((margin, margin + i * step), line, font=font, fill=ink)
    return img


def photo(img, rng, dim=False, scale=1.0):
    """A phone photo of a printed page: page on a desk, tilted, in perspective, uneven light, blur, noise, JPEG."""
    import cv2
    import numpy as np
    from PIL import Image
    a = np.asarray(img).astype(np.float32)
    a = a * np.array([0.97, 0.95, 0.89], np.float32)   # paper, not screen white
    h, w = a.shape[:2]
    pad = int(0.12 * max(h, w))
    canvas = np.zeros((h + 2 * pad, w + 2 * pad, 3), np.float32)
    canvas[:] = (88, 72, 60)   # the desk
    canvas[pad:pad + h, pad:pad + w] = a
    H, W = canvas.shape[:2]
    j = lambda: rng.uniform(-0.035, 0.035) * W
    src = np.float32([[pad, pad], [pad + w, pad], [pad + w, pad + h], [pad, pad + h]])
    dst = np.float32([[pad + j(), pad + j()], [pad + w + j(), pad + j()], [pad + w + j(), pad + h + j()],
                      [pad + j(), pad + h + j()]])
    canvas = cv2.warpPerspective(canvas, cv2.getPerspectiveTransform(src, dst), (W, H), borderValue=(88, 72, 60))
    rot = cv2.getRotationMatrix2D((W / 2, H / 2), rng.uniform(-3.5, 3.5), 1.0)
    canvas = cv2.warpAffine(canvas, rot, (W, H), borderValue=(88, 72, 60))
    # light: brighter on one side, a soft shadow across a corner
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    ang = rng.uniform(0, 2 * np.pi)
    grad = 0.82 + 0.18 * ((np.cos(ang) * xx / W + np.sin(ang) * yy / H) + 1) / 2
    shadow = 1 - 0.28 * np.clip(1 - ((xx - rng.uniform(0, W)) ** 2 + (yy - rng.uniform(0, H)) ** 2) ** 0.5
                                / (0.45 * W), 0, 1)
    light = grad * shadow * (0.55 if dim else 1.0)
    canvas = canvas * light[..., None] + (35 if dim else 0) * (1 - light[..., None])
    canvas = cv2.GaussianBlur(canvas, (0, 0), 1.6 if dim else 1.1)
    canvas += rng.normal(0, 10 if dim else 5, canvas.shape)
    if scale != 1.0:
        canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8))


def make():
    import numpy as np
    os.makedirs(GEN, exist_ok=True)
    rng = random.Random(7)
    nrng = np.random.default_rng(7)

    class R:   # one seeded source for both
        uniform = staticmethod(lambda a, b: rng.uniform(a, b))
        normal = staticmethod(lambda m, s, shape: nrng.normal(m, s, shape))

    fonts = ["georgia.ttf", "times.ttf", "arial.ttf", "calibri.ttf", "cambria.ttc", "pala.ttf", "segoeui.ttf"]
    n = 0
    for i, (name, text) in enumerate(texts()):
        font = fonts[i % len(fonts)]
        base = os.path.join(GEN, name)
        page(text, font, 30).convert("L").save(base + "-scan.png")
        photo(page(text, font, 30), R).save(base + "-photo.jpg", quality=75)
        photo(page(text, font, 30), R, dim=True).save(base + "-dim.jpg", quality=60)
        photo(page(text, font, 26), R, scale=0.55).save(base + "-far.jpg", quality=75)
        page(text, "segoeui.ttf", 16, width=900, ink=(220, 222, 228), paper=(30, 32, 38), margin=24).save(
            base + "-screen.png")
        for kind in ("scan", "photo", "dim", "far", "screen"):
            with open(f"{base}-{kind}.txt", "w", encoding="utf-8") as f:
                f.write(text + "\n")
            n += 1
    print(f"{n} pictures in {GEN}")


# ---- The OCRs: each returns pieces of text as (corners, text): 4 corners (x, y) clockwise from top left ----
def load_rgb(path):
    from PIL import Image, ImageOps
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def make_windows_x2():
    """Windows OCR on the picture enlarged twice (small print reads better), kept under its size limit."""
    return make_windows(scale=2)


def make_windows(scale=1):
    import asyncio
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import (BitmapDecoder, BitmapTransform, ExifOrientationMode,
                                                BitmapPixelFormat, BitmapAlphaMode, ColorManagementMode,
                                                BitmapInterpolationMode)
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import StorageFile, FileAccessMode
    engine = OcrEngine.try_create_from_language(Language("en-US"))

    async def read(path):
        f = await StorageFile.get_file_from_path_async(os.path.abspath(path))
        stream = await f.open_async(FileAccessMode.READ)
        dec = await BitmapDecoder.create_async(stream)
        transform = BitmapTransform()
        k = min(scale, OcrEngine.max_image_dimension / max(dec.oriented_pixel_width, dec.oriented_pixel_height))
        if k > 1:
            # the transform sizes the picture before it is turned upright: the stored width and height
            transform.scaled_width, transform.scaled_height = int(dec.pixel_width * k), int(dec.pixel_height * k)
            transform.interpolation_mode = BitmapInterpolationMode.FANT
        bmp = await dec.get_software_bitmap_async(BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED,
                                                  transform, ExifOrientationMode.RESPECT_EXIF_ORIENTATION,
                                                  ColorManagementMode.DO_NOT_COLOR_MANAGE)
        res = await engine.recognize_async(bmp)
        out = []
        for line in res.lines:
            rs = [w.bounding_rect for w in line.words]
            out.append((rect(min(r.x for r in rs), min(r.y for r in rs), max(r.x + r.width for r in rs),
                             max(r.y + r.height for r in rs)), line.text))
        return out
    return lambda path: asyncio.run(read(path))


def make_rapidocr():
    import numpy as np
    from rapidocr import RapidOCR
    engine = RapidOCR(params={"Global.log_level": "error"})

    def read(path):
        res = engine(np.asarray(load_rgb(path))[:, :, ::-1].copy())
        if res.boxes is None:
            return []
        return [([(float(x), float(y)) for x, y in b], t) for b, t in zip(res.boxes, res.txts)]
    return read


def make_tesseract():
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = TESSERACT

    def read(path):
        d = pytesseract.image_to_data(load_rgb(path), lang="eng", output_type=pytesseract.Output.DICT)
        lines = {}
        for i, word in enumerate(d["text"]):
            if not word.strip():
                continue
            key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
            x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
            if key in lines:
                x0, y0, x1, y1, t = lines[key]
                lines[key] = (min(x0, x), min(y0, y), max(x1, x + w), max(y1, y + h), t + " " + word)
            else:
                lines[key] = (x, y, x + w, y + h, word)
        return [(rect(*b[:4]), b[4]) for b in lines.values()]
    return read


def make_easyocr():
    import numpy as np
    import easyocr
    reader = easyocr.Reader(["en"], gpu=True, verbose=False)

    def read(path):
        res = reader.readtext(np.asarray(load_rgb(path)))
        return [([(float(x), float(y)) for x, y in b], t) for b, t, _ in res]
    return read


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ---- Scoring ----
def norm_words(text):
    text = text.lower().replace("’", "'").replace("‘", "'")
    return "".join(c if c.isalnum() or c == "'" else " " for c in text).split()


def wer(truth, got):
    from rapidfuzz.distance import Levenshtein
    t = norm_words(truth)
    return Levenshtein.distance(t, norm_words(got)) / max(1, len(t))


def cer(truth, got):
    from rapidfuzz.distance import Levenshtein
    t = " ".join(norm_words(truth))
    return Levenshtein.distance(t, " ".join(norm_words(got))) / max(1, len(t))


# ---- Running ----
def pictures():
    out = []
    for folder, kind in ((GEN, None), (REAL, "real")):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(IMAGE_EXT):
                truth = os.path.join(folder, os.path.splitext(name)[0] + ".txt")
                if os.path.exists(truth):
                    k = kind or os.path.splitext(name)[0].rsplit("-", 1)[1]
                    out.append({"path": os.path.join(folder, name), "truth": truth, "kind": k})
    return out


def worker(name, list_file, out_file):
    t0 = time.perf_counter()
    read = globals()["make_" + name]()
    load_s = time.perf_counter() - t0
    results = []
    for item in json.load(open(list_file, encoding="utf-8")):
        t = time.perf_counter()
        try:
            text, error = assemble(read(item["path"])), None
        except Exception as e:   # one unreadable picture must not hide the others
            text, error = "", f"{type(e).__name__}: {e}"
        results.append({**item, "text": text, "seconds": time.perf_counter() - t, "error": error})
    json.dump({"load_s": load_s, "results": results}, open(out_file, "w", encoding="utf-8"), indent=1)


def vram_used():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout
        return int(out.split()[0])
    except Exception:
        return None


def run_engine(name, items):
    import psutil
    os.makedirs(OUT, exist_ok=True)
    list_file = os.path.join(OUT, "pictures.json")
    json.dump(items, open(list_file, "w", encoding="utf-8"))
    out_file = os.path.join(OUT, f"raw-{name}.json")
    idle = vram_used()
    t0 = time.perf_counter()
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "worker", name, list_file, out_file])
    proc = psutil.Process(p.pid)
    peak = {"rss": 0, "vram": idle or 0}

    def watch():
        while p.poll() is None:
            try:
                rss = proc.memory_info().rss + sum(c.memory_info().rss for c in proc.children(recursive=True))
                peak["rss"] = max(peak["rss"], rss)
            except psutil.Error:
                pass
            v = vram_used()
            if v is not None:
                peak["vram"] = max(peak["vram"], v)
            time.sleep(0.25)
    th = threading.Thread(target=watch, daemon=True)
    th.start()
    p.wait()
    th.join()
    total = time.perf_counter() - t0
    if p.returncode:
        return {"engine": name, "failed": f"exit code {p.returncode}"}
    raw = json.load(open(out_file, encoding="utf-8"))
    for r in raw["results"]:
        truth = open(r["truth"], encoding="utf-8").read()
        r["wer"], r["cer"] = wer(truth, r["text"]), cer(truth, r["text"])
    return {"engine": name, "load_s": raw["load_s"], "total_s": total, "peak_ram_mib": peak["rss"] / 2 ** 20,
            "vram_added_mib": (peak["vram"] - idle) if idle is not None else None, "results": raw["results"]}


def report(runs):
    kinds = []
    for run in runs:
        for r in run.get("results", []):
            if r["kind"] not in kinds:
                kinds.append(r["kind"])
    head = f"{'OCR':<10} {'words wrong %':>13} " + " ".join(f"{k:>7}" for k in kinds) + \
        f" {'s/picture':>9} {'load s':>7} {'RAM MiB':>8} {'VRAM MiB':>8}"
    lines = [head, "-" * len(head)]
    for run in runs:
        if "failed" in run:
            lines.append(f"{run['engine']:<10} failed: {run['failed']}")
            continue
        rs = run["results"]
        words = lambda sel: 100 * sum(r["wer"] * len(norm_words(open(r["truth"], encoding="utf-8").read()))
                                      for r in sel) / max(1, sum(len(norm_words(open(r["truth"], encoding="utf-8")
                                                                                .read())) for r in sel))
        by_kind = " ".join(f"{words([r for r in rs if r['kind'] == k]):7.1f}" for k in kinds)
        per = sorted(r["seconds"] for r in rs)[len(rs) // 2]
        vram = "-" if run["vram_added_mib"] is None else f"{run['vram_added_mib']:.0f}"
        errors = sum(1 for r in rs if r["error"])
        lines.append(f"{run['engine']:<10} {words(rs):13.1f} {by_kind} {per:9.2f} {run['load_s']:7.1f} "
                     f"{run['peak_ram_mib']:8.0f} {vram:>8}" + (f"  ({errors} pictures failed)" if errors else ""))
    lines += ["", "words wrong % = word error rate over all words (lower is better), then by picture kind;",
              "s/picture = median time to read one picture after loading; RAM = peak of the OCR's own process;",
              "VRAM = peak added on the whole card (nvidia-smi)."]
    return "\n".join(lines)


def run(engines):
    items = pictures()
    if not items:
        sys.exit("No pictures: run `check_ocr.py make` first.")
    print(f"{len(items)} pictures, OCRs: {', '.join(engines)}")
    runs = []
    for name in engines:
        print(f"-- {name}", flush=True)
        runs.append(run_engine(name, items))
    text = report(runs)
    print(text)
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, "report.txt"), "w", encoding="utf-8").write(text + "\n")
    json.dump(runs, open(os.path.join(OUT, "results.json"), "w", encoding="utf-8"), indent=1)
    print("report:", os.path.join(OUT, "report.txt"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["make", "run", "worker"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--engines", default=",".join(ENGINES))
    a = ap.parse_args()
    if a.step == "make":
        make()
    elif a.step == "worker":
        worker(*a.rest)
    else:
        run(a.engines.split(","))
