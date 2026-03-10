"""Tests for the pure-Python tools in agents/insights_agent/agent.py."""

from pathlib import Path

import pandas as pd
import pytest

from agents.qa_agent.agent import (
    detect_anomalies,
    stint_trend as get_stint_trend,
    summarise_lap_times,
)
from tools.csv_loader import _load_raw

DATA_DIR = Path(__file__).parent.parent / "data"
SESSION_ID = "rosbag2_2025_07_02-10_33_18"
WHEEL_CSV = str(DATA_DIR / f"{SESSION_ID}_wheel_speed.csv")


# ---------------------------------------------------------------------------
# summarise_lap_times
# ---------------------------------------------------------------------------

def test_summarise_lap_times_basic():
    boundaries = [
        {"lap": 0, "t_start": 0.0,   "t_end": 50.0},   # outlap - excluded
        {"lap": 1, "t_start": 50.0,  "t_end": 162.5},
        {"lap": 2, "t_start": 162.5, "t_end": 271.0},
        {"lap": 3, "t_start": 271.0, "t_end": 385.5},
    ]
    result = summarise_lap_times(boundaries)

    assert result["lap_count"] == 3
    assert result["fastest_lap"] in (1, 2, 3)
    assert result["fastest_time_s"] <= result["mean_time_s"] <= result["slowest_time_s"]


def test_summarise_lap_times_delta_to_best_is_zero_for_fastest():
    boundaries = [
        {"lap": 1, "t_start": 0.0,   "t_end": 100.0},
        {"lap": 2, "t_start": 100.0, "t_end": 195.0},  # fastest: 95 s
        {"lap": 3, "t_start": 195.0, "t_end": 300.0},
    ]
    result = summarise_lap_times(boundaries)

    fastest_entry = next(d for d in result["laps"] if d["lap"] == result["fastest_lap"])
    assert fastest_entry["delta_to_best_s"] == 0.0

    for d in result["laps"]:
        assert d["delta_to_best_s"] >= 0.0


def test_summarise_lap_times_identifies_fastest_correctly():
    boundaries = [
        {"lap": 1, "t_start": 0.0,   "t_end": 120.0},   # 120 s
        {"lap": 2, "t_start": 120.0, "t_end": 230.0},    # 110 s - fastest
        {"lap": 3, "t_start": 230.0, "t_end": 355.0},    # 125 s
    ]
    result = summarise_lap_times(boundaries)

    assert result["fastest_lap"] == 2
    assert result["fastest_time_s"] == pytest.approx(110.0)
    assert result["slowest_lap"] == 3


def test_summarise_lap_times_single_lap():
    boundaries = [{"lap": 1, "t_start": 100.0, "t_end": 215.5}]
    result = summarise_lap_times(boundaries)

    assert result["lap_count"] == 1
    assert result["fastest_lap"] == result["slowest_lap"] == 1
    assert result["laps"][0]["delta_to_best_s"] == 0.0


def test_summarise_lap_times_only_outlap():
    """If only lap 0 exists, return an error dict."""
    boundaries = [{"lap": 0, "t_start": 0.0, "t_end": 60.0}]
    result = summarise_lap_times(boundaries)
    assert "error" in result


def test_summarise_lap_times_empty():
    result = summarise_lap_times([])
    assert "error" in result


# ---------------------------------------------------------------------------
# get_stint_trend
# ---------------------------------------------------------------------------

def test_get_stint_trend_returns_trend():
    df = _load_raw(Path(WHEEL_CSV))
    t_min = float(df["stamp_seconds"].min())
    t_max = float(df["stamp_seconds"].max())
    t_mid = (t_min + t_max) / 2.0
    col = [c for c in df.columns if c != "stamp_seconds"][0]

    boundaries = [
        {"lap": 1, "t_start": t_min, "t_end": t_mid},
        {"lap": 2, "t_start": t_mid, "t_end": t_max},
    ]
    result = get_stint_trend(WHEEL_CSV, col, boundaries, stat="max")

    assert "trend" in result
    assert len(result["trend"]) == 2
    assert result["trend"][0]["lap"] == 1
    assert result["trend"][1]["lap"] == 2
    for entry in result["trend"]:
        assert "lap" in entry
        assert "value" in entry


