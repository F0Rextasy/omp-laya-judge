"""Laya Quiz Master: 8 trivia questions, every answer a laya `choice` judgment.

Run: python demo/quiz.py  (writes assets/quiz.gif + demo/quiz-stats.json)
This is laya's home turf — semantic choice among described options — so the
scoreboard is the honest counterpart to the snake experiment (see demo/snake.py,
where single-step spatial pathfinding scores 0).
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))

QUESTIONS = [
    # Every answer is grounded in the state text: laya decides over given
    # material, it does not recall trivia. That is the honest demo.
    ({"email": "You were charged twice for invoice #4411. Refund the duplicate today.",
      "task": "route to the right department"},
     {"dept": {"type": "choice", "instructions": "Which department handles this?",
               "criteria": {"billing": "invoices, payments, refunds",
                            "support": "technical help, bug reports",
                            "security": "breaches, suspicious logins"}}},
     ("dept", "billing")),
    ({"sms": "WIN $$$ FREE MONEY click http://bit.ly/x now",
      "task": "is this spam"},
     {"spam": {"type": "bool", "instructions": "Is this message spam?"}},
     ("spam", True)),
    ({"sms": "Your package arrives tomorrow before noon.",
      "task": "is this spam"},
     {"spam": {"type": "bool", "instructions": "Is this message spam?"}},
     ("spam", False)),
    ({"report": "Export deleted all customer records, no backup exists.",
      "task": "rate severity"},
     {"sev": {"type": "score", "instructions": "How severe is this incident?",
              "criteria": ["trivial", "minor", "major", "critical"]}},
     ("sev", 3)),
    ({"report": "Footer shows 2024 instead of 2025.",
      "task": "rate severity"},
     {"sev": {"type": "score", "instructions": "How severe is this incident?",
              "criteria": ["trivial", "minor", "major", "critical"]}},
     ("sev", 0)),
    ({"log": "NullPointerException at startup, app never opens.",
      "task": "route to the right department"},
     {"dept": {"type": "choice", "instructions": "Which department handles this?",
               "criteria": {"billing": "invoices, payments, refunds",
                            "support": "technical help, bug reports",
                            "security": "breaches, suspicious logins"}}},
     ("dept", "support")),
    ({"value": 42, "task": "even or odd"},
     {"even": {"type": "bool", "instructions": "Is the value even?"}},
     ("even", True)),
    ({"alert": "Multiple failed admin logins from an unknown country at 3am.",
      "task": "route to the right department"},
     {"dept": {"type": "choice", "instructions": "Which department handles this?",
               "criteria": {"billing": "invoices, payments, refunds",
                            "support": "technical help, bug reports",
                            "security": "breaches, suspicious logins"}}},
     ("dept", "security")),
]


def play(router):
    results, ms_total = [], 0
    for i, (state, questions, (qid, expected)) in enumerate(QUESTIONS):
        lq = {}
        for k, v in questions.items():
            if v.get("type") == "bool":
                lq[k] = {"type": "noul", "instructions": v.get("instructions", "")}
            else:
                lq[k] = v
        t0 = time.time()
        out = router.predict(state, lq)
        ms = round((time.time() - t0) * 1000)
        ms_total += ms
        ans = out["answers"][qid]
        if ans.get("type") == "noul":
            ans = {"bool": ans["noul"] >= 0.5, "confidence": ans.get("confidence", 0.0)}
        if "choice" in ans:
            got, ok = ans["choice"], ans["choice"] == expected
        elif "bool" in ans:
            got = ans["bool"] >= 0.5
            ok = got == expected
        else:
            got = int(round(ans["score"]))
            ok = got == expected
        disp = lambda v: ("yes" if v is True else "no" if v is False else str(v))
        results.append({"n": i + 1, "picked": ans.get("choice", got),
                        "picked_disp": ans.get("choice", disp(got)),
                        "expected_disp": disp(expected),
                        "expected": expected, "ok": ok,
                        "conf": round(ans.get("confidence", 0.0), 2), "ms": ms,
                        "state": state, "options": (list(questions[qid].get("criteria", {}).keys())
                        if isinstance(questions[qid].get("criteria"), dict)
                        else list(questions[qid].get("criteria", [])) or ["yes", "no"])})
        mark = "✓" if ok else "✗"
        print(f"{mark} Q{i + 1} picked={ans.get('choice', got)!r} expected={expected!r} "
              f"conf={ans.get('confidence', 0):.2f} {ms}ms")
    score = sum(1 for r in results if r["ok"])
    return {"results": results, "score": score, "total": len(results),
            "ms_total": ms_total, "mean_ms": round(ms_total / len(results))}

def to_gif(game, path):
    from PIL import Image, ImageDraw
    import style as S

    W, H = 720, 440
    imgs = []
    score = 0
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for r in game["results"]:
        if r["ok"]:
            score += 1
        q_text = ", ".join(str(v) for v in r["state"].values())
        q_lines = S.wrap(scratch, S.strip_md(q_text), S.font(17, bold=True), 664)
        for phase in range(14):
            im = Image.new("RGB", (W, H), S.BG)
            d = ImageDraw.Draw(im)
            S.badge_row(d, 16, 14, [(f"Q {r['n']}/{game['total']}", S.BLUE),
                                    (f"{r['ms']}ms", S.CYAN),
                                    ("0 LLM tokens", S.GREEN)])
            q_h = 22 + len(q_lines) * 24
            top = S.panel(d, [16, 52, W - 16, 52 + q_h])
            for li, line in enumerate(q_lines):
                d.text((28, top + li * 24), line, font=S.font(17), fill=S.FG)
            y = 52 + q_h + 16
            for opt in r["options"]:
                picked = (r["picked_disp"] == opt)
                expected = (r["expected_disp"] == opt)
                reveal = phase >= 8
                border, color, suffix = S.BORDER, S.FG, ""
                if reveal and picked and r["ok"]:
                    border, color, suffix = S.GREEN, S.GREEN, "✓ picked"
                elif reveal and picked and not r["ok"]:
                    border, color, suffix = S.RED, S.RED, "✗ picked"
                elif reveal and expected and not r["ok"]:
                    border, color, suffix = S.GREEN, S.GREEN, "✓ expected"
                d.rounded_rectangle([40, y, W - 40, y + 40], radius=8,
                                    fill=S.PANEL, outline=border, width=2)
                opt_font = S.font(16, bold=bool(suffix))
                d.text((56, y + 9), opt, font=opt_font, fill=color)
                if suffix:
                    tw = d.textlength(opt, font=opt_font)
                    d.text((56 + tw + 14, y + 11), suffix, font=S.font(14), fill=color)
                y += 50
            if phase >= 8:
                S.badge(d, 40, y + 4, f"laya → {r['picked_disp']}",
                        S.GREEN if r["ok"] else S.RED, size=15)
                S.prob_bar(d, 40, y + 42, 320, r["conf"],
                           S.GREEN if r["ok"] else S.RED,
                           label="confidence", value=f"{r['conf']:.2f}",
                           label_w=110, h=16, size=14)
            else:
                d.text((40, y + 14), f"laya is judging{'.' * (phase % 4)}",
                       font=S.font(15), fill=S.DIM)
                S.prob_bar(d, 40, y + 42, 320, 0.0, S.BLUE,
                           label="confidence", value="…",
                           label_w=110, h=16, size=14)
            S.badge_row(d, 16, H - 34, [(f"score {score}/{game['total']}", S.GREEN),
                                        (f"mean {game['mean_ms']}ms", S.BLUE)])
            imgs.append(im)
    S.save_gif(imgs, path, duration=120)


def main():
    from laya import Router
    router = Router(device="cpu", preload=True)
    game = play(router)
    print(f"score={game['score']}/{game['total']} mean={game['mean_ms']}ms")
    with open(os.path.join(HERE, "quiz-stats.json"), "w", encoding="utf-8") as f:
        json.dump(game, f, indent=1)
    to_gif(game, os.path.join(HERE, "..", "assets", "quiz.gif"))


if __name__ == "__main__":
    main()
