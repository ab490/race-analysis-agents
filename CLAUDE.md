# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Vision
A platform for autonomous race car telemetry analysis. Engineers and drivers upload ROS2 rosbag2 CSV exports, then interact with AI agents that answer questions in plain English, detect events, generate Plotly charts, and analyse performance by lap and track segment.

---

## Tech Stack

- **Python** + **uv** (always use `uv run`, never plain `python`)
- **Google ADK** — agent orchestration and tool use
- **GCP Vertex AI** — LLM runtime (Gemini)
- **FastAPI** — REST API with SSE streaming
- **Google Cloud Storage** — all persistent storage
- **React + Vite + Tailwind + Plotly.js** — frontend (built, in `frontend/`)

---

## Project Structure

```
race-analysis-agents/
├── agents/
│   ├── orchestrator/     # entry point — routes to sub-agents
│   ├── data_agent/       # schema discovery ("what data do I have?")
│   ├── qa_agent/         # Q&A, lap analysis, anomaly detection, AND plots
│   ├── plot_agent/       # dedicated visualisation agent (pure plot requests)
│   └── insights_agent/   # stub only — all tools merged into qa_agent
├── tools/
│   ├── csv_loader.py     # CSV parsing, timestamp normalisation, session alignment
│   ├── lap_detector.py   # ENU→lat/lon conversion, cumulative distance, lap detection
│   ├── query_engine.py   # stats, time series, threshold events, zone queries
│   ├── plot_generator.py # server-side Plotly figure generation
│   └── gcs_store.py      # all GCS reads/writes
├── api/
│   ├── main.py           # FastAPI app, CORS, auth middleware, router registration
│   ├── auth.py           # X-API-Key header authentication
│   └── routes/
│       ├── upload.py     # POST /upload/session, POST /upload/track, GET /upload/sessions
│       ├── query.py      # POST /query/stream (SSE)
│       └── tracks.py     # GET /tracks/, GET /tracks/{track_id}
├── frontend/             # React + Vite + Tailwind frontend
│   ├── src/
│   │   ├── App.jsx       # navbar with API key input, page routing
│   │   ├── api.js        # axios client + SSE streamQuestion()
│   │   ├── pages/        # UploadPage.jsx, ChatPage.jsx
│   │   └── components/   # ReportView.jsx (react-markdown), PlotSection.jsx
│   └── vite.config.js    # proxy /api → localhost:8000
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
      aligned.csv        ← all topics merged onto stat timeline (stored, not used at query time; CSV for manual inspection)
      laps.json          ← [{lap, t_start, t_end}, ...]
      schema.json        ← {topics, columns_by_topic, time_range, row_counts}
  tracks/<track_id>/
    centerline.kml
    segments.csv
```

**Important**: at query time, agents download files from `raw/` on demand — not the aligned parquet. The aligned parquet is stored but not used by agents directly. Only `laps.json` and `schema.json` from `processed/` are loaded by the query route.

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

1. `POST /query/stream` receives `{session_id, message}`
2. Create a temp directory for this request (`tempfile.mkdtemp`)
3. Load `lap_boundaries` + `schema` (including `columns_by_topic`) from GCS `processed/`
4. Download **only the enriched stat file** to the temp dir
5. Set `_session_ctx` and `_tempdir_ctx` ContextVars (used by `get_topic_file` tool)
6. Build context string: session_id, stat file path, `columns_by_topic`, lap_boundaries, duration
7. Run orchestrator agent — it routes to qa_agent / plot_agent / data_agent
8. Agents call `get_topic_file(topic_name)` on demand to download other CSVs into the temp dir
9. Stream SSE events: `{type: status}` per tool call, `{type: done, report: {...}}` when finished
10. `finally`: reset ContextVars, `shutil.rmtree(temp_dir)` cleans up all downloaded files

**Lazy downloading**: only the stat file is downloaded upfront. Other topic files are downloaded on demand when the agent calls `get_topic_file`. A session with 68 topics will typically download 1–3 files per query instead of all 68.