def test_get_stint_trend_stat_choices():
    df = _load_raw(Path(WHEEL_CSV))
    t_min, t_max = float(df["stamp_seconds"].min()), float(df["stamp_seconds"].max())
    t_mid = (t_min + t_max) / 2.0
    col = [c for c in df.columns if c != "stamp_seconds"][0]
    boundaries = [
        {"lap": 1, "t_start": t_min, "t_end": t_mid},
        {"lap": 2, "t_start": t_mid, "t_end": t_max},
    ]

    for stat in ("max", "min", "mean"):
        result = get_stint_trend(WHEEL_CSV, col, boundaries, stat=stat)
        assert "trend" in result, f"stat={stat} should return trend"


def test_get_stint_trend_column_not_found():
    df = _load_raw(Path(WHEEL_CSV))
    t_min, t_max = float(df["stamp_seconds"].min()), float(df["stamp_seconds"].max())
    boundaries = [{"lap": 1, "t_start": t_min, "t_end": t_max}]

    result = get_stint_trend(WHEEL_CSV, "nonexistent_col", boundaries)
    assert "error" in result
    assert "available_columns" in result


def test_get_stint_trend_no_racing_laps():
    df = _load_raw(Path(WHEEL_CSV))
    t_min, t_max = float(df["stamp_seconds"].min()), float(df["stamp_seconds"].max())
    col = [c for c in df.columns if c != "stamp_seconds"][0]
    boundaries = [{"lap": 0, "t_start": t_min, "t_end": t_max}]

    result = get_stint_trend(WHEEL_CSV, col, boundaries)
    assert "error" in result


def test_get_stint_trend_invalid_stat():
    df = _load_raw(Path(WHEEL_CSV))
    col = [c for c in df.columns if c != "stamp_seconds"][0]
    boundaries = [{"lap": 1, "t_start": 0.0, "t_end": 1e15}]

    result = get_stint_trend(WHEEL_CSV, col, boundaries, stat="median")
    assert "error" in result


# ---------------------------------------------------------------------------
# detect_anomalies
# ---------------------------------------------------------------------------

def test_detect_anomalies_constant_signal(tmp_path):
    """A constant-value signal has zero variance - no anomalies."""
    df = pd.DataFrame({"stamp_seconds": [0.0, 1.0, 2.0], "value": [5.0, 5.0, 5.0]})
    p = tmp_path / "const.csv"
    df.to_csv(p, index=False)

    result = detect_anomalies(str(p), "value")
    assert result["total_anomaly_count"] == 0
    assert "note" in result


def test_detect_anomalies_finds_spike(tmp_path):
    """A large outlier should be flagged."""
    values = [10.0] * 99 + [1000.0]  # one obvious spike
    df = pd.DataFrame({"stamp_seconds": list(range(100)), "value": values})
    p = tmp_path / "spike.csv"
    df.to_csv(p, index=False)

    result = detect_anomalies(str(p), "value", n_sigma=2.0)
    assert result["anomalies_high"]["count"] >= 1
    assert result["total_anomaly_count"] >= 1


def test_detect_anomalies_column_not_found(tmp_path):
    df = pd.DataFrame({"stamp_seconds": [0.0], "value": [1.0]})
    p = tmp_path / "data.csv"
    df.to_csv(p, index=False)

    result = detect_anomalies(str(p), "missing_col")
    assert "error" in result


def test_detect_anomalies_result_keys(tmp_path):
    """Well-formed result should have all expected keys."""
    import numpy as np
    rng = np.random.default_rng(42)
    values = rng.normal(50.0, 5.0, 200).tolist()
    df = pd.DataFrame({"stamp_seconds": list(range(200)), "value": values})
    p = tmp_path / "normal.csv"
    df.to_csv(p, index=False)

    result = detect_anomalies(str(p), "value")
    for key in ("column", "mean", "std", "threshold_high", "threshold_low",
                "n_sigma", "anomalies_high", "anomalies_low", "total_anomaly_count"):
        assert key in result

    assert result["threshold_high"] > result["threshold_low"]
