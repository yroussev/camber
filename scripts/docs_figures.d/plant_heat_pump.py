"""Docs figure of the ``synthetic-heat-pump`` entry (0.105, #147): the ``plant-heat-pump``
exercise's air-source units. Loaded by ``scripts/docs_figures.py`` (see its "Per-entry figure
modules")."""


def register(df) -> None:
    @df.figure("workbook/plant-heat-pump.png")
    def _wb_heat_pump():
        """The plant-heat-pump exercise's own synthetic units (#147): one foggy day at 44-50 °F,
        the unit whose defrost sensor reads too cold against the healthy unit beside it."""
        import matplotlib.pyplot as plt

        from camber.charts.multitrend import fault_multitrend
        from camber.model.roles import Role

        day = "2025-02-26"
        fig, axes = plt.subplots(2, 1, figsize=(9.0, 5.2), sharex=True)
        for ax, unit in zip(axes, ("defrost_sensor", "healthy")):
            raw = df._synthetic_plant("heat_pump", 147, unit).loc[day]
            frame = raw.rename(
                columns={"RV": Role.REVERSING_VALVE_CMD, "COMP": Role.COMPRESSOR_STATUS}
            )[[Role.REVERSING_VALVE_CMD, Role.COMPRESSOR_STATUS]].astype(float)
            defrost = (frame[Role.REVERSING_VALVE_CMD] < 0.5) & (
                frame[Role.COMPRESSOR_STATUS] > 0.5
            )
            fault_multitrend(
                frame,
                [Role.REVERSING_VALVE_CMD, Role.COMPRESSOR_STATUS],
                spans={"defrost (valve reversed, compressor on)": defrost},
                ax=ax,
                title=(
                    f"ASHP__{unit}: {int(defrost.sum())} defrost samples, outdoor air "
                    f"{raw['OAT'].min():.0f}-{raw['OAT'].max():.0f} °F"
                ),
            )
            ax.set_ylim(-0.1, 1.35)
            ax.set_xlabel("")
        axes[-1].set_xlabel("Time")
        fig.tight_layout()
        return fig
