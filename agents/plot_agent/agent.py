"""
Plot Agent: generates Plotly visualisations for any user request.

The agent calls server-side plot generation tools that return complete Plotly
figure dicts. The LLM only specifies what to plot (file path, columns, time
range, title) - it never has to output raw data arrays.

Supported chart types:
  - Time series (any signal over time)
  - Multi-lap overlay (same signal compared across laps)
  - Track map (lat/lon path, optionally coloured by a signal)
  - GG diagram (lateral vs longitudinal acceleration)

The agent returns a report dict: {title, sections: [{type: text|plot, ...}]}
"""

import os
from google.adk.agents import Agent

from agents.qa_agent.agent import describe_uploaded_files, get_topic_file
from tools.plot_generator import (
    make_gg_diagram,
    make_multi_lap_overlay,
    make_time_series,
    make_track_map,
)
from tools.query_engine import (
    get_column_stats,
    get_zone_time_windows,
)


def resolve_lap_window(lap_boundaries: list[dict], lap_number: int) -> dict:
    """
    Resolve a lap number to its start and end timestamps.

    Call this before plotting when the request is scoped to a specific lap.

    Args:
        lap_boundaries: List of dicts with keys 'lap', 't_start', 't_end'.
        lap_number:     The lap number to resolve.

    Returns:
        Dict with lap, t_start, t_end - or error if lap not found.
    """
    for b in lap_boundaries:
        if b["lap"] == lap_number:
            return b
    available = [b["lap"] for b in lap_boundaries]
    return {"error": f"Lap {lap_number} not found. Available laps: {available}"}


def get_zone_windows_for_plot(stat_file_path: str, zone_name: str) -> dict:
    """
    Get the time windows for a named track zone.

    Returns a list of {t_start, t_end, lap} dicts - pass the full list as
    lap_windows to plot_time_series_overlay to get a per-lap overlay chart.

    Args:
        stat_file_path: Path to the *_stat.csv file.
        zone_name:      Zone label (e.g. 's1'). Use available_zones from the
                        response if the exact name is unclear.

    Returns:
        Dict with zone, windows list, total_windows, available_zones.
    """
    return get_zone_time_windows(stat_file_path, zone_name)


