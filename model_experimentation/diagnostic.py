import pandas as pd

df = pd.read_csv("spatial_seer_all_rooms_v3.csv")
scan_df = df.drop_duplicates("scan_id")

print("=== OVERALL ===")
print(f"Total time-series rows : {len(df)}")
print(f"Total unique scans     : {len(scan_df)}")
print(f"Unique locations       : {scan_df['location'].nunique()}")
print(f"Unique room types      : {scan_df['room_label'].nunique()}")

print("\n=== SCANS PER RESCAN BATCH ===")
print(scan_df.groupby(["rescan_num", "rescan"]).size().rename("scans").to_string())

print("\n=== PER LOCATION BREAKDOWN ===")
summary = (
    scan_df.groupby(["room_label", "location", "rescan_num"])
    .size()
    .rename("scans")
    .reset_index()
    .sort_values(["room_label", "location", "rescan_num"])
)
print(summary.to_string(index=False))

print("\n=== LOCATIONS WITH RESCAN DATA ===")
rescan_locs = scan_df[scan_df["rescan"] == True]["location"].unique()
print(sorted(rescan_locs))

print("\n=== LOCATIONS WITH NO RESCAN DATA ===")
no_rescan = [l for l in scan_df["location"].unique() if l not in rescan_locs]
print(sorted(no_rescan))

print("\n=== NOISE LEVEL COVERAGE PER LOCATION + BATCH ===")
print(
    scan_df.groupby(["location", "rescan_num", "noise_type"])
    .size()
    .rename("scans")
    .to_string()
)