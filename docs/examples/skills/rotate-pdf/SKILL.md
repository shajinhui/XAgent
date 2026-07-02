---
name: rotate-pdf
description: Use when the user needs to rotate PDF pages, fix scanned PDF orientation, rotate all pages, or rotate selected pages by 90, 180, or 270 degrees while preserving the original file.
metadata:
  short-description: Rotate PDF pages safely
  icon: pdf
version: 1.0.0
dependencies:
  tools:
    - read_file
    - run_command
policy:
  allow_implicit_invocation: true
---

# Rotate PDF

## Workflow

1. Confirm the input PDF path, output path, rotation degrees, and page range.
2. Prefer `scripts/rotate_pdf.py` for the actual rotation instead of writing new PDF code.
3. Do not overwrite the input PDF unless the user explicitly asks for it.
4. Read `references/README.md` when page ranges, validation, or dependency handling are ambiguous.
5. After running the script, report the output path and any assumptions.

## Command

Use:

```bash
python scripts/rotate_pdf.py input.pdf output.pdf --degrees 90 --pages all
```

Selected pages use 1-based page numbers:

```bash
python scripts/rotate_pdf.py input.pdf output.pdf --degrees 270 --pages 1,3-5
```

## Resources

- `scripts/rotate_pdf.py`: deterministic rotation helper using `pypdf`.
- `references/README.md`: page range and safety guidance.
- `examples/README.md`: example user requests and commands.
- `templates/rotation-request.md`: compact checklist for clarifying ambiguous requests.
