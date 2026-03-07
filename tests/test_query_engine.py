"""Tests for tools/query_engine.py."""

from pathlib import Path

import pandas as pd
import pytest

from tools.csv_loader import _load_raw
from tools.query_engine import (
    find_threshold_events,
    get_column_stats,
    get_column_stats_for_zone,
    get_time_series,
    get_zone_time_windows,
    query_cross_topic,
)

DATA_DIR = Path(__file__).parent.parent / "data"
SESSION_ID = "rosbag2_2025_07_02-10_33_18"
WHEEL_CSV = str(DATA_DIR / f"{SESSION_ID}_wheel_speed.csv")
CONTROL_CSV = str(DATA_DIR / f"{SESSION_ID}_ControlStatus.csv")
BRAKE_CSV = str(DATA_DIR / f"{SESSION_ID}_brake_pressure_report.csv")


def _first_numeric_col(file_path: str) -> str:
    """Return the first non-timestamp column from a CSV file."""
    df = _load_raw(Path(file_path))
    return [c for c in df.columns if c != "t"][0]


# ---------------------------------------------------------------------------
# get_column_stats
# ---------------------------------------------------------------------------

def test_get_column_stats_returns_all_keys():
    col = _first_numeric_col(WHEEL_CSV)
    result = get_column_stats(WHEEL_CSV, col)
    for key in ("column", "count", "min", "max", "mean", "std", "p25", "p50", "p75", "p95"):
        assert key in result


def test_get_column_stats_values_are_consistent():
    col = _first_numeric_col(WHEEL_CSV)
    result = get_column_stats(WHEEL_CSV, col)
    assert result["min"] <= result["p25"] <= result["p50"] <= result["p75"] <= result["p95"] <= result["max"]
    assert result["count"] > 0


def test_get_column_stats_column_not_found():
    with pytest.raises(KeyError, match="nonexistent_column"):
        get_column_stats(WHEEL_CSV, "nonexistent_column")


def test_get_column_stats_time_filter_reduces_count():
    col = _first_numeric_col(WHEEL_CSV)
    df = _load_raw(Path(WHEEL_CSV))
    t_min = float(df["t"].min())
    t_max = float(df["t"].max())
    t_mid = (t_min + t_max) / 2.0

    full = get_column_stats(WHEEL_CSV, col)
    partial = get_column_stats(WHEEL_CSV, col, t_start=t_min, t_end=t_mid)

    assert partial["count"] < full["count"]
    assert partial["count"] > 0


# ---------------------------------------------------------------------------
# get_time_series
# ---------------------------------------------------------------------------

def test_get_time_series_returns_aligned_lists():
    df = _load_raw(Path(WHEEL_CSV))
    cols = [c for c in df.columns if c != "t"][:2]
    result = get_time_series(WHEEL_CSV, cols)

    assert "t" in result
    assert "data" in result
    assert "total_rows" in result
    assert "returned_rows" in result
    for col in cols:
        assert len(result["data"][col]) == len(result["t"])


def test_get_time_series_respects_max_points():
    col = _first_numeric_col(WHEEL_CSV)
    result = get_time_series(WHEEL_CSV, [col], max_points=50)
    assert result["returned_rows"] <= 50
    assert len(result["t"]) == result["returned_rows"]


def test_get_time_series_column_not_found():
    with pytest.raises(KeyError):
        get_time_series(WHEEL_CSV, ["missing_col"])


# ---------------------------------------------------------------------------
# find_threshold_events
# ---------------------------------------------------------------------------

def test_find_threshold_events_returns_all_above_min():
    """Threshold = min value → every non-null row should match '>='."""
    col = _first_numeric_col(WHEEL_CSV)
    df = _load_raw(Path(WHEEL_CSV))
    min_val = float(df[col].min())

    result = find_threshold_events(WHEEL_CSV, col, ">=", min_val)

    assert result["count"] == int(df[col].dropna().count())
    assert result["first_event_t"] is not None
    assert result["first_event_t"] <= result["last_event_t"]


def test_find_threshold_events_impossible_threshold():
    """Threshold above max → no events."""
    col = _first_numeric_col(WHEEL_CSV)
    result = find_threshold_events(WHEEL_CSV, col, ">", 1e18)

    assert result["count"] == 0
    assert result["first_event_t"] is None
    assert result["last_event_t"] is None


def test_find_threshold_events_invalid_operator():
    with pytest.raises(ValueError, match="operator"):
        find_threshold_events(WHEEL_CSV, _first_numeric_col(WHEEL_CSV), "!=", 0.0)


