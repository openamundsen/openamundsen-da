"""Render the compact, printable report for one generated assimilation schedule."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import groupby
from math import ceil
from pathlib import Path
from textwrap import wrap

from matplotlib.font_manager import FontProperties
from matplotlib.textpath import TextToPath

from matplotlib import rc_context
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from openamundsen_da.methods.viz.plots.common import apply_month_interval_axis_labels
from openamundsen_da.methods.viz.reports.project_collection_pdf import (
    A4_PORTRAIT_IN,
    _draw_page_number,
    _save_pdf_page,
)
from openamundsen_da.methods.viz.theme import DA_VARIABLE_STYLES

from .selection import Row, Rule
from .timeline import TimelinePanel, _panel_data

_NAMES = {
    "station_hs": ("Station snow depth", "HS"),
    "station_swe": ("Station snow water equivalent", "SWE"),
    "scf": ("Fractional snow cover", "SCF"),
    "wet_snow": ("Wet snow fraction", "WSF"),
    "wet_snow_line": ("Wet-snow line altitude", "WSLA"),
}
_REASONS = {
    "no observations within timing tolerance and active period": "No acquisition in window",
    "no observation passed quality screening": "Failed quality screening",
    "all eligible observations withheld": "Eligible observations withheld",
    "no eligible observation meets minimum spacing": "Minimum spacing",
    "no free eligible date within the original constraints": "No free eligible date",
    "same-date rescheduling disabled": "Rescheduling disabled",
}
_INK, _GREY, _LINE = "#263238", "#52656e", "#90a4ae"


def _short(variable: str) -> str:
    return _NAMES[variable][1] if variable in _NAMES else variable.replace("_", " ")


def _variable_order(variable):
    return (list(_NAMES).index(variable) if variable in _NAMES else len(_NAMES), variable)


def _day(value) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _period(start, end) -> str:
    first, last = _day(start), _day(end)
    if not first or not last:
        return "Unspecified"
    left = first.strftime("%d %b %Y" if first.year != last.year else "%d %b")
    return f"{left.lstrip('0')} – {last.strftime('%d %b %Y').lstrip('0')}"


class _Pages:
    """Flow text and chart blocks down A4 pages, with a running title on continuations."""

    def __init__(self, subtitle):
        self.figures = []
        self.subtitle = subtitle
        self.new_page()

    def new_page(self):
        self.fig = Figure(figsize=A4_PORTRAIT_IN)
        self.figures.append(self.fig)
        self.y = 0.955
        self.row([(0.065, "Assimilation schedule", 48)], size=17, bold=True, gap=0.014)
        self.row(
            [(0.065, self.subtitle if len(self.figures) == 1 else "Continued", 120)],
            size=10,
            color=_GREY,
            gap=0.020,
        )

    def ensure(self, height):
        if self.y - height < 0.055:
            self.new_page()

    def row(self, cells, *, size=8.5, bold=False, color=_INK, gap=0.009):
        # Measure each wrapped cell in PDF points, including long manual labels.
        font = FontProperties(family="DejaVu Sans", size=size, weight="bold" if bold else "normal")
        metrics = TextToPath()
        columns = []
        for (x, value, width), right in zip(cells, [*(c[0] for c in cells[1:]), 0.94], strict=True):
            available = (right - x) * A4_PORTRAIT_IN[0] * 72 - 4
            while True:
                lines = wrap(str(value), width) or [""]
                longest = max(metrics.get_text_width_height_descent(line, font, False)[0] for line in lines)
                if longest <= available or width == 1:
                    break
                width = max(1, min(width - 1, int(width * available / longest)))
            columns.append((x, lines))
        line_height = size * 1.38 / (72 * A4_PORTRAIT_IN[1])
        for i in range(max(len(lines) for _, lines in columns)):
            self.ensure(line_height)
            for x, lines in columns:
                if i < len(lines):
                    self.fig.text(
                        x,
                        self.y,
                        lines[i],
                        va="top",
                        color=color,
                        fontsize=size,
                        weight="bold" if bold else "normal",
                        parse_math=False,
                    )
            self.y -= line_height
        self.y -= gap

    def section(self, title, min_body=0.04):
        self.ensure(0.061 + min_body)
        self.y -= 0.012
        self.row([(0.065, title.upper(), 80)], size=8, bold=True, gap=0.003)
        self.fig.add_artist(
            Line2D(
                [0.065, 0.935], [self.y, self.y], transform=self.fig.transFigure, color=_LINE, linewidth=0.65
            )
        )
        self.y -= 0.016


def _selection_table(pages, summary, targets):
    pages.section("Observation selection")
    xs = [0.065, 0.422, 0.685, 0.775, 0.875]
    widths = [48, 32, 8, 8, 8]

    def row(values, *, bold=False):
        pages.row(list(zip(xs, values, widths, strict=True)), bold=bold)

    headers = ["Variable", "Active period", "Targets", "Updates", "Missed"]
    row(headers, bold=True)
    total_requested = total_selected = 0
    records = list(summary)
    if not records:
        for var in sorted({str(r["variable"]) for r in targets}):
            matches = [r for r in targets if r["variable"] == var]
            records.append(
                dict(variable=var, requested=len(matches), selected=sum(bool(r.get("date")) for r in matches))
            )
    for item in sorted(
        records,
        key=lambda r: (
            _variable_order(r["variable"]),
            str(r.get("period_start", "")),
        ),
    ):
        if pages.y < 0.12:
            pages.new_page()
            pages.section("Observation selection (continued)")
            row(headers, bold=True)
        var = item["variable"]
        name = f"{_NAMES[var][0]} ({_short(var)})" if var in _NAMES else _short(var)
        requested, selected = int(item.get("requested") or 0), int(item.get("selected") or 0)
        row(
            [
                name,
                _period(item.get("period_start"), item.get("period_end")),
                requested,
                selected,
                requested - selected,
            ]
        )
        total_requested += requested
        total_selected += selected
    row(["Total", "", total_requested, total_selected, total_requested - total_selected], bold=True)


def _timeline(pages, targets, withheld, rules):
    panel = TimelinePanel(
        "",
        {"Updates": [r for r in targets if r.get("date") or r.get("target_date")]},
        [r for r in withheld if r.get("date")] if withheld is not None else None,
    )
    groups, labels = _panel_data(panel)
    variables = sorted(
        {var for _, var, _ in groups},
        key=_variable_order,
    )
    pages.section(
        "Assimilation updates and withheld acquisitions" if withheld is not None else "Assimilation updates",
        min_body=0.14,
    )
    if not groups:
        pages.row([(0.065, "No dated observations or targets.", 95)], color=_GREY)
        return
    colors = {v: DA_VARIABLE_STYLES.get(v, {}).get("line", f"C{i % 10}") for i, v in enumerate(variables)}
    missed = any(not actual for _, _, actual in groups)
    handles = [
        Line2D([], [], ls="", marker="o", color=colors[v], markersize=4, label=_short(v)) for v in variables
    ]
    if missed:
        handles.append(Line2D([], [], ls="", marker="x", color=_INK, markersize=5, label="Missed target"))
    legend_height = 0.020 * ceil(len(handles) / 4)
    pages.ensure(legend_height + 0.127)
    y = pages.y - legend_height - 0.081
    chart = pages.fig.add_axes([0.19, y, 0.737, 0.076])
    for (i, var, actual), days in groups.items():
        offset = 0.44 * (variables.index(var) / (len(variables) - 1) - 0.5) if len(variables) > 1 else 0
        chart.scatter(
            days,
            [i + offset] * len(days),
            marker="o" if actual else "x",
            s=14,
            color=colors[var],
            linewidths=0.8,
            zorder=3,
            clip_on=False,
        )
    chart.set_yticks(range(len(labels)), labels)
    chart.set_ylim(len(labels) - 0.5, -0.5)
    days = [day for dates in groups.values() for day in dates]
    start = min([*days, *(r.start for r in rules)])
    end = max([*days, *(r.end for r in rules)]) + timedelta(days=1)
    if (end - start).days >= 56:
        apply_month_interval_axis_labels(
            chart,
            (start, end),
            interval=max(1, ceil((end - start).days / 450)),
            include_year_on_first=True,
            labelsize=7,
        )
    else:
        locator = AutoDateLocator(minticks=2, maxticks=6)
        chart.xaxis.set_major_locator(locator)
        chart.xaxis.set_major_formatter(ConciseDateFormatter(locator))
        chart.set_xlim(start, end)
        chart.tick_params(axis="x", labelsize=7)
    chart.tick_params(axis="y", length=0, labelsize=7)
    chart.grid(True, ls=":", lw=0.5, color=_LINE)
    chart.set_axisbelow(True)
    for spine in chart.spines.values():
        spine.set_color(_LINE)
    chart.legend(
        handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=4, frameon=False, fontsize=7
    )
    pages.y = y - 0.044


def _missed_dates(pages, targets):
    groups = defaultdict(lambda: defaultdict(list))
    for row in targets:
        if not row.get("date"):
            groups[row["variable"]][row.get("reason") or "Reason unspecified"].append(
                _day(row.get("target_date"))
            )
    pages.section("Missed targets by date")
    if not groups:
        pages.row([(0.065, "No missed targets." if targets else "No prescribed targets.", 92)], color=_GREY)
        return
    for var, reasons in sorted(groups.items()):
        pages.ensure(0.062)
        pages.row([(0.065, _short(var), 88)], bold=True, gap=0.006)
        for reason, days in reasons.items():
            if pages.y < 0.09:
                pages.new_page()
                pages.section(f"Missed targets by date · {_short(var)} (continued)")
            month_groups = groupby(sorted(d for d in days if d), key=lambda d: (d.year, d.month))
            dates = [
                f"{date(year, month, 1):%b} " + ", ".join(str(d.day) for d in entries) + f" ({year})"
                for (year, month), entries in month_groups
            ]
            if None in days:
                dates.append("Date unspecified")
            pages.row(
                [
                    (0.065, _REASONS.get(reason, reason), 32),
                    (0.366, len(days), 4),
                    (0.403, " · ".join(dates), 66),
                ],
                size=8,
                gap=0.005,
            )
        pages.y -= 0.006


def _limits_text(limits):
    names = {
        "station_count": "stations",
        "support_coverage_ratio": "valid coverage",
        "n_wet": "wet pixels",
        "wet_bands": "wet elevation bands",
    }
    result = []
    for key, value in sorted(limits.items()):
        bound, measure = key.split("_", 1)
        value = float(value)
        number = f"{value:.6g}" if measure != "support_coverage_ratio" else f"{100 * value:.6g}%"
        operator = "≥" if bound == "min" else "≤"
        unit = names.get(measure, measure.replace("_", " "))
        if value == 1 and measure in {"station_count", "n_wet", "wet_bands"}:
            unit = unit.removesuffix("s")
        result.append(f"{operator}{number} {unit}")
    return ", ".join(result)


def _criteria(pages, targets, withheld, rules):
    pages.section("Selection criteria")
    counts = Counter(r.variable for r in rules)
    for rule in rules:
        name = _short(rule.variable)
        if counts[rule.variable] > 1:
            name += f" ({_period(rule.start, rule.end)})"
        criteria = [
            f"{name} · {rule.frequency}-day targets"
            if isinstance(rule.frequency, int)
            else f"{name} · {rule.frequency} targets"
        ]
        criteria.extend(
            [
                f"±{rule.tolerance_days} days",
                f"{rule.min_spacing_days}-day minimum spacing",
                "nearest date" if rule.ranking == "nearest" else "quality ranking",
                f"priority {rule.priority}",
            ]
        )
        screens = _limits_text(rule.limits)
        if rule.variable == "wet_snow_line":
            screens = ", ".join(filter(None, ["retrievable line", screens]))
        if screens:
            criteria.append(screens)
        pages.row([(0.065, " · ".join(criteria), 108)], size=8, gap=0.005)
        effective = set()
        for row in targets:
            day = _day(row.get("target_date") or row.get("date"))
            if row["variable"] != rule.variable or not day or not rule.start <= day <= rule.end:
                continue
            raw = row.get("effective_limits")
            if raw:
                limits = json.loads(raw) if isinstance(raw, str) else raw
                if limits != rule.limits:
                    effective.add(_limits_text(limits))
        for limits in sorted(effective):
            pages.row([(0.065, f"{name} recorded effective limits · {limits}", 108)], size=8, gap=0.005)
    if not rules:
        pages.row([(0.065, "Selection rules unspecified.", 100)], color=_GREY)
    if withheld is None:
        return
    by_variable = Counter(r["variable"] for r in withheld)
    details = ", ".join(f"{n} {_short(v)}" for v, n in sorted(by_variable.items()))
    pages.row([(0.065, "Withheld for evaluation · " + (details or "None"), 105)], size=8)


def write_report_pdf(
    path: Path,
    *,
    label: str,
    schedule: str,
    targets: list[Row],
    summary: list[Row],
    withheld: list[Row] | None,
    rules: list[Rule],
) -> None:
    """Write an A4 schedule report using the selections and effective limits from this generation."""
    active = [r for r in rules if r.purpose == "assimilation" and r.schedule == schedule]
    subtitle = [label, schedule + " targets"]
    tolerances = {r.tolerance_days for r in active}
    if len(tolerances) == 1:
        subtitle.append(f"Timing tolerance ±{next(iter(tolerances))} days")
    if active:
        subtitle.append("Same-date rescheduling " + ("on" if active[0].reschedule else "off"))
    with rc_context({"font.family": "DejaVu Sans", "text.usetex": False, "pdf.fonttype": 42}):
        pages = _Pages("   |   ".join(filter(None, subtitle)))
        _selection_table(pages, summary, targets)
        _timeline(pages, targets, withheld, active)
        _missed_dates(pages, targets)
        _criteria(pages, targets, withheld, active)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = {
            "Title": f"Assimilation schedule, {label}, {schedule}",
            "CreationDate": None,
            "ModDate": None,
        }
        with PdfPages(path, metadata=metadata) as pdf:
            for i, figure in enumerate(pages.figures, 1):
                _draw_page_number(figure, page_number=i, total_pages=len(pages.figures))
                _save_pdf_page(pdf, figure)
