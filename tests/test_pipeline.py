"""Unit tests for template routing, merge, and the vision token budget."""
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.main import app
from app.core.extract.budget import (
    ContextOverflow,
    cap_image_dpi,
    encode_image_for_request,
    image_token_count,
    template_token_count,
)
from app.core.extract.client import template_to_json_schema
from app.core.extract.parse import invoice_is_empty, safe_parse_json
from app.core.extract.examples import (
    classifier_examples,
    example_image_tokens,
    id_card_examples,
    invoice_example_tokens,
    invoice_examples,
)
from app.core.extract.templates import CLASS_LABELS, exact_label, load_registry
from app.core.merge.assemble import assemble_document
from app.core.merge.pages import (
    clean_section_name,
    merge_pages,
    normalize_massara_invoice,
    same_section,
    section_key,
)


class LabelTests(unittest.TestCase):
    def test_exact_labels(self):
        self.assertEqual(exact_label("invoice"), "invoice")
        self.assertEqual(exact_label("medicine_details"), "medicine_details")
        self.assertEqual(exact_label("discharge_sheet"), "discharge_sheet")
        self.assertEqual(exact_label("other_document"), "other_document")
        self.assertEqual(exact_label("id_card"), "other_document")

    def test_prose_is_not_an_invoice(self):
        prose = (
            "invoice (contains itemized medical services, prices, and receipt numbers) "
            "OR medicine_details OR discharge_sheet OR other_document"
        )
        self.assertEqual(exact_label(prose), "other_document")
        self.assertEqual(exact_label("Invoice"), "other_document")
        self.assertEqual(exact_label("invoice "), "other_document")


