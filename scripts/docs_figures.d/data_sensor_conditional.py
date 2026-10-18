"""Docs figure of the ``synthetic-ahu-sensor-bias`` entry (0.108, Track C3): the
``data-sensor-conditional`` exercise's biased supply-air sensor against the duct logger. Loaded by
``scripts/docs_figures.py`` (see its "Per-entry figure modules")."""


def register(df) -> None:
    @df.figure("workbook/data-sensor-conditional.png")
    def _wb_data_sensor_conditional():
        """The data-sensor-conditional exercise's own synthetic unit: two occupied days of
        AHU__sat_bias's supply-air reading, the duct logger in the same duct and the mixed air."""
        import matplotlib.pyplot as plt

        raw = df._synthetic_plant("ahu_sensor_bias", 108, "sat_bias")
        raw = raw.loc["2025-05-20 05:00":"2025-05-21 20:45"]
        raw = raw.where(raw["SF_STS"] == 1)  # the fan-on hours: gaps where the unit is off
        fig, ax = plt.subplots(figsize=df.WIDE)
        ax.plot(raw.index, raw["SAT"], label="supply air, BAS sensor (SAT)", lw=1.4)
        ax.plot(raw.index, raw["SAT_LOGGER"], label="supply air, duct logger", lw=1.4)
        ax.plot(raw.index, raw["MAT"], label="mixed air (MAT)", lw=1.0, alpha=0.8)
        ax.plot(raw.index, raw["SAT_SP"], label="setpoint 55 °F", lw=0.8, ls="--", color="0.4")
        ax.set_ylabel("°F")
        ax.set_ylim(48, 80)
        ax.set_title("AHU__sat_bias: the loop holds the sensor at 55 °F; the air leaves at 52 °F")
        ax.legend(loc="upper right", fontsize=8, ncol=2)
        fig.autofmt_xdate()
        fig.tight_layout()
        return fig
