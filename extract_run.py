#!/usr/bin/env python3
"""Extract hospital PDF tables -> NuExtract via local vLLM -> merge page JSONs."""

import copy
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pdfplumber
import requests
from openai import OpenAI

try:
    from bidi import get_display
except ImportError:
    from bidi.algorithm import get_display

# ── Config ────────────────────────────────────────────────────────────────────
PDF_PATH = Path(__file__).resolve().parent / "document - 2026-10-28T121905.859.pdf"
VLLM_BASE = "http://127.0.0.1:8000/v1"
MODEL = "Sohailhosseini/NuExtract3-FP8"
N_PAGES = None  # None = all pages; or e.g. 3 for first 3
OUT_DIR = Path(__file__).resolve().parent
TEMPERATURE = 0.0
MAX_TOKENS = 8192
TOTAL_STRATEGY = "later"
USE_BATCH = True  # True: POST /v1/chat/completions/batch (all pages at once)
# ─────────────────────────────────────────────────────────────────────────────

client = OpenAI(api_key="EMPTY", base_url=VLLM_BASE)

# Few-shot ICL examples (NuExtract `developer` role: inputs… + last = expected JSON).
# Outputs use the same field names as extract_template.
FEWSHOT_EXAMPLES = [
    {
        "input": """[HEADER]
المستشفى الدولي CliniSysErp
كشف تفاصيل الخدمات
حالة مستشفى
المريض : مؤيد محمد شلابي شلابي الغرفة : VIRT-10
رقم الدخول : EP260036500 نوع الغرفة : غرفة زوجية
الرقم الطبي : U2600040571 إسم الطبيب : محمد الشيباني بن مصباح / Mohamed Alshebani Ben Mosbah
الجهة : صندوق التأمين الصحي العام ( رسائل فردية ) التخصص : عظام
الاقامة : من 21/09/2026 19:55 إلى 28/09/2026 22:29 الاتفاقية : حالات الرسائل الفردية


[SECTION] اجراءات طبية (عمليات جراحية)
الرمز | البيان | الكمية | نوع العملية | من | الساعة | إلى | صافي السعر | المريض | الجهة
SUR-O03244 | Polytrauma 7 | 1 | عملية كبرى | 22/09/2026 | 19:45 | 22/09/2026 | 75 000.00 | 0.00 | 75 000.00
المبلغ الاجمالي : 75 000.00 0.00 75 000.00

[SECTION] 
 |  |  | 
 | المريض | الجهة | القيمة
الاجمالي | 0.00 | 102 484.79 | 102 484.79
الخدمة الطبية | 0.00 | 0.00 | 0.00
التخفيض | 0.00 | 1 970.00 | 1 970.00
VAT | 0.00 | 0.00 | 0.00
الدمغة | 0.00 | 0.00 | 0.00
الصافي | 0.00 | 100 514.79 | 100 514.79""",
        "output": {
            "hospital_name": "المستشفى الدولي CliniSysErp",
            "document_title": "كشف تفاصيل الخدمات",
            "print_date": None,
            "patient": {
                "name": "مؤيد محمد شلابي شلابي",
                "admission_no": "EP260036500",
                "medical_no": "U2600040571",
                "status": "حالة مستشفى",
            },
            "payer": "صندوق التأمين الصحي العام ( رسائل فردية )",
            "agreement": "حالات الرسائل الفردية",
            "doctor": "محمد الشيباني بن مصباح / Mohamed Alshebani Ben Mosbah",
            "specialty": "عظام",
            "room": {"number": "VIRT-10", "type": "غرفة زوجية"},
            "stay": {
                "from_date": "21/09/2026",
                "from_time": "19:55",
                "to_date": "28/09/2026",
                "to_time": "22:29",
            },
            "sections": [
                {
                    "section_name": "اجراءات طبية ( عمليات جراحية)",
                    "items": [
                        {
                            "code": "SUR-O03244",
                            "description": "Polytrauma 7",
                            "quantity": 1,
                            "date": "22/09/2026",
                            "time": "19:45",
                            "net_price": 75000.0,
                            "patient_share": 0.0,
                            "payer_share": 75000.0,
                        }
                    ],
                    "section_total": {
                        "net_price": 75000.0,
                        "patient_share": 0.0,
                        "payer_share": 75000.0,
                    },
                }
            ],
            "summary": {
                "total": {"value": 102484.79, "payer_share": 102484.79, "patient_share": 0.0},
                "medical_service": {"value": 0.0, "payer_share": 0.0, "patient_share": 0.0},
                "discount": {"value": 1970.0, "payer_share": 1970.0, "patient_share": 0.0},
                "vat": {"value": 0.0, "payer_share": 0.0, "patient_share": 0.0},
                "stamp_duty": {"value": 0.0, "payer_share": 0.0, "patient_share": 0.0},
                "net": {"value": 100514.79, "payer_share": 100514.79, "patient_share": 0.0},
            },
            "run_date": None,
            "user": None,
        },
    },
    {
        "input": """[HEADER]
المستشفى الدولي CliniSysErp
كشف تفاصيل الخدمات
حالة مستشفى
المريض : مؤيد محمد شلابي شلابي الغرفة : VIRT-10
رقم الدخول : EP260036500 نوع الغرفة : غرفة زوجية
الرقم الطبي : U2600040571 إسم الطبيب : محمد الشيباني بن مصباح / Mohamed Alshebani Ben Mosbah
الجهة : صندوق التأمين الصحي العام ( رسائل فردية ) التخصص : عظام
الاقامة : من 21/09/2026 19:55 إلى 28/09/2026 22:29 الاتفاقية : حالات الرسائل الفردية


[SECTION] كشف
الرمز | البيان | الكمية | التاريخ | الساعة | صافي السعر | المريض | الجهة
ER-Co00006 | كشف طوارئ | 1 | 21/09/2026 | 19:55 | 20.00 | 0.00 | 20.00
المبلغ الاجمالي : 20.00 0.00 20.00

[SECTION] اقامة مريض
الرمز | البيان | الكمية | التاريخ | الساعة | صافي السعر | المريض | الجهة
ACC0000062 | غرفة زوجية | 1 | 21/09/2026 | 19:59 | 275.00 | 0.00 | 275.00
ACC0000062 | غرفة زوجية | 1 | 22/09/2026 | 0:25 | 275.00 | 0.00 | 275.00
ACC0000071 | سرير عناية فائقة | 1 | 23/09/2026 | 3:25 | 1 000.00 | 0.00 | 1 000.00
ACC0000062 | غرفة زوجية | 1 | 24/09/2026 | 0:25 | 275.00 | 0.00 | 275.00
ACC0000062 | غرفة زوجية | 1 | 25/09/2026 | 0:25 | 275.00 | 0.00 | 275.00
ACC0000062 | غرفة زوجية | 1 | 26/09/2026 | 0:25 | 275.00 | 0.00 | 275.00
ACC0000062 | غرفة زوجية | 1 | 27/09/2026 | 0:25 | 275.00 | 0.00 | 275.00
ACC0000062 | غرفة زوجية | 1 | 28/09/2026 | 0:25 | 275.00 | 0.00 | 275.00
MS00000606 | خدمة اعاشه | 7 | 28/09/2026 | 22:00 | 1 155.00 | 0.00 | 1 155.00
المبلغ الاجمالي : 4 080.00 0.00 4 080.00

[SECTION] الاشراف الطبي
الرمز | البيان | الكمية | التاريخ | الساعة | صافي السعر | المريض | الجهة
MCA0000060 | خدمات تمريض | 1 | 21/09/2026 | 19:59 | 40.00 | 0.00 | 40.00
MCA0000060 | خدمات تمريض | 1 | 22/09/2026 | 0:25 | 40.00 | 0.00 | 40.00
MCA0000062 | خدمات تمريض عناية | 1 | 23/09/2026 | 3:25 | 180.00 | 0.00 | 180.00
MCA0000060 | خدمات تمريض | 1 | 24/09/2026 | 0:25 | 40.00 | 0.00 | 40.00
MCA0000060 | خدمات تمريض | 1 | 25/09/2026 | 0:25 | 40.00 | 0.00 | 40.00
MCA0000060 | خدمات تمريض | 1 | 26/09/2026 | 0:25 | 40.00 | 0.00 | 40.00
MCA0000060 | خدمات تمريض | 1 | 27/09/2026 | 0:25 | 40.00 | 0.00 | 40.00
MCA0000060 | خدمات تمريض | 1 | 28/09/2026 | 0:25 | 40.00 | 0.00 | 40.00
المبلغ الاجمالي : 460.00 0.00 460.00

[SECTION] تحاليل طبية""",
        "output": {
            "hospital_name": "المستشفى الدولي CliniSysErp",
            "document_title": "كشف تفاصيل الخدمات",
            "print_date": None,
            "patient": {
                "name": "مؤيد محمد شلابي شلابي",
                "admission_no": "EP260036500",
                "medical_no": "U2600040571",
                "status": "حالة مستشفى",
            },
            "payer": "صندوق التأمين الصحي العام ( رسائل فردية )",
            "agreement": "حالات الرسائل الفردية",
            "doctor": "محمد الشيباني بن مصباح / Mohamed Alshebani Ben Mosbah",
            "specialty": "عظام",
            "room": {"number": "VIRT-10", "type": "غرفة زوجية"},
            "stay": {
                "from_date": "21/09/2026",
                "from_time": "19:55",
                "to_date": "28/09/2026",
                "to_time": "22:29",
            },
            "sections": [
                {
                    "section_name": "كشف",
                    "items": [
                        {
                            "code": "ER-Co00006",
                            "description": "كشف طوارئ",
                            "quantity": 1,
                            "date": "21/09/2026",
                            "time": "19:55",
                            "net_price": 20.0,
                            "patient_share": 0.0,
                            "payer_share": 20.0,
                        }
                    ],
                    "section_total": {
                        "net_price": 20.0,
                        "patient_share": 0.0,
                        "payer_share": 20.0,
                    },
                },
                {
                    "section_name": "اقامة مريض",
                    "items": [
                        {
                            "code": "ACC0000062",
                            "description": "غرفة زوجية",
                            "quantity": 1,
                            "date": "21/09/2026",
                            "time": "19:59",
                            "net_price": 275.0,
                            "patient_share": 0.0,
                            "payer_share": 275.0,
                        },
                        {
                            "code": "ACC0000062",
                            "description": "غرفة زوجية",
                            "quantity": 1,
                            "date": "22/09/2026",
                            "time": "0:25",
                            "net_price": 275.0,
                            "patient_share": 0.0,
                            "payer_share": 275.0,
                        },
                        {
                            "code": "ACC0000071",
                            "description": "سرير عناية فائقة",
                            "quantity": 1,
                            "date": "23/09/2026",
                            "time": "3:25",
                            "net_price": 1000.0,
                            "patient_share": 0.0,
                            "payer_share": 1000.0,
                        },
                        {
                            "code": "ACC0000062",
                            "description": "غرفة زوجية",
                            "quantity": 1,
                            "date": "24/09/2026",
                            "time": "0:25",
                            "net_price": 275.0,
                            "patient_share": 0.0,
                            "payer_share": 275.0,
                        },
                        {
                            "code": "ACC0000062",
                            "description": "غرفة زوجية",
                            "quantity": 1,
                            "date": "25/09/2026",
                            "time": "0:25",
                            "net_price": 275.0,
                            "patient_share": 0.0,
                            "payer_share": 275.0,
                        },
                        {
                            "code": "ACC0000062",
                            "description": "غرفة زوجية",
                            "quantity": 1,
                            "date": "26/09/2026",
                            "time": "0:25",
                            "net_price": 275.0,
                            "patient_share": 0.0,
                            "payer_share": 275.0,
                        },
                        {
                            "code": "ACC0000062",
                            "description": "غرفة زوجية",
                            "quantity": 1,
                            "date": "27/09/2026",
                            "time": "0:25",
                            "net_price": 275.0,
                            "patient_share": 0.0,
                            "payer_share": 275.0,
                        },
                        {
                            "code": "ACC0000062",
                            "description": "غرفة زوجية",
                            "quantity": 1,
                            "date": "28/09/2026",
                            "time": "0:25",
                            "net_price": 275.0,
                            "patient_share": 0.0,
                            "payer_share": 275.0,
                        },
                        {
                            "code": "MS00000606",
                            "description": "خدمة اعاشه",
                            "quantity": 7,
                            "date": "28/09/2026",
                            "time": "22:00",
                            "net_price": 1155.0,
                            "patient_share": 0.0,
                            "payer_share": 1155.0,
                        },
                    ],
                    "section_total": {
                        "net_price": 4080.0,
                        "patient_share": 0.0,
                        "payer_share": 4080.0,
                    },
                },
                {
                    "section_name": "الاشراف الطبي",
                    "items": [
                        {
                            "code": "MCA0000060",
                            "description": "خدمات تمريض",
                            "quantity": 1,
                            "date": "21/09/2026",
                            "time": "19:59",
                            "net_price": 40.0,
                            "patient_share": 0.0,
                            "payer_share": 40.0,
                        },
                        {
                            "code": "MCA0000060",
                            "description": "خدمات تمريض",
                            "quantity": 1,
                            "date": "22/09/2026",
                            "time": "0:25",
                            "net_price": 40.0,
                            "patient_share": 0.0,
                            "payer_share": 40.0,
                        },
                        {
                            "code": "MCA0000062",
                            "description": "خدمات تمريض عناية",
                            "quantity": 1,
                            "date": "23/09/2026",
                            "time": "3:25",
                            "net_price": 180.0,
                            "patient_share": 0.0,
                            "payer_share": 180.0,
                        },
                        {
                            "code": "MCA0000060",
                            "description": "خدمات تمريض",
                            "quantity": 1,
                            "date": "24/09/2026",
                            "time": "0:25",
                            "net_price": 40.0,
                            "patient_share": 0.0,
                            "payer_share": 40.0,
                        },
                        {
                            "code": "MCA0000060",
                            "description": "خدمات تمريض",
                            "quantity": 1,
                            "date": "25/09/2026",
                            "time": "0:25",
                            "net_price": 40.0,
                            "patient_share": 0.0,
                            "payer_share": 40.0,
                        },
                        {
                            "code": "MCA0000060",
                            "description": "خدمات تمريض",
                            "quantity": 1,
                            "date": "26/09/2026",
                            "time": "0:25",
                            "net_price": 40.0,
                            "patient_share": 0.0,
                            "payer_share": 40.0,
                        },
                        {
                            "code": "MCA0000060",
                            "description": "خدمات تمريض",
                            "quantity": 1,
                            "date": "27/09/2026",
                            "time": "0:25",
                            "net_price": 40.0,
                            "patient_share": 0.0,
                            "payer_share": 40.0,
                        },
                        {
                            "code": "MCA0000060",
                            "description": "خدمات تمريض",
                            "quantity": 1,
                            "date": "28/09/2026",
                            "time": "0:25",
                            "net_price": 40.0,
                            "patient_share": 0.0,
                            "payer_share": 40.0,
                        },
                    ],
                    "section_total": {
                        "net_price": 460.0,
                        "patient_share": 0.0,
                        "payer_share": 460.0,
                    },
                },
                {
                    "section_name": "تحاليل طبية",
                    "items": [],
                    "section_total": {
                        "net_price": None,
                        "patient_share": None,
                        "payer_share": None,
                    },
                },
            ],
            "summary": {
                "total": {"value": None, "payer_share": None, "patient_share": None},
                "medical_service": {"value": None, "payer_share": None, "patient_share": None},
                "discount": {"value": None, "payer_share": None, "patient_share": None},
                "vat": {"value": None, "payer_share": None, "patient_share": None},
                "stamp_duty": {"value": None, "payer_share": None, "patient_share": None},
                "net": {"value": None, "payer_share": None, "patient_share": None},
            },
            "run_date": "28/10/2026",
            "user": "محمد.الديب",
        },
    },
]


