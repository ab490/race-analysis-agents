# Race Analysis Agents

A platform for autonomous race car telemetry analysis.

---

## Quick Start

```bash
# Backend
uv sync
uv run uvicorn api.main:app --reload     # API at http://localhost:8000

# Frontend (separate terminal)
cd frontend && npm install
cd frontend && npm run dev               # UI at http://localhost:5173
```

Required environment variables (`.env` in project root):
```
GCP_PROJECT_ID=project-id
GCP_REGION=project-region
VERTEX_AI_MODEL=model-name
GCS_BUCKET_NAME=bucket-name
API_KEY=                                 # optional - leave blank to disable authentication
```

---

## Upload Requirements

Two uploads are required before querying: **track setup** (once per track) and **session files** (once per session).

---

### Step 1 - Upload Track (once per track)

`POST /upload/track`

| Field | Type | Description |
|---|---|---|
| `track_id` | string | Unique name for this track, e.g. `laguna_seca` |
| `kml_file` | file | `*_track.kml` - KML centerline of the track |
| `segments_file` | file | `*_segments.csv` - segment boundary definitions |

#### Track Centerline - `*_track.kml`

A KML file exported from Google Earth with the lat/lon trace of the full track centerline.

```xml
<coordinates>
  -121.756647,36.586462,0
  -121.756500,36.586300,0
  ...
</coordinates>
```

#### Segment Definitions - `*_segments.csv`

Defines the start/finish line and named track segments.

```csv
segment,lat,lon
start_finish,36.586462,-121.756647
s1,36.583936,-121.757775
s2,36.583130,-121.757750
s3,36.583196,-121.757016
```

**Rules:**
- Must contain a `start_finish` row
- Remaining rows are segments listed in lap order (`s1`, `s2`, `s3`, ...)
- Each segment starts at its lat/lon and ends where the next begins; last wraps back to `s1`
- Segment names become zone labels for queries (e.g. "max speed in s1", "brake pressure in s2")
- Coordinates are decimal degrees (WGS84)

---

### Step 2 - Upload Session

`POST /upload/session`

| Field | Type | Description |
|---|---|---|
| `files` | file[] | rosbag2 topic CSVs - include `*_stat.csv` on first upload or when re-processing laps/zones |
| `track_id` | string | Must match a previously uploaded track |
| `force` | bool | If `true`, wipe all existing GCS data for this session and reprocess from scratch. Requires a `*_stat.csv` in the upload. |

**Incremental uploads are supported.** After the initial upload you can add new topic CSVs without re-uploading the stat file. The pipeline merges new files with existing ones in GCS, reuses the enriched stat, and re-aligns all topics.


#### ROS2 Topic CSVs - `rosbag2_YYYY_MM_DD-HH_MM_SS_<topic>.csv`

One file per ROS2 topic from the same recording. All files from the same bag share the date-time prefix.

```
rosbag2_2025_07_02-10_33_18_wheel_speed.csv
rosbag2_2025_07_02-10_33_18_ControlStatus.csv
rosbag2_2025_07_02-10_33_18_tire_temp_fl.csv
```

Timestamp column (`stamp` or `time`) must be in ROS2 format:
```
builtin_interfaces.msg.Time(sec=1751477599, nanosec=930823584)
```

#### Stat File - `*_stat.csv`

The vehicle position file in ENU (East-North-Up) coordinates. This is the **alignment master** - all other topic files are time-align to it.

```csv
stamp,position_x,position_y,position_z,...
"builtin_interfaces.msg.Time(sec=1751477599, nanosec=930823584)",12.4,8.3,0.1,...
```

| Column | Description |
|---|---|
| `stamp` / `time` / `stamp_seconds` | Timestamp |
| `position_x` | East position in metres (ENU frame) |
| `position_y` | North position in metres (ENU frame) |

The ENU origin is the start/finish coordinate from `*_segments.csv`. Upload exactly one stat file per session.

---

## What Happens After Upload

1. Stat file ENU coordinates converted to lat/lon using the S/F point as ECEF origin
2. Cumulative distance computed along the GPS trace
3. Laps detected automatically by counting S/F crossings (min lap distance enforced to avoid noise)
4. Each position row assigned to a named track segment via nearest-neighbour match against KML centerline
5. All topic files time-aligned to the stat file - stat is always the base timeline (nearest-timestamp, no interpolation)
6. Enriched stat file (with `lat`, `lon`, `zone`, `lap` columns) saved back to GCS
7. Processed session stored - no re-upload needed for future queries

---

## Querying a Session

Queries stream back results as Server-Sent Events:

`POST /query/stream`

```json
{ "session_id": "rosbag2_2025_07_02-10_33_18", "message": "What was the max speed in sector 1?" }
```

**Event stream format:**

```
data: {"type": "status", "text": "Computing statistics…"}
data: {"type": "status", "text": "Generating time series chart…"}
data: {"type": "done", "report": {"title": "...", "sections": [...]}}
```

The final `done` event contains a report dict:
```json
{
  "title": "Max Speed - Sector 1",
  "sections": [
    {"type": "text", "content": "Peak speed in s1 was **67.3 mph** (lap 2)."},
    {"type": "plot", "figure": {...plotly dict...}, "caption": "Speed over time in s1"}
  ]
}
```

### Example Questions

