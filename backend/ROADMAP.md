# Spatial Seer Backend — MiniRocket Wiring ROADMAP

Living document. Updated as changes land. Keep terse.

## Goal

Swap the backend inference path from per-sample XGBoost (+ majority vote) to
**multivariate time-series classification with `aeon.classification.convolution_based.MiniRocketClassifier`**,
matching the exact preprocessing in
`model_experimentation/model_4_test.ipynb` Cell 1 + Cell 2.

This is not the final model. It is "similar enough" that when the real trainer
lands, the drop-in is a one-file change (`current_model.pkl`).

## Source of truth: the notebook recipe

From `model_4_test.ipynb`:

- **Library:** `from aeon.classification.convolution_based import MiniRocketClassifier`
- **Channels (7, ordered):**
  `GpuUtil, CpuUtil, FrameTimeStdDev, WorstFrameMs, MainThreadMs, TotalUsedMem, CpuClockFreq`
- **Per-scan preprocessing:**
  1. `sort_values("Timestamp")`
  2. `.iloc[:SERIES_LEN]` (truncate)
  3. `group[CHANNELS].values.T` → shape `(n_channels, timesteps)`
- **`SERIES_LEN`:** `df.groupby("scan_id").size().min()` — computed **at training time** on the training set.
- **Input shape to model:** `(n_instances, n_channels, series_length)`.
- **At inference:** `n_instances = 1`.

## Reversals from the earlier written brief

| Topic | Earlier brief | Notebook | Chosen |
| --- | --- | --- | --- |
| Library | `sktime` | `aeon` | **aeon** |
| Length strategy | `np.interp` resample | truncate first `SERIES_LEN` rows sorted by `Timestamp` | **truncate** |
| Channels | 15 telemetry | 7 specific | **7** |

Rationale in all three cases: matching the trainer exactly eliminates train/serve skew.

## Bundle contract (v2 — MiniRocket era)

```python
{
    "channel_names":   list[str],   # 7-channel ordered list. Also readable via "feature_names" alias.
    "series_length":   int,          # truncation target; set at training time
    "preprocessing":   {
        "method":  "truncate",       # only value currently supported
        "sort_by": "Timestamp",      # column inside csv_dump to sort by before truncation
    },
    "heads": {
        "room":     {"model": <aeon MiniRocketClassifier>, "label_encoder": LabelEncoder},
        "location": {"model": <aeon MiniRocketClassifier>, "label_encoder": LabelEncoder},
    },
    "kind": "minirocket-aeon-v1",
}
```

- Server accepts either `channel_names` or `feature_names` (old name); canonicalised internally to `channel_names`.
- Any head in the bundle that appears in `HEAD_TO_COLUMN` gets written to `live_predictions`. Unregistered heads are returned in the HTTP response but not persisted. Unchanged from v1.
- Adding a new head is still: train → add entry to `HEAD_TO_COLUMN` → `alter table ... add column`.

## Server preprocessing policy (inference path)

Given a `hardware_data.csv_dump` parsed to a `(T_raw, all_cols)` DataFrame:

1. **Drop** `("db_id", "location")` metadata columns (unchanged).
2. **Require** the `Timestamp` column; raise `HTTPException(400)` if absent (we can't match trainer's sort without it).
3. **Sort** by `Timestamp` ascending.
4. **Reindex** to `channel_names` (missing channel → zero-filled, warned once).
5. **Coerce** to `float32`, NaN → 0.
6. **Length-match to `series_length`:**
   - `T_raw > series_length`: truncate to first `series_length` rows.
   - `T_raw == series_length`: as-is.
   - `0 < T_raw < series_length`: edge-pad (repeat last row) to `series_length`, log warning.
   - `T_raw == 0`: `HTTPException(400)`.
7. **Reshape** to `(1, n_channels, series_length)` via `arr.T[np.newaxis, :, :]`.

Edge-pad on short scans is a resilience choice: service keeps flowing, operator
sees a warning. Alternative is hard-fail 400; can be tightened later if we
see skew creep.

## Prediction

- `y_pred = head["model"].predict(X3d)` → length-1 integer array.
- `label = label_encoder.inverse_transform(y_pred)[0]`.
- **Confidence cascade:**
  1. `predict_proba` present → `float(proba[0].max())`.
  2. Else `decision_function` present → stable softmax → max.
     - Binary 1-D scores: sigmoid → `max(p, 1-p)`.
  3. Else → `1.0`.
- aeon's `MiniRocketClassifier` uses RidgeClassifierCV internally → no `predict_proba`, but has `decision_function`. Path (2) is the expected path.

## File-level plan

