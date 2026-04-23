"""One-shot smoke test for the MiniRocket wiring. Safe to delete after.

Exercises the full inference pipeline (bundle load, csv_dump parse,
preprocessing, predict) against a real scan taken from the training CSV,
WITHOUT standing up uvicorn or hitting Supabase.
"""

from __future__ import annotations

import os

# main.py requires Supabase env vars to build a client in lifespan. We
# only import its helper functions here, so stub values are fine.
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_KEY", "dummy")

import pandas as pd

import main as m


def run() -> None:
    bundle = m._load_model_bundle("current_model.pkl")
    print("bundle OK:", bundle["kind"], "series_length=", bundle["series_length"])
    print("channels:", bundle["channel_names"])
    print("preprocessing:", bundle["preprocessing"])

    df = pd.read_csv("../spatial_seer_all_rooms_v3.csv")
    scan_id = df["scan_id"].iloc[0]
    scan = df[df["scan_id"] == scan_id]
    true_room = scan.iloc[0]["room_label"]
    true_loc = scan.iloc[0]["location"]
    print(
        f"Test scan_id={scan_id} true_room={true_room} true_loc={true_loc} "
        f"rows={len(scan)}"
    )

    cols_unity = [
        "Timestamp", "TotalUsedMem", "CpuUtil", "GpuUtil", "BatteryMicroAmps",
        "BatteryTemp", "BatteryLevel", "BatteryVoltageMv", "ScreenBrightness",
        "AvgFPS", "WorstFrameMs", "BestFrameMs", "MainThreadMs", "GCAllocRate",
        "FrameTimeStdDev", "CpuClockFreq",
    ]
    csv_dump = scan[cols_unity].to_csv(index=False)
    record = {"id": 99999, "csv_dump": csv_dump, "device_id": "test"}

    x3d, t_raw = m._record_to_time_series(
        record,
        bundle["channel_names"],
        bundle["series_length"],
        bundle["preprocessing"]["sort_by"],
    )
    print(f"x3d.shape={x3d.shape} dtype={x3d.dtype} t_raw={t_raw}")
    assert x3d.shape == (1, len(bundle["channel_names"]), bundle["series_length"])

    preds = m._predict_heads(bundle, x3d)
    print("predictions:")
    for name, (label, conf) in preds.items():
        print(f"  {name:10s}: {label:30s}  conf={conf:.4f}")

    # Additional: exercise the short-scan edge-pad branch.
    short = scan[cols_unity].iloc[:3]
    short_record = {"id": 88888, "csv_dump": short.to_csv(index=False), "device_id": "test"}
    x3d_s, t_raw_s = m._record_to_time_series(
        short_record,
        bundle["channel_names"],
        bundle["series_length"],
        bundle["preprocessing"]["sort_by"],
    )
    print(f"short scan: t_raw={t_raw_s} -> padded x3d.shape={x3d_s.shape}")
    preds_s = m._predict_heads(bundle, x3d_s)
    for name, (label, conf) in preds_s.items():
        print(f"  {name:10s}: {label:30s}  conf={conf:.4f}")


if __name__ == "__main__":
    run()
