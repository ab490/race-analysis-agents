"""
Upload routes: handles track information files and session CSV uploads and runs the full processing pipeline.

POST /upload/track
  - Accepts *_track.kml + *_segments.csv
  - Saves track files to GCS for reuse across sessions
  
POST /upload/session
  - Accepts rosbag2 topic CSVs and optionally a *_stat.csv
  - Requires track_id to load segment definitions from GCS
  - Incremental: new files are merged with existing GCS raw files and re-aligned
  - If no stat file provided, reuses the enriched stat already in GCS
  - Runs: save new files → collect all from GCS → process stat → align all → save to GCS


"""

import json
import tempfile
from pathlib import Path
from xml.dom import minidom
import numpy as np
import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from scipy.spatial import KDTree

from tools.csv_loader import _load_raw, _parse_filename, get_schema
from tools.csv_loader import load_session as csv_load_session
from tools.gcs_store import (
    delete_session,
    download_raw_file,
    list_raw_files,
    list_sessions,
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


def _sse(text: str) -> str:
    return f"data: {json.dumps({'type': 'status', 'text': text})}\n\n"

def _sse_done(result: dict) -> str:
    return f"data: {json.dumps({'type': 'done', 'result': result})}\n\n"

def _sse_error(text: str) -> str:
    return f"data: {json.dumps({'type': 'error', 'text': text})}\n\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_start_finish(segments_df: pd.DataFrame) -> tuple[float, float]:
    """Extract the start/finish coordinate from the segments DataFrame."""
    sf = segments_df[segments_df["segment"] == "start_finish"]
    if sf.empty:
        raise HTTPException(
            status_code=422,
            detail="segments CSV must have a 'start_finish' row.",
        )
    row = sf.iloc[0]
    return float(row["lat"]), float(row["lon"])


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

    Segments CSV format: segment, lat, lon
    - Each segment's start is its own lat/lon
    - Each segment's end is the next segment's lat/lon
    - The last segment ends at the start_finish coordinate
    """
    centerline_df = centerline_df.copy()
    centerline_df["zone"] = "default"

    track_segments = segments_df[segments_df["segment"] != "start_finish"].reset_index(drop=True)

    for i, row in track_segments.iterrows():
        zone  = row["segment"]
        start = (float(row["lat"]), float(row["lon"]))

        # End is the next segment's start; last segment wraps back to s1
        if i + 1 < len(track_segments):
            next_row = track_segments.iloc[i + 1]
            end = (float(next_row["lat"]), float(next_row["lon"]))
        else:
            first_row = track_segments.iloc[0]
            end = (float(first_row["lat"]), float(first_row["lon"]))

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
    force: bool = Form(default=False),
):
    """
    Upload rosbag2 topic CSVs and optionally a *_stat.csv for a session.

    Returns a Server-Sent Events stream with progress updates.
    Events: {"type": "status", "text": "..."} | {"type": "done", "result": {...}} | {"type": "error", "text": "..."}
    """
    # Validate and read all files before streaming starts (HTTPException still works here)
    available_tracks = list_tracks()
    if track_id not in available_tracks:
        raise HTTPException(
            status_code=404,
            detail=f"Track '{track_id}' not found. Available: {available_tracks}",
        )

    file_data: list[tuple[str, bytes, str]] = []  # (filename, bytes, topic)
    session_id = None
    has_stat = False

    for upload in files:
        if not upload.filename.endswith(".csv"):
            raise HTTPException(status_code=422, detail=f"Only CSV files accepted, got: {upload.filename}")
        file_bytes = await upload.read()
        try:
            sid, topic = _parse_filename(upload.filename)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Filename does not match rosbag2 pattern: {upload.filename}")
        if session_id is None:
            session_id = sid
        elif session_id != sid:
            raise HTTPException(
                status_code=422,
                detail=f"All files must belong to the same session. Got '{sid}', expected '{session_id}'.",
            )
        if topic == "_stat":
            has_stat = True
        file_data.append((upload.filename, file_bytes, topic))

    if not session_id:
        raise HTTPException(status_code=422, detail="No valid rosbag2 CSV files found.")
    if force and not has_stat:
        raise HTTPException(
            status_code=422,
            detail="force=true requires a *_stat.csv so the session can be fully reprocessed from scratch.",
        )

    async def event_generator():
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                # ----------------------------------------------------------------
                # Step 1: Write uploaded files to temp dir, detect session_id
                # ----------------------------------------------------------------
                new_stat_path = None
                pending_saves = []

                for filename, file_bytes, topic in file_data:
                    dest = Path(tmpdir) / filename
                    dest.write_bytes(file_bytes)
                    pending_saves.append((filename, file_bytes))
                    if topic == "_stat":
                        new_stat_path = str(dest)

                # ----------------------------------------------------------------
                # Step 1b: Force wipe — delete all existing GCS data for the session
                # ----------------------------------------------------------------
                if force:
                    yield _sse("Wiping existing session data…")
                    delete_session(session_id)

                # Save uploaded files to GCS (after any force-delete so they aren't wiped)
                yield _sse(f"Saving {len(pending_saves)} {'file' if len(pending_saves) == 1 else 'files'} to storage…")
                for filename, file_bytes in pending_saves:
                    save_raw_file(session_id, filename, file_bytes)

                # ----------------------------------------------------------------
                # Step 2: Collect ALL raw topic names for this session from GCS
                # ----------------------------------------------------------------
                all_topics = list_raw_files(session_id)
                if not all_topics:
                    yield _sse_error("No raw files found in GCS for this session after upload.")
                    return

                # ----------------------------------------------------------------
                # Step 3: Process stat file (new or reuse enriched from GCS)
                # ----------------------------------------------------------------
                if new_stat_path:
                    yield _sse("Processing stat file (GPS conversion, zone assignment, lap detection)…")
                    segments_df   = load_track_segments(track_id)
                    kml_bytes     = load_track_kml(track_id)
                    centerline_df = _parse_kml_centerline(kml_bytes)
                    start_finish  = _parse_start_finish(segments_df)

                    stat_df = _load_raw(Path(new_stat_path))
                    stat_df = process_stat_file(stat_df, start_finish)
                    stat_df = _assign_zones(stat_df, centerline_df, segments_df)
                    stat_df, lap_boundaries = detect_laps(stat_df, start_finish)

                    # Save enriched stat back to GCS (overwrites the raw version)
                    enriched_stat_path = Path(tmpdir) / Path(new_stat_path).name
                    stat_df.to_csv(enriched_stat_path, index=False)
                    save_raw_file(session_id, Path(new_stat_path).name, enriched_stat_path.read_bytes())
                    yield _sse(f"Stat processed — {len(lap_boundaries)} lap(s) detected.")
                else:
                    # Reuse existing enriched stat from GCS
                    if "_stat" not in all_topics:
                        yield _sse_error("No stat file in this upload and no existing stat found in GCS. Upload a *_stat.csv first.")
                        return
                    yield _sse("Loading existing enriched stat file…")
                    enriched_stat_path_str = download_raw_file(session_id, "_stat", target_dir=tmpdir)
                    enriched_stat_path = Path(enriched_stat_path_str)
                    stat_df = _load_raw(enriched_stat_path)

                    # Rebuild lap_boundaries from the 'lap' column already in the enriched stat
                    if "lap" not in stat_df.columns:
                        yield _sse_error("Existing stat file has no 'lap' column. Re-upload the stat file to reprocess.")
                        return
                    lap_boundaries = []
                    for lap in sorted(stat_df["lap"].unique()):
                        if lap < 1:
                            continue
                        lap_rows = stat_df[stat_df["lap"] == lap]
                        lap_boundaries.append({
                            "lap": int(lap),
                            "t_start": float(lap_rows["stamp_seconds"].iloc[0]),
                            "t_end": float(lap_rows["stamp_seconds"].iloc[-1]),
                        })

                # ----------------------------------------------------------------
                # Step 4: Download ALL topic files (except stat, already in tmpdir)
                # ----------------------------------------------------------------
                other_topics = [t for t in all_topics if t != "_stat"]
                yield _sse(f"Downloading {len(other_topics)} topic {'file' if len(other_topics) == 1 else 'files'} from storage…")
                stat_filename = enriched_stat_path.name
                file_paths = [str(enriched_stat_path)]

                for topic in other_topics:
                    try:
                        local_path = download_raw_file(session_id, topic, target_dir=tmpdir)
                        file_paths.append(local_path)
                    except FileNotFoundError:
                        pass  # topic listed but blob missing — skip gracefully

                # ----------------------------------------------------------------
                # Step 5: Align all topics and save to GCS
                # ----------------------------------------------------------------
                yield _sse(f"Aligning {len(file_paths) - 1} topic(s) by timestamp…")
                sessions = csv_load_session(file_paths)
                df = sessions[session_id]

                yield _sse("Saving processed session to storage…")
                schema = get_schema(file_paths)
                save_session(session_id, df, lap_boundaries, schema.get(session_id, {}))

                topic_count = len([f for f in file_paths if not Path(f).name.endswith(stat_filename)])

                yield _sse_done({
                    "message": "Session processed and saved successfully.",
                    "session_id": session_id,
                    "lap_count": len(lap_boundaries),
                    "laps": lap_boundaries,
                    "topics": topic_count,
                    "duration_seconds": round(
                        lap_boundaries[-1]["t_end"] - lap_boundaries[0]["t_start"], 1
                    ) if lap_boundaries else 0,
                })

        except Exception as e:
            yield _sse_error(str(e))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/track")
async def upload_track(
    track_id: str = Form(...),
    kml_file: UploadFile = File(...),
    segments_file: UploadFile = File(...),
):
    """
    Upload track setup files - KML centerline and segment definitions CSV.

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
    required_cols = {"segment", "lat", "lon"}
    missing = required_cols - set(segments_df.columns)
    if missing:
        raise HTTPException(status_code=422, detail=f"segments CSV missing columns: {missing}")

    if "start_finish" not in segments_df["segment"].values:
        raise HTTPException(
            status_code=422,
            detail="segments CSV must contain a 'start_finish' row.",
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
    return {"sessions": list_sessions()}


@router.get("/sessions/{session_id}")
def get_session_info(session_id: str):
    """
    Get metadata for a specific session: lap boundaries and schema.

    Args:
        session_id: The session ID to look up.

    Returns:
        session_id, lap_boundaries (list of {lap, t_start, t_end}), schema.
    """
    from tools.gcs_store import load_session_meta
    if not session_exists(session_id):
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found.",
        )
    lap_boundaries, schema = load_session_meta(session_id)
    return {
        "session_id": session_id,
        "lap_boundaries": lap_boundaries,
        "schema": schema,
    }