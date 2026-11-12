"""Unit tests for template routing, merge, and the vision token budget."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.main import app
from app.core.extract.budget import (
    ContextOverflow,
    encode_image_for_request,
    image_token_count,
)
from app.core.extract.parse import safe_parse_json
from app.core.extract.templates import exact_label, load_registry
from app.core.merge.assemble import assemble_document
from app.core.merge.pages import clean_section_name, merge_pages


class LabelTests(unittest.TestCase):
    def test_exact_labels(self):
        self.assertEqual(exact_label("invoice"), "invoice")
        self.assertEqual(exact_label("id_card"), "id_card")
        self.assertEqual(exact_label("medicine_details"), "medicine_details")
        self.assertEqual(exact_label("other_document"), "other_document")

    def test_prose_is_not_an_invoice(self):
        prose = (
            "invoice (contains itemized medical services, prices, and receipt numbers) "
            "OR medicine_details OR id_card OR other_document"
        )
        self.assertEqual(exact_label(prose), "other_document")
        self.assertEqual(exact_label("Invoice"), "other_document")
        self.assertEqual(exact_label("invoice "), "other_document")


class RegistryTests(unittest.TestCase):
    def test_default_file(self):
        registry = load_registry()
        self.assertEqual(registry.names, ["default"])
        invoice = registry.get("default")["invoice"]
        self.assertEqual(invoice["patient"]["admission_no"], "verbatim-string")
        self.assertIn("id_card_number", registry.get("default")["id_card"])

    def test_missing_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.json").write_text('["clinic"]', encoding="utf-8")
            (root / "clinic.json").write_text('{"invoice": {}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_registry(root)


class MergeTests(unittest.TestCase):
    def test_same_admission_appends_sections_and_keeps_later_summary(self):
        pages = [
            {
                "page": 1,
                "data": {
                    "patient": {"admission_no": "EP1", "medical_no": "U1", "name": "A"},
                    "sections": [{"section_name": "كشف 20.00", "items": [{"code": "A"}]}],
                    "summary": {"net": {"value": 1}},
                },
            },
            {
                "page": 2,
                "data": {
                    "patient": {"admission_no": "EP1", "medical_no": "U1"},
                    "sections": [{"section_name": "كشف", "items": [{"code": "B"}]}],
                    "summary": {"net": {"value": 9}},
                },
            },
        ]
        merged = merge_pages(pages)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["pages"], [1, 2])
        self.assertEqual(len(merged[0]["sections"]), 1)
        self.assertEqual(merged[0]["sections"][0]["section_name"], "كشف")
        self.assertEqual([item["code"] for item in merged[0]["sections"][0]["items"]], ["A", "B"])
        self.assertEqual(merged[0]["summary"]["net"]["value"], 9)
        self.assertEqual(clean_section_name("كشف 20.00 0.00 20.00"), "كشف")


class AssembleTests(unittest.TestCase):
    def test_passport_pages_are_omitted(self):
        payload = assemble_document({
            "1": {"kind": "skipped", "page": 1, "reason": "passport_only"},
            "2": {
                "kind": "id_cards",
                "page": 2,
                "cards": [{"page": 2, "source": "detector", "data": {"id_card_number": "ACA-1"}}],
            },
            "3": {"kind": "other", "page": 3, "document_type": "medicine_details"},
            "4": {
                "kind": "invoice",
                "page": 4,
                "data": {"patient": {"admission_no": "EP1", "medical_no": "U1"}, "sections": []},
            },
        })
        self.assertEqual(len(payload["invoices"]), 1)
        self.assertEqual(payload["id_cards"][0]["source"], "detector")
        self.assertEqual(payload["other_pages"], [{"page": 3, "document_type": "medicine_details"}])
        self.assertNotIn("passport", str(payload))


class BudgetTests(unittest.TestCase):
    def test_a4_page_fits_the_recorded_context(self):
        tokens = image_token_count(2480, 3508)
        self.assertEqual(tokens, 8580)
        self.assertLess(tokens + settings.MAX_TOKENS_INVOICE, settings.VLLM_MAX_MODEL_LEN)

    def test_shrink_and_refuse(self):
        original = settings.VLLM_MAX_MODEL_LEN
        image = Image.new("RGB", (800, 1200), "white")
        try:
            settings.VLLM_MAX_MODEL_LEN = 4096
            encoded = encode_image_for_request(image, {"document_type": ["invoice"]}, 256)
            self.assertTrue(encoded)
            settings.VLLM_MAX_MODEL_LEN = 10
            with self.assertRaises(ContextOverflow):
                encode_image_for_request(image, {"document_type": ["invoice"]}, 256)
        finally:
            settings.VLLM_MAX_MODEL_LEN = original


class ApiTests(unittest.TestCase):
    def test_unknown_template_is_rejected(self):
        client = TestClient(app)
        response = client.post(
            "/process-pdf/",
            data={"template": "missing"},
            files={"file": ("claim.pdf", b"%PDF-1.4", "application/pdf")},
        )
        self.assertEqual(response.status_code, 400)


class ParseTests(unittest.TestCase):
    def test_strips_fences_and_thinking(self):
        raw = '<think>draft</think>\n```json\n{"document_type": "invoice"}\n```'
        self.assertEqual(safe_parse_json(raw)["document_type"], "invoice")


if __name__ == "__main__":
    unittest.main()
