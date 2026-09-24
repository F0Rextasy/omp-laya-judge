"""Compare english vs typed-decisions checkpoints on the 12-case bench.

Run: python benchmark/compare_models.py  (writes benchmark/compare.json)
Sequential loads to stay in RAM. No server involved.
"""
import gc
import json
import os
import time

from run import CASES

HERE = os.path.dirname(os.path.abspath(__file__))
SPECS = {
    "english": ("convaiinnovations/laya", None),
    "typed-decisions": ("convaiinnovations/laya", "typed-decisions"),
    "multilingual": ("convaiinnovations/laya", "multilingual"),
}

# Founder guidance: English checkpoint collapses on non-Latin scripts.
# Turkish cases to prove which checkpoint serves Turkish (our user language).
TR_CASES = [
    ({"fatura": "Ayni faturayi iki kez odedik, iade edin."},
     {"dept": {"type": "choice", "instructions": "Hangi departman?",
               "criteria": {"faturalama": "faturalar, odemeler, iadeler",
                            "destek": "teknik yardim, hata raporlari",
                            "guvenlik": "ihlaller, supheli girisler"}}},
     ("dept", "faturalama")),
    ({"sms": "Hemen tikla, hediye kazandin http://bit.ly/x"},
     {"spam": {"type": "bool", "instructions": "Bu mesaj spam mi?"}},
     ("spam", True)),
    ({"sms": "Kargonuz yarin sabah elinizde olacak."},
     {"spam": {"type": "bool", "instructions": "Bu mesaj spam mi?"}},
     ("spam", False)),
    ({"rapor": "Disari aktarim tum musteri kayitlarini sildi, yedek yok."},
     {"sev": {"type": "score", "instructions": "Olay ne kadar ciddi?",
              "criteria": ["onemsiz", "kucuk", "buyuk", "kritik"]}},
     ("sev", 3)),
]

import random as _random


def score_random(cases):
    rng = _random.Random(0)
    ok = 0
    for _state, questions, (qid, expected) in cases:
        q = questions[qid]
        if q.get("type") == "choice":
            got = rng.choice(list(q["criteria"].keys()))
            ok += got == expected
        elif q.get("type") == "bool":
            got = rng.choice([True, False])
            ok += got == expected
        else:
            got = rng.randrange(len(q["criteria"]))
            ok += got == expected
    return {"correct": ok, "total": len(cases), "mean_ms": 0, "mean_conf": 0.0}


def score_one_cases(router, model, cases):
    ok, ms, confs = 0, 0, []
    for state, questions, (qid, expected) in cases:
        lq = {}
        for k, v in questions.items():
            if v.get("type") == "bool":
                lq[k] = {"type": "noul", "instructions": v.get("instructions", "")}
            else:
                lq[k] = v
        t0 = time.time()
        out = router.predict(state, lq, model=model)
        ms += (time.time() - t0) * 1000
        ans = out["answers"][qid]
        if "choice" in ans:
            good = ans["choice"] == expected
        elif "noul" in ans:
            good = (ans["noul"] >= 0.5) == expected
        else:
            good = int(round(ans["score"])) == expected
        ok += good
        confs.append(round(ans.get("confidence", 0.0), 3))
    n = max(len(cases), 1)
    return {"correct": ok, "total": len(cases), "mean_ms": round(ms / n),
            "mean_conf": round(sum(confs) / len(confs), 3) if confs else 0.0}


def score_one(router, model):
    return score_one_cases(router, model, CASES)


def main():
    from laya import Router
    results = {}
    for name, (repo, sub) in SPECS.items():
        r = Router(models={name: (repo, sub)}, device="cpu", preload=True)
        results[name] = score_one(r, name)
        results[name]["tr"] = score_one_cases(r, name, TR_CASES)
        print(name, results[name], flush=True)
        del r
        gc.collect()
    results["random"] = score_random(CASES)
    results["random"]["tr"] = score_random(TR_CASES)
    print("random", results["random"], flush=True)
    with open(os.path.join(HERE, "compare.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    print("wrote benchmark/compare.json")


if __name__ == "__main__":
    main()
