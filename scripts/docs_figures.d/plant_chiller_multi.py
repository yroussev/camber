"""Docs figure of the ``synthetic-chw-plant-multi`` entry (0.105, #145): the
``plant-chiller-multi`` exercise's plant. Loaded by ``scripts/docs_figures.py`` (see its
"Per-entry figure modules")."""


def register(df) -> None:
    @df.figure("workbook/plant-chiller-multi.png")
    def _wb_chiller_multi():
        """Three of the exercise's towers (#145): the approach each holds against its fan speed.
        The clean tower spreads across fan speeds at about 7 °F, the controlled fouled tower
        crowds toward full fan at the same approach, the badly fouled tower sits at 100 % with a
        wide approach. Only the samples right of the 90 % line are judged by
        cooling_tower_approach."""
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=df.STD)
        styles = (
            ("well_run", "clean tower (TOWER__well_run)", "#4c72b0", "o"),
            ("tower_controlled", "fouled, controlled (TOWER__tower_controlled)", "#dd8452", "s"),
            ("tower_full_fan", "badly fouled, full fan (TOWER__tower_full_fan)", "#c44e52", "^"),
        )
        for scenario, label, color, marker in styles:
            raw = df._synthetic_plant("chw_plant_multi", 145, scenario).dropna(subset=["CWS_T"])
            ax.scatter(
                raw["CT_FAN"],
                raw["CWS_T"] - raw["WB"],
                s=9,
                alpha=0.55,
                color=color,
                marker=marker,
                label=label,
                linewidths=0,
            )
        ax.axvline(90.0, color="0.35", linestyle="--", linewidth=1)
        ax.axhline(10.0, color="0.35", linestyle=":", linewidth=1)
        ax.text(89.0, 2.2, "judged at 90 % fan and above", ha="right", fontsize=8, color="0.3")
        ax.text(21.0, 10.3, "design 7 °F + 3 °F", fontsize=8, color="0.3")
        ax.set_xlabel("tower fan speed (%)")
        ax.set_ylabel("approach: CW supply − wet-bulb (°F)")
        ax.set_xlim(18, 103)
        ax.set_ylim(0, 20.5)
        ax.legend(loc="upper left", fontsize=8, frameon=False, markerscale=2)
        fig.tight_layout()
        return fig
