"""
Data Agent — understands uploaded telemetry CSV structure.

Responsibilities:
- Accept a list of uploaded rosbag2 CSV file paths
- Identify what sessions, topics, and columns are present
- Describe what each topic and column represents in plain English
- Tell other agents what data is available before they query it
"""

import os

from google.adk.agents import Agent

from tools.csv_loader import get_schema, load_session


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def describe_uploaded_files(file_paths: list[str]) -> dict:
    """
    Parse uploaded rosbag2 CSV files and return a structured description of
    their contents: sessions, topics, column names, time ranges, and row counts.

    Use this as the first step whenever a user uploads files. The output tells
    you what data is available before querying actual values.

    Args:
        file_paths: List of absolute paths to uploaded CSV files.

    Returns:
        Dict with session IDs as keys. Each value contains:
            - topics: list of ROS2 topic names present
            - columns: all data column names across all topics
            - time_range: [start, end] as Unix float timestamps
            - row_counts: dict of topic -> number of rows (proxy for sample rate)
            - duration_seconds: total recording duration
    """
    schema = get_schema(file_paths)
    for session_id, info in schema.items():
        t_start, t_end = info["time_range"]
        info["duration_seconds"] = round(t_end - t_start, 3)
    return schema


def load_aligned_session(file_paths: list[str]) -> dict:
    """
    Load and align all uploaded CSV files into a single merged DataFrame per session.

    All topics are aligned to the lowest-frequency topic using nearest-timestamp
    matching. No values are interpolated — only real measurements are used.

    Use this only when actual data values are needed, not just structure.
    Prefer describe_uploaded_files first to understand what is available.

    Args:
        file_paths: List of absolute paths to uploaded CSV files.

    Returns:
        Dict mapping session_id to a summary of the aligned DataFrame:
            - shape: [rows, columns]
            - columns: list of all column names
            - time_range: [start, end] as Unix float timestamps
            - sample: first 3 rows as list of dicts
    """
    sessions = load_session(file_paths)
    result = {}
    for session_id, df in sessions.items():
        t_col = df["t"]
        result[session_id] = {
            "shape": list(df.shape),
            "columns": list(df.columns),
            "time_range": [float(t_col.min()), float(t_col.max())],
            "sample": df.head(3).to_dict(orient="records"),
        }
    return result


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

root_agent = Agent(
    name="data_agent",
    model=os.getenv("VERTEX_AI_MODEL", "gemini-1.5-pro"),
    description=(
        "Understands uploaded race telemetry CSV files exported from ROS2 rosbag2. "
        "Identifies sessions, topics, and columns, and explains what each represents "
        "in plain English for engineers and drivers."
    ),
    instruction="""
You are a race engineering data analyst specialising in ROS2-based autonomous racing systems.

Your job is to help users understand the telemetry data they have uploaded. The data comes
from rosbag2 bag files exported as CSV — one file per ROS2 topic per session.

When a user uploads files:
1. Call describe_uploaded_files with all file paths to get the schema.
2. Summarise what sessions are present, how long they are, and what topics are available.
3. Group topics by category and explain what each group tells you about the car's behaviour:
   - GPS / localisation: gps_top, gps_side, bestgnsspos_*, bestgnssvel_*, heading2_*, gnss_vectornav
   - IMU / inertial: Imu, imu_group, imu_side, imu_vectornav, attitude_group, ins_group
   - Vehicle control: ControlStatus, steering_report, brake_pressure_report, brake_pressure_cmd, wheel_speed
   - Tyres: tire_temp_fl/fr/rl/rr, tire_pressure_fl/fr/rl/rr, potentiometer, strain_gauge
   - Powertrain: marelli, pt_report_1/2/3, wheel_status
   - Planning / autonomy: MPCPrediction, Path, planner_status, ControlStatus
4. If the user asks about a specific topic or column, explain what it measures and why it matters.

Column naming conventions:
- Timestamps are already parsed to float seconds — do not mention ROS2 internals to users.
- Suffixes _side / _top refer to different GPS antenna positions on the car.
- Suffixes _fl, _fr, _rl, _rr = front-left, front-right, rear-left, rear-right.
- ControlStatus is the richest single topic: commanded and actual values for throttle,
  brake, steering, gear, and velocity, plus controller state and cross-track error.

Always respond in plain English. Avoid ROS2 jargon unless the user asks for it.
Do not call load_aligned_session unless the user explicitly asks to inspect data values.
""",
    tools=[
        describe_uploaded_files,
        load_aligned_session,
    ],
)
