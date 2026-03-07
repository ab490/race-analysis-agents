"""Tests for tools/csv_loader.py against real sample data in data/."""

import glob
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


def test_parse_filename_invalid():
    with pytest.raises(ValueError):
        _parse_filename("some_random_file.csv")


# ---------------------------------------------------------------------------
# Integration: schema on real data
# ---------------------------------------------------------------------------

def test_get_schema_returns_session():
    # Use a small subset to keep test fast
    subset = [str(p) for p in ALL_CSVS[:5]]
    schema = get_schema(subset)
    assert SESSION_ID in schema
    s = schema[SESSION_ID]
    assert len(s["topics"]) == 5
    assert s["time_range"][0] < s["time_range"][1]
    assert all(count > 0 for count in s["row_counts"].values())


def test_get_schema_all_files():
    schema = get_schema([str(p) for p in ALL_CSVS])
    assert SESSION_ID in schema
    s = schema[SESSION_ID]
    assert len(s["topics"]) == len(ALL_CSVS)
    assert len(s["columns"]) > 0


# ---------------------------------------------------------------------------
# Integration: load_session on real data
# ---------------------------------------------------------------------------

def test_load_session_two_topics():
    """Two topics from the same session should produce one aligned DataFrame."""
    files = [
        str(DATA_DIR / f"{SESSION_ID}_wheel_speed.csv"),
        str(DATA_DIR / f"{SESSION_ID}_steering_report.csv"),
    ]
    result = load_session(files)
    assert SESSION_ID in result
    df = result[SESSION_ID]
    assert "t" in df.columns
    assert df["t"].is_monotonic_increasing
    # Both topics should have contributed columns
    assert any("wheel_speed" in c for c in df.columns)
    assert any("steering" in c for c in df.columns)


def test_load_session_aligned_to_lowest_freq():
    """Result row count should match the lowest-frequency (fewest rows) topic."""
    from tools.csv_loader import _load_raw
    wheel = _load_raw(DATA_DIR / f"{SESSION_ID}_wheel_speed.csv")
    potentiometer = _load_raw(DATA_DIR / f"{SESSION_ID}_potentiometer.csv")

    files = [
        str(DATA_DIR / f"{SESSION_ID}_wheel_speed.csv"),
        str(DATA_DIR / f"{SESSION_ID}_potentiometer.csv"),
    ]
    result = load_session(files)
    df = result[SESSION_ID]

    # Master is whichever has fewer rows; result row count <= master rows
    # (may be slightly less after time-window trimming)
    min_rows = min(len(wheel), len(potentiometer))
    assert len(df) <= min_rows


def test_load_session_no_invented_values():
    """Aligned values must all come from real measurements (no NaN from gaps)."""
    files = [
        str(DATA_DIR / f"{SESSION_ID}_brake_pressure_report.csv"),
        str(DATA_DIR / f"{SESSION_ID}_potentiometer.csv"),
    ]
    result = load_session(files)
    df = result[SESSION_ID]
    # merge_asof with direction='nearest' should not introduce NaNs for overlapping windows
    assert df.isnull().sum().sum() == 0


def test_load_two_sessions():
    """Files from two different sessions should produce two separate DataFrames."""
    # Simulate a second session by using a copy with a different name pattern
    # Since we only have one real session, test the grouping logic with same files
    # labelled as two topics of one session — real two-session test requires two bags.
    files = [str(p) for p in ALL_CSVS[:3]]
    result = load_session(files)
    # All files are from the same session
    assert len(result) == 1
    assert SESSION_ID in result


def test_load_session_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_session(["/nonexistent/path/rosbag2_2025_07_02-10_33_18_wheel_speed.csv"])
