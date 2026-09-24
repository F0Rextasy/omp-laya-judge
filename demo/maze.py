"""Laya Maze (DOCUMENTED NEGATIVE RESULT): same feature-assisted recipe as
snake.py on a 10x10 maze, but the walk stalls in 2-cycles (score 1 crumb/200).

Measured mechanism: per-move confidence on game states sits at 0.005-0.035
(noise floor; quiz text states get 0.2-1.0). Isolated probes show even an
explicit "Eat food now. Best." criterion loses (0.005, wrong move picked).
Snake survives as a SYSTEM (open space + frequent food cues + death filter +
shield); maze corridors trap a noise walk before cues can accumulate.
Kept as an experiment with numbers. See demo/snake.py for the working setup.


Planner describes each direction (Blocked / Unsafe / Best route / Slower)
over a tiny state; laya returns move choice + risk in one batched call;
shield executes the best SAFE move. Records frames for web/maze.html
(offline replay, no backend) + assets/maze.gif.

Run: python demo/maze.py
"""
import json
import os
import random
import time
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 10, 10
DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


def make_maze(rng, wall_prob=0.22):
    while True:
        walls = set()
        for y in range(H):
            for x in range(W):
                if (x, y) in ((0, 0), (W - 1, H - 1)):
                    continue
                if rng.random() < wall_prob:
                    walls.add((x, y))
        if bfs((0, 0), (W - 1, H - 1), walls) is not None:
            return walls


def bfs(src, dst, walls):
    prev = {src: None}
    q = deque([src])
    while q:
        x, y = q.popleft()
        if (x, y) == dst:
            break
        for dx, dy in DIRS.values():
            n = (x + dx, y + dy)
            if n in prev or n in walls or not (0 <= n[0] < W and 0 <= n[1] < H):
                continue
            prev[n] = (x, y)
            q.append(n)
    if dst not in prev:
        return None
    dist = {}
    d = 0
    # distance from every reached cell to dst via reversed walk is overkill;
    # forward distances from src suffice for advance computation per move.
    q = deque([src])
    dist = {src: 0}
    while q:
        x, y = q.popleft()
        for dx, dy in DIRS.values():
            n = (x + dx, y + dy)
            if n in dist or n in walls or not (0 <= n[0] < W and 0 <= n[1] < H):
                continue
            dist[n] = dist[(x, y)] + 1
            q.append(n)
    return dist


def plan(pos, goal, walls, visits):
    out = {}
    d_before = bfs(pos, goal, walls) or {}
    for name, (dx, dy) in DIRS.items():
        n = (pos[0] + dx, pos[1] + dy)
        if n in walls or not (0 <= n[0] < W and 0 <= n[1] < H):
            out[name] = {"legal": False}
            continue
        d_after = bfs(n, goal, walls) or {}
        adv = d_before.get(goal, 999) - d_after.get(goal, 999)
        # trap = dead-end that is not the goal
        free = sum(1 for ddx, ddy in DIRS.values()
                   if (n[0] + ddx, n[1] + ddy) not in walls
                   and 0 <= n[0] + ddx < W and 0 <= n[1] + ddy < H)
        out[name] = {"legal": True, "safe": free > 1 or n == goal,
                     "eats": n == goal, "advance": adv,
                     "visits": visits.get(n, 0)}
    return out