def test_find_threshold_events_events_capped_at_200():
    """events list is always <= 200 entries regardless of match count."""
    col = _first_numeric_col(WHEEL_CSV)
    df = _load_raw(Path(WHEEL_CSV))
    min_val = float(df[col].min())

    result = find_threshold_events(WHEEL_CSV, col, ">=", min_val)
    assert len(result["events"]) <= 200


# ---------------------------------------------------------------------------
# Zone-based queries (synthetic enriched stat file)
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_stat_csv(tmp_path):
    """Minimal enriched stat CSV with zone and lap columns."""
    df = pd.DataFrame({
        "stamp_seconds": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
        "lat": [36.0] * 10,
        "lon": [-121.0] * 10,
        "zone": ["s1", "s1", "s2", "s2", "s1", "s1", "s2", "s2", "s1", "s1"],
        "lap":  [  1,    1,    1,    1,    1,    2,    2,    2,    2,    2],
    })
    p = tmp_path / "test_stat.csv"
    df.to_csv(p, index=False)
    return str(p)


@pytest.fixture
def synthetic_data_csv(tmp_path):
    """Minimal data CSV aligned to the stat timestamps."""
    df = pd.DataFrame({
        "stamp_seconds": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
        # s1 rows (indices 0,1,4,5,8,9) have high speed; s2 (2,3,6,7) have low speed
        "speed": [50.0, 51.0, 20.0, 21.0, 52.0, 50.0, 22.0, 21.0, 51.0, 50.0],
    })
    p = tmp_path / "test_data.csv"
    df.to_csv(p, index=False)
    return str(p)


def test_get_zone_time_windows_returns_windows(synthetic_stat_csv):
    result = get_zone_time_windows(synthetic_stat_csv, "s1")
    assert result["zone"] == "s1"
    assert len(result["windows"]) > 0
    assert "s1" in result["available_zones"]
    assert "s2" in result["available_zones"]
    for w in result["windows"]:
        assert w["t_start"] <= w["t_end"]
        assert w["lap"] in (1, 2)


def test_get_zone_time_windows_unknown_zone(synthetic_stat_csv):
    result = get_zone_time_windows(synthetic_stat_csv, "s99")
    assert "error" in result
    assert "available_zones" in result
    assert "s1" in result["available_zones"]


def test_get_zone_time_windows_no_zone_column(tmp_path):
    df = pd.DataFrame({"stamp_seconds": [0.0, 1.0], "lat": [36.0, 36.0], "lon": [-121.0, -121.0]})
    p = tmp_path / "no_zone.csv"
    df.to_csv(p, index=False)
    result = get_zone_time_windows(str(p), "s1")
    assert "error" in result


def test_get_column_stats_for_zone_returns_stats(synthetic_stat_csv, synthetic_data_csv):
    result = get_column_stats_for_zone(synthetic_data_csv, synthetic_stat_csv, "s1", "speed")
    assert "overall" in result
    assert "per_lap" in result
    assert result["zone"] == "s1"
    assert result["column"] == "speed"
    assert result["overall"]["count"] > 0
    assert result["overall"]["min"] <= result["overall"]["mean"] <= result["overall"]["max"]


def test_get_column_stats_for_zone_values_differ_by_zone(synthetic_stat_csv, synthetic_data_csv):
    """s1 has higher speed (~50) than s2 (~21) in our synthetic data."""
    s1 = get_column_stats_for_zone(synthetic_data_csv, synthetic_stat_csv, "s1", "speed")
    s2 = get_column_stats_for_zone(synthetic_data_csv, synthetic_stat_csv, "s2", "speed")
    assert s1["overall"]["mean"] > s2["overall"]["mean"]


def test_get_column_stats_for_zone_per_lap_breakdown(synthetic_stat_csv, synthetic_data_csv):
    result = get_column_stats_for_zone(synthetic_data_csv, synthetic_stat_csv, "s1", "speed")
    lap_numbers = [entry["lap"] for entry in result["per_lap"]]
    assert 1 in lap_numbers
    assert 2 in lap_numbers


# ---------------------------------------------------------------------------
# query_cross_topic
# ---------------------------------------------------------------------------

def test_query_cross_topic_aligns_two_files():
    result = query_cross_topic([WHEEL_CSV, BRAKE_CSV], columns=[])
    assert "t" in result
    assert "data" in result
    assert "available_columns" in result
    assert result["returned_rows"] > 0
    assert len(result["t"]) == result["returned_rows"]


def test_query_cross_topic_too_many_files():
    with pytest.raises(ValueError, match="at most 5"):
        query_cross_topic([WHEEL_CSV] * 6, columns=[])
