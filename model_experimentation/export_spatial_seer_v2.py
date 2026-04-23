# ── Re-export with rescan column ─────────────────────────────────────────────
import io
import pandas as pd
from supabase import create_client, Client

SUPABASE_URL = "https://bkbykdicbsxeptltofvl.supabase.co"
SUPABASE_KEY = "sb_publishable_H9JMQP7SVfTem4DlbC6k0A_5_pwwZtE"

OUTPUT_CSV = "spatial_seer_all_rooms_v2.csv"   # new file, old one untouched
PAGE_SIZE  = 100


def fetch_all_rows(client: Client) -> list[dict]:
    rows, offset = [], 0
    while True:
        resp = (
            client.table("hardware_data")
            .select("device_id, csv_dump, room_label, noise_type, location, rescan")
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
    import re
    raw = raw.strip()
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if len(lines) < 2:
        header_match = re.search(r'\d+\.\d{4},', raw)
        if not header_match:
            return pd.DataFrame()
        header_str = raw[:header_match.start()].strip()
        data_str   = raw[header_match.start():]
        row_strings = re.split(r'(?<=\d)\s+(?=\d+\.\d{4},)', data_str.strip())
        lines = [header_str] + row_strings
    try:
        return pd.read_csv(io.StringIO("\n".join(lines)))
    except Exception as e:
        print(f"    [warn] could not parse csv_dump: {e}")
        return pd.DataFrame()


def build_scan_id(room_label, location, noise_type, scan_index, rescan) -> str:
    suffix = "_rescan" if rescan else ""
    return f"{room_label}__{location}__noise{noise_type}__scan{scan_index:02d}{suffix}"


def main():
    print("Connecting to Supabase…")
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Fetching rows…")
    rows = fetch_all_rows(client)
    print(f"Total rows fetched: {len(rows)}")

    scan_counters = {}
    all_frames = []

    for row in rows:
        rescan = bool(row.get("rescan", False))
        key    = (row.get("room_label"), row.get("location"), row.get("noise_type"), rescan)
        scan_idx = scan_counters.get(key, 0)
        scan_counters[key] = scan_idx + 1

        csv_dump = row.get("csv_dump", "")
        if not csv_dump:
            continue

        df = parse_csv_dump(csv_dump)
        if df.empty:
            continue

        df.insert(0, "scan_id",    build_scan_id(key[0], key[1], key[2], scan_idx, rescan))
        df.insert(1, "scan_index", scan_idx)
        df.insert(2, "room_label", row.get("room_label"))
        df.insert(3, "location",   row.get("location"))
        df.insert(4, "noise_type", row.get("noise_type"))
        df.insert(5, "device_id",  row.get("device_id"))
        df.insert(6, "rescan",     rescan)

        all_frames.append(df)

    combined = pd.concat(all_frames, ignore_index=True)
    combined.to_csv(OUTPUT_CSV, index=False)

    print(f"\nShape  : {combined.shape}")
    print(f"Rooms  : {combined['room_label'].nunique()} unique room labels")
    print(f"Locs   : {combined['location'].nunique()} unique locations")
    print(f"Rescan : {combined['rescan'].sum()} rescan rows  /  {(~combined['rescan']).sum()} original rows")
    print(f"Saved  : {OUTPUT_CSV}")


if __name__ == "__main__":
    main()