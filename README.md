# Bobby's Opportunity Intelligence Agent

A small, deterministic personal recruiter system designed for Bobby Chen. It continuously scans internship and new graduate opportunities in Data Science, Machine Learning, and Quantitative Analytics, filters them by learning value and fit, and generates a structured daily report.

The goal is not volume, but quality: only roles that significantly contribute to technical growth and career trajectory are surfaced.

---

## Overview

This system:

- Collects job postings (starting with ByteDance roles)
- Filters by geography (US, China, Europe)
- Focuses on Data Science, ML, and applied quantitative roles
- Scores each role by:
  - Skill fit (math, ML, programming background)
  - Learning value (technical growth potential)
  - Accessibility (likelihood of acceptance)
- Generates a daily Markdown report

---

## User Profile Context

Designed for Bobby Chen:

- Math major, Economics minor (Georgia Tech)
- Strong theoretical foundation (real analysis, probability, linear algebra, differential equations)
- ML research experience (neural network pruning, ODE simulations, experimental ML research)
- Programming: Python, MATLAB, R, Java, SQL
- Goal: Data Science → Machine Learning / Applied Research / Quantitative roles
- Timeline: internships / new grad roles for Jan 2027 – June 2027

---

## Quick Start

```bash
PYTHONPATH=src python3 -m jobfinder run