"""Spatial Seer real-time inference service.

Supabase Database Webhook (on insert to `hardware_data`)
        -> ngrok tunnel
        -> FastAPI `/webhook/predict`
        -> load `current_model.pkl` via joblib
        -> insert prediction into `live_predictions`

The model is a **multivariate time-series classifier** (MiniRocket family).
Each `hardware_data` row is one time-series instance: the N rows inside
`csv_dump` are the temporal axis of a single series with C channels. The
server preprocesses to match the trainer exactly -- sort by `Timestamp`,
truncate to `series_length` -- then hands a `(1, C, series_length)` float32
array to each head's model.

Bundle shape (produced by `create_dummy_model.py` or the real trainer):

    {
        "channel_names":  ["GpuUtil", "CpuUtil", ...],   # ordered
        "series_length":  int,                             # truncation target
        "preprocessing":  {"method": "truncate", "sort_by": "Timestamp"},
        "heads": {
            "room":     {"model": <est>, "label_encoder": <LabelEncoder>},
            "location": {"model": <est>, "label_encoder": <LabelEncoder>},
        },
        "kind": "<free-form version tag>",
    }

`feature_names` is accepted as a back-compat alias for `channel_names`.
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
# happens to include them. `db_id` and `location` are metadata, not signal.
DROP_COLUMNS = ("db_id", "location")

# Only "truncate" is supported today. Any unknown value in the bundle's
# preprocessing block is a hard error -- we refuse to silently invent
# preprocessing logic the trainer didn't ask for.
_SUPPORTED_RESAMPLE_METHODS = frozenset({"truncate"})
_DEFAULT_PREPROCESSING = {"method": "truncate", "sort_by": "Timestamp"}

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

# One-time warning guards so we don't spam the log on every webhook.
_missing_channels_warned: set[str] = set()
_short_scan_warned = False


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


def _normalize_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy keys and fill preprocessing defaults in place."""
    # Accept `feature_names` as an alias for `channel_names` -- old trainers
    # and the v1 XGBoost-era bundles use the former; the new contract
    # canonicalises on `channel_names` because that's what it actually is.
    if "channel_names" not in bundle and "feature_names" in bundle:
        bundle["channel_names"] = bundle["feature_names"]

    pre = bundle.get("preprocessing")
    if pre is None:
        bundle["preprocessing"] = dict(_DEFAULT_PREPROCESSING)
    elif isinstance(pre, dict):
        # Fill missing keys with defaults so downstream code can index safely.
        for k, v in _DEFAULT_PREPROCESSING.items():
            pre.setdefault(k, v)
    return bundle


def _load_model_bundle(path: str) -> dict[str, Any]:
    """Load and validate a MiniRocket-era multi-head model bundle.

    Raises a clear error if the bundle is an older shape (single-head v0,
    or per-sample v1 missing `series_length`) so the operator knows to
    retrain with `create_dummy_model.py` or the real trainer.
    """
    bundle = joblib.load(path)

    if "heads" not in bundle and "model" in bundle:
        raise RuntimeError(
            f"Model bundle at {path} uses the legacy single-head format "
            "(expected top-level 'heads'). Re-run `python create_dummy_model.py` "
            "or retrain with the current multi-head contract."
        )

    required = {"feature_names", "heads"} if "channel_names" not in bundle else {"channel_names", "heads"}
    missing = required - set(bundle)
    if missing:
        raise RuntimeError(f"Model bundle at {path} is missing keys: {missing}")

    bundle = _normalize_bundle(bundle)

    series_length = bundle.get("series_length")
    if not isinstance(series_length, int) or series_length <= 0:
        raise RuntimeError(
            f"Model bundle at {path} is missing a positive integer "
            "'series_length'. This key is required for the MiniRocket-era "
            "contract -- retrain with `create_dummy_model.py` or the real "
            "trainer so it's written into the bundle."
        )

    method = bundle["preprocessing"].get("method")
    if method not in _SUPPORTED_RESAMPLE_METHODS:
        raise RuntimeError(
            f"Model bundle at {path} requests preprocessing method "
            f"{method!r}, which is not supported by this server. Supported: "
            f"{sorted(_SUPPORTED_RESAMPLE_METHODS)}."
        )

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
    bundle = _load_model_bundle(MODEL_PATH)
    _state["model_bundle"] = bundle
    logger.info(
        "Bundle loaded: kind=%s heads=%s channels=%d series_length=%d preprocessing=%s",
        bundle.get("kind"),
        list(bundle["heads"].keys()),
        len(bundle["channel_names"]),
        bundle["series_length"],
        bundle["preprocessing"],
    )
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
    etc.). One `hardware_data` row therefore expands into N timesteps of a
    single multivariate series.
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


