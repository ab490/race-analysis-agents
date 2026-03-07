"""
Plot Agent — generates Plotly visualisations for any user request.

Uses Gemini's built-in code execution (BuiltInCodeExecutor) so the LLM can write arbitrary
Plotly code to answer open-ended plot requests like:
  - "Show acceleration values across track segments"
  - "Compare brake pressure in lap 1 vs lap 3"
  - "GG diagram for the full session"
  - "Speed heatmap on the track map"

The agent:
1. Loads the relevant data via query tools
2. Writes and executes Python + Plotly code
3. Returns a report dict (title + text sections + plot sections)
"""

import os

from google.adk.agents import Agent
from google.adk.code_executors import BuiltInCodeExecutor

from tools.csv_loader import get_schema
from tools.query_engine import (
    get_column_stats,
    get_column_stats_for_zone,
    get_time_series,
    get_zone_time_windows,
    query_cross_topic,
)


# ---------------------------------------------------------------------------
# Data access tools (same as qa_agent — agent needs data before plotting)
# ---------------------------------------------------------------------------

def describe_uploaded_files(file_paths: list[str]) -> dict:
    """
    Discover what topics and columns are available in the uploaded session files.

    Call this when unsure which file contains the columns needed for the plot.

    Args:
        file_paths: List of all file paths available for this session.

    Returns:
        Dict with session IDs as keys, each containing topics, columns,
        time_range, row_counts, and duration_seconds.
    """
    schema = get_schema(file_paths)
    for info in schema.values():
        t_start, t_end = info["time_range"]
        info["duration_seconds"] = round(t_end - t_start, 3)
    return schema


