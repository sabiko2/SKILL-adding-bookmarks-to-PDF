---
name: add-pdf-bookmarks
description: Scan a book PDF's printed table of contents, parse hierarchical entries, reconcile printed or Roman page numbers with physical PDF pages, and write a verified PDF outline whose subtrees are collapsed by default. Use when a user provides a PDF without usable bookmarks, asks to extract a contents page, repair page-number offsets, add chapter bookmarks, or create a navigable book PDF while preserving the original file.
---

# Add PDF Bookmarks

Create bookmarks through a reviewable two-phase workflow. Never overwrite the source PDF.

## Prepare

1. Locate the supplied PDF and this skill directory.
2. If `import pymupdf` fails, run `python -m pip install -r <skill-dir>/scripts/requirements.txt`.
3. Prefer a text-layer PDF. For scanned contents pages, use `--ocr`; this also requires local Tesseract and the requested language data.

## Scan and review

Run:

```bash
python <skill-dir>/scripts/pdf_bookmarks.py scan <book.pdf> --plan <book.bookmarks.json>
```

The scanner searches early pages for contents-like lines, parses title/page pairs, infers hierarchy, and maps printed pages to physical PDF pages. It uses embedded PDF page labels first, then matched heading text and a dominant offset.

Use these options only when automatic scanning is incomplete:

- `--toc-pages 5-12` when the contents-page range is known.
- `--offset 18` when printed page 1 is physical PDF page 19.
- `--ocr --ocr-language eng+chi_sim` for image-only contents pages.
- `--max-scan-pages N` when long front matter places the contents later.

Open the JSON and verify every `entries[].title`, `level`, and `pdf_page`. Treat `pdf_page` as 1-based. Correct the JSON directly when typography defeats the heuristic. Read [review-guide.md](references/review-guide.md) when the plan contains warnings, unresolved entries, Roman-numbered front matter, or unusual hierarchy.

Do not apply a plan with unresolved entries, out-of-range targets, a source hash mismatch, or visibly incorrect offset evidence.

## Write bookmarks

Run:

```bash
python <skill-dir>/scripts/pdf_bookmarks.py apply <book.bookmarks.json> --output <book-bookmarked.pdf>
```

This verifies the source SHA-256, replaces the existing outline, writes the new outline with `collapse=1`, sets the viewer mode to outlines, and saves a separate PDF. `collapse=1` leaves level 1 visible and collapses all subordinate entries. Use `--collapse-level N` only when the user requests a different initial expansion depth.

For a fully automatic run, use `auto`; it writes only when confidence meets the threshold and every entry resolves:

```bash
python <skill-dir>/scripts/pdf_bookmarks.py auto <book.pdf> --output <book-bookmarked.pdf>
```

## Verify and report

Run `python <skill-dir>/scripts/pdf_bookmarks.py verify <book-bookmarked.pdf>`.

Confirm the outline count and targets match the reviewed plan. Report the output path, entry count, detected contents pages, mapping method/offset, warnings, and whether OCR was used. Preserve the JSON beside the result when mapping required manual correction so the decision remains auditable.
