# Bobby's Multi-Company Opportunity Intelligence Agent

A deterministic personal recruiter that reads official company career sources,
normalizes and deduplicates roles, and ranks U.S.-based internships for Bobby
Chen. Full-time, new-grad, uncertain-employment, and non-U.S. roles are excluded
before scoring.

The goal is realistic interview opportunities rather than prestige alone, with
ranking tuned for Bobby's mathematics, applied AI, optimization, computational
math, scientific computing, and ML/data-science background. AI Engineer roles
still matter, but they are no longer the only path; OR/optimization, applied
science, numerical modeling, scientific computing, and mathematically serious
data-science internships can rank highly too. Pure SWE roles are penalized
heavily unless the posting clearly involves AI, ML, modeling, optimization, or
research depth.

## Quick start

```bash
PYTHONPATH=src python3 -m jobfinder run
```

The latest GitHub-readable report is written to `reports/latest.md`. When the
source pool contains enough qualifying companies, it can select up to 15 roles:
five Reach, five Target, and five Safe. If quality is low, slots remain empty
instead of being filled with weak, duplicate, non-U.S., or already-dismissed
jobs. A company can appear at most twice. The first run creates local state in
`data/state.json`; subsequent runs mark newly discovered roles.

Useful commands:

```bash
PYTHONPATH=src python3 -m jobfinder run --dry-run
PYTHONPATH=src python3 -m jobfinder run --fixture tests/fixtures/jobs.json
PYTHONPATH=src python3 -m jobfinder validate-sources
PYTHONPATH=src python3 -m jobfinder tracker
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

No third-party Python packages are required.

## Sources

`config/sources.json` defines company metadata and official ATS adapters. Prefer
structured official ATS/API sources over company-specific HTML selectors.

- ByteDance official careers API
- Greenhouse Job Board API
- Lever Postings API
- Ashby Job Postings API
- Workday public career endpoints
- SmartRecruiters public postings API
- Google official careers structured page data
- Apple official careers server-rendered search pages

Sources run independently and concurrently. A changed or unavailable company
career page is logged and cannot stop other sources or the report. The config
covers 120+ crawlable companies across big tech/AI, mid-size tech, startups,
AI infrastructure, applied AI startups, insurance/risk, healthcare analytics,
finance/market data, quant trading, logistics/OR, robotics, defense technology,
energy/climate, industrial simulation, and research-oriented companies.

Every run writes normalized source health to `reports/source_health.json` and a
major-employer coverage table to `reports/source_coverage.md`. Health statuses
are intentionally specific:

- `success`
- `success_no_jobs`
- `partial_success`
- `source_failure`
- `stale_cache`
- `configuration_error`
- `disabled_intentionally`

`success_no_jobs` means the structured source responded successfully but has no
current postings. A `source_failure` is never treated as proof that the company
has no jobs. Successful source results are cached in `data/source_cache.json`;
if a source fails later, jobs from a successful run within the last seven days
can be reused as `stale_cache`. Cached jobs keep the pipeline alive but are
marked as stale and should not be treated as newly verified.

Permanent 400/401/403/404-style configuration failures are not retried
repeatedly; bounded retries with backoff and `Retry-After` support are reserved
for timeouts, 429s, and selected 5xx responses.

### ATS configuration examples

Greenhouse:

```json
{
  "company": "Example Greenhouse Company",
  "careers_url": "https://boards.greenhouse.io/example",
  "source": {
    "type": "greenhouse",
    "board_token": "example"
  }
}
```

Lever:

```json
{
  "company": "Example Lever Company",
  "careers_url": "https://jobs.lever.co/example",
  "source": {
    "type": "lever",
    "site_token": "example"
  }
}
```

Ashby:

```json
{
  "company": "Example Ashby Company",
  "careers_url": "https://jobs.ashbyhq.com/example",
  "source": {
    "type": "ashby",
    "board_name": "example"
  }
}
```

Workday:

```json
{
  "company": "Example Workday Company",
  "careers_url": "https://example.wd5.myworkdayjobs.com/External",
  "source": {
    "type": "workday",
    "tenant": "example",
    "site": "External",
    "workday_host": "example.wd5.myworkdayjobs.com"
  }
}
```

The loader also supports the older flat shape with `adapter`, `ats_type`,
`board_slug`, and `endpoint`. Auto-detection fills missing ATS hints for
`boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`, and
`*.myworkdayjobs.com`, but it does not overwrite manually configured values.

Run source diagnostics without ranking jobs:

```bash
PYTHONPATH=src python3 -m jobfinder validate-sources
```

The diagnostic table includes company, configured and detected source type,
status, jobs returned, endpoint, and configuration/failure category. Workday
tenants vary; keep `page_size`, `max_pages_per_keyword`, `detail_limit`,
`tenant`, `site`, and `workday_host` configurable, and expect some tenants to
return `configuration_error` or `partial_success` if their public CXS endpoint
shape differs.

### Contributor source guide

When adding companies, first look for an official Greenhouse, Lever, Ashby,
Workday, SmartRecruiters, or first-party JSON endpoint. Do not add LinkedIn,
Handshake, login-gated sources, personal cookies, CAPTCHA bypasses, or paid
scraping APIs. Test every new token with `validate-sources`; if it cannot be
verified, leave the company out or mark it as an explicit fallback rather than
guessing.

## Scoring and buckets

Hard filters run before ranking in this order:

- explicit U.S. location, including U.S.-remote roles
- explicit internship employment or title
- no full-time, new-grad, or ambiguous employment
- no Applied, Rejected, Not Interested, or dismissed jobs from
  `data/job_history.json` and `data/manual_jobs.json`

Timing, title phrasing, preferred qualifications, specific programming language
requirements, previous-internship preferences, graduate-degree preferences, and
company popularity are scoring factors rather than early hard filters. Roles
clearly outside the technical/quantitative target path can still be rejected
after scoring, but borderline roles are down-ranked instead of silently removed.
Untouched previous `New` recommendations are cooled down for about 10 days, but
can backfill the report as `PREVIOUSLY RECOMMENDED` when fewer than 15 fresh
roles survive hard filters. `Viewed`, `Started`, and `Saved` are not permanent
suppression states.

Eligible roles receive a career relevance score:

- AI Engineer / Applied AI / LLM / agent relevance: 35%
- Operations research / optimization relevance: 25%
- Applied math / computational math relevance: 20%
- Data science / statistics / analytics relevance: 15%
- Quant finance / risk modeling relevance: 5%
- bonuses for math/statistics/computational science eligibility, research,
  modeling, algorithms, simulation, smaller-company ownership, and career path
  fit

The relevance score is then blended with:

- internship clarity
- competition ease
- requirement ease
- U.S. stability
- practical learning value
- minus large-company/popularity penalties

Selection then enforces a two-role company cap and targets a 5/5/5
large/mid-size/startup mix. Repeated companies are dynamically down-ranked.
PhD-only research is excluded; postings that merely list a PhD alongside an
undergraduate path are not incorrectly rejected.

- Reach: highly competitive research, quant, foundation-model, or deep systems.
- Target: strong applied roles with plausible undergraduate preparation paths.
- Safe: accessible analytics, risk, operations, product analytics, or research
  assistant roles that still compound toward Bobby's goals.

Profile constraints and tie-breakers live in `config/profile.json`.

## Application tracker

Each selected recommendation is persisted in committed JSON files under `data/`:

- `job_history.json` for crawler-discovered recommendations and statuses
- `manual_jobs.json` for manually added applications
- `user_feedback.json` for rejection / not-interested reasons

The older ignored `applications.json` is still read as a compatibility fallback,
but Phase 1 writes the shareable files above so local and GitHub Actions runs can
use the same state. Jobs use a stable hash of normalized company, title,
location, and application URL. URLs drop tracking parameters, and future runs
suppress exact URL repeats, company/title/location matches, similar-title
duplicates, and jobs already shown in previous reports. Start the local tracker
after running the recommendation agent:

```bash
PYTHONPATH=src python3 -m jobfinder run
PYTHONPATH=src python3 -m jobfinder tracker
```

Then open `http://127.0.0.1:8765`. The tracker provides status badges, status
filters, a dedicated Saved view, persistent notes, manual add/update, and reason
fields for Rejected / Not Interested decisions. Supported statuses are New,
Viewed, Saved, Applied, Rejected, and Not Interested. The UI also shows the
recommendation tier, score, employment classification, source,
AI/agentic relevance, matched keywords, reasons, and concerns stored with each
recommendation.

