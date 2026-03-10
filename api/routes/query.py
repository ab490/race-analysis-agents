"""
Query routes: natural language questions answered by the orchestrator agent.

POST /query/stream
  - Streaming: returns Server-Sent Events with status updates and the final report.
    Events: {"type": "status", "text": "..."} | {"type": "done", "report": {...}} | {"type": "error", "text": "..."}
"""

import ast
import json
import os
import re
import shutil
import tempfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from agents.qa_agent.agent import _session_ctx, _tempdir_ctx
from agents.qa_agent.agent import root_agent as qa_agent
from agents.data_agent.agent import root_agent as data_agent
from agents.orchestrator.agent import root_agent as orchestrator_agent
from tools.gcs_store import download_raw_file, load_session_meta, session_exists

router = APIRouter()


class QueryRequest(BaseModel):
    session_id: str
    message: str


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_report(text: str) -> dict:
    """Parse agent output (JSON string, possibly wrapped in markdown fences) into a report dict."""
    # Strip markdown code fences
    stripped = re.sub(r"^```[a-z]*\n?", "", text.strip(), flags=re.MULTILINE)
    stripped = re.sub(r"\n?```$", "", stripped.strip(), flags=re.MULTILINE)
    stripped = stripped.strip()

    candidates = [text.strip(), stripped]

    # Also try to extract a JSON object embedded anywhere in the text
    # (handles cases where the model outputs prose then a JSON block)
    brace_match = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if brace_match:
        candidates.append(brace_match.group(0))

    for candidate in candidates:
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return _fix_figures(result)
        except Exception:
            pass
        try:
            result = ast.literal_eval(candidate)
            if isinstance(result, dict):
                return _fix_figures(result)
        except Exception:
            pass

    return {"title": "Response", "sections": [{"type": "text", "content": text}]}


def _fix_figures(report: dict) -> dict:
    """If any plot section has a figure that's a JSON string, parse it into a dict."""
    for section in report.get("sections", []):
        if section.get("type") == "plot" and isinstance(section.get("figure"), str):
            try:
                section["figure"] = json.loads(section["figure"])
            except Exception:
                try:
                    section["figure"] = ast.literal_eval(section["figure"])
                except Exception:
                    pass
    return report


def _tool_label(tool_name: str) -> str:
    """Return a human-readable status string for a tool call."""
    labels = {
        "get_topic_file": "Downloading topic data…",
        "describe_uploaded_files": "Discovering available data…",
        "stats_for_column": "Computing statistics…",
        "signal_over_time": "Reading time series…",
        "events_above_threshold": "Detecting events…",
        "correlate_signals": "Correlating signals…",
        "stats_for_zone": "Analysing track zone…",
        "list_zones": "Listing track zones…",
        "resolve_lap_window": "Resolving lap window…",
        "summarise_lap_times": "Summarising lap times…",
        "stint_trend": "Computing stint trend…",
        "sector_times": "Computing sector times…",
        "detect_anomalies": "Detecting anomalies…",
        "compute_resultant": "Computing resultant magnitude…",
        "plot_time_series": "Generating time series chart…",
        "plot_lap_overlay": "Generating lap overlay chart…",
        "plot_track_map": "Generating track map…",
        "plot_gg_diagram": "Generating GG diagram…",
        "load_aligned_session": "Loading session data…",
        "get_zone_windows_for_plot": "Getting zone windows…",
        "get_stats_for_annotation": "Computing annotation stats…",
        "plot_time_series_overlay": "Generating overlay chart…",
        "plot_xy": "Generating XY chart…",
    }
    return labels.get(tool_name, f"Running {tool_name}…")


