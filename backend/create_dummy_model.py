"""Trains placeholder MiniRocket classifiers so the FastAPI server can boot
against real data before the final notebook model is ready.

Run once:

    python create_dummy_model.py

This produces `current_model.pkl`. The model is a two-head multivariate
time-series classifier that mirrors the preprocessing + recipe from
`model_experimentation/model_4_test.ipynb` (Cell 1 + Cell 2):

    * 7 telemetry channels
    * truncate to SERIES_LEN rows after sorting by Timestamp
    * aeon.classification.convolution_based.MiniRocketClassifier per head

It's still a "placeholder" in the sense that the final notebook will
produce the real bundle. But it trains on the real CSV with the real
recipe, so it's representative enough to validate the full webhook path
end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from aeon.classification.convolution_based import MiniRocketClassifier
from sklearn.preprocessing import LabelEncoder

MODEL_PATH = Path(__file__).with_name("current_model.pkl")
# Training CSV lives one directory up from `backend/`.
CSV_PATH = Path(__file__).resolve().parents[1] / "spatial_seer_all_rooms_v3.csv"

# The 7 channels that the notebook selected. Order matters -- it defines
# the channel axis of the `(n_instances, n_channels, series_length)`
# tensor the model consumes at inference time, and is persisted in the
# bundle as `channel_names`.
CHANNEL_NAMES: list[str] = [
    "GpuUtil",
    "CpuUtil",
    "FrameTimeStdDev",
    "WorstFrameMs",
    "MainThreadMs",
    "TotalUsedMem",
    "CpuClockFreq",
]

SORT_BY = "Timestamp"
RANDOM_STATE = 42


def _build_scan_array(
    df: pd.DataFrame, series_len: int, channels: list[str]
) -> tuple[np.ndarray, list[str], list[str]]:
    """Mirror notebook Cell 1's `build_scan_array` exactly.

    Returns:
        X:        (n_scans, n_channels, series_len) float32
        locs:     per-scan location string
        rooms:    per-scan room_label string
    """
    records = []
    for scan_id, group in df.groupby("scan_id"):
        group = group.sort_values(SORT_BY, kind="mergesort").iloc[:series_len]
        if len(group) < series_len:
            # Shouldn't happen if series_len == min scan length, but skip
            # degenerate scans rather than pad them at training time.
            continue
        meta = group.iloc[0]
        records.append({
            "location":   meta["location"],
            "room_label": meta["room_label"],
            "ts":         group[channels].to_numpy(dtype=np.float32).T,
        })
    X = np.stack([r["ts"] for r in records]).astype(np.float32, copy=False)
    locs = [r["location"] for r in records]
    rooms = [r["room_label"] for r in records]
    return X, locs, rooms


def _train_head(X: np.ndarray, y_str: list[str], label: str) -> tuple[MiniRocketClassifier, LabelEncoder]:
    encoder = LabelEncoder().fit(y_str)
    y = encoder.transform(y_str)
    print(f"  training head '{label}' on X={X.shape}  classes={list(encoder.classes_)}")
    clf = MiniRocketClassifier(random_state=RANDOM_STATE)
    clf.fit(X, y)
    return clf, encoder


def main() -> None:
    if not CSV_PATH.exists():
        raise FileNotFoundError(
            f"Training CSV not found at {CSV_PATH}. Place "
            "`spatial_seer_all_rooms_v3.csv` alongside the repo's top-level "
            "`spatial_seer/` folder, or update CSV_PATH."
        )

    print(f"Loading {CSV_PATH}")
    df = pd.read_csv(CSV_PATH)

    missing = [c for c in CHANNEL_NAMES + [SORT_BY, "scan_id", "location", "room_label"] if c not in df.columns]
    if missing:
        raise RuntimeError(f"CSV is missing expected columns: {missing}")

    # SERIES_LEN = shortest scan, exactly like the notebook. This value
    # gets persisted into the bundle; the server truncates inference
    # scans to match.
    scan_lengths = df.groupby("scan_id").size()
    series_length = int(scan_lengths.min())
    print(f"Scans        : {df['scan_id'].nunique()}")
    print(f"Series length: {series_length}  (min across all scans)")

    X, locs, rooms = _build_scan_array(df, series_length, CHANNEL_NAMES)
    print(f"Built tensor : X={X.shape}")

    print("\nTraining heads:")
    location_model, location_encoder = _train_head(X, locs, "location")
    room_model, room_encoder = _train_head(X, rooms, "room")

    bundle = {
        "channel_names": CHANNEL_NAMES,
        "series_length": series_length,
        "preprocessing": {"method": "truncate", "sort_by": SORT_BY},
        "heads": {
            "room":     {"model": room_model,     "label_encoder": room_encoder},
            "location": {"model": location_model, "label_encoder": location_encoder},
        },
        "kind": "minirocket-aeon-v1",
    }
    joblib.dump(bundle, MODEL_PATH)

    print(f"\nWrote model bundle to {MODEL_PATH}")
    print(f"  kind            : {bundle['kind']}")
    print(f"  channel_names   : {CHANNEL_NAMES}")
    print(f"  series_length   : {series_length}")
    print(f"  preprocessing   : {bundle['preprocessing']}")
    print(f"  heads           : {list(bundle['heads'])}")
    print(f"  room classes    : {list(room_encoder.classes_)}")
    print(f"  location classes: {list(location_encoder.classes_)}")


if __name__ == "__main__":
    main()
