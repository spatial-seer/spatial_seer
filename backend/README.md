# Spatial Seer — Backend

Real-time FastAPI inference service. A Supabase Database Webhook on the
`hardware_data` table POSTs the inserted row to this service through an
`ngrok` tunnel; the service runs a classifier and writes the result to the
`live_predictions` table.

## Files

- `main.py` — FastAPI app with `POST /webhook/predict` and `GET /health`.
- `create_dummy_model.py` — Trains placeholder MiniRocket classifiers
  (via `aeon`) against the real labeled CSV so the API boots with a
  representative model before the final notebook trainer is ready.
- `current_model.pkl` — joblib bundle produced by `create_dummy_model.py`
  or the real trainer (see "Model bundle contract" below).
- `ROADMAP.md` — Living plan + decision log for ongoing backend changes.
- `requirements.txt` — Pinned dependency list.
- `.env.example` — Template for Supabase credentials.

## One-time setup

```powershell
cd spatialseer\spatial_seer\backend

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt

python create_dummy_model.py

Copy-Item .env.example .env
# edit .env with your Supabase URL + service role key
```

## Run the API locally

```powershell
$env:SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
$env:SUPABASE_SERVICE_ROLE_KEY = "..."

uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Health check: <http://localhost:8000/health>

## Expose to Supabase via ngrok

```powershell
ngrok http 8000
```

Copy the HTTPS forwarding URL, e.g. `https://abcd-1234.ngrok-free.app`.

## Configure the Supabase webhook

Supabase Dashboard → Database → Webhooks → **Create a new hook**

| Field | Value |
| --- | --- |
| Name | `spatial-seer-predict` |
| Table | `hardware_data` |
| Events | **Insert** |
| Type | HTTP Request |
| Method | `POST` |
| URL | `https://<your-ngrok>.ngrok-free.app/webhook/predict` |
| HTTP Headers | `Content-Type: application/json` |

Supabase sends a JSON body shaped like:

```json
{
  "type": "INSERT",
  "table": "hardware_data",
  "record": { "id": 23, "device_id": "...", "room_label": "kitchen", ... },
  "schema": "public",
  "old_record": null
}
```

The endpoint reads `record`, drops `db_id` and `location`, runs the model,
and inserts `{ trial_id, predicted_room }` into `live_predictions`.

## Time-series inference (MiniRocket era)

Each `hardware_data` row is a **single multivariate time-series instance**
— the N rows inside `csv_dump` are the temporal axis of one scan, not N
independent predictions. The server preprocesses every inbound scan to
match the trainer exactly:

1. Parse `csv_dump` as CSV.
2. Drop metadata columns (`db_id`, `location`).
3. Require the `Timestamp` column; sort ascending. **A missing Timestamp
   is a 400** — we can't match training order without it.
4. Reindex to the bundle's `channel_names` (missing channels → zero-fill,
   one-time warning per channel).
5. Truncate to `series_length` rows (or edge-pad if the scan is shorter,
   with a one-time warning).
6. Reshape to `(1, n_channels, series_length)` `float32`.
7. Call each head's `.predict()` → one label per head, no majority vote.

Confidence comes from `predict_proba` when available, otherwise a
softmax over `decision_function` (aeon's `MiniRocketClassifier` uses
the latter path via `RidgeClassifierCV`).

## Multi-head predictions

The bundle supports multiple heads, which come in two flavours:

- **Real heads** live under `heads` and own a trained model + label
  encoder. They run `.predict()` on the input tensor.
- **Derived heads** live under `derived_heads` and produce a label by
  deterministic lookup from another head's prediction. No model runs;
  confidence is copied from the source head.

Current heads in the demo bundle:

| Head | Kind | Source | DB column | Example output |
| --- | --- | --- | --- | --- |
| `location` | real | — | `predicted_location` | `Floor3Kitchen`, `Outside3102` |
| `room` | derived | `location` | `predicted_room` | `kitchen`, `hallway` |

`HEAD_TO_COLUMN` in `main.py` doesn't care whether a head is real or
derived — any head present in the bundle **and** in the registry gets
its label written to the corresponding column in `live_predictions`.

If you haven't already, make sure `predicted_location` exists on
`live_predictions`:

```sql
alter table public.live_predictions
  add column if not exists predicted_location text;
```

### Adding a third head

Real head (e.g. `noise_type` with its own trained classifier):

1. Fit a classifier in the trainer and drop it into
   `bundle["heads"]["noise_type"]` with a `LabelEncoder`.
2. Add `"noise_type": "predicted_noise_type"` to `HEAD_TO_COLUMN`.
3. `alter table public.live_predictions add column predicted_noise_type text;`.

Derived head (e.g. `floor` looked up from `location`):

1. Add `bundle["derived_heads"]["floor"] = {"from": "location", "mapping": {...}}`.
2. Add `"floor": "predicted_floor"` to `HEAD_TO_COLUMN`.
3. `alter table public.live_predictions add column predicted_floor text;`.

No API changes required for either path.

## Idempotency (webhook retries)

Supabase retries webhooks on non-2xx responses and on network timeouts. If
the DB write succeeds but the HTTP response is lost mid-flight, a retry
would otherwise produce a duplicate prediction row for the same
`trial_id`. The handler uses `upsert(on_conflict="trial_id")` to absorb
retries safely, but that requires a UNIQUE constraint on the target
column. Run this once in the Supabase SQL editor:

```sql
alter table public.live_predictions
  add constraint live_predictions_trial_id_key unique (trial_id);
```

After this, re-delivering the same webhook payload will update the
existing row rather than insert a new one.

## Model bundle contract

`main.py` loads a joblib-dumped dict with this shape:

```python
{
    "channel_names":  ["GpuUtil", "CpuUtil", ...],   # ordered, defines channel axis
    "series_length":  int,                             # truncation target
    "preprocessing":  {"method": "truncate", "sort_by": "Timestamp"},
    "heads": {
        "location": {"model": <est>, "label_encoder": <LabelEncoder>},
        # ...any number of real estimators...
    },
    "derived_heads": {                                 # optional
        "room": {"from": "location", "mapping": {"Floor3Kitchen": "kitchen", ...}},
    },
    "kind": "minirocket-vturcs-v1",                    # free-form version tag
}
```

- Each **real** head's `model` must accept `.predict(X3d)` where
  `X3d.shape == (1, len(channel_names), series_length)` and return an
  integer array of length 1. aeon's `MiniRocketClassifier` fits this
  out of the box.
- `label_encoder` must expose `.inverse_transform([int]) -> [str]`.
- `feature_names` is accepted as a back-compat alias for `channel_names`.
- `series_length` **must** be set at training time — the server refuses
  to load a bundle without it.
- `preprocessing.method` must be `"truncate"`; any other value is a
  startup error. `preprocessing.sort_by` defaults to `"Timestamp"` if
  omitted.
- `derived_heads` is optional. Each entry must have `from` (the name of
  an existing real head) and `mapping` (a `dict[str, str]` translating
  source labels to derived labels). Name collisions with real heads are
  rejected at load time. A mapping miss at inference produces `"unknown"`
  and a one-time warning.

Drop a real bundle at `current_model.pkl` (or point `MODEL_PATH` at it)
and restart uvicorn. No API, DB, or frontend changes required.
