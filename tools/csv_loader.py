"""
CSV loader — single entry point for all telemetry data access.
Agents must never parse CSVs directly; they call these functions instead.

Naming conventions:
  rosbag2 topics:  rosbag2_YYYY_MM_DD-HH_MM_SS_<topic>.csv
  stat file:       <any_name>_stat.csv  (position/ENU data, alignment master)

Timestamp column format:
  ROS2 string:     builtin_interfaces.msg.Time(sec=X, nanosec=Y)  → columns 'stamp' or 'time'
  Already float:   column named 'stamp_seconds'
"""

import re
from pathlib import Path

import pandas as pd

# Regex to parse the ROS2 timestamp string
_ROS_TS_RE = re.compile(r"sec=(\d+),\s*nanosec=(\d+)")

# Regex to parse session ID and topic from rosbag2 filename
_FILENAME_RE = re.compile(r"^(rosbag2_\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2})_(.+)\.csv$")

# Reserved topic name for stat files
_STAT_TOPIC = "_stat"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_ros_timestamp(value: str) -> float:
    """Convert 'builtin_interfaces.msg.Time(sec=X, nanosec=Y)' to float seconds."""
    m = _ROS_TS_RE.search(str(value))
    if not m:
        raise ValueError(f"Cannot parse ROS timestamp: {value!r}")
    return int(m.group(1)) + int(m.group(2)) / 1e9


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
        # Already in float seconds — no parsing needed
        df["t"] = df[stamp_col].astype(float)
    else:
        df["t"] = df[stamp_col].apply(_parse_ros_timestamp)
    df = df.drop(columns=[stamp_col])

    # Drop columns that contain numpy array strings (e.g. position_covariance)
    array_cols = [c for c in df.columns if _is_array_column(df[c])]
    if array_cols:
        df = df.drop(columns=array_cols)

    df = df.sort_values("t").reset_index(drop=True)
    return df


def _parse_filename(filename: str) -> tuple[str | None, str]:
    """
    Extract (session_id, topic) from a CSV filename.

    For rosbag2 files returns (session_id, topic).
    For stat files (*_stat.csv) returns (None, '_stat').

    Raises:
        ValueError: if the filename matches neither pattern.
    """
    if filename.lower().endswith("_stat.csv"):
        return None, _STAT_TOPIC

    # Temp files downloaded from GCS have a prefix like 'tmpXXXXXX_' — strip it
    # so the underlying rosbag2 filename can be matched.
    canonical = re.sub(r"^tmp[a-z0-9]+_", "", filename)

    m = _FILENAME_RE.match(canonical)
    if not m:
        raise ValueError(
            f"Filename '{filename}' does not match expected pattern "
            "'rosbag2_YYYY_MM_DD-HH_MM_SS_<topic>.csv' or '*_stat.csv'"
        )
    return m.group(1), m.group(2)


