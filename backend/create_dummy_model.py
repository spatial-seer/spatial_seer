"""Trains a throwaway XGBoost classifier so the FastAPI server can boot
before the real Spatial Seer model is ready.

Run once:
    python create_dummy_model.py

This produces `current_model.pkl` in the same directory. The bundle format
(model + label encoder + feature names) is the exact contract `main.py`
expects, so swapping in the real model later is a drop-in replacement.
"""

from __future__ import annotations

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


def build_synthetic_dataset(n_samples: int = 500, seed: int = 42):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n_samples, len(FEATURE_NAMES)))
    y_str = rng.choice(ROOM_LABELS, size=n_samples)
    return X, y_str


def main() -> None:
    X, y_str = build_synthetic_dataset()

    label_encoder = LabelEncoder().fit(ROOM_LABELS)
    y = label_encoder.transform(y_str)

    model = XGBClassifier(
        n_estimators=25,
        max_depth=3,
        learning_rate=0.1,
        eval_metric="mlogloss",
        tree_method="hist",
    )
    model.fit(X, y)

    bundle = {
        "model": model,
        "label_encoder": label_encoder,
        "feature_names": FEATURE_NAMES,
        "kind": "dummy-xgboost",
    }
    joblib.dump(bundle, MODEL_PATH)
    print(f"Wrote dummy model bundle to {MODEL_PATH}")
    print(f"  classes      : {list(label_encoder.classes_)}")
    print(f"  feature_names: {FEATURE_NAMES}")


if __name__ == "__main__":
    main()
