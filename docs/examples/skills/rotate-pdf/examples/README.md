# Examples

## Rotate every page right

User request:

```text
Rotate report.pdf 90 degrees clockwise and save it as report-rotated.pdf.
```

Command:

```bash
python scripts/rotate_pdf.py report.pdf report-rotated.pdf --degrees 90 --pages all
```

## Rotate selected pages left

User request:

```text
Pages 2 through 4 are sideways. Rotate those left.
```

Command:

```bash
python scripts/rotate_pdf.py input.pdf output.pdf --degrees 270 --pages 2-4
```