def build_messages(text: str) -> list[dict]:
    """NuExtract messages: few-shot developer turns + user document."""
    messages = []
    for ex in FEWSHOT_EXAMPLES:
        messages.append(
            {
                "role": "developer",
                "content": [
                    {"type": "text", "text": ex["input"]},
                    {
                        "type": "text",
                        "text": json.dumps(ex["output"], ensure_ascii=False),
                    },
                ],
            }
        )
    messages.append(
        {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        }
    )
    return messages

AR = re.compile(r"[\u0600-\u06FF]")
LAT = re.compile(r"[A-Za-z]")
NUM_COLS = ["الكمية", "سعر الوحدة", "صافي السعر", "المريض", "الجهة"]
SKIP_KEYS = ("sections", "summary", "page")

extract_template = {
    "hospital_name": "verbatim-string",
    "document_title": "verbatim-string",
    "print_date": "verbatim-string",
    "patient": {
        "name": "verbatim-string",
        "admission_no": "verbatim-string",
        "medical_no": "verbatim-string",
        "status": "verbatim-string",
    },
    "payer": "verbatim-string",
    "agreement": "verbatim-string",
    "doctor": "verbatim-string",
    "specialty": "verbatim-string",
    "room": {"number": "verbatim-string", "type": "verbatim-string"},
    "stay": {
        "from_date": "verbatim-string",
        "from_time": "verbatim-string",
        "to_date": "verbatim-string",
        "to_time": "verbatim-string",
    },
    "sections": [
        {
            "section_name": "verbatim-string",
            "items": [
                {
                    "code": "verbatim-string",
                    "description": "verbatim-string",
                    "quantity": 0,
                    "date": "verbatim-string",
                    "time": "verbatim-string",
                    "net_price": 0.0,
                    "patient_share": 0.0,
                    "payer_share": 0.0,
                }
            ],
            "section_total": {
                "net_price": 0.0,
                "patient_share": 0.0,
                "payer_share": 0.0,
            },
        }
    ],
    "summary": {
        "total": {"value": 0.0, "payer_share": 0.0, "patient_share": 0.0},
        "medical_service": {"value": 0.0, "payer_share": 0.0, "patient_share": 0.0},
        "discount": {"value": 0.0, "payer_share": 0.0, "patient_share": 0.0},
        "vat": {"value": 0.0, "payer_share": 0.0, "patient_share": 0.0},
        "stamp_duty": {"value": 0.0, "payer_share": 0.0, "patient_share": 0.0},
        "net": {"value": 0.0, "payer_share": 0.0, "patient_share": 0.0},
    },
    "run_date": "verbatim-string",
    "user": "verbatim-string",
}


