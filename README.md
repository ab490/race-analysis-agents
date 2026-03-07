# Race Analysis Agents

A platform for autonomous race car telemetry analysis. Engineers and drivers upload CSV telemetry data exported from ROS2 rosbag2 recordings, and interact with AI agents that answer questions, find correlations, and generate Plotly charts — all in plain English.

---

## Upload Requirements

Two types of uploads are required: **track setup files** (once per track) and **session files** (once per recording).

---

### Step 1 — Upload Track (once per track)

`POST /upload/track`

| Field | Type | Description |
|---|---|---|
| `track_id` | string | Unique name for this track, e.g. `laguna_seca` |
| `kml_file` | file | `*_track.kml` — KML centerline of the track |
| `segments_file` | file | `*_segments.csv` — segment boundary definitions |

#### Track Centerline — `*_track.kml`

A KML file with the lat/lon trace of the full track centerline. Export from Google Earth, RaceLogic VBOX Tools, or any mapping tool that supports KML.

```xml
<coordinates>
  -121.756647,36.586462,0
  -121.756500,36.586300,0
  ...
</coordinates>
```

#### Segment Definitions — `*_segments.csv`

Defines the start/finish line and named track segments.

```csv
segment,start_lat,start_lon,end_lat,end_lon
start_finish,36.586462,-121.756647,36.586462,-121.756647
s1,36.583936,-121.757775,36.583130,-121.757750
s2,36.583130,-121.757750,36.583196,-121.757016
s3,36.583196,-121.757016,36.584604,-121.756992
```

**Rules:**
- First row **must** be `start_finish` — set start and end to the same GPS point
- Remaining rows are segments in lap order; names become zone labels in queries
- Coordinates are decimal degrees (WGS84)

---

### Step 2 — Upload Session

`POST /upload/session`

| Field | Type | Description |
|---|---|---|
| `files` | file[] | All rosbag2 CSVs + one `*_stat.csv` |
| `track_id` | string | Must match a previously uploaded track |

#### ROS2 Topic CSVs — `rosbag2_YYYY_MM_DD-HH_MM_SS_<topic>.csv`

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

You don't need to upload every topic — only the ones relevant to your analysis.

#### Stat File — `*_stat.csv`

The vehicle position file in ENU (East-North-Up) coordinates. This is the **alignment master** — all other topic files snap to its timeline.

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
3. Laps detected automatically by counting S/F crossings (minimum lap distance enforced to avoid noise)
4. Each position row assigned to a named track segment via nearest-neighbour match against KML centerline
5. All topic files time-aligned to the stat file (nearest-timestamp, no interpolation)
6. Enriched stat file (with `lat`, `lon`, `zone`, `lap` columns) saved back to storage
7. Processed session stored — no re-upload needed for future queries

---

## Querying a Session

`POST /query/ask`

```json
{
  "session_id": "rosbag2_2025_07_02-10_33_18",
  "message": "What was the max speed in sector 1 across all laps?"
}
```

Returns a report dict:
```json
{
  "title": "...",
  "sections": [
    {"type": "text", "content": "..."},
    {"type": "plot", "figure": {...}, "caption": "..."}
  ]
}
```

### Example Questions

- *"What was the maximum speed in lap 3?"*
- *"When did the front-left tire temperature exceed 90°C?"*
- *"Average brake pressure in sector 2 across all laps?"*
- *"Compare wheel speed vs vehicle speed in lap 2."*
- *"Plot the GG diagram for the full session."*
- *"Show me a speed heatmap on the track map."*
- *"Was there any MPC failure during the session?"*
- *"Which lap had the highest peak lateral acceleration?"*

---

## Other API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/upload/sessions` | List all processed session IDs |
| `GET` | `/tracks/` | List all uploaded track IDs |
| `GET` | `/tracks/{track_id}` | Get segment definitions for a track |
| `GET` | `/health` | Health check |

---

## Development Setup

```bash
uv sync                                  # install dependencies
uv run uvicorn api.main:app --reload     # run API
uv run pytest                            # run tests
uv run ruff check .                      # lint
uv run ruff format .                     # format
```

`.env` file:
```
GCP_PROJECT_ID=
GCP_REGION=us-central1
VERTEX_AI_MODEL=gemini-2.0-flash-lite-001
GCS_BUCKET_NAME=
```
