"""Spatial Seer real-time inference service.

Supabase Database Webhook (on insert to `hardware_data`)
        -> ngrok tunnel
        -> FastAPI `/webhook/predict`
        -> load `current_model.pkl` via joblib
        -> insert prediction into `live_predictions`

The model bundle is a multi-head contract: one shared feature list, plus
an arbitrary number of prediction "heads" (each a classifier + label
encoder). Every head listed in `HEAD_TO_COLUMN` is predicted per webhook
and written to the corresponding column in `live_predictions`. Adding a
new prediction target is therefore a bundle+schema change, not an API
change.

Expected bundle shape:

    {
        "feature_names": ["TotalUsedMem", "CpuUtil", ...],
        "heads": {
            "room":     {"model": <est>, "label_encoder": <LabelEncoder>},
            "location": {"model": <est>, "label_encoder": <LabelEncoder>},
        },
        "kind": "<free-form version tag>",
    }
"""

from __future__ import annotations

import io
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import joblib
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from postgrest.exceptions import APIError
from supabase import Client, create_client

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("spatial_seer")

MODEL_PATH = os.getenv("MODEL_PATH", "current_model.pkl")
PREDICTIONS_TABLE = os.getenv("PREDICTIONS_TABLE", "live_predictions")
CSV_DUMP_COLUMN = "csv_dump"
# Columns we never feed into the model, even if a future csv_dump variant
# happens to include them. `db_id` and `location` are the originals the
# user flagged; both are metadata, not features.
DROP_COLUMNS = ("db_id", "location")

# Mapping from bundle head name -> live_predictions column name. A head
# only gets persisted if it appears both here AND in the loaded bundle.
# To add a third prediction target (e.g. noise_type):
#   1. Train the head in create_dummy_model.py under heads["noise_type"].
#   2. Add "noise_type": "predicted_noise_type" to this dict.
#   3. `alter table public.live_predictions add column predicted_noise_type text;`
HEAD_TO_COLUMN: dict[str, str] = {
    "room": "predicted_room",
    "location": "predicted_location",
}

_state: dict[str, Any] = {"model_bundle": None, "supabase": None}


_SUPABASE_KEY_ENV_VARS = (
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_KEY",
    "SUPABASE_ANON_KEY",
    "SUPABASE_PUBLISHABLE_KEY",
)


def _get_supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key: str | None = None
    key_source: str | None = None
    for name in _SUPABASE_KEY_ENV_VARS:
        value = os.environ.get(name)
        if value:
            key = value
            key_source = name
            break

    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and a Supabase key must be set in the environment "
            f"(one of: {', '.join(_SUPABASE_KEY_ENV_VARS)})."
        )

    # Publishable / anon keys are subject to Row Level Security. Inserts into
    # `live_predictions` will fail unless RLS is disabled on that table or a
    # policy explicitly allows the role you're using. Use the service role key
    # in production to bypass RLS safely from the server.
    if key.startswith("sb_publishable_") or key_source in {
        "SUPABASE_ANON_KEY",
        "SUPABASE_PUBLISHABLE_KEY",
    }:
        logger.warning(
            "Using a publishable/anon Supabase key (%s). Writes to '%s' will "
            "fail unless RLS is disabled or a permissive policy is in place.",
            key_source,
            PREDICTIONS_TABLE,
        )
    else:
        logger.info("Using Supabase key from %s", key_source)

    return create_client(url, key)