def fix_line(s: str) -> str:
    return get_display(s) if AR.search(s) else s


def fix_cell(c) -> str:
    if not c:
        return ""
    return " ".join(fix_line(l.strip()) for l in c.split("\n") if l.strip())


def rtl_words(ws):
    out, run = [], []
    for w in ws:
        t = w["text"]
        if LAT.search(t) and not AR.search(t):
            run.append(t)
        else:
            out.extend(reversed(run))
            run = []
            out.append(t)
    out.extend(reversed(run))
    return out


def header_lines(page, y_limit):
    crop = page.crop((0, 0, page.width, y_limit))
    rows = {}
    for w in crop.extract_words():
        rows.setdefault(round(w["top"] / 5), []).append(w)
    out = []
    for k in sorted(rows):
        ws = sorted(rows[k], key=lambda w: -w["x0"])
        line = " ".join(fix_line(t) for t in rtl_words(ws))
        line = line.replace("مددد", "محمد")
        if out and LAT.search(line) and not AR.search(line):
            out[-1] += " " + line
        else:
            out.append(line)
    return out


def section_title(page, top, prev_bottom):
    y0 = prev_bottom if prev_bottom is not None else max(top - 40, 0)
    if top - y0 < 5:
        return ""
    txt = page.crop((0, y0, page.width, top)).extract_text() or ""
    return " ".join(fix_line(l.strip()) for l in txt.splitlines() if l.strip())


