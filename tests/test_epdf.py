"""Tests for the electronic PDF (e-PDF) API, Janzour parser, worker, and text-only NuExtract client."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pymupdf as fitz
from fastapi.testclient import TestClient

from app.config import settings
from app.core.epdf import (
    EpdfDocument,
    EpdfError,
    EpdfPagePayload,
    get_epdf_parser,
    has_selectable_text,
    registered_epdf_templates,
    require_selectable_text,
)
from app.core.epdf.parsers.janzour import (
    dedupe_headers,
    fix_cell,
    fix_line,
    format_page_text,
    norm_title,
    rows_to_lines,
    rtl_words,
    table_to_records,
)
from app.core.extract.client import extract_text
from app.core.extract.templates import load_registry
from app.main import app
from app.services import epdf_worker, extract_worker


def _make_pdf_bytes(*, text: str | None = None) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


class FakeAsyncRedis:
    """Minimal async Redis double capturing hashes, streams, lists, and pubsub publishes."""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.streams: list[tuple[str, dict[str, str]]] = []
        self.published: list[tuple[str, str]] = []

    async def hset(self, key: str, mapping: dict[str, str] | None = None, **kwargs):
        bucket = self.hashes.setdefault(key, {})
        if mapping:
            bucket.update({k: str(v) for k, v in mapping.items()})
        for k, v in kwargs.items():
            bucket[k] = str(v)

    async def expire(self, key: str, ttl: int):
        return True

    async def set(self, key: str, value, ex: int | None = None):
        self.kv[key] = str(value)
        return True

    async def rpush(self, key: str, *values):
        self.lists.setdefault(key, []).extend(str(v) for v in values)

    async def xadd(self, stream: str, fields: dict[str, str], **kwargs):
        self.streams.append((stream, dict(fields)))
        return "1-0"

    async def publish(self, channel: str, payload: str):
        self.published.append((channel, payload))
        return 1

    async def aclose(self):
        return None


class JanzourEpdfParserTests(unittest.TestCase):
    def test_registry_contains_janzour_and_rejects_unknown(self):
        self.assertIn("janzour", registered_epdf_templates())
        self.assertTrue(callable(get_epdf_parser("janzour")))
        with self.assertRaises(EpdfError):
            get_epdf_parser("nonexistent_hospital")

    def test_fix_line_and_ligature_cleanup(self):
        self.assertEqual(fix_line("EP260036500"), "EP260036500")
        self.assertEqual(fix_line("VIRT-10"), "VIRT-10")
        # Visual-order Arabic gets flipped to logical order; ligature fixed.
        self.assertEqual(fix_line("دددم"), "محمد")
        self.assertEqual(fix_cell("line1\nline2"), "line1 line2")

    def test_rtl_words_preserves_ltr_latin_runs(self):
        words = [
            {"text": "الطبيب"},
            {"text": "Alshebani"},
            {"text": "Mohamed"},
            {"text": "VIRT-10"},
        ]
        self.assertEqual(rtl_words(words), ["الطبيب", "VIRT-10", "Mohamed", "Alshebani"])

    def test_norm_title_strips_trailing_zero_noise(self):
        self.assertEqual(norm_title("أدوية و مستلزمات طبية 0.00"), "أدوية و مستلزمات طبية")
        self.assertEqual(norm_title("خدمات طبية"), "خدمات طبية")

    def test_dedupe_headers_surgery_table_two_time_columns(self):
        headers = [
            "الرمز", "البيان", "الكمية", "نوع العملية",
            "من", "الساعة", "إلى", "الساعة",
            "صافي السعر", "المريض", "الجهة",
        ]
        deduped = dedupe_headers(headers)
        self.assertEqual(len(deduped), len(set(deduped)))
        self.assertIn("الساعة (من)", deduped)
        self.assertIn("الساعة (إلى)", deduped)

    def test_table_to_records_skips_page1_header_only_orphan(self):
        orphan = [["الرمز", "البيان", "الكمية", "التاريخ", "الساعة", "صافي السعر", "المريض", "الجهة"]]
        kind, records = table_to_records(orphan)
        self.assertEqual(kind, "empty")
        self.assertEqual(records, [])

    def test_table_to_records_parses_summary_table_and_total_rows(self):
        summary_rows = [
            ["", "المريض", "الجهة", "القيمة"],
            ["الإجمالي", "0.00", "102 484.79", "102 484.79"],
            ["التخفيض", "0.00", "1 970.00", "1 970.00"],
            ["الصافي", "0.00", "100 514.79", "100 514.79"],
        ]
        kind, records = table_to_records(summary_rows)
        self.assertEqual(kind, "summary")
        self.assertEqual(len(records), 4)

        item_rows = [
            ["الرمز", "البيان", "الكمية", "التاريخ", "الساعة", "صافي السعر", "المريض", "الجهة"],
            ["ER-Co00006", "كشف طوارئ", "1", "21/09/2026", "19:55", "20.00", "0.00", "20.00"],
            ["", "المبلغ الإجمالي :", "", "", "", "20.00", "", "20.00"],
        ]
        kind, records = table_to_records(item_rows)
        self.assertEqual(kind, "items")
        lines = rows_to_lines(kind, records)
        self.assertEqual(len(lines), 3)
        self.assertIn("ER-Co00006", lines[1])
        self.assertTrue(lines[2].startswith("[SECTION_TOTAL]"))
        self.assertIn("صافي السعر: 20.00", lines[2])
        self.assertIn("الجهة: 20.00", lines[2])

    def test_table_to_records_unpacks_merged_pharmacy_time_date_price(self):
        rows = [
            ["الرمز", "البيان", "الوحدة", "الكمية", "سعر الوحدة", "التاريخ", "الساعة", "صافي السعر", "المريض", "الجهة"],
            ["CAST0000003", "POP bandage", "قطعة", "3", "", "", "8:36 22/09/2026 3.75", "11.25", "0.00", "11.25"],
        ]
        kind, records = table_to_records(rows)
        self.assertEqual(kind, "items")
        self.assertEqual(records[0]["سعر الوحدة"], "3.75")
        self.assertEqual(records[0]["التاريخ"], "22/09/2026")
        self.assertEqual(records[0]["الساعة"], "8:36")

    def test_format_page_text_includes_header_footer_and_summary(self):
        text = format_page_text(
            header=["المريض : مؤيد محمد شلابي", "رقم الدخول : EP260036500"],
            footer=["RUN DATE : 28/10/2026", "USER : محمد.الديب"],
            page_number=12,
            sections=[
                {
                    "kind": "summary",
                    "title": "",
                    "rows": [
                        ["", "المريض", "الجهة", "القيمة"],
                        ["الصافي", "0.00", "100 514.79", "100 514.79"],
                    ],
                }
            ],
        )
        self.assertIn("[HEADER]", text)
        self.assertIn("[FOOTER]", text)
        self.assertIn("=== PAGE 12 ===", text)
        self.assertIn("[SUMMARY]", text)
        self.assertIn("100 514.79", text)


class SelectableTextValidationTests(unittest.TestCase):
    def test_text_layer_validation_distinguishes_epdf_and_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            epdf_path = Path(tmp) / "electronic.pdf"
            epdf_path.write_bytes(_make_pdf_bytes(text="CliniSysErp Invoice EP260036500 U2600040571"))
            self.assertTrue(has_selectable_text(epdf_path))
            require_selectable_text(epdf_path)

            scanned_path = Path(tmp) / "scanned.pdf"
            scanned_path.write_bytes(_make_pdf_bytes(text=None))
            self.assertFalse(has_selectable_text(scanned_path))
            with self.assertRaises(ValueError) as ctx:
                require_selectable_text(scanned_path)
            self.assertIn("/process-pdf/", str(ctx.exception))


class EpdfApiRouteTests(unittest.TestCase):
    def setUp(self):
        self.headers = {"X-API-Key": settings.API_KEY}

    def test_list_epdf_templates(self):
        with TestClient(app) as client:
            resp = client.get("/api/epdf/templates", headers=self.headers)
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertTrue(payload["ok"])
            self.assertIn("janzour", payload["templates"])

    def test_process_epdf_rejects_unregistered_template(self):
        pdf_bytes = _make_pdf_bytes(text="CliniSysErp Invoice EP260036500 U2600040571")
        with TestClient(app) as client:
            resp = client.post(
                "/api/epdf/process",
                headers=self.headers,
                data={"template": "musafat"},
                files={"file": ("inv.pdf", pdf_bytes, "application/pdf")},
            )
            self.assertEqual(resp.status_code, 400)
            self.assertIn("Unknown e-PDF template", resp.json()["detail"])

    def test_process_epdf_rejects_scanned_pdf_with_422(self):
        scanned_bytes = _make_pdf_bytes(text=None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "STORAGE_DIR", tmp):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/epdf/process",
                    headers=self.headers,
                    data={"template": "janzour"},
                    files={"file": ("scan.pdf", scanned_bytes, "application/pdf")},
                )
                self.assertEqual(resp.status_code, 422)
                self.assertIn("/process-pdf/", resp.json()["detail"])
                # Uploaded scanned file must be removed after rejection.
                uploads = list((Path(tmp) / "uploads").glob("*.pdf"))
                self.assertEqual(uploads, [])

    def test_process_epdf_and_batch_enqueue_on_epdf_stream(self):
        epdf_bytes = _make_pdf_bytes(text="CliniSysErp Invoice EP260036500 U2600040571 Patient Test")
        fake_redis = FakeAsyncRedis()
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "STORAGE_DIR", tmp):
            with TestClient(app) as client:
                client.app.state.redis = fake_redis
                resp = client.post(
                    "/api/epdf/process",
                    headers=self.headers,
                    data={"template": "janzour"},
                    files={"file": ("janzour_epdf.pdf", epdf_bytes, "application/pdf")},
                )
                self.assertEqual(resp.status_code, 200)
                body = resp.json()
                self.assertEqual(body["status"], "queued")
                self.assertEqual(body["template"], "janzour")
                self.assertEqual(body["pipeline"], "epdf")
                self.assertEqual(body["page_count"], 1)
                self.assertEqual(len(fake_redis.streams), 1)
                stream_name, fields = fake_redis.streams[0]
                self.assertEqual(stream_name, settings.STREAM_EPDF)
                self.assertEqual(fields["task_id"], body["task_id"])
                self.assertEqual(fake_redis.hashes[f"job:{body['task_id']}"]["pipeline"], "epdf")

                # Batch upload
                batch_resp = client.post(
                    "/api/epdf/batch",
                    headers=self.headers,
                    data={"template": "janzour"},
                    files=[
                        ("files", ("a.pdf", epdf_bytes, "application/pdf")),
                        ("files", ("b.pdf", epdf_bytes, "application/pdf")),
                    ],
                )
                self.assertEqual(batch_resp.status_code, 200)
                items = batch_resp.json()
                self.assertEqual(len(items), 2)
                self.assertTrue(all(item["pipeline"] == "epdf" for item in items))
                self.assertEqual(len(fake_redis.streams), 3)
                self.assertTrue(all(s == settings.STREAM_EPDF for s, _ in fake_redis.streams))


class EpdfWorkerAndTextExtractTests(unittest.TestCase):
    def test_extract_text_builds_text_only_vllm_payload(self):
        captured: dict = {}

        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "usage": {"prompt_tokens": 250},
                    "choices": [{"message": {"content": json.dumps({"hospital_name": "المستشفى الدولي"})}}],
                }

        class FakeHttp:
            async def post(self, url, json=None):
                captured["url"] = url
                captured["body"] = json
                return FakeResponse()

        parsed = asyncio.run(
            extract_text(
                FakeHttp(),
                "[HEADER]\nالمريض : test",
                {"hospital_name": "verbatim-string"},
                max_tokens=1024,
                instructions="Keep verbatim.",
                page_id=1,
            )
        )
        self.assertEqual(parsed["hospital_name"], "المستشفى الدولي")
        self.assertEqual(captured["body"]["messages"], [{"role": "user", "content": "[HEADER]\nالمريض : test"}])
        self.assertFalse(captured["body"]["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(captured["body"]["chat_template_kwargs"]["instructions"], "Keep verbatim.")
        self.assertIn("json_schema", captured["body"]["response_format"])

    def test_epdf_worker_enqueues_direct_text_invoice_stage(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "STORAGE_DIR", tmp):
            fake_store = MagicMock()
            fake_store.read_job.return_value = {
                "filename": "inv.pdf",
                "template": "janzour",
                "pdf_path": "/tmp/dummy.pdf",
                "status": "queued",
                "batch_id": "",
            }
            fake_doc = EpdfDocument(
                template="janzour",
                header=["المريض : test"],
                footer=["RUN DATE : 28/10/2026"],
                pages=[
                    EpdfPagePayload(page=1, text="[HEADER]\npage 1 text", sections=[]),
                    EpdfPagePayload(page=2, text="[HEADER]\npage 2 text", sections=[]),
                ],
            )
            with patch.object(epdf_worker, "get_epdf_parser", return_value=lambda _p: fake_doc), \
                 patch.object(epdf_worker, "publish_update"):
                epdf_worker.handle(fake_store, {"task_id": "task-epdf-1"})

            fake_store.set_pending_once.assert_called_once_with("task-epdf-1", 2)
            self.assertEqual(fake_store.enqueue.call_count, 2)
            first_stream, first_fields = fake_store.enqueue.call_args_list[0].args
            self.assertEqual(first_stream, settings.STREAM_EXTRACT)
            self.assertEqual(first_fields["stage"], "invoice")
            self.assertEqual(first_fields["input_mode"], "text")
            self.assertEqual(first_fields["page"], 1)
            self.assertEqual(Path(first_fields["text_path"]).read_text(encoding="utf-8"), "[HEADER]\npage 1 text")

    def test_extract_worker_processes_text_mode_without_images(self):
        registry = load_registry()
        with tempfile.TemporaryDirectory() as tmp:
            text_file = Path(tmp) / "page_1.txt"
            text_file.write_text("[HEADER]\nالمريض : test\n[SECTION] كشف", encoding="utf-8")

            fake_store = MagicMock()
            fake_store.read_job.return_value = {
                "filename": "inv.pdf",
                "template": "janzour",
                "status": "processing",
                "batch_id": "",
            }
            fake_store.complete_page.return_value = True

            sample_invoice = {
                "hospital_name": "المستشفى الدولي",
                "patient": {"name": "test", "admission_no": "EP1", "medical_no": "U1"},
                "sections": [{"section_name": "كشف", "items": [{"code": "ER-1", "net_price": 20.0}]}],
            }

            with patch.object(extract_worker, "extract_text", new=AsyncMock(return_value=sample_invoice)) as mock_text, \
                 patch.object(extract_worker, "extract_image", new=AsyncMock()) as mock_img, \
                 patch.object(extract_worker, "publish_update"):
                asyncio.run(
                    extract_worker.process_message(
                        fake_store,
                        registry,
                        MagicMock(),
                        {
                            "task_id": "task-epdf-2",
                            "page": "1",
                            "stage": "invoice",
                            "input_mode": "text",
                            "text_path": str(text_file),
                            "template_name": "janzour",
                        },
                    )
                )

            mock_text.assert_awaited_once()
            mock_img.assert_not_called()
            fake_store.complete_page.assert_called_once()
            args = fake_store.complete_page.call_args.args
            self.assertEqual(args[0], "task-epdf-2")
            self.assertEqual(args[1], 1)
            self.assertEqual(args[2]["kind"], "invoice")
            self.assertEqual(args[2]["data"]["hospital_name"], "المستشفى الدولي")


if __name__ == "__main__":
    unittest.main()
