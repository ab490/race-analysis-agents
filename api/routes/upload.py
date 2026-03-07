"""
Upload routes — handles session CSV uploads and runs the full processing pipeline.

POST /upload/session
  - Accepts rosbag2 topic CSVs + one *_stat.csv
  - Requires track_id to load segment definitions from GCS
  - Runs: load → ENU→lat/lon → lap detection → segment assignment → align → save to GCS

POST /upload/track
  - Accepts *_track.kml + *_segments.csv
  - Saves track files to GCS for reuse across sessions
"""

import tempfile
from pathlib import Path
from xml.dom import minidom

import numpy as np
import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from scipy.spatial import KDTree

from tools.csv_loader import get_schema
from tools.csv_loader import load_session as csv_load_session
from tools.gcs_store import (
    list_tracks,
    load_track_kml,
    load_track_segments,
    save_raw_file,
    save_session,
    save_track_files,
    session_exists,
)
from tools.lap_detector import detect_laps, process_stat_file

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_start_finish(segments_df: pd.DataFrame) -> tuple[float, float]:
    """Extract the start/finish coordinate from the segments DataFrame."""
    sf = segments_df[segments_df["segment"] == "start_finish"]
    if sf.empty:
        raise HTTPException(
            status_code=422,
            detail="segments CSV must have a 'start_finish' row as the first entry.",
        )
    row = sf.iloc[0]
    return float(row["start_lat"]), float(row["start_lon"])


def _parse_kml_centerline(kml_bytes: bytes) -> pd.DataFrame:
    """Parse a KML file and return a DataFrame with lat, lon columns."""
    xmldoc = minidom.parseString(kml_bytes)
    coordinates = xmldoc.getElementsByTagName("coordinates")
    if not coordinates:
        raise HTTPException(status_code=422, detail="No <coordinates> found in KML file.")
    coord_text = coordinates[0].firstChild.nodeValue.strip()
    coords = []
    for line in coord_text.split():
        parts = line.split(",")
        if len(parts) >= 2:
            coords.append({"lon": float(parts[0]), "lat": float(parts[1])})
    return pd.DataFrame(coords)