def table_to_records(rows):
    if rows and rows[0] and rows[0][0] == "الرمز":
        keys = rows[0]
        return [dict(zip(keys, r)) for r in rows[1:]]
    return rows


def extract_pdf(pdf_path: Path) -> dict:
    result = {"header": [], "sections": []}
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            tables = sorted(page.find_tables(), key=lambda t: t.bbox[1])
            if not tables:
                continue
            if pno == 1:
                result["header"] = header_lines(page, tables[0].bbox[1] - 40)
            prev_bottom = None
            for t in tables:
                rows = [[fix_cell(c) for c in reversed(r)] for r in t.extract()]
                result["sections"].append(
                    {
                        "page": pno,
                        "title": section_title(page, t.bbox[1], prev_bottom),
                        "rows": table_to_records(rows),
                    }
                )
                prev_bottom = t.bbox[3]
    return result


def is_total(r):
    return not r.get("الرمز") and any(
        ("الاجمالي" in str(v) or "الإجمالي" in str(v)) for v in r.values()
    )


def rows_to_lines(rows):
    if not rows:
        return []
    if isinstance(rows[0], list):
        return [" | ".join(r) for r in rows]
    cols = list(rows[0].keys())
    lines = [" | ".join(cols)]
    for r in rows:
        if is_total(r):
            continue
        lines.append(" | ".join(str(r.get(c, "")) for c in cols))
    return lines


