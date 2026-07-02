---
name: release-note-drafter
description: Use when drafting concise release notes or change summaries from repository changes, git diffs, issue notes, or implementation summaries.
metadata:
  short-description: Draft release notes from repo changes
  icon: document
version: 1.0.0
dependencies:
  tools:
    - read_file
    - grep
policy:
  allow_implicit_invocation: true
---

# Release Note Drafter

## Workflow

1. Identify the user-facing changes, fixes, and risks.
2. Read `references/README.md` when wording style or section rules matter.
3. Use `templates/release-note.md` when the user asks for a structured artifact.
4. Keep internal implementation detail out unless it changes user behavior.

## Resources

- `references/README.md`: release note style and filtering rules.
- `examples/README.md`: sample inputs and outputs.
- `templates/release-note.md`: reusable release note layout.
