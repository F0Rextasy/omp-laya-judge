"""Shared visual language for the demo GIFs.

GitHub-dark theme, truetype fonts big enough to read in a README, colored
probability bars, and pill-shaped badges. quiz/snake/tetris renderers all
import this so the media reads as one family instead of three hand-rolled
draw loops.
"""
import os

from PIL import Image, ImageDraw, ImageFont

# GitHub dark palette
BG = (13, 17, 23)
PANEL = (22, 27, 34)
TRACK = (48, 54, 61)
BORDER = (48, 54, 61)
FG = (230, 237, 243)
DIM = (139, 148, 158)
GREEN = (63, 185, 80)
RED = (248, 81, 73)
BLUE = (88, 166, 255)
YELLOW = (248, 189, 16)
PURPLE = (188, 140, 255)
CYAN = (57, 185, 204)

_FONT_CACHE = {}


def font(size, bold=False):
    """Windows Fonts truetype lookup, cached; fallback to sized default."""
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    windir = os.environ.get("WINDIR", r"C:\Windows")
    names = (("arialbd.ttf", "segoeuib.ttf", "consolab.ttf", "verdanab.ttf")
             if bold else ("arial.ttf", "segoeui.ttf", "consola.ttf", "verdana.ttf"))
    f = None
    for name in names:
        try:
            f = ImageFont.truetype(os.path.join(windir, "Fonts", name), size)
            break
        except OSError:
            continue
    if f is None:
        try:
            f = ImageFont.load_default(size)
        except TypeError:  # Pillow < 9.2 has no size argument
            f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def strip_md(text):
    """Drop markdown emphasis — GIF frames must not show raw *asterisks*."""
    for ch in "*_`":
        text = text.replace(ch, "")
    return text


def wrap(d, text, fnt, max_w):
    """Greedy word wrap measured in pixels."""
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if d.textlength(trial, font=fnt) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def badge(d, x, y, text, color, size=14, pad=9):
    """Pill chip: colored outline + matching text. Returns the right edge."""
    fnt = font(size, bold=True)
    tw = d.textlength(text, font=fnt)
    h = size + 10
    box = [x, y, x + tw + 2 * pad, y + h]
    d.rounded_rectangle(box, radius=h // 2, outline=color, width=2)
    d.text((x + pad, y + 4), text, font=fnt, fill=color)
    return box[2]


def badge_row(d, x, y, items, size=14, gap=8):
    """Draw [(text, color), ...] chips left to right. Returns right edge."""
    for text, color in items:
        x = badge(d, x, y, text, color, size=size) + gap
    return x - gap if items else x


def prob_bar(d, x, y, w, p, color, label=None, value=None,
             label_w=0, size=13, h=14):
    """Track + colored fill + label/value text — the 'renkli grafik'."""
    p = max(0.0, min(1.0, float(p)))
    if label is not None:
        d.text((x, y - 1), label, font=font(size, bold=True), fill=FG)
        x += label_w
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=TRACK)
    fill_w = max(h, round(w * p))  # keep the pill shape at low values
    d.rounded_rectangle([x, y, x + fill_w, y + h], radius=h // 2, fill=color)
    if value is not None:
        d.text((x + w + 8, y - 1), value, font=font(size), fill=FG)
    return y + h


def panel(d, box, title=None, title_color=None, size=16):
    """Rounded card with optional bold title; returns content top y."""
    d.rounded_rectangle(box, radius=8, fill=PANEL, outline=BORDER, width=1)
    if title:
        d.text((box[0] + 12, box[1] + 9), title,
               font=font(size, bold=True), fill=title_color or FG)
        return box[1] + 9 + size + 10
    return box[1] + 10


def save_gif(imgs, path, duration):
    """Palette-quantized GIF (keeps 300-frame game captures README-sized)."""
    frames = [im.convert("P", palette=Image.Palette.ADAPTIVE, colors=128)
              if im.mode != "P" else im for im in imgs]
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=duration, loop=0, optimize=True)
    print("wrote", path, len(frames), "frames")