---

## Agent Architecture

Each agent lives in `agents/<name>/agent.py` and exposes `root_agent`. Tools are plain Python functions; ADK uses their docstrings to decide when to call them.

| Agent | Responsibility |
|---|---|
| `orchestrator` | Routes questions to the right sub-agent; never answers directly |
| `data_agent` | "What data do I have?" — describes topics and columns from schema in context |
| `qa_agent` | Everything else: stats, events, lap times, trends, sector analysis, anomalies, plots |
| `plot_agent` | Pure visualisation requests with no analysis |

**Routing rules** (from orchestrator instruction):
- "What data do I have?" / schema questions → `data_agent`
- Any analysis, numbers, trends, anomalies → `qa_agent` (also generates plots proactively)
- Pure visualisation only → `plot_agent`

**`qa_agent` tools** (16 total):
- `get_topic_file` — downloads a topic CSV on demand, returns local path
- `describe_uploaded_files` — schema from file list (use when topic details needed)
- `stats_for_column`, `stats_for_zone`, `list_zones`
- `signal_over_time`, `events_above_threshold`, `correlate_signals`
- `resolve_lap_window`, `summarise_lap_times`, `stint_trend`, `sector_times`
- `detect_anomalies`
- `plot_time_series`, `plot_lap_overlay`, `plot_track_map`, `plot_gg_diagram`

**`plot_agent` tools**: `get_topic_file`, `describe_uploaded_files`, `resolve_lap_window`, `get_zone_windows_for_plot`, `get_stats_for_annotation`, `plot_time_series`, `plot_time_series_overlay`, `plot_track_map`, `plot_gg_diagram`

`plot_agent` and `data_agent` import shared tools (`get_topic_file`, `describe_uploaded_files`) directly from `agents/qa_agent/agent.py`.

**Do NOT use `BuiltInCodeExecutor` or `VertexAiCodeExecutor`** — both are incompatible with Vertex AI custom function tools ("Multiple tools supported only when all are search tools"). `tools/plot_generator.py` generates complete Plotly figure dicts server-side instead.

---

## Key Tool Functions

### `tools/csv_loader.py`
- `_load_raw(path)` — loads CSV, parses ROS2 timestamps to float seconds, drops array-string columns
- `_parse_filename(name)` → `(session_id, topic)` or `(None, "_stat")` for stat files
- `_align_session(topic_dfs)` — `merge_asof` all topics onto stat master; columns suffixed `__topic`
- `load_session(file_paths)` → `{session_id: aligned_df}`
- `get_schema(file_paths)` → `{session_id: {topics, columns_by_topic, time_range, row_counts}}`

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
- `save_session / load_session_meta / load_session / session_exists / list_sessions` — processed session management
- `load_session_meta` — loads only `laps.json` + `schema.json` (no parquet download); use this at query time
- `load_session` — loads full aligned DataFrame + meta; only needed if the parquet data is actually required
- `save_track_files / load_track_segments / load_track_kml / list_tracks` — track management
- `download_raw_file(session_id, topic, target_dir=None)` — `target_dir` writes to a shared dir instead of a temp file; matches by `stem.endswith(topic)`

### `tools/plot_generator.py`
- `make_time_series(file_path, columns, title, y_label, t_start, t_end, y_scale)` → Plotly figure dict
- `make_multi_lap_overlay(file_path, column, lap_windows, title, y_label, y_scale)` → Plotly figure dict
- `make_track_map(stat_file_path, color_column, color_label, t_start, t_end)` → Plotly figure dict
- `make_gg_diagram(imu_file_path, lat_accel_col, lon_accel_col, t_start, t_end)` → Plotly figure dict

