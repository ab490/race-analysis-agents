"""
Orchestrator Agent - the entry point for all user interactions.

Receives every user message and decides how to respond:
- Routes analytical questions to the qa_agent
- Routes visualisation requests to the plot_agent
- Routes "what data do I have?" questions to the data_agent
- For complex requests, coordinates multiple agents and assembles a combined report

The orchestrator itself does not query data directly - it delegates to
specialised sub-agents and combines their outputs into a final report.
"""

import os

from google.adk.agents import Agent

from agents.data_agent.agent import root_agent as data_agent
from agents.plot_agent.agent import root_agent as plot_agent
from agents.qa_agent.agent import root_agent as qa_agent


root_agent = Agent(
    name="orchestrator",
    model=os.getenv("VERTEX_AI_MODEL", "gemini-2.0-flash-lite-001"),
    description=(
        "Entry point for all race telemetry analysis requests. "
        "Routes questions to the right specialist agent and assembles "
        "combined reports when multiple agents are needed."
    ),
    instruction="""
You are the entry point for a race telemetry analysis platform.

## Step 1 - can you answer from context?

Your context already contains: Session ID, Stat file path, Available topics and
columns, Lap boundaries, and Duration. Answer these immediately from context
without calling any sub-agent:
- How many laps / what lap numbers → count Lap boundaries entries with lap >= 1
- Session duration → read Duration from context
- Session ID → read Session ID from context

## Step 2 - otherwise route to exactly one sub-agent

- **data_agent**: user asks what data/topics/columns are available
- **qa_agent**: everything else - sensor values, speeds, lap times, sector times,
  trends, events, anomalies, correlations, plots, charts
- **plot_agent**: user wants ONLY a chart with no analysis at all

When in doubt, always route to **qa_agent**.

## Rules
- Never ask which agent to use - always decide yourself, default to qa_agent
- Never call more than one sub-agent per question

## Response format - MANDATORY
Your ENTIRE response must be a single valid JSON object with no text before or after it.
Keys: "title" (string) and "sections" (list of objects with "type"/"content" for text or "type"/"figure"/"caption" for plots).
""",
    sub_agents=[
        data_agent,
        qa_agent,
        plot_agent,
    ],
)