### `backend/main.py`
- Drop `_majority_vote`.
- Replace `_record_to_feature_frame` → `_record_to_time_series` returning `(np.ndarray, int)` where the int is `T_raw` (for logging).
- `_load_model_bundle`: accept `channel_names`/`feature_names`, require `series_length`, validate `preprocessing.method`.
- `_predict_heads`: single-instance predict + confidence cascade.
- `/webhook/predict`: keep response shape; add `raw_samples` + `series_length`; keep `n_samples` as alias for `series_length` (non-breaking).
- `/health`: add `series_length`, `preprocessing`.

### `backend/create_dummy_model.py`
- Load `../spatial_seer_all_rooms_v3.csv`.
- Build scan array exactly per notebook Cell 1.
- Train two independent `MiniRocketClassifier(random_state=42)` heads: location + room.
- Fit each on the full dataset (no train/test split — this is the dummy/placeholder trainer; the real notebook owns proper evaluation).
- Dump bundle with `kind="minirocket-aeon-v1"`.

### `backend/requirements.txt`
- Add `aeon`.
- Remove `xgboost` (no longer imported anywhere).
- Keep everything else.

### `backend/README.md`
- Update bundle-shape example at bottom.
- Add "Time-series inference" section.
- Update `create_dummy_model.py` description (trains against real CSV now).

### Not touched
- Frontend.
- DB schema.
- `HEAD_TO_COLUMN` semantics.
- `_write_prediction` / idempotency.
- Webhook payload parsing.
- `HardwareSpy.cs` / Unity code.

## Open decisions logged

| # | Decision | Choice | Reversible? |
| --- | --- | --- | --- |
| 1 | Library | aeon | Yes, retrain |
| 2 | Length method | truncate-sort-by-Timestamp | Yes, retrain |
| 3 | Short-scan policy | edge-pad + warn | Yes, config |
| 4 | Missing Timestamp policy | 400 error | Yes, config |
| 5 | xgboost in requirements | remove | Yes, one-line add |
| 6 | Dummy training data | full CSV, no split | Yes, trainer change |

## Progress log

- **2026-04-23 — plan committed, trainer + server rewrite in flight.**
- **2026-04-23 — rewrite complete, smoke test passed.**
  - `main.py` rewritten: `_record_to_time_series` (truncate + sort by Timestamp),
    `_load_model_bundle` enforces `series_length` + `preprocessing.method`,
    `_predict_heads` single-instance with `predict_proba` / `decision_function`
    cascade, `_majority_vote` removed. `/health` now exposes `channel_names`,
    `series_length`, `preprocessing`. `/webhook/predict` response adds
    `raw_samples` + `series_length`; keeps `n_samples` as alias.
  - `create_dummy_model.py` now loads `../spatial_seer_all_rooms_v3.csv`,
    mirrors notebook Cell 1 preprocessing (7 channels, truncate to min
    scan length, sort by Timestamp), fits one `MiniRocketClassifier` per
    head, writes `minirocket-aeon-v1` bundle. Trained on 152 scans, 19
    locations, 4 room types, `series_length=90`. Training runtime ~2 min.
  - `requirements.txt`: `aeon` added, `xgboost` removed.
  - `README.md`: new "Time-series inference" section, bundle-contract
    example rewritten.
  - `_smoke_test.py` added (dev-only): loads the bundle, fabricates a
    webhook-shaped `record` from a real scan, runs the full
    preprocess→predict path without uvicorn/Supabase. Result:
    `(1, 7, 90)` float32 tensor, `room=kitchen, location=Floor3Kitchen`
    on scan `kitchen__Floor3Kitchen__noise2__r0__scan00`. Short-scan
    edge-pad branch exercised and warned once as designed.

## Known caveats for the placeholder model

- Trained on **the entire CSV with no held-out split**, so accuracy on
  training rows is artificially high and confidence will saturate near
  1.0. This is wiring, not evaluation. The real notebook owns proper
  train/test + LOBO evaluation.
- aeon's `MiniRocketClassifier` uses `RidgeClassifierCV` under the hood
  → no `predict_proba`. The server's confidence path exits through
  `decision_function` + softmax, which is exactly what the final model
  will also do (unless the trainer swaps in a calibrated head).
- `current_model.pkl` is overwritten on every `create_dummy_model.py`
  run. When the real bundle arrives, just drop it in place.

## Next wiring-agnostic follow-ups (not blocking, not done here)

1. Rename `create_dummy_model.py` → `train_model.py` or
   `create_placeholder_model.py` (the "dummy" name is now a misnomer —
   it trains on real data).
2. A `validate_bundle.py` that diffs bundle `channel_names` against
   Unity's csv_dump header and runs per-head accuracy on a held-out
   slice. Useful the day a new bundle lands.
3. Expose `/health` a git-like SHA or mtime of `current_model.pkl` so
   ops can correlate a prediction to a specific bundle.
4. Consider tightening short-scan policy from "edge-pad + warn" to
   "hard 400" once real-world scan lengths stabilise above
   `series_length`.
