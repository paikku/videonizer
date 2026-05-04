# Agent workflow

The procedure to run during a coding task in this repo. Optimized for **development-phase throughput** — see `PROJECT_RULES.md` for the principle.

The point is to keep the loop tight. Don't let the procedure cost more time than the change it gates.

---

## 1) Orient quickly

Before touching code, locate the area:

- HTTP routing → `app/main.py` (mounts) and `app/routers/<area>.py`
- Persistence → `app/storage/` (per-module: `projects.py`, `resources.py`, `images.py`, `labelsets.py`)
- Video pipeline → `app/normalize.py`, `app/probe.py`, `app/jobs.py`
- Segmentation → `app/segment/{service,registry,polygon}.py` + `app/segment/backends/`
- Export pipeline → `app/export/`
- HTTP surface contract → `API_CONTRACT.md` (root)

If a `__spec__.md` exists in the module you're touching, **skim it** for invariants you'd otherwise re-derive (locking model, filename rules, error mapping). It is not required reading end-to-end — pull the parts you need.

`API_CONTRACT.md` is the contract with clients. If your change touches an HTTP-visible behavior, re-read the relevant section before editing code.

## 2) Plan, then wait for go-ahead

> **🚨 STOP — plan-first is the default. The "skip plan" list is short and narrow. 🚨**
>
> Before any non-trivial change, post a short plan **and stop in that turn**. Do not edit code in the same message you post the plan in. Wait for the user's explicit go-ahead — even a one-word `ok` / `ㄱㄱ` / `진행`. Silence is **not** consent.
>
> If you're unsure whether something qualifies as trivial, it doesn't. Plan it.

**Skip the plan only when *all four* of these hold**:

1. **WHAT is fixed by the user's words** — a literal value, an exact rename target, a named file location, or an explicit reference to copy ("같은 패턴을 X 모듈처럼").
2. **HOW is obvious**, or the user enumerated the steps in this turn.
3. **Blast radius stays local** — no other route, no contract change, no module boundary crossed.
4. **Easy to undo** — a single `git revert` would unwind it cleanly.

**Plan and wait when *any* of these hold**:

1. You'd have to **invent** a value, name, location, library, or default.
2. The request uses interpretive verbs — "정리", "개선", "이상해", "별로", "어떻게 하면 좋을까", "rewrite", "improve".
3. Diagnosis surfaces **more than one fix site**, or you suspect side effects beyond the immediate change.
4. New module, new dependency, new pattern, or a contract addition / change.
5. **Hard to undo** — deletion of a route, file move, contract / schema change, force-push, history rewrite, deletion of stored data.
6. Adjacent finding bigger than a 1–2 line cleanup (separate bug, non-trivial refactor opportunity → `REFACTORING.md`).
7. The user's message is a complaint or impression, not an instruction ("이거 별로네", "음…").

A plan looks like:

```text
1. <step> → verify: <how you'll check it passed>
2. <step> → verify: <...>
```

Hand the plan over and **wait for the user's go-ahead** — don't start editing in the same message you posted the plan in. A 30-second redirect on the plan is dramatically cheaper than rewriting the wrong implementation. If the user only confirms part of the plan, run with the confirmed part and re-confirm the rest.

If the user redirects mid-implementation ("actually try X instead"), treat the redirect as a new task — re-plan if the new direction qualifies above, otherwise just do it. Don't carry stale assumptions forward.

## 3) Implement

- Edit only the files traced to the plan or task. (See `PROJECT_RULES.md` §3.)
- Adjacent cleanup is fine when it's small and local — don't open a separate PR for a typo or a dead import next to your change.
- **Module boundaries are real.** `app/storage/*` is the only module that opens files. `app/routers/*` calls `storage`, never the FS. `app/segment/*` is self-contained. Don't introduce cross-imports that violate this.
- **Contract changes**: update `API_CONTRACT.md` in the *same commit* as the route change. Frontend reads the contract before reading the diff.
- Don't add comments explaining what well-named code already says. Add a comment only when the *why* is non-obvious (a hidden invariant, a workaround, a subtle race).
- **Never reference frontend implementation details.** No vision component names, page names, or click flows in this repo.

## 4) Verify

Run the **Definition of Done** in `.agent/definition-of-done.md`. The gate is intentionally short. Don't pad it.

Mechanical floor:

```bash
pytest -q
```

For changes touching a route or contract behavior, also run a quick `curl` round-trip:

```bash
PORT=8000 STORAGE_ROOT=./tmp_storage python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
curl -s http://127.0.0.1:8000/healthz
# … the specific endpoint you changed …
```

State explicitly which paths you exercised; if you couldn't run the live service yourself, say so — don't claim it works.

## 5) Spec & changelog — only when they earn their keep

Specs and changelogs in this repo are **living docs** for things future agents (and you, in two weeks) will need to re-derive otherwise. They are not paperwork to gate every change.

**Update `__spec__.md` when**:

