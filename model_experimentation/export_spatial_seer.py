"""
Spatial Seer — Supabase Hardware Data Exporter
Exports all rows from hardware_data, parses the embedded csv_dump,
and writes one flat CSV with metadata columns prepended to each row.

Usage:
    pip install supabase pandas
    python export_spatial_seer.py
"""

import io
import os
import pandas as pd
from supabase import create_client, Client

# ── CONFIG ────────────────────────────────────────────────────────────────────


OUTPUT_CSV   = "spatial_seer_all_rooms.csv"
PAGE_SIZE    = 100          # rows per Supabase page (max 1000; keep lower for large csv_dumps)
# ─────────────────────────────────────────────────────────────────────────────

# Metadata columns that come directly from the hardware_data row
META_COLS = ["device_id", "room_label", "noise_type", "location", "scan_index"]


def fetch_all_rows(client: Client) -> list[dict]:
    """Paginate through hardware_data and return every row."""
    rows, offset = [], 0
    while True:
        resp = (
            client.table("hardware_data")
            .select("device_id, csv_dump, room_label, noise_type, location")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = resp.data
        if not batch:
            break
        rows.extend(batch)
        print(f"  fetched {len(rows)} rows so far…")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def parse_csv_dump(raw: str) -> pd.DataFrame:
    """
    Parse the whitespace/newline-delimited csv_dump string.
    The first line is the header; subsequent lines are data rows.
    Values are separated by commas; rows are separated by whitespace / newlines.
    """
    raw = raw.strip()

    # Normalise: the dump uses spaces between rows but commas within a row.
    # Split on newlines first; if it looks like one long line, split on the
    # known header token to recover structure.
    lines = [l.strip() for l in raw.splitlines() if l.strip()]

    if len(lines) < 2:
        # Entire dump may be one space-separated blob — reconstruct by finding
        # the header and splitting on the numeric timestamp pattern.
        import re
        # Header is everything up to the first float token
        header_match = re.search(r'\d+\.\d{4},', raw)
        if not header_match:
            return pd.DataFrame()
        header_str = raw[:header_match.start()].strip()
        data_str   = raw[header_match.start():]
        # Each row starts with a float timestamp; split before each one
        row_strings = re.split(r'(?<=\d)\s+(?=\d+\.\d{4},)', data_str.strip())
        lines = [header_str] + row_strings

    csv_text = "\n".join(lines)
    try:
        df = pd.read_csv(io.StringIO(csv_text))
    except Exception as e:
        print(f"    [warn] could not parse csv_dump: {e}")
        return pd.DataFrame()
    return df


def build_scan_id(room_label: str, location: str, noise_type, scan_index: int) -> str:
    return f"{room_label}__{location}__noise{noise_type}__scan{scan_index:02d}"


def main():
    print("Connecting to Supabase…")
    client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Fetching rows from hardware_data…")
    rows = fetch_all_rows(client)
    print(f"Total rows fetched: {len(rows)}")

    # Group rows by (room_label, location, noise_type) to assign scan indices
    # (the DB has 6 scans per combo; we number them 0–5 in fetch order)
    scan_counters: dict[tuple, int] = {}

    all_frames: list[pd.DataFrame] = []

    for row in rows:
        key = (row.get("room_label"), row.get("location"), row.get("noise_type"))
        scan_idx = scan_counters.get(key, 0)
        scan_counters[key] = scan_idx + 1

        csv_dump = row.get("csv_dump", "")
        if not csv_dump:
            print(f"  [skip] empty csv_dump for {key}")
            continue

        df = parse_csv_dump(csv_dump)
        if df.empty:
            print(f"  [skip] unparseable csv_dump for {key}")
            continue

        # Prepend metadata columns
        df.insert(0, "scan_id",    build_scan_id(key[0], key[1], key[2], scan_idx))
        df.insert(1, "scan_index", scan_idx)
        df.insert(2, "room_label", row.get("room_label"))
        df.insert(3, "location",   row.get("location"))
        df.insert(4, "noise_type", row.get("noise_type"))
        df.insert(5, "device_id",  row.get("device_id"))

        all_frames.append(df)

    if not all_frames:
        print("No data to write — check your credentials or table contents.")
        return

    print("Concatenating all frames…")
    combined = pd.concat(all_frames, ignore_index=True)

    print(f"Writing {len(combined):,} time-series rows to '{OUTPUT_CSV}'…")
    combined.to_csv(OUTPUT_CSV, index=False)
    print("Done.")
    print(f"\nShape : {combined.shape}")
    print(f"Rooms : {combined['room_label'].nunique()} unique room labels")
    print(f"Locs  : {combined['location'].nunique()} unique locations")
    print(f"Noise : {sorted(combined['noise_type'].unique())}")
    print(f"Scans : {combined['scan_id'].nunique()} unique scans")
    print(f"\nColumns:\n{list(combined.columns)}")


if __name__ == "__main__":
    main()