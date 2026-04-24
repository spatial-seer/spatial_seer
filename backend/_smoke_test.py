"""One-shot smoke test for the MiniRocket wiring. Safe to delete after.

Exercises the full inference pipeline (bundle load, csv_dump parse,
preprocessing, real head predict, derived head lookup) against real
scans taken from the training CSV, without standing up uvicorn or
hitting Supabase.

Covers three cases:
  1. A seen scan (rescan_num == 0) -- sanity: expect a confident,
     correct prediction.
  2. A held-out scan (rescan_num == 1) -- honest signal: this is what
     the demo model actually has to classify.
  3. A deliberately short csv_dump -- exercises the edge-pad branch.
"""

from __future__ import annotations

import os

# main.py builds a Supabase client in lifespan. We only import helper
# functions here, so stub values are fine.
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_KEY", "dummy")

import pandas as pd

import main as m


UNITY_CSV_COLUMNS = [
    "Timestamp", "TotalUsedMem", "CpuUtil", "GpuUtil", "BatteryMicroAmps",
    "BatteryTemp", "BatteryLevel", "BatteryVoltageMv", "ScreenBrightness",
    "AvgFPS", "WorstFrameMs", "BestFrameMs", "MainThreadMs", "GCAllocRate",
    "FrameTimeStdDev", "CpuClockFreq",
]


def run_scan(bundle, scan: pd.DataFrame, label: str) -> None:
    true_room = scan.iloc[0]["room_label"]
    true_loc = scan.iloc[0]["location"]
    scan_id = scan.iloc[0]["scan_id"]
    rescan_num = int(scan.iloc[0]["rescan_num"])
    print(f"\n[{label}] scan_id={scan_id}")
    print(f"         rescan_num={rescan_num}  rows={len(scan)}")
    print(f"         true: room={true_room:<12} loc={true_loc}")

    csv_dump = scan[UNITY_CSV_COLUMNS].to_csv(index=False)
    record = {"id": 99999, "csv_dump": csv_dump, "device_id": "test"}
    x3d, t_raw = m._record_to_time_series(
        record,
        bundle["channel_names"],
        bundle["series_length"],
        bundle["preprocessing"]["sort_by"],
    )
    assert x3d.shape == (1, len(bundle["channel_names"]), bundle["series_length"])

    preds = m._predict_heads(bundle, x3d)
    print(f"         x3d.shape={x3d.shape}  t_raw={t_raw}")
    for name, (pred_label, conf) in preds.items():
        mark_label = "room" if name == "room" else ("location" if name == "location" else name)
        truth = {"room": true_room, "location": true_loc}.get(name, "?")
        ok = "OK " if pred_label == truth else "MISS"
        print(f"         {ok} {mark_label:10s}: pred={pred_label:<30} conf={conf:.4f}")


def run() -> None:
    bundle = m._load_model_bundle("current_model.pkl")
    print("bundle:", bundle["kind"])
    print("  channels      :", bundle["channel_names"])
    print("  series_length :", bundle["series_length"])
    print("  preprocessing :", bundle["preprocessing"])
    print("  real heads    :", list(bundle["heads"].keys()))
    print("  derived heads :", {k: v["from"] for k, v in bundle.get("derived_heads", {}).items()})

    df = pd.read_csv("../spatial_seer_all_rooms_v3.csv")

    # Case 1: a seen scan (rescan_num == 0).
    seen_id = df[df["rescan_num"] == 0]["scan_id"].iloc[0]
    run_scan(bundle, df[df["scan_id"] == seen_id], "seen (rescan_num=0)")

    # Case 2: a held-out scan (rescan_num == 1) in a qualifying location.
    rescan_df = df[(df["rescan"] == True) & (df["rescan_num"] == 1)]
    if not rescan_df.empty:
        holdout_id = rescan_df["scan_id"].iloc[0]
        run_scan(bundle, df[df["scan_id"] == holdout_id], "held-out (rescan_num=1)")
    else:
        print("\n[held-out] no rescan_num==1 scans in CSV; skipping")

    # Case 3: deliberately short scan to exercise edge-pad + derived head.
    short = df[df["scan_id"] == seen_id].iloc[:3]
    run_scan(bundle, short, "short scan (padding branch)")


if __name__ == "__main__":
    run()