- A documented behavior, contract, or invariant changed.
- A new error code, header, or route surfaced (and `API_CONTRACT.md` was updated to match).
- The current spec would mislead someone reading it after your change.

**Skip `__spec__.md` for**:

- Refactors that don't change documented behavior.
- Bug fixes that restore the documented behavior — the spec was already correct.

**Update `__changelog__.md` when**:

- Your change shifts a non-obvious *current* design decision in the area, and the rationale would not be obvious from `__spec__.md` or the code alone.
- The current state carries a regression risk worth flagging to the next reader.

**Skip the changelog for**:

- Routine fixes / cleanups / small enhancements with no surprising reasoning.
- Anything already adequately captured by `__spec__.md`.

`__changelog__.md` is **current-state only** — it documents the rationale behind the latest design decisions, not how those decisions evolved. We don't care which date a decision flipped or what the previous version was; we care *why the current shape is the current shape*. When the rationale shifts, **rewrite the relevant entry in place** instead of appending a new dated entry. If an old rationale no longer applies, delete it. No dates, no superseded entries.

Each entry is short:

```markdown
## <one-line policy / decision title>

- **Why**: <one or two sentences capturing the rationale that's not obvious from the spec / code>
- **Risk**: <what could regress in the current implementation; what to watch> (optional)
```

If a `__spec__.md` doesn't exist for a module you're significantly changing the contract of, **create a short scaffold** — purpose, public surface, key invariants, known pitfalls — and stop there. A 30-line spec that covers the load-bearing facts is better than a 300-line spec nobody updates.

`REFACTORING.md` (root) is the place for **deferred** restructuring ideas. Don't smuggle a refactor into a feature commit; record it there for a focused later pass.

## 6) Commit & PR

> **🚨 STOP — read these two rules before every single `git commit` 🚨**
>
> **Language scope (do not mix this up):**
> - **Docs** (`__spec__.md`, `__changelog__.md`, `.agent/*.md`, `PROJECT_RULES.md`, `CLAUDE.md`, `AGENTS.md`, `README.md`, `API_CONTRACT.md`, `REFACTORING.md`, code comments) → **English**.
> - **Commit messages, PR / MR titles + bodies, merge commit messages, review replies** → **Korean**.
>
> 1. **Every commit must be authored as `paikku <jungi_@naver.com>`.** The default env in this sandbox is `Claude <noreply@anthropic.com>`, so a bare `git commit` will be wrong every single time. Set the four env vars inline on every commit:
>
>     ```bash
>     GIT_AUTHOR_NAME="paikku" GIT_AUTHOR_EMAIL="jungi_@naver.com" \
>     GIT_COMMITTER_NAME="paikku" GIT_COMMITTER_EMAIL="jungi_@naver.com" \
>     git commit -m "<제목>" -m "<본문>"
>     ```
>
>     Never touch `git config --global` (see PROJECT_RULES §git safety). The "Verified" badge dropping is expected.
>
> 2. **Commit / PR / MR text is Korean** — subject + body, PR/MR title + body + summary bullets + test checklist + review replies + merge commit messages, all of it.
>
>     - **No English conventional-commit prefixes.** `docs:`, `docs(routers):`, `feat:`, `fix:`, `refactor:`, `chore:` etc. are all banned. Match the existing repo style — Korean subject, no prefix. If you really need a scope marker, use the bare identifier (`storage: …`, `routers: …`).
>     - English identifiers (`JobLimiter`, `ProbeResult`, file paths, library names) embedded in a Korean sentence are fine — don't force awkward translations.
>     - No Claude session links, no `Co-authored-by: Claude`, no "generated by" footers, no agent attribution of any form.
>
> Break either rule → **self-correct immediately** with `--amend` (most recent) or `git filter-branch --env-filter` / `--msg-filter` + `git push --force-with-lease`. Don't wait for the user to flag it.

Other rules:

- **One logical change per commit.** A "unit" is a feature, a bug fix, a refactor, or a doc update. Don't bundle unrelated changes into one commit; don't fragment a single mechanical edit across many. If splitting a small task into multiple commits would force redundant verification cycles for trivial wins, just commit once.
- **Contract + code in the same commit.** If the route changed, `API_CONTRACT.md` changes with it. The frontend cannot tell which commit introduced a regression if the contract lags the code.
- Existing English commits / English PRs stay as they are — only new ones go Korean.
- Reference the relevant `__spec__.md` section when it helps reviewers (e.g. `storage spec §locking`), but it's not required.
- If the change updated `__spec__.md`, include that in the same commit so the spec doesn't lag the code.

---

## Run commands (reference)

```bash
pytest -q                                   # full unit + integration suite
pytest tests/test_storage.py -v             # one module
PORT=8000 STORAGE_ROOT=./tmp \
  python3 -m uvicorn app.main:app --reload  # live dev server
```

The repo has no Makefile and no `npm` / Node toolchain. Tests are the verification floor; live `curl` is the upper layer.
