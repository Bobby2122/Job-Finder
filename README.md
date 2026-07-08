# Bobby's Multi-Company Opportunity Intelligence Agent

A deterministic personal recruiter that reads official company career sources,
normalizes and deduplicates roles, and ranks U.S.-based internships for Bobby
Chen. Full-time, new-grad, uncertain-employment, and non-U.S. roles are excluded
before scoring.

The goal is realistic interview opportunities rather than prestige alone, with
the current ranking tilted toward AI Engineer / agentic AI internships. LLM,
RAG, AI agents, workflow automation, prompt/tool calling, model evaluation, and
AI-product roles receive the strongest boost. Pure SWE roles are rejected unless
the posting clearly involves applied AI systems.

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
PYTHONPATH=src python3 -m jobfinder tracker
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

No third-party Python packages are required.

## Sources

`config/sources.json` defines company metadata and official ATS adapters:

- ByteDance official careers API
- Greenhouse Job Board API
- Lever Postings API
- Ashby Job Postings API
- Workday public career endpoints

Sources run independently and concurrently. A changed or unavailable company
career page is logged and cannot stop other sources or the report. The config
covers big tech/AI, mid-size tech, startups, insurance/risk, healthcare
analytics, finance/market data, logistics/OR, and research-oriented companies.

## Scoring and buckets

Hard filters run before ranking in this order:

- AI Engineer classifier: AI-focused title, or multiple AI-engineering signals
  plus system-building responsibility language
- explicit U.S. location, including U.S.-remote roles
- explicit internship employment or title
- Spring 2027, Jan-Jun 2027, or Summer 2027 timing; 2026 seasons are blocked
- no full-time, new-grad, or ambiguous employment
- no senior-only, irrelevant marketing/sales, or pure SWE roles without AI scope
- no Applied, Rejected, Not Interested, dismissed, previously recommended, or
  likely duplicate jobs from `data/job_history.json` and `data/manual_jobs.json`

Eligible roles receive an ease-adjusted score:

- AI/agentic relevance: 34%
- internship clarity: 20%
- competition ease: 20%
- requirement ease: 13%
- U.S. stability: 8%
- practical learning value: 5%
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