def get_data_for_plot(
    file_path: str,
    columns: list[str],
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Load time series data for one or more columns from a single topic file.

    Call this to fetch the data you need before writing plot code.
    Returns up to 1000 points (higher than qa_agent's 500 — plots benefit
    from more resolution).

    Args:
        file_path: Path to the rosbag2 CSV file.
        columns:   Column names to retrieve.
        t_start:   Optional start time filter (Unix float).
        t_end:     Optional end time filter (Unix float).

    Returns:
        Dict with t (timestamps), data (column → values), total_rows, returned_rows.
    """
    return get_time_series(file_path, columns, t_start, t_end, max_points=1000)


def get_cross_topic_data(
    file_paths: list[str],
    columns: list[str],
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Align 2–5 topic files and return columns together for cross-topic plotting.

    Use this when the plot requires signals from different topic files
    (e.g. GPS position + speed, brake pressure + tire temperature).

    Column names in the result are suffixed with the topic name
    (e.g. 'actual_velocity_mps__ControlStatus').

    Args:
        file_paths: 2–5 relevant rosbag2 CSV file paths.
        columns:    Column names in 'column__topic' format. Pass [] for all.
        t_start:    Optional start time filter.
        t_end:      Optional end time filter.

    Returns:
        Dict with t, data, total_rows, returned_rows, available_columns.
    """
    return query_cross_topic(file_paths, columns, t_start, t_end, max_points=1000)


def get_stats_for_plot(
    file_path: str,
    column: str,
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """
    Get descriptive statistics for a column — useful for annotation on plots
    or for generating summary bar/box charts.

    Args:
        file_path: Path to the rosbag2 CSV file.
        column:    Column name.
        t_start:   Optional start time filter.
        t_end:     Optional end time filter.

    Returns:
        Dict with min, max, mean, std, p25, p50, p75, p95, count.
    """
    return get_column_stats(file_path, column, t_start, t_end)


def get_zone_windows_for_plot(
    stat_file_path: str,
    zone_name: str,
) -> dict:
    """
    Get the time windows for a named track zone — use before plotting zone-scoped data.

    Returns a list of {t_start, t_end, lap} dicts. Pass each window's t_start/t_end
    into get_data_for_plot to load data for that zone occurrence, then overlay traces
    per lap on the same chart.

    Args:
        stat_file_path: Path to the *_stat.csv file.
        zone_name:      Zone label (e.g. 'sector_1'). Use available_zones from the
                        response if the exact name is unclear.

    Returns:
        Dict with zone, windows list, total_windows, available_zones.
    """
    return get_zone_time_windows(stat_file_path, zone_name)


def get_zone_stats_for_plot(
    data_file_path: str,
    stat_file_path: str,
    zone_name: str,
    column: str,
) -> dict:
    """
    Get per-lap statistics for a column in a zone — useful for bar/box charts by zone.

    Args:
        data_file_path: Path to the topic CSV containing the column.
        stat_file_path: Path to the *_stat.csv file.
        zone_name:      Track zone label.
        column:         Column to analyse.

    Returns:
        Dict with overall stats and per_lap breakdown.
    """
    return get_column_stats_for_zone(data_file_path, stat_file_path, zone_name, column)


def resolve_lap_window(lap_boundaries: list[dict], lap_number: int) -> dict:
    """
    Resolve a lap number to its start and end timestamps.

    Call this before loading data when the plot is scoped to a specific lap.

    Args:
        lap_boundaries: List of dicts with keys 'lap', 't_start', 't_end'.
        lap_number:     The lap number to resolve.

    Returns:
        Dict with lap, t_start, t_end — or error if lap not found.
    """
    for b in lap_boundaries:
        if b["lap"] == lap_number:
            return b
    available = [b["lap"] for b in lap_boundaries]
    return {"error": f"Lap {lap_number} not found. Available laps: {available}"}


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

root_agent = Agent(
    name="plot_agent",
    model=os.getenv("VERTEX_AI_MODEL", "gemini-1.5-pro"),
    description=(
        "Generates Plotly visualisations for any race telemetry plot request. "
        "Handles time series, track maps, GG diagrams, lap comparisons, "
        "segment breakdowns, and any custom chart the user asks for."
    ),
    instruction="""
You are a race data visualisation engineer. Your job is to create Plotly charts
that help engineers and drivers understand telemetry data.

## How to handle any plot request

1. **Understand what to visualise.**
   Identify: which signals, over what time range, grouped/coloured by what.

2. **Load the data.**
   - Call describe_uploaded_files if unsure which file contains the needed columns.
   - Call get_data_for_plot for single-topic data.
   - Call get_cross_topic_data for signals from multiple topics.
   - Call resolve_lap_window first if the request is scoped to a lap.

3. **Write and execute Python + Plotly code.**
   Use the code executor to generate the figure. Always use plotly.graph_objects
   or plotly.express. Return the figure as a JSON dict using fig.to_dict().

4. **Return a report dict** with title, text summary, and the plot(s).

## Plot guidelines
- Use a dark theme: `template="plotly_dark"`
- For track maps: scatter plot with lat on y-axis, lon on x-axis, `mode="lines+markers"`
- For heatmaps on track: colour scatter points by signal value using a colorscale
- For lap comparisons: one trace per lap, different colours, shared x-axis
- For segment breakdowns: use box plots or bar charts grouped by segment/zone
- For GG diagrams: scatter of lateral_accel (x) vs longitudinal_accel (y), normalise to g
- Always label axes clearly with units
- Add a descriptive title to every chart

## Common topic-to-column mappings
- Speed → ControlStatus: actual_velocity_mps (convert × 2.23694 for mph)
- Steering → ControlStatus: actual_steering_degree
- Brake pressure → brake_pressure_report: brake_pressure_fdbk_front/rear
- Wheel speed → wheel_speed: wheel_speed_fl/fr/rl/rr
- Acceleration → Imu: linear_acceleration_x/y/z (divide by 9.80665 for g)
- GPS position → gps_top: latitude, longitude
- Tire temp → tire_temp_fl/fr/rl/rr: per-corner columns
- Suspension → potentiometer: wheel_potentiometer_fl/fr/rl/rr
- Segment/zone → stat file: zone column (assigned during upload processing)

## Code execution format
Write Python code in a code block. The code must:
- Import pandas, plotly.graph_objects as go (or plotly.express as px)
- Build the figure using data from the tool results above
- End with: `print(fig.to_dict())`

## Output format
After executing the code, return a report dict:
{
  "title": "...",
  "sections": [
    {"type": "text", "content": "...brief insight..."},
    {"type": "plot", "figure": {...plotly dict...}, "caption": "..."}
  ]
}
""",
    code_executor=BuiltInCodeExecutor(),
    tools=[
        describe_uploaded_files,
        get_data_for_plot,
        get_cross_topic_data,
        get_stats_for_plot,
        get_zone_windows_for_plot,
        get_zone_stats_for_plot,
        resolve_lap_window,
    ],
)