class RegistryTests(unittest.TestCase):
    def test_janzour_file(self):
        registry = load_registry()
        self.assertEqual(registry.names, ["janzour", "massara", "musafat", "safwa"])
        safwa_invoice = dict(registry.get("safwa")["invoice"])
        self.assertEqual(
            safwa_invoice.pop("instructions"),
            "Output every printed table row. Keep consecutive rows that repeat the same code, time, and price as separate items. Do not merge them.",
        )
        self.assertEqual(safwa_invoice, registry.get("janzour")["invoice"])
        self.assertEqual(registry.get("safwa")["id_card"], registry.get("janzour")["id_card"])
        self.assertEqual(
            registry.get("safwa")["classify"]["document_type"],
            registry.get("massara")["classify"]["document_type"],
        )
        self.assertEqual(
            registry.get("safwa")["classify"]["instructions"],
            "If the title at the top contains فاتورة رقم or إيصال رقم followed by a number, choose outpatient_invoice. If the title is كشف تفاصيل الخدمات and has no invoice number, choose inpatient_invoice.",
        )
        self.assertNotIn("instructions", registry.get("massara")["classify"])
        invoice = registry.get("janzour")["invoice"]
        outpatient = registry.get("safwa")["outpatient_invoice"]
        self.assertEqual(outpatient["invoice_number"], "verbatim-string")
        self.assertNotIn("summary", outpatient)
        item = outpatient["sections"][0]["items"][0]
        self.assertEqual(item["unit_price"], 0.0)
        self.assertNotIn("patient_share", item)
        self.assertEqual(outpatient["patient"], invoice["patient"])
        self.assertEqual(invoice["patient"]["admission_no"], "verbatim-string")
        self.assertIn("card_number", registry.get("janzour")["id_card"])
        self.assertIn("validity_period", registry.get("janzour")["id_card"])
        self.assertEqual(
            registry.get("janzour")["classify"]["document_type"],
            list(CLASS_LABELS),
        )

    def test_massara_template(self):
        registry = load_registry()
        invoice = registry.get("massara")["invoice"]
        self.assertEqual(invoice["patient"]["file_number"], "verbatim-string")
        self.assertEqual(invoice["receipt"]["receipt_number"], "verbatim-string")
        self.assertIsNone(invoice["summary"]["total"])
        self.assertNotIn("doctor_invoice", registry.get("massara"))
        id_card = registry.get("massara")["id_card"]
        self.assertEqual(id_card, registry.get("janzour")["id_card"])
        outpatient = registry.get("massara")["outpatient_invoice"]
        self.assertEqual(outpatient["summary"]["total"], 0.0)
        self.assertNotIn("specialize", outpatient["sections"][0]["items"][0])
        self.assertEqual(
            registry.get("massara")["classify"]["document_type"],
            [
                "inpatient_invoice",
                "outpatient_invoice",
                "other_document",
            ],
        )
        musafat = registry.get("musafat")
        massara = registry.get("massara")
        self.assertEqual(
            musafat["invoice"]["receipt"]["receipt_number"],
            "verbatim-string. The digits printed after إيصال رقم. Copy them. Do not return null.",
        )
        musafat_invoice = json.loads(json.dumps(musafat["invoice"]))
        musafat_invoice["receipt"]["receipt_number"] = "verbatim-string"
        massara_invoice = json.loads(json.dumps(massara["invoice"]))
        self.assertEqual(
            massara_invoice.pop("instructions"),
            "Write each section once. Do not copy a section or its rows into another section. Keep rows that share a code when the date or time differs.",
        )
        self.assertEqual(musafat_invoice, massara_invoice)
        self.assertEqual(musafat["classify"], massara["classify"])
        self.assertEqual(musafat["outpatient_invoice"], massara["outpatient_invoice"])
        self.assertEqual(musafat["id_card"], massara["id_card"])

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

    def test_section_key_collapses_arabic_spelling_variants(self):
        self.assertEqual(section_key("الأشعة"), section_key("الاشعة"))
        self.assertEqual(section_key("الأشعة"), "الاشعه")
        self.assertEqual(section_key("الصّيدلية"), section_key("الصيدليه"))
        self.assertTrue(same_section("الأشعة", "الاشعة"))
        self.assertFalse(same_section("الأشعة", "المختبر"))

    def test_arabic_spelling_variants_merge_across_pages(self):
        pages = [
            {
                "page": 1,
                "data": {
                    "patient": {"admission_no": "EP1", "medical_no": "U1"},
                    "sections": [{"section_name": "الأشعة", "items": [{"code": "A"}]}],
                },
            },
            {
                "page": 2,
                "data": {
                    "patient": {"admission_no": "EP1", "medical_no": "U1"},
                    "sections": [{"section_name": "الاشعة", "items": [{"code": "B"}]}],
                },
            },
        ]
        merged = merge_pages(pages)
        self.assertEqual(len(merged[0]["sections"]), 1)
        self.assertEqual(merged[0]["sections"][0]["section_name"], "الأشعة")
        self.assertEqual([item["code"] for item in merged[0]["sections"][0]["items"]], ["A", "B"])

    def test_fuzzy_merge_only_at_page_boundary(self):
        pages = [
            {
                "page": 1,
                "data": {
                    "patient": {"admission_no": "EP1", "medical_no": "U1"},
                    "sections": [
                        {"section_name": "الأشعة", "items": [{"code": "A"}]},
                        {"section_name": "الأشع", "items": [{"code": "B"}]},
                    ],
                },
            },
            {
                "page": 2,
                "data": {
                    "patient": {"admission_no": "EP1", "medical_no": "U1"},
                    "sections": [{"section_name": "الأشع", "items": [{"code": "C"}]}],
                },
            },
        ]
        merged = merge_pages(pages)
        # Within page 1, near-miss titles stay separate; across the page break they merge.
        self.assertEqual(len(merged[0]["sections"]), 2)
        self.assertEqual([item["code"] for item in merged[0]["sections"][0]["items"]], ["A"])
        self.assertEqual([item["code"] for item in merged[0]["sections"][1]["items"]], ["B", "C"])

    def test_sandwiched_misread_ids_merge_into_neighbors(self):
        pages = [
            {
                "page": 6,
                "data": {
                    "patient": {
                        "admission_no": "EP250053055",
                        "medical_no": "U2500055368",
                        "name": "A",
                    },
                    "sections": [{"section_name": "أدوية و مستلزمات طبية", "items": [{"code": "P6"}]}],
                },
            },
            {
                "page": 7,
                "data": {
                    "patient": {
                        "admission_no": "EP250053055",
                        "medical_no": "U25000553368",
                        "name": "A",
                    },
                    "sections": [{"section_name": "أدوية و مستلزمات طبية", "items": [{"code": "P7"}]}],
                },
            },
            {
                "page": 8,
                "data": {
                    "patient": {
                        "admission_no": "EP250053055",
                        "medical_no": "U2500055368",
                        "name": "A",
                    },
                    "sections": [{"section_name": "أدوية و مستلزمات طبية", "items": [{"code": "P8"}]}],
                },
            },
        ]
        merged = merge_pages(pages)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["pages"], [6, 7, 8])
        self.assertEqual(merged[0]["patient"]["medical_no"], "U2500055368")
        self.assertEqual(
            [item["code"] for item in merged[0]["sections"][0]["items"]],
            ["P6", "P7", "P8"],
        )

    def test_different_neighbor_ids_are_not_sandwiched(self):
        pages = [
            {
                "page": 1,
                "data": {
                    "patient": {"admission_no": "EP1", "medical_no": "U1"},
                    "sections": [{"section_name": "A", "items": [{"code": "1"}]}],
                },
            },
            {
                "page": 2,
                "data": {
                    "patient": {"admission_no": "EP2", "medical_no": "U2"},
                    "sections": [{"section_name": "B", "items": [{"code": "2"}]}],
                },
            },
            {
                "page": 3,
                "data": {
                    "patient": {"admission_no": "EP3", "medical_no": "U3"},
                    "sections": [{"section_name": "C", "items": [{"code": "3"}]}],
                },
            },
        ]
        merged = merge_pages(pages)
        self.assertEqual(len(merged), 3)

    def test_massara_renames_receipt_number_and_merges_on_file_and_invoice(self):
        page = normalize_massara_invoice({
            "patient": {"file_number": "1105839"},
            "receipt": {"receipt_number": "754425", "receipt_date": "25.04.2025"},
            "sections": [{"section_name": "تحليل", "items": [{"code": "A"}]}],
        })
        self.assertNotIn("receipt_number", page["receipt"])
        self.assertEqual(page["receipt"]["invoice_number"], "754425")

        pages = [
            {
                "page": 1,
                "data": {
                    "patient": {"file_number": "1105839"},
                    "receipt": {"invoice_number": "754425"},
                    "sections": [{"section_name": "تحليل", "items": [{"code": "A"}]}],
                },
            },
            {
                "page": 2,
                "data": {
                    "patient": {"file_number": "1105839"},
                    "receipt": {"invoice_number": "754425"},
                    "sections": [{"section_name": "تحليل", "items": [{"code": "B"}]}],
                },
            },
            {
                "page": 3,
                "data": {
                    "patient": {"file_number": "1105839"},
                    "receipt": {"invoice_number": "1032754"},
                    "sections": [{"section_name": "البيانات", "items": [{"code": "C"}]}],
                    "summary": {"total": 10},
                },
            },
        ]
        merged = merge_pages(pages, template="massara")
        self.assertEqual(merge_pages(pages, template="musafat"), merged)
        self.assertEqual(len(merged), 2)
        by_invoice = {
            (doc.get("receipt") or {}).get("invoice_number"): doc
            for doc in merged
        }
        self.assertEqual(by_invoice["754425"]["pages"], [1, 2])
        self.assertEqual(
            [item["code"] for item in by_invoice["754425"]["sections"][0]["items"]],
            ["A", "B"],
        )
        self.assertEqual(by_invoice["1032754"]["pages"], [3])
        self.assertEqual(by_invoice["1032754"]["summary"]["total"], 10)

    def test_blank_inpatient_continuation_joins_previous_section(self):
        pages = [
            {
                "page": 4,
                "document_type": "invoice",
                "data": {
                    "patient": {"file_number": "450633"},
                    "receipt": {"invoice_number": "24422"},
                    "sections": [
                        {"section_name": "خدمات أخرى", "items": [{"code": "A"}]},
                        {"section_name": "خدمات إيوائية", "items": [{"code": "B"}]},
                    ],
                },
            },
            {
                "page": 5,
                "document_type": "invoice",
                "data": {
                    "patient": {"file_number": "450633"},
                    "receipt": {"invoice_number": "24422"},
                    "sections": [
                        {"section_name": None, "items": [{"code": "C"}], "section_total": {"net_price": 30}},
                        {"section_name": "عمليات جراحية", "items": [{"code": "D"}]},
                    ],
                },
            },
        ]
        for template in ("massara", "musafat"):
            merged = merge_pages(pages, template=template)
            self.assertEqual(len(merged), 1)
            self.assertNotIn("_document_type", merged[0])
            sections = merged[0]["sections"]
            self.assertEqual([section["section_name"] for section in sections], ["خدمات أخرى", "خدمات إيوائية", "عمليات جراحية"])
            self.assertEqual([item["code"] for item in sections[1]["items"]], ["B", "C"])
            self.assertEqual(sections[1]["section_total"]["net_price"], 30)

        outpatient = [
            {**pages[0], "document_type": "outpatient_invoice"},
            {**pages[1], "document_type": "outpatient_invoice"},
        ]
        kept = merge_pages(outpatient, template="musafat")
        self.assertEqual(
            [section["section_name"] for section in kept[0]["sections"]],
            ["خدمات أخرى", "خدمات إيوائية", "", "عمليات جراحية"],
        )

    def test_safwa_keeps_inpatient_and_outpatient_apart(self):
        patient = {"admission_no": "IP2500001615", "medical_no": "U2200036133"}
        pages = [
            {
                "page": 3,
                "document_type": "outpatient_invoice",
                "data": {
                    "patient": patient,
                    "invoice_number": "FI2500001740",
                    "sections": [{"section_name": "حالة مستشفى", "items": [{"code": "A"}]}],
                },
            },
            {
                "page": 4,
                "document_type": "inpatient_invoice",
                "data": {
                    "patient": patient,
                    "sections": [{"section_name": "كشف", "items": [{"code": "B"}]}],
                },
            },
            {
                "page": 5,
                "document_type": "inpatient_invoice",
                "data": {
                    "patient": patient,
                    "sections": [{"section_name": "كشف", "items": [{"code": "C"}]}],
                },
            },
        ]
        merged = merge_pages(pages, template="safwa")
        self.assertEqual(len(merged), 2)
        by_type = {doc["document_type"]: doc for doc in merged}
        self.assertEqual(by_type["outpatient_invoice"]["pages"], [3])
        self.assertEqual(by_type["inpatient_invoice"]["pages"], [4, 5])
        self.assertEqual(
            [item["code"] for item in by_type["inpatient_invoice"]["sections"][0]["items"]],
            ["B", "C"],
        )
        self.assertEqual(len(merge_pages(pages)), 1)

        sandwiched = [
            {
                "page": 1,
                "document_type": "inpatient_invoice",
                "data": {"patient": patient, "sections": [{"section_name": "كشف", "items": [{"code": "A"}]}]},
            },
            {
                "page": 2,
                "document_type": "outpatient_invoice",
                "data": {
                    "patient": {"admission_no": "IP9", "medical_no": "U9"},
                    "invoice_number": "RG1",
                    "sections": [{"section_name": "إيصال", "items": [{"code": "B"}]}],
                },
            },
            {
                "page": 3,
                "document_type": "inpatient_invoice",
                "data": {"patient": patient, "sections": [{"section_name": "كشف", "items": [{"code": "C"}]}]},
            },
        ]
        split = merge_pages(sandwiched, template="safwa")
        self.assertEqual(len(split), 2)
        outpatient_doc = next(doc for doc in split if doc["document_type"] == "outpatient_invoice")
        self.assertEqual(outpatient_doc["pages"], [2])
        self.assertEqual(outpatient_doc["patient"]["admission_no"], "IP9")


