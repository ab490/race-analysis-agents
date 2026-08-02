"""
QA Agent: answers natural language questions about race telemetry data.

Responsibilities:
- Accept any natural language question about a session
- Discover which files contain which columns when unsure (via describe_uploaded_files)
- Load only the relevant topic files for each question
- Support lap-scoped and time-scoped questions
- Lap time summaries, stint trends, sector breakdowns, anomaly detection
- Return answers in plain English with supporting numbers
"""

import contextvars as _cv
import os
from pathlib import Path
from google.adk.agents import Agent

from tools.csv_loader import COORD_COLS, _align_session, _load_raw, get_schema
from tools.gcs_store import download_raw_file as _download_raw_file

# Per-request context: set by the query route before running the agent
_session_ctx: _cv.ContextVar[str] = _cv.ContextVar("session_id", default="")
_tempdir_ctx: _cv.ContextVar[str] = _cv.ContextVar("temp_dir", default="")
from tools.plot_generator import (
    make_gg_diagram,
    make_multi_lap_overlay,
    make_time_series,
    make_track_map,
    make_xy_plot,
)
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


def get_topic_file(topic_name: str) -> dict:
    """
    Download a raw topic CSV for this session and return its local file path.

    Call this to get the file path for any topic BEFORE passing it to query or
    plot tools. The topic_name must match one of the topic names listed in
    "Available topics and columns" in your context.

    Args:
        topic_name: Topic name exactly as listed (e.g. 'ControlStatus',
                    'wheel_speed', 'Imu'). For the stat file use '_stat'.

    Returns:
        Dict with 'path' (local file path to pass to other tools) and 'topic',
        or {'error': '...'} if the topic is not found.
    """
    session_id = _session_ctx.get()
    temp_dir = _tempdir_ctx.get()
    if not session_id:
        return {"error": "No session context available."}
    try:
        path = _download_raw_file(session_id, topic_name, target_dir=temp_dir or None)
        return {"path": path, "topic": topic_name}
    except FileNotFoundError:
        return {"error": f"Topic '{topic_name}' not found in session. Check available topics in context."}
    except Exception as e:
        return {"error": str(e)}