def get_stats_for_annotation(
    file_path: str,
    column: str,
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Get descriptive statistics for a column - use to annotate plot captions
    with min/max/mean values.

    Args:
        file_path: Path to the rosbag2 CSV file.
        column:    Column name.
        t_start:   Optional start time filter.
        t_end:     Optional end time filter.

    Returns:
        Dict with min, max, mean, std, p25, p50, p75, p95, count.
    """
    try:
        return get_column_stats(file_path, column, t_start, t_end)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Plot generation tools - each returns a complete Plotly figure dict
# ---------------------------------------------------------------------------

def plot_time_series(
    file_path: str,
    columns: list[str],
    title: str,
    y_label: str,
    t_start: float | None = None,
    t_end: float | None = None,
    y_scale: float = 1.0,
) -> dict:
    """
    Generate a time-series chart for one or more columns from a single CSV file.

    Use for any "plot X over time" request. Returns a complete Plotly figure dict
    - include it verbatim under "figure" in a plot section of your report.

    If the tool returns {"error": "..."}, read the available columns listed in
    the error and retry with the correct column name.

    Args:
        file_path: Path to the topic CSV.
        columns:   List of column names to plot.
        title:     Chart title.
        y_label:   Y-axis label including units (e.g. "Speed (m/s)").
        t_start:   Optional start time filter (Unix float).
        t_end:     Optional end time filter (Unix float).
        y_scale:   Multiply all y values by this factor (e.g. 2.23694 for m/s->mph,
                   1/9.80665 to convert m/s² -> g).

    Returns:
        Plotly figure dict {"data": [...], "layout": {...}} or {"error": "..."}.
    """
    return make_time_series(file_path, columns, title, y_label, t_start, t_end, y_scale)


def plot_time_series_overlay(
    file_path: str,
    column: str,
    lap_windows: list[dict],
    title: str,
    y_label: str,
    y_scale: float = 1.0,
) -> dict:
    """
    Overlay the same signal across multiple laps on one chart.

    Use for lap comparison requests like "compare brake pressure in lap 1 vs lap 3"
    or "overlay speed across all laps in sector s1".

    Pass lap_windows as a list of {lap, t_start, t_end} dicts - either from
    resolve_lap_window (for specific laps) or get_zone_windows_for_plot (for all
    occurrences of a zone).

    If the tool returns {"error": "..."}, read the available columns and retry.

    Args:
        file_path:   Path to the topic CSV.
        column:      Column to compare across laps.
        lap_windows: List of {lap, t_start, t_end} dicts.
        title:       Chart title.
        y_label:     Y-axis label with units.
        y_scale:     Multiply y values by this factor.

    Returns:
        Plotly figure dict {"data": [...], "layout": {...}} or {"error": "..."}.
    """
    return make_multi_lap_overlay(file_path, column, lap_windows, title, y_label, y_scale)


def plot_track_map(
    stat_file_path: str,
    color_column: str | None = None,
    color_label: str = "",
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Generate a track map from GPS lat/lon in the stat file.

    Optionally colour the path by a signal value (e.g. speed heatmap on track).
    The stat file must have lat and lon columns (added during upload processing).

    If color_column returns {"error": "..."}, check available columns and retry
    or omit color_column to get a plain track outline.

    Args:
        stat_file_path: Path to the enriched *_stat.csv file.
        color_column:   Optional column in the stat file to use as colour scale.
                        None = plain line.
        color_label:    Label for the colour scale bar.
        t_start:        Optional time filter.
        t_end:          Optional time filter.

    Returns:
        Plotly figure dict {"data": [...], "layout": {...}} or {"error": "..."}.
    """
    return make_track_map(stat_file_path, color_column, color_label, t_start, t_end)


def plot_gg_diagram(
    imu_file_path: str,
    lat_accel_col: str = "linear_acceleration_y",
    lon_accel_col: str = "linear_acceleration_x",
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Generate a GG diagram (lateral vs longitudinal acceleration scatter plot).

    Acceleration values are automatically converted from m/s² to g.

    If a column name is wrong, the tool returns {"error": "..."} with the
    available columns - retry with the correct names.

    Args:
        imu_file_path:  Path to the IMU CSV file.
        lat_accel_col:  Column for lateral acceleration in m/s².
                        Default: "linear_acceleration_y".
        lon_accel_col:  Column for longitudinal acceleration in m/s².
                        Default: "linear_acceleration_x".
        t_start:        Optional time filter.
        t_end:          Optional time filter.

    Returns:
        Plotly figure dict {"data": [...], "layout": {...}} or {"error": "..."}.
    """
    return make_gg_diagram(imu_file_path, lat_accel_col, lon_accel_col, t_start, t_end)


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

root_agent = Agent(
    name="plot_agent",
    model=os.getenv("VERTEX_AI_MODEL", "gemini-2.0-flash-lite-001"),
    description=(
        "Generates Plotly visualisations for any race telemetry plot request. "
        "Handles time series, track maps, GG diagrams, lap comparisons, "
        "and segment breakdowns."
    ),
    instruction="""
You are a race data visualisation engineer. Your job is to create Plotly charts
that help engineers and drivers understand telemetry data.

## How to handle any plot request

1. **Understand what to visualise.**
   Identify: which signals, over what time range, grouped/coloured by what.

2. **Get the file path for the needed topic.**
   Your context includes "Available topics and columns". Find which topic has the
   column you need, then call get_topic_file(topic_name) to download it and get
   its local path. Pass that path to the plot tool.
   Call describe_uploaded_files (with the stat file path) only if you need to
   explore columns in more detail.

3. **Resolve scope.**
   - Lap-scoped request -> call resolve_lap_window first to get t_start/t_end.
   - Zone-scoped overlay -> call get_zone_windows_for_plot to get per-lap windows.

4. **Call the right plot tool.**
   Each tool returns a complete Plotly figure dict - you never have to output raw
   data arrays yourself.

   | Request type                              | Tool                        |
   |-------------------------------------------|-----------------------------|
   | Any signal over time                      | plot_time_series            |
   | Same signal compared across laps/zones    | plot_time_series_overlay    |
   | Track path (plain or coloured by signal)  | plot_track_map              |
   | GG / acceleration diagram                 | plot_gg_diagram             |

5. **Handle errors by retrying.**
   If a tool returns `{"error": "..."}`, read the available columns listed in the
   error and retry immediately with the correct column name. Never give up on the
   first error.

6. **Build the report.**
   Return the figure dict verbatim from the tool result - do NOT modify it.
   Add a brief text section with key insights (peak values, trends, comparisons).

## Common topic-to-column mappings
- Speed -> ControlStatus: actual_velocity_mps (y_scale=2.23694 for mph)
- Steering -> ControlStatus: actual_steering_degree
- Brake pressure -> brake_pressure_report: brake_pressure_fdbk_front, brake_pressure_fdbk_rear
- Wheel speed -> wheel_speed: wheel_speed_fl/fr/rl/rr
- Acceleration -> Imu: linear_acceleration_x/y/z (y_scale=1/9.80665 for g)
- GPS position -> stat file: lat, lon
- Tire temp -> tire_temp_fl/fr/rl/rr: per-corner columns
- Suspension -> potentiometer: wheel_potentiometer_fl/fr/rl/rr
- Zone / segment -> stat file: zone column

## Output format
{
  "title": "...",
  "sections": [
    {"type": "text", "content": "...brief insight..."},
    {"type": "plot", "figure": <figure dict from tool>, "caption": "..."}
  ]
}
""",
    tools=[
        get_topic_file,
        describe_uploaded_files,
        resolve_lap_window,
        get_zone_windows_for_plot,
        get_stats_for_annotation,
        plot_time_series,
        plot_time_series_overlay,
        plot_track_map,
        plot_gg_diagram,
    ],
)