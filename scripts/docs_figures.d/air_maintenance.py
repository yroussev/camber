"""Docs figure of the ``synthetic-ahu-maintenance`` entry (0.105, #146): the ``air-maintenance``
exercise's hunting valve. Loaded by ``scripts/docs_figures.py`` (see its "Per-entry figure
modules")."""


def register(df) -> None:
    @df.figure("workbook/air-maintenance.png")
    def _wb_air_maintenance():
        """The air-maintenance exercise's own synthetic unit (#146): a morning of a hunting
        cooling valve at the trend's 5-minute resolution."""
        import pandas as pd

        from camber.model.roles import Role

        raw = df._synthetic_plant("ahu_maintenance", 146, "hunting")
        raw = raw.loc["2025-06-04 05:00":"2025-06-04 10:55"]
        frame = pd.DataFrame({Role.COOL_VALVE: raw["CLG_VLV"], Role.SUPPLY_AIR_TEMP: raw["SAT"]})
        return df._evidence_fig("control_hunting", "AHU__hunting", frame, figsize=df.WIDE)