def page_text(res, pno):
    out = ["[HEADER]", *res["header"], "", f"=== PAGE {pno} ==="]
    for s in res["sections"]:
        if s["page"] == pno:
            out += ["", f"[SECTION] {s['title']}", *rows_to_lines(s["rows"])]
    return "\n".join(out)


def safe_parse_json(output_str: str) -> dict:
    cleaned = output_str.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    # Strip thinking blocks if present
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned).strip()
    cleaned = re.sub(r"<reasoning>[\s\S]*?</reasoning>", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # try to find outermost JSON object
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return {}
        return {}


def _chat_payload(text: str, template_str: str) -> dict:
    return {
        "model": MODEL,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "messages": build_messages(text),
        "chat_template_kwargs": {
            "template": template_str,
            "enable_thinking": False,
        },
    }


def run_nuextract(text: str, template_str: str) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        messages=build_messages(text),
        extra_body={
            "chat_template_kwargs": {
                "template": template_str,
                "enable_thinking": False,
            }
        },
    )
    return (response.choices[0].message.content or "").strip()


def run_nuextract_batch(page_texts: list[tuple[int, str]], template_str: str) -> tuple[list[dict], float]:
    """Send all pages in one /v1/chat/completions/batch call.

    Returns ([{page, raw, error?}, ...], wall_seconds).
    Batch API returns one overall wall time; per-page latency is not exposed by the server.
    """
    messages_batch = [build_messages(text) for _, text in page_texts]
    body = {
        "model": MODEL,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "messages": messages_batch,
        "chat_template_kwargs": {
            "template": template_str,
            "enable_thinking": False,
        },
    }
    t0 = time.perf_counter()
    resp = requests.post(
        f"{VLLM_BASE}/chat/completions/batch",
        json=body,
        timeout=600,
    )
    wall = time.perf_counter() - t0
    resp.raise_for_status()
    data = resp.json()

    # vLLM batch returns one ChatCompletion with choices[i] = result for messages[i]
    if not isinstance(data, dict) or "choices" not in data:
        raise RuntimeError(f"Unexpected batch response shape: {type(data)} keys={list(data)[:20] if isinstance(data, dict) else None}")

    choices = sorted(data["choices"], key=lambda c: c.get("index", 0))
    if len(choices) != len(page_texts):
        raise RuntimeError(f"Batch size mismatch: sent {len(page_texts)} pages, got {len(choices)} choices")

    results = []
    for (pno, _), choice in zip(page_texts, choices):
        try:
            raw = ((choice.get("message") or {}).get("content") or "").strip()
        except (AttributeError, TypeError) as e:
            results.append({"page": pno, "raw": "", "error": f"bad choice: {e}"})
            continue
        results.append({"page": pno, "raw": raw})
    return results, wall


