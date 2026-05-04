# `app/routers/` design decisions

## Routers raise typed errors, never `HTTPException`

- **Why**: `app/errors.py::ServiceError` (and its subclasses `NotFoundError`, `BadRequestError`, …) carry the contract envelope's `code`. Using FastAPI's `HTTPException(detail=...)` would break the envelope shape and force every client to handle two response formats.
- **Risk**: New error categories need a new `ServiceError` subclass plus the matching code in `API_CONTRACT.md §0`. Don't reach for inline `HTTPException` as a shortcut.

## `RequestValidationError` is rewritten to the contract envelope

- **Why**: FastAPI's default 422 ships `{detail: [...]}`, which violates the contract's "every non-2xx is `{error, message}`" rule. The handler in `main.py` rewrites it. `BadRequestError` (400) keeps the same envelope and is used for semantic input failures discovered after schema validation succeeds.
- **Risk**: The handler swallows the per-field error array; clients only see "request body failed validation". If field-level diagnostics become necessary, the handler can be extended to include a `details` field — but that requires a contract update.

## Export router lives in its own file (`exports.py`), not inside `labelsets.py`

- **Why**: Export pulls the entire `app/export/*` pipeline + does the LabelSet / images / resources / annotations gather. Bundling it into `labelsets.py` would make that file too dense to scan and would mix two responsibilities (CRUD vs. derived dataset emission).
- **Risk**: New export formats expand `exports.py` only; `labelsets.py` stays focused on the data plane.
