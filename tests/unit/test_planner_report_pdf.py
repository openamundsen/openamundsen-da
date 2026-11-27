"""Content and pagination contracts for the printable observation schedule."""

from datetime import date, timedelta

from openamundsen_da.planner import report_pdf
from openamundsen_da.planner.selection import Rule


def test_pdf_is_reproducible_without_evaluation(tmp_path):
    args = dict(label="2022/23", schedule="monthly", targets=[], summary=[], withheld=None, rules=[])
    first, second = tmp_path / "monthly.pdf", tmp_path / "copy.pdf"
    report_pdf.write_report_pdf(first, **args)
    report_pdf.write_report_pdf(second, **args)
    assert first.read_bytes().startswith(b"%PDF")
    assert first.read_bytes() == second.read_bytes()


def test_selection_details_survive_page_breaks(tmp_path, monkeypatch):
    origin = date(2022, 10, 1)
    rules = []
    targets = []
    summary = []
    for i in range(45):
        day = origin + timedelta(days=i)
        rules.append(
            Rule(
                "assimilation",
                "scf",
                day,
                day,
                schedule="daily",
                frequency=1,
                priority=1,
                limits={"min_support_coverage_ratio": 0.1},
            )
        )
        targets.append(
            dict(
                variable="scf",
                target_date=day,
                date=day if i == 0 else "",
                reason="" if i == 0 else "Failed manual check",
                effective_limits='{"min_support_coverage_ratio": 0.25}',
            )
        )
        summary.append(
            dict(variable="scf", period_start=day, period_end=day, requested=1, selected=int(i == 0))
        )

    pages = []
    monkeypatch.setattr(report_pdf, "_save_pdf_page", lambda pdf, fig: pages.append(fig))
    report_pdf.write_report_pdf(
        tmp_path / "report.pdf",
        label="Test basin",
        schedule="daily",
        targets=targets,
        summary=summary,
        withheld=[],
        rules=rules,
    )
    assert len(pages) > 1
    text = " ".join(item.get_text() for page in pages for item in page.texts)
    assert "SELECTION CRITERIA" in text
    assert "Failed manual check" in text
    assert "recorded effective limits · ≥25% valid coverage" in text
    assert "Withheld for evaluation · None" in text
    for i, page in enumerate(pages, 1):
        assert page.texts[-1].get_text() == f"{i} / {len(pages)}"
        assert all(0.05 < item.get_position()[1] < 0.98 for item in page.texts[:-1])
