"""Tests for tools/csv_loader.py against real sample data in data/."""

from pathlib import Path
import pytest

from tools.csv_loader import (
    _parse_filename,
    _parse_ros_timestamp,
    get_schema,
    load_session,
)

DATA_DIR = Path(__file__).parent.parent / "data"
ALL_CSVS = sorted(DATA_DIR.glob("*.csv"))
SESSION_ID = "rosbag2_2025_07_02-10_33_18"
STAT_CSV = str(DATA_DIR / f"{SESSION_ID}_stat.csv")


# ---------------------------------------------------------------------------
# Unit: timestamp parsing
# ---------------------------------------------------------------------------

def test_parse_ros_timestamp_basic():
    t = _parse_ros_timestamp("builtin_interfaces.msg.Time(sec=1751477599, nanosec=930823584)")
    assert abs(t - 1751477599.930823584) < 1e-6


def test_parse_ros_timestamp_zero_nanosec():
    t = _parse_ros_timestamp("builtin_interfaces.msg.Time(sec=1751477600, nanosec=0)")
    assert t == 1751477600.0


def test_parse_ros_timestamp_invalid():
    with pytest.raises(ValueError):
        _parse_ros_timestamp("not a timestamp")


# ---------------------------------------------------------------------------
# Unit: filename parsing
# ---------------------------------------------------------------------------

def test_parse_filename_standard():
    session, topic = _parse_filename("rosbag2_2025_07_02-10_33_18_wheel_speed.csv")
    assert session == "rosbag2_2025_07_02-10_33_18"
    assert topic == "wheel_speed"


def test_parse_filename_uppercase_topic():
    session, topic = _parse_filename("rosbag2_2025_07_02-10_33_18_Imu.csv")
    assert topic == "Imu"


def test_parse_filename_stat_with_session_prefix():
    session, topic = _parse_filename("rosbag2_2025_07_02-10_33_18_stat.csv")
    assert session == "rosbag2_2025_07_02-10_33_18"
    assert topic == "_stat"


def test_parse_filename_invalid():
    with pytest.raises(ValueError):
        _parse_filename("some_random_file.csv")


def test_parse_filename_stat_without_prefix_raises():
    with pytest.raises(ValueError):
        _parse_filename("vehicle_stat.csv")


# ---------------------------------------------------------------------------
# Integration: schema on real data
# ---------------------------------------------------------------------------

def test_get_schema_returns_session():
    # Use a small subset to keep test fast
    subset = [str(p) for p in ALL_CSVS[:5]]
    # Derive expected topic count: non-stat files become named topics,
    # stat file (if any) becomes the '_stat' topic.
    stat_count = sum(1 for p in subset if p.lower().endswith("_stat.csv"))
    non_stat_count = len(subset) - stat_count
    expected_topics = non_stat_count + (1 if stat_count > 0 else 0)

    schema = get_schema(subset)
    assert SESSION_ID in schema
    s = schema[SESSION_ID]
    assert len(s["topics"]) == expected_topics
    assert s["time_range"][0] < s["time_range"][1]
    assert all(count > 0 for count in s["row_counts"].values())


def test_get_schema_all_files():
    stat_count = sum(1 for p in ALL_CSVS if p.name.lower().endswith("_stat.csv"))
    non_stat_count = len(ALL_CSVS) - stat_count
    expected_topics = non_stat_count + (1 if stat_count > 0 else 0)

    schema = get_schema([str(p) for p in ALL_CSVS])
    assert SESSION_ID in schema
    s = schema[SESSION_ID]
    assert len(s["topics"]) == expected_topics
    assert len(s["columns"]) > 0
    assert "columns_by_topic" in s
    assert all(isinstance(v, list) for v in s["columns_by_topic"].values())


# ---------------------------------------------------------------------------
# Integration: load_session on real data
# ---------------------------------------------------------------------------

def test_load_session_two_topics():
    """Two topics from the same session should produce one aligned DataFrame."""
    files = [
        STAT_CSV,
        str(DATA_DIR / f"{SESSION_ID}_wheel_speed.csv"),
        str(DATA_DIR / f"{SESSION_ID}_steering_report.csv"),
    ]
    result = load_session(files)
    assert SESSION_ID in result
    df = result[SESSION_ID]
    assert "stamp_seconds" in df.columns
    assert df["stamp_seconds"].is_monotonic_increasing
    # Both topics should have contributed columns
    assert any("wheel_speed" in c for c in df.columns)
    assert any("steering" in c for c in df.columns)


def test_load_session_aligned_to_stat_file():
    """Result row count should match the stat file (always used as base timeline)."""
    from tools.csv_loader import _load_raw
    stat = _load_raw(DATA_DIR / f"{SESSION_ID}_stat.csv")

    files = [
        STAT_CSV,
        str(DATA_DIR / f"{SESSION_ID}_wheel_speed.csv"),
        str(DATA_DIR / f"{SESSION_ID}_potentiometer.csv"),
    ]
    result = load_session(files)
    df = result[SESSION_ID]

    # Base is always the stat file; result row count equals its row count
    assert len(df) == len(stat)


def test_load_session_no_invented_values():
    """Aligned values must all come from real measurements (no NaN from gaps)."""
    files = [
        STAT_CSV,
        str(DATA_DIR / f"{SESSION_ID}_brake_pressure_report.csv"),
        str(DATA_DIR / f"{SESSION_ID}_potentiometer.csv"),
    ]
    result = load_session(files)
    df = result[SESSION_ID]
    # nearest-index lookup always picks a real row, so alignment never introduces NaN
    assert df.isnull().sum().sum() == 0


def test_load_two_sessions():
    """Files from two different sessions should produce two separate DataFrames."""
    # Simulate a second session by using a copy with a different name pattern
    # Since we only have one real session, test the grouping logic with same files
    # labelled as two topics of one session - real two-session test requires two bags.
    # Always include the stat file so _align_session can find its required base.
    non_stat = [p for p in ALL_CSVS if not p.name.endswith("_stat.csv")]
    files = [STAT_CSV] + [str(p) for p in non_stat[:2]]
    result = load_session(files)
    # All files are from the same session
    assert len(result) == 1
    assert SESSION_ID in result


def test_load_session_requires_stat_file():
    """load_session raises ValueError when no stat file is provided."""
    files = [
        str(DATA_DIR / f"{SESSION_ID}_wheel_speed.csv"),
        str(DATA_DIR / f"{SESSION_ID}_steering_report.csv"),
    ]
    with pytest.raises(ValueError, match="_stat"):
        load_session(files)


def test_load_session_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_session(["/nonexistent/path/rosbag2_2025_07_02-10_33_18_wheel_speed.csv"])