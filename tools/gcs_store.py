"""
GCS Store — save and load session data to/from Google Cloud Storage.

Storage layout in GCS:
  <bucket>/
    sessions/<session_id>/
      raw/                  ← original uploaded CSV files (one per topic)
        wheel_speed.csv
        ControlStatus.csv
        session_stat.csv
        ...
      processed/            ← output of the upload pipeline
        aligned.parquet
        laps.json
        schema.json
    tracks/<track_id>/
      centerline.kml
      segments.csv

At query time agents download individual topic CSVs from raw/ to a local
temp file and pass that path to query_engine functions — keeping query_engine
independent of GCS.
"""

import io
import json
import os
import tempfile
from pathlib import Path

import pandas as pd
from google.cloud import storage


def _bucket() -> storage.Bucket:
    client = storage.Client(project=os.getenv("GCP_PROJECT_ID"))
    return client.bucket(os.getenv("GCS_BUCKET_NAME"))


# ---------------------------------------------------------------------------
# Session storage
# ---------------------------------------------------------------------------

def save_session(
    session_id: str,
    df: pd.DataFrame,
    lap_boundaries: list[dict],
    schema: dict,
) -> None:
    """
    Save a processed and aligned session DataFrame to GCS.

    Args:
        session_id:     Unique session identifier (rosbag2 prefix).
        df:             Fully aligned DataFrame (output of csv_loader.load_session).
        lap_boundaries: List of {lap, t_start, t_end} dicts from lap_detector.
        schema:         Session schema dict from csv_loader.get_schema.
    """
    bucket = _bucket()
    prefix = f"sessions/{session_id}/processed"

    bucket.blob(f"{prefix}/aligned.csv").upload_from_string(
        df.to_csv(index=False), content_type="text/csv"
    )

    bucket.blob(f"{prefix}/laps.json").upload_from_string(
        json.dumps(lap_boundaries), content_type="application/json"
    )

    bucket.blob(f"{prefix}/schema.json").upload_from_string(
        json.dumps(schema), content_type="application/json"
    )


def load_session_meta(session_id: str) -> tuple[list[dict], dict]:
    """
    Load lap boundaries and schema for a session without downloading the parquet.

    Use this at query time — the aligned parquet is not needed for agent queries.

    Args:
        session_id: Unique session identifier.

    Returns:
        Tuple of (lap_boundaries, schema).

    Raises:
        FileNotFoundError: If the session does not exist in GCS.
    """
    bucket = _bucket()
    prefix = f"sessions/{session_id}/processed"

    if not bucket.blob(f"{prefix}/aligned.csv").exists():
        raise FileNotFoundError(f"Session '{session_id}' not found in GCS.")

    lap_boundaries = json.loads(bucket.blob(f"{prefix}/laps.json").download_as_text())
    schema = json.loads(bucket.blob(f"{prefix}/schema.json").download_as_text())

    return lap_boundaries, schema


def load_session(session_id: str) -> tuple[pd.DataFrame, list[dict], dict]:
    """
    Load a processed session from GCS including the full aligned DataFrame.

    Prefer load_session_meta when only lap boundaries and schema are needed.

    Args:
        session_id: Unique session identifier.

    Returns:
        Tuple of (aligned DataFrame, lap_boundaries, schema).

    Raises:
        FileNotFoundError: If the session does not exist in GCS.
    """
    bucket = _bucket()
    prefix = f"sessions/{session_id}/processed"

    blob = bucket.blob(f"{prefix}/aligned.csv")
    if not blob.exists():
        raise FileNotFoundError(f"Session '{session_id}' not found in GCS.")

    df = pd.read_csv(io.StringIO(blob.download_as_text()))
    lap_boundaries = json.loads(bucket.blob(f"{prefix}/laps.json").download_as_text())
    schema = json.loads(bucket.blob(f"{prefix}/schema.json").download_as_text())

    return df, lap_boundaries, schema


def session_exists(session_id: str) -> bool:
    """Check if a session has already been processed and stored in GCS."""
    return _bucket().blob(f"sessions/{session_id}/processed/aligned.csv").exists()


def list_sessions() -> list[str]:
    """List all fully processed session IDs stored in GCS."""
    bucket = _bucket()
    session_ids = set()
    for blob in bucket.list_blobs(prefix="sessions/"):
        parts = blob.name.split("/")
        # Only include sessions that have a completed aligned.parquet
        if len(parts) >= 4 and parts[2] == "processed" and parts[3] == "aligned.csv":
            session_ids.add(parts[1])
    return sorted(session_ids)


# ---------------------------------------------------------------------------
# Raw file storage (individual topic CSVs)
# ---------------------------------------------------------------------------

