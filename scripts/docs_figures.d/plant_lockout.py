"""Docs figure of the ``synthetic-hw-plant-lockout`` entry (0.103, #133): the ``plant-lockout``
exercise's plant. Loaded by ``scripts/docs_figures.py`` (see its "Per-entry figure modules")."""


def register(df) -> None:
    @df.figure("workbook/plant-lockout.png")
    def _wb_lockout():
        """The plant-lockout exercise's own synthetic plant (CAMBER's generator, not a download)."""
        import io

        import pandas as pd

        from camber.datasets._synthetic import GENERATORS
        from camber.model.roles import Role

        text = GENERATORS["hw_plant_lockout"].build(133)["hw_plant_lockout.csv"]
        raw = pd.read_csv(io.StringIO(text), parse_dates=["Timestamp"])
        raw = raw[raw["scenario"] == "pump_left_on"].set_index("Timestamp")
        raw = raw.loc["2025-05-19":"2025-06-08"]  # three weeks around the warm spell
        frame = pd.DataFrame(
            {
                Role.OAT: raw["OAT"],
                Role.PUMP_STATUS: raw["HWP_STS"].astype(float),
                Role.HW_PUMP_SPEED: raw["HWP_SPD"],
                Role.BOILER_STATUS: raw["BLR_FIRE"].astype(float),
            }
        )
        return df._evidence_fig(
            "hw_pump_summer_lockout", "PLANT__pump_left_on", frame, figsize=df.WIDE
        )
