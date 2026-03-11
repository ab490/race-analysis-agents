# Race Analysis Agents

AI powered telemetry analysis platform for autonomous racing data.

This system allows engineers to **upload race sessions, process multi-sensor telemetry and query driving performance using natural language.** The platform automatically generates **statistics, visualizations and insights** about vehicle behavior.

![Race Analysis Dashboard](images/speed_profile.png)
---

# Overview

Autonomous race cars generate large amounts of telemetry data across many sensors:

- wheel speed  
- tire temperature  
- brake pressure  
- vehicle position  
- planner status  
- MPC predictions  

Analyzing this data manually is time-consuming.

Race Analysis Agents uses **AI agents and automated pipelines** to:

- align multi-topic telemetry
- detect laps and track segments
- compute race statistics
- generate visualizations
- answer engineering questions about vehicle performance

---

# Features

## AI Telemetry Querying

Ask questions such as:

- *"What was the maximum speed in sector 1?"*  
- *"Which lap had the highest lateral acceleration?"*  
- *"Compare wheel speed across all laps."*

AI agents automatically run data queries and generate charts.


---

## Multi-Sensor Data Alignment

The system processes ROS2 telemetry topics and aligns them onto a unified timeline.

Pipeline includes:

- timestamp normalization  
- ENU to GPS coordinate conversion  
- lap detection  
- segment classification  

---

## Track-Aware Analysis

Track geometry is loaded using:

- KML centerline files
- segment boundary definitions

This enables queries like:

- sector analysis
- corner performance
- lap comparison

---

## Interactive Visualization

Generated reports include:

- time series charts
- GG diagrams
- track heatmaps
- lap comparisons

---
## Demo

Query: *"Plot the brake pressure in lap 2"*

<img src="images/brake_pressure_lap2.png" width="900">

The system automatically filters telemetry data for **Lap 2**, extracts the available brake pressure signals, and generates a time-series visualization of **front and rear brake pressure**. This allows engineers to analyze braking events, evaluate brake balance and understand how braking inputs evolve throughout the lap.

Query: *"Show the raceline for lap 3 colored by speed"*

<img src="images/raceline.png" width="900">

The system filters telemetry for **Lap 3** and visualizes the vehicle raceline across the track, coloring each point based on vehicle speed.  
This helps engineers identify **which turns, especially tight corners, had lower speeds** and where on the track the vehicle reached **maximum speeds**, providing insight into acceleration zones, braking regions and overall racing line performance.

Query: *"Was there any sudden jump in the localization?"*

<img src="images/localization.png" width="900">

The AI system interprets the query and automatically determines what **"localization"** refers to in the telemetry dataset. It selects the relevant signals, analyzes the data for anomalies, and produces both a **natural language explanation** and a **diagnostic plot** highlighting sudden jumps or irregular patterns in the localization signals.

This enables engineers to quickly identify potential localization failures or state estimation instability during the race session.

---

## Quick Start

```bash
# Backend
uv sync
uv run uvicorn api.main:app --reload     # API at http://localhost:8000

# Frontend (separate terminal)
cd frontend && npm install
cd frontend && npm run dev               # UI at http://localhost:5173
```

Required environment variables (`.env` in project root):
```
GCP_PROJECT_ID=project-id
GCP_REGION=project-region
VERTEX_AI_MODEL=model-name
GCS_BUCKET_NAME=bucket-name
API_KEY=                                 # optional - leave blank to disable authentication
```

---

## Upload Requirements

Two uploads are required before querying:

- **Track setup** (once per track)
- **Session telemetry files** (once per session)

For detailed upload instructions and file format specifications, see:

[Telemetry Data Pipeline Documentation](docs/data_pipeline.md)

For detailed information about **querying sessions, streaming responses and API endpoints**, see:

[Query & API Reference](docs/api.md)

---

## Repository Structure

```
race-analysis-agents/
├── agents/                         # AI agent definitions (Google ADK)
│   ├── data_agent/                 # Schema and data discovery 
│   ├── qa_agent/                   # Telemetry analysis - stats, events, lap insights
│   └── plot_agent/                 # Visualization focused queries
│
├── api/                            # FastAPI backend
│   ├── main.py                     # Application entry point
│   ├── auth.py                     # API key authentication
│   └── routes/
│       ├── upload.py               # Track and session upload endpoints
│       ├── query.py                # Query streaming and agent routing
│       └── tracks.py               # Track metadata endpoints
│
├── tools/                          # Functions used by agents and routes
│   ├── csv_loader.py               # CSV parsing and topic alignment
│   ├── lap_detector.py             # Lap detection and coordinate conversion
│   ├── query_engine.py             # Telemetry query functions
│   ├── plot_generator.py           # Plotly figure generation
│   └── gcs_store.py                # Cloud storage interface
│
├── frontend/                       # React + Vite + Tailwind web UI
│   ├── src/
│   │   ├── App.jsx                 
│   │   ├── api.js                 
│   │   ├── pages/
│   │   │   ├── ChatPage.jsx        
│   │   │   └── UploadPage.jsx      
│   │   └── components/
│   │       ├── ReportView.jsx      
│   │       └── PlotSection.jsx     
│   └── vite.config.js             
│
├── tests/                          # Pytest tests
├── docs/                           # Project documentation
├── images/                         # Images for README
├── main.py                         # Uvicorn endpoint
└── pyproject.toml                  # Python project config and dependencies (uv)    
``` 

### Key design points

- **`agents/`** - each agent is a folder with `agent.py` exposing `root_agent`. Tools are plain Python functions; ADK uses their docstrings to decide when to call them.
- **`tools/`** - pure library layer. Agents never parse CSVs directly; all data access goes through these modules.
- **`api/routes/query.py`** - routes questions by keyword: schema questions -> `data_agent`, plot/chart/graph keywords -> `plot_agent`, everything else -> `qa_agent`. Intercepts Plotly figure dicts from the ADK event stream and injects them into the final report (prevents the LLM from having to embed large figure dicts in JSON).
- **`agents/qa_agent/agent.py`** - uses ContextVars (`_session_ctx`, `_tempdir_ctx`) so `align_topics()` and `get_topic_file()` can lazily download topic CSVs from GCS on demand without passing session state through every tool call.
- **Alignment** - the stat file is always the base timeline. All other topics align to it via nearest-index lookup. No interpolation.

---

## Development

```bash
# Backend
uv sync
uv run uvicorn api.main:app --reload     # http://localhost:8000
uv run pytest                            # run tests
uv run ruff check .                      # lint
uv run ruff format .                     # format

# Frontend
cd frontend && npm install
cd frontend && npm run dev               # http://localhost:5173
cd frontend && npm run build             # production build
```
