"""Routing tripwires: non-Latin must leave the english checkpoint, and
real Turkish orthography (with diacritics) must stay above the floor.

The english checkpoint is confidently wrong on non-Latin scripts upstream
documents 0.000 accuracy at 0.952 confidence on Khmer — so routing, not
just answers, is the thing under test. Skipped unless LAYA_SLOW_TESTS=1.
"""
import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "server")

SLOW = os.environ.get("LAYA_SLOW_TESTS") == "1"

DEPT = {"billing": "faturalar, odemeler, iadeler",
        "support": "teknik yardim, hata raporlari",
        "guvenlik": "ihlaller, supheli girisler"}


@unittest.skipUnless(SLOW, "set LAYA_SLOW_TESTS=1 (needs cached checkpoints)")
class TripwireBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SERVER not in sys.path:
            sys.path.insert(0, SERVER)
        import core
        from tests.support import post_json, start_real_sidecar
        cls.core = core
        cls.post_json = staticmethod(post_json)
        cls.url, _sidecar = start_real_sidecar()

    @classmethod
    def judge(cls, state, questions):
        raw = cls.post_json(cls.url, {"state": state, "questions": questions})
        _converted, kinds = cls.core.to_laya_questions(questions)
        return cls.core.judge_result(raw, kinds, raw["latency_ms"])


class TestNonLatinRouting(TripwireBase):
    def test_thai_state_routes_to_multilingual(self):
        out = self.judge(
            {"message": "ข้อความนี้เป็นภาษาไทยเพื่อทดสอบระบบจัดเส้นทาง"},
            {"dept": {"type": "choice", "instructions": "Which department?",
                      "criteria": DEPT}})
        self.assertEqual(out["model"], "laya/multilingual",
                         f"non-Latin state must not ride the english checkpoint: {out}")

    def test_latin_state_stays_english(self):
        out = self.judge(
            {"text": "The invoice was charged twice, please refund it."},
            {"dept": {"type": "choice", "instructions": "Which department?",
                      "criteria": DEPT}})
        self.assertEqual(out["model"], "laya/english")


class TestTurkishDiacritics(TripwireBase):
    """v0.2.0 only tested ASCII-folded Turkish (\"Ayni faturayi\")."""

    CASES = [
        ({"fatura": "Aynı faturayı iki kez ödedik, lütfen iade edin."},
         {"dept": {"type": "choice", "instructions": "Hangi departman?",
                   "criteria": DEPT}}, "dept", "billing"),
        ({"sms": "Hemen tıkla, hediye kazandın http://bit.ly/x"},
         {"spam": {"type": "bool", "instructions": "Bu mesaj spam mi?"}}, "spam", True),
        ({"sms": "Kargonuz yarın sabah elinizde olacak."},
         {"spam": {"type": "bool", "instructions": "Bu mesaj spam mi?"}}, "spam", False),
        ({"rapor": "Dışa aktarım tüm müşteri kayıtlarını sildi, yedek yok."},
         {"sev": {"type": "score", "instructions": "Olay ne kadar ciddi?",
                  "criteria": ["onemsiz", "kucuk", "buyuk", "kritik"]}}, "sev", 3),
    ]

    def test_score_floor(self):
        correct = 0
        for state, questions, qid, expected in self.CASES:
            ans = self.judge(state, questions)["answers"][qid]
            if "choice" in ans:
                ok = ans["choice"] == expected
            elif "bool" in ans:
                ok = (ans["bool"] >= 0.5) == expected
            else:
                ok = int(round(ans["score"])) == expected
            correct += ok
        # Measured on this machine with laya 0.3.20 auto-routing: see git
        # history for the run; floor kept at half the suite so a routing or
        # checkpoint regression trips it.
        self.assertGreaterEqual(correct, 2, f"TR diacritic floor 2/4, got {correct}")


if __name__ == "__main__":
    unittest.main()
