"""Docs figure of the ``synthetic-hw-plant-cycling`` entry (0.104, #137): the
``plant-boiler-cycling`` exercise's plant. Loaded by ``scripts/docs_figures.py`` (see its
"Per-entry figure modules")."""


def register(df) -> None:
    @df.figure("workbook/plant-boiler-cycling.png")
    def _wb_boiler_cycling():
        """The plant-boiler-cycling exercise's own synthetic plant (#137): two days of firing."""
        import pandas as pd

        from camber.model.roles import Role

        raw = df._synthetic_plant("hw_plant_cycling", 137, "short_cycling")
        raw = raw.loc["2025-03-12":"2025-03-13"]
        frame = pd.DataFrame(
            {Role.BOILER_STATUS: raw["BLR_FIRE"].astype(float), Role.OAT: raw["OAT"]}
        )
        return df._evidence_fig(
            "boiler_short_cycle", "PLANT__short_cycling", frame, figsize=df.WIDE
        )
