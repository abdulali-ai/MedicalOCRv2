# ID Card & Passport Detection Model

## 1. Overview
* **Model File:** `best.pt` (6.2 MB)
* **Architecture:** YOLOv8 Nano (`yolov8n`) Object Detection
* **Task:** `detect`
* **Purpose:** Detects and localizes Libyan ID cards and passports within full-page scanned documents, medical clinic intake forms, and camera photos.

---

## 2. Input Specifications
* **Input Type:** Still image (`.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`) or NumPy array (`uint8`, BGR/RGB).
* **Target Resolution:** $640 \times 640$ pixels (Ultralytics handles aspect-ratio letterboxing automatically).
* **Color Channels:** 3 (RGB or BGR).
* **Input Context:** Can handle high-resolution document scans containing text, tables, stamps, and signatures alongside the card or passport.

---

## 3. Output Specifications
For each detected document, the model outputs:
1. **Bounding Box Coordinates:** `[x1, y1, x2, y2]` in absolute pixel coordinates.
2. **Confidence Score:** Float between `0.0` and `1.0` (Recommended operating threshold: `conf >= 0.35`).
3. **Class IDs & Names:**
   * `0`: `id card`
   * `1`: `passport`

### Example Output Structure (Python Ultralytics):
```python
results[0].boxes.xyxy   # Tensor of bounding box corners [x1, y1, x2, y2]
results[0].boxes.conf   # Tensor of confidence scores [0.89, 0.94, ...]
results[0].boxes.cls    # Tensor of class IDs [0, 1, ...]
```

---

## 4. Usage Example (Python)

```python
from ultralytics import YOLO
import cv2

# Load model
model = YOLO("models/id_card_detection/best.pt")

# Run inference
results = model.predict(source="path/to/scan.jpg", conf=0.35, imgsz=640)

# Extract cropped cards
img = cv2.imread("path/to/scan.jpg")
for box in results[0].boxes:
    cls_id = int(box.cls[0].item())
    class_name = model.names[cls_id]
    confidence = float(box.conf[0].item())
    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
    
    print(f"Detected {class_name} with {confidence*100:.1f}% confidence at [{x1}, {y1}, {x2}, y2]")
    
    # Crop detected card
    crop = img[y1:y2, x1:x2]
    cv2.imwrite(f"crop_{class_name}.jpg", crop)
```

---

## 5. Pipeline Role
This model acts as **Step 1** of the pipeline:
$$\text{Full Scanned Document} \xrightarrow{\textbf{ID Detection Model}} \text{Cropped ID Card}$$
The resulting cropped image is directly passed to the **Orientation Classifier** (`models/orientation_classifier/best.pt`).

