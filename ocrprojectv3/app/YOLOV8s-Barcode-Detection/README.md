---
license: agpl-3.0
base_model:
- Ultralytics/YOLOv8
tags:
- ultralytics
- tracking
- instance-segmentation
- image-classification
- pose-estimation
- obb
- object-detection
- yolo
- yolov8
- yolov3
- yolov5
- yolov9
- yolov10
- barcode
- qrcode
- package
- deliveries
pipeline_tag: object-detection
library_name: ultralytics
---

<div align="center">
  <p>
    <a href="https://www.ultralytics.com" target="_blank">
      <img width="100%" src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/yolov8detectotcover.png" alt="YOLO Vision banner"></a>
  </p>
<!-- 
<div>
    <a href="https://github.com/ultralytics/ultralytics/actions/workflows/ci.yaml"><img src="https://github.com/ultralytics/ultralytics/actions/workflows/ci.yaml/badge.svg" alt="Ultralytics CI"></a>
    <a href="https://colab.research.google.com/github/ultralytics/ultralytics/blob/main/examples/tutorial.ipynb"><img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open Ultralytics In Colab"></a>
</div>
<br>
-->
<h1 align="center">YOLOV8s Barcode/QR Code Detection Model</h1>
  
<div align="center">Special thanks to <a href="https://huggingface.co/armaggheddon97">Armaggheddon97</a> and his <strong align="center">4090</strong></div>
  