class AssembleTests(unittest.TestCase):
    def test_passport_pages_are_omitted(self):
        payload = assemble_document({
            "1": {"kind": "skipped", "page": 1, "reason": "passport_only"},
            "2": {
                "kind": "id_cards",
                "page": 2,
                "cards": [{"page": 2, "source": "detector", "data": {"card_number": "ACA-1"}}],
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
        self.assertEqual(
            [item.get("kind") for item in payload["raw_pages"]],
            ["skipped", "id_cards", "other", "invoice"],
        )
        self.assertEqual(payload["raw_pages"][1]["cards"][0]["data"]["card_number"], "ACA-1")
        self.assertEqual(payload["raw_pages"][3]["data"]["patient"]["admission_no"], "EP1")
        self.assertNotIn("passport", str(payload["invoices"]))

    def test_empty_invoice_page_is_an_error_and_not_merged(self):
        payload = assemble_document({
            "1": {"kind": "error", "page": 1, "message": "empty invoice", "document_type": "invoice"},
            "2": {
                "kind": "invoice",
                "page": 2,
                "document_type": "invoice",
                "data": {"patient": {"admission_no": "EP1", "medical_no": "U1"}, "sections": []},
            },
        })
        self.assertEqual(payload["errors"], [{"page": 1, "message": "empty invoice"}])
        self.assertEqual([item["page"] for item in payload["raw_pages"]], [1, 2])
        self.assertEqual(len(payload["invoices"]), 1)
        self.assertEqual(payload["invoices"][0]["pages"], [2])


class BudgetTests(unittest.TestCase):
    def test_a4_page_fits_the_recorded_context(self):
        tokens = image_token_count(2480, 3508)
        self.assertEqual(tokens, 8580)
        self.assertLess(tokens + settings.MAX_TOKENS_INVOICE, settings.VLLM_MAX_MODEL_LEN)

    def test_classifier_examples_fit_16k(self):
        examples = classifier_examples("janzour")
        labels = [item.label for item in examples]
        self.assertEqual(labels, [
            "discharge_sheet",
            "invoice",
            "invoice",
            "other_document",
            "other_document",
            "other_document",
        ])
        self.assertTrue(set(labels) <= set(CLASS_LABELS))
        classify = load_registry().get("janzour")["classify"]
        query = image_token_count(1654, 1169)
        packed = (
            example_image_tokens("janzour")
            + query
            + template_token_count(classify)
            + settings.MAX_TOKENS_CLASSIFY
        )
        self.assertLessEqual(packed, 16384)
        self.assertEqual(classify["document_type"], list(CLASS_LABELS))
        massara = classifier_examples("massara")
        self.assertEqual(
            [item.label for item in massara],
            [
                "other_document",
                "outpatient_invoice",
                "inpatient_invoice",
                "other_document",
                "other_document",
                "other_document",
                "outpatient_invoice",
            ],
        )
        self.assertEqual(
            [item.label for item in classifier_examples("musafat")],
            [
                "other_document",
                "inpatient_invoice",
                "other_document",
                "other_document",
                "other_document",
                "outpatient_invoice",
                "other_document",
            ],
        )
        self.assertEqual(
            [item.label for item in classifier_examples("safwa")],
            [
                "other_document",
                "inpatient_invoice",
                "inpatient_invoice",
                "other_document",
                "other_document",
                "outpatient_invoice",
                "outpatient_invoice",
            ],
        )

    def test_invoice_example_fits_beside_a_full_page(self):
        examples = invoice_examples("janzour")
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0].tokens, image_token_count(1240, 1753))
        output = json.loads(examples[0].output)
        self.assertEqual(output["user"], "kanz")
        self.assertEqual(len(output["sections"]), 2)
        invoice = load_registry().get("janzour")["invoice"]
        page_w = round(2480 * 200 / 300)
        page_h = round(3508 * 200 / 300)
        packed = (
            invoice_example_tokens("janzour")
            + image_token_count(page_w, page_h)
            + template_token_count(invoice)
            + settings.MAX_TOKENS_INVOICE
        )
        self.assertLessEqual(packed, settings.VLLM_MAX_MODEL_LEN)
        massara_invoices = invoice_examples("massara")
        self.assertEqual(len(massara_invoices), 3)
        self.assertEqual(
            json.loads(massara_invoices[1].output)["patient"]["file_number"],
            "1121128",
        )
        self.assertEqual(
            json.loads(massara_invoices[2].output)["patient"]["file_number"],
            "1105839",
        )
        musafat_invoices = invoice_examples("musafat")
        self.assertEqual(
            [item.output for item in musafat_invoices],
            [item.output for item in massara_invoices],
        )
        safwa_invoices = invoice_examples("safwa")
        self.assertEqual(safwa_invoices[0].output, examples[0].output)
        self.assertEqual(len(safwa_invoices), 3)
        self.assertEqual(
            [item.file for item in invoice_examples("safwa", "invoice")],
            ["inpatient_invoice.jpg"],
        )
        self.assertEqual(
            [item.file for item in invoice_examples("safwa", "outpatient_invoice")],
            ["outpatient_invoice.jpg", "outpatient_invoice_1.jpg"],
        )
        outpatient = json.loads(safwa_invoices[1].output)
        self.assertEqual(outpatient["invoice_number"], "RG2500172090")
        self.assertEqual(outpatient["patient"]["admission_no"], "OP2500090469")
        self.assertEqual(outpatient["patient"]["medical_no"], "U2500005228")
        self.assertEqual(outpatient["sections"][0]["items"][0]["code"], "SPE0000017")
        self.assertEqual(outpatient["sections"][0]["items"][0]["unit_price"], 25.0)
        self.assertEqual(outpatient["user"], "بشري.محمد")
        second = json.loads(safwa_invoices[2].output)
        self.assertEqual(second["invoice_number"], "FI2500001740")
        self.assertEqual(second["patient"]["admission_no"], "IP2500001615")
        self.assertEqual(second["patient"]["medical_no"], "U2200036133")
        self.assertEqual(second["sections"][0]["items"][0]["description"], "خدمات الإقامة والاعاشة")
        self.assertEqual(second["sections"][0]["section_total"]["net_price"], 5080.1)
        self.assertEqual(second["user"], "CASHIER")
        massara_invoice = load_registry().get("massara")["invoice"]
        massara_packed = (
            invoice_example_tokens("massara")
            + image_token_count(page_w, page_h)
            + template_token_count(massara_invoice)
            + settings.MAX_TOKENS_INVOICE
        )
        self.assertLessEqual(massara_packed, settings.VLLM_MAX_MODEL_LEN)

    def test_id_card_example_matches_template(self):
        examples = id_card_examples("janzour")
        self.assertEqual(len(examples), 1)
        output = json.loads(examples[0].output)
        self.assertEqual(output["card_number"], "ACA-2501-02959-P03")
        self.assertEqual(output["validity_period"]["start_date"], "2025-02-17")
        schema = load_registry().get("janzour")["id_card"]
        self.assertEqual(set(output), set(schema))
        self.assertEqual(set(output["validity_period"]), set(schema["validity_period"]))
        self.assertEqual(len(id_card_examples("massara")), 1)
        self.assertEqual(
            [item.output for item in id_card_examples("musafat")],
            [item.output for item in id_card_examples("massara")],
        )
        self.assertEqual(id_card_examples("safwa"), [])

    def test_dpi_above_200_is_reduced_to_200(self):
        image = Image.new("RGB", (300, 600), "white")
        self.assertEqual(cap_image_dpi(image, 300).size, (200, 400))
        self.assertEqual(cap_image_dpi(image, 200).size, (300, 600))
        self.assertEqual(cap_image_dpi(image, 150).size, (300, 600))

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

    def test_blank_invoice_payload_is_empty(self):
        self.assertTrue(invoice_is_empty({}))
        self.assertTrue(invoice_is_empty(None))
        self.assertTrue(invoice_is_empty({
            "hospital_name": None,
            "sections": [],
            "summary": {"total": 0, "paid": None, "remaining": ""},
        }))
        self.assertFalse(invoice_is_empty({
            "patient": {"file_number": "450633"},
            "sections": [{"section_name": "تحاليل طبية", "items": [{"code": "A"}]}],
        }))

    def test_massara_invoice_schema_is_json_schema(self):
        invoice = load_registry().get("massara")["invoice"]
        invoice.pop("instructions", None)
        schema = template_to_json_schema(invoice)
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["properties"]["sections"]["type"], "array")
        item = schema["properties"]["sections"]["items"]["properties"]["items"]["items"]
        self.assertEqual(item["properties"]["quantity"]["type"], "integer")
        self.assertEqual(item["properties"]["net_price"]["type"], "number")
        self.assertEqual(
            schema["properties"]["summary"]["properties"]["total"]["type"],
            ["string", "number", "null"],
        )
        labels = template_to_json_schema(
            {"document_type": ["inpatient_invoice", "outpatient_invoice", "other_document"]}
        )
        self.assertEqual(
            labels["properties"]["document_type"]["enum"],
            ["inpatient_invoice", "outpatient_invoice", "other_document"],
        )


if __name__ == "__main__":
    unittest.main()
