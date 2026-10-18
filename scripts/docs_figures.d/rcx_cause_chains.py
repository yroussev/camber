"""Docs figure of the ``synthetic-g36-rcx-chains`` entry (0.108): the ``rcx-cause-chains``
exercise's AHU-to-VAV cause chains. Loaded by ``scripts/docs_figures.py`` (see its "Per-entry
figure modules")."""

_SCENARIOS = ("healthy", "one_bad_box", "warm_sat", "low_static", "cold_sat")


def register(df) -> None:
    @df.figure("workbook/rcx-cause-chains.png")
    def _wb_rcx_cause_chains():
        """Every box of the five air handlers, coloured by the issue the RCx report's triage puts
        its findings in (the run template's topology declared): linked into its air handler's
        chain, an issue of its own, or no finding at all."""
        import os
        import tempfile

        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch, Rectangle

        from camber import datasets
        from camber.config import run_config
        from camber.rules.builtin import builtin_registry
        from camber.rules.triage import link_findings

        with tempfile.TemporaryDirectory(prefix="camber-chains-") as tmp:
            store = os.path.join(tmp, "store")
            datasets.ingest("synthetic-g36-rcx-chains", store, data_dir=os.path.join(tmp, "c"))
            cfg = datasets.config_template(
                "synthetic-g36-rcx-chains", store, exercise="rcx-cause-chains"
            )
            res = run_config(cfg, base_dir=store)
        fired = [f for f in res.findings if f.severity in ("warn", "fault")]
        issues = link_findings(fired, rules=builtin_registry(), topology=res.topology)
        linked, own = {}, set()
        for iss in issues:
            for z in iss.zone_links:
                for box in z.boxes:
                    linked[box] = z.chain
            if not iss.zone_links and iss.equip.startswith("VAV"):
                own.add(iss.equip)
        colors = {
            "sat_high": ("#c44e52", "linked: supply air too warm"),
            "static_low": ("#dd8452", "linked: duct static too low"),
            "sat_low": ("#4c72b0", "linked: supply air too cold"),
        }
        fig, ax = plt.subplots(figsize=df.STD)
        for row, scen in enumerate(_SCENARIOS):
            y = len(_SCENARIOS) - 1 - row
            ahu_issue = any(i.equip == f"AHU__{scen}" and i.zone_links for i in issues)
            ax.text(-0.3, y + 0.4, f"AHU__{scen}", ha="right", va="center", fontsize=8)
            if ahu_issue:
                (z,) = next(i for i in issues if i.equip == f"AHU__{scen}").zone_links
                ax.text(6.25, y + 0.4, z.count(), ha="left", va="center", fontsize=8)
            for k in range(1, 7):
                box = f"VAV{k}__{scen}"
                if box in linked:
                    face = colors[linked[box]][0]
                elif box in own:
                    face = "#55a868"
                else:
                    face = "0.88"
                ax.add_patch(Rectangle((k - 1, y), 0.85, 0.8, facecolor=face, edgecolor="0.4"))
                ax.text(k - 0.575, y + 0.4, f"VAV{k}", ha="center", va="center", fontsize=7)
        handles = [Patch(facecolor=c, edgecolor="0.4", label=lab) for c, lab in colors.values()]
        handles += [
            Patch(facecolor="#55a868", edgecolor="0.4", label="an issue of its own"),
            Patch(facecolor="0.88", edgecolor="0.4", label="no finding"),
        ]
        ax.legend(
            handles=handles,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.02),
            ncol=3,
            fontsize=8,
            frameon=False,
        )
        ax.set_xlim(-2.6, 7.6)
        ax.set_ylim(-0.2, len(_SCENARIOS))
        ax.axis("off")
        fig.tight_layout()
        return fig
