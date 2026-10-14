"""Docs figure of the ``synthetic-chw-plant-staging`` entry (0.104, #137): the
``plant-chiller-staging`` exercise's plant. Loaded by ``scripts/docs_figures.py`` (see its
"Per-entry figure modules")."""


def register(df) -> None:
    @df.figure("workbook/plant-chiller-staging.png")
    def _wb_chiller_staging():
        """The plant-chiller-staging exercise's own synthetic plant (#137): condenser water
        against the wet-bulb, against a reset that follows the wet-bulb plus 5 to 9 °F."""
        import matplotlib.pyplot as plt
        import numpy as np

        from camber.charts.diagnostic import DiagnosticTemplate, diagnostic_scatter
        from camber.model.roles import Role

        raw = df._synthetic_plant("chw_plant_staging", 137, "fixed_cw").dropna(subset=["CWS_T"])
        frame = raw.rename(columns={"WB": Role.WETBULB_TEMP, "CWS_T": Role.CW_SUPPLY_TEMP})

        def expected(xv):
            mid = np.clip(np.asarray(xv, float) + 7.0, 65.0, 85.0)
            return mid - 2.0, mid + 2.0

        template = DiagnosticTemplate(
            "cw_reset",
            Role.WETBULB_TEMP,
            Role.CW_SUPPLY_TEMP,
            expected,
            "wet-bulb (°F)",
            "condenser water supply (°F)",
            "PNNL re-tuning ch. 8: reset with the wet-bulb",
        )
        fig, ax = plt.subplots(figsize=df.STD)
        diagnostic_scatter(frame, template, ax=ax)
        fig.tight_layout()
        return fig
