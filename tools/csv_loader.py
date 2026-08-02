"""
CSV loader: single entry point for all telemetry data access.
Agents must never parse CSVs directly; they call these functions instead.

Naming conventions:
  rosbag2 topics:  rosbag2_YYYY_MM_DD-HH_MM_SS_<topic>.csv
  stat file:       rosbag2_YYYY_MM_DD-HH_MM_SS_stat.csv

Timestamp column format:
  ROS2 string:     builtin_interfaces.msg.Time(sec=X, nanosec=Y)  -> columns 'stamp' or 'time'
  Already float:   column named 'stamp_seconds'
"""

import re
from pathlib import Path
import pandas as pd
import numpy as np

# Regex to parse the ROS2 timestamp string
_ROS_TS_RE = re.compile(r"sec=(\d+),\s*nanosec=(\d+)")

# Regex to parse session ID and topic from rosbag2 filename
_FILENAME_RE = re.compile(r"^(rosbag2_\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2})_(.+)\.csv$")

# Reserved topic name for stat file
_STAT_TOPIC = "_stat"

# Coordinate related columns that should only come from the stat file
COORD_COLS = [
    "position_x",
    "position_y",
    "position_z",
    "pos_x",
    "pos_y",
    "pos_z",
    "lat",
    "lon",
    "alt",
    "latitude",
    "longitude",
    "altitude",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_ros_timestamp(value: str) -> float:
    """Convert 'builtin_interfaces.msg.Time(sec=X, nanosec=Y)' to float seconds."""
    if isinstance(value, str):
        m = _ROS_TS_RE.search(value)
        if m:
            return int(m.group(1)) + int(m.group(2)) / 1e9

    if isinstance(value, (int, float, np.number)):
        if value > 1e12:
            return value / 1e9
        return float(value)

    raise ValueError(f"Cannot parse ROS timestamp: {value!r}")


def _get_stamp_column(df: pd.DataFrame) -> str:
    """Return the name of the timestamp column ('stamp', 'time', or 'stamp_seconds')."""
    for name in ("stamp", "time", "stamp_seconds"):
        if name in df.columns:
            return name
    raise KeyError(f"No timestamp column found. Columns: {list(df.columns)}")


def _is_array_column(series: pd.Series) -> bool:
    """Detect columns that contain numpy array strings like '[0. 0. 0.]'."""
    sample = series.dropna().head(1)
    if sample.empty:
        return False
    return str(sample.iloc[0]).startswith("[")


def _load_raw(file_path: Path) -> pd.DataFrame:
    """
    Load a single CSV, parse the timestamp column into float seconds,
    drop unparseable array-string columns, and sort by time.
    """
    df = pd.read_csv(file_path)
    df.columns = [c.strip() for c in df.columns]

    stamp_col = _get_stamp_column(df)

    if stamp_col == "stamp_seconds":
        # Already in float seconds (no parsing needed)
        df["stamp_seconds"] = df[stamp_col].astype(float)
    else:
        df["stamp_seconds"] = df[stamp_col].apply(_parse_ros_timestamp)
        df = df.drop(columns=[stamp_col])

    # Drop columns that contain numpy array strings (e.g. position_covariance)
    array_cols = [c for c in df.columns if _is_array_column(df[c])]
    if array_cols:
        df = df.drop(columns=array_cols)

    df = df.sort_values("stamp_seconds").reset_index(drop=True)
    return df


def _parse_filename(filename: str) -> tuple[str, str]:
    """
    Extract (session_id, topic) from a rosbag2 CSV filename.

    All files - including the stat file - are expected to have the session
    prefix (e.g. rosbag2_YYYY_MM_DD-HH_MM_SS_stat.csv). The stat file is
    identified by the topic name 'stat' and returned as '_stat'.

    Raises:
        ValueError: if the filename does not match the rosbag2 pattern.
    """
    # Temp files downloaded from GCS have prefix 'tmpXXXXXX_' 
    # strip it so the underlying rosbag2 filename can be matched
    canonical = re.sub(r"^tmp[a-z0-9]+_", "", filename)

    m = _FILENAME_RE.match(canonical)
    if not m:
        raise ValueError(
            f"Filename '{filename}' does not match expected pattern "
            "'rosbag2_YYYY_MM_DD-HH_MM_SS_<topic>.csv'"
        )

    topic = m.group(2)
    if topic.lower() == "stat":
        return m.group(1), _STAT_TOPIC
    return m.group(1), topic


def _find_closest_indices(target_timestamps: np.ndarray, reference_timestamps: np.ndarray) -> np.ndarray:
    """
    For each timestamp in target_timestamps, return the index of the closest
    timestamp in reference_timestamps.
    """
    sorted_indices = np.argsort(reference_timestamps)
    sorted_timestamps = reference_timestamps[sorted_indices]

    idx = np.searchsorted(sorted_timestamps, target_timestamps, side="left")
    idx = np.clip(idx, 1, len(sorted_timestamps) - 1)

    left_distances = np.abs(sorted_timestamps[idx - 1] - target_timestamps)
    right_distances = np.abs(sorted_timestamps[idx] - target_timestamps)

    closest = np.where(left_distances <= right_distances, idx - 1, idx)
    return sorted_indices[closest]


def _align_session(topic_dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Align multiple topic DataFrames from the same session onto the stat file timeline.

    Strategy:
    - Use the stat file (_stat) as the base timeline (canonical position/lap reference).
    - For every other topic, snap each base timestamp to the nearest timestamp
      in that topic via nearest-index lookup.
    - No interpolation and no overlap trimming/skipping before alignment.

    Args:
        topic_dfs: dict mapping topic name to DataFrame. Must include '_stat'.
                   Each DataFrame must contain 'stamp_seconds'.

    Returns:
        Single aligned DataFrame on the stat file timeline.
    """
    if not topic_dfs:
        raise ValueError("No topics provided for alignment.")

    # Validate all DataFrames have stamp_seconds
    for topic, df in topic_dfs.items():
        if "stamp_seconds" not in df.columns:
            raise ValueError(f"'stamp_seconds' missing in topic: {topic}")

    if _STAT_TOPIC not in topic_dfs:
        raise ValueError("Stat file (_stat) is required for alignment but was not provided.")

    base_topic = _STAT_TOPIC
    base_df = topic_dfs[base_topic].copy()
    reference_stamps = base_df["stamp_seconds"].to_numpy()

    # Step 3: Detect duplicate columns across topics (excluding timestamp columns)
    all_columns = {}
    for topic, df in topic_dfs.items():
        for col in df.columns:
            col_lower = col.lower()
            if col_lower in ["stamp", "time", "stamp_seconds"]:
                continue
            all_columns[col_lower] = all_columns.get(col_lower, 0) + 1

    duplicate_columns = {col for col, count in all_columns.items() if count > 1}

    # Step 4: Rename base file columns (keep unique names unchanged, suffix only duplicate columns)
    base_new_names = {}
    for col in base_df.columns:
        col_lower = col.lower()
        if col_lower in ["stamp", "time", "stamp_seconds"]:
            base_new_names[col] = col
        elif col_lower in duplicate_columns:
            base_new_names[col] = f"{col}_{base_topic}"
        else:
            base_new_names[col] = col

    base_df.rename(columns=base_new_names, inplace=True)
    aligned_data = base_df.copy()

    # Step 5: Align all other topics to base file
    for topic, df in topic_dfs.items():
        if topic == base_topic:
            continue

        if df.empty:
            continue

        aligned_indices = _find_closest_indices(
            reference_stamps,
            df["stamp_seconds"].to_numpy()
        )

        df_clean = df.drop(
            columns=[c for c in ["stamp", "time", "stamp_seconds"] if c in df.columns]
        ).reset_index(drop=True)

        df_clean = df_clean.iloc[aligned_indices].reset_index(drop=True)

        new_names = {}
        for col in df_clean.columns:
            col_lower = col.lower()
            if col_lower in duplicate_columns:
                new_names[col] = f"{col}_{topic}"
            else:
                new_names[col] = col

        df_clean.rename(columns=new_names, inplace=True)
        aligned_data = aligned_data.join(df_clean)

    return aligned_data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_session(file_paths: list[str]) -> dict[str, pd.DataFrame]:
    """
    Load and align one or more rosbag2 CSV files into per-session DataFrames.

    Files are grouped by session ID parsed from the filename. The stat file (_stat)
    is always used as the base timeline - all other topics snap to it via nearest-index
    lookup. Unique column names are kept as-is; only columns that appear in multiple
    topics are suffixed with the topic name (e.g. 'speed_wheel_speed').

    Args:
        file_paths: List of paths to rosbag2 CSV files including the stat file.

    Returns:
        Dict mapping session_id to an aligned DataFrame containing all topics.

    Raises:
        FileNotFoundError: If any file does not exist.
        ValueError: If filenames don't match expected conventions, or multiple
                    stat files are provided.
    """
    sessions: dict[str, dict[str, pd.DataFrame]] = {}
    stat_count = 0

    for fp in file_paths:
        path = Path(fp)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {fp}")

        session_id, topic = _parse_filename(path.name)
        try:
            df = _load_raw(path)
        except KeyError:
            print(f"Warning: skipping '{path.name}' - no timestamp column found.")
            continue
        
        # Keep coordinate columns only for stat file
        if topic != _STAT_TOPIC:
            df = df.drop(columns=[c for c in COORD_COLS if c in df.columns], errors="ignore")
        else:
            stat_count += 1
            if stat_count > 1:
                raise ValueError("Multiple stat files provided. Upload one *_stat.csv per session.")

        sessions.setdefault(session_id, {})[topic] = df

    return {
        session_id: _align_session(topics)
        for session_id, topics in sessions.items()
    }


def get_schema(file_paths: list[str]) -> dict[str, dict]:
    """
    Return schema information for each session without full alignment.

    Useful for agents to discover what data is available before querying.

    Args:
        file_paths: List of paths to rosbag2 CSV files.

    Returns:
        Dict mapping session_id to a dict with:
            - topics:     list of topic names in this session
            - columns:    all column names across all topics (excluding 'stamp_seconds')
            - time_range: [t_start, t_end] as float Unix timestamps
            - row_counts: dict mapping topic name to number of rows
    """
    # Accumulate metadata one file at a time so we never hold more than a single
    # DataFrame in memory - important for large sessions (many/large topics).
    acc: dict[str, dict] = {}

    for fp in file_paths:
        path = Path(fp)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {fp}")
        session_id, topic = _parse_filename(path.name)
        try:
            df = _load_raw(path)
        except KeyError:
            print(f"Warning: skipping '{path.name}' - no timestamp column found.")
            continue
        if topic != _STAT_TOPIC:
            df = df.drop(columns=[c for c in COORD_COLS if c in df.columns], errors="ignore")

        cols = [c for c in df.columns if c != "stamp_seconds"]
        s = acc.setdefault(session_id, {
            "topics": set(),
            "columns": [],
            "columns_by_topic": {},
            "row_counts": {},
            "t_min": float("inf"),
            "t_max": float("-inf"),
        })
        s["topics"].add(topic)
        s["columns"].extend(cols)
        s["columns_by_topic"][topic] = cols
        s["row_counts"][topic] = len(df)
        s["t_min"] = min(s["t_min"], float(df["stamp_seconds"].min()))
        s["t_max"] = max(s["t_max"], float(df["stamp_seconds"].max()))
        # df is dropped on the next iteration - only metadata is retained

    return {
        session_id: {
            "topics": sorted(s["topics"]),
            "columns": s["columns"],
            "columns_by_topic": s["columns_by_topic"],
            "time_range": [s["t_min"], s["t_max"]],
            "row_counts": s["row_counts"],
        }
        for session_id, s in acc.items()
    }