# `app/storage/` design decisions

Current rationale for non-obvious storage decisions. Update entries in place when the rationale shifts — no dated history. See `__spec__.md` for behavior contracts.

## Per-path locks resolve to absolute paths

- **Why**: Two callers can reach the same file via different relative paths (e.g. one builds the path off `project_dir(id)`, another off a `Path("./storage/...")` literal in a test fixture). Keying the lock dict by `Path.resolve()` guarantees they serialize on the same lock.
- **Risk**: If a test ever mounts `STORAGE_ROOT` via a symlink that resolves elsewhere, two paths that look identical will get different lock keys. None of the current tests do this.

## `clear_locks()` is a test-only helper

- **Why**: asyncio.Locks bind to whichever event loop first uses them. pytest creates a fresh loop per async test, and locks created in one loop trip `RuntimeError: bound to a different event loop` if reused. The helper is idempotent and only called from fixtures.
- **Risk**: If production code ever calls `clear_locks()` it will reset live mutexes mid-flight, allowing concurrent writes through. The function is not exported through any public route — keep it that way.

## Sub-resource indices are pre-written empty on project creation

- **Why**: `list_resources` / `list_images` / `list_labelsets` for a brand-new project would otherwise trip `FileNotFoundError`. We could also `read_json(path, [])` with a default — and we do — but writing empty stubs makes the on-disk shape predictable for debugging and external tooling.
- **Risk**: A failure mid-`create_project` could leave a partially-initialized directory. The route layer surfaces 500 in that case; the orphan directory is not garbage-collected. Manual cleanup is the operator's job until that becomes a real problem.