def save_raw_file(session_id: str, filename: str, file_bytes: bytes) -> None:
    """
    Save a raw uploaded CSV file to GCS under sessions/<id>/raw/.

    Call this for every uploaded file during the upload pipeline before
    any processing happens.

    Args:
        session_id: Session identifier (rosbag2 prefix).
        filename:   Original filename (e.g. 'rosbag2_2025_07_02-10_33_18_wheel_speed.csv').
        file_bytes: Raw file bytes.
    """
    _bucket().blob(f"sessions/{session_id}/raw/{filename}").upload_from_string(
        file_bytes, content_type="text/csv"
    )


def download_raw_file(session_id: str, topic: str, target_dir: str | None = None) -> str:
    """
    Download a raw topic CSV from GCS to a local file and return its path.

    Args:
        session_id:  Session identifier.
        topic:       Topic name (e.g. 'wheel_speed', 'ControlStatus', '_stat').
        target_dir:  If given, write the file into this directory instead of a
                     system temp file. The caller owns the directory and its cleanup.

    Returns:
        Absolute path to the downloaded local file.

    Raises:
        FileNotFoundError: If no raw file for this topic exists in GCS.
    """
    bucket = _bucket()
    prefix = f"sessions/{session_id}/raw/"

    matching = [
        blob for blob in bucket.list_blobs(prefix=prefix)
        if Path(blob.name).stem.lower().endswith(topic.lower()) or Path(blob.name).stem.lower() == topic.lower()
    ]

    if not matching:
        raise FileNotFoundError(
            f"No raw file for topic '{topic}' in session '{session_id}'."
        )

    blob = matching[0]
    filename = Path(blob.name).name
    data = blob.download_as_bytes()

    if target_dir:
        dest = Path(target_dir) / filename
        dest.write_bytes(data)
        return str(dest)

    tmp = tempfile.NamedTemporaryFile(suffix=f"_{filename}", delete=False, mode="wb")
    tmp.write(data)
    tmp.flush()
    tmp.close()
    return tmp.name


def list_raw_files(session_id: str) -> list[str]:
    """
    List topic names available in the raw/ folder for a session.

    Returns:
        List of topic names (e.g. ['wheel_speed', 'ControlStatus', '_stat']).
    """
    bucket = _bucket()
    prefix = f"sessions/{session_id}/raw/"
    topics = []
    for blob in bucket.list_blobs(prefix=prefix):
        filename = Path(blob.name).name
        if filename.endswith(".csv"):
            # Strip rosbag2 prefix to get topic name
            from tools.csv_loader import _parse_filename
            try:
                _, topic = _parse_filename(filename)
                topics.append(topic)
            except ValueError:
                topics.append(Path(filename).stem)
    return sorted(topics)


# ---------------------------------------------------------------------------
# Track file storage
# ---------------------------------------------------------------------------

def save_track_files(
    track_id: str,
    kml_bytes: bytes,
    segments_csv_bytes: bytes,
) -> None:
    """
    Save track setup files (KML + segments CSV) to GCS.

    Args:
        track_id:           Unique track identifier (e.g. 'laguna_seca').
        kml_bytes:          Raw bytes of the *_track.kml file.
        segments_csv_bytes: Raw bytes of the *_segments.csv file.
    """
    bucket = _bucket()
    prefix = f"tracks/{track_id}"
    bucket.blob(f"{prefix}/centerline.kml").upload_from_string(
        kml_bytes, content_type="application/vnd.google-earth.kml+xml"
    )
    bucket.blob(f"{prefix}/segments.csv").upload_from_string(
        segments_csv_bytes, content_type="text/csv"
    )


def load_track_segments(track_id: str) -> pd.DataFrame:
    """
    Load the segment definitions CSV for a track.

    Returns:
        DataFrame with columns: segment, start_lat, start_lon, end_lat, end_lon.

    Raises:
        FileNotFoundError: If the track does not exist in GCS.
    """
    bucket = _bucket()
    blob = bucket.blob(f"tracks/{track_id}/segments.csv")
    if not blob.exists():
        raise FileNotFoundError(f"Track '{track_id}' not found in GCS.")
    return pd.read_csv(io.StringIO(blob.download_as_text()))


def load_track_kml(track_id: str) -> bytes:
    """
    Load the raw KML bytes for a track centerline.

    Raises:
        FileNotFoundError: If the track KML does not exist in GCS.
    """
    bucket = _bucket()
    blob = bucket.blob(f"tracks/{track_id}/centerline.kml")
    if not blob.exists():
        raise FileNotFoundError(f"Track KML for '{track_id}' not found in GCS.")
    return blob.download_as_bytes()


def list_tracks() -> list[str]:
    """List all track IDs stored in GCS."""
    bucket = _bucket()
    track_ids = set()
    for blob in bucket.list_blobs(prefix="tracks/"):
        parts = blob.name.split("/")
        if len(parts) >= 2:
            track_ids.add(parts[1])
    return sorted(track_ids)
