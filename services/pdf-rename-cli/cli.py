#!/usr/bin/env python3
"""Managed CLI for PDF preview extraction and plan-based splitting."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter

CASE_ID_RE = re.compile(r"AZCG\d{10,}")


def _write_preview(reader: PdfReader, output_path: Path, chars: int) -> None:
    total = len(reader.pages)
    with output_path.open("w", encoding="utf-8") as f:
        f.write(f"Total pages: {total}\n\n")
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            preview = text[:chars].replace("\n", " | ")
            case_ids = sorted(set(CASE_ID_RE.findall(text)))
            f.write(f"--- Page {index} ---\n")
            if case_ids:
                f.write(f"Case IDs: {', '.join(case_ids)}\n")
            f.write(preview)
            f.write("\n\n")
            print(
                f"[pdf-rename-cli] extracted page {index}/{total}",
                file=sys.stderr,
                flush=True,
            )


def cmd_extract_text(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_path = Path(args.output)

    reader = PdfReader(str(input_path))
    _write_preview(reader, output_path, args.chars)

    payload = {
        "success": True,
        "command": "extract-text",
        "input": str(input_path),
        "output": str(output_path),
        "total_pages": len(reader.pages),
        "chars": args.chars,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _split_one(reader: PdfReader, output_dir: Path, item: dict) -> dict:
    pages = item["pages"]
    filename = item["filename"]
    writer = PdfWriter()
    total_pages = len(reader.pages)
    written_pages: list[int] = []

    for page_num in pages:
        if not isinstance(page_num, int):
            raise ValueError(f"Invalid page number: {page_num!r}")
        if page_num < 1 or page_num > total_pages:
            raise ValueError(f"Page {page_num} out of range 1-{total_pages}")
        writer.add_page(reader.pages[page_num - 1])
        written_pages.append(page_num)

    output_path = output_dir / filename
    with output_path.open("wb") as f:
        writer.write(f)

    return {
        "filename": filename,
        "path": str(output_path),
        "pages": written_pages,
    }


def cmd_split(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan)
    with plan_path.open("r", encoding="utf-8") as f:
        plan = json.load(f)

    input_path = Path(plan["input"])
    output_dir = Path(plan["output_dir"])
    splits = plan["splits"]

    output_dir.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(str(input_path))

    outputs = []
    for index, item in enumerate(splits, start=1):
        result = _split_one(reader, output_dir, item)
        outputs.append(result)
        print(
            f"[pdf-rename-cli] wrote {index}/{len(splits)} -> {result['filename']}",
            file=sys.stderr,
            flush=True,
        )

    payload = {
        "success": True,
        "command": "split",
        "input": str(input_path),
        "output_dir": str(output_dir),
        "files": outputs,
        "total_pages": len(reader.pages),
        "file_count": len(outputs),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def cmd_health(_: argparse.Namespace) -> int:
    payload = {
        "success": True,
        "command": "health",
        "dependency": "pypdf",
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PDF Rename managed CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser(
        "extract-text",
        help="Extract page previews to a text file",
    )
    extract_parser.add_argument("--input", required=True, help="Input PDF path")
    extract_parser.add_argument("--output", required=True, help="Output preview text path")
    extract_parser.add_argument(
        "--chars",
        type=int,
        default=300,
        help="Preview chars per page",
    )
    extract_parser.set_defaults(func=cmd_extract_text)

    split_parser = subparsers.add_parser(
        "split",
        help="Split a PDF according to split_plan.json",
    )
    split_parser.add_argument("--plan", required=True, help="Path to split_plan.json")
    split_parser.set_defaults(func=cmd_split)

    health_parser = subparsers.add_parser("health", help="Service health check")
    health_parser.set_defaults(func=cmd_health)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
