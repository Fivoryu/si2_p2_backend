"""Entrena YOLOv8n sobre dashboard y CarDD. Uso: python ml/train_models.py [--epochs 25]"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
DATASETS = ROOT / "datasets"


def train_one(name: str, data_yaml: Path, epochs: int, imgsz: int = 640) -> Path:
    from ultralytics import YOLO

    print(f"\n=== Entrenando {name} ({epochs} épocas) ===")
    model = YOLO("yolov8n.pt")
    results = model.train(
        data=str(data_yaml),
        epochs=epochs,
        imgsz=imgsz,
        batch=8,
        patience=10,
        project=str(ROOT / "runs"),
        name=name,
        exist_ok=True,
        verbose=True,
    )
    best = Path(results.save_dir) / "weights" / "best.pt"
    MODELS.mkdir(parents=True, exist_ok=True)
    dest = MODELS / f"{name}_best.pt"
    shutil.copy2(best, dest)
    print(f"Modelo guardado: {dest}")
    return dest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=25, help="Épocas por modelo")
    parser.add_argument("--dashboard-only", action="store_true")
    parser.add_argument("--cardd-only", action="store_true")
    args = parser.parse_args()

    from prepare_datasets import prepare_cardd, prepare_dashboard

    if not args.cardd_only:
        prepare_dashboard()
        train_one("dashboard", DATASETS / "dashboard_yolo" / "data.yaml", args.epochs)

    if not args.dashboard_only:
        prepare_cardd()
        train_one("cardd", DATASETS / "cardd_yolo" / "data.yaml", args.epochs)

    print("\nEntrenamiento completado.")


if __name__ == "__main__":
    main()
