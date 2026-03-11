# API Documentation

This document describes the API endpoints used by the **Race Analysis Agents** platform for uploading telemetry data and querying sessions.

---

## Querying a Session

Queries are processed by AI agents and streamed back using **Server-Sent Events (SSE)**:

`POST /query/stream`

```json
{ "session_id": "rosbag2_2025_07_02-10_33_18", "message": "What was the max speed in sector 1?" }
```

**Event stream format:**

```
data: {"type": "status", "text": "Computing statistics…"}
data: {"type": "status", "text": "Generating time series chart…"}
data: {"type": "done", "report": {"title": "...", "sections": [...]}}
```

The final `done` event contains a report dict:
```json
{
  "title": "Max Speed - Sector 1",
  "sections": [
    {"type": "text", "content": "Peak speed in s1 was **67.3 mph** (lap 2)."},
    {"type": "plot", "figure": {...plotly dict...}, "caption": "Speed over time in s1"}
  ]
}
```

### Example Questions

**Data queries** (routed to `qa_agent`):
- *"What was the maximum speed in lap 3?"*
- *"When did brake pressure exceed 50 bar?"*
- *"Average brake pressure in sector 2 across all laps?"*
- *"How did lap times change across the stint?"*
- *"Were there any anomalies in lateral G-force?"*
- *"Was there any MPC failure during the session?"*
- *"Which lap had the highest peak lateral acceleration?"*
- *"Where is the car losing the most time?"*
- *"Is there degradation in lap times? Which sector is slowest?"*
- *"Compare cross-track error across laps - is the car drifting off line?"*

**Visualisation** (routed to `plot_agent` when plot/chart/graph keywords detected):
- *"Plot the GG diagram for the full session."*
- *"Show me a speed heatmap on the track map."*
- *"Chart front-left tire temperature over time."*
- *"Overlay wheel speed across all laps."*
- *"Compare wheel speed vs vehicle speed in lap 2."*

---

## Authentication

Set `API_KEY` in the environment to require authentication. When set, every request must include:

```
X-API-Key: <your key>
```

Leave `API_KEY` unset (or empty) to disable auth (useful for local development).

The web UI has an API Key field in the top-right navbar that stores the key in `localStorage`.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/upload/track` | Upload track KML + segments CSV |
| `POST` | `/upload/session` | Upload session CSVs and trigger processing |
| `GET` | `/upload/sessions` | List all processed session IDs |
| `GET` | `/upload/sessions/{id}` | Get session metadata (lap boundaries, schema) |
| `POST` | `/query/stream` | Stream a question as SSE |
| `GET` | `/tracks/` | List all uploaded track IDs |
| `GET` | `/tracks/{track_id}` | Get segment definitions for a track |
| `GET` | `/health` | Health check |

---
