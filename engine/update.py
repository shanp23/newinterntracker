#!/usr/bin/env python3
"""
Non-Technical AI Internship Engine
==================================
Polls FREE public job-board endpoints (Greenhouse, Lever, Ashby,
SmartRecruiters, Workable, Workday) -- no API keys, no LLM calls, $0 cost.

Keeps ONLY:
  * Internships / co-ops whose TITLE contains "AI" (or "Artificial Intelligence")
  * Cycles: Fall 2026, Spring 2027, Summer 2027
  * NON-TECHNICAL roles: no coding / programming-language requirement,
    no software-engineering roles. Data/analytics tools are allowed only when
    the posting says they are preferred / a plus / not required / will-train.

Buckets results into four tables:
  1. Drop Radar  - Raleigh-Durham HYBRID roles (last cycle's post date -> expected this cycle)
  2. Active      - Raleigh-Durham area postings open right now
  3. Drop Radar  - US-REMOTE roles
  4. Active      - US-REMOTE postings open right now

Run:  python engine/update.py          (regenerates README.md)
"""

import csv
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_CSV = ROOT / "companies.csv"
SEEN_JSON = ROOT / "data" / "seen.json"
HISTORY_JSON = ROOT / "data" / "history.json"
UPSTREAM_CACHE = ROOT / "data" / "upstream_companies.json"
README = ROOT / "README.md"

# The 3,541-company registry maintained by the reference engine
# (zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships).
# Downloaded fresh on every run -- free, public, no key -- and cached locally
# as a fallback in case the download ever fails.
REGISTRY_URL = ("https://raw.githubusercontent.com/zshah101/"
                "Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships/"
                "main/data/companies.json")
MAX_WORKERS = 24

UA = {"User-Agent": "Mozilla/5.0 (compatible; NonTechAIInternTracker/1.0; +github)"}
TIMEOUT = 25
TODAY = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# 1. FILTER RULES
# ---------------------------------------------------------------------------

# Title MUST contain standalone "AI" (case-sensitive, word-bounded so "air",
# "aid", "chair" never match) or the phrase "artificial intelligence".
def title_has_ai(title: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z])AI(?![A-Za-z])", title)
                or re.search(r"artificial\s+intelligence", title, re.I))

INTERN_RE = re.compile(r"\b(intern(ship)?|co-?op)\b", re.I)

# Technical roles rejected outright by title.
TITLE_BLOCKLIST = re.compile(
    r"\b(software|engineer|engineering|developer|devops|sre|swe|programmer|"
    r"machine\s*learning|ml\b|deep\s*learning|data\s*scien(ce|tist)|scientist|"
    r"computer\s*vision|nlp|llm|robotic|firmware|hardware|embedded|full[\s-]?stack|"
    r"front[\s-]?end|back[\s-]?end|cyber|security|infrastructure|architect|"
    r"quant(itative)?|analytics\s+engineer|prompt\s+engineer|applied\s+research|"
    r"research\s+scientist|technical|technology\s+(rotational|development)\s+program|"
    r"coding|it\s+intern)\b",
    re.I,
)

# Hard-coded coding / programming keywords. If the description REQUIRES any of
# these, the role is rejected.
CODING_KW = re.compile(
    r"\bpython\b|\bjava(script)?\b|c\+\+|c#|\bc\s*(programming|language)\b|"
    r"\bsql\b|\bhtml\b|\bcss\b|\btypescript\b|\bmatlab\b|"
    r"\br\s+(programming|language)\b|\bgolang\b|\bscala\b|\bpytorch\b|"
    r"\btensorflow\b|\bkeras\b|\bgit(hub)?\b|\bprogramming\b|\bcoding\b|"
    r"\bscripting\b|software\s+development|software\s+engineering|"
    r"object[\s-]oriented|api\s+development|machine\s+learning\s+model",
    re.I,
)

# Data-aggregation / BI tools: allowed ONLY if softened (preferred / plus /
# not required / will train) -- per user requirement.
DATA_TOOL_KW = re.compile(
    r"\b(tableau|power\s*bi|alteryx|snowflake|databricks|looker|qlik|sas\b|"
    r"spss|stata|salesforce|hubspot|big\s*query)\b",
    re.I,
)

