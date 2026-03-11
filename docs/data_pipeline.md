# Telemetry Data Pipeline

This document describes how telemetry data is uploaded and processed in the **Race Analysis Agents** platform.

Before telemetry queries can be performed, two uploads are required:

1. **Track Setup** – defines the track geometry and segment boundaries.
2. **Session Data** – contains telemetry data recorded during a race session.

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
