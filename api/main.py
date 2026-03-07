"""FastAPI application entry point."""

from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

app.include_router(upload.router, prefix="/upload", tags=["upload"], dependencies=_auth)
app.include_router(query.router, prefix="/query", tags=["query"], dependencies=_auth)
app.include_router(tracks.router, prefix="/tracks", tags=["tracks"], dependencies=_auth)


@app.get("/health")
def health():
    return {"status": "ok"}