def _record_to_time_series(
    record: dict[str, Any],
    channel_names: list[str],
    series_length: int,
    sort_by: str,
) -> tuple[np.ndarray, int]:
    """Expand a Supabase `hardware_data` record into a `(1, C, T)` tensor.

    Matches the trainer's preprocessing exactly:
        1. Parse `csv_dump` -> DataFrame.
        2. Drop metadata columns (`db_id`, `location`).
        3. Require the sort column (`Timestamp`) and sort ascending.
        4. Reindex to `channel_names` (missing channel -> zeros, warned once).
        5. Coerce to float, NaN -> 0.
        6. Length-match to `series_length`:
             T_raw > series_length  -> truncate to first `series_length`
             T_raw == series_length -> as-is
             T_raw < series_length  -> edge-pad (repeat last row), warn once
             T_raw == 0             -> 400
        7. Reshape to `(1, C, series_length)` float32.

    Returns (X3d, T_raw) so the webhook handler can log raw length
    separately from the post-truncation length.
    """
    global _short_scan_warned

    csv_text = record.get(CSV_DUMP_COLUMN)
    df = _parse_csv_dump(csv_text)
    df = df.drop(columns=[c for c in DROP_COLUMNS if c in df.columns], errors="ignore")

    t_raw = len(df)
    if t_raw == 0:
        raise HTTPException(
            status_code=400,
            detail=f"'{CSV_DUMP_COLUMN}' parsed to zero rows.",
        )

    if sort_by not in df.columns:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{CSV_DUMP_COLUMN}' is missing required sort column "
                f"'{sort_by}'. This server matches trainer preprocessing by "
                f"sorting on that column before truncation; inference would "
                f"be skewed without it."
            ),
        )
    df = df.sort_values(sort_by, kind="mergesort").reset_index(drop=True)

    # Warn once per missing channel so operators notice schema drift
    # without drowning the log.
    for name in channel_names:
        if name not in df.columns and name not in _missing_channels_warned:
            logger.warning(
                "Channel '%s' missing from csv_dump; zero-filling. Check "
                "Unity client header vs bundle channel_names.",
                name,
            )
            _missing_channels_warned.add(name)

    # Align to declared channel order, coerce, fillna.
    aligned = pd.DataFrame(0.0, index=range(t_raw), columns=channel_names)
    for name in channel_names:
        if name in df.columns:
            aligned[name] = (
                pd.to_numeric(df[name], errors="coerce").fillna(0.0).to_numpy()
            )

    arr = aligned.to_numpy(dtype=np.float32, copy=False)  # (T_raw, C)

    if t_raw >= series_length:
        arr = arr[:series_length]
    else:
        # Edge-pad: repeat last row to pad up to series_length. Trainer
        # never sees this path (SERIES_LEN is set from min scan length),
        # so it's purely a serving-time resilience move.
        if not _short_scan_warned:
            logger.warning(
                "Inference scan has %d rows, shorter than series_length=%d. "
                "Edge-padding with last row. Further short-scan warnings "
                "suppressed.",
                t_raw,
                series_length,
            )
            _short_scan_warned = True
        pad = np.repeat(arr[-1:, :], series_length - t_raw, axis=0)
        arr = np.concatenate([arr, pad], axis=0)

    # (series_length, C) -> (C, series_length) -> (1, C, series_length)
    x3d = np.ascontiguousarray(arr.T[np.newaxis, :, :], dtype=np.float32)
    return x3d, t_raw


def _softmax(scores: np.ndarray) -> np.ndarray:
    """Numerically stable softmax along the last axis."""
    shifted = scores - np.max(scores, axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.sum(exp, axis=-1, keepdims=True)


def _confidence_from_model(model: Any, x3d: np.ndarray) -> float:
    """Graceful cascade: predict_proba -> decision_function -> 1.0.

    aeon's MiniRocketClassifier uses RidgeClassifierCV under the hood,
    which has no `predict_proba`. The decision_function path is what
    actually runs in practice; predict_proba is here for forward
    compatibility with other trainers.
    """
    if hasattr(model, "predict_proba"):
        try:
            proba = np.asarray(model.predict_proba(x3d))
            return float(proba[0].max())
        except Exception:
            logger.debug("predict_proba failed, falling back to decision_function", exc_info=True)

    if hasattr(model, "decision_function"):
        try:
            scores = np.asarray(model.decision_function(x3d))
            if scores.ndim == 1:
                # Binary classifier returning (n_instances,) decision values.
                p = 1.0 / (1.0 + np.exp(-scores[0]))
                return float(max(p, 1.0 - p))
            # Multiclass: (n_instances, n_classes)
            probs = _softmax(scores[0])
            return float(probs.max())
        except Exception:
            logger.debug("decision_function failed, returning 1.0", exc_info=True)

    return 1.0


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
    flowing -- but warns loudly so the operator can add the constraint.
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
        "channel_names": bundle.get("channel_names") if bundle else None,
        "feature_names": bundle.get("channel_names") if bundle else None,
        "series_length": bundle.get("series_length") if bundle else None,
        "preprocessing": bundle.get("preprocessing") if bundle else None,
    }


def _predict_heads(
    bundle: dict[str, Any], x3d: np.ndarray
) -> dict[str, tuple[str, float]]:
    """Run every head in the bundle over a single time-series instance.

    Returns {head_name: (label, confidence)}. x3d must be shape
    (1, n_channels, series_length).
    """
    predictions: dict[str, tuple[str, float]] = {}
    for head_name, head in bundle["heads"].items():
        model = head["model"]
        y_pred = np.asarray(model.predict(x3d))
        idx = int(y_pred[0])
        label = str(head["label_encoder"].inverse_transform(np.asarray([idx]))[0])
        conf = _confidence_from_model(model, x3d)
        predictions[head_name] = (label, conf)
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

    channel_names: list[str] = bundle["channel_names"]
    series_length: int = bundle["series_length"]
    sort_by: str = bundle["preprocessing"].get("sort_by", "Timestamp")

    x3d, t_raw = _record_to_time_series(record, channel_names, series_length, sort_by)

    try:
        head_predictions = _predict_heads(bundle, x3d)
    except Exception as exc:
        logger.exception("Prediction failed")
        raise HTTPException(status_code=500, detail=f"Prediction failed: {exc}") from exc

    trial_id = record.get("trial_id") or record.get("id")

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

    pretty = " ".join(
        f"{name}={label}({conf:.2f})"
        for name, (label, conf) in head_predictions.items()
    )
    logger.info(
        "trial_id=%s raw_samples=%d series_length=%d -> %s",
        trial_id,
        t_raw,
        series_length,
        pretty,
    )

    return {
        "trial_id": trial_id,
        "raw_samples": t_raw,
        "series_length": series_length,
        "n_samples": series_length,  # back-compat alias
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
