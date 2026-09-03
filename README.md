# project-ops

A small place for maintaining my GitHub projects from one repository.

I created this because some project-level maintenance does not really belong inside the projects themselves. Things like old deployment URLs, repository metadata, stale links, and other cleanup are easier to manage from one place.

The goal is to keep the tooling simple and safe.

Audits should only report what is wrong. Changes should be explicit and reviewed before they are applied.

## Current work

The first cleanup is removing stale Vercel deployment links left behind after deleting an old Vercel account.

Repositories currently involved:

- `OmprakashSahani/lerobot-state-atlas`
- `OmprakashSahani/codex-benchmark-guardian`

My current portfolio, `OmprakashSahani/Krsna`, has an active deployment and should not be changed by that cleanup.

## Safety

Credentials and tokens do not belong in this repository.

Scripts should use existing authenticated tools such as the GitHub CLI instead of storing secrets in files.