def _load_model_bundle(path: str) -> dict[str, Any]:
    """Load and validate a multi-head model bundle.

    Raises a clear error if the bundle is the legacy single-head shape
    (`{"model", "label_encoder", ...}`) so the operator knows to retrain
    with the new `create_dummy_model.py`.
    """
    bundle = joblib.load(path)

    if "heads" not in bundle and "model" in bundle:
        raise RuntimeError(
            f"Model bundle at {path} uses the legacy single-head format "
            "(expected top-level 'heads'). Re-run `python create_dummy_model.py` "
            "or retrain your real model with the new multi-head contract."
        )

    required = {"feature_names", "heads"}
    missing = required - set(bundle)
    if missing:
        raise RuntimeError(f"Model bundle at {path} is missing keys: {missing}")

    heads = bundle["heads"]
    if not isinstance(heads, dict) or not heads:
        raise RuntimeError(f"Model bundle 'heads' must be a non-empty dict, got: {heads!r}")
    for head_name, head in heads.items():
        if not isinstance(head, dict) or {"model", "label_encoder"} - set(head):
            raise RuntimeError(
                f"Head '{head_name}' must be a dict with 'model' and 'label_encoder'."
            )
    return bundle


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading model bundle from %s", MODEL_PATH)
    _state["model_bundle"] = _load_model_bundle(MODEL_PATH)
    logger.info("Connecting to Supabase")
    _state["supabase"] = _get_supabase_client()
    logger.info("Startup complete. Ready for webhooks.")
    yield
    _state.clear()


app = FastAPI(title="Spatial Seer Inference", lifespan=lifespan)


def _parse_csv_dump(csv_text: str) -> pd.DataFrame:
    """Parse the multi-line CSV string that lives in `hardware_data.csv_dump`.

    The client writes one header row followed by N sample rows, where each
    sample is a single snapshot of device telemetry (memory, CPU, GPU, FPS,
    etc.). One `hardware_data` row therefore expands into N feature rows.
    """
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise HTTPException(
            status_code=400,
            detail=f"Record missing non-empty '{CSV_DUMP_COLUMN}' text field.",
        )
    try:
        return pd.read_csv(io.StringIO(csv_text))
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to parse '{CSV_DUMP_COLUMN}' as CSV: {exc}",
        ) from exc


def _record_to_feature_frame(record: dict[str, Any], feature_names: list[str]) -> pd.DataFrame:
    """Expand a Supabase `hardware_data` record into the model's feature matrix.

    The actual telemetry lives inside `record['csv_dump']` as CSV text with
    one header row and many sample rows. We parse it, drop metadata columns
    the user flagged, align to the model's declared `feature_names`, and
    coerce to float. Missing features fall back to 0 so a partial payload
    never crashes inference.
    """
    csv_text = record.get(CSV_DUMP_COLUMN)
    df = _parse_csv_dump(csv_text)
    df = df.drop(columns=[c for c in DROP_COLUMNS if c in df.columns], errors="ignore")

    n = len(df)
    if n == 0:
        raise HTTPException(
            status_code=400,
            detail=f"'{CSV_DUMP_COLUMN}' parsed to zero rows.",
        )

    X = pd.DataFrame(0.0, index=range(n), columns=feature_names)
    for name in feature_names:
        if name in df.columns:
            X[name] = pd.to_numeric(df[name], errors="coerce").fillna(0.0).to_numpy()
    return X


def _majority_vote(labels: np.ndarray) -> tuple[str, float]:
    """Return the modal label and its share of the total (confidence proxy)."""
    values, counts = np.unique(labels, return_counts=True)
    idx = int(np.argmax(counts))
    return str(values[idx]), float(counts[idx] / counts.sum())


_MISSING_UNIQUE_CONSTRAINT_CODE = "42P10"
_constraint_missing_warned = False


def _api_error_code(exc: APIError) -> str | None:
    """Pull the Postgres SQLSTATE out of a postgrest APIError in a way that
    survives the library's slightly inconsistent shape across versions."""
    code = getattr(exc, "code", None)
    if code:
        return code
    if exc.args and isinstance(exc.args[0], dict):
        return exc.args[0].get("code")
    return None


