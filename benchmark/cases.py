"""One case set, every backend.

A 12-case bench could not separate two models: the two misses sat one bucket
from the answer, and one case flipping changes accuracy by 8%. The set below
is 48 cases across the four things the layer actually does, so a backend's
weakness shows up as a *pattern* instead of noise.

Groups:
  route    - pick one of 2-4 departments/labels from a short set
  triage   - yes/no decisions: spam, sentiment, language, urgency, domain
  severity - 0..3 rubric scoring, where the score head is weakest
  mixed    - the real call shape: a JSON document, two questions at once
  turkish  - the user's own language; the English checkpoint is documented as
             weaker on non-Latin scripts, and that must show up in the numbers

No case is tuned to favour a backend. Where the rubric is genuinely arguable
the expected value is the majority reading, and `note` records why.
"""
from typing import Any, Dict, List, Tuple

Case = Dict[str, Any]

DEPT = {
    "billing": "invoices, payments, refunds, charges",
    "support": "bugs, crashes, how-to, technical help",
    "security": "breach, account takeover, leaked credentials",
}

SPAM_Q = {"type": "bool", "instructions": "is this unsolicited bulk mail?"}
URGENT_Q = {"type": "bool", "instructions": "does this need a human within the hour?"}
TECH_Q = {"type": "bool", "instructions": "is this a technical fault rather than a request?"}
LANG_Q = {"type": "bool", "instructions": "is this text written in english?"}
SAT_Q = {"type": "bool", "instructions": "is the writer angry or dissatisfied?"}

SEV = {"type": "score", "instructions": "how severe is this incident?",
       "criteria": ["trivial", "minor", "major", "critical"]}

ROUTE_Q = {"type": "choice", "instructions": "which team owns this?", "criteria": DEPT}


def _route(text: str, expected: str, note: str = "") -> Case:
    return {"id": f"route-{len(CASES)}", "group": "route", "state": {"text": text},
            "questions": {"dept": ROUTE_Q}, "expected": {"dept": expected}, "note": note}


CASES: List[Case] = []

# --- route: the decision the layer does most often -------------------------
for text, expected in [
    ("Charged twice for the same subscription, please refund", "billing"),
    ("How do I rotate my API key?", "billing"),
    ("The app crashes on launch since 3.2.0", "support"),
    ("Where is the export button in the settings page?", "support"),
    ("An attacker logged into my account from another country", "security"),
    ("We leaked an API key in a public commit, rotate it now", "security"),
    ("My card was declined but the money left the account", "billing"),
    ("The dashboard shows stale data since yesterday", "support"),
    ("Someone reset my password without asking", "security"),
    ("Please move the invoice to next month", "billing"),
    ("The mobile app crashes when opening the camera", "support"),
    ("I think my session cookie is being stolen", "security"),
]:
    CASES.append(_route(text, expected))

# --- triage: yes/no --------------------------------------------------------
BOOL_CASES = [
    ("WINNER!!! Click here to claim your prize now", SPAM_Q, True, "triage"),
    ("Hi, here are the meeting notes from Tuesday", SPAM_Q, False, "triage"),
    ("The production database is down, all users are affected", URGENT_Q, True, "triage"),
    ("When is the next planned maintenance window?", URGENT_Q, False, "triage"),
    ("The login button does nothing when clicked", TECH_Q, True, "triage"),
    ("Can you move my invoice to the next billing period?", TECH_Q, False, "triage"),
    ("How many users signed up last month?", TECH_Q, False, "triage"),
    ("The writer states the third refund was denied and demands an explanation", SAT_Q, True, "triage"),
    ("Thanks, that solved it, much appreciated", SAT_Q, False, "triage"),
    ("You promised Friday and it is now Tuesday", SAT_Q, True, "triage"),
    ("The upload stalls at 90 percent every time", TECH_Q, True, "triage"),
    ("Please cancel my subscription going forward", TECH_Q, False, "triage"),
]
for text, question, expected, group in BOOL_CASES:
    CASES.append({"id": f"{group}-{len(CASES)}", "group": group, "state": {"text": text},
                  "questions": {"q": question}, "expected": {"q": expected}, "note": ""})

# --- language on both sides of the atlas ----------------------------------
for text, expected in [
    ("Please reset my password, I cannot log in", True),
    ("Die Rechnung wurde doppelt belastet, bitte um Erstattung", False),
    ("Uygulama sürekli kapaniyor, yardım eder misiniz", False),
    ("この請求書の重複請求を返金してください", False),
    ("Please double charge my card so I can test the refund flow", True),
    ("El servicio no funciona desde ayer por la mañana", False),
]:
    CASES.append({"id": f"lang-{len(CASES)}", "group": "triage", "state": {"text": text},
                  "questions": {"en": LANG_Q}, "expected": {"en": expected},
                  "note": "english detection across scripts"})

