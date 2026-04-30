---
id: ci-cd
title: CI/CD Integration
sidebar_position: 4
---

# Scenario 4 — CI/CD Integration

A regression catcher you can't run in CI is a regression catcher you
won't run. Wire `agentprdiff check` into the same workflow as your unit
tests.

## GitHub Actions (recommended)

```yaml title=".github/workflows/agents.yml"
name: agent-regression
on: [pull_request]
permissions:
  contents: read   # least-privilege; GHAS flags workflows without this.
jobs:
  agentprdiff:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - env:
          # Match whatever env var your production agent reads.
          OPENAI_API_KEY:    ${{ secrets.OPENAI_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          # Make the semantic-judge mode explicit (don't rely on autodetection).
          AGENTGUARD_JUDGE:  anthropic
        run: |
          agentprdiff check suites/*.py --json-out artifacts/agentprdiff.json
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: agentprdiff, path: artifacts/ }
```

Artifact upload happens on `if: always()` so a failed check still hands you
the JSON to inspect locally. The regression panel printed to the terminal
is preserved in the workflow log.

> If you `--json-out artifacts/...`, add `artifacts/agentprdiff*.json` (or
> the broader `artifacts/`) to your project's `.gitignore`. The workflow
> upload doesn't prevent a contributor from accidentally `git add`ing it
> locally.

## GitLab CI

```yaml title=".gitlab-ci.yml"
agentprdiff:
  image: python:3.11-slim
  stage: test
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  variables:
    AGENTGUARD_JUDGE: "anthropic"
  before_script:
    - pip install -e ".[dev]"
  script:
    - agentprdiff check suites/*.py --json-out artifacts/agentprdiff.json
  artifacts:
    when: always
    paths: [artifacts/]
```

Set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` as masked CI/CD variables in
your project settings.

## CircleCI

```yaml title=".circleci/config.yml"
version: 2.1
jobs:
  agentprdiff:
    docker:
      - image: cimg/python:3.11
    steps:
      - checkout
      - run: pip install -e ".[dev]"
      - run:
          name: agentprdiff check
          command: |
            agentprdiff check suites/*.py \
              --json-out /tmp/agentprdiff.json
      - store_artifacts:
          path: /tmp/agentprdiff.json
workflows:
  version: 2
  pr:
    jobs:
      - agentprdiff:
          context: agent-secrets    # holds OPENAI_API_KEY etc.
```

## Buildkite

```yaml title=".buildkite/pipeline.yml"
steps:
  - label: ":robot_face: agentprdiff"
    command: |
      pip install -e ".[dev]"
      agentprdiff check suites/*.py --json-out artifacts/agentprdiff.json
    artifact_paths: "artifacts/agentprdiff.json"
    env:
      AGENTGUARD_JUDGE: anthropic
```

## What the JSON artifact looks like

```json
{
  "suite": "customer_support",
  "mode": "check",
  "summary": {
    "cases_total": 4,
    "cases_passed": 4,
    "cases_regressed": 0,
    "has_regression": false
  },
  "cases": [
    {
      "suite_name": "customer_support",
      "case_name": "refund_happy_path",
      "trace": { "...": "full Trace JSON" },
      "grader_results": [
        { "passed": true, "grader_name": "contains('refund')", "reason": "..." }
      ],
      "delta": {
        "baseline_exists": true,
        "cost_delta_usd": 0.0,
        "latency_delta_ms": 12.3,
        "tool_sequence_changed": false,
        "output_changed": false,
        "assertion_changes": [...]
      }
    }
  ]
}
```

Stable schema, easy to grep:

```bash
jq '.summary.cases_regressed' artifacts/agentprdiff.json
```

## Conditional skip when secrets are missing

You may want CI to *warn* instead of *fail* when an API key isn't set —
useful in fork PRs where secrets aren't injected:

```yaml
- env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
  run: |
    if [ -z "$OPENAI_API_KEY" ]; then
      echo "::warning::OPENAI_API_KEY missing; skipping agentprdiff check."
      exit 0
    fi
    agentprdiff check suites/*.py --json-out artifacts/agentprdiff.json
```

The scaffolded workflow (`agentprdiff scaffold <name>`) does exactly this
out of the box.

## Pre-commit hook (local)

```yaml title=".pre-commit-config.yaml"
- repo: local
  hooks:
    - id: agentprdiff
      name: agentprdiff
      entry: agentprdiff check suites/*.py
      language: system
      pass_filenames: false
      stages: [pre-push]
```

Stage as `pre-push` rather than `pre-commit` so a single noisy edit doesn't
re-run a heavy suite on every save.

## Updating baselines from a PR

Two reasonable workflows:

1. **Author re-records.** Checkout the branch, run
   `agentprdiff record suites/*.py`, commit the resulting JSON diff under
   `.agentprdiff/baselines/`, push. Reviewers see the trace deltas in the
   normal PR diff.
2. **Bot-driven re-record.** A `/regen-baselines` slash-command on the PR
   triggers a workflow that runs `agentprdiff record`, opens a follow-up
   PR with the updated baselines, and links it from the original PR.
   Useful in larger teams where author re-records get forgotten.

Either way, the PR diff under `.agentprdiff/baselines/` is the review
surface. *Don't* auto-accept new baselines silently — that defeats the
whole point.
