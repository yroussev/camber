"""Linked references: the free PNNL Building Re-tuning resources behind CAMBER's rules.

Provisional (0.96, #78).

CAMBER's rules and recommendations have long cited the PNNL Building Re-tuning material as plain
text ("PNNL Re-tuning Ch.8"). This module is the one registry of those resources: each entry has a
stable id, its title, publisher, document number, a public URL, a kind and the date the URL was
last checked. :data:`RULE_REFERENCES` maps a rule name to the references a reader should open to
learn more about that finding, and reports use it to add "Learn more" links and a "Further
reading" list.

**Link policy.** CAMBER links to these documents and never copies them: no text, figures or PDFs
from them are reproduced in the repository or in a report. Where a guide is mapped to a rule, the
mapping was checked against the guide's own section headings (``section``); a rule no guide clearly
covers is mapped to the relevant training chapter, or left unmapped rather than forced. The URLs
are checked weekly by ``scripts/datasets_linkcheck.py --references``.

Dependency-free (stdlib only).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

__all__ = [
    "GUIDE",
    "TRAINING",
    "TOOL_GUIDE",
    "REPORT",
    "PROJECT",
    "RELATED_TOOL",
    "KINDS",
    "Reference",
    "REFERENCES",
    "RULE_REFERENCES",
    "WALKDOWN_REFERENCES",
    "reference",
    "references_for",
    "reference_ids_for",
    "references_for_findings",
    "reference_urls",
    "links_html",
    "links_text",
]

GUIDE = "guide"  # a guide to one re-tuning measure
TRAINING = "training_chapter"  # a chapter of the re-tuning training course
TOOL_GUIDE = "tool_guide"  # a guide to an analysis tool
REPORT = "report"  # a technical report
PROJECT = "project_page"  # the project's own web pages
RELATED_TOOL = "related_tool"  # a related tool CAMBER does not ship or wrap
KINDS = (GUIDE, TRAINING, TOOL_GUIDE, REPORT, PROJECT, RELATED_TOOL)

_PNNL = "Pacific Northwest National Laboratory"
_BASE = "https://www.pnnl.gov/sites/default/files/media/file/"
_VERIFIED = "2026-09-29"
# the training course the ten chapters belong to (every chapter carries this number)
_COURSE = "Large Commercial Buildings: Re-tuning for Efficiency"
_COURSE_NO = "PNNL-SA-85063"


@dataclass(frozen=True)
class Reference:
    """One linked reference. ``section`` names the heading(s) a rule mapping was checked against
    (title and heading only; never quoted text)."""

    id: str
    title: str
    publisher: str
    number: str  # the publisher's document number ("" when it has none)
    url: str
    kind: str  # one of KINDS
    verified_on: str  # ISO date the URL last answered 200
    section: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def label(self) -> str:
        """A human label: the title plus the document number when there is one."""
        return f"{self.title} ({self.number})" if self.number else self.title

    def short(self) -> str:
        """A short link text: ``PNNL guide: <measure>``, ``PNNL re-tuning ch. N`` or the title."""
        if self.kind == GUIDE and ": " in self.title:
            return "PNNL guide: " + self.title.split(": ", 1)[1]
        if self.kind == TRAINING:
            return "PNNL re-tuning ch. " + self.id.rsplit("ch", 1)[1]
        return self.title


def _guide(rid, title, number, file, section=""):
    return Reference(
        rid,
        f"Building Re-Tuning Training Guide: {title}",
        _PNNL,
        number,
        _BASE + file,
        GUIDE,
        _VERIFIED,
        section,
    )


def _chapter(n, title, file):
    return Reference(
        f"pnnl-retuning-ch{n}",
        f"{_COURSE}, chapter {n}: {title}",
        _PNNL,
        _COURSE_NO,
        _BASE + file,
        TRAINING,
        _VERIFIED,
    )


_ALL = (
    # --- guides to the re-tuning measures ------------------------------------------------------
    _guide(
        "pnnl-guide-economizer",
        "Air-Side Economizer Operation",
        "PNNL-SA-86706",
        "pnnl_sa_86706.pdf",
        "Is the outdoor-air damper open when outdoor conditions are not favorable (outdoor-air "
        "temperature > return-air temperature)?; Does the cooling coil operate during economizer "
        "mode?",
    ),
    _guide(
        "pnnl-guide-static-pressure",
        "AHU Static Pressure Control",
        "PNNL-SA-84187",
        "pnnl_sa_84187.pdf",
        "Is there a reset-schedule for the duct static pressure?; "
        "Determine whether the static pressure set point is too high or too low",
    ),
    _guide(
        "pnnl-guide-discharge-air-temp",
        "AHU Discharge-Air Temperature Control",
        "PNNL-SA-84186",
        "pnnl_sa_84186.pdf",
        "Is reset being used to control the discharge-air set point?; Is the discharge-air "
        "temperature meeting set point, or do deviations occur?",
    ),
    _guide(
        "pnnl-guide-occupancy-scheduling",
        "Occupancy Scheduling: Night and Weekend Temperature Set back and Supply Fan Cycling "
        "during Unoccupied Hours",
        "PNNL-SA-85194",
        "pnnl_sa_85194.pdf",
        "Is there night set back for unoccupied hours?",
    ),
    _guide(
        "pnnl-guide-zone-heat-cool",
        "Zone Heating and Cooling Control",
        "PNNL-SA-85200",
        "pnnl_sa_85200.pdf",
        "Is there significant reheat occurring at the interior zones?",
    ),
    _guide(
        "pnnl-guide-plant-cooling",
        "Central Utility Plant Cooling Control",
        "PNNL-SA-89198",
        "pnnl_sa_89198.pdf",
        "Is reset utilized on the chilled water supply temperature?; "
        "Is the loop delta-T (ChWRT-ChWST) low?; "
        "Is the loop differential pressure set point constant and if so, can it be reset at "
        "partial load conditions?",
    ),
    _guide(
        "pnnl-guide-plant-heating",
        "Central Utility Plant Heating Control",
        "PNNL-SA-89222",
        "pnnl_sa_89222.pdf",
        "Is the loop delta-T (HWST-HWRT) low?; "
        "Is the hot water loop differential pressure constant and if so, can it be reset at "
        "partial load conditions?",
    ),
    _guide(
        "pnnl-guide-min-oa",
        "AHU Minimum Outdoor-Air Operation",
        "PNNL-SA-88958",
        "pnnl_sa_88958.pdf",
        "Is outdoor air sufficient for ventilation or is over-ventilation occurring?",
    ),
    _guide(
        "pnnl-guide-ahu-heat-cool",
        "AHU Heating and Cooling Control",
        "PNNL-SA-88359",
        "pnnl_sa_88359.pdf",
        "Is there simultaneous heating and cooling occurring?",
    ),
    # --- the training chapters -----------------------------------------------------------------
    _chapter(1, "Introduction", "ch1_introduction.pdf"),
    _chapter(2, "Building Personality", "ch2_building_personality.pdf"),
    _chapter(3, "Collect Initial Building Information", "ch3_collect_initial.pdf"),
    _chapter(4, "Pre-Re-Tuning Phase: Trend Data Collection and Analysis", "ch4_pre-re-tuning.pdf"),
    _chapter(
        5, "Air Handling Units: Pre-Re-Tuning and Trending and Re-Tuning", "ch5_air_handling.pdf"
    ),
    _chapter(6, "Economizer Operations: Pre-Re-Tuning and Re-Tuning", "ch6_economizer.pdf"),
    _chapter(
        7,
        "Terminal Units in Air Distribution System: Pre-Re-Tuning and Re-Tuning",
        "ch7_terminal_units.pdf",
    ),
    _chapter(8, "Central Utility Plant: Pre-Re-Tuning and Re-Tuning", "ch8_central_plant.pdf"),
    _chapter(9, "Building Walk Down", "ch9_building_walkdown.pdf"),
    _chapter(10, "Re-Tuning Building Controls and Systems", "ch10_retuning_building.pdf"),
    # --- other resources -----------------------------------------------------------------------
    Reference(
        "pnnl-trending-requirements",
        "Re-Tuning Training Guide: Trending Requirements for Re-Tuning",
        _PNNL,
        "",
        _BASE + "trending_requirements_retuning.pdf",
        GUIDE,
        _VERIFIED,
    ),
    Reference(
        "pnnl-ecam-interval-data",
        "Interval Data Analysis with the Energy Charting and Metrics Tool (ECAM)",
        _PNNL,
        "PNNL-20495",
        _BASE + "pnnl_20495.pdf",
        TOOL_GUIDE,
        _VERIFIED,
    ),
    Reference(
        "pnnl-retuning-savings-large-office",
        "Energy Savings Modeling of Standard Commercial Building Retuning Measures: Large Office "
        "Buildings",
        _PNNL,
        "PNNL-21569",
        _BASE + "pnnl_21569.pdf",
        REPORT,
        _VERIFIED,
    ),
    Reference(
        "pnnl-retuning-project",
        "Building Re-tuning (project page)",
        _PNNL,
        "",
        "https://www.pnnl.gov/projects/building-re-tuning",
        PROJECT,
        _VERIFIED,
    ),
    Reference(
        "pnnl-retuning-downloads",
        "Building Re-tuning: downloads",
        _PNNL,
        "",
        "https://www.pnnl.gov/projects/building-re-tuning/downloads",
        PROJECT,
        _VERIFIED,
    ),
    Reference(
        "ecam",
        "ECAM (Energy Charting and Metrics), an Excel-based M&V and trend-charting tool",
        "Lattice Energy Works",
        "",
        "https://latticeenergyworks.com/services/technology-market-assessment/",
        RELATED_TOOL,
        _VERIFIED,
    ),
)

#: every reference, by id (insertion order is page order on the references page)
REFERENCES: dict = {r.id: r for r in _ALL}

_CH4, _CH5, _CH6, _CH7, _CH8 = (f"pnnl-retuning-ch{n}" for n in (4, 5, 6, 7, 8))

#: rule name -> reference ids, most specific first. A rule with no clear match is absent.
RULE_REFERENCES: dict = {
    # air-side economizer and outdoor air
    "outdoor_air_fraction": ("pnnl-guide-economizer", "pnnl-guide-min-oa", _CH6),
    "economizer_high_limit": ("pnnl-guide-economizer", _CH6),
    "free_cooling_missed": ("pnnl-guide-economizer", _CH6),
    # ventilation: minimum outdoor air, DCV and 62.1
    "dcv_verification": ("pnnl-guide-min-oa", _CH5),
    "dcv_system_verification": ("pnnl-guide-min-oa", _CH5),
    "co2_ventilation": ("pnnl-guide-min-oa", _CH5),
    "co2_ventilation_system": ("pnnl-guide-min-oa", _CH5),
    "ventilation_rate_62_1": ("pnnl-guide-min-oa",),
    "ventilation_system_62_1": ("pnnl-guide-min-oa",),
    # air handler: static pressure, discharge-air temperature, heating and cooling
    "static_pressure_reset": ("pnnl-guide-static-pressure", _CH5),
    # the static guide's "too high or too low" section reads the box damper positions: most boxes
    # throttled means static is too high, most wide open means the boxes are starved (0.98, #89)
    "damper_census": ("pnnl-guide-static-pressure", _CH5, _CH7),
    "supply_air_reset": ("pnnl-guide-discharge-air-temp", _CH5),
    "supply_air_reset_compliance": ("pnnl-guide-discharge-air-temp", _CH5),
    "supply_air_control": ("pnnl-guide-discharge-air-temp", _CH5),
    "simultaneous_heat_cool": ("pnnl-guide-ahu-heat-cool", _CH5),
    "leaking_valve": ("pnnl-guide-ahu-heat-cool", _CH5),
    "control_hunting": (_CH5,),
    # scheduling
    "night_weekend_setback": ("pnnl-guide-occupancy-scheduling", _CH5),
    # zones and terminal units
    "reheat_penalty": ("pnnl-guide-zone-heat-cool", _CH7),
    "reheat_minimization_g36": ("pnnl-guide-zone-heat-cool", _CH7),
    "overcooling_min_flow": (_CH7,),
    "overcooling_severity": (_CH7,),
    "unmet_setpoint_hours": (_CH7,),
    "airflow_tracking": (_CH7,),
    "zones_heat_cool_census": (_CH7,),
    "reheat_capacity_shortfall": (_CH7,),  # 0.98 (#89)
    "cohort_airflow": (_CH7,),
    "cohort_space_temp": (_CH7,),
    # zone-request census rules (0.98, #89). The discharge-air guide's reset section bases a
    # zone-driven reset on the zones it serves, setting aside the warmest and coolest; the static
    # guide's "too high or too low" section sets the static by the most demanding boxes, leaving
    # out failed or outlier ones, and reads boxes wide open as starved.
    "sat_rogue_zone_census": ("pnnl-guide-discharge-air-temp", _CH7),
    "static_rogue_zone_census": ("pnnl-guide-static-pressure", _CH7),
    "sat_cohort_starvation": (_CH5, _CH7),
    "static_cohort_starvation": ("pnnl-guide-static-pressure", _CH5, _CH7),
    # Left unmapped (no guide or chapter clearly covers them): the G36 reset-effectiveness rules
    # (sat_/static_reset_effectiveness, trim-and-respond, a G36 sequence the guides predate),
    # filter_fouling, g36_afdd (cites Guideline 36 itself), the DX and heat-pump rules and
    # source_loop_deltat (the course covers built-up air handlers and central plants), and the
    # drift detectors other than the two plant ones mapped below.
    # central plant, cooling
    "chw_plant_reset": ("pnnl-guide-plant-cooling", _CH8),
    "chw_pump_dp_reset": ("pnnl-guide-plant-cooling", _CH8),
    "chw_supply_tracking": (_CH8,),
    "chiller_efficiency": (_CH8,),
    "chiller_staging": (_CH8,),
    "condenser_water_reset": (_CH8,),
    "cooling_tower_approach": (_CH8,),
    "condenser_bypass_leak": (_CH8,),  # 0.98 (#89): chiller, condenser-water and tower topics
    "chiller_approach_fouling": (_CH8,),
    "chiller_staging_fleet": (_CH8,),
    "cooling_tower_fan_effort_drift": (_CH8,),
    # central plant, heating
    "hw_plant_deltat": ("pnnl-guide-plant-heating", _CH8),
    "hw_pump_dp_reset": ("pnnl-guide-plant-heating", _CH8),
    "boiler_short_cycle": (_CH8,),
    "boiler_summer_lockout": (_CH8,),
    "boiler_efficiency_drift": (_CH8,),  # 0.98 (#89): ch. 8's boiler-efficiency topic
}


#: The walk-down chapter: every item of the RCx report's "Verify on site" checklist links to it
#: (0.98, #88; :mod:`camber.walkdown`). A process chapter, so it is not in RULE_REFERENCES.
WALKDOWN_REFERENCES: tuple = ("pnnl-retuning-ch9",)


def reference(rid: str) -> Reference:
    """The reference with id ``rid`` (``KeyError`` when unknown)."""
    return REFERENCES[rid]


def reference_ids_for(rule: str) -> list:
    """The reference ids mapped to ``rule`` (``[]`` when it has none)."""
    return list(RULE_REFERENCES.get(str(rule or ""), ()))


def references_for(rule: str) -> list:
    """The :class:`Reference` objects mapped to ``rule``, most specific first."""
    return [REFERENCES[i] for i in reference_ids_for(rule)]


def references_for_findings(findings, *, kinds=None) -> list:
    """The distinct references for the rules of ``findings`` (Finding objects or rule names),
    first-seen order; ``kinds`` keeps only those kinds (e.g. ``(GUIDE,)``)."""
    out, seen = [], set()
    for f in findings or ():
        rule = f if isinstance(f, str) else getattr(f, "rule", "")
        for r in references_for(rule):
            if r.id in seen or (kinds is not None and r.kind not in kinds):
                continue
            seen.add(r.id)
            out.append(r)
    return out


def reference_urls() -> list:
    """Every reference URL, for the link check."""
    return [r.url for r in REFERENCES.values()]


def _known(ids) -> list:
    return [REFERENCES[i] for i in ids or () if i in REFERENCES]


def links_html(ids, *, sep: str = " · ") -> str:
    """The "Learn more" links for reference ``ids``, an HTML fragment (``""`` when none).

    Unknown ids are skipped. Each link opens the publisher's page in a new tab and sends no
    referrer."""
    import html as _html

    refs = _known(ids)
    return sep.join(
        f"<a href='{_html.escape(r.url, quote=True)}' target='_blank' rel='noopener noreferrer' "
        f"title='{_html.escape(r.label(), quote=True)}'>{_html.escape(r.short())}</a>"
        for r in refs
    )


def links_text(ids) -> str:
    """The reference ``ids`` as plain text (``id`` list, ``""`` when none) for text outputs."""
    return ", ".join(r.id for r in _known(ids))