def _write_prediction(supabase: Client, row: dict[str, Any]) -> str | None:
    """Write a prediction row idempotently when possible.

    Prefers an UPSERT keyed on `trial_id` so webhook retries do not create
    duplicate rows. If the required UNIQUE constraint is missing from the
    target table, falls back to a plain INSERT so the pipeline keeps
    flowing — but warns loudly so the operator can add the constraint.
    Returns an error string on total failure, else None.
    """
    global _constraint_missing_warned
    try:
        supabase.table(PREDICTIONS_TABLE).upsert(
            row, on_conflict="trial_id"
        ).execute()
        return None
    except APIError as exc:
        if _api_error_code(exc) != _MISSING_UNIQUE_CONSTRAINT_CODE:
            logger.exception("Failed to upsert prediction")
            return str(exc)

        if not _constraint_missing_warned:
            logger.warning(
                "%s.trial_id has no UNIQUE constraint; falling back to INSERT "
                "(webhook retries will create duplicates). Run:\n"
                "  alter table public.%s add constraint "
                "%s_trial_id_key unique (trial_id);",
                PREDICTIONS_TABLE,
                PREDICTIONS_TABLE,
                PREDICTIONS_TABLE,
            )
            _constraint_missing_warned = True

        try:
            supabase.table(PREDICTIONS_TABLE).insert(row).execute()
            return None
        except Exception as fallback_exc:
            logger.exception("Fallback insert also failed")
            return str(fallback_exc)
    except Exception as exc:
        logger.exception("Failed to upsert prediction")
        return str(exc)


@app.get("/health")
def health() -> dict[str, Any]:
    bundle = _state.get("model_bundle")
    heads = list(bundle["heads"].keys()) if bundle else None
    return {
        "ok": True,
        "model_loaded": bundle is not None,
        "model_kind": bundle.get("kind") if bundle else None,
        "heads": heads,
        "head_columns": HEAD_TO_COLUMN,
        "feature_names": bundle.get("feature_names") if bundle else None,
    }


def _predict_heads(
    bundle: dict[str, Any], X: pd.DataFrame
) -> dict[str, tuple[str, float]]:
    """Run every head in the bundle over X. Returns {head_name: (label, confidence)}."""
    X_arr = X.to_numpy()
    predictions: dict[str, tuple[str, float]] = {}
    for head_name, head in bundle["heads"].items():
        y_pred = head["model"].predict(X_arr)
        decoded = head["label_encoder"].inverse_transform(np.asarray(y_pred))
        predictions[head_name] = _majority_vote(decoded)
    return predictions


@app.post("/webhook/predict")
async def webhook_predict(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    record = payload.get("record") if isinstance(payload, dict) else None
    if not isinstance(record, dict):
        raise HTTPException(status_code=400, detail="Webhook payload missing 'record' object.")

    bundle = _state.get("model_bundle")
    if bundle is None:
        raise HTTPException(status_code=503, detail="Model not loaded.")

    feature_names: list[str] = bundle["feature_names"]
    X = _record_to_feature_frame(record, feature_names)

    try:
        head_predictions = _predict_heads(bundle, X)
    except Exception as exc:
        logger.exception("Prediction failed")
        raise HTTPException(status_code=500, detail=f"Prediction failed: {exc}") from exc

    trial_id = record.get("trial_id") or record.get("id")
    n_samples = len(X)

    # Build the DB row from head predictions that map to known columns.
    # Heads present in the bundle but not in HEAD_TO_COLUMN are still
    # returned in the HTTP response but not persisted.
    row: dict[str, Any] = {"trial_id": trial_id}
    for head_name, column in HEAD_TO_COLUMN.items():
        if head_name in head_predictions:
            row[column] = head_predictions[head_name][0]

    supabase: Client | None = _state.get("supabase")
    insert_error: str | None = None
    if supabase is not None:
        insert_error = _write_prediction(supabase, row)

    # Nice compact log line: "room=kitchen(1.00) location=Floor3Kitchen(0.83)"
    pretty = " ".join(
        f"{name}={label}({conf:.2f})"
        for name, (label, conf) in head_predictions.items()
    )
    logger.info("trial_id=%s samples=%d -> %s", trial_id, n_samples, pretty)

    return {
        "trial_id": trial_id,
        "n_samples": n_samples,
        "predictions": {
            name: {"label": label, "confidence": round(conf, 4)}
            for name, (label, conf) in head_predictions.items()
        },
        "written_columns": {
            column: row[column]
            for column in HEAD_TO_COLUMN.values()
            if column in row
        },
        "insert_error": insert_error,
    }