def _align_session(topic_dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Align multiple topic DataFrames from the same session.

    Strategy:
    - If a stat file ('_stat' topic) is present, use it as the master timeline.
      The stat file is the position/spatial reference for the session.
    - Otherwise fall back to the lowest-frequency topic as master.
    - Merge all other topics onto the master using nearest-timestamp match
      (merge_asof). No interpolation — always a real measured value.
    - Topics with no time overlap with the master are skipped.

    Args:
        topic_dfs: dict mapping topic name to its DataFrame (must have column 't').

    Returns:
        Single aligned DataFrame with column 't' plus all topic columns.
        Duplicate column names across topics are suffixed with the topic name.
    """
    if not topic_dfs:
        raise ValueError("No topics provided for alignment.")

    # Stat file is the preferred master; fall back to lowest-frequency topic
    if _STAT_TOPIC in topic_dfs:
        master_topic = _STAT_TOPIC
    else:
        master_topic = min(topic_dfs, key=lambda t: len(topic_dfs[t]))
    master = topic_dfs[master_topic].copy()

    # Rename non-t columns to avoid collisions
    master = master.rename(columns={c: f"{c}__{master_topic}" for c in master.columns if c != "t"})

    for topic, df in topic_dfs.items():
        if topic == master_topic:
            continue

        other = df.copy().rename(
            columns={c: f"{c}__{topic}" for c in df.columns if c != "t"}
        )

        # Check for time overlap with master — skip topics with no overlap at all.
        # Only trim 'other' to the master window; master is never shrunk so that
        # aligning successive topics doesn't progressively shorten the timeline.
        master_start = float(master["t"].iloc[0])
        master_end = float(master["t"].iloc[-1])
        other_start = float(other["t"].iloc[0])
        other_end = float(other["t"].iloc[-1])

        if other_start > master_end or other_end < master_start:
            # No overlap — skip this topic
            continue

        other = other[
            (other["t"] >= master_start) & (other["t"] <= master_end)
        ].reset_index(drop=True)

        master = pd.merge_asof(
            master,
            other,
            on="t",
            direction="nearest",
        )

    return master


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_session(file_paths: list[str]) -> dict[str, pd.DataFrame]:
    """
    Load and align one or more rosbag2 CSV files into per-session DataFrames.

    Files are grouped by session ID parsed from the filename. The stat file
    (*_stat.csv) is used as the alignment master if provided — all other topics
    snap to its timeline. Without a stat file, the lowest-frequency topic is used.

    The stat file has no session prefix in its filename, so it is added to all
    sessions in the upload. If multiple sessions are present, exactly one stat
    file must be provided per session — raise ValueError otherwise.

    Args:
        file_paths: List of paths to rosbag2 CSV files, optionally including
                    one *_stat.csv file.

    Returns:
        Dict mapping session_id to an aligned DataFrame containing all topics.
        Example keys: 'rosbag2_2025_07_02-10_33_18'

    Raises:
        FileNotFoundError: If any file does not exist.
        ValueError: If filenames don't match expected conventions, or multiple
                    stat files are provided.
    """
    sessions: dict[str, dict[str, pd.DataFrame]] = {}
    stat_df: pd.DataFrame | None = None
    stat_count = 0

    for fp in file_paths:
        path = Path(fp)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {fp}")

        session_id, topic = _parse_filename(path.name)
        df = _load_raw(path)

        if topic == _STAT_TOPIC:
            stat_df = df
            stat_count += 1
            if stat_count > 1:
                raise ValueError("Multiple stat files provided. Upload one *_stat.csv per session.")
        else:
            sessions.setdefault(session_id, {})[topic] = df

    # Add stat file to every session (typically there is only one session per upload)
    if stat_df is not None:
        for session_id in sessions:
            sessions[session_id][_STAT_TOPIC] = stat_df

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
            - columns:    all column names across all topics (excluding 't')
            - time_range: [t_start, t_end] as float Unix timestamps
            - row_counts: dict mapping topic name to number of rows
    """
    sessions: dict[str, dict[str, pd.DataFrame]] = {}
    stat_df: pd.DataFrame | None = None

    for fp in file_paths:
        path = Path(fp)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {fp}")
        session_id, topic = _parse_filename(path.name)
        df = _load_raw(path)
        if topic == _STAT_TOPIC:
            stat_df = df
        else:
            sessions.setdefault(session_id, {})[topic] = df

    if stat_df is not None:
        for session_id in sessions:
            sessions[session_id][_STAT_TOPIC] = stat_df

    result = {}
    for session_id, topics in sessions.items():
        all_cols = []
        t_min, t_max = float("inf"), float("-inf")
        row_counts = {}

        for topic, df in topics.items():
            all_cols.extend([c for c in df.columns if c != "t"])
            t_min = min(t_min, df["t"].min())
            t_max = max(t_max, df["t"].max())
            row_counts[topic] = len(df)

        result[session_id] = {
            "topics": sorted(topics.keys()),
            "columns": all_cols,
            "columns_by_topic": {
                topic: [c for c in df.columns if c != "t"]
                for topic, df in topics.items()
            },
            "time_range": [t_min, t_max],
            "row_counts": row_counts,
        }

    return result
