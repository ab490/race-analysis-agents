"""
Plot generator: server-side Plotly figure construction.

These functions load data from CSV files and return ready-to-use Plotly figure
dicts (matching the output of fig.to_dict()). The plot_agent calls these as
tools so the LLM never has to output raw data arrays - it only specifies
what to plot and the tool returns the complete figure.

All functions cap data at max_points (default 150) to keep response size
manageable. Time axis is normalised to seconds-from-start-of-window.
"""

from pathlib import Path
import pandas as pd

from tools.csv_loader import _load_raw


_DARK = "plotly_dark"

# Vertical spike line + unified tooltip shown when hovering over time-series charts
_SPIKE_X = {
    "showspikes": True,
    "spikecolor": "rgba(180,180,180,0.45)",
    "spikethickness": 1,
    "spikemode": "across",
    "spikesnap": "cursor",
}

_HOVER_LABEL = {
    "bgcolor": "black",
    "bordercolor": "rgba(180,180,180,0.45)",
    "font": {"color": "white"},
    "namelength": -1,
}


def _load_and_filter(file_path: str, t_start: float | None, t_end: float | None, max_points: int):
    """Load a raw CSV, apply time filter, and downsample."""
    df = _load_raw(Path(file_path))
    if t_start is not None:
        df = df[df["stamp_seconds"] >= t_start]
    if t_end is not None:
        df = df[df["stamp_seconds"] <= t_end]
    if len(df) > max_points:
        step = max(1, len(df) // max_points)
        df = df.iloc[::step].head(max_points)
    return df


def make_time_series(
    file_path: str,
    columns: list[str],
    title: str,
    y_label: str,
    t_start: float | None = None,
    t_end: float | None = None,
    y_scale: float = 1.0,
    max_points: int = 150,
) -> dict:
    """
    Generate a Plotly time series figure for one or more columns.

    Use this for any "plot X over time" request.

    Args:
        file_path:  Path to the topic CSV.
        columns:    Column name(s) to plot.
        title:      Chart title.
        y_label:    Y-axis label (include units).
        t_start:    Optional start time filter.
        t_end:      Optional end time filter.
        y_scale:    Multiply all y values by this factor (e.g. 1/9.80665 for g).
        max_points: Max data points per trace (default 150).

    Returns:
        Plotly figure dict with data and layout.
    """
    df = _load_and_filter(file_path, t_start, t_end, max_points)

    missing = [c for c in columns if c not in df.columns]
    if missing:
        available = [c for c in df.columns if c != "stamp_seconds"]
        return {"error": f"Columns not found: {missing}. Available: {available}"}

    t0 = float(df["stamp_seconds"].iloc[0]) if len(df) else 0
    t_rel = (df["stamp_seconds"] - t0).round(3).tolist()

    traces = [
        {
            "type": "scatter",
            "x": t_rel,
            "y": (df[col] * y_scale).round(4).tolist(),
            "mode": "lines",
            "name": col,
        }
        for col in columns
    ]

    return {
        "data": traces,
        "layout": {
            "title": {"text": title},
            "xaxis": {"title": {"text": "Time (s)"}, **_SPIKE_X},
            "yaxis": {"title": {"text": y_label}},
            "hovermode": "x unified",
            "hoverlabel": _HOVER_LABEL,
            "template": _DARK,
        },
    }


def make_multi_lap_overlay(
    file_path: str,
    column: str,
    lap_windows: list[dict],
    title: str,
    y_label: str,
    y_scale: float = 1.0,
    max_points: int = 150,
) -> dict:
    """
    Overlay the same signal across multiple laps on one chart.

    Use this for lap comparison requests like "compare brake pressure in lap 1 vs lap 3".

    Args:
        file_path:   Path to the topic CSV.
        column:      Column to compare.
        lap_windows: List of {lap, t_start, t_end} dicts (from resolve_lap_window or lap_boundaries).
        title:       Chart title.
        y_label:     Y-axis label with units.
        y_scale:     Multiply y values by this factor.
        max_points:  Max points per lap trace.

    Returns:
        Plotly figure dict.
    """
    df_full = _load_raw(Path(file_path))
    if column not in df_full.columns:
        available = [c for c in df_full.columns if c != "stamp_seconds"]
        return {"error": f"Column '{column}' not found. Available: {available}"}

    traces = []
    for w in lap_windows:
        lap_df = df_full[(df_full["stamp_seconds"] >= w["t_start"]) & (df_full["stamp_seconds"] <= w["t_end"])]
        if len(lap_df) > max_points:
            step = max(1, len(lap_df) // max_points)
            lap_df = lap_df.iloc[::step].head(max_points)
        if lap_df.empty:
            continue
        t0 = float(lap_df["stamp_seconds"].iloc[0])
        traces.append({
            "type": "scatter",
            "x": (lap_df["stamp_seconds"] - t0).round(3).tolist(),
            "y": (lap_df[column] * y_scale).round(4).tolist(),
            "mode": "lines",
            "name": f"Lap {w['lap']}",
        })

    return {
        "data": traces,
        "layout": {
            "title": {"text": title},
            "xaxis": {"title": {"text": "Lap time (s)"}, **_SPIKE_X},
            "yaxis": {"title": {"text": y_label}},
            "hovermode": "x unified",
            "hoverlabel": _HOVER_LABEL,
            "template": _DARK,
        },
    }


def make_track_map(
    stat_file_path: str,
    color_column: str | None = None,
    color_label: str = "",
    t_start: float | None = None,
    t_end: float | None = None,
    max_points: int = 150,
) -> dict:
    """
    Generate a track map from the stat file (lat/lon).

    Optionally colour the trace by a signal value (e.g. speed heatmap on track).
    The stat file must have lat and lon columns (added during upload processing).

    Args:
        stat_file_path: Path to the enriched *_stat.csv file.
        color_column:   Optional column to use as colour. None = plain line.
        color_label:    Label for the colour scale.
        t_start:        Optional time filter.
        t_end:          Optional time filter.
        max_points:     Max data points.

    Returns:
        Plotly figure dict.
    """
    df = _load_and_filter(stat_file_path, t_start, t_end, max_points)

    for col in ("lat", "lon"):
        if col not in df.columns:
            return {"error": f"'{col}' column not found in stat file. Was it enriched during upload?"}

    trace = {
        "type": "scatter",
        "x": df["lon"].round(6).tolist(),
        "y": df["lat"].round(6).tolist(),
        "mode": "lines+markers",
        "marker": {"size": 4},
        "name": "Track",
    }

    if color_column and color_column in df.columns:
        trace["mode"] = "markers"
        trace["marker"] = {
            "size": 5,
            "color": df[color_column].round(4).tolist(),
            "colorscale": "RdYlGn",
            "showscale": True,
            "colorbar": {"title": color_label or color_column},
        }
        trace["name"] = color_label or color_column

    return {
        "data": [trace],
        "layout": {
            "title": {"text": "Track Map"},
            "xaxis": {"title": {"text": "Longitude"}, "scaleanchor": "y"},
            "yaxis": {"title": {"text": "Latitude"}},
            "template": _DARK,
        },
    }


def make_gg_diagram(
    imu_file_path: str,
    lat_accel_col: str = "linear_acceleration_y",
    lon_accel_col: str = "linear_acceleration_x",
    t_start: float | None = None,
    t_end: float | None = None,
    max_points: int = 150,
) -> dict:
    """
    Generate a GG diagram (lateral vs longitudinal acceleration).

    Args:
        imu_file_path:  Path to the IMU CSV file.
        lat_accel_col:  Column for lateral acceleration (m/s²).
        lon_accel_col:  Column for longitudinal acceleration (m/s²).
        t_start:        Optional time filter.
        t_end:          Optional time filter.
        max_points:     Max data points.

    Returns:
        Plotly figure dict with acceleration in g.
    """
    df = _load_and_filter(imu_file_path, t_start, t_end, max_points)

    for col in (lat_accel_col, lon_accel_col):
        if col not in df.columns:
            available = [c for c in df.columns if c != "stamp_seconds"]
            return {"error": f"Column '{col}' not found. Available: {available}"}

    g = 9.80665
    return {
        "data": [{
            "type": "scatter",
            "x": (df[lat_accel_col] / g).round(4).tolist(),
            "y": (df[lon_accel_col] / g).round(4).tolist(),
            "mode": "markers",
            "marker": {"size": 4, "opacity": 0.7},
            "name": "GG",
        }],
        "layout": {
            "title": {"text": "GG Diagram"},
            "xaxis": {"title": {"text": "Lateral Acceleration (g)"}, "zeroline": True},
            "yaxis": {"title": {"text": "Longitudinal Acceleration (g)"}, "zeroline": True},
            "template": _DARK,
        },
    }


def make_xy_plot(
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
    max_points: int = 300,
) -> dict:
    """
    Generate a scatter/line plot of any column vs any other column, optionally
    from two different topic files. The two files are aligned by timestamp.

    Use for requests like:
    - "speed vs distance"
    - "steering angle vs speed"
    - "brake pressure vs lateral G"

    Args:
        x_file_path: Path to the CSV containing the X column.
        x_column:    Column name for the X axis.
        y_file_path: Path to the CSV containing the Y column (can be same file).
        y_column:    Column name for the Y axis.
        title:       Chart title.
        x_label:     X-axis label with units.
        y_label:     Y-axis label with units.
        t_start:     Optional start time filter.
        t_end:       Optional end time filter.
        x_scale:     Multiply X values by this factor.
        y_scale:     Multiply Y values by this factor.
        max_points:  Max data points (default 300).

    Returns:
        Plotly figure dict or {"error": "..."}.
    """
    x_df = _load_and_filter(x_file_path, t_start, t_end, max_points)
    if x_column not in x_df.columns:
        available = [c for c in x_df.columns if c != "stamp_seconds"]
        return {"error": f"Column '{x_column}' not found in x file. Available: {available}"}

    if x_file_path == y_file_path:
        merged = x_df
    else:
        y_df = _load_and_filter(y_file_path, t_start, t_end, max_points * 4)
        if y_column not in y_df.columns:
            available = [c for c in y_df.columns if c != "stamp_seconds"]
            return {"error": f"Column '{y_column}' not found in y file. Available: {available}"}
        merged = pd.merge_asof(
            x_df[["stamp_seconds", x_column]].sort_values("stamp_seconds"),
            y_df[["stamp_seconds", y_column]].sort_values("stamp_seconds"),
            on="stamp_seconds",
            direction="nearest",
        )
        if len(merged) > max_points:
            step = max(1, len(merged) // max_points)
            merged = merged.iloc[::step].head(max_points)

    if y_column not in merged.columns:
        return {"error": f"Column '{y_column}' not available after alignment."}

    return {
        "data": [{
            "type": "scatter",
            "x": (merged[x_column] * x_scale).round(4).tolist(),
            "y": (merged[y_column] * y_scale).round(4).tolist(),
            "mode": "lines",
            "name": f"{y_column} vs {x_column}",
        }],
        "layout": {
            "title": {"text": title},
            "xaxis": {"title": {"text": x_label}},
            "yaxis": {"title": {"text": y_label}},
            "template": _DARK,
        },
    }