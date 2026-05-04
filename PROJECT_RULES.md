# Project Rules

Global behavioral rules for any agent (Claude, Codex, Cursor, human, etc.) working in this repo.

> **We are in development phase, not production.** These rules optimize for **throughput** — fast iteration, fast feedback, fast course-correction. They are not the rules a production-bound team would write. When this service ships to real users, revisit them.

For the work procedure, see `.agent/workflow.md`. For the bar that defines "done," see `.agent/definition-of-done.md`. For module-level living requirements, see the nearest `__spec__.md` under `app/`. **For HTTP surface changes — request/response shape, status codes, error envelope — read `API_CONTRACT.md` first.**

---

## 0) Backend ↔ frontend isolation

`API_CONTRACT.md` is the *only* document shared with any client. Everything below is internal to videonizer.

- Internal docs (`__spec__.md`, `__changelog__.md`, code comments) describe **how** the service satisfies the contract — locking model, file layout, ffmpeg flags, model registry. Clients never read them.
- Internal docs **do not name client components, pages, or behaviors**. If a refactor invalidates a frontend assumption, the contract is the channel for that conversation, not a comment in this repo.
- The reverse holds in the frontend repo: it does not document our locking model, our subprocess strategy, or our `STORAGE_ROOT` layout. It only knows the contract.

If a contract change is required to fix a bug, *update the contract first*, get the frontend onto the new version, then change the code. Don't ship code that diverges from the contract — even briefly — and call it forward-compatible.

### Contract mirror

`API_CONTRACT.md` exists in **both** repos: `videonizer/API_CONTRACT.md` (canonical) and `vision/API_CONTRACT.md` (mirror). They must stay byte-identical. The split-copy layout is intentional — the frontend developer reads the contract from inside their own repo without crossing repos.

Sync rule:

1. **Edit the canonical copy first** (`videonizer/API_CONTRACT.md`) in the same commit as the route change.
2. **Mirror the new file verbatim** to `vision/API_CONTRACT.md` in a paired commit on the matching branch in vision. `cp ../videonizer/API_CONTRACT.md ./API_CONTRACT.md` is the expected one-liner.
3. The two commits land together — frontend code that depends on the new contract field cannot land before the mirror is in place, otherwise its CI typecheck has nothing to read.

Drift is a bug. CI in either repo may diff the two and fail; if `diff -q` ever surfaces a difference, fix in the same PR.

---

## 1) Reasonable assumptions over interrogation

> **Scope**: this rule covers **fine-grained decisions inside an already-approved task** — naming a local helper, picking between two equivalent implementations, choosing a default value. It does **not** override `.agent/workflow.md §2`'s plan-first requirement. Whether to plan before starting is decided by §2's checklist; how to settle a small detail mid-implementation is decided here.

The user is iterating fast. Stopping to ask about every micro-ambiguity inside a confirmed task kills the loop.

**Default (inside an approved task)**: pick the most reasonable interpretation, do the work, and report what you assumed in one line. The user can redirect if you guessed wrong — that round-trip is cheaper than asking up front.

**Even inside an approved task, stop and ask when**:

- The action is **irreversible or destructive** (deleting `STORAGE_ROOT` data, dropping a route from the contract, force-pushing).
- The decision is **architectural** (introducing a new module, swapping the persistence strategy, picking a new dependency, breaking a boundary).
- Two interpretations would produce **dramatically different work** (e.g. "rewrite this service" vs "tweak this handler").

For everything else: assume, do, report. Surface a simpler approach if you see one — but bias toward implementing the user's request first and noting the alternative, not blocking on it.

## 2) Simplicity is non-negotiable

This is the rule that does **not** loosen in development mode. Short code is the cheapest code to throw away.

- Build only what was asked. Nothing speculative.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested. The 99% case ships first; configurability only when the 99% breaks.
- No error handling for impossible scenarios. The validation boundary is where untrusted bytes enter the process — request bodies (Pydantic / form parsers), filesystem reads of legacy data, subprocess output. Internal calls between modules trust each other.
- If you wrote 200 lines and 50 would do, rewrite it.

Throughput depends on this. Every speculative abstraction is a future tax — and in development mode where the design is changing weekly, that tax is paid in full and often.

Sanity check: would a senior engineer call this over-engineered? If yes, simplify.

## 3) Focused changes, not surgical ones

The change should do its job and leave the area no worse than before. That's a softer bar than "every line traces to the request" — adjacent cleanup is fine when it's local and obvious.

- The **main change** must trace to the user's request. Don't smuggle in unrelated features.
- **Adjacent cleanup is allowed and encouraged** when it's small and lives next to your change: tidy a comment, drop a now-orphan import, fix a typo, delete dead code you bumped into. Mention it in the report; don't open a separate PR.
- **Don't refactor things that aren't broken** unless that refactor *is* the task. A working pattern in another module is not your concern this session. Add it to `REFACTORING.md` instead.
- **Match the existing style** when adding to existing code, even if you'd write it differently. Mixed styles within a file are worse than either style alone.
- **Respect module boundaries.** `app/storage/*` does not import HTTP types. `app/routers/*` does not touch the filesystem directly — it goes through `storage`. `app/segment/*` is self-contained. `app/normalize.py` does not pull in `app/storage`. Don't create cross-cutting helpers; if you need shared logic, lift it to its own module.

The diff should read as one coherent change a reviewer can absorb in one pass — not a maximally-minimal patch, not a junk drawer.

## 4) Verify enough to keep the loop moving

Verification scales with the size and risk of the change. Don't over-verify trivial work; don't under-verify risky work.

**Minimum gate, every change**:

- `pytest -q` is green (or only fails on pre-existing issues unrelated to your change — say so).
- For changes touching a route or contract: a `curl` against the live `uvicorn` confirming the contract still matches `API_CONTRACT.md`. Note which paths you exercised in the report.

**Add to the gate when the change warrants it**:

- Multi-step refactor / module-boundary change → also walk one error path on top of the happy path.
- Touches `app/storage/*` → confirm a round-trip against a temp `STORAGE_ROOT`, not just the unit tests.
- Touches `app/normalize.py` or `app/segment/*` → run the live route once with a real fixture (decode-required AVI for normalize, real JPEG for segment).

If you couldn't run the live service yourself (no `ffmpeg` in sandbox, no model weights, …), say so explicitly. Don't claim "it works" when you only ran the unit tests.

---

> The goal is not zero risk; it's high throughput with manageable risk. Catch real regressions, not hypothetical ones. When in doubt, do the work, report what you did, and let the user redirect — that loop is faster than the loop where you stop and ask.
