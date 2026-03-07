"""
Tracks routes — list available tracks and their segment info.

GET /tracks/            — list all track IDs in GCS
GET /tracks/{track_id}  — get segment definitions for a track
"""

from fastapi import APIRouter, HTTPException

from tools.gcs_store import list_tracks, load_track_segments

router = APIRouter()


@router.get("/")
def get_tracks():
    """List all tracks that have been uploaded to GCS."""
    return {"tracks": list_tracks()}


@router.get("/{track_id}")
def get_track(track_id: str):
    """
    Get segment definitions for a track.

    Returns:
        Dict with track_id, start_finish coordinate, and list of segments.
    """
    try:
        df = load_track_segments(track_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Track '{track_id}' not found.")

    start_finish = df[df["segment"] == "start_finish"].iloc[0]
    segments = df[df["segment"] != "start_finish"].to_dict(orient="records")

    return {
        "track_id": track_id,
        "start_finish": {
            "lat": float(start_finish["lat"]),
            "lon": float(start_finish["lon"]),
        },
        "segments": segments,
    }
