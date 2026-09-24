"""Build web/snake.html: offline canvas replay of demo/snake.py frames.

Usage: python web/build_snake_page.py   (reads web/snake-frames.json)
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>laya snake - live decision replay (offline)</title>
<style>
 :root { color-scheme: dark; }
 body { background: #0d1117; color: #e6edf3; font-family: ui-monospace, monospace; margin: 0; }
 header { padding: 12px 20px; border-bottom: 1px solid #30363d; }
 header h1 { font-size: 1.1em; margin: 0; }
 header p { color: #8b949e; font-size: 0.85em; margin: 4px 0 0; }
 main { display: flex; gap: 20px; padding: 16px 20px; flex-wrap: wrap; }
 canvas { background: #010409; border: 1px solid #30363d; border-radius: 8px; image-rendering: pixelated; }
 .panel { min-width: 300px; flex: 1; max-width: 460px; }
 .hud { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 12px; }
 .stat { border: 1px solid #30363d; border-radius: 8px; padding: 8px 10px; }
 .stat .v { font-size: 1.3em; } .stat .k { color: #8b949e; font-size: 0.75em; }
 .moves { border: 1px solid #30363d; border-radius: 12px; padding: 14px 16px; margin-bottom: 12px;
   background: linear-gradient(180deg, #0d1117, #010409); box-shadow: 0 4px 24px rgba(0,0,0,.45); }
 .moves h3 { margin: 0 0 10px; font-size: .72em; letter-spacing: .14em; color: #8b949e; font-weight: 600; }
 .mrow { display: grid; grid-template-columns: 52px 1fr 64px; align-items: center; gap: 10px; margin: 8px 0; }
 .mrow .name { color: #8b949e; font-size: .8em; letter-spacing: .08em; }
 .mrow.exec .name { color: #e6edf3; font-weight: 700; }
 .mrow .track { background: #161b22; border-radius: 99px; height: 14px; overflow: hidden;
   border: 1px solid #21262d; }
 .mrow .fill { height: 100%; border-radius: 99px; background: #30363d;
   transition: width .18s ease-out; }
 .mrow.exec .fill { background: linear-gradient(90deg, #238636, #3fb950);
   box-shadow: 0 0 12px rgba(63,185,80,.55); }
 .mrow.prop .fill { background: linear-gradient(90deg, #9e6a03, #d29922);
   box-shadow: 0 0 12px rgba(210,153,34,.55); }
 .mrow .pv { text-align: right; font-variant-numeric: tabular-nums; }
 .mrow .tag { display: inline-block; font-size: .68em; margin-left: 6px; padding: 1px 7px; border-radius: 99px;
   letter-spacing: .06em; }
 .mrow.exec .tag { background: rgba(63,185,80,.15); color: #3fb950; border: 1px solid rgba(63,185,80,.4); }
 .mrow.prop .tag { background: rgba(210,153,34,.15); color: #d29922; border: 1px solid rgba(210,153,34,.4); }
 .meters { display: flex; gap: 8px; flex-wrap: wrap; }
 .meter { border: 1px solid #30363d; border-radius: 8px; padding: 6px 12px; font-size: .82em; color: #8b949e; }
 .meter b { color: #e6edf3; font-variant-numeric: tabular-nums; }
 .shield { margin-top: 10px; padding: 8px 12px; border-radius: 8px; font-size: .82em;
   background: rgba(210,153,34,.1); border: 1px solid rgba(210,153,34,.45); color: #e3b341; }
 .controls { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
 button, select { background: #238636; color: #fff; border: 0; border-radius: 6px; padding: 6px 14px; font: inherit; cursor: pointer; }
 select { background: #21262d; border: 1px solid #30363d; }
 input[type=range] { flex: 1; min-width: 140px; }
 .shield { color: #d29922; font-weight: bold; }
</style>
</head>
<body>
<header>
<h1>laya snake &mdash; every move decided by laya, replayed live</h1>
<p>Recorded from <code>python demo/snake.py</code>: planner describes each direction, laya picks in one batched call (~1s on CPU, 0 tokens), shield executes the best SAFE move.</p>
</header>
<main>
<canvas id="board"></canvas>
<div class="panel">
<div class="controls">
<button id="play">&#10074;&#10074; pause</button>
<button id="prev">&larr;</button>
<button id="next">&rarr;</button>
<select id="speed"><option value="6">6 fps</option><option value="12" selected>12 fps</option><option value="30">30 fps</option><option value="60">60 fps</option></select>
<input id="scrub" type="range" min="0" max="1" value="0">
</div>
<div class="hud">
<div class="stat"><div class="v" id="s-move">-</div><div class="k">move</div></div>
<div class="stat"><div class="v" id="s-score">-</div><div class="k">score</div></div>
<div class="stat"><div class="v" id="s-ms">-</div><div class="k">executed</div></div>
<div class="stat"><div class="v" id="s-mean">-</div><div class="k">mean ms</div></div>
<div class="stat"><div class="v" id="s-conf">-</div><div class="k">confidence</div></div>
<div class="stat"><div class="v" id="s-sh">-</div><div class="k">shields</div></div>
</div>
<div class="moves" id="moves"></div>
<div class="meters" id="meters"></div>
</div>
</main>
<script>
const DATA = __DATA__;
const FR = DATA.frames, GW = DATA.grid[0], GH = DATA.grid[1];
const ORDER = ["UP", "DOWN", "LEFT", "RIGHT"];
const cv = document.getElementById("board"), ctx = cv.getContext("2d");
const CELL = 34;
cv.width = GW * CELL; cv.height = GH * CELL;
let i = 0, playing = true, timer = null, shields = 0, score = 0;
const seenFood = new Set();
function draw() {
  const f = FR[i];
  ctx.fillStyle = "#010409"; ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.strokeStyle = "#161b22";
  for (let x = 1; x < GW; x++) { ctx.beginPath(); ctx.moveTo(x*CELL, 0); ctx.lineTo(x*CELL, cv.height); ctx.stroke(); }
  for (let y = 1; y < GH; y++) { ctx.beginPath(); ctx.moveTo(0, y*CELL); ctx.lineTo(cv.width, y*CELL); ctx.stroke(); }
  f.snake.forEach(([x, y], k) => {
    ctx.fillStyle = k === 0 ? "#3fb950" : "#58a6ff";
    ctx.fillRect(x*CELL+2, y*CELL+2, CELL-4, CELL-4);
  });
  if (f.food) {
    ctx.fillStyle = "#f85149";
    ctx.beginPath(); ctx.arc(f.food[0]*CELL+CELL/2, f.food[1]*CELL+CELL/2, CELL/3, 0, 7); ctx.fill();
  }
  // HUD
  document.getElementById("s-move").textContent = `${i+1}/${FR.length}`;
  const ate = FR.slice(0, i+1).filter((g, k) => k > 0 && JSON.stringify(g.snake) !== JSON.stringify(FR[k-1].snake) && g.snake.length > FR[k-1].snake.length).length;
  document.getElementById("s-score").textContent = ate;
  document.getElementById("s-ms").textContent = f.executed + (f.shield ? "*" : "");
  document.getElementById("s-mean").textContent = DATA.stats.mean_ms + "ms";
  document.getElementById("s-conf").textContent = f.conf.toFixed(2);
  const sh = FR.slice(0, i+1).filter(g => g.shield).length;
  document.getElementById("s-sh").textContent = sh;
  // move bars
  const mv = document.getElementById("moves");
  const pct = p => Math.round(p * 100);
  mv.innerHTML = `<h3>LAYA DECISION &middot; 0 TOKENS</h3>` + ORDER.map(m => {
    const p = f.probs[m] || 0;
    const cls = m === f.executed ? (f.shield ? "mrow exec prop" : "mrow exec") : (m === f.proposed ? "mrow prop" : "mrow");
    const tag = m === f.executed ? (f.shield ? `<span class="tag">SHIELD</span>` : `<span class="tag">PLAY</span>`) : (m === f.proposed && f.shield ? `<span class="tag">WANTED</span>` : "");
    return `<div class="${cls}"><span class="name">${m}</span><div class="track"><div class="fill" style="width:${pct(p)}%"></div></div><span class="pv">${p.toFixed(2)}${tag}</span></div>`;
  }).join("") + (f.shield ? `<div class="shield">&#9888; laya wanted <b>${f.proposed}</b> &mdash; unsafe, shield played <b>${f.executed}</b></div>` : "");
  document.getElementById("meters").innerHTML =
    `<span class="meter">dead-end risk <b>${f.risk.toFixed(2)}</b></span><span class="meter">food reachable <b>${f.reach.toFixed(2)}</b></span><span class="meter">final <b>${DATA.stats.score}</b> &middot; shields <b>${DATA.stats.shields}</b> &middot; alive <b>${DATA.stats.alive}</b></span>`;
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

with open(os.path.join(HERE, "snake-frames.json"), encoding="utf-8") as f:
    data = json.load(f)
html = TEMPLATE.replace("__DATA__", json.dumps(data))
out = os.path.join(HERE, "snake.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("wrote", out, len(html), "bytes,", len(data["frames"]), "frames")
