"""Laya Tetris: planner-shortlist + laya placement pick + safety shield.

Per incoming piece the planner enumerates every legal (rotation, column)
landing, scores them with classic Tetris features (lines, holes, height,
bumpiness), and shortlists the top 6. Laya picks one in a single batched
call (~1s on CPU, 0 tokens); the shield executes the highest-probability
placement that keeps the stack out of the danger zone.

Honest note (cf. demo/snake.py, demo/maze.py): game-state confidences run
at noise level, so navigation is planner + shield with laya ranking.
Recorded frames replay in web/tetris.html.
"""
import json
import os
import random
import time

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 10, 20
MAX_PIECES = 120
SHORTLIST = 6

BASE = {
    "I": [(0, 1), (1, 1), (2, 1), (3, 1)],
    "O": [(0, 0), (1, 0), (0, 1), (1, 1)],
    "T": [(1, 0), (0, 1), (1, 1), (2, 1)],
    "S": [(1, 0), (2, 0), (0, 1), (1, 1)],
    "Z": [(0, 0), (1, 0), (1, 1), (2, 1)],
    "J": [(0, 0), (0, 1), (1, 1), (2, 1)],
    "L": [(2, 0), (0, 1), (1, 1), (2, 1)],
}
COLORS = {"I": 1, "O": 2, "T": 3, "S": 4, "Z": 5, "J": 6, "L": 7}


def rotations(cells):
    out, cur = [], cells
    for _ in range(4):
        xs = [x for x, _ in cur]
        ys = [y for _, y in cur]
        norm = sorted((x - min(xs), y - min(ys)) for x, y in cur)
        if norm not in out:
            out.append(norm)
        cur = [(-y, x) for x, y in cur]
    return out


def collides(board, cells, ox, oy):
    for x, y in cells:
        bx, by = ox + x, oy + y
        if bx < 0 or bx >= W or by >= H:
            return True
        if by >= 0 and board[by][bx]:
            return True
    return False


def features(board):
    heights = []
    for x in range(W):
        h = 0
        for y in range(H):
            if board[y][x]:
                h = H - y
                break
        heights.append(h)
    holes = 0
    for x in range(W):
        seen = False
        for y in range(H):
            if board[y][x]:
                seen = True
            elif seen:
                holes += 1
    bump = sum(abs(heights[i] - heights[i + 1]) for i in range(W - 1))
    return heights, holes, bump


def describe(feat, lines):
    agg, holes, bump, top = feat
    if lines >= 2:
        return f"Clears {lines} lines. Best."
    if lines == 1:
        return f"Clears 1 line. Good."
    if holes >= 2:
        return f"Risky. Digs {holes} holes."
    if top >= 12:
        return f"Risky. Stacks to row {H - top}."
    if bump <= 4 and agg <= 30:
        return "Safe. Flat low stack."
    if agg <= 40:
        return "Safe. Keeps it low."
    return "Safe. High but playable."


def placements(board, kind):
    cands = []
    for ri, cells in enumerate(rotations(BASE[kind])):
        maxx = max(x for x, _ in cells)
        for ox in range(-2, W):
            if any(not (0 <= ox + x < W) for x, _ in cells):
                continue
            oy = -4
            while not collides(board, cells, ox, oy + 1):
                oy += 1
            if collides(board, cells, ox, oy):
                continue
            locked = [row[:] for row in board]
            for x, y in cells:
                if oy + y >= 0:
                    locked[oy + y][ox + x] = COLORS[kind]
            full = [r for r in locked if all(r)]
            lines = len(full)
            rest = [r for r in locked if not all(r)]
            final = [[0] * W for _ in range(lines)] + rest
            final = ([[0] * W for _ in range(H - len(final))]) + final
            heights, holes, bump = features(final)
            agg = sum(heights)
            cands.append({
                "key": (ri, ox), "cells": [(ox + x, oy + y) for x, y in cells],
                "board": final, "lines": lines, "agg": agg, "holes": holes,
                "bump": bump, "top": max(heights) if heights else 0,
            })
    cands.sort(key=lambda c: (-c["lines"], c["holes"], c["agg"], c["bump"]))
    return cands