def run_nuextract_concurrent(page_texts: list[tuple[int, str]], template_str: str) -> tuple[list[dict], float]:
    """Fire all pages at once via thread pool; record per-page and wall time."""

    def _one(pno: int, text: str):
        t0 = time.perf_counter()
        try:
            raw = run_nuextract(text, template_str)
            return {"page": pno, "raw": raw, "seconds": time.perf_counter() - t0}
        except Exception as e:
            return {"page": pno, "raw": "", "error": str(e), "seconds": time.perf_counter() - t0}

    t0 = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=len(page_texts) or 1) as ex:
        futs = [ex.submit(_one, pno, text) for pno, text in page_texts]
        for fut in as_completed(futs):
            results.append(fut.result())
    wall = time.perf_counter() - t0
    results.sort(key=lambda r: r["page"])
    return results, wall


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def clean_section_name(name) -> str:
    """Strip trailing prices/totals the model sometimes glues onto section titles.

    e.g. 'أدوية و مستلزمات طبية 0.00' -> 'أدوية و مستلزمات طبية'
         'كشف 20.00 0.00 20.00'       -> 'كشف'
    """
    s = _norm(name)
    if not s:
        return s
    # drop trailing runs of numbers / decimals / spaced thousands (75 000.00)
    s = re.sub(
        r"(?:\s*[\d]+(?:\s[\d]{3})*(?:[.,]\d+)?)+$",
        "",
        s,
    ).strip()
    # also drop a lone trailing decimal like '.00' leftovers
    s = re.sub(r"[\s.:_\-]+$", "", s).strip()
    return s


def sanitize_page_data(data: dict) -> dict:
    """Normalize model JSON before merge (section names, etc.)."""
    if not isinstance(data, dict):
        return data
    out = copy.deepcopy(data)
    for sec in out.get("sections") or []:
        if isinstance(sec, dict) and "section_name" in sec:
            sec["section_name"] = clean_section_name(sec.get("section_name"))
    return out


def _body(page):
    raw = page["data"] if isinstance(page.get("data"), dict) else page
    return sanitize_page_data(raw) if isinstance(raw, dict) else raw


