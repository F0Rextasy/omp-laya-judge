"""Render benchmark/results.json as assets/benchmark.svg (badge panel + latency bars)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
res = json.load(open(os.path.join(HERE, "results.json")))
cal = json.load(open(os.path.join(HERE, "calibration.json")))
rows = res["rows"]
W, LH, TOP, LEFT, MAXW = 640, 30, 74, 190, 380
H = TOP + len(rows) * LH + 50
max_ms = max(r["ms"] for r in rows)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;")


def chip(x, y, txt, color):
    """Pill outline + bold text; monospace is ~7.2px/char at size 12."""
    w = int(len(txt) * 7.2) + 20
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="22" rx="11" '
                 f'fill="none" stroke="{color}" stroke-width="1.5"/>')
    parts.append(f'<text x="{x + 10}" y="{y + 16}" fill="{color}" font-size="12" '
                 f'font-weight="bold">{esc(txt)}</text>')
    return x + w + 10


parts = [
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="monospace">',
    '<rect width="100%" height="100%" fill="#0d1117"/>',
    '<text x="20" y="28" fill="#e6edf3" font-size="17" font-weight="bold">'
    'laya-judge · 12-case bench · CPU</text>',
]
x = 20
for txt, color in ((f"{res['correct']}/{res['cases']} correct", "#3fb950"),
                   (f"mean {res['mean_ms']}ms", "#58a6ff"),
                   (f"p50 {res['p50_ms']}ms", "#bc8cff"),
                   (f"p95 {res['p95_ms']}ms", "#f8bd10"),
                   ("0 tokens", "#3fb950")):
    x = chip(x, 42, txt, color)

for i, r in enumerate(rows):
    y = TOP + i * LH
    w = max(3, r["ms"] / max_ms * MAXW)
    color = "#3fb950" if r["ok"] else "#f85149"
    label = f'{r["qid"]} exp={r["expected"]}'
    parts.append(f'<rect x="{LEFT}" y="{y}" width="{MAXW}" height="16" rx="4" fill="#21262d"/>')
    parts.append(f'<text x="20" y="{y + 14}" fill="#8b949e" font-size="12">{esc(label)}</text>')
    parts.append(f'<rect x="{LEFT}" y="{y}" width="{w:.0f}" height="16" rx="4" fill="{color}"/>')
    parts.append(f'<text x="{LEFT + w + 8:.0f}" y="{y + 14}" fill="#e6edf3" font-size="12">{r["ms"]}ms</text>')

parts.append(f'<text x="20" y="{H - 26}" fill="#8b949e" font-size="11">green=correct red=miss · '
             f'gate {cal["gate"]}: {cal["auto_accept"]}/{cal["cases"]} auto-accept, '
             f'{cal["misses_caught_by_gate"]}/{cal["misses_total"]} misses caught</text>')
parts.append(f'<text x="20" y="{H - 10}" fill="#8b949e" font-size="11">'
             f'{cal["misses_escaped"]} escaped ({cal["false_accept_rate"]:.0%} false-accept) · '
             f'CPU torch · source benchmark/results.json</text>')
parts.append("</svg>")
out = os.path.join(HERE, "..", "assets", "benchmark.svg")
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, "w", encoding="utf-8").write("\n".join(parts))
print("wrote", out)
