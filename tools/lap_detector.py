"""
Lap detector: processes the stat file to produce lap boundaries.

The stat file contains position_x/y in ENU (East-North-Up) coordinates.
This module:
  1. Converts ENU -> lat/lon
  2. Computes cumulative distance along the trajectory
  3. Detects lap crossings using the start/finish GPS coordinate
  4. Returns lap boundaries (t_start, t_end per lap) usable by any agent

Lap boundaries are stored as a list of dicts so agents can translate
"lap 3" into a time window and pass it to query functions.
"""

import math
import numpy as np
import pandas as pd
from geopy.distance import geodesic

# ---------------------------------------------------------------------------
# Earth constants for ENU -> LLA conversion
# ---------------------------------------------------------------------------
_A  = 6378137.0          # Earth semi-major axis (m)
_E2 = 6.69437999014e-3   # Earth eccentricity squared
_F  = 1 / 298.257223563  # Earth flattening


# ---------------------------------------------------------------------------
# ENU -> LLA conversion
# ---------------------------------------------------------------------------

def _fix_reference_coord(reference_point: tuple[float, float]) -> tuple[list, list]:
    """Compute ECEF origin and rotation matrix for ENU frame at reference LLA."""
    lat, lon = reference_point
    alt = 0.0     # Assuming altitude is zero (only in 2D)

    c_lat = math.cos(math.radians(lat))
    c_lon = math.cos(math.radians(lon))
    s_lat = math.sin(math.radians(lat))
    s_lon = math.sin(math.radians(lon))

    N = _A / math.sqrt(1.0 - _E2 * s_lat ** 2)

    ecef0 = [
        (alt + N) * c_lat * c_lon,
        (alt + N) * c_lat * s_lon,
        (alt + N * (1 - _E2)) * s_lat,
    ]

    R = [
        -s_lon,          c_lon,          0,
        -s_lat * c_lon, -s_lat * s_lon,  c_lat,
         c_lat * c_lon,  c_lat * s_lon,  s_lat,
    ]

    return ecef0, R


def _enu_to_lla(enu_x: float, enu_y: float, ecef0: list, R: list) -> tuple[float, float]:
    """Convert a single ENU (x, y) point to (lat, lon) in degrees."""
    enu = [enu_x, enu_y, 0.0]     # For 2D, we assume Z is zero 

    ecef_delta = [
        R[0] * enu[0] + R[3] * enu[1] + R[6] * enu[2],
        R[1] * enu[0] + R[4] * enu[1] + R[7] * enu[2],
        R[2] * enu[0] + R[5] * enu[1] + R[8] * enu[2],
    ]

    ecef = [ecef0[i] + ecef_delta[i] for i in range(3)]

    b   = _A * (1 - _F)
    ep2 = (_A ** 2 - b ** 2) / b ** 2
    p   = math.sqrt(ecef[0] ** 2 + ecef[1] ** 2)
    theta = math.atan2(ecef[2] * _A, p * b)

    lon = math.atan2(ecef[1], ecef[0])
    lat = math.atan2(
        ecef[2] + ep2 * b * math.sin(theta) ** 3,
        p - _E2 * _A * math.cos(theta) ** 3,
    )

    return math.degrees(lat), math.degrees(lon)


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------

def _cumulative_distance(
    lats: np.ndarray,
    lons: np.ndarray,
    reference_point: tuple[float, float] | None = None,
) -> list[float]:
    """
    Compute cumulative geodesic distance along a GPS trajectory.

    Distance starts from the reference point if provided, otherwise
    the first GPS point is used as the reference.

    Args:
        lats: array of latitudes
        lons: array of longitudes
        reference_point: optional (lat, lon) to measure distance from

    Returns:
        List of cumulative distances in metres.
    """
    # Make reference point as start point if not explicitly mentioned
    if reference_point is None:
        reference_point = (lats[0], lons[0])
        
    # Distance from reference to first point    
    first_pt = (lats[0], lons[0])
    distances = [geodesic(reference_point, first_pt).meters]

    # Add pair-wise distances
    for i in range(1, len(lats)):
        prev_pt = (lats[i - 1], lons[i - 1])
        curr_pt = (lats[i], lons[i])

        d = geodesic(prev_pt, curr_pt).meters
        distances.append(distances[-1] + d)

    return distances


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_stat_file(
    stat_df: pd.DataFrame,
    start_finish: tuple[float, float],
) -> pd.DataFrame:
    """
    Convert ENU position columns to lat/lon and compute cumulative distance.

    Must be called before detect_laps.

    Args:
        stat_df:      DataFrame loaded from *_stat.csv (must have 'stamp_seconds',
                      'position_x', 'position_y' columns).
        start_finish: (lat, lon) of the start/finish line in decimal degrees.
                      Used as the ENU reference origin.

    Returns:
        stat_df with added columns: 'lat', 'lon', 'cumulative_distance'

    Raises:
        ValueError: If required position columns are missing.
    """
    required = {"position_x", "position_y"}
    missing = required - set(stat_df.columns)
    if missing:
        raise ValueError(f"Stat file missing required columns: {missing}")

    ecef0, R = _fix_reference_coord(start_finish)

    lats, lons = zip(*[
        _enu_to_lla(x, y, ecef0, R)
        for x, y in zip(stat_df["position_x"], stat_df["position_y"])
    ])

    stat_df = stat_df.copy()
    stat_df["lat"] = list(lats)
    stat_df["lon"] = list(lons)
    stat_df["cumulative_distance"] = _cumulative_distance(
        np.array(lats),
        np.array(lons),
        reference_point=None
    )

    return stat_df


