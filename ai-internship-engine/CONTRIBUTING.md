# Contributing

## Where the 3,500+ companies come from

The engine auto-downloads the full company registry from the
[reference tech-internship engine](https://github.com/zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships)
(`data/companies.json`, 3,500+ companies across 11 job platforms) on every run,
so you inherit every company they add — automatically. A cached copy is kept at
`data/upstream_companies.json` as a fallback. `companies.csv` in this repo is
only for **additions** they don't track (Triangle-area and non-tech employers).
On a name collision, the upstream entry wins.


## Add or fix a company (one line)

Open [`companies.csv`](companies.csv) and add a row:

```
Company Name,ats,token,notes
```

Supported `ats` values and where the token comes from:

| ATS | Token | How to find it |
| --- | --- | --- |
| `greenhouse` | board slug | careers URL like `boards.greenhouse.io/<token>` or `job-boards.greenhouse.io/<token>` |
| `lever` | company slug | `jobs.lever.co/<token>` |
| `ashby` | board name | `jobs.ashbyhq.com/<token>` |
| `smartrecruiters` | company id | `jobs.smartrecruiters.com/<token>/...` |
| `workable` | account slug | `apply.workable.com/<token>` |
| `workday` | `tenant\|wdN\|site` | careers URL like `https://<tenant>.<wdN>.myworkdayjobs.com/<site>` → token `tenant\|wdN\|site` |

## Fixing feed errors

The README footer lists any company whose feed failed on the last run —
usually a wrong token or a company that changed job platforms. Find their
current careers URL, update the row, and the next run picks it up.

No API keys are ever needed; all six feeds are public.
