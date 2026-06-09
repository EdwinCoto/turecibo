---
description: "Use when writing, editing, or reviewing Azure Functions Python code, HTTP trigger handlers, request validation, or response formatting. Covers v2 programming model patterns, error handling, and security."
applyTo: "**/*.py"
---

# Azure Functions Python v2 — HTTP Triggers

## Function Structure

```python
import azure.functions as func
import logging
import json

app = func.FunctionApp()

@app.route(route="hello", methods=["GET", "POST"])
def hello(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("hello function triggered")
    return func.HttpResponse(
        json.dumps({"message": "Hello"}),
        status_code=200,
        mimetype="application/json",
    )
```

## Request Handling

- Parse JSON body with `req.get_json(silent=True)` — returns `None` on failure, never raises.
- Read query params via `req.params.get("key")`.
- Always validate required inputs and return `400` with a descriptive message if missing.

```python
body = req.get_json(silent=True)
if not body or "name" not in body:
    return func.HttpResponse("Missing 'name'", status_code=400)
```

## Error Handling

- Wrap handler logic in try/except; return `500` with a safe message (no stack traces to client).
- Log the full exception server-side: `logging.exception("Unhandled error")`.

```python
try:
    result = do_work(body)
except Exception:
    logging.exception("Unhandled error in hello")
    return func.HttpResponse("Internal server error", status_code=500)
```

## Security

- Validate `Content-Type: application/json` for POST/PUT when expecting JSON.
- Never echo raw user input back in error messages.
- Use `func.AuthLevel.FUNCTION` (default) or `ANONYMOUS` explicitly — never leave it implicit for public APIs.

```python
@app.route(route="secure", auth_level=func.AuthLevel.FUNCTION)
def secure(req: func.HttpRequest) -> func.HttpResponse:
    ...
```

## Response Helpers

Prefer a shared helper for consistent JSON responses:

```python
def json_response(data: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data),
        status_code=status,
        mimetype="application/json",
    )
```
