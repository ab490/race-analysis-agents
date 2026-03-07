"""
Query route — receives user questions and routes them through the orchestrator.

POST /query/ask
  - Takes a user message + session_id
  - Loads session data from GCS
  - Passes to orchestrator agent
  - Returns a report dict (title + text/plot sections)
"""

import os

from fastapi import APIRouter, HTTPException
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from agents.orchestrator.agent import root_agent
from tools.gcs_store import download_raw_file, list_raw_files, load_session, session_exists

router = APIRouter()


class QueryRequest(BaseModel):
    session_id: str
    message: str


@router.post("/ask")
async def ask(request: QueryRequest):
    """
    Send a natural language question about a session to the orchestrator agent.

    Args:
        session_id: The session ID to query (must have been uploaded and processed).
        message:    The user's question in plain English.

    Returns:
        A report dict with title and sections (text and/or plot).
    """
    if not session_exists(request.session_id):
        raise HTTPException(
            status_code=404,
            detail=f"Session '{request.session_id}' not found. Upload it first via /upload/session.",
        )

    _, lap_boundaries, schema = load_session(request.session_id)

    # Download all raw topic files from GCS to local temp paths for agents to use
    topics = list_raw_files(request.session_id)
    file_paths = []
    for topic in topics:
        try:
            file_paths.append(download_raw_file(request.session_id, topic))
        except FileNotFoundError:
            pass  # topic listed but blob missing — skip

    t_start, t_end = schema.get("time_range", [None, None])
    duration = round(t_end - t_start, 1) if t_start and t_end else "unknown"

    # Build context message with session metadata and local file paths
    context = (
        f"Session ID: {request.session_id}\n"
        f"File paths (local): {file_paths}\n"
        f"Available topics: {schema.get('topics', [])}\n"
        f"Lap boundaries: {lap_boundaries}\n"
        f"Duration: {duration}s\n\n"
        f"User question: {request.message}"
    )

    # Run the orchestrator agent
    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="race-analysis",
        session_service=session_service,
    )

    adk_session = await session_service.create_session(
        app_name="race-analysis",
        user_id="user",
    )

    response_text = ""
    try:
        async for event in runner.run_async(
            user_id="user",
            session_id=adk_session.id,
            new_message=types.Content(
                role="user",
                parts=[types.Part(text=context)],
            ),
        ):
            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        response_text += part.text
    finally:
        # Clean up temp files downloaded from GCS
        import os as _os
        for path in file_paths:
            try:
                _os.unlink(path)
            except OSError:
                pass

    # The orchestrator returns a report dict as text — parse it
    import ast
    try:
        report = ast.literal_eval(response_text)
    except Exception:
        # If the agent returned plain text rather than a dict, wrap it
        report = {
            "title": "Response",
            "sections": [{"type": "text", "content": response_text}],
        }

    return report
