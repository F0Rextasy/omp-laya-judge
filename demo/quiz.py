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
    W, H = 640, 400
    imgs = []
    score = 0
    for r in game["results"]:
        if r["ok"]:
            score += 1
        state_txt = json.dumps(r["state"])[:72]
        opts = [f"{o} {'(picked)' if o == r['picked_disp'] else ''}".rstrip()
                for o in r["options"]]
        for phase in range(14):
            im = Image.new("RGB", (W, H), (13, 17, 23))
            d = ImageDraw.Draw(im)
            d.text((20, 20), f"Q{r['n']}/{game['total']}: {state_txt}", fill=(230, 237, 243))
            y = 60
            for ox, opt in enumerate(r["options"]):
                picked = (r["picked_disp"] == opt)
                marker = ""
                if phase >= 8:
                    if picked:
                        marker = ">> "
                    elif opt == r["expected_disp"] and not r["ok"]:
                        marker = "(correct) "
                color = (63, 185, 80) if (phase >= 8 and picked and r["ok"]) else \
                    (248, 81, 73) if (phase >= 8 and picked and not r["ok"]) else \
                    (139, 148, 158)
                d.text((30, y), f"{marker}{opt}", fill=color)
                y += 26
            d.text((20, H - 60),
                   f"laya: {r['picked_disp']} (conf {r['conf']})" if phase >= 8 else "laya is judging...",
                   fill=(230, 237, 243))
            d.text((20, H - 34), f"score: {score}/{game['total']} - 0 LLM tokens",
                   fill=(139, 148, 158))
            imgs.append(im)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=120, loop=0)
    print("wrote", path, len(imgs), "frames")


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
