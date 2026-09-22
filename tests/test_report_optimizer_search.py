"""build_workbook()'s optional "Optimizer Search" tab -- present only when
the scenario (an *_optimized*.yaml written by `roth_optimizer optimize
--out-scenario ... --save-iterations ....html`) names its scatter chart
file in a roth_conversions entry's `scatter_chart` field. A missing or
unusable chart file must only warn, never fail the report."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from pt import report as report_mod
from roth_optimizer import scatter_chart as scatter_chart_mod

from . import _helpers as h

_LOG = [
    {"lifetime_taxes": 100000.0, "after_tax_estate_value": 900000.0},
    {"lifetime_taxes": 150000.0, "after_tax_estate_value": 950000.0},
    {"lifetime_taxes": 120000.0, "after_tax_estate_value": 940000.0},
]
_AGE_PROJECTIONS = {
    "winner": [{"age": 85, "lifetime_taxes": 160000.0, "after_tax_estate_value": 990000.0}],
    "zero_conversions": [{"age": 85, "lifetime_taxes": 90000.0, "after_tax_estate_value": 870000.0}],
    "original": None,
}


class OptimizerSearchTabTests(unittest.TestCase):
    def setUp(self):
        self._db = h.TempDB()
        self.conn = h.seeded_connection(self._db.__enter__())
        self.snapshot_id = h.import_fidelity_household(self.conn)
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        self._db.__exit__(None, None, None)

    def _write_chart(self, name="search.html"):
        html = scatter_chart_mod.render_scatter_html(
            _LOG, winner=(150000.0, 950000.0), zero_point=(80000.0, 850000.0),
            original_point=(120000.0, 910000.0), heir_tax_rate=0.24, scenario_label="s.yaml",
            seed=1, restarts=1, evaluations=len(_LOG), winner_age=82, age_projections=_AGE_PROJECTIONS,
        )
        (self.dir / name).write_text(html, encoding="utf-8")

    def _scenario_path(self, scatter_chart=None):
        text = Path(h.TEST_SCENARIO).read_text()
        entry = ("\nroth_conversions:\n  - owner: jordan\n    start_date: 2032-01-01\n"
                 "    end_date: 2035-12-31\n    constraints: {max_marginal_bracket: 24, "
                 "max_conversion_per_year: 50000}\n")
        if scatter_chart:
            entry += f"    scatter_chart: {scatter_chart}\n"
        path = self.dir / "scenario_optimized_24.yaml"
        path.write_text(text + entry)
        return path

    def _build(self, scenario_path):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            wb = report_mod.build_workbook(self.conn, self.snapshot_id, profile_path=h.TEST_PROFILE,
                                            scenario_path=scenario_path, public=True)
        return wb, stderr.getvalue()

    def test_tab_added_with_native_chart_and_link_when_the_chart_file_exists(self):
        self._write_chart()
        wb, warnings = self._build(self._scenario_path(scatter_chart="search.html"))
        self.assertEqual(warnings, "")
        self.assertIn("Optimizer Search", wb.sheetnames)
        ws = wb["Optimizer Search"]
        self.assertEqual(len(ws._charts), 1)
        self.assertTrue(ws["A2"].hyperlink.target.endswith("/search.html"))
        labels = [row[0] for row in ws.iter_rows(min_row=6, max_row=12, max_col=1, values_only=True)]
        self.assertEqual(labels[:3], ["Optimized (winner)", "No conversions", "Current plan"])
        self.assertIn("Optimized @ age 85", labels)
        # every candidate schedule ends up in the sheet's own table
        candidate_rows = [r for r in ws.iter_rows(min_col=1, max_col=3, values_only=True)
                          if isinstance(r[0], int) and r[1] in (100000, 150000, 120000)]
        self.assertEqual(len(candidate_rows), len(_LOG))

    def test_chart_area_plot_area_and_legend_match_the_requested_dimensions(self):
        """Locks in the exact geometry a person asked for by eye (chart
        area, inner plot area/margins, legend box) so a future refactor of
        _add_optimizer_search_sheet() can't silently drift back to Excel's
        own auto-layout -- see that function's own comments for the
        fraction-of-chart-area math each assertion below mirrors."""
        self._write_chart()
        wb, _ = self._build(self._scenario_path(scatter_chart="search.html"))
        chart = wb["Optimizer Search"]._charts[0]

        # Chart area: 10in x 5in (openpyxl height/width are centimeters).
        self.assertAlmostEqual(chart.width, 25.40, places=2)
        self.assertAlmostEqual(chart.height, 12.70, places=2)

        # Inner plot area: 8.5in wide (1in left margin) x 3.75in tall,
        # leaving a 0.5in top margin (for the centered title) and a
        # 0.75in bottom margin -- all as fractions of the 10in x 5in
        # chart area above.
        plot = chart.layout.manualLayout
        self.assertEqual(plot.layoutTarget, "inner")
        self.assertAlmostEqual(plot.x, 0.10, places=2)
        self.assertAlmostEqual(plot.y, 0.10, places=2)
        self.assertAlmostEqual(plot.w, 0.85, places=2)
        self.assertAlmostEqual(plot.h, 0.75, places=2)

        # Legend: 7in x 1in, overlaid in the upper right of the plot area
        # above (right edge at plot.x + plot.w, top edge at plot.y), opaque.
        self.assertTrue(chart.legend.overlay)
        legend = chart.legend.layout.manualLayout
        self.assertAlmostEqual(legend.w, 0.70, places=2)
        self.assertAlmostEqual(legend.h, 0.20, places=2)
        self.assertAlmostEqual(legend.x, 0.25, places=2)
        self.assertAlmostEqual(legend.y, 0.10, places=2)
        self.assertEqual(chart.legend.graphicalProperties.solidFill.srgbClr, "FFFFFF")

        # Title: left to Excel/LibreOffice's own automatic placement (no
        # manualLayout) -- centers by default, and now fits the 0.5in top
        # margin above without colliding with the legend. Just the font
        # size is asserted here; see _add_optimizer_search_sheet()'s own
        # comments for why this is 11pt, not _add_line_chart()'s 16pt.
        self.assertIsNone(chart.title.layout)
        title_run = chart.title.tx.rich.p[0].r[0]
        self.assertEqual(title_run.rPr.sz, 1100)

    def test_missing_chart_file_only_warns(self):
        wb, warnings = self._build(self._scenario_path(scatter_chart="not_there.html"))
        self.assertNotIn("Optimizer Search", wb.sheetnames)
        self.assertIn("WARNING", warnings)
        self.assertIn("not_there.html", warnings)
        self.assertIn("Projection", wb.sheetnames)  # the rest of the report is unaffected

    def test_unusable_chart_file_only_warns(self):
        (self.dir / "search.html").write_text("<html>not a chart</html>")
        wb, warnings = self._build(self._scenario_path(scatter_chart="search.html"))
        self.assertNotIn("Optimizer Search", wb.sheetnames)
        self.assertIn("WARNING", warnings)

    def test_no_scatter_chart_field_means_no_tab_and_no_warning(self):
        wb, warnings = self._build(self._scenario_path())
        self.assertNotIn("Optimizer Search", wb.sheetnames)
        self.assertEqual(warnings, "")


if __name__ == "__main__":
    unittest.main()
