# Spatial Seer — Backend

Real-time FastAPI inference service. A Supabase Database Webhook on the
`hardware_data` table POSTs the inserted row to this service through an
`ngrok` tunnel; the service runs a classifier and writes the result to the
`live_predictions` table.

## Files

- `main.py` — FastAPI app with `POST /webhook/predict` and `GET /health`.
- `create_dummy_model.py` — Trains a placeholder XGBoost model so the API can
  boot before the real model is ready.
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

## Multi-head predictions

The model bundle supports multiple prediction heads. The current heads are:

| Head | DB column | Example output |
| --- | --- | --- |
| `room` | `predicted_room` | `kitchen`, `hallway` |
| `location` | `predicted_location` | `Floor3Kitchen`, `Outside3102` |

If you haven't already, add the `predicted_location` column to the
`live_predictions` table:

```sql
alter table public.live_predictions
  add column if not exists predicted_location text;
```

To add a third head later (e.g. `noise_type`):

1. Train a classifier for it in `create_dummy_model.py` under
   `heads["noise_type"]`.
2. Add `"noise_type": "predicted_noise_type"` to `HEAD_TO_COLUMN` in
   `main.py`.
3. Run `alter table public.live_predictions add column
   predicted_noise_type text;`.

No other API changes needed.

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

## Swapping the real model in

`main.py` loads a bundle with this shape:

```python
{
    "model": <fitted estimator with .predict>,
    "label_encoder": <sklearn LabelEncoder>,
    "feature_names": ["TotalUsedMem", "CpuUtil", ...],
}
```

Save a real bundle to `current_model.pkl` (or set `MODEL_PATH`) and restart
uvicorn. No API changes required.
