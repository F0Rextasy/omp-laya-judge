"""Regenerate assets/before-after.gif from benchmark/results.json.

LLM side numbers are constants from the head-to-head run (same 12
questions through `omp -p`, 2026-09: 16-25s sampled, 18.4s mean,
728 tokens mean). The laya side is always the committed bench result.
"""
import json
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
RES = json.load(open(os.path.join(HERE, "results.json")))
LLM_SECONDS = 18.4
LLM_TOKENS = 728

W, H = 640, 360
BG, FG, DIM = "#0d1117", "#e6edf3", "#8b949e"
RED, GREEN = "#f85149", "#3fb950"
BAR_X, BAR_W, BAR_H = 150, 30, 30


def font(size):
    for name in ("consola.ttf", "cour.ttf", "DejaVuSansMono.ttf",
                 "C:/Windows/Fonts/consola.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_TITLE, F_LABEL = font(14), font(13)

laya_ms = RES["mean_ms"]
rows = [
    (58, f"LLM {LLM_SECONDS}s", RED),
    (104, f"laya {laya_ms}ms", GREEN),
    (184, f"LLM {LLM_TOKENS} tok", RED),
    (230, "laya 0 tok", GREEN),
]


def frame(progress):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((20, 18), "judge()/per question: LLM vs laya-judge (measured)",
           font=F_TITLE, fill=FG)
    for y, label, color in rows:
        d.text((24, y + 8), label, font=F_LABEL, fill=DIM)
        w = max(2, round(BAR_W * progress))
        d.rectangle([BAR_X, y, BAR_X + w, y + BAR_H], fill=color)
    return img


frames = ([frame(1.0)] + [frame(p / 23) for p in range(24)]
          + [frame(1.0)] * 8)
out = os.path.join(HERE, "..", "assets", "before-after.gif")
os.makedirs(os.path.dirname(out), exist_ok=True)
frames[0].save(out, save_all=True, append_images=frames[1:], duration=70,
               loop=0, optimize=True)
print("wrote", out, f"({len(frames)} frames, laya {laya_ms}ms)")
