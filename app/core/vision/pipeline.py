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


def skew_angle(gray: np.ndarray, max_deg: float = 10, step: float = 0.2) -> float:
    """Angle, in degrees, that makes text rows horizontal.

    Search a downscaled binarized copy. The projection-profile variance peaks
    when the row sums have the sharpest peaks and valleys.
    """
    height, width = gray.shape
    margin_y, margin_x = int(height * 0.04), int(width * 0.04)
    cropped = gray[margin_y:height - margin_y, margin_x:width - margin_x]
    if max(cropped.shape) > 2000:
        cropped = cv2.resize(cropped, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    binary = cv2.threshold(cropped, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    crop_h, crop_w = binary.shape
    best, best_score = 0.0, -1.0
    for angle in np.arange(-max_deg, max_deg + step, step):
        matrix = cv2.getRotationMatrix2D((crop_w / 2, crop_h / 2), float(angle), 1.0)
        rotated = cv2.warpAffine(binary, matrix, (crop_w, crop_h), flags=cv2.INTER_NEAREST)
        score = float(np.var(rotated.sum(axis=1, dtype=np.float64)))
        if score > best_score:
            best, best_score = float(angle), score
    return best


def _rotation_canvas(width: int, height: int, angle: float) -> tuple[np.ndarray, int, int]:
    """Rotation that keeps the whole page inside the frame.

    The extra margin is white. Rotating inside the original size clips the corners.
    """
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    radians = np.deg2rad(abs(angle))
    cosine, sine = float(np.cos(radians)), float(np.sin(radians))
    bound_w = int(np.ceil(height * sine + width * cosine))
    bound_h = int(np.ceil(height * cosine + width * sine))
    matrix[0, 2] += (bound_w - width) / 2
    matrix[1, 2] += (bound_h - height) / 2
    return matrix, bound_w, bound_h


def deskew(image: np.ndarray, min_deg: float = 0.3) -> tuple[np.ndarray, float]:
    """Rotate the full-resolution image once by the angle found on a downscaled copy.

    Angles below min_deg are left alone so the interpolation does not blur the page.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    angle = skew_angle(gray)
    if abs(angle) < min_deg:
        return image, angle
    height, width = gray.shape
    matrix, bound_w, bound_h = _rotation_canvas(width, height, angle)
    corrected = cv2.warpAffine(
        image, matrix, (bound_w, bound_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    return corrected, angle


CLASSIFY_TOP_FRACTION = 0.40


def top_crop(image: np.ndarray, fraction: float = CLASSIFY_TOP_FRACTION) -> np.ndarray:
    height = image.shape[0]
    if height < 2:
        return image
    cut = max(1, min(height, round(height * fraction)))
    return image[:cut, :]


def top_half(image: np.ndarray) -> np.ndarray:
    return top_crop(image, 0.5)


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