### `agents/qa_agent/agent.py` — ContextVars for lazy file downloading
```python
_session_ctx: ContextVar[str]  # set to session_id before each agent run
_tempdir_ctx: ContextVar[str]  # set to temp_dir before each agent run

def get_topic_file(topic_name: str) -> dict:
    # reads _session_ctx and _tempdir_ctx, calls download_raw_file with target_dir
    # returns {"path": "/tmp/race_query_.../filename.csv", "topic": topic_name}
```

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
    {"type": "text", "content": "...markdown..."},
    {"type": "plot", "figure": {...plotly dict...}, "caption": "..."}
  ]
}
```
Text sections support markdown (rendered via `react-markdown` + `@tailwindcss/typography`).

---

## SSE Streaming Protocol

`POST /query/stream` returns `text/event-stream`. Each line is:
```
data: {"type": "status", "text": "Computing statistics…"}
data: {"type": "done", "report": {...}}
data: {"type": "error", "text": "..."}
```
Frontend uses the Fetch API (not EventSource) to support POST. The `AbortController` cancel is wired to a Cancel button, session switch, and component unmount.

---

## Authentication

`api/auth.py` — `require_api_key` FastAPI dependency applied to all three routers.
- If `API_KEY` env var is unset/empty: auth is disabled (local dev)
- If set: every request must pass `X-API-Key: <key>` header
- Frontend stores the key in `localStorage` and attaches it via axios interceptor + manual Fetch header

---

## Commands

```bash
uv sync                                      # install dependencies
uv run uvicorn api.main:app --reload         # run API (dev, port 8000)
uv run pytest                                # run all tests
uv run pytest tests/test_csv_loader.py       # run single test file
uv run ruff check .                          # lint
uv run ruff format .                         # format

cd frontend && npm install                   # install frontend deps
cd frontend && npm run dev                   # frontend dev server (port 5173)
cd frontend && npm run build                 # production build
```

---

## Environment Variables
```
GCP_PROJECT_ID=
GCP_REGION=us-central1
VERTEX_AI_MODEL=gemini-2.0-flash-lite-001
GCS_BUCKET_NAME=
API_KEY=                        # optional; leave blank to disable auth
```

---

## Conventions
- One agent per folder; each needs `agent.py` with `root_agent`
- Tool docstrings are part of the API — agents use them to decide when to call each tool
- CSV parsing only via `csv_loader.py`; lap logic only via `lap_detector.py`; queries only via `query_engine.py`; plots only via `plot_generator.py`
- No business logic in API routes — routes only validate, orchestrate, and delegate
- Never hardcode GCP project IDs, bucket names, or coordinates — use env vars
- Shared tools (`get_topic_file`, `describe_uploaded_files`) live in `qa_agent` and are imported by other agents

## What NOT to Do
- Don't use `BuiltInCodeExecutor` or `VertexAiCodeExecutor` — broken on Vertex AI with custom tools
- Don't use plain `openai` or `anthropic` SDKs — everything goes through Vertex AI
- Don't let agents parse CSVs directly — always go through the tools layer
- Don't download all session files upfront in `_prepare_context` — use `get_topic_file` for lazy on-demand downloading

---

## Frontend

```bash
cd frontend && npm run dev    # dev server at http://localhost:5173
cd frontend && npm run build  # production build
```

- Vite proxy: `/api/*` → `http://localhost:8000` (strips `/api` prefix)
- Two pages: `UploadPage` (track + session upload), `ChatPage` (session selector + chat)
- `ChatPage`: split layout — left panel (320px) for conversation history + input, right panel for report
- `ReportView` renders `{type: text}` sections via `react-markdown` + `prose prose-invert` classes
- `PlotSection` wraps `react-plotly.js` — receives Plotly figure dict directly from API
- API key stored in `localStorage`, attached via axios interceptor and manual Fetch header

---

## Known Issues / TODO

### Low priority
1. **Blocking GCS I/O in async routes** — `load_session`, `download_raw_file`, etc. are
   synchronous and called from async FastAPI handlers. Fix: wrap with `asyncio.to_thread`.

2. **No deployment config** — no Dockerfile, Cloud Run config, or CI/CD pipeline.

3. **CORS wide open** — `allow_origins=["*"]` is fine for dev; restrict for production.

4. **Multi-session comparison** — compare two sessions against each other (not built).
