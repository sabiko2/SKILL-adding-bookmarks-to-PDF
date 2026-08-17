import importlib.util
import argparse
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("pdf_bookmarks.py")
SPEC = importlib.util.spec_from_file_location("pdf_bookmarks", MODULE_PATH)
assert SPEC and SPEC.loader
pdf_bookmarks = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pdf_bookmarks
SPEC.loader.exec_module(pdf_bookmarks)


class ParserTests(unittest.TestCase):
    def test_roman_conversion(self):
        self.assertEqual(pdf_bookmarks.roman_to_int("xiv"), 14)
        self.assertIsNone(pdf_bookmarks.roman_to_int("hello"))

    def test_parses_hierarchy_and_page_tokens(self):
        lines = [
            pdf_bookmarks.Line("1 Introduction ........ 1", 10),
            pdf_bookmarks.Line("1.1 Background ........ 3", 28),
            pdf_bookmarks.Line("Appendix ........ xiv", 10),
        ]
        entries = pdf_bookmarks.parse_lines(lines)
        self.assertEqual([entry["level"] for entry in entries], [1, 2, 1])
        self.assertEqual(entries[2]["printed_page_value"], 14)

    def test_page_range(self):
        self.assertEqual(
            pdf_bookmarks.parse_page_range("2-4,6", 8), [2, 3, 4, 6]
        )

    def test_dominant_offset(self):
        offset, confidence = pdf_bookmarks.dominant_offset(
            [(12, 0.9), (12, 0.8), (13, 0.2)]
        )
        self.assertEqual(offset, 12)
        self.assertGreater(confidence, 0.8)


class IntegrationTests(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("pymupdf"), "PyMuPDF is not installed"
    )
    def test_write_collapsed_outline(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "test.pdf"
            document = pymupdf.open()
            document.new_page()
            document.new_page()
            document.set_toc(
                [[1, "Chapter", 1], [2, "Section", 2]], collapse=1
            )
            document.save(output)
            document.close()
            check = pymupdf.open(output)
            self.assertEqual(
                check.get_toc(), [[1, "Chapter", 1], [2, "Section", 2]]
            )
            self.assertTrue(check.get_toc(False)[0][3].get("collapse"))
            check.close()

    @unittest.skipUnless(
        importlib.util.find_spec("pymupdf"), "PyMuPDF is not installed"
    )
    def test_scan_map_apply_end_to_end(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "book.pdf"
            plan_path = root / "book.bookmarks.json"
            output = root / "book-bookmarked.pdf"
            document = pymupdf.open()
            page_text = [
                "A Sample Book",
                "Contents\n1 Introduction ........ 1\n1.1 Background ........ 2\n2 Methods ........ 4",
                "1 Introduction\nOpening text",
                "1.1 Background\nContext",
                "Interlude",
                "2 Methods\nProcedure",
            ]
            for text in page_text:
                page = document.new_page()
                page.insert_text((72, 72), text)
            document.save(source)
            document.close()

            scan_args = argparse.Namespace(
                pdf=str(source), plan=str(plan_path), toc_pages=None,
                max_scan_pages=80, offset=None, ocr=False,
                ocr_language="eng", password=None, force=False,
            )
            plan, _ = pdf_bookmarks.scan_pdf(scan_args)
            self.assertEqual(plan["toc_pages"], [2])
            self.assertEqual(plan["mapping"]["arabic_offset"], 2)
            self.assertEqual(
                [entry["pdf_page"] for entry in plan["entries"]], [3, 4, 6]
            )

            apply_args = argparse.Namespace(
                plan=str(plan_path), pdf=None, output=str(output),
                collapse_level=1, ignore_hash=False, password=None,
                force=False,
            )
            pdf_bookmarks.apply_plan(apply_args)
            check = pymupdf.open(output)
            self.assertEqual(
                check.get_toc(),
                [[1, "1 Introduction", 3], [2, "1.1 Background", 4], [1, "2 Methods", 6]],
            )
            self.assertTrue(check.get_toc(False)[0][3].get("collapse"))
            check.close()


if __name__ == "__main__":
    unittest.main()
