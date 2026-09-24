"""Render benchmark/results.json as assets/benchmark.svg (latency bars + accuracy)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
res = json.load(open(os.path.join(HERE, "results.json")))
cal = json.load(open(os.path.join(HERE, "calibration.json")))
rows = res["rows"]
W, LH, TOP, LEFT, MAXW = 640, 30, 70, 190, 400
H = TOP + len(rows) * LH + 50
max_ms = max(r["ms"] for r in rows)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;")


parts = [
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="monospace">',
    '<rect width="100%" height="100%" fill="#0d1117"/>',
    f'<text x="20" y="28" fill="#e6edf3" font-size="16">laya-judge: {res["correct"]}/{res["cases"]} correct, '
    f'mean {res["mean_ms"]}ms (p50 {res["p50_ms"]}, p95 {res["p95_ms"]}), 0 LLM tokens (CPU)</text>',
]
for i, r in enumerate(rows):
    y = TOP + i * LH
    w = max(3, r["ms"] / max_ms * MAXW)
    color = "#3fb950" if r["ok"] else "#f85149"
    label = f'{r["qid"]} exp={r["expected"]}'
    parts.append(f'<text x="20" y="{y + 14}" fill="#8b949e" font-size="12">{esc(label)}</text>')
    parts.append(f'<rect x="{LEFT}" y="{y}" width="{w:.0f}" height="16" fill="{color}"/>')
    parts.append(f'<text x="{LEFT + w + 8:.0f}" y="{y + 14}" fill="#e6edf3" font-size="12">{r["ms"]}ms</text>')
parts.append(f'<text x="20" y="{H - 12}" fill="#8b949e" font-size="11">green=correct red=miss · '
             f'gate {cal["gate"]}: {cal["auto_accept"]}/{cal["cases"]} auto-accept, '
             f'{cal["misses_caught_by_gate"]}/{cal["misses_total"]} misses caught, '
             f'{cal["misses_escaped"]} escaped ({cal["false_accept_rate"]:.0%} false-accept) · CPU torch</text>')
parts.append("</svg>")
out = os.path.join(HERE, "..", "assets", "benchmark.svg")
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, "w", encoding="utf-8").write("\n".join(parts))
print("wrote", out)
