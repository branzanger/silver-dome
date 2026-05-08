#!/usr/bin/env python3
"""
Download Shahed-136 drone images and train YOLOv8n on them.
Uses DuckDuckGo image search to grab training images, auto-labels them
(full-image bounding box since Shahed is the primary subject),
then trains YOLOv8n for a quick fine-tune.
"""
import os
import ssl
import sys
import urllib.request
import hashlib
from pathlib import Path

# Disable SSL verification for downloads
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

DATASET_DIR = Path(__file__).parent
IMAGES_TRAIN = DATASET_DIR / "images" / "train"
IMAGES_VAL = DATASET_DIR / "images" / "val"
LABELS_TRAIN = DATASET_DIR / "labels" / "train"
LABELS_VAL = DATASET_DIR / "labels" / "val"

# Shahed-136 image URLs (publicly available press/military imagery)
SHAHED_URLS = [
    "https://upload.wikimedia.org/wikipedia/commons/thumb/1/18/Shahed_136_at_the_2nd_exhibition_of_the_requirements_and_achievements_of_the_army_01_%28cropped%29.jpg/640px-Shahed_136_at_the_2nd_exhibition_of_the_requirements_and_achievements_of_the_army_01_%28cropped%29.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4d/Shahed_136_at_the_2nd_exhibition_of_the_requirements_and_achievements_of_the_army_02.jpg/640px-Shahed_136_at_the_2nd_exhibition_of_the_requirements_and_achievements_of_the_army_02.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/e/ee/Geran-2_UAV_fragments_%28cropped%29.jpg/640px-Geran-2_UAV_fragments_%28cropped%29.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/8/80/Geran-2_drone_attacks_on_Kyiv%2C_17_October_2022_%2802%29.jpg/640px-Geran-2_drone_attacks_on_Kyiv%2C_17_October_2022_%2802%29.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3c/UAV_Shahed_136_001.jpg/640px-UAV_Shahed_136_001.jpg",
]


def download_images():
    """Download Shahed images from URLs."""
    downloaded = 0
    for i, url in enumerate(SHAHED_URLS):
        fname = f"shahed_{i:03d}.jpg"
        # 80% train, 20% val
        dest_dir = IMAGES_VAL if i % 5 == 0 else IMAGES_TRAIN
        dest = dest_dir / fname
        if dest.exists():
            print(f"  [skip] {fname} already exists")
            downloaded += 1
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            data = resp.read()
            with open(dest, "wb") as f:
                f.write(data)
            print(f"  [ok] {fname} ({len(data)//1024}KB)")
            downloaded += 1
        except Exception as e:
            print(f"  [fail] {fname}: {e}")
    return downloaded


def create_labels():
    """Create YOLO format labels. Class 0 = shahed-136.
    Since these are images OF Shaheds, we use a centered bounding box
    covering most of the frame (0.1 to 0.9 in both dimensions).
    """
    for split, img_dir, lbl_dir in [
        ("train", IMAGES_TRAIN, LABELS_TRAIN),
        ("val", IMAGES_VAL, LABELS_VAL),
    ]:
        count = 0
        for img_path in img_dir.glob("*.jpg"):
            label_path = lbl_dir / img_path.with_suffix(".txt").name
            # YOLO format: class x_center y_center width height (normalized)
            # Centered box covering 80% of the image
            label_path.write_text("0 0.5 0.5 0.8 0.8\n")
            count += 1
        print(f"  {split}: {count} labels")


def create_yaml():
    """Create YOLO dataset YAML config."""
    yaml_content = f"""# Shahed-136 Drone Detection Dataset
path: {DATASET_DIR}
train: images/train
val: images/val

nc: 1
names: ['shahed-136']
"""
    yaml_path = DATASET_DIR / "shahed.yaml"
    yaml_path.write_text(yaml_content)
    print(f"  Dataset config: {yaml_path}")
    return yaml_path


def train(yaml_path, epochs=30):
    """Train YOLOv8n on the Shahed dataset."""
    from ultralytics import YOLO

    model = YOLO("yolov8n.pt")
    results = model.train(
        data=str(yaml_path),
        epochs=epochs,
        imgsz=640,
        batch=4,
        name="shahed_detector",
        project=str(DATASET_DIR / "runs"),
        exist_ok=True,
        device="mps",  # Apple Silicon GPU
        verbose=True,
    )

    # Copy best weights to models/shahed.pt
    best_pt = DATASET_DIR / "runs" / "shahed_detector" / "weights" / "best.pt"
    dest = DATASET_DIR.parent / "models" / "shahed.pt"
    if best_pt.exists():
        import shutil
        shutil.copy2(best_pt, dest)
        print(f"\n  Model saved: {dest}")
    else:
        print(f"\n  WARNING: best.pt not found at {best_pt}")

    return results


if __name__ == "__main__":
    print("=" * 50)
    print("Shahed-136 Drone Detection - Dataset & Training")
    print("=" * 50)

    print("\n1. Downloading Shahed-136 images...")
    n = download_images()
    print(f"   {n} images ready")

    print("\n2. Creating YOLO labels...")
    create_labels()

    print("\n3. Creating dataset config...")
    yaml_path = create_yaml()

    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    print(f"\n4. Training YOLOv8n ({epochs} epochs)...")
    train(yaml_path, epochs=epochs)

    print("\n  Done! Restart Silver Dome to use the new model.")
