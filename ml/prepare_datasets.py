"""Convierte car-dashboard (CSV Roboflow) y CarDD (COCO) a formato YOLOv8."""

from __future__ import annotations

import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT.parent
DASHBOARD_SRC = BACKEND / "car-dashboard"
CARDD_SRC = BACKEND / "car-accident" / "CarDD_release" / "CarDD_COCO"
OUT = ROOT / "datasets"


def _yolo_line(class_id: int, xmin: float, ymin: float, xmax: float, ymax: float, w: int, h: int) -> str:
    xc = ((xmin + xmax) / 2) / w
    yc = ((ymin + ymax) / 2) / h
    bw = (xmax - xmin) / w
    bh = (ymax - ymin) / h
    return f"{class_id} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}"


def prepare_dashboard() -> Path:
    out = OUT / "dashboard_yolo"
    if out.exists():
        shutil.rmtree(out)

    splits = {"train": "train", "valid": "val", "test": "test"}
    class_names: list[str] = []

    for src_split, dst_split in splits.items():
        src_dir = DASHBOARD_SRC / src_split
        ann_path = src_dir / "_annotations.csv"
        if not ann_path.exists():
            continue

        img_out = out / "images" / dst_split
        lbl_out = out / "labels" / dst_split
        img_out.mkdir(parents=True, exist_ok=True)
        lbl_out.mkdir(parents=True, exist_ok=True)

        boxes: dict[str, list[str]] = defaultdict(list)
        with ann_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cls = row["class"].strip()
                if cls not in class_names:
                    class_names.append(cls)
                cid = class_names.index(cls)
                w, h = int(row["width"]), int(row["height"])
                line = _yolo_line(
                    cid,
                    float(row["xmin"]),
                    float(row["ymin"]),
                    float(row["xmax"]),
                    float(row["ymax"]),
                    w,
                    h,
                )
                boxes[row["filename"]].append(line)

        for fname, lines in boxes.items():
            src_img = src_dir / fname
            if not src_img.exists():
                continue
            shutil.copy2(src_img, img_out / fname)
            (lbl_out / f"{Path(fname).stem}.txt").write_text("\n".join(lines), encoding="utf-8")

    yaml_path = out / "data.yaml"
    names_yaml = "\n".join(f"  {i}: {n}" for i, n in enumerate(class_names))
    yaml_path.write_text(
        f"path: {out.as_posix()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"test: images/test\n"
        f"nc: {len(class_names)}\n"
        f"names:\n{names_yaml}\n",
        encoding="utf-8",
    )
    (OUT / "dashboard_classes.json").write_text(
        json.dumps(class_names, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"dashboard_yolo: {len(class_names)} clases -> {out}")
    return out


def prepare_cardd() -> Path:
    out = OUT / "cardd_yolo"
    if out.exists():
        shutil.rmtree(out)

    split_map = {
        "train2017": "train",
        "val2017": "val",
        "test2017": "test",
    }
    categories: list[str] = []

    for coco_split, yolo_split in split_map.items():
        ann_file = CARDD_SRC / "annotations" / f"instances_{coco_split}.json"
        img_dir = CARDD_SRC / coco_split
        if not ann_file.exists():
            continue

        data = json.loads(ann_file.read_text(encoding="utf-8"))
        if not categories:
            categories = [c["name"] for c in sorted(data["categories"], key=lambda x: x["id"])]
            cat_id_to_idx = {c["id"]: categories.index(c["name"]) for c in data["categories"]}

        img_out = out / "images" / yolo_split
        lbl_out = out / "labels" / yolo_split
        img_out.mkdir(parents=True, exist_ok=True)
        lbl_out.mkdir(parents=True, exist_ok=True)

        id_to_file = {im["id"]: im["file_name"] for im in data["images"]}
        id_to_size = {im["id"]: (im["width"], im["height"]) for im in data["images"]}
        boxes: dict[str, list[str]] = defaultdict(list)

        for ann in data["annotations"]:
            fname = id_to_file.get(ann["image_id"])
            if not fname:
                continue
            w, h = id_to_size[ann["image_id"]]
            x, y, bw, bh = ann["bbox"]
            line = _yolo_line(
                cat_id_to_idx[ann["category_id"]],
                x,
                y,
                x + bw,
                y + bh,
                w,
                h,
            )
            boxes[fname].append(line)

        for fname, lines in boxes.items():
            src_img = img_dir / fname
            if not src_img.exists():
                continue
            shutil.copy2(src_img, img_out / fname)
            (lbl_out / f"{Path(fname).stem}.txt").write_text("\n".join(lines), encoding="utf-8")

    yaml_path = out / "data.yaml"
    names_yaml = "\n".join(f"  {i}: {n}" for i, n in enumerate(categories))
    yaml_path.write_text(
        f"path: {out.as_posix()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"test: images/test\n"
        f"nc: {len(categories)}\n"
        f"names:\n{names_yaml}\n",
        encoding="utf-8",
    )
    (OUT / "cardd_classes.json").write_text(
        json.dumps(categories, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"cardd_yolo: {len(categories)} clases -> {out}")
    return out


if __name__ == "__main__":
    prepare_dashboard()
    prepare_cardd()
    print("Listo.")
