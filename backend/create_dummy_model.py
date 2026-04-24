"""Trains the Spatial Seer demo model: a MiniRocket location classifier
plus a deterministic location -> room_type lookup.

Run once:

    python create_dummy_model.py

Mirrors `model_experimentation/vturcs_models.ipynb` Cells 1+2 exactly:

    * 7 telemetry channels
    * series_length = minimum scan length across the full dataset
    * sort by Timestamp, truncate to series_length
    * aeon MiniRocketClassifier fit on location labels
    * train ONLY on rescan_num == 0 (baseline scans); rescan_num == 1 is
      reserved for held-out evaluation in the notebook
    * LabelEncoder fit on all locations in the full dataset (so the
      encoder's class space survives changes to the training slice)
    * room_type predicted indirectly via a `loc_to_room` mapping stored
      in the bundle under `derived_heads`

The server treats `derived_heads` as first-class: `_predict_heads` runs
the real classifier, then fills in the room label from the mapping.
HEAD_TO_COLUMN and the DB-write path need no changes.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from aeon.classification.convolution_based import MiniRocketClassifier
from sklearn.preprocessing import LabelEncoder

MODEL_PATH = Path(__file__).with_name("current_model.pkl")
CSV_PATH = Path(__file__).resolve().parents[1] / "spatial_seer_all_rooms_v3.csv"

# 7 channels, order matches the notebook. This becomes `channel_names`
# in the bundle and defines the channel axis of the model input tensor.
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
TRAIN_RESCAN_NUM = 0  # matches Cell 1: train on baseline scans only
RANDOM_STATE = 42


def _build_scan_array(
    df: pd.DataFrame, series_len: int, channels: list[str]
) -> tuple[np.ndarray, list[str]]:
    """Port of Cell 1's `build_scan_array`. Returns (X, locs)."""
    records = []
    for _scan_id, group in df.groupby("scan_id"):
        group = group.sort_values(SORT_BY, kind="mergesort").iloc[:series_len]
        if len(group) < series_len:
            # Defensive: a scan shorter than the dataset minimum shouldn't
            # exist, but skip rather than pad at training time if it does.
            continue
        records.append({
            "location": group.iloc[0]["location"],
            "ts":       group[channels].to_numpy(dtype=np.float32).T,
        })
    X = np.stack([r["ts"] for r in records]).astype(np.float32, copy=False)
    locs = [r["location"] for r in records]
    return X, locs


def main() -> None:
    if not CSV_PATH.exists():
        raise FileNotFoundError(
            f"Training CSV not found at {CSV_PATH}. Place "
            "`spatial_seer_all_rooms_v3.csv` alongside the repo's top-level "
            "`spatial_seer/` folder, or update CSV_PATH."
        )

    print(f"Loading {CSV_PATH}")
    df = pd.read_csv(CSV_PATH)

    required = CHANNEL_NAMES + [SORT_BY, "scan_id", "location", "room_label", "rescan_num"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"CSV is missing expected columns: {missing}")

    # series_length is dataset-wide minimum (Cell 1 semantics). Applies
    # to both training and inference via the bundle.
    series_length = int(df.groupby("scan_id").size().min())

    # Encoder fit across ALL locations so the class space is stable.
    all_locations = sorted(df["location"].unique())
    loc_enc = LabelEncoder().fit(all_locations)

    # Location -> room_type lookup from the full dataset. Stored verbatim
    # in the bundle under derived_heads["room"]["mapping"].
    loc_to_room: dict[str, str] = (
        df.drop_duplicates("location")
          .set_index("location")["room_label"]
          .to_dict()
    )

    train_df = df[df["rescan_num"] == TRAIN_RESCAN_NUM].reset_index(drop=True)
    X_train, train_locs = _build_scan_array(train_df, series_length, CHANNEL_NAMES)
    y_train_loc = loc_enc.transform(train_locs)

    print(f"Total scans in CSV  : {df['scan_id'].nunique()}")
    print(f"All locations       : {len(all_locations)}")
    print(f"Series length       : {series_length}")
    print(f"Train filter        : rescan_num == {TRAIN_RESCAN_NUM}")
    print(f"Train scans         : {len(X_train)}")
    print(f"Train locations seen: {len(set(train_locs))}/{len(all_locations)}")
    print(f"X_train shape       : {X_train.shape}\n")

    print("Training MiniRocket location head")
    clf = MiniRocketClassifier(random_state=RANDOM_STATE)
    clf.fit(X_train, y_train_loc)

    bundle = {
        "channel_names": CHANNEL_NAMES,
        "series_length": series_length,
        "preprocessing": {"method": "truncate", "sort_by": SORT_BY},
        "heads": {
            "location": {"model": clf, "label_encoder": loc_enc},
        },
        "derived_heads": {
            "room": {"from": "location", "mapping": loc_to_room},
        },
        "kind": "minirocket-vturcs-v1",
    }
    joblib.dump(bundle, MODEL_PATH)

    print(f"\nWrote model bundle to {MODEL_PATH}")
    print(f"  kind             : {bundle['kind']}")
    print(f"  channel_names    : {CHANNEL_NAMES}")
    print(f"  series_length    : {series_length}")
    print(f"  preprocessing    : {bundle['preprocessing']}")
    print(f"  real heads       : {list(bundle['heads'])}")
    print(f"  derived heads    : {list(bundle['derived_heads'])}")
    print(f"  location classes : {len(loc_enc.classes_)} ({list(loc_enc.classes_)})")
    print(f"  room types       : {sorted(set(loc_to_room.values()))}")


if __name__ == "__main__":
    main()