# --- severity: where the score head is weakest ----------------------------
SEV_CASES = [
    ("Typo in the footer copyright year", 0, "cosmetic"),
    ("Button colour slightly off in dark mode", 0, "cosmetic"),
    ("Search results misspell a vendor name", 1, "small user-facing bug"),
    ("The CSV export shifts one column left", 1, "wrong data, not fatal"),
    ("The login page takes 12 seconds to load on mobile", 2, "major slowness, core path"),
    ("Checkout occasionally fails with a generic error", 2, "revenue path, intermittent"),
    ("All customer records were deleted by a bad migration, no backup", 3, "irreversible data loss"),
    ("The production database is unreachable worldwide", 3, "total outage"),
]
for text, expected, note in SEV_CASES:
    CASES.append({"id": f"sev-{len(CASES)}", "group": "severity", "state": {"text": text},
                  "questions": {"sev": SEV}, "expected": {"sev": expected}, "note": note})

# --- mixed: the real call shape, JSON state and two questions at once -----
CASES.extend([
    {"id": "mixed-1", "group": "mixed",
     "state": {"subject": "Refund my duplicate payment", "body": "Charged twice for March.", "attachments": 1},
     "questions": {"billing": ROUTE_Q, "urgent": URGENT_Q},
     "expected": {"billing": "billing", "urgent": False}, "note": ""},
    {"id": "mixed-2", "group": "mixed",
     "state": {"subject": "Account takeover", "body": "Password was reset from another country and 2FA is off."},
     "questions": {"dept": ROUTE_Q, "urgent": URGENT_Q},
     "expected": {"dept": "security", "urgent": True}, "note": ""},
    {"id": "mixed-3", "group": "mixed",
     "state": {"subject": "Where is the export button?", "body": "I cannot find it in the settings page."},
     "questions": {"dept": ROUTE_Q, "tech": TECH_Q},
     "expected": {"dept": "support", "tech": True}, "note": "not urgent, but technical"},
    {"id": "mixed-4", "group": "mixed",
     "state": {"subject": "Chargeback", "body": "My bank reversed the charge for the annual plan."},
     "questions": {"dept": ROUTE_Q, "sev": SEV},
     "expected": {"dept": "billing", "sev": 1}, "note": "money, but reversible"},
    {"id": "mixed-5", "group": "mixed",
     "state": {"subject": "Leaked key", "body": "AWS secret is visible in the public repo commit 4f2a1c9."},
     "questions": {"dept": ROUTE_Q, "urgent": URGENT_Q},
     "expected": {"dept": "security", "urgent": True}, "note": ""},
    {"id": "mixed-6", "group": "mixed",
     "state": {"subject": "Slow dashboard", "body": "Charts take 20 seconds to render for large ranges."},
     "questions": {"dept": ROUTE_Q, "sev": SEV},
     "expected": {"dept": "support", "sev": 2}, "note": ""},
])

# --- the user's own language ----------------------------------------------
TR_CASES = [
    ("Ayni faturayi iki kez odedim, iade edin", ROUTE_Q, {"dept": "billing"}),
    ("Uygulama acildiginda cokmeye basliyor", ROUTE_Q, {"dept": "support"}),
    ("Hesabima tanimadigim biri girdi, sifremi degistirdi", ROUTE_Q, {"dept": "security"}),
    ("Aylik faturami bir sonraki aya almak istiyorum", ROUTE_Q, {"dept": "billing"}),
    ("Bu hafta kactin kez gonderdiniz, geri cekin", SPAM_Q, {"q": True}),
    ("Toplanti notlarini paylasmak istiyorum", SPAM_Q, {"q": False}),
    ("Veritabani tamamen silinmis, yedek yok", SEV, {"sev": 3}),
    ("Menudeki yazi hatasi", SEV, {"sev": 0}),
]
for text, question, expected in TR_CASES:
    group = "severity" if question is SEV else ("triage" if question is SPAM_Q else "turkish")
    CASES.append({"id": f"{group}-{len(CASES)}", "group": group, "state": {"text": text},
                  "questions": {"q": question}, "expected": expected, "note": "turkish"})

BY_GROUP: Dict[str, List[Case]] = {}
for case in CASES:
    BY_GROUP.setdefault(case["group"], []).append(case)

assert len(CASES) == 52, f"expected 52 cases, built {len(CASES)}"
