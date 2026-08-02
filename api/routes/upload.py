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
  - Runs: save new files -> collect all from GCS -> process stat -> align all -> save to GCS
"""

import json
import tempfile
from pathlib import Path
from xml.dom import minidom
import numpy as np
import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from scipy.spatial import KDTree

from tools.csv_loader import _load_raw, _parse_filename, get_schema
from tools.gcs_store import (
    delete_session,
    download_raw_file,
    generate_raw_upload_url,
    list_raw_files,
    list_sessions,
    list_tracks,
    load_track_kml,
    load_track_segments,
    save_raw_file,
    save_session_meta,
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


def _merge_schema(dst: dict, src: dict) -> None:
    """Merge one file's schema (src) into an accumulator (dst) in place."""
    topics = set(dst.get("topics", []))
    topics.update(src.get("topics", []))
    dst["topics"] = list(topics)
    dst.setdefault("columns", []).extend(src.get("columns", []))
    dst.setdefault("columns_by_topic", {}).update(src.get("columns_by_topic", {}))
    dst.setdefault("row_counts", {}).update(src.get("row_counts", {}))
    s_range = src.get("time_range", [float("inf"), float("-inf")])
    d_range = dst.get("time_range", [float("inf"), float("-inf")])
    dst["time_range"] = [min(d_range[0], s_range[0]), max(d_range[1], s_range[1])]


def _laps_from_stat(stat_df: pd.DataFrame) -> list[dict]:
    """Rebuild lap boundaries from the 'lap' column of an already-enriched stat file."""
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
    return lap_boundaries


async def _run_pipeline(session_id: str, track_id: str, tmpdir: str):
    """
    Process a session whose raw CSVs already live in GCS (sessions/<id>/raw/).

    Yields SSE strings. Steps:
      1. Enrich the stat file if it is still raw (GPS conversion, zones, laps),
         or reuse it if it is already enriched (has a 'lap' column).
      2. Download all topic files, align them by timestamp, save the processed
         session back to GCS.

    Shared by both the multipart /session endpoint and the direct-to-GCS
    /process endpoint so the processing logic lives in exactly one place.
    """
    all_topics = list_raw_files(session_id)
    if not all_topics:
        yield _sse_error("No raw files found in GCS for this session.")
        return
    if "_stat" not in all_topics:
        yield _sse_error("No stat file found for this session. A *_stat.csv is required.")
        return

    # ------------------------------------------------------------------
    # Stat file: enrich if raw, reuse if already processed
    # ------------------------------------------------------------------
    enriched_stat_path = Path(download_raw_file(session_id, "_stat", target_dir=tmpdir))
    stat_df = _load_raw(enriched_stat_path)

    if "lap" not in stat_df.columns:
        yield _sse("Processing stat file (GPS conversion, zone assignment, lap detection)…")
        segments_df   = load_track_segments(track_id)
        kml_bytes     = load_track_kml(track_id)
        centerline_df = _parse_kml_centerline(kml_bytes)
        start_finish  = _parse_start_finish(segments_df)

        stat_df = process_stat_file(stat_df, start_finish)
        stat_df = _assign_zones(stat_df, centerline_df, segments_df)
        stat_df, lap_boundaries = detect_laps(stat_df, start_finish)

        # Overwrite the raw stat in GCS with the enriched version
        stat_df.to_csv(enriched_stat_path, index=False)
        save_raw_file(session_id, enriched_stat_path.name, enriched_stat_path.read_bytes())
        yield _sse(f"Stat processed - {len(lap_boundaries)} lap(s) detected.")
    else:
        yield _sse("Using already-enriched stat file…")
        lap_boundaries = _laps_from_stat(stat_df)

    # ------------------------------------------------------------------
    # Build schema one topic at a time: download -> read metadata -> delete.
    # Peak memory stays ~a single topic file, not the whole session (Cloud Run's
    # filesystem is RAM-backed, so downloaded files count against memory). The
    # full aligned dataset is intentionally NOT produced - queries re-align only
    # the topics they need, on demand, from the raw files.
    # ------------------------------------------------------------------
    other_topics = [t for t in all_topics if t != "_stat"]
    yield _sse(f"Building schema for {len(other_topics) + 1} topic(s)…")

    schema = get_schema([str(enriched_stat_path)]).get(session_id, {})

    for topic in other_topics:
        try:
            topic_path = download_raw_file(session_id, topic, target_dir=tmpdir)
        except FileNotFoundError:
            continue  # topic listed but blob missing - skip gracefully
        try:
            one = get_schema([topic_path]).get(session_id)
            if one:
                _merge_schema(schema, one)
        finally:
            Path(topic_path).unlink(missing_ok=True)  # free memory immediately

    schema["topics"] = sorted(schema.get("topics", []))
    save_session_meta(session_id, lap_boundaries, schema)

    topic_count = max(len(schema.get("row_counts", {})) - 1, 0)
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class SignedUrlRequest(BaseModel):
    track_id: str
    filenames: list[str]


