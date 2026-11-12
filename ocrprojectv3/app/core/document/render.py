"""Render each PDF page to a 300 DPI PNG."""
from __future__ import annotations

import os
from pathlib import Path

import fitz


def extract_images_from_pdf(pdf_path: str, output_dir: str) -> list[str]:
    os.makedirs(output_dir, exist_ok=True)
    document = fitz.open(pdf_path)
    image_paths: list[str] = []
    try:
        for page_num in range(len(document)):
            page = document[page_num]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
            image_path = os.path.join(output_dir, f"page_{page_num + 1}.png")
            pixmap.save(image_path)
            image_paths.append(image_path)
    finally:
        document.close()
    return image_paths


def page_dir(task_id: str) -> Path:
    from app.config import settings
    path = Path(settings.STORAGE_DIR) / "pages" / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path
