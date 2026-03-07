"""
QA Agent — answers natural language questions about race telemetry data.

Responsibilities:
- Accept any natural language question about a session
- Discover which files contain which columns when unsure (via describe_uploaded_files)
- Load only the relevant topic files for each question
- Support lap-scoped and time-scoped questions
- Return answers in plain English with supporting numbers
"""

import os

from google.adk.agents import Agent

from tools.csv_loader import get_schema
from tools.query_engine import (
    find_threshold_events,
    get_column_stats,
    get_column_stats_for_zone,
    get_time_series,
    get_zone_time_windows,
    query_cross_topic,
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def describe_uploaded_files(file_paths: list[str]) -> dict:
    """
    Discover what topics and columns are available in the uploaded session files.

    Call this first whenever you are unsure which file contains a column needed
    to answer the user's question. Returns topic names, all column names, time
    ranges, and row counts (which indicate sample rate).

    Args:
        file_paths: List of all file paths available for this session.

    Returns:
        Dict with session IDs as keys, each containing:
            - topics: list of topic names
            - columns: all column names across all topics
            - time_range: [start, end] Unix float timestamps
            - row_counts: dict of topic -> row count
            - duration_seconds: session duration
    """
    schema = get_schema(file_paths)
    for info in schema.values():
        t_start, t_end = info["time_range"]
        info["duration_seconds"] = round(t_end - t_start, 3)
    return schema


def stats_for_column(
    file_path: str,
    column: str,
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Get min, max, mean, and percentile statistics for one column in one topic file.

    Use this for questions like:
    - "What was the max speed?"
    - "Average brake pressure in lap 3?"
    - "What was the range of steering angles?"

    To scope the question to a lap, pass the lap's t_start and t_end from
    resolve_lap_window.

    Args:
        file_path: Path to the relevant rosbag2 CSV file.
        column:    Exact column name to analyse.
        t_start:   Optional start time as Unix float.
        t_end:     Optional end time as Unix float.

    Returns:
        Dict with min, max, mean, std, p25, p50, p75, p95, count.
    """
    return get_column_stats(file_path, column, t_start, t_end)


def signal_over_time(
    file_path: str,
    columns: list[str],
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Return a time series signal for one or more columns from one topic file.

    Use this for questions like:
    - "Show me speed over the session."
    - "How did brake pressure change during lap 2?"
    - "Plot steering angle over time."

    Data is downsampled to 500 points to keep responses manageable.

    Args:
        file_path: Path to the relevant rosbag2 CSV file.
        columns:   List of column names to retrieve.
        t_start:   Optional start time as Unix float.
        t_end:     Optional end time as Unix float.

    Returns:
        Dict with t (timestamps), data (column → values), total_rows, returned_rows.
    """
    return get_time_series(file_path, columns, t_start, t_end)


def events_above_threshold(
    file_path: str,
    column: str,
    operator: str,
    threshold: float,
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Find every moment a sensor value crosses a threshold.

    Use this for questions like:
    - "When did tire temperature exceed 90°C?"
    - "When was the speed below 5 m/s?"
    - "How long was brake pressure above 40 bar?"

    Args:
        file_path:  Path to the relevant rosbag2 CSV file.
        column:     Column name to check.
        operator:   One of: '>', '>=', '<', '<=', '=='.
        threshold:  The numeric threshold value.
        t_start:    Optional start time filter.
        t_end:      Optional end time filter.

    Returns:
        Dict with events list, count, first/last event timestamps,
        and total_duration_seconds the condition was true.
    """
    return find_threshold_events(file_path, column, operator, threshold, t_start, t_end)


def correlate_signals(
    file_paths: list[str],
    columns: list[str],
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Align 2–5 topic files and return specific columns together for cross-topic analysis.

    Use this for questions like:
    - "What was the steering angle when speed was highest?"
    - "Compare brake pressure vs tire temperature."
    - "Show wheel speed vs vehicle speed through a lap."

    Column names in the result are suffixed with the topic name
    (e.g. 'actual_velocity_mps__ControlStatus'). If unsure which files to pass,
    call describe_uploaded_files first to find which file contains each column.

    Args:
        file_paths: 2–5 relevant rosbag2 CSV file paths.
        columns:    Column names in 'column__topic' format. Pass [] for all columns.
        t_start:    Optional start time filter.
        t_end:      Optional end time filter.

    Returns:
        Dict with t, data, total_rows, returned_rows, available_columns.
    """
    return query_cross_topic(file_paths, columns, t_start, t_end)


def stats_for_zone(
    data_file_path: str,
    stat_file_path: str,
    zone_name: str,
    column: str,
) -> dict:
    """
    Get aggregated statistics for a column scoped to a named track zone/segment.

    Use this for questions like:
    - "Max speed in sector 1?"
    - "Average brake pressure in the hairpin across all laps?"
    - "Tire temperature in the chicane per lap?"

    Returns overall stats (pooled across all laps) and a per-lap breakdown.
    Call list_zones first if unsure of the zone name.

    Args:
        data_file_path: Path to the topic CSV containing the column.
        stat_file_path: Path to the *_stat.csv file (must have zone column).
        zone_name:      Track zone label (e.g. 'sector_1', 'hairpin').
        column:         Column name to analyse.

    Returns:
        Dict with zone, column, overall stats, per_lap breakdown, available_zones.
    """
    return get_column_stats_for_zone(data_file_path, stat_file_path, zone_name, column)


def list_zones(stat_file_path: str) -> dict:
    """
    List all track zone/segment names available in the stat file.

    Call this when the user refers to a track section but you are unsure of
    the exact zone label (e.g. "the chicane" may be stored as "sector_2").

    Args:
        stat_file_path: Path to the *_stat.csv file.

    Returns:
        Dict with available_zones (list of zone name strings).
    """
    result = get_zone_time_windows(stat_file_path, "__probe__")
    return {"available_zones": result.get("available_zones", [])}


def resolve_lap_window(lap_boundaries: list[dict], lap_number: int) -> dict:
    """
    Resolve a lap number to its start and end timestamps.

    Call this before any query when the user's question is scoped to a specific
    lap (e.g. "in lap 3", "during lap 2"). Pass the returned t_start and t_end
    to other query tools.

    Args:
        lap_boundaries: List of dicts with keys 'lap', 't_start', 't_end'.
                        Example: [{'lap': 1, 't_start': 1751477600.0, 't_end': 1751477712.5}]
        lap_number:     The lap number to resolve (1-indexed).

    Returns:
        Dict with lap, t_start, t_end — or an error key if lap not found.
    """
    for b in lap_boundaries:
        if b["lap"] == lap_number:
            return b
    available = [b["lap"] for b in lap_boundaries]
    return {"error": f"Lap {lap_number} not found. Available laps: {available}"}


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

root_agent = Agent(
    name="qa_agent",
    model=os.getenv("VERTEX_AI_MODEL", "gemini-1.5-pro"),
    description=(
        "Answers any natural language question about race telemetry data. "
        "Handles stats, event detection, time series, cross-topic correlation, "
        "and lap-scoped or time-scoped queries."
    ),
    instruction="""
You are a race engineer analyst answering questions about telemetry data from an
autonomous racing car. Data comes from ROS2 rosbag2 CSV exports — one file per topic.
Users can ask anything — your job is to figure out how to answer it using the tools.

## Step-by-step approach for any question

1. **Understand what the user wants.**
   Identify: which signal(s), over what time range (full session / specific lap / time window),
   and what kind of answer (single number / time series / event list / comparison).

2. **Identify which files contain the needed columns.**
   Use your knowledge of common topic-to-column mappings below.
   If you are unsure, call describe_uploaded_files with all session file paths first —
   it returns all column names grouped by topic so you can find the right file.

3. **Resolve lap window if needed.**
   If the user says "in lap 3" or "during lap 2", call resolve_lap_window first
   to get t_start and t_end, then pass those to the query tool.

4. **Call the right query tool.**
   - Single stat (max/min/mean/range) → stats_for_column
   - Stat scoped to a track segment/zone → stats_for_zone (pass stat file + data file)
   - "When did X happen?" or "how long was X above Y?" → events_above_threshold
   - "Show me X over time" or trend questions → signal_over_time
   - Two or more signals together / correlations → correlate_signals
   - Unsure of zone name → call list_zones with the stat file path first

5. **Answer in plain English.**
   - Convert m/s to mph where helpful (× 2.23694)
   - Round numbers to 2 decimal places
   - If a column is missing, tell the user what IS available

## Common topic-to-column mappings
| What user asks about | File topic | Key columns |
|---|---|---|
| Speed / velocity | ControlStatus | actual_velocity_mps, target_velocity_mps |
| Steering | ControlStatus | actual_steering_degree, cmd_steering_degree |
| Throttle / brake (commanded) | ControlStatus | cmd_throttle, cmd_brake |
| Brake pressure (actual) | brake_pressure_report | brake_pressure_fdbk_front, brake_pressure_fdbk_rear |
| Wheel speed | wheel_speed | wheel_speed_fl, wheel_speed_fr, wheel_speed_rl, wheel_speed_rr |
| Tire temperature | tire_temp_fl/fr/rl/rr | fl_tire_temp_01..04 (per corner) |
| Tire pressure | tire_pressure_fl/fr/rl/rr | tire pressure per corner |
| Acceleration / G-force | Imu | linear_acceleration_x/y/z |
| Yaw / attitude | attitude_group | yawpitchroll_x/y/z |
| GPS position | gps_top or gps_side | latitude, longitude |
| Suspension / ride height | potentiometer | wheel_potentiometer_fl/fr/rl/rr |
| Engine / powertrain | marelli, pt_report_1/2/3 | varies |
| Gear | ControlStatus | actual_gear, cmd_gear |
| Cross track / heading error | ControlStatus | cross_track_error, heading_error |
| MPC state | ControlStatus | mpc_failed, mpc_steering_cmd |
| Controller computation time | ControlStatus | controller_computation_time |

## Column naming in cross-topic results
When using correlate_signals, result columns are named 'column__topic'.
Strip the suffix when presenting results to the user.

## What you must not do
- Do not load all uploaded files to answer a simple question
- Do not make up values — if data is missing, say so clearly
- Do not perform lap detection — use resolve_lap_window with provided boundaries
""",
    tools=[
        describe_uploaded_files,
        stats_for_column,
        stats_for_zone,
        list_zones,
        signal_over_time,
        events_above_threshold,
        correlate_signals,
        resolve_lap_window,
    ],
)