class ProcessRequest(BaseModel):
    track_id: str
    session_id: str
    # Accepted for API compatibility. Processing always rebuilds the processed
    # session from whatever raw files are currently in storage, so this is a
    # no-op today; kept so the client can signal intent without breaking.
    force: bool = False


@router.post("/signed-urls")
def create_signed_urls(req: SignedUrlRequest):
    """
    Return a signed GCS upload URL for each file so the client can PUT the raw
    CSVs directly to storage, bypassing Cloud Run's 32 MiB request limit.

    Validates that the track exists and that all filenames belong to the same
    session. After uploading, the client calls POST /upload/process to run the
    pipeline on the uploaded files.

    Returns:
        {session_id, urls: {filename: signed_url}}
    """
    available_tracks = list_tracks()
    if req.track_id not in available_tracks:
        raise HTTPException(
            status_code=404,
            detail=f"Track '{req.track_id}' not found. Available: {available_tracks}",
        )

    session_id = None
    urls: dict[str, str] = {}
    for filename in req.filenames:
        if not filename.endswith(".csv"):
            raise HTTPException(status_code=422, detail=f"Only CSV files accepted, got: {filename}")
        try:
            sid, _ = _parse_filename(filename)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Filename does not match rosbag2 pattern: {filename}")
        if session_id is None:
            session_id = sid
        elif session_id != sid:
            raise HTTPException(
                status_code=422,
                detail=f"All files must belong to the same session. Got '{sid}', expected '{session_id}'.",
            )
        urls[filename] = generate_raw_upload_url(session_id, filename)

    if not session_id:
        raise HTTPException(status_code=422, detail="No valid rosbag2 CSV files provided.")

    return {"session_id": session_id, "urls": urls}


@router.post("/process")
async def process_session(req: ProcessRequest):
    """
    Run the processing pipeline on a session whose raw CSVs were already
    uploaded to GCS via signed URLs. Returns an SSE progress stream.
    """
    available_tracks = list_tracks()
    if req.track_id not in available_tracks:
        raise HTTPException(
            status_code=404,
            detail=f"Track '{req.track_id}' not found. Available: {available_tracks}",
        )

    async def event_generator():
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                async for ev in _run_pipeline(req.session_id, req.track_id, tmpdir):
                    yield ev
        except Exception as e:
            yield _sse_error(str(e))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/session")
async def upload_session(
    files: list[UploadFile] = File(...),
    track_id: str = Form(...),
    force: bool = Form(default=False),
):
    """
    Upload rosbag2 topic CSVs and optionally a *_stat.csv for a session.

    Returns a Server-Sent Events stream with progress updates.
    """
    # Validate and read all files before streaming starts
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
                # Force wipe - delete all existing GCS data for the session first
                if force:
                    yield _sse("Wiping existing session data…")
                    delete_session(session_id)

                # Save uploaded files to GCS (after any force-delete so they aren't wiped)
                yield _sse(f"Saving {len(file_data)} {'file' if len(file_data) == 1 else 'files'} to storage…")
                for filename, file_bytes, _ in file_data:
                    save_raw_file(session_id, filename, file_bytes)

                # Run the shared pipeline on the now-uploaded raw files
                async for ev in _run_pipeline(session_id, track_id, tmpdir):
                    yield ev

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