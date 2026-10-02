# Shared repository working instructions

## Repository and synchronization

- The active team repository is `inpilot-dev/hacku-2026`. Work from the actual GitHub clone rather than a separate chat workspace with similar files.
- At the start of each work session and before integration or push, inspect local status, fetch the remote and review incoming commits.
- If the worktree is clean and the current branch can fast-forward, pull with `git pull --ff-only` from its configured upstream. For the current team checkout that is `origin main`.
- Never overwrite uncommitted work, auto-stash, reset, discard changes or force-push to make synchronization succeed. Preserve local changes and inspect/reconcile divergence before continuing.
- Read applicable `AGENTS.md` files and changes to `contracts/` and teammate modules before editing.

## Feature ownership

- Abdullah: agent, draft interpretation and catalog/model integration.
- Timmy: wallet, quotes, policy enforcement and payments.
- Seungbin: formal verification, evaluation and audit/verifier backend.
- Noah: frontend, shared app composition and end-to-end demonstration integration.
- Shared contract changes require coordinated updates. Do not reimplement another owner's financial or verification logic in the frontend.
