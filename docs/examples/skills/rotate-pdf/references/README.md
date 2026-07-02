# Rotate PDF Reference

Use this reference when the request does not clearly specify pages, direction, or output handling.

## Clarify

- Input PDF path.
- Output PDF path.
- Degrees: usually `90`, `180`, `270`, or `-90`.
- Pages: `all` or 1-based ranges like `1,3-5`.

## Safety

- Do not overwrite the original PDF unless explicitly requested.
- If the user says "rotate left", use `--degrees 270` or `--degrees -90`.
- If the user says "rotate right", use `--degrees 90`.
- If page numbering is ambiguous, assume user-visible 1-based page numbers.

## Dependency

The script uses `pypdf`. If it is missing, report the error and ask the user to install it or approve installing it in their environment.
