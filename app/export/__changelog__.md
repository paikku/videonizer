# `app/export/` design decisions

## Mulberry32 PRNG was ported byte-for-byte from the TS implementation

- **Why**: The frontend can preview a "what would this dataset look like?" panel before downloading the archive. If preview and dataset don't agree on the assignment, the panel lies. Keeping the same PRNG output for the same seed makes the two sides identical.
- **Risk**: Switching to `random.shuffle` or numpy's `default_rng` would silently break preview parity. Any change to `_mulberry32` needs a coordinated FE change.

## ZIP archive is built fully in memory

- **Why**: `zipfile.ZipFile(io.BytesIO(...))` is the stdlib path that doesn't require a streaming HTTP response or a worker pool. For typical LabelSets (<2k labeled images, <5MB labels + manifest), the archive fits comfortably under 100MB even with `includeImages`. The ZIP is returned as `bytes`, then `Response(content=...)` pumps it.
- **Risk**: A LabelSet with several thousand high-res images in `includeImages` mode can OOM the worker. Tracked in `REFACTORING.md` as a streaming candidate.

## `image_batch` images that have no annotations and aren't excluded are dropped

- **Why**: Production datasets shouldn't carry "I didn't get to it" images. The user is expected to either finish labeling or click `excludedImageIds` (which marks the image as a known-negative for that LabelSet). The `unusable-images` warning in `validate_export` calls this out before the user clicks download.
- **Risk**: A user who expects every member image to land in the archive will be surprised. The validate dry-run is the channel for that surprise; UI should surface it.
