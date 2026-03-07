"""
Report builder — defines the shared report output schema used by all agents.

Every agent response that goes back to the user is structured as a Report.
A Report contains ordered sections — each section is either text or a Plotly figure.
The frontend renders these sections interleaved (text + charts + text + charts...).

Usage:
    builder = ReportBuilder("Brake Analysis — Lap 3")
    builder.add_text("Front brake pressure peaked at 62 bar during turn 3.")
    builder.add_plot(fig_dict)
    builder.add_text("Rear brake pressure remained below 20 bar throughout.")
    report = builder.build()
"""

from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Section types
# ---------------------------------------------------------------------------

@dataclass
class TextSection:
    type: Literal["text"] = "text"
    content: str = ""


@dataclass
class PlotSection:
    type: Literal["plot"] = "plot"
    figure: dict = field(default_factory=dict)  # Plotly figure JSON dict
    caption: str = ""                            # Optional caption below the chart


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class Report:
    title: str
    sections: list[TextSection | PlotSection] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise report to a JSON-safe dict for API responses."""
        return {
            "title": self.title,
            "sections": [
                {
                    "type": s.type,
                    **({"content": s.content} if isinstance(s, TextSection) else {}),
                    **({"figure": s.figure, "caption": s.caption} if isinstance(s, PlotSection) else {}),
                }
                for s in self.sections
            ],
        }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class ReportBuilder:
    """
    Fluent builder for assembling a Report section by section.

    Example:
        builder = ReportBuilder("Speed Analysis")
        builder.add_text("Max speed was 68.4 mph in lap 3.")
        builder.add_plot(fig, caption="Speed trace — lap 3")
        report = builder.build()
        return report.to_dict()
    """

    def __init__(self, title: str):
        self._title = title
        self._sections: list[TextSection | PlotSection] = []

    def add_text(self, content: str) -> "ReportBuilder":
        """Add a text paragraph to the report."""
        self._sections.append(TextSection(content=content.strip()))
        return self

    def add_plot(self, figure: dict, caption: str = "") -> "ReportBuilder":
        """
        Add a Plotly figure to the report.

        Args:
            figure:  Plotly figure as a JSON-serialisable dict.
                     Obtain via plotly.io.to_json(fig) or fig.to_dict().
            caption: Optional caption displayed below the chart.
        """
        self._sections.append(PlotSection(figure=figure, caption=caption))
        return self

    def build(self) -> Report:
        """Return the assembled Report."""
        return Report(title=self._title, sections=list(self._sections))


# ---------------------------------------------------------------------------
# Helper used by agents returning reports as tool output
# ---------------------------------------------------------------------------

def empty_report(title: str, message: str) -> dict:
    """Return a minimal report dict with a single text section — used for errors or empty results."""
    return ReportBuilder(title).add_text(message).build().to_dict()
