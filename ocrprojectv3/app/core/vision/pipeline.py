"""ID-card detection, orientation, and the top-half classification crop."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from app.config import settings


_ID_CLASS = "id card"
_PASSPORT_CLASS = "passport"


def rotate_to_upright(image: np.ndarray, predicted_angle: str) -> np.ndarray:
    if predicted_angle == "90":
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if predicted_angle == "180":
        return cv2.rotate(image, cv2.ROTATE_180)
    if predicted_angle == "270":
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    return image


def top_half(image: np.ndarray) -> np.ndarray:
    height = image.shape[0]
    if height < 2:
        return image
    return image[: height // 2, :]


class VisionModels:
    def __init__(self):
        self.detector = YOLO(settings.ID_MODEL_PATH)
        self.orientation = YOLO(settings.ORIENT_MODEL_PATH)

    def detect(self, image: np.ndarray) -> tuple[list[tuple[int, int, int, int]], bool]:
        """Return id-card boxes and whether any passport box was present."""
        results = self.detector.predict(
            source=image,
            conf=settings.ID_DETECT_CONF,
            imgsz=settings.ID_DETECT_IMGSZ,
            verbose=False,
        )
        id_boxes: list[tuple[int, int, int, int]] = []
        saw_passport = False
        boxes = results[0].boxes
        if boxes is None:
            return id_boxes, saw_passport
        for box in boxes:
            class_id = int(box.cls[0].item())
            class_name = str(self.detector.names.get(class_id, class_id))
            x1, y1, x2, y2 = (int(value) for value in box.xyxy[0].tolist())
            if class_name == _ID_CLASS or class_id == 0:
                id_boxes.append((x1, y1, x2, y2))
            elif class_name == _PASSPORT_CLASS or class_id == 1:
                saw_passport = True
        return id_boxes, saw_passport

    def upright(self, image: np.ndarray) -> np.ndarray:
        results = self.orientation.predict(
            source=image,
            imgsz=settings.ORIENT_IMGSZ,
            verbose=False,
        )
        top1 = int(results[0].probs.top1)
        angle = str(self.orientation.names[top1])
        return rotate_to_upright(image, angle)


def read_bgr(path: str | Path) -> np.ndarray:
    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(path)
    return image


def write_bgr(path: str | Path, image: np.ndarray) -> str:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), image):
        raise OSError(f"Could not write {destination}")
    return str(destination)


def crop_box(image: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    x1 = max(0, min(width - 1, x1))
    x2 = max(x1 + 1, min(width, x2))
    y1 = max(0, min(height - 1, y1))
    y2 = max(y1 + 1, min(height, y2))
    return image[y1:y2, x1:x2]
