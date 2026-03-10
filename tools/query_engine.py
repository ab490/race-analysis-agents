"""
Query engine: reusable data query functions used by agents.

All functions operate on single topic files or small aligned subsets.
Agents select which files to load based on the question - never load all
files at once.

Lap-based queries require lap boundaries produced by lap_detector.detect_laps.
Agents resolve "lap 3" -> (t_start, t_end) via get_lap_time_windows and pass
those time bounds into the query functions below.
"""

from pathlib import Path
import pandas as pd

from tools.csv_loader import COORD_COLS, _align_session, _load_raw, _parse_filename


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _apply_time_filter(
    df: pd.DataFrame,
    t_start: float | None,
    t_end: float | None,
) -> pd.DataFrame:
    if t_start is not None:
        df = df[df["stamp_seconds"] >= t_start]
    if t_end is not None:
        df = df[df["stamp_seconds"] <= t_end]
    return df


# ---------------------------------------------------------------------------
# Single-topic queries
# ---------------------------------------------------------------------------

def get_column_stats(
    file_path: str,
    column: str,
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Return descriptive statistics for one column in one topic CSV file.

    Use this for questions like "what was the max speed?", "average brake
    pressure?", "min/max tire temperature?". Supports optional time window
    so agents can answer lap-scoped questions like "max speed in lap 3"
    by passing the lap's t_start and t_end.

    Args:
        file_path: Path to a single rosbag2 CSV file.
        column:    Exact column name to analyse.
        t_start:   Optional start time (Unix float). Defaults to file start.
        t_end:     Optional end time (Unix float). Defaults to file end.

    Returns:
        Dict with: column, count, min, max, mean, std, p25, p50, p75, p95.

    Raises:
        KeyError: If column does not exist.
        FileNotFoundError: If the file does not exist.
    """
    df = _load_raw(Path(file_path))
    if column not in df.columns:
        raise KeyError(
            f"Column '{column}' not found. "
            f"Available: {[c for c in df.columns if c != 'stamp_seconds']}"
        )

    df = _apply_time_filter(df, t_start, t_end)
    s = df[column].dropna()

    return {
        "column": column,
        "count": int(s.count()),
        "min": float(s.min()),
        "max": float(s.max()),
        "mean": round(float(s.mean()), 4),
        "std": round(float(s.std()), 4),
        "p25": round(float(s.quantile(0.25)), 4),
        "p50": round(float(s.quantile(0.50)), 4),
        "p75": round(float(s.quantile(0.75)), 4),
        "p95": round(float(s.quantile(0.95)), 4),
    }


def get_time_series(
    file_path: str,
    columns: list[str],
    t_start: float | None = None,
    t_end: float | None = None,
    max_points: int = 500,
) -> dict:
    """
    Return time series data for one or more columns from a single topic file.

    Use this for questions like "show me speed over time", "plot brake pressure
    during lap 2", or when you need the raw signal to answer a question.
    Data is downsampled to max_points if the file has more rows, preserving
    the shape of the signal without returning huge payloads.

    Args:
        file_path:  Path to a single rosbag2 CSV file.
        columns:    List of column names to return.
        t_start:    Optional start time filter (Unix float).
        t_end:      Optional end time filter (Unix float).
        max_points: Maximum rows to return (default 500).

    Returns:
        Dict with:
            - t: list of timestamps (Unix float seconds)
            - data: dict mapping column name to list of values
            - total_rows: row count before downsampling
            - returned_rows: rows actually returned
    """
    df = _load_raw(Path(file_path))

    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise KeyError(
            f"Columns not found: {missing}. "
            f"Available: {[c for c in df.columns if c != 'stamp_seconds']}"
        )

    df = _apply_time_filter(df, t_start, t_end)
    total_rows = len(df)

    if total_rows > max_points:
        step = max(1, total_rows // max_points)
        df = df.iloc[::step].head(max_points)

    return {
        "stamp_seconds": df["stamp_seconds"].tolist(),
        "data": {col: df[col].tolist() for col in columns},
        "total_rows": total_rows,
        "returned_rows": len(df),
    }


def find_threshold_events(
    file_path: str,
    column: str,
    operator: str,
    threshold: float,
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Find all moments when a column's value crosses a threshold.

    Use this for questions like "when did tire temp exceed 90°C?",
    "when was brake pressure above 50 bar?", "when was speed below 10 m/s?".

    Args:
        file_path:  Path to a single rosbag2 CSV file.
        column:     Column name to check.
        operator:   One of: '>', '>=', '<', '<=', '=='.
        threshold:  Numeric threshold value.
        t_start:    Optional start time to restrict search window.
        t_end:      Optional end time to restrict search window.

    Returns:
        Dict with:
            - events: list of {t, value} dicts (capped at 200)
            - count: total number of matching rows
            - first_event_t: timestamp of first event (or null)
            - last_event_t: timestamp of last event (or null)
            - total_duration_seconds: estimated total time condition was true
    """
    ops = {">": "__gt__", ">=": "__ge__", "<": "__lt__", "<=": "__le__", "==": "__eq__"}
    if operator not in ops:
        raise ValueError(f"operator must be one of {list(ops.keys())}")

    df = _load_raw(Path(file_path))
    if column not in df.columns:
        raise KeyError(
            f"Column '{column}' not found. "
            f"Available: {[c for c in df.columns if c != 'stamp_seconds']}"
        )

    df = _apply_time_filter(df, t_start, t_end)
    mask = getattr(df[column], ops[operator])(threshold)
    matched = df[mask][["stamp_seconds", column]].dropna()

    # Estimate total duration condition was true
    duration = 0.0
    if len(matched) > 1:
        dt = matched["stamp_seconds"].diff().dropna()
        median_gap = float(dt.median())
        duration = float(dt[dt <= 3 * median_gap].sum())

    events = matched.rename(columns={column: "value"}).to_dict(orient="records")

    return {
        "events": events[:200],
        "count": len(matched),
        "first_event_t": float(matched["stamp_seconds"].iloc[0]) if len(matched) else None,
        "last_event_t": float(matched["stamp_seconds"].iloc[-1]) if len(matched) else None,
        "total_duration_seconds": round(duration, 3),
    }


# ---------------------------------------------------------------------------
# Zone-based queries
# ---------------------------------------------------------------------------

def get_zone_time_windows(
    stat_file_path: str,
    zone_name: str,
) -> dict:
    """
    Return the time windows when the car was in a named track zone/segment.

    The stat file has a 'zone' column assigned during upload. Zones are not
    contiguous across the session - they repeat each lap. This function finds
    every contiguous interval where zone == zone_name and returns them as
    a list of {t_start, t_end, lap} dicts that can be passed into other
    query functions.

    Use this before calling get_column_stats or get_time_series when the
    question is scoped to a segment (e.g. "max speed in sector 1",
    "brake pressure in the hairpin").

    Args:
        stat_file_path: Path to the *_stat.csv file (must have zone and lap columns).
        zone_name:      Exact zone label (e.g. 'sector_1', 'hairpin'). Call
                        list_zones first if unsure of available names.

    Returns:
        Dict with:
            - zone: the requested zone name
            - windows: list of {t_start, t_end, lap} for each occurrence
            - total_windows: number of intervals found
            - available_zones: list of all zone names in the file
    """
    df = _load_raw(Path(stat_file_path))

    if "zone" not in df.columns:
        return {
            "error": "stat file has no 'zone' column - was zone assignment run during upload?",
            "available_columns": list(df.columns),
        }

    available_zones = sorted(df["zone"].dropna().unique().tolist())

    if zone_name not in available_zones:
        return {
            "error": f"Zone '{zone_name}' not found.",
            "available_zones": available_zones,
        }

    # Find contiguous runs where zone matches
    in_zone = df["zone"] == zone_name
    # Group consecutive rows with the same zone value
    run_id = (in_zone != in_zone.shift()).cumsum()
    windows = []
    for _, group in df[in_zone].groupby(run_id[in_zone]):
        lap = int(group["lap"].iloc[0]) if "lap" in group.columns else None
        windows.append({
            "t_start": float(group["stamp_seconds"].iloc[0]),
            "t_end": float(group["stamp_seconds"].iloc[-1]),
            "lap": lap,
        })

    return {
        "zone": zone_name,
        "windows": windows,
        "total_windows": len(windows),
        "available_zones": available_zones,
    }


def get_column_stats_for_zone(
    data_file_path: str,
    stat_file_path: str,
    zone_name: str,
    column: str,
) -> dict:
    """
    Return aggregated statistics for a column scoped to a track zone.

    Combines zone window lookup and column stats into one call. Use this
    for questions like "max speed in sector 1", "average brake pressure in
    the hairpin across all laps".

    Aggregates across ALL laps - returns both per-lap breakdown and
    overall (pooled) statistics.

    Args:
        data_file_path: Path to the topic CSV containing the column.
        stat_file_path: Path to the *_stat.csv file (must have zone column).
        zone_name:      Track zone label (e.g. 'sector_1').
        column:         Column name in the data file to analyse.

    Returns:
        Dict with:
            - zone, column
            - overall: {min, max, mean, std, count} across all zone windows
            - per_lap: list of {lap, t_start, t_end, min, max, mean, count}
            - available_zones: all zones in the stat file
    """
    zone_result = get_zone_time_windows(stat_file_path, zone_name)
    if "error" in zone_result:
        return zone_result

    data_df = _load_raw(Path(data_file_path))
    if column not in data_df.columns:
        raise KeyError(
            f"Column '{column}' not found. "
            f"Available: {[c for c in data_df.columns if c != 'stamp_seconds']}"
        )

    per_lap = []
    all_values = []

    for w in zone_result["windows"]:
        window_df = _apply_time_filter(data_df, w["t_start"], w["t_end"])
        s = window_df[column].dropna()
        if s.empty:
            continue
        all_values.extend(s.tolist())
        per_lap.append({
            "lap": w["lap"],
            "t_start": w["t_start"],
            "t_end": w["t_end"],
            "min": round(float(s.min()), 4),
            "max": round(float(s.max()), 4),
            "mean": round(float(s.mean()), 4),
            "count": int(s.count()),
        })

    if not all_values:
        return {
            "zone": zone_name,
            "column": column,
            "error": "No data found in zone windows.",
            "available_zones": zone_result["available_zones"],
        }

    import numpy as np
    arr = np.array(all_values)
    return {
        "zone": zone_name,
        "column": column,
        "overall": {
            "min": round(float(arr.min()), 4),
            "max": round(float(arr.max()), 4),
            "mean": round(float(arr.mean()), 4),
            "std": round(float(arr.std()), 4),
            "count": len(arr),
        },
        "per_lap": per_lap,
        "available_zones": zone_result["available_zones"],
    }


# ---------------------------------------------------------------------------
# Cross-topic queries
# ---------------------------------------------------------------------------

def query_cross_topic(
    file_paths: list[str],
    columns: list[str],
    t_start: float | None = None,
    t_end: float | None = None,
    max_points: int = 500,
) -> dict:
    """
    Align 2-5 topic files and return specific columns together.

    Use this for cross-topic questions like "what was the steering angle when
    speed was highest?", "compare brake pressure vs tire temperature over time".

    Only pass the files relevant to the question. Unique column names are kept
    as-is; only columns that appear in multiple topics are suffixed with the
    topic name (e.g. 'speed_wheel_speed').

    Args:
        file_paths: List of 2-5 relevant rosbag2 CSV file paths.
        columns:    Column names to return. Pass an empty list to return all columns.
                    Use available_columns from a prior call if unsure of exact names.
        t_start:    Optional start time filter (Unix float).
        t_end:      Optional end time filter (Unix float).
        max_points: Maximum rows to return (default 500).

    Returns:
        Dict with:
            - stamp_seconds: list of timestamps
            - data: dict mapping column name to list of values
            - total_rows: row count before downsampling
            - returned_rows: rows returned
            - available_columns: all columns in the aligned result
    """
    if len(file_paths) > 5:
        raise ValueError(
            "Pass at most 5 files to query_cross_topic to keep queries focused."
        )

    topic_dfs = {}
    for fp in file_paths:
        path = Path(fp)
        _, topic = _parse_filename(path.name)
        raw = _load_raw(path)
        if topic != "_stat":
            raw = raw.drop(columns=[c for c in COORD_COLS if c in raw.columns], errors="ignore")
        topic_dfs[topic] = raw

    df = _align_session(topic_dfs)
    df = _apply_time_filter(df, t_start, t_end)

    available = [c for c in df.columns if c != "stamp_seconds"]

    if columns:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise KeyError(f"Columns not found: {missing}. Available: {available}")
        select_cols = columns
    else:
        select_cols = available

    total_rows = len(df)
    if total_rows > max_points:
        step = max(1, total_rows // max_points)
        df = df.iloc[::step].head(max_points)

    return {
        "stamp_seconds": df["stamp_seconds"].tolist(),
        "data": {col: df[col].tolist() for col in select_cols},
        "total_rows": total_rows,
        "returned_rows": len(df),
        "available_columns": available,
    }