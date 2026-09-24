"""Regenerate assets/before-after.gif from benchmark/results.json.

LLM side numbers are constants from the head-to-head run (same 12
questions through `omp -p`, 2026-09: 16-25s sampled, 18.4s mean,
728 tokens mean). The laya side is always the committed bench result.
Shares the demo/ style contract (badges, bars, GitHub-dark palette).
"""
import json
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "demo"))
import style as S  # noqa: E402

RES = json.load(open(os.path.join(HERE, "results.json")))
LLM_SECONDS = 18.4
LLM_TOKENS = 728

W, H = 640, 360
laya_ms = RES["mean_ms"]
llm_ms = int(LLM_SECONDS * 1000)


def group(d, y, title, rows, max_val):
    """Section header + two proportional bars. Rows are (label, val, text, color)."""
    d.text((20, y), title, font=S.font(12, bold=True), fill=S.DIM)
    y += 22
    for label, val, text, color in rows:
        frac = (val / max_val) if max_val else 0
        S.prob_bar(d, 20, y, 420, frac, color, label=label, value=text,
                   label_w=70, size=15, h=16)
        y += 30
    return y


def frame(progress):
    im = Image.new("RGB", (W, H), S.BG)
    d = ImageDraw.Draw(im)
    d.text((20, 16), "judge() per question — measured head-to-head",
           font=S.font(17, bold=True), fill=S.FG)
    S.badge_row(d, 20, 44, [("12-case bench", S.BLUE), ("CPU", S.PURPLE),
                            ("0 tokens (laya)", S.GREEN)], size=12)
    y = group(d, 84, "LATENCY / QUESTION",
              [("LLM", llm_ms * progress, f"{LLM_SECONDS}s", S.RED),
               ("laya", laya_ms * progress, f"{laya_ms}ms", S.GREEN)], llm_ms)
    group(d, y + 14, "TOKENS / QUESTION",
          [("LLM", LLM_TOKENS * progress, f"{LLM_TOKENS} tok", S.RED),
           ("laya", 0, "0 tok", S.GREEN)], LLM_TOKENS)
    d.text((20, H - 46),
           f"same 12 questions · LLM {LLM_SECONDS}s mean (16-25s sampled), "
           f"{LLM_TOKENS} tok mean",
           font=S.font(11), fill=S.DIM)
    d.text((20, H - 28),
           f"laya: committed results.json (p50 {RES['p50_ms']}ms, "
           f"p95 {RES['p95_ms']}ms)",
           font=S.font(11), fill=S.DIM)
    return im


frames = ([frame(1.0)] + [frame(p / 23) for p in range(24)]
          + [frame(1.0)] * 8)
out = os.path.join(HERE, "..", "assets", "before-after.gif")
os.makedirs(os.path.dirname(out), exist_ok=True)
frames[0].save(out, save_all=True, append_images=frames[1:], duration=70,
               loop=0, optimize=True)
print("wrote", out, f"({len(frames)} frames, laya {laya_ms}ms)")