def _assign_zones(stat_df: pd.DataFrame, centerline_df: pd.DataFrame, segments_df: pd.DataFrame) -> pd.DataFrame:
    """
    Assign a track zone/segment label to each row of the stat DataFrame
    using nearest-neighbour matching against the KML centerline.

    Strategy (mirrors existing racing-data-analysis-tool):
    1. For each segment boundary pair, find the closest centerline points
    2. Assign zone labels to centerline rows
    3. Match each stat row to the nearest centerline point → inherit its zone
    """
    centerline_df = centerline_df.copy()
    centerline_df["zone"] = "default"

    track_segments = segments_df[segments_df["segment"] != "start_finish"]

    for _, row in track_segments.iterrows():
        zone = row["segment"]
        start = (row["start_lat"], row["start_lon"])
        end   = (row["end_lat"],   row["end_lon"])

        start_idx = ((centerline_df["lat"] - start[0]) ** 2 + (centerline_df["lon"] - start[1]) ** 2).idxmin()
        end_idx   = ((centerline_df["lat"] - end[0])   ** 2 + (centerline_df["lon"] - end[1])   ** 2).idxmin()

        if start_idx <= end_idx:
            mask = (centerline_df.index >= start_idx) & (centerline_df.index <= end_idx) & (centerline_df["zone"] == "default")
        else:
            mask = ((centerline_df.index >= start_idx) | (centerline_df.index <= end_idx)) & (centerline_df["zone"] == "default")

        centerline_df.loc[mask, "zone"] = zone

    # Nearest-neighbour match stat rows to centerline zones
    ref_coords  = np.radians(centerline_df[["lat", "lon"]].to_numpy())
    stat_coords = np.radians(stat_df[["lat", "lon"]].to_numpy())
    tree = KDTree(ref_coords)
    _, idxs = tree.query(stat_coords, k=1)

    stat_df = stat_df.copy()
    stat_df["zone"] = centerline_df.iloc[idxs]["zone"].values
    return stat_df


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/session")
async def upload_session(
    files: list[UploadFile] = File(...),
    track_id: str = Form(...),
):
    """
    Upload rosbag2 topic CSVs + one *_stat.csv for a session.

    Runs the full processing pipeline:
      load CSVs → ENU→lat/lon → lap detection → zone assignment → align → save to GCS

    Args:
        files:    List of CSV files (rosbag2 topics + one *_stat.csv).
        track_id: Track identifier matching a previously uploaded track in GCS.

    Returns:
        session_id, lap count, topics uploaded, duration_seconds.
    """
    # Validate track exists
    available_tracks = list_tracks()
    if track_id not in available_tracks:
        raise HTTPException(
            status_code=404,
            detail=f"Track '{track_id}' not found. Available: {available_tracks}",
        )

    # Save uploaded files to a temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        file_paths = []
        stat_path = None
        raw_files = []  # (filename, bytes) — held for GCS raw/ upload after session_id is known

        for upload in files:
            if not upload.filename.endswith(".csv"):
                raise HTTPException(status_code=422, detail=f"Only CSV files accepted, got: {upload.filename}")

            file_bytes = await upload.read()
            dest = Path(tmpdir) / upload.filename
            dest.write_bytes(file_bytes)
            file_paths.append(str(dest))
            raw_files.append((upload.filename, file_bytes))

            if upload.filename.lower().endswith("_stat.csv"):
                stat_path = str(dest)

        if stat_path is None:
            raise HTTPException(
                status_code=422,
                detail="No *_stat.csv file found. Upload exactly one stat file per session.",
            )

        # Detect session_id from rosbag2 filenames
        rosbag_files = [f for f in file_paths if not f.endswith("_stat.csv")]
        if not rosbag_files:
            raise HTTPException(status_code=422, detail="No rosbag2 topic CSV files found.")

        from tools.csv_loader import _parse_filename
        session_id = _parse_filename(Path(rosbag_files[0]).name)[0]

        if session_exists(session_id):
            return {
                "message": "Session already processed.",
                "session_id": session_id,
                "reprocessed": False,
            }

        # Save raw uploaded files to GCS before any processing
        for filename, file_bytes in raw_files:
            save_raw_file(session_id, filename, file_bytes)

        # Load track files from GCS
        segments_df   = load_track_segments(track_id)
        kml_bytes     = load_track_kml(track_id)
        centerline_df = _parse_kml_centerline(kml_bytes)
        start_finish  = _parse_start_finish(segments_df)

        # Load and process stat file
        from tools.csv_loader import _load_raw
        stat_df = _load_raw(Path(stat_path))
        stat_df = process_stat_file(stat_df, start_finish)
        stat_df = _assign_zones(stat_df, centerline_df, segments_df)
        stat_df, lap_boundaries = detect_laps(stat_df, start_finish)

        # Replace stat file with the enriched version (lat/lon/zone/lap columns added)
        enriched_stat_path = Path(tmpdir) / Path(stat_path).name
        # Save enriched stat as temp CSV so csv_loader can re-read it with standard pipeline
        # Use stamp_seconds since t column is already float
        stat_df_out = stat_df.copy()
        stat_df_out.insert(0, "stamp_seconds", stat_df_out.pop("t"))
        stat_df_out.to_csv(enriched_stat_path, index=False)

        # Overwrite the original stat in GCS raw/ with the enriched version
        # (adds lat, lon, zone, lap columns that agents need at query time)
        save_raw_file(session_id, Path(stat_path).name, enriched_stat_path.read_bytes())

        # Update file_paths to use enriched stat
        file_paths = [f for f in file_paths if not f.endswith("_stat.csv")]
        file_paths.append(str(enriched_stat_path))

        # Load and align all topics
        sessions = csv_load_session(file_paths)
        df = sessions[session_id]

        # Get schema — include the enriched stat so zone/lap/lat/lon columns are listed
        schema = get_schema(file_paths)

        # Save to GCS
        save_session(session_id, df, lap_boundaries, schema.get(session_id, {}))

    return {
        "message": "Session processed and saved successfully.",
        "session_id": session_id,
        "lap_count": len(lap_boundaries),
        "laps": lap_boundaries,
        "topics": len(rosbag_files),
        "duration_seconds": round(
            lap_boundaries[-1]["t_end"] - lap_boundaries[0]["t_start"], 1
        ) if lap_boundaries else 0,
    }


@router.post("/track")
async def upload_track(
    track_id: str = Form(...),
    kml_file: UploadFile = File(...),
    segments_file: UploadFile = File(...),
):
    """
    Upload track setup files — KML centerline and segment definitions CSV.

    The segments CSV must have columns: segment, start_lat, start_lon, end_lat, end_lon.
    The first row must be 'start_finish'.

    Args:
        track_id:       Unique identifier for this track (e.g. 'laguna_seca').
        kml_file:       The *_track.kml file.
        segments_file:  The *_segments.csv file.
    """
    if not kml_file.filename.endswith(".kml"):
        raise HTTPException(status_code=422, detail="kml_file must be a .kml file.")
    if not segments_file.filename.endswith(".csv"):
        raise HTTPException(status_code=422, detail="segments_file must be a .csv file.")

    kml_bytes      = await kml_file.read()
    segments_bytes = await segments_file.read()

    # Validate segments CSV
    segments_df = pd.read_csv(pd.io.common.BytesIO(segments_bytes))
    required_cols = {"segment", "start_lat", "start_lon", "end_lat", "end_lon"}
    missing = required_cols - set(segments_df.columns)
    if missing:
        raise HTTPException(status_code=422, detail=f"segments CSV missing columns: {missing}")

    if segments_df.iloc[0]["segment"] != "start_finish":
        raise HTTPException(
            status_code=422,
            detail="First row of segments CSV must be 'start_finish'.",
        )

    save_track_files(track_id, kml_bytes, segments_bytes)

    return {
        "message": f"Track '{track_id}' saved successfully.",
        "track_id": track_id,
        "segment_count": len(segments_df) - 1,  # exclude start_finish row
    }


@router.get("/sessions")
def get_sessions():
    """List all processed sessions available in GCS."""
    from tools.gcs_store import list_sessions
    return {"sessions": list_sessions()}