SOFTENER = re.compile(
    r"\b(preferr?ed|a\s+plus|nice[\s-]to[\s-]have|not\s+required|"
    r"no\s+experience\s+(necessary|required)|will\s+(be\s+)?train|training\s+(is\s+)?provided|"
    r"willing(ness)?\s+to\s+learn|helpful|bonus|desirable|advantage(ous)?|"
    r"familiarity|exposure\s+to|learn\s+on\s+the\s+job|can\s+be\s+learned|"
    r"is\s+beneficial|would\s+be\s+great|ideal(ly)?|encouraged)\b",
    re.I,
)

REQUIRER = re.compile(
    r"\b(required|require[sd]?|must\b|proficien(t|cy)|strong\s+(knowledge|skills?|command)|"
    r"working\s+knowledge|demonstrated|hands[\s-]on\s+experience|fluent|expert(ise)?|"
    r"minimum\s+qualification|you\s+will\s+need|you\s+must|ability\s+to\s+(code|program|write))\b",
    re.I,
)

CYCLES = {
    "Fall 2026": re.compile(r"\b(fall|autumn)\s*(of\s*)?2026\b|\b2026\s*fall\b", re.I),
    "Spring 2027": re.compile(r"\bspring\s*(of\s*)?2027\b|\b2027\s*spring\b", re.I),
    "Summer 2027": re.compile(r"\bsummer\s*(of\s*)?2027\b|\b2027\s*summer\b|\bsummer\s*'?27\b", re.I),
}
# Wrong-cycle mentions used to exclude generic postings that are clearly for
# a cycle we don't track.
OTHER_CYCLE = re.compile(r"\b(summer|spring)\s*(of\s*)?2026\b|\b(fall|autumn)\s*(of\s*)?2027\b|\b202[89]\b|\b2025\b", re.I)

RDU_CITIES = re.compile(
    r"\b(raleigh|durham|chapel\s*hill|cary|morrisville|apex|research\s+triangle|rtp)\b", re.I
)
HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
REMOTE_RE = re.compile(r"\bremote\b", re.I)
US_RE = re.compile(r"\b(united\s+states|usa?|u\.s\.?a?\.?|us[\s-]?based|anywhere\s+in\s+the\s+us|nationwide)\b", re.I)
NON_US_RE = re.compile(r"\b(canada|uk|united\s+kingdom|emea|europe|india|singapore|australia|apac|latam|mexico|germany|france|ireland|netherlands|japan|china|brazil)\b", re.I)

GRAD_RE = re.compile(
    r"([^.\n]{0,80}\b(graduat\w+|class\s+of|degree\s+completion|expected\s+to\s+graduate)"
    r"[^.\n]{0,90}?\b20\d{2}\b[^.\n]{0,25})",
    re.I,
)

STRIP_TAGS = re.compile(r"<[^>]+>")