This repository hosts a finetuned [Ultralytics](https://www.ultralytics.com/) [YOLOv8s](https://github.com/ultralytics/ultralytics) model specifically designed for **barcode and QR code classification and detection**. Building upon the robust and efficient architecture of YOLOv8, this model has been fine-tuned on a comprehensive dataset of over 5,000 images, encompassing various barcode types (EAN13, Code128, etc.) and QR codes.

YOLOv8 is renowned for its speed, accuracy, and ease of use, making it an excellent foundation for specialized object detection tasks like barcode and QR code recognition. This finetuned model aims to provide a reliable and performant solution for applications requiring automated barcode and QR code scanning and identification.

We hope this model proves useful for your projects! For general YOLOv8 documentation, please refer to the Ultralytics <a href="https://docs.ultralytics.com/">Docs</a>. For specific questions or discussions related to this finetuned model, please raise an issue on its [Hugging Face model page](https://huggingface.co/Piero2411/YOLOV8s_Barcode_Detection).

## <div align="center">Documentation & Usage</div>

Below is a quickstart guide for installing the necessary dependencies and using this finetuned model.

<details open>
<summary>Install</summary>

To use this model, you'll need the `ultralytics` package, which can be installed via pip. Ensure you have a [**Python>=3.8**](https://www.python.org/) environment with [**PyTorch>=1.8**](https://pytorch.org/get-started/locally/).

[![PyPI - Version](https://img.shields.io/pypi/v/ultralytics?logo=pypi&logoColor=white)](https://pypi.org/project/ultralytics/) [![Downloads](https://static.pepy.tech/badge/ultralytics)](https://pepy.tech/project/ultralytics) [![PyPI - Python Version](https://img.shields.io/pypi/pyversions/ultralytics?logo=python&logoColor=gold)](https://pypi.org/project/ultralytics/)

```bash
pip install ultralytics
```

For alternative installation methods or more detailed instructions on setting up your environment, please refer to the Ultralytics [Quickstart Guide](https://docs.ultralytics.com/quickstart/).

[![Conda Version](https://img.shields.io/conda/vn/conda-forge/ultralytics?logo=condaforge)](https://anaconda.org/conda-forge/ultralytics) [![Docker Image Version](https://img.shields.io/docker/v/ultralytics/ultralytics?sort=semver&logo=docker)](https://hub.docker.com/r/ultralytics/ultralytics)

</details>

<details open>
<summary>Usage</summary>

### CLI

You can use this model directly from the Command Line Interface (CLI) using the `yolo` command. Make sure the `YOLOV8s_Barcode_Detection.pt` model file is accessible in your current directory or specified with its full path.

```bash
yolo predict model=YOLOV8s_Barcode_Detection.pt source='path/to/your/image.jpg'
```

The `yolo` command offers various modes and arguments (e.g., `imgsz=640`). For more details, consult the YOLOv8 [CLI Docs](https://docs.ultralytics.com/usage/cli/).

### Python

This model can also be easily integrated into your Python applications. The usage mirrors the standard Ultralytics YOLO API.

```python
from ultralytics import YOLO

# Load your finetuned model
model = YOLO("YOLOV8s_Barcode_Detection.pt")

# Perform object detection on an image
results = model("path/to/image.jpg")

# Optionally, visualize the results
results[0].show()

# You can also export the model to other formats (e.g., ONNX)
# This is useful for deployment in various environments.
# path = model.export(format="onnx") # uncomment to export
```

For more advanced Python usage, including training, validation, and different prediction modes, refer to the YOLOv8 [Python Docs](https://docs.ultralytics.com/usage/python/).

</details>

## <div align="center">Model Details</div>

This specific model, `YOLOV8s_Barcode_Detection.pt`, is a finetuned instance of the **YOLOv8s (small) detection model**. It has been specifically trained for **barcode and QR code classification and detection**.

### Dataset

The model was finetuned on a custom dataset comprising **over 5,000 images**. This dataset includes a wide variety of barcode types such as EAN13, Code128, and many more, alongside various QR code instances, ensuring robustness across different real-world scenarios.

### Training Parameters

The training process leveraged a pre-trained `yolov8s.pt` checkpoint and was conducted with the following key parameters:

```python
# Base model used for finetuning
model = YOLO('yolov8s.pt') 

# Training parameters
results = model.train(
    data='dataset/data_autosplit.yaml', # Path to the custom dataset
    epochs=30,           # Number of training epochs
    patience=10,         # Early stopping patience
    batch=16,            # Batch size (adjust based on GPU memory)
    imgsz=640,           # Image size for training
    name='yolov8_large_dataset_v1', # Run name

    # Augmentation parameters
    degrees=180,         # Random rotation (0-180 degrees)
    translate=0.1,       # Image translation (0-1)
    scale=0.5,           # Image scaling (0-1)
    shear=10,            # Image shearing (degrees)
    perspective=0.001,   # Image perspective (0-0.001)
    fliplr=0.5,          # Horizontal flip (probability)
    flipud=0.5,          # Vertical flip (probability)
    cutmix=0.2,          # CutMix augmentation (probability)
)
```

The combination of a robust base model, a diverse custom dataset, and carefully selected training parameters aims to provide high accuracy and generalization for barcode and QR code detection across various real-world scenarios.

<img width="1024" src="https://raw.githubusercontent.com/ultralytics/assets/main/im/banner-tasks.png" alt="Ultralytics YOLO supported tasks">

While the table below provides a general overview of the YOLOv8s base model's performance on the COCO dataset, which serves as a reference for its architectural capabilities. **Please note that the performance metrics for `YOLOV8s_Barcode_Detection.pt` will differ, reflecting its specialized training on barcode and QR code data.**

<details open><summary>Base YOLOv8s Detection (COCO Reference)</summary>

See [Detection Docs](https://docs.ultralytics.com/tasks/detect/) for usage examples with these models trained on [COCO](https://docs.ultralytics.com/datasets/detect/coco/), which include 80 pre-trained classes.

| Model                                                                                | size<br><sup>(pixels) | mAP<sup>val<br>50-95 | Speed<br><sup>CPU ONNX<br>(ms) | Speed<br><sup>A100 TensorRT<br>(ms) | params<br><sup>(M) | FLOPs<br><sup>(B) |
| ------------------------------------------------------------------------------------ | --------------------- | -------------------- | ------------------------------ | ----------------------------------- | ------------------ | ----------------- |
| [YOLOv8s](https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8s.pt) | 640                   | 44.9                 | 128.4                          | 1.20                                | 11.2               | 28.6              |

- **mAP<sup>val</sup>** values are for single-model single-scale on [COCO val2017](https://cocodataset.org/) dataset. <br>Reproduce by `yolo val detect data=coco.yaml device=0`
- **Speed** averaged over COCO val images using an [Amazon EC2 P4d](https://aws.amazon.com/ec2/instance-types/p4/) instance. <br>Reproduce by `yolo val detect data=coco.yaml batch=1 device=0|cpu`

</details>

## <div align="center">Performance and Results</div>

Below are the key performance metrics and visualizations from the training and validation of the `YOLOV8s_Barcode_Detection.pt` model.

### Key Metrics from Training (Last Epoch)

| Metric                   | Value   |
| :----------------------- | :------ |
| `metrics/precision(B)`   | 0.97545 |
| `metrics/recall(B)`      | 0.99127 |
| `metrics/mAP50(B)`       | 0.98643 |
| `metrics/mAP50-95(B)`    | 0.77643 |
| `val/box_loss`           | 0.988   |
| `val/cls_loss`           | 0.43007 |
| `val/dfl_loss`           | 1.11172 |

These metrics demonstrate the model's high precision and recall, especially at an IoU threshold of 0.5 (mAP50), indicating excellent performance in detecting both barcode and QR code instances.

### Visual Results

Here are various plots generated during the training process, providing deeper insights into the model's performance and behavior:

| Plot Type                    | Description                                                                                                                                                                                                                            | Image                                                                                                                                                                              |
| :--------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **F1-Confidence Curve**      | This curve shows the F1 score across different confidence thresholds for both barcode and QR code classes, as well as the overall F1 score. The peak F1 of `0.98` is achieved at a confidence of `0.553`.                               | <img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/BoxF1_curve.png" width="400px" alt="F1-Confidence Curve">                                            |
| **Precision-Confidence Curve** | This plot illustrates how precision changes with varying confidence thresholds. A high precision of `1.00` is achieved at a confidence of `0.928`.                                                                                   | <img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/BoxP_curve.png" width="400px" alt="Precision-Confidence Curve">                                        |
| **Precision-Recall Curve**   | The Precision-Recall curve highlights the trade-off between precision and recall. The model achieved an impressive overall `mAP@0.5` of `0.986`, with individual class scores of `0.979` for barcode and `0.992` for qrcode.             | <img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/BoxPR_curve.png" width="400px" alt="Precision-Recall Curve">                                        |
| **Recall-Confidence Curve**  | This curve shows the recall performance at different confidence thresholds.                                                                                                                                                              | <img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/BoxR_curve.png" width="400px" alt="Recall-Confidence Curve">                                        |
| **Confusion Matrix (Normalized)** | The normalized confusion matrix shows the proportion of true positives, false positives, and false negatives for each class, normalized by the true class count.                                                                       | <img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/confusion_matrix_normalized.png" width="400px" alt="Confusion Matrix Normalized">                   |
| **Confusion Matrix (Absolute Counts)** | The absolute confusion matrix shows the raw counts of true positives, false positives, and false negatives for each class.                                                                                                     | <img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/confusion_matrix.png" width="400px" alt="Confusion Matrix">                                            |
| **Dataset Distribution**     | This plot provides insights into the distribution of instances within the dataset, including class counts, bounding box shapes, and centroid locations.                                                                              | <img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/labels.jpg" width="400px" alt="Dataset Distribution">                                               |
| **Training & Validation Curves** | These plots track the training and validation losses (box, classification, DFL) and performance metrics (precision, recall, mAP) across all epochs, demonstrating the model's learning progression.                                 | <img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/results.png" width="400px" alt="Training and Validation Loss/Metrics Curves">                          |

#### Example Prediction Batches
Visual examples of the model's predictions on validation images, showing detected bounding boxes and class labels (before and after confidence thresholding for clarity).

<details><summary>Labeled Predictions (Ground Truth)</summary>
<img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/val_batch0_labels.jpg" width="100%" alt="Labeled Predictions (Ground Truth)">
<img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/val_batch1_labels.jpg" width="100%" alt="Labeled Predictions (Ground Truth)">
<img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/val_batch2_labels.jpg" width="100%" alt="Labeled Predictions (Ground Truth)">
</details>

<details><summary>Predicted Bounding Boxes (with Confidence)</summary>
<img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/val_batch0_pred.jpg" width="100%" alt="Predicted Bounding Boxes (with Confidence)">
<img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/val_batch1_pred.jpg" width="100%" alt="Predicted Bounding Boxes (with Confidence)">
<img src="https://images-and-logos.pages.dev/HuggingFace/YoloV8BarcodeDetector/val_batch2_pred.jpg" width="100%" alt="Predicted Bounding Boxes (with Confidence)">
</details>

## <div align="center">License</div>

Ultralytics offers two licensing options to accommodate diverse use cases:

- **AGPL-3.0 License**: This [OSI-approved](https://opensource.org/license) open-source license is ideal for students and enthusiasts, promoting open collaboration and knowledge sharing. See the [LICENSE](https://github.com/ultralytics/ultralytics/blob/main/LICENSE) file for more details.
–––