**Data queries** (routed to `qa_agent`):
- *"What was the maximum speed in lap 3?"*
- *"When did brake pressure exceed 50 bar?"*
- *"Average brake pressure in sector 2 across all laps?"*
- *"How did lap times change across the stint?"*
- *"Were there any anomalies in lateral G-force?"*
- *"Was there any MPC failure during the session?"*
- *"Which lap had the highest peak lateral acceleration?"*
- *"Where is the car losing the most time?"*
- *"Is there degradation in lap times? Which sector is slowest?"*
- *"Compare cross-track error across laps - is the car drifting off line?"*

**Visualisation** (routed to `plot_agent` when plot/chart/graph keywords detected):
- *"Plot the GG diagram for the full session."*
- *"Show me a speed heatmap on the track map."*
- *"Chart front-left tire temperature over time."*
- *"Overlay wheel speed across all laps."*
- *"Compare wheel speed vs vehicle speed in lap 2."*

---

## Authentication

Set `API_KEY` in the environment to require authentication. When set, every request must include:

```
X-API-Key: <your key>
```

Leave `API_KEY` unset (or empty) to disable auth (useful for local development).

The web UI has an API Key field in the top-right navbar that stores the key in `localStorage`.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/upload/track` | Upload track KML + segments CSV |
| `POST` | `/upload/session` | Upload session CSVs and trigger processing |
| `GET` | `/upload/sessions` | List all processed session IDs |
| `GET` | `/upload/sessions/{id}` | Get session metadata (lap boundaries, schema) |
| `POST` | `/query/stream` | Stream a question as SSE |
| `GET` | `/tracks/` | List all uploaded track IDs |
| `GET` | `/tracks/{track_id}` | Get segment definitions for a track |
| `GET` | `/health` | Health check |

---

## Repository Structure

```
race-analysis-agents/
├── agents/                         # AI agent definitions (Google ADK)
│   ├── data_agent/                 # Handles schema/data discovery questions ("what data do I have?")
│   ├── qa_agent/                   # Main workhorse - stats, events, lap analysis, anomalies, plots (18 tools)
│   └── plot_agent/                 # Pure visualization requests with no analysis (9 tools)
│
├── api/                            # FastAPI application
│   ├── main.py                     # App initialization, router registration
│   ├── auth.py                     # X-API-Key authentication dependency
│   └── routes/
│       ├── upload.py               # POST /upload/session and /upload/track - full processing pipeline
│       ├── query.py                # POST /query/stream - SSE streaming, agent routing, figure interception
│       └── tracks.py               # GET /tracks/ - list and retrieve track metadata
│
├── tools/                          # Shared library functions used by agents and routes
│   ├── csv_loader.py               # CSV parsing, ROS2 timestamp normalisation, multi-topic alignment
│   ├── lap_detector.py             # ENU -> lat/lon conversion, cumulative distance, lap detection
│   ├── query_engine.py             # Stats, time series, threshold events, zone and cross-topic queries
│   ├── plot_generator.py           # Server-side Plotly figure generation (5 chart types)
│   └── gcs_store.py                # All GCS reads/writes - sessions, raw files, tracks
│
├── frontend/                       # React + Vite + Tailwind web UI
│   ├── src/
│   │   ├── App.jsx                 # Root component - navbar, API key input, page routing
│   │   ├── api.js                  # Axios client and SSE streamQuestion() helper
│   │   ├── pages/
│   │   │   ├── ChatPage.jsx        # Session selector, chat history, report panel, PDF export
│   │   │   └── UploadPage.jsx      # Track and session file upload forms
│   │   └── components/
│   │       ├── ReportView.jsx      # Renders report dict (markdown text + Plotly charts)
│   │       └── PlotSection.jsx     # Wraps react-plotly.js, receives figure dict from API
│   └── vite.config.js              # Dev proxy: /api/* -> http://localhost:8000
│
├── tests/                          # Test suite (pytest)
├── data/                           # Sample CSVs for local development (gitignored)
├── main.py                         # Thin re-export of api.main:app for uvicorn
└── pyproject.toml                  # Python project config and dependencies (uv)    
``` 

### Key design points

- **`agents/`** - each agent is a folder with `agent.py` exposing `root_agent`. Tools are plain Python functions; ADK uses their docstrings to decide when to call them.
- **`tools/`** - pure library layer. Agents never parse CSVs directly; all data access goes through these modules.
- **`api/routes/query.py`** - routes questions by keyword: schema questions -> `data_agent`, plot/chart/graph keywords -> `plot_agent`, everything else -> `qa_agent`. Intercepts Plotly figure dicts from the ADK event stream and injects them into the final report (prevents the LLM from having to embed large figure dicts in JSON).
- **`agents/qa_agent/agent.py`** - uses ContextVars (`_session_ctx`, `_tempdir_ctx`) so `align_topics()` and `get_topic_file()` can lazily download topic CSVs from GCS on demand without passing session state through every tool call.
- **Alignment** - the stat file is always the base timeline. All other topics align to it via nearest-index lookup. No interpolation.

---

## Development

```bash
# Backend
uv sync
uv run uvicorn api.main:app --reload     # http://localhost:8000
uv run pytest                            # run tests
uv run ruff check .                      # lint
uv run ruff format .                     # format

# Frontend
cd frontend && npm install
cd frontend && npm run dev               # http://localhost:5173
cd frontend && npm run build             # production build
```