"""Docs figure of the ``synthetic-ahu-excess-oa`` entry (0.108, Track C2): the
``air-excess-oa-cost`` exercise's unit holding excess outside air. Loaded by
``scripts/docs_figures.py`` (see its "Per-entry figure modules")."""


def register(df) -> None:
    @df.figure("workbook/air-excess-oa-cost.png")
    def _wb_air_excess_oa_cost():
        """The air-excess-oa-cost exercise's own synthetic unit: the outdoor_air_fraction evidence
        (OA fraction against outdoor temperature) of AHU__excess_oa at its declared 20 % minimum."""
        import pandas as pd

        from camber.model.roles import Role

        raw = df._synthetic_plant("ahu_excess_oa", 195, "excess_oa")
        frame = pd.DataFrame(
            {
                Role.OAT: raw["OAT"],
                Role.RETURN_AIR_TEMP: raw["RAT"],
                Role.MIXED_AIR_TEMP: raw["MAT"],
                Role.OA_DAMPER: raw["OA_DMPR"],
                Role.SUPPLY_FAN_STATUS: raw["SF_STS"].astype(float),
                Role.OCCUPANCY: raw["OCC"].astype(float),
            }
        )
        return df._evidence_fig(
            "outdoor_air_fraction",
            "AHU__excess_oa",
            frame,
            figsize=df.WIDE,
            params={"min_oa_pct": 20.0},
        )
