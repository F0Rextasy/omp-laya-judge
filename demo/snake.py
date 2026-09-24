"""Laya Snake, feature-assisted (mizorewww/laya-mlx recipe, torch port).

Architecture: a deterministic planner describes each direction
(Blocked / Unsafe / Eat now / Best route / Slower route) over a tiny state.
Laya returns a distribution over UP/DOWN/LEFT/RIGHT plus two risk estimates
in ONE batched predict() call. A safety shield executes the highest-probability
SAFE move and counts interventions (SHIELD).

Run: python demo/snake.py  (writes assets/snake.gif + demo/snake-stats.json)
"""
import json
import os
import time
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 12, 12
DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


def bfs_dist(src, blocked):
    dist = {src: 0}
    q = deque([src])
    while q:
        x, y = q.popleft()
        for dx, dy in DIRS.values():
            n = (x + dx, y + dy)
            if n in dist or n in blocked or not (0 <= n[0] < W and 0 <= n[1] < H):
                continue
            dist[n] = dist[(x, y)] + 1
            q.append(n)
    return dist


def plan(snake, food):
    """Return per-direction move info + reachability summary."""
    body = set(snake)
    out = {}
    for name, (dx, dy) in DIRS.items():
        head = (snake[0][0] + dx, snake[0][1] + dy)
        if not (0 <= head[0] < W and 0 <= head[1] < H) or head in body - {snake[-1]}:
            out[name] = {"legal": False, "reason": "wall" if not (
                0 <= head[0] < W and 0 <= head[1] < H) else "body"}
            continue
        eats = head == food
        new_snake = [head] + snake if eats else [head] + snake[:-1]
        occ = set(new_snake)
        free = {(x, y) for x in range(W) for y in range(H)} - occ
        # safe iff we can still chase our tail (survival invariant)
        tail_ok = head in bfs_dist(new_snake[-1], occ - {head, new_snake[-1]}) or head == new_snake[-1]
        room = len(bfs_dist(head, occ - {head})) >= len(new_snake)
        d_before = bfs_dist(snake[0], body - {snake[0]})
        d_after = bfs_dist(head, occ - {head})
        adv = d_before.get(food, 999) - d_after.get(food, 999)
        out[name] = {"legal": True, "safe": bool(tail_ok and room),
                     "eats": eats, "advance": adv if eats else adv}
    return out