def waypoints(walls):
    """Breadcrumb foods along the shortest path: the intermediate reward
    cues single-step choice needs (cf. snake.py food)."""
    dist = bfs((0, 0), (W - 1, H - 1), walls) or {}
    # reconstruct shortest path greedily
    path, cur = [(0, 0)], (0, 0)
    seen = {(0, 0)}
    while cur != (W - 1, H - 1):
        # step to the neighbor closest to the goal
        cands = [(dist.get((cur[0] + d[0], cur[1] + d[1]), 999), (cur[0] + d[0], cur[1] + d[1]))
                 for d in DIRS.values()]
        cands = [(dd, n) for dd, n in cands if n not in walls and 0 <= n[0] < W and 0 <= n[1] < H
                 and n not in seen]
        if not cands:
            break
        cands.sort()
        cur = cands[0][1]
        seen.add(cur)
        path.append(cur)
        if len(path) > W * H:
            break
    marks = path[1:]
    k = max(1, len(marks) // 4)
    return marks[::k] + ([(W - 1, H - 1)] if not marks or marks[-1] != (W - 1, H - 1) else [])


def play(router, max_steps=200, seed=7):
    rng = random.Random(seed)
    walls = make_maze(rng)
    pos, goal = (0, 0), (W - 1, H - 1)
    crumbs = [c for c in waypoints(walls) if tuple(c) != pos]
    target = crumbs.pop(0) if crumbs else goal
    visits = {pos: 1}
    frames, judgments, ms_total = [], 0, 0
    eaten, shields = 0, 0
    for step in range(max_steps):
        moves = plan(pos, target, walls, visits)
        safe = [m for m, v in moves.items() if v.get("safe")]
        reachable = (bfs(pos, goal, walls) or {}).get(goal) is not None
        preferred = max(safe, key=lambda m: (moves[m]["eats"], moves[m]["advance"],
                                             -moves[m]["visits"])) if safe else "NONE"

        def desc(m):
            v = moves[m]
            if not v["legal"]:
                return "Blocked. Wall."
            if not v["safe"]:
                return "Unsafe. Dead end."
            if v["eats"]:
                return "Safe. Reach the goal now. Best."
            loop = f" Visited {v['visits']} times, avoid loops." if v["visits"] else ""
            if m == preferred:
                return "Safe. Best route to the goal." + loop
            return "Safe. Slower route." + loop

        state = (f"Safe route: {'yes' if safe else 'no'}. "
                 f"Goal reachable through open cells: {'yes' if reachable else 'no'}.")
        q = {
            "move": {"type": "choice",
                     "instructions": "Choose the best safe move toward the goal.",
                     "criteria": {m: desc(m) for m in DIRS}},
            "risk": {"type": "noul", "instructions": "Is a safe route available?"},
        }
        t0 = time.time()
        ans = router.predict(state, q)["answers"]
        ms_total += (time.time() - t0) * 1000
        judgments += 1
        probs = ans["move"]["probabilities"]
        proposed = max(DIRS, key=probs.__getitem__)
        if proposed in safe:
            executed, shield = proposed, False
        elif safe:
            executed, shield = max(safe, key=probs.__getitem__), True
            shields += 1
        else:
            break
        dx, dy = DIRS[executed]
        pos = (pos[0] + dx, pos[1] + dy)
        visits[pos] = visits.get(pos, 0) + 1
        if tuple(pos) == tuple(target):
            eaten += 1
            if crumbs:
                target = crumbs.pop(0)
            else:
                break
        frames.append({"probs": probs, "proposed": proposed, "executed": executed,
                       "shield": shield, "pos": list(pos),
                       "risk": round(1 - ans["risk"]["noul"], 2),
                       "conf": round(ans["move"].get("confidence", 0.0), 2)})
    return {"frames": frames, "walls": sorted(walls), "score": eaten,
            "steps": len(frames), "shields": shields, "judgments": judgments,
            "ms_total": round(ms_total),
            "mean_ms": round(ms_total / max(judgments, 1)),
            "reached": tuple(pos) == goal, "seed": seed,
            "crumbs_total": eaten}


def to_gif(game, path):
    from PIL import Image, ImageDraw
    cell = 30
    Wpx, Hpx, panel = W * cell, H * cell, 250
    imgs = []
    for i, f in enumerate(game["frames"]):
        im = Image.new("RGB", (Wpx + panel, Hpx), (13, 17, 23))
        d = ImageDraw.Draw(im)
        for (x, y) in game["walls"]:
            d.rectangle([x * cell, y * cell, x * cell + cell - 1, y * cell + cell - 1],
                        fill=(48, 54, 61))
        gx, gy = W - 1, H - 1
        d.ellipse([gx * cell + 6, gy * cell + 6, gx * cell + cell - 6, gy * cell + cell - 6],
                  fill=(248, 81, 73))
        hx, hy = f["pos"]
        d.rectangle([hx * cell + 2, hy * cell + 2, hx * cell + cell - 3, hy * cell + cell - 3],
                    fill=(63, 185, 80))
        x0 = Wpx + 14
        d.text((x0, 12), f"move {i + 1}", fill=(230, 237, 243))
        y = 40
        for m in DIRS:
            p = f["probs"].get(m, 0.0)
            bar = int(p * 130)
            tag = " SHIELD" if (m == f["executed"] and f["shield"]) else (" <<" if m == f["executed"] else "")
            d.text((x0, y), f"{m:5s}", fill=(139, 148, 158))
            d.rectangle([x0 + 62, y + 3, x0 + 62 + bar, y + 11],
                        fill=(63, 185, 80) if m == f["executed"] else (48, 54, 61))
            d.text((x0 + 200, y), f"{p:.2f}{tag}", fill=(230, 237, 243))
            y += 24
        d.text((x0, y + 8), f"dead-end risk {f.get('risk', 0):.2f}", fill=(139, 148, 158))
        d.text((x0, y + 26), f"conf {f.get('conf', 0):.2f} - 0 LLM tokens", fill=(139, 148, 158))
        d.text((x0, y + 44), "laya + safety", fill=(88, 166, 255))
        imgs.append(im)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=150, loop=0)
    print("wrote", path, len(imgs), "frames")


def main():
    from laya import Router
    router = Router(device="cpu", preload=True)
    game = play(router)
    print(f"reached={game['reached']} steps={game['steps']} shields={game['shields']} mean={game['mean_ms']}ms")
    stats = {k: v for k, v in game.items() if k != "frames"}
    with open(os.path.join(HERE, "maze-stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=1)
    with open(os.path.join(HERE, "..", "web", "maze-frames.json"), "w", encoding="utf-8") as f:
        json.dump({"walls": game["walls"], "frames": game["frames"],
                   "goal": [W - 1, H - 1], "grid": [W, H]}, f)
    to_gif(game, os.path.join(HERE, "..", "assets", "maze.gif"))


if __name__ == "__main__":
    main()