async def _prepare_context(
    session_id: str, message: str, temp_dir: str
) -> tuple[str, str | None]:
    """
    Load session metadata, download only the stat file, and build the context
    string passed to the orchestrator. Returns (context, stat_file_path).
    Other topic files are downloaded on demand via get_topic_file().
    """
    lap_boundaries, schema = load_session_meta(session_id)

    stat_file_path = None
    try:
        stat_file_path = download_raw_file(session_id, "_stat", target_dir=temp_dir)
    except FileNotFoundError:
        pass

    t_start, t_end = schema.get("time_range", [None, None])
    duration = round(t_end - t_start, 1) if t_start and t_end else "unknown"
    columns_by_topic = schema.get("columns_by_topic", {})

    context = (
        f"Session ID: {session_id}\n"
        f"Stat file path (enriched — has lat, lon, zone, lap columns): {stat_file_path}\n"
        f"Available topics and columns:\n{json.dumps(columns_by_topic, indent=2)}\n"
        f"Lap boundaries: {lap_boundaries}\n"
        f"Duration: {duration}s\n\n"
        f"IMPORTANT: To get the file path for any topic other than the stat file, "
        f"call get_topic_file(topic_name) — it downloads the file and returns its local path.\n"
        f"Use your tools directly to answer the question. Do NOT mention routing or sub-agents.\n\n"
        f"User question: {message}"
    )
    return context, stat_file_path


_PLOT_TOOLS = {"plot_time_series", "plot_lap_overlay", "plot_track_map", "plot_gg_diagram", "plot_xy"}

_SCHEMA_KEYWORDS = {"what data", "what topics", "what columns", "what files", "available topics",
                    "available columns", "what is available", "what do i have", "data available"}


def _pick_agent(message: str):
    """Pick the right agent directly without an LLM routing call."""
    lower = message.lower()
    if any(kw in lower for kw in _SCHEMA_KEYWORDS):
        return data_agent
    return qa_agent


def _make_runner(agent) -> tuple[Runner, InMemorySessionService]:
    session_service = InMemorySessionService()
    runner = Runner(
        agent=agent,
        app_name="race-analysis",
        session_service=session_service,
    )
    return runner, session_service


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/stream")
async def stream_ask(request: QueryRequest):
    """
    Streaming query — returns Server-Sent Events.

    Event types:
      {"type": "status", "text": "..."}   — tool call in progress
      {"type": "done",   "report": {...}} — final report dict
      {"type": "error",  "text": "..."}   — error occurred
    """
    if not session_exists(request.session_id):
        raise HTTPException(
            status_code=404,
            detail=f"Session '{request.session_id}' not found. Upload it first via /upload/session.",
        )

    async def event_generator():
        temp_dir = tempfile.mkdtemp(prefix="race_query_")
        context, _ = await _prepare_context(request.session_id, request.message, temp_dir)

        # Set per-request context so get_topic_file() knows which session/dir to use
        token_session = _session_ctx.set(request.session_id)
        token_tempdir = _tempdir_ctx.set(temp_dir)

        agent = _pick_agent(request.message)
        runner, session_service = _make_runner(agent)
        adk_session = await session_service.create_session(app_name="race-analysis", user_id="user")

        response_text = ""
        captured_figures: list[dict] = []
        try:
            async for event in runner.run_async(
                user_id="user",
                session_id=adk_session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=context)]),
            ):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "function_call") and part.function_call:
                            label = _tool_label(part.function_call.name)
                            yield f"data: {json.dumps({'type': 'status', 'text': label})}\n\n"
                        elif hasattr(part, "function_response") and part.function_response:
                            # Intercept plot tool results directly — don't rely on the LLM
                            # to embed the large figure dict in its text response.
                            fn = part.function_response
                            if fn.name in _PLOT_TOOLS:
                                resp = fn.response if isinstance(fn.response, dict) else {}
                                if "data" in resp and "layout" in resp:
                                    captured_figures.append(resp)
                        elif hasattr(part, "text") and part.text and not event.is_final_response():
                            snippet = part.text.strip().splitlines()[0][:120]
                            yield f"data: {json.dumps({'type': 'status', 'text': snippet})}\n\n"

                if event.is_final_response() and event.content:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            response_text += part.text

            # Parse whatever text the model returned
            report = _parse_report(response_text) if response_text.strip() else {
                "title": "Analysis", "sections": [],
            }

            # Inject captured figures that the model failed to embed itself
            already_embedded = sum(1 for s in report.get("sections", []) if s.get("type") == "plot")
            for fig in captured_figures[already_embedded:]:
                report.setdefault("sections", []).append({"type": "plot", "figure": fig, "caption": ""})

            yield f"data: {json.dumps({'type': 'done', 'report': report})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"
        finally:
            _session_ctx.reset(token_session)
            _tempdir_ctx.reset(token_tempdir)
            shutil.rmtree(temp_dir, ignore_errors=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