def detect_laps(
    stat_df: pd.DataFrame,
    start_finish: tuple[float, float],
    threshold_m: float = 20.0,
    lap_distance_threshold: float = 500.0,
    min_lap_distance: float = 3500.0,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Detect lap boundaries from a processed stat file.

    The car is considered to have completed a lap when it comes within
    threshold_m of the start/finish point AND has travelled at least
    min_lap_distance since the last crossing.

    Args:
        stat_df:               DataFrame with 'stamp_seconds', 'lat', 'lon',
                               'cumulative_distance' columns (output of
                               process_stat_file).
        start_finish:          (lat, lon) of the start/finish line.
        threshold_m:           Distance in metres to consider "at S/F line".
        lap_distance_threshold: If the car is within this distance of the
                               start at the beginning of the session, those
                               rows are marked lap 0 (outlap/warmup).
        min_lap_distance:      Minimum metres to count as a full lap crossing.

    Returns:
        Tuple of:
          - stat_df with a 'lap' column added (0 = outlap, 1+ = race laps)
          - List of lap boundary dicts, one per completed lap:
            [{'lap': 1, 't_start': float, 't_end': float}, ...]

    Raises:
        ValueError: If required columns are missing (call process_stat_file first).
    """
    required = {"stamp_seconds", "lat", "lon", "cumulative_distance"}
    missing = required - set(stat_df.columns)
    if missing:
        raise ValueError(
            f"Missing columns {missing}. Call process_stat_file first."
        )

    stat_df = stat_df.copy()
    coords     = stat_df[["lat", "lon"]].values
    cum_dists  = stat_df["cumulative_distance"].values
    timestamps = stat_df["stamp_seconds"].values
    lap_numbers = np.full(len(stat_df), -1, dtype=int)

    # Find all rows close to the start/finish line
    close_to_start = [
        i for i, (lat, lon) in enumerate(coords)
        if geodesic((lat, lon), start_finish).meters <= threshold_m
    ]

    if not close_to_start:
        # Car never crossed S/F - treat entire session as lap 1
        stat_df["lap"] = 1
        boundaries = [{"lap": 1, "t_start": float(timestamps[0]), "t_end": float(timestamps[-1])}]
        return stat_df, boundaries

    first_close_idx = close_to_start[0]

    # Determine lap start
    if cum_dists[first_close_idx] < lap_distance_threshold:
        # Car started near S/F - rows before first crossing are outlap (lap 0)
        lap_numbers[:first_close_idx] = 0
        lap_num = 1
        lap_start_idx = first_close_idx
        lap_start_dist = cum_dists[first_close_idx]
    else:
        lap_num = 1
        lap_start_idx = 0
        lap_start_dist = cum_dists[0]

    lap_numbers[lap_start_idx] = lap_num

    for i in range(lap_start_idx + 1, len(stat_df)):
        lat, lon = coords[i]
        dist_to_start    = geodesic((lat, lon), start_finish).meters
        current_cum_dist = cum_dists[i]

        if (dist_to_start <= threshold_m and
                (current_cum_dist - lap_start_dist) >= min_lap_distance):
            lap_num += 1
            lap_start_idx  = i
            lap_start_dist = current_cum_dist

        lap_numbers[i] = lap_num

    stat_df["lap"] = lap_numbers

    # Build lap boundaries from the lap column
    boundaries = []
    for lap in sorted(stat_df["lap"].unique()):
        if lap < 1:
            continue  # skip outlap
        lap_rows = stat_df[stat_df["lap"] == lap]
        boundaries.append({
            "lap": int(lap),
            "t_start": float(lap_rows["stamp_seconds"].iloc[0]),
            "t_end":   float(lap_rows["stamp_seconds"].iloc[-1]),
        })

    return stat_df, boundaries


def get_lap_time_windows(boundaries: list[dict]) -> dict[int, tuple[float, float]]:
    """
    Convert lap boundaries list to a dict for quick lap -> (t_start, t_end) lookup.

    Args:
        boundaries: Output of detect_laps (list of lap boundary dicts).

    Returns:
        Dict mapping lap number to (t_start, t_end) tuple.
    """
    return {b["lap"]: (b["t_start"], b["t_end"]) for b in boundaries}