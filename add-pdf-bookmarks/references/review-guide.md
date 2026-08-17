# Bookmark plan review guide

## Plan fields

- `source.sha256`: binds the plan to the exact input PDF.
- `toc_pages`: physical, 1-based PDF pages identified as the printed contents.
- `mapping`: strategy, inferred Arabic/Roman offsets, and confidence.
- `entries[].printed_page`: page token printed in the book.
- `entries[].pdf_page`: physical, 1-based destination written to the PDF.
- `entries[].level`: outline depth; the first entry must be level 1 and depth may increase by at most one between adjacent entries.
- `warnings`: ambiguity or missing evidence requiring review.

## Review rules

1. Compare the first, middle, and last entry against visible chapter headings.
2. Check every numbering transition, especially Roman front matter to Arabic body pages.
3. Keep parts and chapters at level 1 unless the book clearly nests chapters under parts. Keep subsections below parents.
4. Remove headers, footers, running titles, and contents-page numbers mistakenly parsed as entries.
5. Preserve meaningful punctuation and non-Latin titles; remove dot leaders.
6. Ensure `1 <= pdf_page <= source.page_count` for every entry.
7. Apply `pdf_page = printed_page_value + offset`. Offset 18 means printed page 1 targets physical page 19.

## Resolving warnings

- **No contents pages detected**: rerun with `--toc-pages`; add `--ocr` if extracted text is empty.
- **Low mapping confidence**: inspect one known chapter start, calculate its offset, and rerun with `--offset`.
- **Roman entries unresolved**: use embedded page labels when correct; otherwise edit each `pdf_page` after visual inspection.
- **Wrapped titles split across lines**: join the title in JSON and remove the spurious fragment.
- **Wrong hierarchy**: edit `level` values while keeping the first at 1 and preventing jumps larger than one.
- **Hash mismatch**: rescan the actual PDF. Use `--ignore-hash` only after proving page order and content are identical.

## Existing outlines

Applying a plan replaces the existing outline. Before applying, use `verify` on the source. If it already contains useful bookmarks, preserve the original and ask whether replacement is intended.
