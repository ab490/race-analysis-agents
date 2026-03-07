# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Vision
A platform for autonomous race car telemetry analysis. Engineers and drivers upload ROS2 rosbag2 CSV exports, then interact with AI agents that answer questions in plain English, detect events, generate Plotly charts, and analyse performance by lap and track segment.

---

## Tech Stack

- **Python** + **uv** (always use `uv run`, never plain `python`)
- **Google ADK** — agent orchestration and tool use
- **GCP Vertex AI** — LLM runtime (Gemini)
- **FastAPI** — REST API
- **Google Cloud Storage** — all persistent storage
- **React + TypeScript + Plotly.js** — frontend (not yet built)

---

## Project Structure

```
race-analysis-agents/
├── agents/
│   ├── orchestrator/     # entry point — routes to sub-agents
│   ├── data_agent/       # schema discovery
│   ├── qa_agent/         # natural language Q&A over telemetry
│   └── plot_agent/       # Plotly chart generation (uses BuiltInCodeExecutor)
├── tools/
│   ├── csv_loader.py     # CSV parsing, timestamp normalisation, session alignment
│   ├── lap_detector.py   # ENU→lat/lon conversion, cumulative distance, lap detection
│   ├── query_engine.py   # stats, time series, threshold events, zone queries
│   ├── gcs_store.py      # all GCS reads/writes
│   └── report_builder.py # shared report schema {title, sections}
├── api/
│   ├── main.py           # FastAPI app, CORS, router registration
│   └── routes/
│       ├── upload.py     # POST /upload/session, POST /upload/track, GET /upload/sessions
│       ├── query.py      # POST /query/ask
│       └── tracks.py     # GET /tracks/, GET /tracks/{track_id}
├── tests/
└── data/                 # sample CSVs for development
```

---

## GCS Storage Layout

```
<bucket>/
  sessions/<session_id>/
    raw/              ← original uploaded CSVs; stat file is the ENRICHED version
      wheel_speed.csv
      ControlStatus.csv
      session_stat.csv   ← enriched: has lat, lon, zone, lap columns added
      ...
    processed/
      aligned.parquet    ← all topics merged onto stat timeline
      laps.json          ← [{lap, t_start, t_end}, ...]
      schema.json        ← {topics, columns, time_range, row_counts}
  tracks/<track_id>/
    centerline.kml
    segments.csv
```

**Important**: at query time, agents download files from `raw/` — not the aligned parquet. The aligned parquet is stored but not used by agents directly. Only `laps.json` and `schema.json` from `processed/` are loaded by the query route.

---

## Upload Pipeline (runs once per session)

Track files must be uploaded first via `POST /upload/track`. Then:

1. `POST /upload/session` receives rosbag2 CSVs + `*_stat.csv` + `track_id`
2. Files written to temp dir; session_id parsed from rosbag2 filename prefix
3. Original files saved to GCS `raw/` immediately
4. Track KML + segments loaded from GCS
5. Stat file processed: ENU → lat/lon (S/F coordinate as ECEF origin)
6. Zone assigned per row via KDTree nearest-neighbour against KML centerline
7. Laps detected via S/F crossings (min distance threshold prevents false triggers)
8. Enriched stat (lat, lon, zone, lap, cumulative_distance added) overwrites GCS `raw/` stat
9. All topics aligned to enriched stat timeline via `merge_asof(direction="nearest")`
10. Aligned parquet + laps.json + schema.json saved to GCS `processed/`

---

## Query Flow (per user question)

1. `POST /query/ask` receives `{session_id, message}`
2. Load `lap_boundaries` + `schema` from GCS `processed/`
3. Download all raw files (including enriched stat) from GCS to local temp paths
4. Build context string: session_id, file_paths, topics, lap_boundaries, duration
5. Run orchestrator agent — it routes to qa_agent / plot_agent / data_agent
6. Agents call tools using the local temp file paths
7. Temp files deleted after agent run (try/finally)
8. Response parsed as report dict `{title, sections}` and returned

---

## Agent Architecture

Each agent lives in `agents/<name>/agent.py` and exposes `root_agent`. Tools are plain Python functions; ADK uses their docstrings to decide when to call them.

| Agent | Responsibility |
|---|---|
| `orchestrator` | Routes questions to the right sub-agent(s); assembles combined reports |
| `data_agent` | Discovers available topics and columns from file schema |
| `qa_agent` | Stats, event detection, cross-topic correlation, lap/zone-scoped queries |
| `plot_agent` | Generates Plotly charts via `BuiltInCodeExecutor` (Gemini native code execution) |

`plot_agent` uses `BuiltInCodeExecutor` — NOT `VertexAiCodeExecutor`. Do not switch back; the Vertex version creates GCP Extension resources on every import.