def play(router, max_steps=300, seed=7):
    import random
    rng = random.Random(seed)
    snake = [(W // 2, H // 2 + 1), (W // 2, H // 2), (W // 2 - 1, H // 2)]
    food = (rng.randrange(W), rng.randrange(H))
    while food in snake:
        food = (rng.randrange(W), rng.randrange(H))
    frames, judgments, ms_total = [], 0, 0
    eaten, shields, deaths = 0, 0, 0
    for step in range(max_steps):
        moves = plan(snake, food)
        safe = [m for m, v in moves.items() if v.get("safe")]
        occ = set(snake)
        reachable = food in bfs_dist(snake[0], occ - {snake[0]})
        space = W * H - len(snake)
        preferred = max(safe, key=lambda m: (moves[m]["eats"], moves[m]["advance"])) if safe else "NONE"

        def desc(m):
            v = moves[m]
            if not v["legal"]:
                return "Blocked. Collision."
            if not v["safe"]:
                return "Unsafe. Traps the snake."
            if v["eats"]:
                return "Safe. Eat food now. Best."
            if m == preferred:
                return "Safe. Best route to food."
            return "Safe. Slower route."

        state = (f"Safe route: {'yes' if safe else 'no'}. "
                 f"Food reachable through empty cells: {'yes' if reachable else 'no'}.")
        q = {
            "move": {"type": "choice",
                     "instructions": "Choose the best safe move toward food.",
                     "criteria": {m: desc(m) for m in DIRS}},
            "risk": {"type": "noul", "instructions": "Is a safe route available?"},
            "food": {"type": "noul", "instructions": "Is food reachable through empty cells?"},
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
            frames.append({"probs": probs, "proposed": proposed, "executed": "NONE",
                           "shield": True, "snake": list(snake), "food": food})
            deaths += 1
            break
        dx, dy = DIRS[executed]
        snake = [(snake[0][0] + dx, snake[0][1] + dy)] + snake
        if snake[0] == food:
            eaten += 1
            free = [(x, y) for x in range(W) for y in range(H) if (x, y) not in snake]
            if not free:
                frames.append({"probs": probs, "proposed": proposed, "executed": executed,
                               "shield": shield, "snake": list(snake), "food": None})
                break
            food = rng.choice(free)
        else:
            snake.pop()
        frames.append({"probs": probs, "proposed": proposed, "executed": executed,
                       "shield": shield, "snake": [list(p) for p in snake],
                       "food": list(food) if food else None,
                       "risk": round(1 - ans["risk"]["noul"], 2),
                       "reach": round(ans["food"]["noul"], 2),
                       "conf": round(ans["move"].get("confidence", 0.0), 2)})
    return {"frames": frames, "score": eaten, "steps": len(frames), "shields": shields,
            "judgments": judgments, "ms_total": round(ms_total),
            "mean_ms": round(ms_total / max(judgments, 1)), "alive": deaths == 0,
            "seed": seed}


def to_gif(game, path):
    from PIL import Image, ImageDraw
    import style as S

    cell, Wpx = 30, W * 30
    panel, Hpx = 260, H * 30
    imgs = []
    for i, f in enumerate(game["frames"]):
        im = Image.new("RGB", (Wpx + panel, Hpx), S.BG)
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([Wpx + 4, 4, Wpx + panel - 4, Hpx - 4], radius=10,
                            fill=S.PANEL, outline=S.BORDER, width=1)
        for (x, y) in f["snake"]:
            d.rectangle([x * cell + 1, y * cell + 1, x * cell + cell - 1, y * cell + cell - 1],
                        fill=S.BLUE)
        hx, hy = f["snake"][0]
        d.rectangle([hx * cell + 1, hy * cell + 1, hx * cell + cell - 1, hy * cell + cell - 1],
                    fill=S.GREEN)
        if f["food"]:
            fx, fy = f["food"]
            d.ellipse([fx * cell + 6, fy * cell + 6, fx * cell + cell - 6, fy * cell + cell - 6],
                      fill=S.RED)
        x0 = Wpx + 16
        d.text((x0, 12), f"move {i + 1}/{game['steps']}",
               font=S.font(19, bold=True), fill=S.FG)
        S.badge_row(d, x0, 42, [(f"score {game['score']}", S.GREEN),
                                (f"shields {game['shields']}", S.YELLOW)],
                    size=12, gap=6)
        y = 84
        for m in DIRS:
            p = f["probs"].get(m, 0.0)
            executed = m == f["executed"]
            shielded = f["shield"] and m == f["proposed"] and not executed
            color = S.GREEN if executed else (S.BLUE if shielded else S.CYAN)
            tag = " SHIELD" if executed and f["shield"] else (" »" if shielded else "")
            S.prob_bar(d, x0, y, 90, p, color, label=m, value=f"{p:.2f}{tag}",
                       label_w=46, size=13, h=14)
            y += 24
        d.line([x0, y + 6, x0 + panel - 32, y + 6], fill=S.BORDER, width=1)
        y += 18
        for label, val, color in (("risk", f.get("risk", 0), S.RED),
                                  ("reach", f.get("reach", 0), S.BLUE),
                                  ("conf", f.get("conf", 0), S.PURPLE)):
            S.prob_bar(d, x0, y, 90, val, color, label=label, value=f"{val:.2f}",
                       label_w=46, size=12, h=10)
            y += 20
        if f["shield"]:
            S.badge(d, x0, y + 8, "shield intervened", S.YELLOW, size=12)
        S.badge_row(d, x0, Hpx - 34, [("0 tokens", S.GREEN), ("laya CPU", S.BLUE)],
                    size=12, gap=6)
        imgs.append(im)
    S.save_gif(imgs, path, duration=150)


def main():
    from laya import Router
    router = Router(device="cpu", preload=True)
    game = play(router)
    print(f"score={game['score']} steps={game['steps']} shields={game['shields']} "
          f"alive={game['alive']} mean={game['mean_ms']}ms")
    stats = {k: v for k, v in game.items() if k != "frames"}
    stats["policy"] = "planner features + laya choice + safety shield (compact prompt)"
    with open(os.path.join(HERE, "snake-stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=1)
    frames = []
    for f in game["frames"]:
        frames.append({
            "probs": {m: round(f["probs"].get(m, 0.0), 4) for m in DIRS},
            "proposed": f["proposed"], "executed": f["executed"],
            "shield": f["shield"], "snake": [list(p) for p in f["snake"]],
            "food": list(f["food"]) if f["food"] else None,
            "risk": f.get("risk", 0), "reach": f.get("reach", 0),
            "conf": f.get("conf", 0)})
    with open(os.path.join(HERE, "..", "web", "snake-frames.json"), "w", encoding="utf-8") as f:
        json.dump({"frames": frames, "grid": [W, H], "stats": stats}, f)
    print("wrote web/snake-frames.json", len(frames), "frames")
    to_gif(game, os.path.join(HERE, "..", "assets", "snake.gif"))


if __name__ == "__main__":
    main()