def align_topics(topic_names: list[str]) -> dict:
    """
    Download the requested topic CSVs, align them by timestamp, and return the
    path to a single merged CSV containing all their columns.

    Always call this FIRST - before any stats, event, or plot tool - passing
    every topic you need to answer the question. The stat file (_stat) is always
    included automatically; do not list it in topic_names.

    Args:
        topic_names: Topic names exactly as listed in "Available topics and columns"
                     (e.g. ["ControlStatus", "wheel_speed"]). Do not include "_stat".

    Returns:
        Dict with:
            - aligned_path: local file path to pass to all other tools
            - columns: list of all data columns in the aligned file
            - row_count: number of rows
        Or {"error": "..."} on failure.
    """
    session_id = _session_ctx.get()
    temp_dir = _tempdir_ctx.get()
    if not session_id:
        return {"error": "No session context available."}

    topic_dfs = {}

    # Stat file is always included - already downloaded to temp_dir at query start
    stat_files = list(Path(temp_dir).glob("*_stat.csv"))
    if stat_files:
        stat_path = str(stat_files[0])
    else:
        try:
            stat_path = _download_raw_file(session_id, "_stat", target_dir=temp_dir)
        except Exception as e:
            return {"error": f"Could not load stat file: {e}"}
    try:
        topic_dfs["_stat"] = _load_raw(Path(stat_path))
    except Exception as e:
        return {"error": f"Failed to load stat file: {e}"}

    # Download and load each requested topic (strip coordinate columns to avoid
    # collision with the stat file's lat/lon/position_x/y columns)
    for topic in topic_names:
        if topic in ("_stat", "stat"):
            continue
        try:
            path = _download_raw_file(session_id, topic, target_dir=temp_dir)
            df = _load_raw(Path(path))
            df = df.drop(columns=[c for c in COORD_COLS if c in df.columns], errors="ignore")
            topic_dfs[topic] = df
        except FileNotFoundError:
            return {"error": f"Topic '{topic}' not found. Check available topics in context."}
        except Exception as e:
            return {"error": f"Failed to load topic '{topic}': {e}"}

    try:
        aligned_df = _align_session(topic_dfs)
    except Exception as e:
        return {"error": f"Alignment failed: {e}"}

    aligned_path = str(Path(temp_dir) / "aligned_query.csv")
    aligned_df.to_csv(aligned_path, index=False)

    columns = [c for c in aligned_df.columns if c != "stamp_seconds"]
    return {
        "aligned_path": aligned_path,
        "columns": columns,
        "row_count": len(aligned_df),
    }


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
        On error, returns {"error": "...", "available_columns": [...]} so the agent can retry.
    """
    try:
        return get_column_stats(file_path, column, t_start, t_end)
    except Exception as e:
        return {"error": str(e)}


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
        Dict with t (timestamps), data (column -> values), total_rows, returned_rows.
        On error, returns {"error": "...", "available_columns": [...]} so the agent can retry.
    """
    try:
        return get_time_series(file_path, columns, t_start, t_end)
    except Exception as e:
        return {"error": str(e)}


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
        On error, returns {"error": "..."} so the agent can retry.
    """
    try:
        return find_threshold_events(file_path, column, operator, threshold, t_start, t_end)
    except (KeyError, ValueError, Exception) as e:
        return {"error": str(e)}


def correlate_signals(
    file_paths: list[str],
    columns: list[str],
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Align 2-5 topic files and return specific columns together for cross-topic analysis.

    Use this for questions like:
    - "What was the steering angle when speed was highest?"
    - "Compare brake pressure vs tire temperature."
    - "Show wheel speed vs vehicle speed through a lap."

    Unique column names are kept as-is; only columns that appear in multiple
    topics are suffixed with the topic name (e.g. 'speed_wheel_speed').
    If unsure which files to pass, call describe_uploaded_files first.

    Args:
        file_paths: 2-5 relevant rosbag2 CSV file paths.
        columns:    Column names to return. Pass [] for all columns. Use
                    available_columns from a prior call if unsure of exact names.
        t_start:    Optional start time filter.
        t_end:      Optional end time filter.

    Returns:
        Dict with t, data, total_rows, returned_rows, available_columns.
    """
    try:
        return query_cross_topic(file_paths, columns, t_start, t_end)
    except Exception as e:
        return {"error": str(e)}


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
    try:
        return get_column_stats_for_zone(data_file_path, stat_file_path, zone_name, column)
    except Exception as e:
        return {"error": str(e)}


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
        Dict with lap, t_start, t_end - or an error key if lap not found.
    """
    for b in lap_boundaries:
        if b["lap"] == lap_number:
            return b
    available = [b["lap"] for b in lap_boundaries]
    return {"error": f"Lap {lap_number} not found. Available laps: {available}"}


def summarise_lap_times(lap_boundaries: list[dict]) -> dict:
    """
    Compute lap time durations and summary statistics from lap boundaries.

    Use this for any question about lap times:
    - "How were the lap times?"
    - "Which lap was fastest?"
    - "What was the delta to best lap?"

    Lap 0 is the outlap (before the first start/finish crossing) and is excluded.
    Lap 1+ are the racing laps.

    Args:
        lap_boundaries: List of {lap, t_start, t_end} dicts for the session.

    Returns:
        Dict with laps (list of {lap, duration_s, delta_to_best_s}),
        fastest_lap, fastest_time_s, slowest_lap, slowest_time_s,
        mean_time_s, lap_count. On error, returns {"error": "..."}.
    """
    racing = [b for b in lap_boundaries if b["lap"] > 0]
    if not racing:
        return {"error": "No racing laps found (lap > 0). Only outlap detected."}

    durations = [
        {"lap": b["lap"], "duration_s": round(b["t_end"] - b["t_start"], 3)}
        for b in racing
    ]
    times = [d["duration_s"] for d in durations]
    best = min(times)
    for d in durations:
        d["delta_to_best_s"] = round(d["duration_s"] - best, 3)

    fastest = min(durations, key=lambda x: x["duration_s"])
    slowest = max(durations, key=lambda x: x["duration_s"])

    return {
        "laps": durations,
        "fastest_lap": fastest["lap"],
        "fastest_time_s": fastest["duration_s"],
        "slowest_lap": slowest["lap"],
        "slowest_time_s": slowest["duration_s"],
        "mean_time_s": round(sum(times) / len(times), 3),
        "lap_count": len(racing),
    }


def stint_trend(
    file_path: str,
    column: str,
    lap_boundaries: list[dict],
    stat: str = "max",
) -> dict:
    """
    Track how a metric changes across laps (stint performance trend).

    Use this for questions like:
    - "Did speed degrade over the stint?"
    - "How did peak brake pressure change lap by lap?"
    - "Is tire temperature increasing across laps?"

    Args:
        file_path:      Path to the topic CSV containing the column.
        column:         Column name to analyse.
        lap_boundaries: List of {lap, t_start, t_end} dicts.
        stat:           One of 'max', 'min', or 'mean'. Default: 'max'.

    Returns:
        Dict with column, stat, trend (list of {lap, value}), available_columns.
        On error, returns {"error": "...", "available_columns": [...]}.
    """
    if stat not in ("max", "min", "mean"):
        return {"error": "stat must be one of: max, min, mean"}

    racing = [b for b in lap_boundaries if b["lap"] > 0]
    if not racing:
        return {"error": "No racing laps found."}

    try:
        df = _load_raw(Path(file_path))
    except Exception as e:
        return {"error": str(e)}

    available = [c for c in df.columns if c != "stamp_seconds"]
    if column not in df.columns:
        return {"error": f"Column '{column}' not found.", "available_columns": available}

    trend = []
    for b in racing:
        window = df[(df["stamp_seconds"] >= b["t_start"]) & (df["stamp_seconds"] <= b["t_end"])][column].dropna()
        if window.empty:
            continue
        trend.append({"lap": b["lap"], "value": round(float(getattr(window, stat)()), 4)})

    if not trend:
        return {"error": f"No data found for '{column}' in any racing lap.", "available_columns": available}

    return {"column": column, "stat": stat, "trend": trend, "available_columns": available}


def sector_times(stat_file_path: str) -> dict:
    """
    Get the time each racing lap spent in each track zone/sector.

    Use this for sector analysis questions like:
    - "Which sector was slowest?"
    - "How did sector 1 time change across laps?"
    - "Where am I losing time?"

    Args:
        stat_file_path: Path to the *_stat.csv file (must have zone and lap columns).

    Returns:
        Dict with zones (list of zone names) and sector_times
        ({zone: {lap: duration_s, ...}, ...}). On error, returns {"error": "..."}.
    """
    df = _load_raw(Path(stat_file_path))
    missing = [c for c in ("zone", "lap") if c not in df.columns]
    if missing:
        return {"error": f"stat file missing columns: {missing}. Was zone assignment run during upload?"}

    available_zones = sorted(df["zone"].dropna().unique().tolist())
    result: dict[str, dict[int, float]] = {}

    for zone in available_zones:
        zone_result = get_zone_time_windows(stat_file_path, zone)
        if "error" in zone_result:
            continue
        per_lap: dict[int, float] = {}
        for w in zone_result["windows"]:
            lap = w.get("lap")
            if lap is not None and lap > 0:
                per_lap[lap] = round(w["t_end"] - w["t_start"], 3)
        if per_lap:
            result[zone] = per_lap

    return {"zones": list(result.keys()), "sector_times": result}


def compute_resultant(
    file_path: str,
    columns: list[str],
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Compute the vector magnitude (sqrt of sum of squares) of 2 or 3 columns,
    then return statistics on the result.

    Use this whenever a metric is stored as components rather than a single value:
    - Resultant linear speed from velocity_x, velocity_y (or add velocity_z)
    - Total linear acceleration from linear_acceleration_x/y/z
    - Resultant angular velocity from angular_velocity_x/y/z
    - Net force or any other vector quantity split across axes

    Example: compute_resultant(path, ["angular_velocity_x", "angular_velocity_y",
             "angular_velocity_z"]) returns stats for sqrt(x²+y²+z²).

    Args:
        file_path: Path to the topic CSV.
        columns:   2 or 3 column names whose squares will be summed under sqrt.
        t_start:   Optional start time filter.
        t_end:     Optional end time filter.

    Returns:
        Dict with min, max, mean, std, p25, p50, p75, p95, count, and
        'resultant_of' listing the source columns.
        On error, returns {"error": "...", "available_columns": [...]}.
    """
    try:
        df = _load_raw(Path(file_path))
    except Exception as e:
        return {"error": str(e)}

    available = [c for c in df.columns if c != "stamp_seconds"]
    missing = [c for c in columns if c not in df.columns]
    if missing:
        return {"error": f"Columns not found: {missing}", "available_columns": available}

    if t_start is not None:
        df = df[df["stamp_seconds"] >= t_start]
    if t_end is not None:
        df = df[df["stamp_seconds"] <= t_end]

    if df.empty:
        return {"error": "No data in the specified time range.", "available_columns": available}

    import numpy as np
    resultant = np.sqrt(sum(df[c] ** 2 for c in columns))

    return {
        "resultant_of": columns,
        "min": round(float(resultant.min()), 4),
        "max": round(float(resultant.max()), 4),
        "mean": round(float(resultant.mean()), 4),
        "std": round(float(resultant.std()), 4),
        "p25": round(float(resultant.quantile(0.25)), 4),
        "p50": round(float(resultant.quantile(0.50)), 4),
        "p75": round(float(resultant.quantile(0.75)), 4),
        "p95": round(float(resultant.quantile(0.95)), 4),
        "count": int(resultant.count()),
    }


