"""Docs figure of the ``synthetic-g36-vav-fleet`` entry (0.105, #144): the ``zone-g36-fleet``
exercise's SAT rogue-zone census. Loaded by ``scripts/docs_figures.py`` (see its "Per-entry
figure modules")."""


def register(df) -> None:
    @df.figure("workbook/zone-g36-fleet.png")
    def _wb_zone_g36_fleet():
        """The exercise's SAT rogue-zone census (#144): every box of the eight air handlers,
        placed by its share of its air handler's SAT reset requests and by the share of active
        cycles it holds the binding (largest) request, the cooling loop over 95 % counted as
        one request (generator version 2). A rogue needs both: the shaded corner.
        The equipment room of AHU__rogue_sat sits alone in it; the near miss's warm corner and
        the healthy fleet's zone 1 sit just outside."""
        import matplotlib.pyplot as plt
        import pandas as pd

        from camber.g36_reset import rogue_zone_census

        frames, groups = {}, {}
        for scenario in (
            "healthy",
            "rogue_sat",
            "rogue_static",
            "starved",
            "not_responding",
            "reheat",
            "heat_cool",
            "near_miss",
        ):
            raw = df._synthetic_plant("g36_vav_fleet", 144, scenario)
            for k in range(1, 7):
                zone = f"VAV{k}__{scenario}"
                frames[zone] = pd.DataFrame(
                    {
                        "t": raw[f"ZN{k}_T"],
                        "sp": raw[f"ZN{k}_CLG_SP"],
                        "loop": raw[f"ZN{k}_CLG_LOOP"],
                        "fan": raw["SF_STS"],
                        "occ": raw["OCC"],
                    }
                )
                groups[zone] = f"AHU__{scenario}"
        res = rogue_zone_census(
            frames,
            reset="sat",
            groups=groups,
            temp_col="t",
            cool_sp_col="sp",
            gate_cols=("fan", "occ"),
            cool_loop_col="loop",
        )
        share, binding = res.zone_request_share, res.zone_binding_frac
        fig, ax = plt.subplots(figsize=df.STD)
        ax.fill_between([1.0 / 3.0, 1.02], 0.5, 1.02, color="#c44e52", alpha=0.10, linewidth=0)
        ax.text(0.99, 0.53, "rogue: binding ≥ 50 % and share ≥ 2/6", ha="right", fontsize=8)
        rest = [
            z for z in share if z not in ("VAV1__rogue_sat", "VAV1__near_miss", "VAV1__healthy")
        ]
        ax.scatter(
            [share[z] for z in rest],
            [binding[z] for z in rest],
            s=18,
            color="0.6",
            label="the other 45 boxes",
        )
        for zone, color, marker in (
            ("VAV1__rogue_sat", "#c44e52", "^"),
            ("VAV1__near_miss", "#dd8452", "s"),
            ("VAV1__healthy", "#4c72b0", "o"),
        ):
            ax.scatter(share[zone], binding[zone], s=46, color=color, marker=marker, label=zone)
        ax.axvline(1.0 / 3.0, color="0.35", linestyle="--", linewidth=1)
        ax.axhline(0.5, color="0.35", linestyle="--", linewidth=1)
        ax.set_xlim(-0.02, 1.02)
        ax.set_ylim(-0.02, 1.02)
        ax.set_xlabel("share of its air handler's SAT reset requests")
        ax.set_ylabel("share of active cycles holding the binding request")
        ax.legend(loc="upper left", fontsize=8, frameon=False)
        fig.tight_layout()
        return fig
