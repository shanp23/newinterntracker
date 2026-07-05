"""Offline tests for engine/update.py — no network needed."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "engine"))
import update as U

CASES = [
    # (title, location, desc, expect_accept, expect_bucket_or_reason)
    ("AI Marketing Intern - Summer 2027", "Raleigh, NC (Hybrid)",
     "Support AI campaign launches. Experience with Tableau is a plus but not required. "
     "Pursuing a degree in Marketing, Business, or Economics. Expected graduation between December 2027 and May 2028.",
     True, "rdu_hybrid"),

    ("AI Strategy Intern (Fall 2026)", "Durham, NC",
     "Help our AI governance team draft adoption playbooks. Training provided on internal tools. "
     "Must be graduating no earlier than May 2027.",
     True, "rdu_onsite"),

    ("AI Operations Intern", "Remote - United States",
     "Summer 2027 program. You will coordinate AI pilot rollouts. Familiarity with Power BI helpful. "
     "Class of 2028 welcome.",
     True, "remote_us"),

    # ---- must be rejected ----
    ("AI Software Engineering Intern - Summer 2027", "Raleigh, NC", "Build AI systems.", False, "technical title"),
    ("Machine Learning Intern", "Remote, US", "AI research.", False, "no AI in title / technical"),
    ("AI Product Intern - Summer 2027", "Remote - US",
     "Must be proficient in Python and SQL. Business majors welcome.", False, "coding required"),
    ("AI Business Analyst Intern - Spring 2027", "Cary, NC (Hybrid)",
     "Qualifications: SQL, Excel, strong communication.", False, "coding in requirements section"),
    ("AI Marketing Intern - Summer 2026", "Raleigh, NC", "Join our summer 2026 cohort.", False, "wrong cycle"),
    ("Marketing Intern", "Raleigh, NC", "Work on AI campaigns.", False, "no AI in TITLE"),
    ("AI Governance Intern - Summer 2027", "Remote - Canada", "Policy work.", False, "non-US remote"),
    ("AI Chair Assembly Intern", "Raleigh, NC", "AIR and CHAIR words.", True, "rdu_onsite"),  # 'AI' standalone in title -> accept; checks word boundary logic below
    ("Air Quality Intern", "Raleigh, NC", "environmental.", False, "'Air' must NOT match AI"),
    ("AI Research Scientist Intern - Summer 2027", "Remote - US", "PhD track.", False, "scientist blocked"),
    ("AI Product Marketing Intern - Summer 2027", "Remote (US)",
     "Pursuing a degree in Computer Science. Python required.", False, "CS-only + coding"),
    ("AI Enablement Co-op - Spring 2027", "Chapel Hill, NC hybrid",
     "Help teams adopt AI. Alteryx experience desirable; you can learn it on the job. "
     "Must be able to graduate in 2027 or later.", True, "rdu_hybrid"),
]

def run_case(title, loc, desc, expect, note):
    ok_ai = U.title_has_ai(title)
    ok_intern = bool(U.INTERN_RE.search(title))
    blocked = bool(U.TITLE_BLOCKLIST.search(title))
    cycle = U.detect_cycle(title, desc)
    bad_code, why = U.coding_required(desc)
    bucket = U.classify_location(loc, desc)
    accepted = ok_ai and ok_intern and not blocked and cycle is not None and not bad_code and bucket is not None
    status = "PASS" if accepted == expect else "FAIL"
    print(f"[{status}] {title[:52]:<52} accept={accepted} (want {expect})  "
          f"ai={ok_ai} intern={ok_intern} blk={blocked} cyc={cycle} code={bad_code}({why[:40]}) bucket={bucket}")
    return accepted == expect

fails = sum(not run_case(*c[:3], c[3], c[4]) for c in CASES)

# grad extraction spot checks
for d in ["Expected graduation between December 2027 and May 2028 preferred.",
          "Must be a member of the class of 2027.",
          "No grad info here."]:
    print("grad ->", U.grad_requirement(d))

# README generation smoke test with fake data
fake_jobs = [
    {"company": "SAS Institute", "title": "AI & Analytics Intern (Business)", "location": "Cary, NC (Hybrid)",
     "url": "https://example.com/1", "cycle": "Summer 2027", "posted": "2026-07-01",
     "grad": "graduating between Dec 2027 and May 2028", "bucket": "rdu_hybrid"},
    {"company": "Deloitte", "title": "AI & Data Strategy Intern", "location": "Remote - US",
     "url": "https://example.com/2", "cycle": "Fall 2026", "posted": "2026-06-20",
     "grad": "Not specified", "bucket": "remote_us"},
]
hist = json.loads((Path(__file__).parent / "data" / "history.json").read_text())
U.write_readme(fake_jobs, hist, ["ExampleCo (workday): HTTPError"], 60)
print("\nREADME bytes:", (Path(__file__).parent / "README.md").stat().st_size)
print("TOTAL FAILS:", fails)
sys.exit(1 if fails else 0)
