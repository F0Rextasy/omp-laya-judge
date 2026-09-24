"""Build web/tetris.html: offline canvas replay of demo/tetris.py frames.

Usage: python web/build_tetris_page.py   (reads web/tetris-frames.json)
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>laya tetris - live placement replay (offline)</title>
<style>
 :root { color-scheme: dark; }
 body { background: #0d1117; color: #e6edf3; font-family: ui-monospace, monospace; margin: 0; }
 header { padding: 12px 20px; border-bottom: 1px solid #30363d; }
 header h1 { font-size: 1.1em; margin: 0; }
 header p { color: #8b949e; font-size: 0.85em; margin: 4px 0 0; }
 main { display: flex; gap: 20px; padding: 16px 20px; flex-wrap: wrap; }
 .side { display: flex; flex-direction: column; gap: 12px; }
 canvas { background: #010409; border: 1px solid #30363d; border-radius: 12px; image-rendering: pixelated;
   box-shadow: 0 4px 24px rgba(0,0,0,.45); }
 .panel { min-width: 320px; flex: 1; max-width: 520px; }
 .hud { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 12px; }
 .stat { border: 1px solid #30363d; border-radius: 10px; padding: 8px 12px;
   background: linear-gradient(180deg, #0d1117, #010409); }
 .stat .v { font-size: 1.35em; font-variant-numeric: tabular-nums; }
 .stat .k { color: #8b949e; font-size: 0.72em; letter-spacing: .1em; }
 .opts { display: grid; gap: 8px; margin-bottom: 12px; }
 .opt { border: 1px solid #21262d; border-radius: 10px; padding: 8px 12px; background: #0d1117;
   display: grid; grid-template-columns: 30px 1fr 52px; gap: 10px; align-items: center; }
 .opt .id { font-weight: 700; color: #8b949e; }
 .opt .desc { font-size: .85em; color: #c9d1d9; }
 .opt .track { grid-column: 1 / -1; background: #161b22; border-radius: 99px; height: 10px; overflow: hidden; }
 .opt .fill { height: 100%; border-radius: 99px; background: #30363d; transition: width .18s ease-out; }
 .opt .p { text-align: right; font-variant-numeric: tabular-nums; }
 .opt.exec { border-color: rgba(63,185,80,.5); box-shadow: 0 0 16px rgba(63,185,80,.25); }
 .opt.exec .id { color: #3fb950; }
 .opt.exec .fill { background: linear-gradient(90deg, #238636, #3fb950); box-shadow: 0 0 10px rgba(63,185,80,.6); }
 .opt.sh { border-color: rgba(210,153,34,.55); box-shadow: 0 0 16px rgba(210,153,34,.3); }
 .opt.sh .id { color: #d29922; }
 .opt.sh .fill { background: linear-gradient(90deg, #9e6a03, #d29922); box-shadow: 0 0 10px rgba(210,153,34,.6); }
 .opt.want { border-style: dashed; }
 .controls { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
 button, select { background: #238636; color: #fff; border: 0; border-radius: 8px; padding: 6px 14px; font: inherit; cursor: pointer; }
 select { background: #21262d; border: 1px solid #30363d; }
 input[type=range] { flex: 1; min-width: 140px; }
 .meters { display: flex; gap: 8px; flex-wrap: wrap; }
 .meter { border: 1px solid #30363d; border-radius: 8px; padding: 6px 12px; font-size: .82em; color: #8b949e; }
 .meter b { color: #e6edf3; font-variant-numeric: tabular-nums; }
 .banner { margin-bottom: 12px; padding: 8px 12px; border-radius: 10px; font-size: .85em;
   background: rgba(210,153,34,.1); border: 1px solid rgba(210,153,34,.45); color: #e3b341; }
</style>
</head>
<body>
<header>
<h1>laya tetris &mdash; every placement picked by laya, replayed live</h1>
<p>Recorded from <code>python demo/tetris.py</code>: planner shortlists 6 landings per piece, laya picks in one batched call (~1s on CPU, 0 tokens), shield keeps the stack out of the danger zone.</p>
</header>
<main>
<div class="side">
<canvas id="board"></canvas>
</div>
<div class="panel">
<div class="controls">
<button id="play">&#10074;&#10074; pause</button>
<button id="prev">&larr;</button>
<button id="next">&rarr;</button>
<select id="speed"><option value="4">4 fps</option><option value="8" selected>8 fps</option><option value="20">20 fps</option><option value="40">40 fps</option></select>
<input id="scrub" type="range" min="0" max="1" value="0">
</div>
<div class="hud">
<div class="stat"><div class="v" id="s-move">-</div><div class="k">PIECE</div></div>
<div class="stat"><div class="v" id="s-score">-</div><div class="k">SCORE</div></div>
<div class="stat"><div class="v" id="s-lines">-</div><div class="k">LINES</div></div>
<div class="stat"><div class="v" id="s-mean">-</div><div class="k">MEAN MS</div></div>
<div class="stat"><div class="v" id="s-conf">-</div><div class="k">CONFIDENCE</div></div>
<div class="stat"><div class="v" id="s-sh">-</div><div class="k">SHIELDS</div></div>
</div>
<div id="banner"></div>
<div class="opts" id="opts"></div>
<div class="meters" id="meters"></div>
</div>
</main>
<script>
const DATA = __DATA__;
const FR = DATA.frames, GW = DATA.grid[0], GH = DATA.grid[1], COLORS = DATA.colors;
const cv = document.getElementById("board"), ctx = cv.getContext("2d");
const CELL = 26;
cv.width = GW * CELL; cv.height = GH * CELL;
let i = 0, playing = true, timer = null;
function block(x, y, c) {
  ctx.fillStyle = c;
  ctx.fillRect(x*CELL+1, y*CELL+1, CELL-2, CELL-2);
  ctx.fillStyle = "rgba(255,255,255,.22)";
  ctx.fillRect(x*CELL+1, y*CELL+1, CELL-2, 4);
}
function draw() {
  const f = FR[i];
  ctx.fillStyle = "#010409"; ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.strokeStyle = "#161b22";
  for (let x = 1; x < GW; x++) { ctx.beginPath(); ctx.moveTo(x*CELL, 0); ctx.lineTo(x*CELL, cv.height); ctx.stroke(); }
  for (let y = 1; y < GH; y++) { ctx.beginPath(); ctx.moveTo(0, y*CELL); ctx.lineTo(cv.width, y*CELL); ctx.stroke(); }
  f.board.forEach((row, y) => row.forEach((v, x) => { if (v) block(x, y, COLORS[v % COLORS.length]); }));
  document.getElementById("s-move").textContent = `${f.n}/${FR.length} ${f.piece}`;
  document.getElementById("s-score").textContent = f.score;
  document.getElementById("s-lines").textContent = f.total_lines;
  document.getElementById("s-mean").textContent = DATA.stats.mean_ms + "ms";
  document.getElementById("s-conf").textContent = f.conf.toFixed(2);
  document.getElementById("s-sh").textContent = FR.slice(0, i+1).filter(g => g.shield).length;
  document.getElementById("banner").innerHTML = f.shield
    ? `<div class="banner">&#9888; laya wanted <b>${f.proposed}</b> &mdash; danger zone, shield played <b>${f.executed}</b></div>` : "";
  document.getElementById("opts").innerHTML = f.options.map(o => {
    const cls = o.id === f.executed ? (f.shield ? "opt exec sh" : "opt exec") : (o.id === f.proposed ? "opt want" : "opt");
    return `<div class="${cls}"><span class="id">${o.id}</span><span class="desc">${o.desc}</span><span class="p">${o.p.toFixed(2)}</span><div class="track"><div class="fill" style="width:${Math.round(o.p*100)}%"></div></div></div>`;
  }).join("");
  document.getElementById("meters").innerHTML =
    `<span class="meter">cleared here <b>${f.lines}</b></span><span class="meter">risk <b>${f.risk.toFixed(2)}</b></span><span class="meter">0 LLM tokens</span><span class="meter">final <b>${DATA.stats.score}</b> &middot; pieces <b>${DATA.stats.pieces}</b> &middot; alive <b>${DATA.stats.alive}</b></span>`;
  document.getElementById("scrub").max = FR.length - 1;
  if (document.activeElement !== document.getElementById("scrub")) document.getElementById("scrub").value = i;
}
function loop() {
  clearInterval(timer);
  if (playing) timer = setInterval(() => { i = (i + 1) % FR.length; draw(); }, 1000 / +document.getElementById("speed").value);
}
document.getElementById("play").onclick = e => { playing = !playing; e.target.innerHTML = playing ? "&#10074;&#10074; pause" : "&#9654; play"; loop(); };
document.getElementById("next").onclick = () => { i = (i + 1) % FR.length; draw(); };
document.getElementById("prev").onclick = () => { i = (i - 1 + FR.length) % FR.length; draw(); };
document.getElementById("speed").onchange = loop;
document.getElementById("scrub").oninput = e => { i = +e.target.value; draw(); };
draw(); loop();
</script>
</body>
</html>
"""

with open(os.path.join(HERE, "tetris-frames.json"), encoding="utf-8") as f:
    data = json.load(f)
html = TEMPLATE.replace("__DATA__", json.dumps(data))
out = os.path.join(HERE, "tetris.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("wrote", out, len(html), "bytes,", len(data["frames"]), "frames")
