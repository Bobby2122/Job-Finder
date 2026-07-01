# Bobby's Multi-Company Opportunity Intelligence Agent

A deterministic personal recruiter that reads official company career sources,
normalizes and deduplicates roles, and ranks opportunities for Bobby Chen's
January-June 2027 window. China-based roles are currently excluded.

The goal is quality rather than application volume. Company brand is a small
factor; technical learning, timing, fit, and realistic access matter more.

## Quick start

```bash
PYTHONPATH=src python3 -m jobfinder run
```

The latest GitHub-readable report is written to `reports/latest.md`. It selects
up to 15 roles: five Reach, five Target, and five Safe. The first run creates
local state in `data/state.json`; subsequent runs mark newly discovered roles.

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

Roles receive 0-10 scores for:

- Skill fit: 30%
- Learning value: 25%
- Accessibility: 20%
- Timing fit: 15%
- Location fit: 5%
- Company/career value: 5%

The rules favor Python, ML, experimentation, statistics, optimization,
forecasting, risk, modeling, research, and data infrastructure. Senior,
PhD-only, 2026-only, marketing/sales, and China-based roles cannot enter the
selected buckets.

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