The application button routes through the local tracker and changes only New
to Viewed. It never infers Started or Applied. Those outcomes remain manual.
Applied, Rejected, and Not Interested roles are excluded from later
recommendations. Previously recommended jobs are also excluded from the default
daily push so stale or repeated jobs do not fill empty slots. Pass
`--show-history` to `jobfinder run` to include tracked history.

## Fixture Tests

The offline fixtures exercise the same loader, scorer, selector, report writer,
and tracker persistence used by the CLI:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m jobfinder run --fixture tests/fixtures/e2e_5_5_5_roles.json
```

`tests/fixtures/e2e_5_5_5_roles.json` contains enough realistic roles to select
exactly five Reach, five Target, and five Safe recommendations while proving
U.S.-internship filtering, duplicate suppression, status suppression, Saved-role
behavior, company caps, stable tracker IDs, report counts, and tracker
persistence. The shortage fixtures cover insufficient Reach supply, company-cap
constraints, and low career relevance with counter-based explanations rather
than generic market claims.

Manual entries are respected the same way as crawler-discovered roles. Add a
job as Applied, Rejected, or Not Interested if you handled it elsewhere; future
runs will suppress matching or likely duplicate postings. Rejection reasons such
as "too SWE", "wrong location", "full-time only", "not AI focused", and "not
qualified" are stored in `user_feedback.json` and summarized in the report's
Daily Filtering Report with suggested ranking improvements. These suggestions do
not automatically modify code.

The JSON store survives local restarts and is included in GitHub Actions cache
and artifacts. Local tracker edits must be copied or committed to whatever
environment runs the daily job if the tracker and crawler run on different
machines.

## Discord

Discord is optional and non-fatal; `reports/latest.md` is the primary output.
Set the `DISCORD_WEBHOOK_URL` GitHub repository secret to receive a summary.
Missing or blocked webhooks never prevent report generation.

## Daily automation

`.github/workflows/daily-tracker.yml` runs daily at 09:00 China Standard Time and
can also be triggered manually. It restores seen-role state, writes the report
to the GitHub Actions summary, and uploads reports and state as artifacts.
