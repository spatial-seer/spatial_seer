"""
export_scans.py
---------------
Exports rows from the Supabase `spatial_scans` table as individual CSV files.

Each scan is exported into its own subdirectory:
    exports/{room_label}__{noise_type}__{location}__id{id}/
        timeseries.csv       <- csv_dump
        object_snapshot.csv  <- per-object snapshot
        room_snapshot.csv    <- per-scan room dimensions

Already-exported IDs are detected from existing subdirectories,
so re-running the script is safe and idempotent.

Setup:
    pip install supabase python-dotenv

Configure via environment variables or a .env file:
    SUPABASE_URL=https://your-project.supabase.co
    SUPABASE_KEY=your-service-role-or-anon-key
    EXPORT_DIR=./exports          # optional, defaults to ./exports
"""

import os
import re
import sys
import csv
import io
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
EXPORT_DIR   = Path(os.environ.get("EXPORT_DIR", "./exports"))

TABLE        = "spatial_scans"
PAGE_SIZE    = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(value: str) -> str:
    value = str(value).strip()
    value = re.sub(r"[^\w\-]", "_", value)
    value = re.sub(r"_+", "_", value)
    return value


def build_dirname(row: dict) -> str:
    parts = [
        slugify(row.get("room_label") or "unlabeled"),
        slugify(str(row.get("noise_type")) if row.get("noise_type") is not None else "nonoise"),
        slugify(row.get("location")   or "nolocation"),
    ]
    return "__".join(parts) + f"__id{row['id']}"


def already_exported_ids(export_dir: Path) -> set:
    pattern = re.compile(r"__id(\d+)$")
    ids = set()
    if export_dir.exists():
        for entry in export_dir.iterdir():
            if entry.is_dir():
                m = pattern.search(entry.name)
                if m:
                    ids.add(int(m.group(1)))
    return ids


def write_csv(path: Path, csv_text: str) -> None:
    reader = csv.reader(io.StringIO(csv_text, newline=""))
    rows   = list(reader)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)


def export_row(row: dict, export_dir: Path) -> None:
    dirname  = build_dirname(row)
    scan_dir = export_dir / dirname
    scan_dir.mkdir(parents=True, exist_ok=True)

    csv_dump = row.get("csv_dump")
    if csv_dump:
        write_csv(scan_dir / "timeseries.csv", csv_dump)
    else:
        log.warning("  id=%s -- csv_dump is empty, skipping timeseries.csv", row["id"])

    obj_snap = row.get("object_snapshot")
    if obj_snap:
        write_csv(scan_dir / "object_snapshot.csv", obj_snap)
    else:
        log.warning("  id=%s -- object_snapshot is empty, skipping object_snapshot.csv", row["id"])

    room_snap = row.get("room_snapshot")
    if room_snap:
        write_csv(scan_dir / "room_snapshot.csv", room_snap)
    else:
        log.warning("  id=%s -- room_snapshot is empty, skipping room_snapshot.csv", row["id"])

    log.info("  Exported  ->  %s/", dirname)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error(
            "SUPABASE_URL and SUPABASE_KEY must be set "
            "(export them or put them in a .env file)."
        )
        sys.exit(1)

    try:
        from supabase import create_client, Client
    except ImportError:
        log.error("supabase package not found.  Run:  pip install supabase python-dotenv")
        sys.exit(1)

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    done_ids = already_exported_ids(EXPORT_DIR)
    log.info("Found %d already-exported scan(s) in '%s'.", len(done_ids), EXPORT_DIR)

    exported = 0
    skipped  = 0
    errors   = 0
    offset   = 0

    while True:
        response = (
            client.table(TABLE)
            .select("id, room_label, noise_type, location, csv_dump, object_snapshot, room_snapshot")
            .order("id")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )

        rows = response.data
        if not rows:
            break

        for row in rows:
            row_id = row.get("id")

            if row_id is None:
                log.warning("Row without an id -- skipping: %s", row)
                errors += 1
                continue

            if row_id in done_ids:
                skipped += 1
                continue

            try:
                export_row(row, EXPORT_DIR)
                done_ids.add(row_id)
                exported += 1
            except Exception as exc:
                log.error("Failed to export id=%s: %s", row_id, exc)
                errors += 1

        offset += PAGE_SIZE
        if len(rows) < PAGE_SIZE:
            break

    log.info(
        "Done.  Exported: %d  |  Skipped (already existed): %d  |  Errors: %d",
        exported, skipped, errors,
    )


if __name__ == "__main__":
    main()