def clean_text(raw: str) -> str:
    if not raw:
        return ""
    txt = html.unescape(raw)
    txt = STRIP_TAGS.sub(" ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def coding_required(desc: str):
    """Return (rejected: bool, reason: str).  Sentence-level analysis:
    a coding keyword only rejects when the same sentence carries requirement
    language and no softener.  Data tools reject on the same rule."""
    sentences = re.split(r"(?<=[.!?;•·\n])\s+|\u2022", desc)
    for s in sentences:
        if not s.strip():
            continue
        has_code = CODING_KW.search(s)
        has_tool = DATA_TOOL_KW.search(s)
        if not (has_code or has_tool):
            continue
        soft = SOFTENER.search(s)
        hard = REQUIRER.search(s)
        if soft:
            continue                      # "Python a plus" -> fine
        if has_code and hard:
            return True, f"coding required: '{s.strip()[:110]}'"
        if has_code and not hard:
            # bare mention inside a Requirements/Qualifications block is a
            # requirement in disguise -> reject; elsewhere it's contextual.
            if re.search(r"(qualification|requirement|must[\s-]have|what\s+you.ll\s+need)", s, re.I):
                return True, f"coding in requirements: '{s.strip()[:110]}'"
            continue
        if has_tool and hard:
            return True, f"data tool required: '{s.strip()[:110]}'"
    # Reject CS-only degree requirements (user is Econ major / BusAdm minor)
    if re.search(r"(pursuing|enrolled\s+in)[^.\n]{0,60}computer\s+science", desc, re.I) and not re.search(
        r"(business|econom|marketing|communicat|management|liberal\s+arts|any\s+major|related\s+field)", desc, re.I
    ):
        return True, "CS-degree-only requirement"
    if re.search(r"software\s+engineering", desc, re.I) and re.search(
        r"(experience|background|skills?)\s+in[^.\n]{0,40}software\s+engineering", desc, re.I
    ):
        return True, "software engineering background required"
    return False, ""


def detect_cycle(title: str, desc: str):
    # 1) The title is authoritative: "AI Marketing Intern - Summer 2027"
    t_hits = [name for name, rx in CYCLES.items() if rx.search(title)]
    if t_hits:
        return " / ".join(t_hits)
    if OTHER_CYCLE.search(title):
        return None                       # title names a cycle we don't track
    # 2) Fall back to the description
    d_hits = [name for name, rx in CYCLES.items() if rx.search(desc)]
    if d_hits:
        return " / ".join(d_hits)
    if OTHER_CYCLE.search(desc):
        return None
    return "Not stated (verify)"          # generic intern posting, kept but flagged


def grad_requirement(desc: str) -> str:
    m = GRAD_RE.search(desc)
    if m:
        snippet = re.sub(r"\s+", " ", m.group(1)).strip(" ,;:-")
        return (snippet[:120] + "…") if len(snippet) > 120 else snippet
    return "Not specified"


def classify_location(loc: str, desc: str, is_remote_flag=False):
    """Return one of: 'rdu_hybrid', 'rdu_onsite', 'remote_us', None.
    City/country tests use ONLY the location field (descriptions casually
    mention other cities); the hybrid test may also look at the description."""
    if RDU_CITIES.search(loc):
        hybrid = HYBRID_RE.search(loc) or HYBRID_RE.search(desc[:5000])
        return "rdu_hybrid" if hybrid else "rdu_onsite"
    remote = is_remote_flag or REMOTE_RE.search(loc)
    if remote:
        if NON_US_RE.search(loc) and not US_RE.search(loc):
            return None                   # remote, but for another country
        return "remote_us"                # US or unspecified-remote
    return None


# ---------------------------------------------------------------------------
# 2. ATS FETCHERS  (all free, public, key-less)
# ---------------------------------------------------------------------------

def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def fetch_greenhouse(token):
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true").json()
    for j in data.get("jobs", []):
        yield {
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "desc": clean_text(j.get("content", "")),
            "url": j.get("absolute_url", ""),
            "posted": (j.get("first_published") or j.get("updated_at") or "")[:10],
        }


def fetch_lever(token):
    data = _get(f"https://api.lever.co/v0/postings/{token}?mode=json").json()
    for j in data:
        ts = j.get("createdAt")
        yield {
            "title": j.get("text", ""),
            "location": (j.get("categories") or {}).get("location", "") or "",
            "desc": clean_text(j.get("descriptionPlain") or j.get("description", "")),
            "url": j.get("hostedUrl", ""),
            "posted": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "",
        }


def fetch_ashby(token):
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=false").json()
    for j in data.get("jobs", []):
        yield {
            "title": j.get("title", ""),
            "location": j.get("location", "") or "",
            "desc": clean_text(j.get("descriptionPlain") or j.get("descriptionHtml", "")),
            "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
            "posted": (j.get("publishedAt") or "")[:10],
            "is_remote": bool(j.get("isRemote")),
        }


def fetch_smartrecruiters(token):
    base = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    data = _get(base + "?limit=100").json()
    for j in data.get("content", []):
        title = j.get("name", "")
        # cheap pre-filter before pulling the full description
        if not (title_has_ai(title) and INTERN_RE.search(title)):
            continue
        loc = j.get("location") or {}
        loc_s = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
        desc = ""
        try:
            detail = _get(f"{base}/{j['id']}").json()
            secs = (detail.get("jobAd") or {}).get("sections") or {}
            desc = clean_text(" ".join((secs.get(k) or {}).get("text", "") for k in secs))
        except Exception:
            pass
        yield {
            "title": title,
            "location": loc_s + (" (Remote)" if loc.get("remote") else ""),
            "desc": desc,
            "url": f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
            "posted": (j.get("releasedDate") or "")[:10],
        }


def fetch_workable(token):
    data = _get(f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true").json()
    for j in data.get("jobs", []):
        yield {
            "title": j.get("title", ""),
            "location": ", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")]))
            + (" (Remote)" if j.get("telecommuting") else ""),
            "desc": clean_text(j.get("description", "")),
            "url": j.get("url", "") or j.get("shortlink", ""),
            "posted": (j.get("published_on") or "")[:10],
        }


