"""FastAPI application entry point."""

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import upload, query, tracks

app = FastAPI(title="Race Analysis Agents API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/upload", tags=["upload"])
app.include_router(query.router, prefix="/query", tags=["query"])
app.include_router(tracks.router, prefix="/tracks", tags=["tracks"])


@app.get("/health")
def health():
    return {"status": "ok"}
