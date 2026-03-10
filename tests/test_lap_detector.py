"""Tests for tools/lap_detector.py."""

import math

import pandas as pd
import pytest

from tools.lap_detector import detect_laps, get_lap_time_windows, process_stat_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stat_df(lats, lons, cum_dists=None, t_start=0.0):
    """Build a minimal stat DataFrame for testing."""
    n = len(lats)
    if cum_dists is None:
        cum_dists = [float(i * 100) for i in range(n)]
    return pd.DataFrame({
        "stamp_seconds": [t_start + float(i) for i in range(n)],
        "lat": list(lats),
        "lon": list(lons),
        "cumulative_distance": list(cum_dists),
    })


def _circular_session(start_finish, n_laps=2, pts_per_lap=60, step_m=300.0):
    """
    Synthetic stat DataFrame with n_laps complete laps on a circular track.

    The first point of each lap is placed exactly at start_finish so the
    detector reliably recognises the crossing. step_m * pts_per_lap gives
    the lap distance; must exceed min_lap_distance (3500 m).
    """
    rows = []
    cum = 0.0
    R = 0.05  # ~5 km radius in degrees

    for lap in range(n_laps):
        for i in range(pts_per_lap):
            if i == 0:
                lat, lon = start_finish
            else:
                angle = 2 * math.pi * i / pts_per_lap
                lat = start_finish[0] + R * math.sin(angle)
                lon = start_finish[1] + R * math.cos(angle)
            rows.append({
                "stamp_seconds": float(lap * pts_per_lap + i),
                "lat": lat,
                "lon": lon,
                "cumulative_distance": cum,
            })
            cum += step_m

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# process_stat_file
# ---------------------------------------------------------------------------

def test_process_stat_file_adds_columns():
    df = pd.DataFrame({
        "stamp_seconds": [0.0, 1.0, 2.0],
        "position_x": [0.0, 10.0, 20.0],
        "position_y": [0.0, 0.0, 0.0],
    })
    start_finish = (36.586462, -121.756647)
    result = process_stat_file(df, start_finish)

    for col in ("lat", "lon", "cumulative_distance"):
        assert col in result.columns

    assert result["cumulative_distance"].iloc[0] == 0.0
    assert result["cumulative_distance"].iloc[-1] > 0.0
    # cumulative distance is non-decreasing
    assert (result["cumulative_distance"].diff().dropna() >= 0).all()


def test_process_stat_file_lat_lon_near_origin():
    """ENU origin should map back to approximately the reference lat/lon."""
    ref = (36.586462, -121.756647)
    df = pd.DataFrame({
        "stamp_seconds": [0.0],
        "position_x": [0.0],
        "position_y": [0.0],
    })
    result = process_stat_file(df, ref)
    assert abs(result["lat"].iloc[0] - ref[0]) < 0.001
    assert abs(result["lon"].iloc[0] - ref[1]) < 0.001


def test_process_stat_file_missing_columns():
    df = pd.DataFrame({"stamp_seconds": [0.0], "position_x": [0.0]})
    with pytest.raises(ValueError, match="missing required columns"):
        process_stat_file(df, (36.0, -121.0))


# ---------------------------------------------------------------------------
# detect_laps
# ---------------------------------------------------------------------------

def test_detect_laps_no_crossings():
    """Car never comes near S/F — entire session treated as lap 1."""
    start_finish = (36.0, -121.0)
    df = _make_stat_df(
        lats=[36.5, 36.6, 36.7, 36.8],
        lons=[-121.5, -121.5, -121.5, -121.5],
        cum_dists=[0.0, 100.0, 200.0, 300.0],
    )
    result_df, boundaries = detect_laps(df, start_finish)

    assert len(boundaries) == 1
    assert boundaries[0]["lap"] == 1
    assert (result_df["lap"] == 1).all()


def test_detect_laps_two_complete_laps():
    """Two laps on a circular track produce two lap boundaries."""
    start_finish = (36.586462, -121.756647)
    df = _circular_session(start_finish, n_laps=2, pts_per_lap=60, step_m=300.0)

    result_df, boundaries = detect_laps(df, start_finish)

    assert len(boundaries) == 2
    assert boundaries[0]["lap"] == 1
    assert boundaries[1]["lap"] == 2
    for b in boundaries:
        assert b["t_start"] < b["t_end"]


def test_detect_laps_three_complete_laps():
    start_finish = (36.586462, -121.756647)
    df = _circular_session(start_finish, n_laps=3, pts_per_lap=60, step_m=300.0)

    _, boundaries = detect_laps(df, start_finish)

    assert len(boundaries) == 3
    laps = [b["lap"] for b in boundaries]
    assert laps == [1, 2, 3]


def test_detect_laps_outlap():
    """
    Car starts slightly offset from S/F but approaches within 500 m —
    rows before the first S/F crossing become lap 0 (outlap).
    """
    start_finish = (36.0, -121.0)
    rows = []
    cum = 0.0

    # 4 rows slightly away from S/F (each ~157 m from S/F, well outside threshold_m=20)
    for i in range(4):
        rows.append({
            "stamp_seconds": float(i),
            "lat": 36.001,
            "lon": -121.001,
            "cumulative_distance": cum,
        })
        cum += 60.0  # total: 240 m before first crossing

    # Row 4: exactly at S/F, cum = 240 m < lap_distance_threshold (500 m) → outlap branch
    rows.append({"stamp_seconds": 4.0, "lat": 36.0, "lon": -121.0, "cumulative_distance": cum})
    cum += 60.0

    # Build up > 3500 m for the first racing lap
    for i in range(60):
        rows.append({
            "stamp_seconds": 5.0 + i,
            "lat": 36.05,
            "lon": -121.05,
            "cumulative_distance": cum,
        })
        cum += 100.0

    # Return to S/F — cum from lap start ≈ 6000 m > 3500 m → crossing
    rows.append({"stamp_seconds": 65.0, "lat": 36.0, "lon": -121.0, "cumulative_distance": cum})

    df = pd.DataFrame(rows)
    result_df, boundaries = detect_laps(df, start_finish)

    # Rows 0–3 should be labeled lap 0 (outlap)
    outlap_rows = result_df[result_df["lap"] == 0]
    assert len(outlap_rows) == 4

    # At least one racing lap detected
    assert len(boundaries) >= 1
    assert all(b["lap"] >= 1 for b in boundaries)


def test_detect_laps_lap_column_added():
    """detect_laps always adds a 'lap' column to the returned DataFrame."""
    start_finish = (36.0, -121.0)
    df = _make_stat_df([36.5], [-121.5])
    result_df, _ = detect_laps(df, start_finish)
    assert "lap" in result_df.columns


def test_detect_laps_missing_columns():
    df = pd.DataFrame({"stamp_seconds": [0.0], "lat": [36.0], "lon": [-121.0]})  # no cumulative_distance
    with pytest.raises(ValueError, match="Missing columns"):
        detect_laps(df, (36.0, -121.0))


# ---------------------------------------------------------------------------
# get_lap_time_windows
# ---------------------------------------------------------------------------

def test_get_lap_time_windows_basic():
    boundaries = [
        {"lap": 1, "t_start": 100.0, "t_end": 212.5},
        {"lap": 2, "t_start": 212.5, "t_end": 320.0},
        {"lap": 3, "t_start": 320.0, "t_end": 435.0},
    ]
    windows = get_lap_time_windows(boundaries)
    assert windows[1] == (100.0, 212.5)
    assert windows[2] == (212.5, 320.0)
    assert windows[3] == (320.0, 435.0)


def test_get_lap_time_windows_empty():
    assert get_lap_time_windows([]) == {}
