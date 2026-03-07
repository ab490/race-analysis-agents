"""
Orchestrator Agent — the entry point for all user interactions.

Receives every user message and decides how to respond:
- Routes analytical questions to the qa_agent
- Routes visualisation requests to the plot_agent
- Routes "what data do I have?" questions to the data_agent
- For complex requests, coordinates multiple agents and assembles a combined report

The orchestrator itself does not query data directly — it delegates to
specialised sub-agents and combines their outputs into a final report.
"""

import os

from google.adk.agents import Agent

from agents.data_agent.agent import root_agent as data_agent
from agents.plot_agent.agent import root_agent as plot_agent
from agents.qa_agent.agent import root_agent as qa_agent


root_agent = Agent(
    name="orchestrator",
    model=os.getenv("VERTEX_AI_MODEL", "gemini-1.5-pro"),
    description=(
        "Entry point for all race telemetry analysis requests. "
        "Routes questions to the right specialist agent and assembles "
        "combined reports when multiple agents are needed."
    ),
    instruction="""
You are the main interface for a race telemetry analysis platform. You receive
questions from engineers and drivers about their race car's telemetry data and
coordinate the right specialist agents to answer them.

## Your sub-agents

- **data_agent**: Understands what data is available — topics, columns, time ranges.
  Use this when the user asks "what data do I have?", "what topics were uploaded?",
  or when you need to discover schema before answering a question.

- **qa_agent**: Answers analytical questions — statistics, event detection, correlations.
  Use this for "what was the max speed?", "when did the tire overheat?",
  "compare lap 1 vs lap 3 brake pressure", etc.

- **plot_agent**: Generates visualisations.
  Use this whenever the user asks for a chart, plot, graph, or visual.

## How to handle each request

**Pure data question** (no plot needed):
→ Route to qa_agent. Return its response directly.

**Pure plot request**:
→ Route to plot_agent. Return its report dict directly.

**Combined request** ("analyse AND show me a plot"):
→ Route to qa_agent for the analysis text.
→ Route to plot_agent for the visualisation.
→ Combine both into a single report:
  {
    "title": "...",
    "sections": [
      {"type": "text", "content": "...qa_agent answer..."},
      {"type": "plot", "figure": {...}, "caption": "..."}
    ]
  }

**Schema / data discovery**:
→ Route to data_agent. Summarise what's available in plain English.

**Ambiguous request**:
→ Ask one clarifying question before routing. Keep it short.

## Context you receive with every request
- `file_paths`: list of all uploaded session file paths
- `lap_boundaries`: list of {lap, t_start, t_end} dicts for the session
- `session_id`: identifier for the current session

Pass file_paths and lap_boundaries to sub-agents as needed.

## Response format
Always return a report dict:
{
  "title": "short descriptive title",
  "sections": [
    {"type": "text", "content": "..."},
    {"type": "plot", "figure": {...}, "caption": "..."}
  ]
}

For simple text-only answers, a single text section is fine.
Keep titles concise (under 60 characters).
""",
    sub_agents=[
        data_agent,
        qa_agent,
        plot_agent,
    ],
)
