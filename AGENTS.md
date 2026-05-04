# Project Instructions

Read `PROJECT_RULES.md` and `.agent/workflow.md` before starting.
For any change touching the HTTP surface, **read `API_CONTRACT.md` first**
— the contract is the single agreement between this service and any
client (the vision frontend, scripts, tests). Implementation details
under `app/` are not part of the contract and clients do not see them.

## Most-violated rules (summary — full text in the two docs above)

1. **Plan-first is the default.** Skip the plan only when *all four* hold:
   - WHAT is fixed by the user's words (literal value / exact rename
     target / named file location / explicit reference like *"같은
     패턴을 X 모듈처럼"*).
   - HOW is obvious, or the user enumerated the steps in this turn.
   - Blast radius stays local (no other route, no contract change, no
     storage layout change, no module boundary touched).
   - A single `git revert` would unwind it cleanly.

   Otherwise: post a short plan and stop in that turn. No code edits
   before an explicit go-ahead (`ok` / `ㄱㄱ` / `진행`). Silence is not
   consent. Full trigger list lives in `.agent/workflow.md §2`.
   `PROJECT_RULES.md §1`'s "assume and act" applies only to fine-grained
   decisions *inside an approved plan* — it is not a bypass for the
   plan-first rule itself.

2. **The contract is law.**
   - `API_CONTRACT.md` is the only thing the frontend reads. Any change
     to a route's URL, method, request schema, response schema, status
     code, or header is a contract change and requires updating the
     contract in the same commit as the code.
   - Changes to internal modules (`app/storage/*`, `app/normalize.py`,
     `app/segment/*`, …) that don't surface to the contract are
     invisible to clients and don't require contract churn.
   - **Never document frontend implementation details.** This service
     does not know which page in vision uses which endpoint. If a
     comment or spec mentions a vision component name, delete it.

3. **Language scope.**
   - Docs (`__spec__.md`, `__changelog__.md`, `.agent/*.md`,
     `PROJECT_RULES.md`, `CLAUDE.md`, `AGENTS.md`, `README.md`,
     `API_CONTRACT.md`, `REFACTORING.md`, code comments) → **English**.
   - Commit messages, PR/MR titles + bodies, merge commits, review
     replies → **Korean**.
   - No conventional-commit prefixes (`feat:`, `fix:`, `docs:`, etc.).
     If a scope marker is needed, use a bare identifier
     (`storage: …`, `routers: …`).

4. **Commits must be authored as `paikku`.** The sandbox default is
   `Claude <noreply@anthropic.com>`, so every commit must set the four
   env vars inline: `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`,
   `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL`. Exact values are in
   `.agent/workflow.md §6`. Never touch `git config --global`.

5. **No refactoring, abstraction, or "flexibility" beyond the request.**
   See `PROJECT_RULES.md §2`–`§3`. If 200 lines do what 50 would, rewrite.
   Refactor candidates uncovered along the way go to `REFACTORING.md`,
   not the diff in front of you.
