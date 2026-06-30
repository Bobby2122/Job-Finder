# Bobby's Opportunity Intelligence Agent

A small, deterministic personal recruiter for ByteDance roles. It searches the
official ByteDance careers source, filters for Bobby's target geographies and
career stage, scores each role for skill fit, learning value, and accessibility,
and writes a concise daily Markdown report.

The agent is intentionally strict. A high score means the role is worth Bobby's
time, not merely that it contains the words "machine learning."

## Quick start

```bash
PYTHONPATH=src python3 -m jobfinder run
```

The latest report is written to `reports/latest.md`; dated reports are stored
alongside it. The first run creates local state in `data/state.json`. Subsequent
runs mark newly discovered relevant roles.

Useful commands:

```bash
PYTHONPATH=src python3 -m jobfinder run --fixture tests/fixtures/jobs.json
PYTHONPATH=src python3 -m jobfinder run --dry-run
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

No third-party Python packages are required.

## Alerts

Every run prints urgent roles and writes them to the report. In GitHub Actions,
the same report appears in the workflow summary.

### Discord webhook

The daily run posts a Discord summary when at least one recommended role exists.
Urgent roles scoring 8.5 or higher are highlighted first. Add this GitHub Actions
repository secret:

- `DISCORD_WEBHOOK_URL`: the webhook URL for the destination Discord channel

The tracker sends a JSON POST in Discord's standard format:

```json
{
  "content": "Internship Intelligence Report..."
}
```

If the secret is absent, notifications are skipped silently. Request failures
are logged without exposing the webhook URL and never stop the job pipeline.

## Daily automation

`.github/workflows/daily-tracker.yml` runs at 09:00 China Standard Time and can
also be triggered manually. GitHub only schedules workflows present on the
repository's default branch. The workflow restores seen-job state from a rolling
cache and uploads the generated reports and state as an artifact; it does not
auto-apply or submit applications.

## Scoring

Each role receives three 0-10 scores:

- Learning value: 40%
- Skill fit: 35%
- Accessibility: 25%

The rules live in `src/jobfinder/scoring.py` and are deterministic and tested.
They favor ML, experimentation, statistics, optimization, modeling, research
engineering, and substantial data systems. They down-rank senior, PhD-only,
generic BI, marketing, sales, and unrelated roles.

The profile, search vocabulary, geographies, timeline, and thresholds are in
`config/profile.json`, so Phase 2 can add companies without rewriting scoring.

## Data source

The client uses the public careers endpoint called by ByteDance's current
official careers site:

`https://jobs.bytedance.com/api/v1/public/supplier/search/job/posts`

It uses only public job information and applies conservative timeouts, retries,
pagination limits, and deduplication. If ByteDance changes the contract, the run
fails loudly rather than silently producing an empty "all clear" report.
