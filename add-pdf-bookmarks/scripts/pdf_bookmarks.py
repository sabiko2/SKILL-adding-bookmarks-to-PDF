#!/usr/bin/env python3
"""Scan printed PDF contents and write a reviewable, collapsed outline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
TOC_WORDS = (
    "table of contents", "contents", "目录", "目次", "inhalt",
    "inhaltsverzeichnis", "sommaire", "indice", "índice",
)
PAGE_TOKEN = r"(?:\d{1,5}|[ivxlcdmIVXLCDM]{1,12})"
ENTRY_RE = re.compile(
    rf"^(?P<title>.+?)(?P<sep>\s*[.·•…_\-]{{2,}}\s*|\s{{2,}}|\s+)"
    rf"(?P<page>{PAGE_TOKEN})\s*$"
)
NUMBERED_RE = re.compile(
    r"^\s*(?:chapter\s+|chapitre\s+|第\s*)?(\d+(?:\.\d+)*)[.、\s]", re.I
)


@dataclass
class Line:
    text: str
    x: float = 0.0


def die(message: str, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def require_pymupdf():
    try:
        import pymupdf  # type: ignore
    except ImportError:
        die("PyMuPDF is required; run: python -m pip install -r scripts/requirements.txt")
    return pymupdf


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def roman_to_int(value: str) -> int | None:
    value = value.upper()
    if not value or not re.fullmatch(r"[IVXLCDM]+", value):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total, previous = 0, 0
    for char in reversed(value):
        current = values[char]
        total += -current if current < previous else current
        previous = max(previous, current)
    return total if total > 0 else None


def page_value(token: str) -> tuple[int | None, str]:
    if token.isdigit():
        return int(token), "arabic"
    return roman_to_int(token), "roman"


def normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in text if char.isalnum())


def search_title(title: str) -> str:
    title = re.sub(
        r"^\s*(?:part|chapter|section|chapitre|第[一二三四五六七八九十百]+[章节部篇])\s*",
        "", title, flags=re.I,
    )
    title = re.sub(r"^\s*\d+(?:\.\d+)*[.、:\-\s]+", "", title)
    return normalized(title)


def open_document(path: Path, password: str | None):
    pymupdf = require_pymupdf()
    try:
        document = pymupdf.open(path)
    except Exception as exc:
        die(f"cannot open PDF: {exc}")
    if document.needs_pass and not (password and document.authenticate(password)):
        document.close()
        die("PDF is encrypted; supply the correct --password")
    if not document.is_pdf:
        document.close()
        die("input is not a PDF")
    return document


def extract_lines(page: Any, ocr: bool, ocr_language: str) -> list[Line]:
    def from_dict(data: dict[str, Any]) -> list[Line]:
        result: list[Line] = []
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for item in block.get("lines", []):
                text = "".join(span.get("text", "") for span in item.get("spans", [])).strip()
                if text:
                    result.append(Line(text, float(item.get("bbox", [0])[0])))
        return result

    lines = from_dict(page.get_text("dict", sort=True))
    if ocr and sum(len(line.text) for line in lines) < 20:
        try:
            textpage = page.get_textpage_ocr(language=ocr_language, full=True)
            text = page.get_text("text", textpage=textpage, sort=True)
            lines = [Line(item.strip()) for item in text.splitlines() if item.strip()]
        except Exception as exc:
            die(f"OCR failed on PDF page {page.number + 1}: {exc}")
    return lines


def infer_level(title: str, x: float, base_x: float) -> int:
    match = NUMBERED_RE.match(title)
    if match:
        return min(6, match.group(1).count(".") + 1)
    if re.match(r"^\s*(part|book|chapter|篇|部|卷|第.+章)\b", title, re.I):
        return 1
    return min(6, max(0, round((x - base_x) / 18.0)) + 1)


def parse_lines(lines: list[Line]) -> list[dict[str, Any]]:
    candidates: list[tuple[Line, re.Match[str]]] = []
    toc_names = {normalized(word) for word in TOC_WORDS}
    for line in lines:
        match = ENTRY_RE.match(line.text.strip())
        if not match:
            continue
        title = re.sub(r"[.·•…_\-\s]+$", "", match.group("title")).strip()
        token = match.group("page")
        value, _ = page_value(token)
        separator = match.group("sep")
        has_leader = bool(re.search(r"[.·•…_\-]{2,}", separator))
        structured = bool(
            NUMBERED_RE.match(title)
            or re.match(r"^\s*(part|book|chapter|section|篇|部|卷|第.+[章节])", title, re.I)
        )
        if value is None or len(normalized(title)) < 2:
            continue
        if not has_leader and not structured and len(title) < 4:
            continue
        if normalized(title) in toc_names:
            continue
        candidates.append((line, match))
    if not candidates:
        return []
    base_x = min(line.x for line, _ in candidates)
    entries: list[dict[str, Any]] = []
    previous_level = 1
    for line, match in candidates:
        title = re.sub(r"[.·•…_\-\s]+$", "", match.group("title")).strip()
        token = match.group("page")
        value, numbering = page_value(token)
        level = infer_level(title, line.x, base_x)
        level = 1 if not entries else min(level, previous_level + 1)
        previous_level = level
        entries.append({
            "level": level,
            "title": title,
            "printed_page": token,
            "printed_page_value": value,
            "numbering": numbering,
        })
    entries[0]["level"] = 1
    return entries


def parse_page_range(spec: str, page_count: int) -> list[int]:
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(part)
        if start < 1 or end < start or end > page_count:
            die(f"invalid --toc-pages range: {part}")
        pages.update(range(start, end + 1))
    if not pages:
        die("--toc-pages is empty")
    return sorted(pages)


def detect_toc_pages(page_lines: list[list[Line]]) -> list[int]:
    counts = [len(parse_lines(lines)) for lines in page_lines]
    keywords = [
        any(word in " ".join(line.text for line in lines).casefold() for word in TOC_WORDS)
        for lines in page_lines
    ]
    seeds = [i for i, count in enumerate(counts) if count >= 3 or (keywords[i] and count >= 1)]
    if not seeds:
        return []
    groups: list[list[int]] = []
    for index in seeds:
        if groups and index <= groups[-1][-1] + 1:
            groups[-1].append(index)
        else:
            groups.append([index])
    best = max(
        groups,
        key=lambda group: sum(counts[i] for i in group) + 5 * sum(keywords[i] for i in group),
    )
    start, end = best[0], best[-1]
    while start > 0 and counts[start - 1] >= 1:
        start -= 1
    while end + 1 < len(counts) and counts[end + 1] >= 1:
        end += 1
    return list(range(start, end + 1))


def page_heading_text(lines: list[Line]) -> list[str]:
    return [line.text.strip() for line in lines if line.text.strip()][:24]


def best_title_page(
    title: str, headings: list[list[str]], excluded: set[int]
) -> tuple[int | None, float]:
    key = search_title(title)
    if len(key) < 4:
        return None, 0.0
    best_page, best_score = None, 0.0
    for index, lines in enumerate(headings):
        if index in excluded:
            continue
        for line in lines:
            candidate = normalized(line)
            if not candidate:
                continue
            if key in candidate or (candidate in key and len(candidate) >= 6):
                score = 0.98 if key == candidate else 0.92
            else:
                score = SequenceMatcher(None, key, candidate).ratio()
            if score > best_score:
                best_page, best_score = index + 1, score
    return (best_page, best_score) if best_score >= 0.74 else (None, best_score)


def dominant_offset(votes: list[tuple[int, float]]) -> tuple[int | None, float]:
    if not votes:
        return None, 0.0
    weights: Counter[int] = Counter()
    for offset, weight in votes:
        weights[offset] += weight
    offset, support = weights.most_common(1)[0]
    return offset, round(support / sum(weights.values()), 3)


def map_entries(
    document: Any,
    entries: list[dict[str, Any]],
    headings: list[list[str]],
    toc_pages: list[int],
    explicit_offset: int | None,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    excluded = {page - 1 for page in toc_pages}
    has_labels = bool(document.get_page_labels())
    votes: dict[str, list[tuple[int, float]]] = {"arabic": [], "roman": []}
    direct: list[tuple[int | None, float]] = []

    for entry in entries:
        label_pages = document.get_page_numbers(str(entry["printed_page"])) if has_labels else []
        label_pages = [page for page in label_pages if page not in excluded]
        if len(label_pages) == 1:
            actual, score = label_pages[0] + 1, 1.0
            entry["mapping_evidence"] = "page_label"
        else:
            actual, score = best_title_page(entry["title"], headings, excluded)
            if actual:
                entry["mapping_evidence"] = f"title_match:{score:.2f}"
        direct.append((actual, score))
        if actual and entry["printed_page_value"] is not None:
            votes[entry["numbering"]].append(
                (actual - entry["printed_page_value"], score)
            )

    offsets: dict[str, int | None] = {}
    confidences: dict[str, float] = {}
    for numbering in ("arabic", "roman"):
        if explicit_offset is not None and numbering == "arabic":
            offsets[numbering], confidences[numbering] = explicit_offset, 1.0
        else:
            offsets[numbering], confidences[numbering] = dominant_offset(votes[numbering])

    unresolved = 0
    for entry, (actual, score) in zip(entries, direct):
        value = entry["printed_page_value"]
        offset = offsets[entry["numbering"]]
        mapped = value + offset if value is not None and offset is not None else actual
        if mapped is None or not (1 <= mapped <= document.page_count):
            entry["pdf_page"] = None
            unresolved += 1
        else:
            entry["pdf_page"] = mapped
            if "mapping_evidence" not in entry:
                entry["mapping_evidence"] = f"{entry['numbering']}_offset:{offset:+d}"
            if actual and abs(actual - mapped) > 1 and score >= 0.85:
                warnings.append(
                    f"title evidence disagrees for {entry['title']!r}: offset->{mapped}, match->{actual}"
                )

    used_confidences = [
        confidences[entry["numbering"]]
        for entry in entries
        if offsets[entry["numbering"]] is not None
    ]
    confidence = (
        round(sum(used_confidences) / len(used_confidences), 3)
        if used_confidences else 0.0
    )
    if unresolved:
        warnings.append(f"{unresolved} entries have no valid physical PDF page")
    if confidence < 0.7:
        warnings.append(
            f"mapping confidence is low ({confidence:.3f}); verify or provide --offset"
        )
    method = (
        "explicit_offset" if explicit_offset is not None
        else "page_labels_and_title_offset" if has_labels
        else "title_offset"
    )
    return {
        "method": method,
        "confidence": confidence,
        "arabic_offset": offsets["arabic"],
        "roman_offset": offsets["roman"],
        "unresolved": unresolved,
    }, warnings


def scan_pdf(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    source = Path(args.pdf).resolve()
    if not source.is_file():
        die(f"input PDF does not exist: {source}")
    document = open_document(source, args.password)
    try:
        limit = min(document.page_count, args.max_scan_pages)
        early_lines = [
            extract_lines(document[i], args.ocr, args.ocr_language) for i in range(limit)
        ]
        if args.toc_pages:
            toc_pages = parse_page_range(args.toc_pages, document.page_count)
        else:
            toc_pages = [index + 1 for index in detect_toc_pages(early_lines)]
        if not toc_pages:
            die("no contents pages detected; specify --toc-pages or use --ocr")
        all_lines = early_lines + [
            extract_lines(document[i], args.ocr, args.ocr_language)
            for i in range(limit, document.page_count)
        ]
        entries: list[dict[str, Any]] = []
        for page_number in toc_pages:
            entries.extend(parse_lines(all_lines[page_number - 1]))
        if not entries:
            die("contents pages produced no title/page entries")
        entries[0]["level"] = 1
        headings = [page_heading_text(lines) for lines in all_lines]
        mapping, warnings = map_entries(
            document, entries, headings, toc_pages, args.offset
        )
        existing = document.get_toc()
        if existing:
            warnings.append(
                f"source already has {len(existing)} bookmarks; apply will replace them"
            )
        plan = {
            "schema_version": SCHEMA_VERSION,
            "source": {
                "path": str(source),
                "sha256": sha256(source),
                "page_count": document.page_count,
            },
            "toc_pages": toc_pages,
            "ocr_used": bool(args.ocr),
            "mapping": mapping,
            "entries": entries,
            "warnings": warnings,
        }
    finally:
        if not document.is_closed:
            document.close()
    plan_path = (
        Path(args.plan).resolve()
        if args.plan else source.with_suffix(".bookmarks.json")
    )
    if plan_path.exists() and not args.force:
        die(f"plan already exists: {plan_path}; use --force to replace it")
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"plan: {plan_path}")
    print(f"contents pages: {', '.join(map(str, toc_pages))}")
    print(
        f"entries: {len(entries)}; confidence: {mapping['confidence']:.3f}; "
        f"unresolved: {mapping['unresolved']}"
    )
    for warning in warnings:
        print(f"warning: {warning}")
    return plan, plan_path


def validate_plan(plan: dict[str, Any], document: Any) -> list[list[Any]]:
    if plan.get("schema_version") != SCHEMA_VERSION:
        die(f"unsupported plan schema: {plan.get('schema_version')}")
    entries = plan.get("entries")
    if not isinstance(entries, list) or not entries:
        die("plan has no entries")
    toc: list[list[Any]] = []
    previous = 1
    for index, entry in enumerate(entries):
        level = entry.get("level")
        title = str(entry.get("title", "")).strip()
        page = entry.get("pdf_page")
        if not isinstance(level, int) or level < 1:
            die(f"entry {index + 1} has invalid level")
        if (index == 0 and level != 1) or (index and level > previous + 1):
            die(f"entry {index + 1} has an invalid hierarchy jump")
        if not title:
            die(f"entry {index + 1} has an empty title")
        if not isinstance(page, int) or not (1 <= page <= document.page_count):
            die(f"entry {index + 1} has unresolved or invalid pdf_page")
        toc.append([level, title, page])
        previous = level
    return toc


def apply_plan(args: argparse.Namespace) -> Path:
    plan_path = Path(args.plan).resolve()
    if not plan_path.is_file():
        die(f"plan does not exist: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read plan: {exc}")
    source = Path(args.pdf or plan.get("source", {}).get("path", "")).resolve()
    if not source.is_file():
        die(f"source PDF does not exist: {source}")
    expected_hash = plan.get("source", {}).get("sha256")
    if expected_hash and not args.ignore_hash and sha256(source) != expected_hash:
        die("source SHA-256 differs from the reviewed plan; rescan or use --ignore-hash")
    output = (
        Path(args.output).resolve()
        if args.output else source.with_name(f"{source.stem}-bookmarked.pdf")
    )
    if output == source:
        die("refusing to overwrite the source PDF")
    if output.exists() and not args.force:
        die(f"output already exists: {output}; use --force to replace it")
    if args.collapse_level < 1:
        die("--collapse-level must be at least 1")
    document = open_document(source, args.password)
    try:
        toc = validate_plan(plan, document)
        document.set_toc(toc, collapse=args.collapse_level)
        document.set_pagemode("UseOutlines")
        document.save(output, garbage=3, deflate=True)
    except Exception as exc:
        if output.exists():
            output.unlink()
        die(f"could not write bookmarks: {exc}")
    finally:
        if not document.is_closed:
            document.close()
    print(f"output: {output}")
    print(
        f"bookmarks written: {len(toc)}; collapsed below level: {args.collapse_level}"
    )
    return output


def verify_pdf(args: argparse.Namespace) -> None:
    source = Path(args.pdf).resolve()
    document = open_document(source, args.password)
    try:
        toc = document.get_toc()
        print(f"pdf: {source}")
        print(f"pages: {document.page_count}; bookmarks: {len(toc)}")
        for level, title, page, *_ in toc:
            status = "ok" if 1 <= page <= document.page_count else "INVALID"
            print(f"{'  ' * (level - 1)}- {title} -> {page} [{status}]")
    finally:
        document.close()


def add_scan_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("pdf")
    parser.add_argument("--plan")
    parser.add_argument("--toc-pages", help="1-based pages, e.g. 5-9,11")
    parser.add_argument("--max-scan-pages", type=int, default=80)
    parser.add_argument(
        "--offset", type=int,
        help="physical PDF page minus printed Arabic page",
    )
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--ocr-language", default="eng")
    parser.add_argument("--password")
    parser.add_argument("--force", action="store_true")


def add_apply_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("plan")
    parser.add_argument("--pdf", help="override source path stored in plan")
    parser.add_argument("--output")
    parser.add_argument("--collapse-level", type=int, default=1)
    parser.add_argument("--ignore-hash", action="store_true")
    parser.add_argument("--password")
    parser.add_argument("--force", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="scan contents and write a review plan")
    add_scan_options(scan)
    apply = commands.add_parser("apply", help="write bookmarks from a reviewed plan")
    add_apply_options(apply)
    verify = commands.add_parser("verify", help="inspect an existing PDF outline")
    verify.add_argument("pdf")
    verify.add_argument("--password")
    auto = commands.add_parser(
        "auto", help="scan and apply only a high-confidence plan"
    )
    add_scan_options(auto)
    auto.add_argument("--output")
    auto.add_argument("--collapse-level", type=int, default=1)
    auto.add_argument("--min-confidence", type=float, default=0.8)
    return parser


def main(argv: Iterable[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "scan":
        scan_pdf(args)
    elif args.command == "apply":
        apply_plan(args)
    elif args.command == "verify":
        verify_pdf(args)
    else:
        plan, plan_path = scan_pdf(args)
        if (
            plan["mapping"]["unresolved"]
            or plan["mapping"]["confidence"] < args.min_confidence
        ):
            die(f"automatic apply refused; review plan: {plan_path}")
        apply_args = argparse.Namespace(
            plan=str(plan_path),
            pdf=None,
            output=args.output,
            collapse_level=args.collapse_level,
            ignore_hash=False,
            password=args.password,
            force=args.force,
        )
        apply_plan(apply_args)


if __name__ == "__main__":
    main()