def fetch_workday(token):
    """token format:  tenant|wdN|site   e.g.  metlife|wd5|MetLifeCareers"""
    tenant, wd, site = token.split("|")
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    payload = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "AI intern"}
    r = requests.post(url, headers={**UA, "Content-Type": "application/json"},
                      json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobPostings", []):
        title = j.get("title", "")
        if not (title_has_ai(title) and INTERN_RE.search(title)):
            continue
        desc, posted = "", ""
        try:
            d = _get(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{j['externalPath']}").json()
            info = d.get("jobPostingInfo") or {}
            desc = clean_text(info.get("jobDescription", ""))
            posted = (info.get("startDate") or "")[:10]
        except Exception:
            pass
        yield {
            "title": title,
            "location": j.get("locationsText", ""),
            "desc": desc,
            "url": f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}{j.get('externalPath','')}",
            "posted": posted,
        }


def fetch_oracle(token):
    """token format: host|site  e.g.  ehtl.fa.us6.oraclecloud.com|CX_2"""
    host, site = token.split("|")
    base = f"https://{host}/hcmRestApi/resources/latest"
    url = (f"{base}/recruitingCEJobRequisitions?onlyData=true"
           f"&finder=findReqs;siteNumber={site},keyword=%22AI%22%20intern,limit=50")
    data = _get(url).json()
    items = (data.get("items") or [{}])[0].get("requisitionList", [])
    for j in items:
        title = j.get("Title", "")
        if not (title_has_ai(title) and INTERN_RE.search(title)):
            continue
        desc = ""
        try:
            durl = (f"{base}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                    f"&finder=ById;siteNumber={site},Id=%22{j['Id']}%22")
            dd = _get(durl).json()
            di = (dd.get("items") or [{}])[0]
            desc = clean_text((di.get("ExternalDescriptionStr") or "") + " " +
                              (di.get("ExternalQualificationsStr") or "") + " " +
                              (di.get("ExternalResponsibilitiesStr") or ""))
        except Exception:
            pass
        yield {
            "title": title,
            "location": j.get("PrimaryLocation", ""),
            "desc": desc,
            "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{j.get('Id')}",
            "posted": (j.get("PostedDate") or "")[:10],
        }


def fetch_rippling(token):
    data = _get(f"https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs").json()
    for j in data:
        loc = ""
        wl = j.get("workLocation") or {}
        if isinstance(wl, dict):
            loc = wl.get("label", "")
        if j.get("isRemote"):
            loc = (loc + " Remote").strip()
        yield {
            "title": j.get("name", ""),
            "location": loc,
            "desc": clean_text(j.get("description", "") or ""),
            "url": j.get("url", ""),
            "posted": (j.get("publishedAt") or j.get("createdAt") or "")[:10],
        }


def fetch_breezy(token):
    data = _get(f"https://{token}.breezy.hr/json").json()
    for j in data:
        loc = (j.get("location") or {})
        loc_s = loc.get("name", "") if isinstance(loc, dict) else str(loc)
        yield {
            "title": j.get("name", ""),
            "location": loc_s,
            "desc": clean_text(j.get("description", "") or ""),
            "url": j.get("url", ""),
            "posted": (j.get("published_date") or "")[:10],
        }


def fetch_recruitee(token):
    data = _get(f"https://{token}.recruitee.com/api/offers/").json()
    for j in data.get("offers", []):
        yield {
            "title": j.get("title", ""),
            "location": j.get("location", "") or ("Remote" if j.get("remote") else ""),
            "desc": clean_text((j.get("description") or "") + " " + (j.get("requirements") or "")),
            "url": j.get("careers_url", ""),
            "posted": (j.get("created_at") or "")[:10],
        }


def fetch_amazon(token):
    url = ("https://www.amazon.jobs/en/search.json?result_limit=100&sort=recent"
           "&base_query=AI%20intern&country=USA")
    data = _get(url).json()
    for j in data.get("jobs", []):
        title = j.get("title", "")
        if not (title_has_ai(title) and INTERN_RE.search(title)):
            continue
        yield {
            "title": title,
            "location": j.get("normalized_location", "") or j.get("location", ""),
            "desc": clean_text((j.get("description") or "") + " " +
                               (j.get("basic_qualifications") or "") + " " +
                               (j.get("preferred_qualifications") or "")),
            "url": "https://www.amazon.jobs" + (j.get("job_path") or ""),
            "posted": (j.get("posted_date") or "")[:10],
        }


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "workday": fetch_workday,
    "oracle": fetch_oracle,
    "rippling": fetch_rippling,
    "breezy": fetch_breezy,
    "recruitee": fetch_recruitee,
    "amazon": fetch_amazon,
}

# ---------------------------------------------------------------------------
# 3. PIPELINE
# ---------------------------------------------------------------------------

def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def load_registry():
    """Download the upstream 3,500+ company registry; fall back to the local
    cache if the download fails. Returns list of (name, ats, token)."""
    entries = None
    try:
        r = requests.get(REGISTRY_URL, headers=UA, timeout=30)
        r.raise_for_status()
        entries = r.json()
        UPSTREAM_CACHE.write_text(json.dumps(entries))
    except Exception:
        entries = load_json(UPSTREAM_CACHE, [])
    out = []
    for e in entries or []:
        name, ats, slug = e.get("name", ""), e.get("ats", ""), e.get("slug", "")
        if ats == "workday" and e.get("wd") and e.get("site"):
            out.append((name, "workday", f"{slug}|{e['wd']}|{e['site']}"))
        elif ats == "oracle" and e.get("host") and e.get("site"):
            out.append((name, "oracle", f"{e['host']}|{e['site']}"))
        elif ats in FETCHERS and slug:
            out.append((name, ats, slug))
    return out


def build_company_list():
    """Upstream registry + local companies.csv, de-duplicated. On a name
    collision the upstream entry wins (their tokens are validated)."""
    upstream = load_registry()
    names = {n.lower() for n, _, _ in upstream}
    tokens = {(a, t) for _, a, t in upstream}
    merged = list(upstream)
    for row in csv.DictReader(COMPANIES_CSV.open()):
        name, ats, token = row["company"].strip(), row["ats"].strip(), row["token"].strip()
        if not token or ats not in FETCHERS:
            continue
        if name.lower() in names or (ats, token) in tokens:
            continue
        merged.append((name, ats, token))
    return merged


def fetch_company(name, ats, token):
    """Worker: returns (name, jobs, error)."""
    try:
        return name, list(FETCHERS[ats](token)), None
    except Exception as e:
        return name, [], f"{name} ({ats}): {type(e).__name__}"


def run():
    seen = load_json(SEEN_JSON, {})
    history = load_json(HISTORY_JSON, {})
    history.setdefault("rdu_hybrid", {})
    history.setdefault("remote_us", {})
    companies = build_company_list()
    print(f"registry: {len(companies)} companies to poll")

    accepted, rejected_log, errors = [], [], []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(fetch_company, n, a, t) for n, a, t in companies]
        for fut in as_completed(futures):
            name, jobs, err = fut.result()
            if err:
                errors.append(err)
                continue
            for job in jobs:
                title = job["title"].strip()
                if not title or not INTERN_RE.search(title):
                    continue
                if not title_has_ai(title):
                    continue
                if TITLE_BLOCKLIST.search(title):
                    rejected_log.append((name, title, "technical title"))
                    continue
                cycle = detect_cycle(title, job["desc"])
                if cycle is None:
                    rejected_log.append((name, title, "wrong cycle"))
                    continue
                bad, why = coding_required(job["desc"])
                if bad:
                    rejected_log.append((name, title, why))
                    continue
                bucket = classify_location(job["location"], job["desc"], job.get("is_remote", False))
                if bucket is None:
                    continue
                key = job["url"] or f"{name}|{title}"
                first_seen = seen.get(key, TODAY.strftime("%Y-%m-%d"))
                seen[key] = first_seen
                posted = job["posted"] or first_seen
                accepted.append({
                    "company": name, "title": title, "location": job["location"] or "—",
                    "url": job["url"], "cycle": cycle, "posted": posted,
                    "grad": grad_requirement(job["desc"]),
                    "bucket": bucket,
                })
                scope = "rdu_hybrid" if bucket == "rdu_hybrid" else ("remote_us" if bucket == "remote_us" else None)
                if scope:
                    h = history[scope].setdefault(name, {})
                    if not h.get("observed_first_post") or posted < h["observed_first_post"]:
                        h["observed_first_post"] = posted
                        h["role_hint"] = title
                        h["location"] = job["location"] or h.get("location", "—")
                        h["grad"] = grad_requirement(job["desc"])
                        h["seeded"] = False

    elapsed = time.time() - t0
    ok = len(companies) - len(errors)
    SEEN_JSON.write_text(json.dumps(seen, indent=1, sort_keys=True))
    HISTORY_JSON.write_text(json.dumps(history, indent=1, sort_keys=True))
    write_readme(accepted, history, errors, len(companies), ok, elapsed)
    print(f"done in {elapsed:.0f}s: {len(accepted)} accepted, "
          f"{len(rejected_log)} filtered out, {len(errors)}/{len(companies)} feed errors")
    for r in rejected_log[:40]:
        print("  filtered:", r)


