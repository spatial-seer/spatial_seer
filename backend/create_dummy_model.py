"""Trains throwaway XGBoost classifiers so the FastAPI server can boot
before the real Spatial Seer model is ready.

Run once:
    python create_dummy_model.py

This produces `current_model.pkl`. The bundle format is intentionally a
multi-head contract: one shared feature matrix, N prediction "heads" each
with their own model + label encoder. `main.py` iterates heads and writes
a corresponding `predicted_<head>` column into `live_predictions`, so
adding a third prediction target later is a bundle-only change.

Current heads:
    * room       -> broad room category (kitchen, hallway, ...)
    * location   -> specific location string (Floor3Kitchen, Outside3102, ...)
"""

from __future__ import annotations

from typing import Iterable

import joblib
import numpy as np
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier

MODEL_PATH = "current_model.pkl"

ROOM_LABELS = [
    "kitchen",
    "hallway",
    "bedroom",
    "living_room",
    "bathroom",
    "office",
    "outside",
]

# A representative sample of the specific locations observed in the Unity
# captures. Swap these out when the real trainer runs on real data; the
# contract with `main.py` is only that `heads["location"]["label_encoder"]`
# exposes an `inverse_transform` that returns strings.
LOCATION_LABELS = [
    "Floor3Kitchen",
    "Floor3Hallway",
    "Floor2Bedroom",
    "Floor2LivingRoom",
    "Floor1Bathroom",
    "Floor1Office",
    "Outside3102",
]

# These are the numeric columns that live INSIDE each row's `csv_dump`
# string on the `hardware_data` table. Keep in sync with the Unity client's
# CSV writer. `Timestamp` is intentionally excluded; it's a monotonically
# increasing counter per scan and not a room-predictive feature.
FEATURE_NAMES = [
    "TotalUsedMem",
    "CpuUtil",
    "GpuUtil",
    "BatteryMicroAmps",
    "BatteryTemp",
    "BatteryLevel",
    "BatteryVoltageMv",
    "ScreenBrightness",
    "AvgFPS",
    "WorstFrameMs",
    "BestFrameMs",
    "MainThreadMs",
    "GCAllocRate",
    "FrameTimeStdDev",
    "CpuClockFreq",
]


def _build_feature_matrix(n_samples: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(size=(n_samples, len(FEATURE_NAMES)))


def _train_head(X: np.ndarray, labels: Iterable[str], seed: int):
    """Train one classification head and return (model, label_encoder)."""
    labels = list(labels)
    rng = np.random.default_rng(seed)
    y_str = rng.choice(labels, size=len(X))

    encoder = LabelEncoder().fit(labels)
    y = encoder.transform(y_str)

    model = XGBClassifier(
        n_estimators=25,
        max_depth=3,
        learning_rate=0.1,
        eval_metric="mlogloss",
        tree_method="hist",
    )
    model.fit(X, y)
    return model, encoder


def main() -> None:
    X = _build_feature_matrix(n_samples=500, seed=42)

    room_model, room_encoder = _train_head(X, ROOM_LABELS, seed=1)
    location_model, location_encoder = _train_head(X, LOCATION_LABELS, seed=2)

    bundle = {
        "feature_names": FEATURE_NAMES,
        "heads": {
            "room": {
                "model": room_model,
                "label_encoder": room_encoder,
            },
            "location": {
                "model": location_model,
                "label_encoder": location_encoder,
            },
        },
        "kind": "dummy-xgboost-v2",
    }
    joblib.dump(bundle, MODEL_PATH)
    print(f"Wrote dummy model bundle to {MODEL_PATH}")
    print(f"  kind          : {bundle['kind']}")
    print(f"  feature_names : {FEATURE_NAMES}")
    print(f"  heads         : {list(bundle['heads'])}")
    print(f"  room classes  : {list(room_encoder.classes_)}")
    print(f"  location classes: {list(location_encoder.classes_)}")


if __name__ == "__main__":
    main()