---

## Key Tool Functions

### `tools/csv_loader.py`
- `_load_raw(path)` — loads CSV, parses ROS2 timestamps to float seconds, drops array-string columns
- `_parse_filename(name)` → `(session_id, topic)` or `(None, "_stat")` for stat files
- `_align_session(topic_dfs)` — `merge_asof` all topics onto stat master; columns suffixed `__topic`
- `load_session(file_paths)` → `{session_id: aligned_df}`
- `get_schema(file_paths)` → `{session_id: {topics, columns, time_range, row_counts}}`

### `tools/lap_detector.py`
- `process_stat_file(stat_df, start_finish)` → adds `lat`, `lon`, `cumulative_distance`
- `detect_laps(stat_df, start_finish)` → adds `lap` column, returns `(stat_df, boundaries)`
- Lap 0 = outlap (before first S/F crossing); lap 1+ = race laps
- `min_lap_distance=3500m` prevents noise crossings from triggering false laps

### `tools/query_engine.py`
- `get_column_stats(file_path, column, t_start, t_end)` → stats dict
- `get_time_series(file_path, columns, t_start, t_end, max_points)` → time series dict
- `find_threshold_events(file_path, column, operator, threshold, ...)` → event list
- `query_cross_topic(file_paths, columns, ...)` → aligned multi-topic data (columns suffixed `__topic`)
- `get_zone_time_windows(stat_file_path, zone_name)` → `[{t_start, t_end, lap}, ...]`
- `get_column_stats_for_zone(data_file, stat_file, zone_name, column)` → overall + per-lap stats

### `tools/gcs_store.py`
- `save_raw_file / download_raw_file / list_raw_files` — raw CSV management
- `save_session / load_session / session_exists / list_sessions` — processed session management
- `save_track_files / load_track_segments / load_track_kml / list_tracks` — track management
- `download_raw_file(session_id, topic)` matches by `stem.endswith(topic)` — handles `_stat` correctly

---

## File Format Contracts

- **rosbag2 CSVs**: `rosbag2_YYYY_MM_DD-HH_MM_SS_<topic>.csv`; timestamp in `stamp` or `time` column as `builtin_interfaces.msg.Time(sec=X, nanosec=Y)`
- **stat file**: `*_stat.csv`; must have `position_x`, `position_y` (ENU metres); timestamp in `stamp`, `time`, or `stamp_seconds`
- **segments CSV**: columns: `segment, lat, lon`; must contain `start_finish` row (lap marker only, not a zone); remaining rows are segments in lap order — each ends where the next begins; last segment wraps back to `s1`
- **track KML**: standard KML with `<coordinates>` block (lon,lat,alt per point)
- One session = one rosbag2 prefix (multi-bag sessions not supported)

---

## Alignment Strategy
- **Master timeline**: stat file (lowest frequency, position reference)
- **Method**: `pandas.merge_asof(direction="nearest")` — real measured values only, no interpolation
- **Column naming**: all non-time columns suffixed `__<topic>` in the aligned output
- **Time overlap**: topics with no overlap with master are silently skipped

---

## Report Schema
All agent responses follow this structure:
```python
{
  "title": "...",
  "sections": [
    {"type": "text", "content": "..."},
    {"type": "plot", "figure": {...plotly dict...}, "caption": "..."}
  ]
}
```

---

## Commands

```bash
uv sync                                     # install dependencies
uv run uvicorn api.main:app --reload        # run API (dev)
uv run pytest                               # run all tests
uv run pytest tests/test_csv_loader.py      # run single test file
uv run ruff check .                         # lint
uv run ruff format .                        # format
```

---

## Environment Variables
```
GCP_PROJECT_ID=
GCP_REGION=us-central1
VERTEX_AI_MODEL=gemini-2.0-flash-lite-001
GCS_BUCKET_NAME=
```

---

## Conventions
- One agent per folder; each needs `agent.py` with `root_agent`
- Tool docstrings are part of the API — agents use them to decide when to call each tool
- CSV parsing only via `csv_loader.py`; lap logic only via `lap_detector.py`; queries only via `query_engine.py`
- No business logic in API routes — routes only validate, orchestrate, and delegate
- Never hardcode GCP project IDs, bucket names, or coordinates — use env vars

## What NOT to Do
- Don't use `VertexAiCodeExecutor` — it creates GCP Extension resources. Use `BuiltInCodeExecutor`.
- Don't use plain `openai` or `anthropic` SDKs — everything goes through Vertex AI
- Don't let agents parse CSVs directly — always go through the tools layer
- Don't build the frontend until the agent layer is working end-to-end