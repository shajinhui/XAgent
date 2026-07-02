#!/usr/bin/env python3
"""按页旋转 PDF 的命令行工具。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:  # pragma: no cover - 由运行环境决定
    PdfReader = None
    PdfWriter = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rotate all or selected PDF pages.")
    parser.add_argument("input_pdf", help="Input PDF path.")
    parser.add_argument("output_pdf", help="Output PDF path.")
    parser.add_argument("--degrees", type=int, required=True, help="Rotation degrees, e.g. 90, 180, 270, -90.")
    parser.add_argument("--pages", default="all", help="1-based page range, e.g. all, 1, 1,3-5.")
    return parser.parse_args()


def normalize_degrees(degrees: int) -> int:
    if degrees % 90 != 0:
        raise ValueError("--degrees must be a multiple of 90")
    return degrees % 360


def parse_page_spec(spec: str, total_pages: int) -> set[int]:
    text = spec.strip().lower()
    if text == "all":
        return set(range(total_pages))
    selected: set[int] = set()
    for chunk in text.split(","):
        part = chunk.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                raise ValueError(f"invalid page range: {part}")
            selected.update(range(start - 1, end))
        else:
            selected.add(int(part) - 1)
    if not selected:
        raise ValueError("--pages selected no pages")
    invalid = [page + 1 for page in selected if page < 0 or page >= total_pages]
    if invalid:
        raise ValueError(f"page out of range: {invalid[0]}")
    return selected


def rotate_pdf(input_pdf: Path, output_pdf: Path, degrees: int, pages: str) -> None:
    if PdfReader is None or PdfWriter is None:
        raise RuntimeError("pypdf is required. Install it with: python -m pip install pypdf")
    if not input_pdf.exists() or not input_pdf.is_file():
        raise FileNotFoundError(f"input PDF not found: {input_pdf}")
    if input_pdf.resolve() == output_pdf.resolve():
        raise ValueError("output PDF must be different from input PDF")

    rotation = normalize_degrees(degrees)
    reader = PdfReader(str(input_pdf))
    writer = PdfWriter()
    selected_pages = parse_page_spec(pages, len(reader.pages))

    for index, page in enumerate(reader.pages):
        if index in selected_pages and rotation:
            page.rotate(rotation)
        writer.add_page(page)

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as handle:
        writer.write(handle)


def main() -> int:
    args = parse_args()
    try:
        rotate_pdf(
            Path(args.input_pdf).expanduser(),
            Path(args.output_pdf).expanduser(),
            args.degrees,
            args.pages,
        )
    except Exception as exc:  # noqa: BLE001 - CLI 需要给出简洁错误
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"rotated PDF written to {args.output_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