def _doc_key(b):
    p = b.get("patient") or {}
    return _norm(p.get("admission_no")), _norm(p.get("medical_no"))


def _merge_dicts(old, new):
    if old is None:
        return copy.deepcopy(new)
    if new is None:
        return copy.deepcopy(old)
    if isinstance(old, dict) and isinstance(new, dict):
        out = copy.deepcopy(old)
        for k, v in new.items():
            out[k] = _merge_dicts(out.get(k), v) if k in out else copy.deepcopy(v)
        return out
    return copy.deepcopy(new)


def _merge_totals(old, new):
    if TOTAL_STRATEGY == "later" or old is None or new is None:
        return copy.deepcopy(new if new is not None else old)
    out = copy.deepcopy(new)
    for k, v in new.items():
        if isinstance(v, (int, float)) and isinstance(old.get(k), (int, float)):
            out[k] = round(old[k] + v, 2)
    return out


def _merge_section(a, b):
    out = copy.deepcopy(a)
    out["items"] = (a.get("items") or []) + copy.deepcopy(b.get("items") or [])
    if "section_total" in a or "section_total" in b:
        out["section_total"] = _merge_totals(a.get("section_total"), b.get("section_total"))
    if "summary" in a or "summary" in b:
        out["summary"] = _merge_dicts(a.get("summary"), b.get("summary"))
    return out


def _append_sections(doc, sections):
    for sec in sections:
        sec = copy.deepcopy(sec)
        if isinstance(sec, dict):
            sec["section_name"] = clean_section_name(sec.get("section_name"))
        last = doc["sections"][-1] if doc["sections"] else None
        # Fuse whenever consecutive sections share a cleaned name (cross-page + within-page)
        if last and _norm(last.get("section_name")) == _norm(sec.get("section_name")):
            doc["sections"][-1] = _merge_section(last, sec)
        else:
            doc["sections"].append(sec)


def merge_pages(pages):
    docs = {}
    for page in pages:
        b = _body(page)
        key = _doc_key(b)
        pg = page.get("page")

        if key not in docs:
            doc = {k: copy.deepcopy(v) for k, v in b.items() if k not in SKIP_KEYS}
            doc["pages"] = [pg] if pg is not None else []
            doc["sections"] = []
            _append_sections(doc, b.get("sections") or [])
            if b.get("summary") is not None:
                doc["summary"] = copy.deepcopy(b["summary"])
            docs[key] = doc
            continue

        doc = docs[key]
        if pg is not None:
            doc["pages"].append(pg)
        for k, v in b.items():
            if k in SKIP_KEYS:
                continue
            if doc.get(k) in (None, "", {}) and v not in (None, "", {}):
                doc[k] = copy.deepcopy(v)
            elif isinstance(doc.get(k), dict) and isinstance(v, dict):
                doc[k] = {
                    **v,
                    **{kk: vv for kk, vv in doc[k].items() if vv not in (None, "")},
                }
        _append_sections(doc, b.get("sections") or [])
        if b.get("summary") is not None:
            doc["summary"] = _merge_dicts(doc.get("summary"), b["summary"])
    return list(docs.values())