def play(router, max_pieces=MAX_PIECES, seed=7):
    rng = random.Random(seed)
    board = [[0] * W for _ in range(H)]
    bag = []
    frames, judgments, ms_total = [], 0, 0
    score, total_lines, shields = 0, 0, 0
    for n in range(max_pieces):
        if not bag:
            bag = list(BASE)
            rng.shuffle(bag)
        kind = bag.pop()
        cands = placements(board, kind)
        if not cands:
            break
        short = cands[:SHORTLIST]
        ids = [chr(ord("A") + k) for k in range(len(short))]
        heights, holes, _ = features(board)
        state = (f"Incoming {kind}. Stack height {max(heights) if heights else 0}, "
                 f"holes {holes}, lines so far {total_lines}.")
        q = {
            "place": {"type": "choice", "instructions": "Choose the best placement.",
                      "criteria": {i: describe((c["agg"], c["holes"], c["bump"], c["top"]), c["lines"])
                                   for i, c in zip(ids, short)}},
            "risk": {"type": "noul", "instructions": "Will the stack enter the top 4 rows?"},
        }
        t0 = time.time()
        ans = router.predict(state, q)["answers"]
        ms_total += (time.time() - t0) * 1000
        judgments += 1
        probs = ans["place"]["probabilities"]
        order = sorted(ids, key=probs.__getitem__, reverse=True)
        proposed = order[0]
        # shield: stay out of the top-4 danger zone while any option does
        calm = [i for i in ids if short[ids.index(i)]["top"] <= H - 4] or ids
        executed = next((i for i in order if i in calm), order[0])
        shield = executed != proposed
        shields += shield
        pick = short[ids.index(executed)]
        board = pick["board"]
        score += [0, 100, 300, 500, 800][min(pick["lines"], 4)] + 1
        total_lines += pick["lines"]
        frames.append({
            "board": board, "piece": kind, "next": bag[-1] if bag else None,
            "options": [{"id": i, "desc": q["place"]["criteria"][i], "p": round(probs.get(i, 0.0), 4)}
                        for i in ids],
            "proposed": proposed, "executed": executed, "shield": shield,
            "lines": pick["lines"], "score": score, "total_lines": total_lines,
            "conf": round(ans["place"].get("confidence", 0.0), 3),
            "risk": round(1 - ans["risk"]["noul"], 2),
            "n": n + 1,
        })
    return {"frames": frames, "score": score, "lines": total_lines, "pieces": len(frames),
            "shields": shields, "judgments": judgments,
            "ms_total": round(ms_total), "mean_ms": round(ms_total / max(judgments, 1)),
            "alive": len(frames) >= max_pieces, "seed": seed}


def to_gif(game, path):
    from PIL import Image, ImageDraw
    cell, Wpx, Hpx = 18, W * 18, H * 18
    panel = 300
    imgs = []
    for i, f in enumerate(game["frames"]):
        im = Image.new("RGB", (Wpx + panel, Hpx), (13, 17, 23))
        d = ImageDraw.Draw(im)
        pal = [(0, 0, 0), (88, 166, 255), (248, 189, 16), (188, 140, 255), (63, 185, 80),
               (248, 81, 73), (57, 185, 204), (210, 153, 34)]
        for y, row in enumerate(f["board"]):
            for x, v in enumerate(row):
                if v:
                    d.rectangle([x * cell + 1, y * cell + 1, x * cell + cell - 1, y * cell + cell - 1],
                                fill=pal[v % len(pal)])
        x0 = Wpx + 12
        d.text((x0, 10), f"piece {f['n']} ({f['piece']})  score {f['score']}", fill=(230, 237, 243))
        d.text((x0, 28), f"lines {f['total_lines']}  shields {game['shields']}", fill=(139, 148, 158))
        y = 56
        for o in f["options"]:
            bar = int(o["p"] * 120)
            tag = " SHIELD" if (o["id"] == f["executed"] and f["shield"]) else (" <<" if o["id"] == f["executed"] else "")
            d.text((x0, y), o["id"], fill=(139, 148, 158))
            d.rectangle([x0 + 24, y + 3, x0 + 24 + bar, y + 11],
                        fill=(63, 185, 80) if o["id"] == f["executed"] else (48, 54, 61))
            d.text((x0 + 152, y), f"{o['p']:.2f}{tag}", fill=(230, 237, 243))
            y += 22
        d.text((x0, y + 6), f"conf {f['conf']:.2f} - 0 LLM tokens", fill=(139, 148, 158))
        imgs.append(im)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=150, loop=0)
    print("wrote", path, len(imgs), "frames")


def main():
    from laya import Router
    router = Router(device="cpu", preload=True)
    game = play(router)
    print(f"score={game['score']} lines={game['lines']} pieces={game['pieces']} "
          f"shields={game['shields']} alive={game['alive']} mean={game['mean_ms']}ms")
    stats = {k: v for k, v in game.items() if k != "frames"}
    with open(os.path.join(HERE, "tetris-stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=1)
    with open(os.path.join(HERE, "..", "web", "tetris-frames.json"), "w", encoding="utf-8") as f:
        json.dump({"frames": game["frames"], "grid": [W, H],
                   "colors": ["#000", "#58a6ff", "#f8bd10", "#bc8cff", "#3fb950", "#f85149", "#39b9cc", "#d29922"],
                   "stats": stats}, f)
    print("wrote web/tetris-frames.json", len(game["frames"]), "frames")
    to_gif(game, os.path.join(HERE, "..", "assets", "tetris.gif"))


if __name__ == "__main__":
    main()
