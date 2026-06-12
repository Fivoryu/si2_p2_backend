"""
Entrena el modelo de clasificación de intents para reportes dinámicos.

Arquitectura:
  TF-IDF (500 features) → Dense(128, ReLU) → Dropout(0.3) →
  Dense(64, ReLU) → Dropout(0.3) → Dense(8, Softmax)

Salida:
  intent_model.h5       — modelo Keras
  tfidf_vectorizer.pkl  — vectorizer TF-IDF
  label_encoder.pkl     — codificador de labels

Uso: python train_report_model.py [--epochs 50] [--batch-size 32]
"""
import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split

import tensorflow as tf
from tensorflow import keras


INTENTS = ["AGGREGATE", "COMPARE", "COUNT", "EXPLAIN", "LIST", "MAP", "RANKING", "TREND"]


def load_data(csv_path: str) -> tuple[list[str], list[str], list[dict]]:
    """Carga datos del CSV."""
    df = pd.read_csv(csv_path, encoding="utf-8")
    texts = df["text"].tolist()
    intents = df["intent"].tolist()
    queries = [json.loads(q) for q in df["query_json"].tolist()]
    return texts, intents, queries


def build_model(input_dim: int, num_classes: int) -> keras.Model:
    """Construye el modelo de clasificación."""
    model = keras.Sequential([
        keras.layers.Input(shape=(input_dim,)),
        keras.layers.Dense(128, activation="relu"),
        keras.layers.Dropout(0.3),
        keras.layers.Dense(64, activation="relu"),
        keras.layers.Dropout(0.3),
        keras.layers.Dense(num_classes, activation="softmax"),
    ])
    model.compile(
        optimizer="adam",
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    out_dir = Path(__file__).parent
    csv_path = out_dir / "training_data.csv"

    # 1. Cargar datos
    print("Cargando datos de entrenamiento...")
    texts, intents, queries = load_data(str(csv_path))
    print(f"  {len(texts)} ejemplos, {len(set(intents))} intents")

    # 2. Vectorizar texto (TF-IDF)
    print("Vectorizando texto (TF-IDF)...")
    tfidf = TfidfVectorizer(
        max_features=500,
        ngram_range=(1, 2),
        min_df=1,
        max_df=0.95,
        sublinear_tf=True,
    )
    X = tfidf.fit_transform(texts).toarray()
    print(f"  Matriz: {X.shape}")

    # 3. Codificar labels
    le = LabelEncoder()
    y = le.fit_transform(intents)
    print(f"  Classes: {list(le.classes_)}")

    # 4. Split train/val
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.15, random_state=42, stratify=y
    )
    print(f"  Train: {len(X_train)}, Val: {len(X_val)}")

    # 5. Construir modelo
    model = build_model(X.shape[1], len(le.classes_))
    model.summary()

    # 6. Entrenar
    print("\nEntrenando modelo...")
    callbacks = [
        keras.callbacks.EarlyStopping(
            monitor="val_accuracy", patience=10, restore_best_weights=True
        ),
        keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss", factor=0.5, patience=5, min_lr=1e-5
        ),
    ]
    history = model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        epochs=args.epochs,
        batch_size=args.batch_size,
        callbacks=callbacks,
        verbose=1,
    )

    # 7. Evaluar
    val_loss, val_acc = model.evaluate(X_val, y_val, verbose=0)
    print(f"\nAccuracy en validación: {val_acc:.4f}")

    # 8. Guardar
    model_path = out_dir / "intent_model.h5"
    tfidf_path = out_dir / "tfidf_vectorizer.pkl"
    le_path = out_dir / "label_encoder.pkl"

    model.save(str(model_path))
    print(f"Modelo guardado: {model_path}")

    with open(tfidf_path, "wb") as f:
        pickle.dump(tfidf, f)
    print(f"TF-IDF guardado: {tfidf_path}")

    with open(le_path, "wb") as f:
        pickle.dump(le, f)
    print(f"Label encoder guardado: {le_path}")

    # 9. Guardar métricas
    metrics = {
        "accuracy": float(val_acc),
        "epochs_run": len(history.history["loss"]),
        "classes": list(le.classes_),
        "vocab_size": X.shape[1],
    }
    metrics_path = out_dir / "training_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"Métricas guardadas: {metrics_path}")


if __name__ == "__main__":
    main()