def main():
    print(f"PDF: {PDF_PATH}")
    print(f"Model: {MODEL} @ {VLLM_BASE} | temperature={TEMPERATURE} | batch={USE_BATCH}")

    t_pdf0 = time.perf_counter()
    result = extract_pdf(PDF_PATH)
    extract_secs = time.perf_counter() - t_pdf0
    with open(OUT_DIR / "extracted.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    pages = sorted({s["page"] for s in result["sections"]})
    if N_PAGES is not None:
        pages = pages[:N_PAGES]
    print(f"{len(result['sections'])} tables across pages {pages}")
    print(f"PDF text extract: {extract_secs:.2f}s")

    template_str = json.dumps(extract_template, indent=4)
    page_texts = [(pno, page_text(result, pno)) for pno in pages]
    for pno, text in page_texts:
        print(f"  page {pno}: {len(text):,} chars")

    t_infer0 = time.perf_counter()
    if USE_BATCH:
        print(f"\n>>> Sending {len(page_texts)} pages in ONE batch request...")
        try:
            batch_results, batch_wall = run_nuextract_batch(page_texts, template_str)
        except Exception as e:
            print(f"Batch endpoint failed ({e}); falling back to concurrent single requests...")
            batch_results, batch_wall = run_nuextract_concurrent(page_texts, template_str)
    else:
        print(f"\n>>> Sending {len(page_texts)} pages concurrently...")
        batch_results, batch_wall = run_nuextract_concurrent(page_texts, template_str)
    infer_wall = time.perf_counter() - t_infer0

    # If batch API gave no per-page seconds, approximate evenly (note in report)
    has_per_page = any("seconds" in r for r in batch_results)
    n = max(len(batch_results), 1)
    page_outputs = []
    timing_rows = []

    print("\n" + "=" * 60)
    print("PER-PAGE RESULTS")
    print("=" * 60)
    for r in sorted(batch_results, key=lambda x: x["page"]):
        pno = r["page"]
        raw = r.get("raw") or ""
        err = r.get("error")
        secs = r.get("seconds")
        if secs is None:
            # batch API: only wall known; store share of wall for reporting
            secs = batch_wall / n
            secs_note = "approx (batch wall / n)"
        else:
            secs_note = "measured"
        parsed = {} if err else safe_parse_json(raw)
        ok = bool(parsed)
        print(f"PAGE {pno:2d} | {secs:7.2f}s ({secs_note}) | parsed={ok} | raw_chars={len(raw)}"
              + (f" | ERROR: {err}" if err else ""))
        if raw and not err:
            print(raw[:500] + ("..." if len(raw) > 500 else ""))
            print("-" * 40)
        page_outputs.append({
            "page": pno,
            "data": parsed,
            "raw": raw,
            "seconds": round(secs, 3),
            "seconds_note": secs_note,
            "error": err,
        })
        timing_rows.append({
            "page": pno,
            "seconds": round(secs, 3),
            "seconds_note": secs_note,
            "raw_chars": len(raw),
            "parsed_ok": ok,
            "error": err,
        })

    total_pdf_secs = time.perf_counter() - t_pdf0
    timing_report = {
        "temperature": TEMPERATURE,
        "model": MODEL,
        "mode": "batch" if USE_BATCH else "concurrent",
        "pages": pages,
        "pdf_text_extract_seconds": round(extract_secs, 3),
        "inference_wall_seconds": round(batch_wall, 3),
        "inference_wall_incl_overhead_seconds": round(infer_wall, 3),
        "overall_pdf_seconds": round(total_pdf_secs, 3),
        "sum_per_page_seconds": round(sum(t["seconds"] for t in timing_rows), 3),
        "per_page": timing_rows,
        "note": (
            "Batch API returns when all pages finish; per-page times are "
            "wall/n unless concurrent mode measured each request."
            if not has_per_page
            else "Per-page times are individual request latencies; wall is parallel elapsed."
        ),
    }

    print("\n" + "=" * 60)
    print("TIMING SUMMARY")
    print("=" * 60)
    print(f"  PDF text extract : {timing_report['pdf_text_extract_seconds']:.2f}s")
    print(f"  Inference wall   : {timing_report['inference_wall_seconds']:.2f}s  ({len(pages)} pages)")
    print(f"  Sum per-page     : {timing_report['sum_per_page_seconds']:.2f}s")
    print(f"  OVERALL PDF      : {timing_report['overall_pdf_seconds']:.2f}s")
    for t in timing_rows:
        print(f"    page {t['page']}: {t['seconds']:.2f}s  [{t['seconds_note']}]  parsed={t['parsed_ok']}")

    with open(OUT_DIR / "page_outputs.json", "w", encoding="utf-8") as f:
        json.dump(page_outputs, f, ensure_ascii=False, indent=2)
    with open(OUT_DIR / "timings.json", "w", encoding="utf-8") as f:
        json.dump(timing_report, f, ensure_ascii=False, indent=2)

    merge_input = [{"page": p["page"], "data": p["data"]} for p in page_outputs if p.get("data")]
    merged = merge_pages(merge_input)
    out_path = OUT_DIR / "merged.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    for d in merged:
        patient = d.get("patient") or {}
        print(
            f"{patient.get('admission_no')} / {patient.get('medical_no')}: "
            f"pages {d.get('pages')} -> {len(d.get('sections') or [])} sections, "
            f"{sum(len(s.get('items') or []) for s in (d.get('sections') or []))} items, "
            f"summary={'yes' if d.get('summary') else 'no'}"
        )
    print("saved ->", out_path)
    print("page_outputs ->", OUT_DIR / "page_outputs.json")
    print("timings ->", OUT_DIR / "timings.json")


if __name__ == "__main__":
    main()
