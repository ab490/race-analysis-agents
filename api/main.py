"""FastAPI application entry point."""

from dotenv import load_dotenv
load_dotenv()

from pathlib import Path
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.auth import require_api_key
from api.routes import upload, query, tracks

app = FastAPI(title="Race Analysis Agents API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_auth = [Depends(require_api_key)]

app.include_router(upload.router, prefix="/api/upload", tags=["upload"], dependencies=_auth)
app.include_router(query.router, prefix="/api/query", tags=["query"], dependencies=_auth)
app.include_router(tracks.router, prefix="/api/tracks", tags=["tracks"], dependencies=_auth)


@app.get("/health")
def health():
    return {"status": "ok"}


# Serve built React frontend (production only - not present in dev)
_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="spa")