# ---------------------------------------------------------------------------
# 4. README GENERATION
# ---------------------------------------------------------------------------

def md_escape(s):
    return s.replace("|", "\\|")


def fmt_date(d):
    try:
        return datetime.strptime(d[:10], "%Y-%m-%d").strftime("%b %d, %Y")
    except Exception:
        return d or "—"


def active_table(rows):
    if not rows:
        return "_No qualifying postings are live right now. The engine re-checks every 4 hours — watch the Drop Radar below for what's coming._\n"
    rows.sort(key=lambda r: r["posted"], reverse=True)
    out = ["| Company | Role | Location | Cycle | Graduation Requirement | Posted | Apply |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        out.append(
            f"| {md_escape(r['company'])} | {md_escape(r['title'])} | {md_escape(r['location'])} "
            f"| {r['cycle']} | {md_escape(r['grad'])} | {fmt_date(r['posted'])} "
            f"| [Apply]({r['url']}) |"
        )
    return "\n".join(out) + "\n"


def radar_table(hist, live_companies):
    if not hist:
        return "_No history yet — the engine builds this automatically as it observes posting cycles._\n"
    out = ["| Company | Typical Role | Location | Last Cycle's First Post | Expected This Cycle | Status | Graduation Req. |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    items = []
    for comp, h in hist.items():
        base = h.get("observed_first_post") or h.get("seed_first_post", "")
        try:
            dt = datetime.strptime(base, "%Y-%m-%d")
            expected = dt.replace(year=dt.year + 1)
            days = (expected - TODAY.replace(tzinfo=None)).days
            if days < 0:
                exp_s = f"~{expected.strftime('%b %d')} · any day now"
            elif days <= 60:
                exp_s = f"~{expected.strftime('%b %d')} · in ~{days}d"
            else:
                exp_s = f"~{expected.strftime('%b %d')}"
            last_s = dt.strftime("%b %d, %Y")
            sort_key = expected
        except Exception:
            last_s, exp_s, sort_key = "—", "—", datetime.max
        seeded = " *(seeded estimate)*" if h.get("seeded", True) else ""
        status = "✅ live now" if comp in live_companies else "⏳ waiting"
        loc = h.get("location", "—")
        grad = h.get("grad", "shown when posting goes live")
        items.append((sort_key, f"| {md_escape(comp)} | {md_escape(h.get('role_hint','AI intern'))} "
                                f"| {md_escape(loc)} | {last_s}{seeded} | {exp_s} | {status} | {md_escape(grad)} |"))
    items.sort(key=lambda x: x[0])
    out += [line for _, line in items]
    return "\n".join(out) + "\n"


def write_readme(jobs, history, errors, n_companies, n_ok=None, elapsed=None):
    rdu_active = [j for j in jobs if j["bucket"] in ("rdu_hybrid", "rdu_onsite")]
    remote_active = [j for j in jobs if j["bucket"] == "remote_us"]
    live_rdu = {j["company"] for j in jobs if j["bucket"] == "rdu_hybrid"}
    live_remote = {j["company"] for j in remote_active}
    stamp = TODAY.strftime("%b %d, %Y at %H:%M UTC")

    md = f"""# Non-Technical AI Internships — Fall 2026 · Spring 2027 · Summer 2027

![Updates](https://img.shields.io/badge/updates-every%204%20hours-3fb950)
![Cost](https://img.shields.io/badge/cost-%240%20%C2%B7%20no%20API%20keys-2f81f7)
![Scope](https://img.shields.io/badge/scope-non--technical%20AI%20roles-e67e22)

A self-updating engine that tracks **non-technical AI internships** so you don't have to.
Built for business / economics students: **every role's title says "AI"**, and every posting is
screened so that **no coding, programming language, or software-engineering skills are required**.
Data tools (Tableau, Power BI, Alteryx, etc.) only appear when the posting says they're
*preferred / a plus / not required / trained on the job*.

**{len(jobs)} open roles · {n_companies} companies tracked · updated {stamp}**

⭐ **Star this repo** to save it — the tables below rebuild themselves every 4 hours.

## Scope

- **Roles:** AI Strategy, AI Marketing, AI Operations, AI Product, AI Governance & Policy,
  AI Enablement / Adoption, AI Business Analysis, AI Communications — *never* Software
  Engineering, ML, or Data Science.
- **Title rule:** must contain standalone **"AI"** (or "Artificial Intelligence").
- **Cycles:** Fall 2026 · Spring 2027 · Summer 2027 (a `Not stated (verify)` tag means the
  posting didn't name a term — confirm before applying).
- **Regions:** Raleigh–Durham–Chapel Hill (Triangle) and US-Remote.
- **Majors:** postings requiring a Computer Science degree with no business/economics
  alternative are filtered out automatically.

## How to use

- **Posted** = the date the job portal published the role (falls back to the date this
  engine first saw it). Newest on top.
- **Graduation Requirement** is extracted verbatim from each description; `Not specified`
  means the posting doesn't state one.
- Drop Radar rows marked *(seeded estimate)* come from the starter history file, not yet
  from an observed cycle — the engine replaces them with real dates as it watches postings.
- Roles close fast — always confirm on the company site.

---

## 📍 Table 1 — Drop Radar: Hybrid AI Internships in Raleigh–Durham

*When Triangle-area companies are expected to post hybrid non-technical AI internships,
projected from last cycle's first post date. Treat "expected" as the latest point to start
watching — companies trend earlier every year.*

{radar_table(history.get("rdu_hybrid", {}), live_rdu)}

## 📍 Table 2 — Active Now: AI Internships in Raleigh–Durham

{active_table(rdu_active)}

## 🌐 Table 3 — Drop Radar: US-Remote AI Internships

{radar_table(history.get("remote_us", {}), live_remote)}

## 🌐 Table 4 — Active Now: US-Remote AI Internships

{active_table(remote_active)}

---

## How it stays current

A small Python engine reads free public hiring feeds (Greenhouse, Lever, Ashby,
SmartRecruiters, Workable, Workday, Oracle, Rippling, Breezy, Recruitee, Amazon)
directly — **no API keys, no paid services, no LLM calls**. On every run it
first syncs the full **3,500+ company registry** maintained by the
[reference tech-internship engine](https://github.com/zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships)
(so new companies added there appear here automatically), merges in the local
[`companies.csv`](companies.csv) additions (Triangle-area and non-tech employers
the tech list doesn't need), polls every company concurrently, keeps only
non-technical AI internships for the three tracked cycles, records each role's
first-seen date so Drop Radar projections improve over time, and regenerates
this page through GitHub Actions **every 4 hours**.

## What gets filtered out (and why)

| Filter | Rule |
| --- | --- |
| Title | must contain "AI" and "Intern/Co-op"; anything with engineer/developer/software/ML/scientist/technical is dropped |
| Coding | any sentence requiring Python, Java, C++, SQL, R, JavaScript, "programming", "coding", etc. → dropped |
| Data tools | Tableau/Power BI/Alteryx/etc. allowed only with "preferred", "a plus", "not required", or "will train" language |
| Degree | CS-only degree requirements dropped (Econ/Business majors welcome) |
| Cycle | only Fall 2026, Spring 2027, Summer 2027 (or unstated, flagged for you to verify) |

## Run your own copy (2 minutes, $0)

1. Create a new **public** GitHub repo and upload these files (keep the folder layout,
   including `.github/workflows/update.yml`).
2. Repo **Settings → Actions → General → Workflow permissions** → select
   **"Read and write permissions"** → Save.
3. Go to the **Actions** tab → *Update internship tables* → **Run workflow** for the
   first refresh. After that it runs itself every 4 hours.

## Contributing

Adding a company takes one line in [`companies.csv`](companies.csv) — see
[CONTRIBUTING.md](CONTRIBUTING.md).

*Engine (last run): {n_companies:,} companies polled across 11 job platforms{f" · {100*(n_ok or 0)//max(n_companies,1)}% fetch success" if n_ok is not None else ""}{f" · completed in {elapsed:.0f}s" if elapsed else ""}.*
"""
    README.write_text(md)


if __name__ == "__main__":
    sys.exit(run())
