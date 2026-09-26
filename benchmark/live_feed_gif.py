"""Regenerate assets/live-feed.gif from the running sidecar.

Every card in this animation is a real answer: benchmark/live_feed_cards.ts
asks the sidecar the same four questions the harness gates ask and renders the
answers with the shipped row builder, then hands the text over. This script
only puts pixels around those lines, so the GIF cannot drift from what a
session actually shows.

    python benchmark/live_feed_gif.py
"""
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "demo"))
import style as S  # noqa: E402

W, H = 780, 500
PAD = 22
LINE_H = 20
TOP = 92
# The card text uses these; none of the UI fonts on Windows carry them, so they
# are drawn from Segoe UI Symbol, which does. Verified by comparing each
# glyph's mask against U+E000 (.notdef) - in arial, verdana and consola all
# four are .notdef, in seguisym none are.
SYMBOLS = ("⚡", "◀", "█", "░", "▸", "·")


def collect() -> dict:
    """Real decisions, real rows."""
    result = subprocess.run(
        ["bun", "run", os.path.join(HERE, "live_feed_cards.ts")],
        cwd=HERE, capture_output=True, timeout=300)
    if result.returncode != 0:
        raise SystemExit(f"sidecar capture failed: {result.stderr.decode(chr(117)+chr(116)+chr(102)+chr(45)+chr(56), chr(105)+chr(103)+chr(110)+chr(111)+chr(114)+chr(101))[:300]}")
    return json.loads(result.stdout.decode("utf-8", "ignore"))


def symbol_font(size: int):
    try:
        path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "seguisym.ttf")
        return ImageFont.truetype(path, size)
    except OSError:
        return None


COLOURS = {"█": S.GREEN, "◀": S.GREEN, "░": S.TRACK}


def draw_line(draw, y, line, font, symbols, default):
    """One card line on a monospace grid, swapping in the symbol font.

    The card text uses ⚡ ▸ ◀ █ ░, none of which arial/verdana/consola carry -
    they draw as .notdef boxes - so those cells come from Segoe UI Symbol.
    """
    cell = font.getlength("M")
    for index, character in enumerate(line):
        if character in SYMBOLS and symbols is not None:
            draw.text((PAD + index * cell, y), character, font=symbols,
                      fill=COLOURS.get(character, default))
            continue
        draw.text((PAD + index * cell, y), character, font=font,
                  fill=COLOURS.get(character, default))


def frame(revealed: int, data: dict) -> Image.Image:
    image = Image.new("RGB", (W, H), S.BG)
    draw = ImageDraw.Draw(image)
    draw.text((PAD, 18), "laya decides in the loop", font=S.font(20, bold=True), fill=S.FG)
    S.badge_row(draw, PAD, 48, [
        (f"v{data['version']}", S.BLUE),
        ("0 LLM tokens", S.GREEN),
        (f"{data['total']}ms local", S.PURPLE),
        ("CPU", S.CYAN),
    ], size=12)
    draw.line([(PAD, TOP - 12), (W - PAD, TOP - 12)], fill=S.BORDER)

    font = ImageFont.truetype(
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "consola.ttf"), 13)
    symbols = symbol_font(15)
    y = TOP
    for card in data["cards"][:revealed]:
        draw_line(draw, y, f"⚡ laya ▸ {card['label']}", font, symbols, S.DIM)
        y += LINE_H
        for line in card["rows"]:
            draw_line(draw, y, line, font, symbols, S.FG)
            y += LINE_H
        y += 8

    draw.line([(PAD, H - 32), (W - PAD, H - 32)], fill=S.BORDER)
    draw.text((PAD, H - 24),
              "real answers from the local sidecar · rows drawn by hooks/lib/record.ts",
              font=S.font(11), fill=S.DIM)
    return image


def main() -> None:
    data = collect()
    cards = len(data["cards"])
    frames = [frame(0, data)]
    for shown in range(1, cards + 1):
        frames.extend(frame(shown, data) for _ in range(9))
    frames.extend(frame(cards, data) for _ in range(14))
    out = os.path.join(HERE, "..", "assets", "live-feed.gif")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=90, loop=0, optimize=True)
    print("wrote", out, f"({len(frames)} frames, {data['total']}ms of local decisions)")


if __name__ == "__main__":
    main()
