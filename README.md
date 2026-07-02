# Bobby's Multi-Company Opportunity Intelligence Agent

A deterministic personal recruiter that reads official company career sources,
normalizes and deduplicates roles, and ranks U.S.-based internships for Bobby
Chen. Full-time, new-grad, uncertain-employment, and non-U.S. roles are excluded
before scoring.

The goal is realistic interview opportunities rather than prestige alone.
Analytics, operations research, data, risk, finance, product analytics, and
accessible SWE internships receive an explicit ease bias.

## Quick start

```bash
PYTHONPATH=src python3 -m jobfinder run
```

The latest GitHub-readable report is written to `reports/latest.md`. When the
source pool contains enough qualifying companies, it selects exactly 15 roles:
five Reach, five Target, and five Safe. A company can appear at most twice. The
first run creates local state in `data/state.json`; subsequent runs mark newly
discovered roles.

Useful commands:

```bash
PYTHONPATH=src python3 -m jobfinder run --dry-run
PYTHONPATH=src python3 -m jobfinder run --fixture tests/fixtures/jobs.json
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

- explicit U.S. location, including U.S.-remote roles
- explicit internship employment or title
- no full-time, new-grad, or ambiguous employment
- no senior-only or irrelevant marketing/sales roles

Eligible roles receive an ease-adjusted score:

- relevance: 30%
- internship clarity: 20%
- competition ease: 20%
- requirement ease: 15%
- U.S. stability: 10%
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

## Discord

Discord is optional and non-fatal; `reports/latest.md` is the primary output.
Set the `DISCORD_WEBHOOK_URL` GitHub repository secret to receive a summary.
Missing or blocked webhooks never prevent report generation.

## Daily automation

`.github/workflows/daily-tracker.yml` runs daily at 09:00 China Standard Time and
can also be triggered manually. It restores seen-role state, writes the report
to the GitHub Actions summary, and uploads reports and state as artifacts.