def detect_anomalies(
    file_path: str,
    column: str,
    n_sigma: float = 3.0,
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Detect statistically anomalous values in a signal (outside mean ± n_sigma * std).

    Use this for questions like:
    - "Was there anything unusual in the G-forces?"
    - "Were there any tire temperature spikes?"
    - "Did wheel speed ever behave unexpectedly?"
    - "Were there any MPC failures or unusual controller events?"

    Args:
        file_path: Path to the topic CSV.
        column:    Column name to check.
        n_sigma:   Standard deviations from mean to flag. Default 3.0.
        t_start:   Optional start time filter.
        t_end:     Optional end time filter.

    Returns:
        Dict with column, mean, std, threshold_high, threshold_low, n_sigma,
        anomalies_high ({count, events}), anomalies_low ({count, events}),
        total_anomaly_count. On error, returns {"error": "..."}.
    """
    try:
        stats = get_column_stats(file_path, column, t_start, t_end)
    except Exception as e:
        return {"error": str(e)}

    mean = stats["mean"]
    std = stats["std"]

    if std == 0:
        return {
            "column": column,
            "mean": mean,
            "std": 0,
            "note": "Zero variance - signal is constant, no anomalies.",
            "total_anomaly_count": 0,
        }

    threshold_high = mean + n_sigma * std
    threshold_low = mean - n_sigma * std

    high = find_threshold_events(file_path, column, ">", threshold_high, t_start, t_end)
    low = find_threshold_events(file_path, column, "<", threshold_low, t_start, t_end)

    return {
        "column": column,
        "mean": mean,
        "std": std,
        "threshold_high": round(threshold_high, 4),
        "threshold_low": round(threshold_low, 4),
        "n_sigma": n_sigma,
        "anomalies_high": {"count": high["count"], "events": high["events"][:20]},
        "anomalies_low": {"count": low["count"], "events": low["events"][:20]},
        "total_anomaly_count": high["count"] + low["count"],
    }


# ---------------------------------------------------------------------------
# Plot tools - generate Plotly figures to include alongside analysis
# ---------------------------------------------------------------------------

def plot_time_series(
    file_path: str,
    columns: list[str],
    title: str,
    y_label: str,
    t_start: float | None = None,
    t_end: float | None = None,
    y_scale: float = 1.0,
) -> dict:
    """
    Generate a time-series chart for one or more columns.

    Use this whenever the user asks for a chart, plot, or graph alongside
    an analysis - or any "show me X over time" request.

    Args:
        file_path: Path to the topic CSV.
        columns:   Column name(s) to plot.
        title:     Chart title.
        y_label:   Y-axis label with units (e.g. "Speed (m/s)").
        t_start:   Optional start time filter.
        t_end:     Optional end time filter.
        y_scale:   Multiply y values by this factor (e.g. 2.23694 for m/s->mph).

    Returns:
        Plotly figure dict or {"error": "..."} with available columns.
    """
    return make_time_series(file_path, columns, title, y_label, t_start, t_end, y_scale)


def plot_lap_overlay(
    file_path: str,
    column: str,
    lap_windows: list[dict],
    title: str,
    y_label: str,
    y_scale: float = 1.0,
) -> dict:
    """
    Overlay the same signal across multiple laps on one chart.

    Use this for lap comparison requests like "compare brake pressure lap 1 vs lap 3",
    or to visualise a signal across all racing laps.

    Args:
        file_path:   Path to the topic CSV.
        column:      Column to compare.
        lap_windows: List of {lap, t_start, t_end} dicts.
        title:       Chart title.
        y_label:     Y-axis label with units.
        y_scale:     Multiply y values by this factor.

    Returns:
        Plotly figure dict or {"error": "..."} with available columns.
    """
    return make_multi_lap_overlay(file_path, column, lap_windows, title, y_label, y_scale)


def plot_track_map(
    stat_file_path: str,
    color_column: str | None = None,
    color_label: str = "",
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Generate a track map from GPS lat/lon in the stat file.

    Optionally colour the path by any signal (e.g. speed heatmap on track).

    Args:
        stat_file_path: Path to the enriched *_stat.csv file.
        color_column:   Optional column to use as colour scale. None = plain line.
        color_label:    Label for the colour scale bar.
        t_start:        Optional time filter.
        t_end:          Optional time filter.

    Returns:
        Plotly figure dict or {"error": "..."}.
    """
    return make_track_map(stat_file_path, color_column, color_label, t_start, t_end)


def plot_xy(
    x_file_path: str,
    x_column: str,
    y_file_path: str,
    y_column: str,
    title: str,
    x_label: str,
    y_label: str,
    t_start: float | None = None,
    t_end: float | None = None,
    x_scale: float = 1.0,
    y_scale: float = 1.0,
) -> dict:
    """
    Plot any column against any other column (from the same or different topic files).

    Use this for requests like:
    - "speed vs distance" (x=cumulative_distance from stat file, y=actual_velocity_mps from ControlStatus)
    - "steering angle vs speed"
    - "brake pressure vs lateral G"
    - Any "X vs Y" chart where X is not time

    If the two columns are in different files, they are aligned by timestamp automatically.
    Pass the same path for x_file_path and y_file_path when both columns are in one file.

    Args:
        x_file_path: Path to the CSV containing the X-axis column.
        x_column:    Column name for the X axis.
        y_file_path: Path to the CSV containing the Y-axis column.
        y_column:    Column name for the Y axis.
        title:       Chart title.
        x_label:     X-axis label with units (e.g. "Distance (m)").
        y_label:     Y-axis label with units (e.g. "Speed (m/s)").
        t_start:     Optional start time filter.
        t_end:       Optional end time filter.
        x_scale:     Multiply X values by this factor.
        y_scale:     Multiply Y values by this factor.

    Returns:
        Plotly figure dict or {"error": "..."} with available columns.
    """
    return make_xy_plot(x_file_path, x_column, y_file_path, y_column,
                        title, x_label, y_label, t_start, t_end, x_scale, y_scale)


def plot_gg_diagram(
    imu_file_path: str,
    lat_accel_col: str = "linear_acceleration_y",
    lon_accel_col: str = "linear_acceleration_x",
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Generate a GG diagram (lateral vs longitudinal acceleration scatter).

    Acceleration is automatically converted from m/s² to g.

    Args:
        imu_file_path:  Path to the IMU CSV file.
        lat_accel_col:  Lateral acceleration column. Default: "linear_acceleration_y".
        lon_accel_col:  Longitudinal acceleration column. Default: "linear_acceleration_x".
        t_start:        Optional time filter.
        t_end:          Optional time filter.

    Returns:
        Plotly figure dict or {"error": "..."} with available columns.
    """
    return make_gg_diagram(imu_file_path, lat_accel_col, lon_accel_col, t_start, t_end)


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

root_agent = Agent(
    name="qa_agent",
    model=os.getenv("VERTEX_AI_MODEL", "gemini-2.5-flash"),
    generate_content_config={"temperature": 0},
    description=(
        "Answers any natural language question about race telemetry data. "
        "Handles stats, event detection, time series, cross-topic correlation, "
        "lap-scoped queries, lap time summaries, stint trends, sector breakdowns, "
        "and anomaly detection."
    ),
    instruction="""
You are a race engineer analyst answering questions about telemetry data from an
autonomous racing car. Data comes from ROS2 rosbag2 CSV exports - one file per topic.
Users can ask anything - your job is to figure out how to answer it using the tools.

CRITICAL: You may briefly narrate what you are doing (e.g. "Aligning brake_pressure_report..."),
but you MUST immediately follow every narration with a tool call. A text-only response
is never acceptable — your final output must always be the JSON report produced after
calling all necessary tools.

## Step-by-step approach for any question

1. **Call align_topics first - always.**
   Look at "Available topics and columns" in your context and identify every topic
   that contains a column relevant to the question. Call align_topics with all of
   them in one go. This downloads the files and merges them into a single aligned
   CSV. Use the returned `aligned_path` for every tool call after this step.
   The stat file is always included automatically - do not list it.

   If the exact column doesn't exist, look for alternatives:
   - Speed may be stored as actual_velocity_mps, hor_speed, vel_magnitude, or
     as components (velocity_x/y/z) - use compute_resultant for components
   - Acceleration may be linear_acceleration_x/y/z - use compute_resultant for magnitude
   - Angular rate may be angular_velocity_x/y/z - use compute_resultant for magnitude
   - Always pick the most relevant available column rather than giving up

2. **Resolve lap window if needed.**
   If the user says "in lap 3" or "during lap 2", call resolve_lap_window to get
   t_start and t_end, then pass those to every subsequent tool call.

3. **Call the right query tool - all using aligned_path.**
   - Single stat (max/min/mean/range) -> stats_for_column(aligned_path, column)
   - Metric stored as x/y/z components -> compute_resultant(aligned_path, [cols])
   - Stat scoped to a track zone -> stats_for_zone(aligned_path, aligned_path, zone)
   - "When did X happen?" / "how long was X above Y?" -> events_above_threshold
   - "Show me X over time" / trend questions -> signal_over_time
   - Unsure of zone names -> list_zones(aligned_path)
   - Lap time summary / fastest lap / delta to best -> summarise_lap_times
   - Per-lap metric trend -> stint_trend(aligned_path, column, lap_boundaries)
   - Sector / zone time breakdown -> sector_times(aligned_path)
   - Spikes / anomalies -> detect_anomalies(aligned_path, column)

4. **Add a plot whenever it adds value.**
   Use plot tools proactively - any time a visual makes the answer richer.
   All plots take aligned_path as the file argument.
   - Signal over time -> plot_time_series(aligned_path, columns)
   - Lap comparison -> plot_lap_overlay(aligned_path, column, lap_windows)
   - Track map / heatmap -> plot_track_map(aligned_path)
   - GG diagram -> plot_gg_diagram(aligned_path, lat_col, lon_col)
   - X vs Y (e.g. speed vs distance) -> plot_xy(aligned_path, x_col, aligned_path, y_col)
   Include the figure dict verbatim - do NOT modify it.

5. **Handle tool errors by retrying - never give up on the first error.**
   If a tool returns `{"error": "..."}`, read the available column names it returns,
   pick the closest match, and retry. Only report a column as unavailable if there
   is truly no relevant alternative.

6. **Answer in plain English.**
   - Convert m/s to mph where helpful (× 2.23694)
   - Round numbers to 2 decimal places
   - If no relevant column exists at all, tell the user what IS available

## Common topic-to-column mappings (hints only - always verify against actual context)
| What user asks about | Topic | Key columns |
|---|---|---|
| Speed / velocity | ControlStatus | actual_velocity_mps, target_velocity_mps |
| Steering | ControlStatus | actual_steering_degree, cmd_steering_degree |
| Throttle / brake (commanded) | ControlStatus | cmd_throttle, cmd_brake |
| Brake pressure (actual) | brake_pressure_report | brake_pressure_fdbk_front, brake_pressure_fdbk_rear |
| Wheel speed | wheel_speed | wheel_speed_fl, wheel_speed_fr, wheel_speed_rl, wheel_speed_rr |
| Tire temperature | tire_temp_fl/fr/rl/rr | fl_tire_temp_01..04 (per corner) |
| Acceleration / G-force | Imu | linear_acceleration_x/y/z |
| Yaw / attitude | attitude_group | yawpitchroll_x/y/z |
| GPS position | gps_top or gps_side | latitude, longitude |
| Suspension / ride height | potentiometer | wheel_potentiometer_fl/fr/rl/rr |
| Gear | ControlStatus | actual_gear, cmd_gear |
| Cross track / heading error | ControlStatus | cross_track_error, heading_error |
| MPC state | ControlStatus | mpc_failed, mpc_steering_cmd |

## Output format - MANDATORY

Your ENTIRE response must be a single valid JSON object. Do NOT output any prose,
markdown, or explanation text before or after the JSON.

The JSON must have this structure:
- "title": short string
- "sections": list of section objects

Section types:
- Text section: "type" is "text", "content" is a markdown string
- Plot section: "type" is "plot", "figure" is the plotly dict returned by the plot tool (copied verbatim), "caption" is a short string

Rules:
- Output ONLY the JSON object - nothing before it, nothing after it
- When you call a plot tool, copy its entire return value verbatim as the "figure"
  field of a plot section. Never describe a plot in prose - always include the figure.
- Text sections support markdown (bold, bullet lists, tables)
- For text-only answers a single text section is fine
- For answers with plots, put text section first, then plot section(s)

## What you must not do
- Do not load all uploaded files to answer a simple question
- Do not make up values - if data is missing, say so clearly
- Do not perform lap detection - use resolve_lap_window with provided boundaries
""",
    tools=[
        align_topics,
        describe_uploaded_files,
        stats_for_column,
        stats_for_zone,
        list_zones,
        signal_over_time,
        events_above_threshold,
        resolve_lap_window,
        summarise_lap_times,
        stint_trend,
        sector_times,
        detect_anomalies,
        compute_resultant,
        plot_time_series,
        plot_lap_overlay,
        plot_track_map,
        plot_gg_diagram,
        plot_xy,
    ],
)