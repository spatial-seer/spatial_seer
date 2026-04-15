import io
import os
from typing import Optional

import pandas as pd
from supabase import Client, create_client

# Pull and unpack telemetry snapshots from the Supabase hardware_data table.

CSV_COLUMNS = [
    "Timestamp",
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


def load_local_env(env_path: str = ".env") -> None:
    """Load key/value pairs from a local .env file into os.environ."""
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_supabase_client() -> Client:
    load_local_env()
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")

    if not url or not key:
        raise ValueError(
            "Missing Supabase credentials. Set NEXT_PUBLIC_SUPABASE_URL and "
            "NEXT_PUBLIC_SUPABASE_ANON_KEY in your environment or .env file."
        )

    return create_client(url, key)


def normalize_csv_dump(csv_dump: str) -> str:
    """
    Normalize csv_dump payload into parseable CSV text.
    Handles both literal '\\n' strings and real newlines.
    """
    return csv_dump.replace("\\r", "").replace("\\n", "\n").strip()


def parse_hardware_csv(csv_dump: str, record_id: Optional[int]) -> Optional[pd.DataFrame]:
    if not csv_dump:
        return None

    try:
        normalized = normalize_csv_dump(csv_dump)
        df = pd.read_csv(io.StringIO(normalized))
    except Exception as exc:
        print(f"Error parsing CSV for record ID {record_id}: {exc}")
        return None

    # Keep known telemetry columns in expected order when present.
    existing = [col for col in CSV_COLUMNS if col in df.columns]
    if existing:
        df = df[existing]
    else:
        print(f"Warning: no expected telemetry columns found for record ID {record_id}.")

    # Convert telemetry columns to numeric where possible.
    for col in CSV_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def fetch_and_unpack_hardware_data(max_rows: int = 1000) -> Optional[pd.DataFrame]:
    supabase = build_supabase_client()

    print("Fetching records from Supabase table: hardware_data ...")
    response = (
        supabase.table("hardware_data")
        .select("*")
        .order("id", desc=False)
        .limit(max_rows)
        .execute()
    )
    records = response.data or []
    print(f"Successfully downloaded {len(records)} records.")

    all_rows = []
    print("Unpacking nested CSV payloads...")

    for record in records:
        record_id = record.get("id")
        created_at = record.get("created_at")
        device_id = record.get("device_id")
        room_label = record.get("room_label")
        noise_type = record.get("noise_type")
        location = record.get("location")
        csv_dump = record.get("csv_dump")

        temp_df = parse_hardware_csv(csv_dump, record_id)
        if temp_df is None or temp_df.empty:
            continue

        # Attach table metadata so each telemetry row keeps experiment context.
        temp_df["db_id"] = record_id
        temp_df["created_at"] = created_at
        temp_df["device_id"] = device_id
        temp_df["room_label"] = room_label
        temp_df["noise_type"] = noise_type
        temp_df["location"] = location

        all_rows.append(temp_df)

    if not all_rows:
        print("No valid CSV data found in hardware_data.")
        return None

    master_df = pd.concat(all_rows, ignore_index=True)
    print(f"Master dataset created with {len(master_df)} telemetry rows.")

    output_filename = "vr_hardware_master_dataset.csv"
    master_df.to_csv(output_filename, index=False)
    print(f"Data saved locally to: {output_filename}")

    return master_df


if __name__ == "__main__":
    df = fetch_and_unpack_hardware_data()
    if df is not None:
        print(df.head())
