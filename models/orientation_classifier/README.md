# ID Card Orientation Classifier Model

## 1. Overview
* **Model File:** `best.pt` (3.0 MB)
* **Architecture:** YOLO11 Nano Classifier (`yolo11n-cls`)
* **Task:** `classify`
* **Purpose:** Takes a cropped ID card and classifies its orientation angle into one of 4 discrete classes ($0^\circ, 90^\circ, 180^\circ, 270^\circ$). Enables downstream auto-rotation to true upright ($0^\circ$) before OCR or face recognition.
* **Accuracy:** **100.0% Top-1 Accuracy** across 192 unseen validation test cards.
* **Latency:** **~1.7 ms / image** on CPU.

---

## 2. Input Specifications
* **Input Type:** Cropped image of an ID card (`.jpg`, `.jpeg`, `.png`, NumPy array).
* **Target Resolution:** $224 \times 224$ pixels (Ultralytics handles resizing and normalization automatically).
* **Color Channels:** 3 (RGB or BGR).
* **Expected Input:** A tight or semi-tight crop of a single ID card (typically produced by `models/id_card_detection/best.pt`).

---

## 3. Output Specifications
The model outputs a probability distribution across the 4 classes and returns the Top-1 predicted angle:

| Class Key | Meaning | Physical Rotation Required to Make Upright ($0^\circ$) |
| :---: | :--- | :--- |
| **`"0"`** | Card is **Upright** ($0^\circ$) | No rotation needed (`img`) |
| **`"90"`** | Card is rotated **$90^\circ$ Clockwise** | Rotate **$90^\circ$ Counter-Clockwise** (`cv2.ROTATE_90_COUNTERCLOCKWISE`) |
| **`"180"`** | Card is **Upside-Down** ($180^\circ$) | Rotate **$180^\circ$** (`cv2.ROTATE_180`) |
| **`"270"`** | Card is rotated **$270^\circ$ CW** ($90^\circ$ CCW) | Rotate **$90^\circ$ Clockwise** (`cv2.ROTATE_90_CLOCKWISE`) |

### Output Accessors (Python Ultralytics):
```python
results[0].probs.top1       # Class index (0, 1, 2, or 3)
results[0].probs.top1conf   # Confidence score (e.g., 0.998)
model.names[top1_idx]       # Predicted angle string: '0', '90', '180', or '270'
```

---

## 4. Usage Example & Auto-Rotation (Python)

```python
from ultralytics import YOLO
import cv2

model = YOLO("models/orientation_classifier/best.pt")

def rotate_to_upright(img, predicted_angle: str):
    if predicted_angle == "0":
        return img
    elif predicted_angle == "90":
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    elif predicted_angle == "180":
        return cv2.rotate(img, cv2.ROTATE_180)
    elif predicted_angle == "270":
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    return img

# Load cropped card
crop = cv2.imread("path/to/cropped_id_card.jpg")

# Predict orientation
results = model.predict(source=crop, imgsz=224, verbose=False)
top1_idx = results[0].probs.top1
angle = model.names[top1_idx]
conf = float(results[0].probs.top1conf.item())

print(f"Predicted Orientation: {angle}° (Confidence: {conf*100:.1f}%)")

# Correct rotation to upright (0°)
upright_card = rotate_to_upright(crop, angle)
cv2.imwrite("card_upright.jpg", upright_card)
```

---

## 5. Pipeline Role
This model acts as **Step 2** of the pipeline:
$$\text{Cropped ID Card} \xrightarrow{\textbf{Orientation Classifier}} \text{Angle } \{0^\circ, 90^\circ, 180^\circ, 270^\circ\} \xrightarrow{\text{Rotate}} \text{Upright Card } (0^\circ)$$
The normalized upright card is ready for OCR (Tesseract / PaddleOCR / EasyOCR), MRZ reading, or facial biometric extraction.

