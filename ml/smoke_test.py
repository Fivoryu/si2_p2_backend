"""Prueba rápida de inferencia con modelos entrenados."""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from app.services.vision import classify_image_file, models_available  # noqa: E402


def sample_images(folder: Path, n: int = 3) -> list[Path]:
    imgs = list(folder.glob("*.jpg")) + list(folder.glob("*.png")) + list(folder.glob("*.jpeg"))
    random.shuffle(imgs)
    return imgs[:n]


def main() -> None:
    if not models_available():
        print("Modelos no encontrados en ml/models/*.pt")
        sys.exit(1)

    dash = ROOT / "datasets" / "dashboard_yolo" / "images" / "val"
    cardd = ROOT / "datasets" / "cardd_yolo" / "images" / "val"

    print("=== Dashboard (tablero) ===")
    for img in sample_images(dash):
        codigo, conf = classify_image_file(img)
        print(f"  {img.name}: {codigo} ({conf:.2f})")

    print("\n=== CarDD (daños) ===")
    for img in sample_images(cardd):
        codigo, conf = classify_image_file(img)
        print(f"  {img.name}: {codigo} ({conf:.2f})")


if __name__ == "__main__":
    main()
