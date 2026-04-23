"""
export_scans.py
---------------
Exports rows from the Supabase `exfiltrated_data` table as individual CSV files.

Each file is named:
    {room_label}__{noise_type}__{location}__id{id}.csv

Already-exported IDs are detected from filenames in the output directory,
so re-running the script is safe and idempotent — it only writes new rows.

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

TABLE        = "exfiltrated_data"
PAGE_SIZE    = 100          # rows fetched per Supabase request


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(value: str) -> str:
    """Make a string safe for use in a filename."""
    value = str(value).strip()
    value = re.sub(r"[^\w\-]", "_", value)   # replace non-word chars
    value = re.sub(r"_+", "_", value)         # collapse consecutive underscores
    return value


def build_filename(row: dict) -> str:
    """
    Pattern:  {room_label}__{noise_type}__{location}__id{id}.csv
    Any of the label fields may be None / empty — slugify handles that.
    """
    parts = [
        slugify(row.get("room_label")  or "unlabeled"),
        slugify(row.get("noise_type")  or "nonoise"),
        slugify(row.get("location")    or "nolocation"),
    ]
    return "__".join(parts) + f"__id{row['id']}.csv"


def already_exported_ids(export_dir: Path) -> set:
    """
    Scan the export directory for files matching __id{N}.csv and return
    the set of integer IDs that have already been written.
    """
    pattern = re.compile(r"__id(\d+)\.csv$", re.IGNORECASE)
    ids = set()
    if export_dir.exists():
        for f in export_dir.iterdir():
            m = pattern.search(f.name)
            if m:
                ids.add(int(m.group(1)))
    return ids


def write_csv(path: Path, csv_text: str) -> None:
    """
    Write the csv_dump string to disk.  Normalises line endings so the
    file is always written with the platform's native newline.
    """
    reader = csv.reader(io.StringIO(csv_text, newline=""))
    rows   = list(reader)

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)


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

    # ── Find IDs already on disk ────────────────────────────────────────────
    done_ids = already_exported_ids(EXPORT_DIR)
    log.info("Found %d already-exported scan(s) in '%s'.", len(done_ids), EXPORT_DIR)

    # ── Paginate through the table ──────────────────────────────────────────
    exported   = 0
    skipped    = 0
    errors     = 0
    offset     = 0

    while True:
        response = (
            client.table(TABLE)
            .select("id, room_label, noise_type, location, csv_dump")
            .order("id")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )

        rows = response.data
        if not rows:
            break   # no more data

        for row in rows:
            row_id = row.get("id")

            if row_id is None:
                log.warning("Row without an id — skipping: %s", row)
                errors += 1
                continue

            if row_id in done_ids:
                skipped += 1
                continue

            csv_dump = row.get("csv_dump")
            if not csv_dump:
                log.warning("Row id=%s has empty csv_dump — skipping.", row_id)
                errors += 1
                continue

            filename = build_filename(row)
            out_path = EXPORT_DIR / filename

            try:
                write_csv(out_path, csv_dump)
                done_ids.add(row_id)   # prevent double-write within same run
                exported += 1
                log.info("  Exported  →  %s", filename)
            except Exception as exc:
                log.error("Failed to write %s: %s", filename, exc)
                errors += 1

        offset += PAGE_SIZE
        if len(rows) < PAGE_SIZE:
            break   # last page

    # ── Summary ─────────────────────────────────────────────────────────────
    log.info(
        "Done.  Exported: %d  |  Skipped (already existed): %d  |  Errors: %d",
        exported, skipped, errors,
    )


if __name__ == "__main__":
    main()
