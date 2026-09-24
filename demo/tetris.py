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
    import style as S

    cell, Wpx, Hpx = 18, W * 18, H * 18
    panel = 300
    hues = [S.BLUE, S.PURPLE, S.YELLOW, S.CYAN, S.RED, S.DIM]
    imgs = []
    for i, f in enumerate(game["frames"]):
        im = Image.new("RGB", (Wpx + panel, Hpx), S.BG)
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([Wpx + 4, 4, Wpx + panel - 4, Hpx - 4], radius=10,
                            fill=S.PANEL, outline=S.BORDER, width=1)
        pal = [(0, 0, 0), S.BLUE, S.YELLOW, S.PURPLE, S.GREEN, S.RED, S.CYAN,
               (210, 153, 34)]
        for y, row in enumerate(f["board"]):
            for x, v in enumerate(row):
                if v:
                    d.rectangle([x * cell + 1, y * cell + 1, x * cell + cell - 1, y * cell + cell - 1],
                                fill=pal[v % len(pal)])
        x0 = Wpx + 16
        d.text((x0, 12), f"piece {f['n']} · {f['piece']}",
               font=S.font(19, bold=True), fill=S.FG)
        S.badge_row(d, x0, 42, [(f"score {f['score']}", S.GREEN),
                                (f"lines {f['total_lines']}", S.BLUE)],
                    size=12, gap=6)
        y = 82
        for k, o in enumerate(f["options"]):
            executed = o["id"] == f["executed"]
            color = S.GREEN if executed else hues[k % len(hues)]
            tag = " SHIELD" if executed and f["shield"] else ""
            S.prob_bar(d, x0, y, 150, o["p"], color, label=o["id"],
                       value=f"{o['p']:.2f}{tag}", label_w=26, size=13, h=14)
            y += 24
        y += 8
        d.line([x0, y, x0 + panel - 32, y], fill=S.BORDER, width=1)
        y += 12
        S.prob_bar(d, x0, y, 150, f["conf"], S.PURPLE, label="conf",
                   value=f"{f['conf']:.2f}", label_w=52, size=12, h=10)
        y += 20
        S.prob_bar(d, x0, y, 150, f["risk"], S.RED, label="risk",
                   value=f"{f['risk']:.2f}", label_w=52, size=12, h=10)
        S.badge_row(d, x0, Hpx - 34, [(f"shields {game['shields']}", S.YELLOW),
                                      ("0 tokens", S.GREEN)], size=12, gap=6)
        imgs.append(im)
    S.save_gif(imgs, path, duration=150)


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
