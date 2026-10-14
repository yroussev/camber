"""Linked references: the free PNNL Building Re-tuning resources behind CAMBER's rules, and the
M&V standards and papers behind CAMBER's model and method names.

Provisional (0.96, #78).

CAMBER's rules and recommendations have long cited the PNNL Building Re-tuning material as plain
text ("PNNL Re-tuning Ch.8"). This module is the one registry of those resources: each entry has a
stable id, its title, publisher, document number, a public URL, a kind and the date the URL was
last checked. :data:`RULE_REFERENCES` maps a rule name to the references a reader should open to
learn more about that finding, and reports use it to add "Learn more" links and a "Further
reading" list.

**M&V references** (0.105, #163). :data:`MV_REFERENCES` lists the standards, protocols and papers
that :mod:`camber.mandv.names` cites for each M&V model and method name. They carry their authors,
year, venue and DOI so :meth:`Reference.citation` can give a full citation. Some sit behind a
paywall; the link is then the publisher's or a library's record page, never a copy.

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
    "PAPER",
    "STANDARD",
    "KINDS",
    "Reference",
    "REFERENCES",
    "MV_REFERENCES",
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
PAPER = "paper"  # a journal or conference paper (0.105)
STANDARD = "standard"  # a standard, guideline, protocol or method specification (0.105)
KINDS = (GUIDE, TRAINING, TOOL_GUIDE, REPORT, PROJECT, RELATED_TOOL, PAPER, STANDARD)

_PNNL = "Pacific Northwest National Laboratory"
_BASE = "https://www.pnnl.gov/sites/default/files/media/file/"
_VERIFIED = "2026-09-29"
_MV_VERIFIED = "2026-10-12"
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
    # bibliographic detail for papers, reports and standards (0.105; "" when not applicable)
    authors: str = ""
    year: str = ""
    venue: str = ""  # journal / proceedings, volume(issue), pages
    doi: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def citation(self) -> str:
        """A plain-text citation: authors (year). Title. Venue. Number. DOI (0.105)."""
        parts = [f"{self.authors} ({self.year})." if self.authors else ""]
        parts.append(f"{self.title}.")
        parts += [f"{self.venue}." if self.venue else "", f"{self.number}." if self.number else ""]
        parts.append(f"doi:{self.doi}." if self.doi else "")
        if not self.venue and not self.authors:
            parts.append(f"{self.publisher}.")
        return " ".join(p for p in parts if p)

    def label(self) -> str:
        """A human label: the title plus the document number when there is one."""
        return f"{self.title} ({self.number})" if self.number else self.title

    def short(self) -> str:
        """A short link text: ``PNNL guide: <measure>``, ``PNNL re-tuning ch. N`` or the title."""
        if self.kind == GUIDE and ": " in self.title:
            return "PNNL guide: " + self.title.split(": ", 1)[1]
        if self.kind == TRAINING and self.id.startswith("pnnl-small-retuning-"):
            return "PNNL small-building re-tuning ch. " + self.id.rsplit("ch", 1)[1]
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
    # --- the small-building course (packaged units without a BAS) -----------------------------
    Reference(
        "pnnl-small-retuning-ch3",
        "Small/Medium-Sized Commercial Building Re-tuning Training, chapter 3: Identifying and "
        "Implementing Re-tuning Measures",
        _PNNL,
        "PNNL-SA-92685",
        _BASE + "ch3_sm_identifying_and_implementing_retuning_measures.pdf",
        TRAINING,
        "2026-10-10",
        "Building HVAC Implementation: Different HVAC System General Guidance Examples (packaged "
        "units); Building HVAC Implementation: Thermostats",
    ),
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
    # --- M&V standards, protocols and papers (0.105, #163; camber.mandv.names) ----------------
    Reference(
        "ashrae-g14-2014",
        "ASHRAE Guideline 14-2014, Measurement of Energy, Demand, and Water Savings",
        "ASHRAE",
        "Guideline 14-2014",
        "https://www.ashrae.org/technical-resources/standards-and-guidelines/titles-purposes-and-scopes",
        STANDARD,
        _MV_VERIFIED,
        year="2014",
    ),
    Reference(
        "ashrae-rp1050-imt",
        "Inverse Modeling Toolkit: Numerical Algorithms (ASHRAE RP-1050)",
        "ASHRAE",
        "KC-03-02-1",
        "https://iifiir.org/en/fridoc/inverse-modelling-toolkit-imt-numerical-algorithms-22451",
        PAPER,
        _MV_VERIFIED,
        authors="Kissock, J.K., Haberl, J.S., Claridge, D.E.",
        year="2003",
        venue="ASHRAE Transactions 109(2)",
    ),
    Reference(
        "fels-1986-prism",
        "PRISM: An introduction",
        "Elsevier",
        "",
        "https://doi.org/10.1016/0378-7788(86)90003-4",
        PAPER,
        _MV_VERIFIED,
        authors="Fels, M.F.",
        year="1986",
        venue="Energy and Buildings 9(1-2):5-18",
        doi="10.1016/0378-7788(86)90003-4",
    ),
    Reference(
        "mathieu-2011-towt",
        "Quantifying Changes in Building Electricity Use, With Application to Demand Response",
        "IEEE",
        "LBNL-4944E",
        "https://www.osti.gov/biblio/1048308",
        PAPER,
        _MV_VERIFIED,
        authors="Mathieu, J.L., Price, P.N., Kiliccote, S., Piette, M.A.",
        year="2011",
        venue="IEEE Transactions on Smart Grid 2(3):507-518",
        doi="10.1109/TSG.2011.2145010",
    ),
    Reference(
        "lbnl-rmv2",
        "RMV2.0: an R package for measurement and verification 2.0 (towt_baseline, version 1.1.0)",
        "Lawrence Berkeley National Laboratory",
        "",
        "https://github.com/LBNL-ETA/RMV2.0",
        RELATED_TOOL,
        _MV_VERIFIED,
        section="R/towt_baseline.R: makeBaseline, fitLBNLregress, piecewiseVariables",
        authors="Touzani, S.",
        year="2017",
    ),
    Reference(
        "granderson-2015-baselines",
        "Automated measurement and verification: Performance of public domain whole-building "
        "electric baseline models",
        "Elsevier",
        "LBNL-187596",
        "https://www.osti.gov/pages/biblio/1247594",
        PAPER,
        _MV_VERIFIED,
        authors="Granderson, J., Price, P.N., Jump, D., Addy, N., Sohn, M.D.",
        year="2015",
        venue="Applied Energy 144:106-113",
        doi="10.1016/j.apenergy.2015.01.026",
    ),
    Reference(
        "lbnl-187225",
        "Assessment of Automated Measurement and Verification (M&V) Methods",
        "Lawrence Berkeley National Laboratory",
        "LBNL-187225",
        "https://www.osti.gov/biblio/1236174",
        REPORT,
        _MV_VERIFIED,
        authors="Granderson, J., Touzani, S., Custodio, C., Sohn, M.D., Fernandes, S., Jump, D.",
        year="2015",
        doi="10.2172/1236174",
    ),
    Reference(
        "lbnl-6963e",
        "Performance Metrics and Objective Testing Methods for Energy Baseline Modeling Software",
        "Lawrence Berkeley National Laboratory",
        "LBNL-6963E",
        "https://www.aceee.org/wp-content/uploads/proceedings-1980-2020/2014/11-734.pdf",
        PAPER,
        _MV_VERIFIED,
        authors="Granderson, J., Jump, D., Price, P.N., Sohn, M.D.",
        year="2014",
        venue="2014 ACEEE Summer Study on Energy Efficiency in Buildings, paper 11-734",
    ),
    Reference(
        "caltrack-2",
        "CalTRACK Technical Documentation: Methods (CalTRACK 2.0)",
        "CalTRACK (LF Energy OpenEEmeter)",
        "",
        "https://docs.caltrack.org/en/latest/methods.html",
        STANDARD,
        _MV_VERIFIED,
        year="2018",
    ),
    Reference(
        "evo-ipmvp-core-2022",
        "International Performance Measurement and Verification Protocol (IPMVP) Core Concepts "
        "2022",
        "Efficiency Valuation Organization (EVO)",
        "EVO 10000-1:2022",
        "https://evo-world.org/en/products-services-mainmenu-en/protocols/ipmvp",
        STANDARD,
        _MV_VERIFIED,
        year="2022",
    ),
    Reference(
        "doe-sep-mv-2019",
        "Superior Energy Performance 50001 Program Measurement & Verification Protocol: 2019 "
        "(Edition 2)",
        "U.S. Department of Energy",
        "",
        "https://betterbuildingssolutioncenter.energy.gov/iso-50001/sep-50001/resources/"
        "sep-50001-measurement-and-verification-protocol",
        STANDARD,
        _MV_VERIFIED,
        year="2023",
    ),
    # Reserved slots (0.105): the synthetic workbook entries add their references only between
    # their own markers, so their branches do not touch the same lines.
    # BEGIN slot #146 (AHU maintenance: filter_fouling, control_hunting)
    # END slot #146
    # BEGIN slot #147 (heat pump)
    # --- heat pumps (0.105, #147): no PNNL re-tuning guide or chapter covers them ---------------
    Reference(
        "nrcan-heat-pump-guide",
        "Heating and cooling with a heat pump",
        "Natural Resources Canada",
        "",
        "https://natural-resources.canada.ca/energy-efficiency/energy-star/heating-cooling-heat-pump",
        GUIDE,
        "2026-10-13",
        "Air-source heat pumps: The defrost cycle; Supplementary heat sources; Related "
        "equipment: Supplementary heating systems; Thermostats",
    ),
    Reference(
        "neep-ashp-install-guide",
        "Guide to Installing Air-Source Heat Pumps in Cold Climates",
        "Northeast Energy Efficiency Partnerships (NEEP)",
        "",
        "https://neep.org/sites/default/files/Installing%20Air-Source%20Heat%20Pumps%20in%20Cold"
        "%20Climates.pdf",
        GUIDE,
        "2026-10-13",
        "System Setup: Thermostat - Installer Setup",
        year="2018",
    ),
    Reference(
        "epri-tb-114673",
        "Variable Flow Pumping: The Key to Energy Efficiency in Water Loop and Geothermal Heat "
        "Pump System Applications",
        "Electric Power Research Institute (EPRI)",
        "TB-114673",
        "https://restservice.epri.com/publicdownload/TB-114673/0/Product",
        REPORT,
        "2026-10-13",
        "Making Water Source Heat Pumps Efficient: Improved Loop Pump Control; Variable Flow "
        "System Design and Control Details",
        year="1999",
    ),
    # END slot #147
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
    # BEGIN slot #146 (AHU maintenance): rule mappings
    # END slot #146
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
    "actuator_stuck": (_CH7,),  # 0.98 (#85): ch. 7's terminal damper and valve checks
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
    # Left unmapped (no guide or chapter clearly covers them):
    # - filter_fouling;
    # - the G36 reset-effectiveness rules (sat_/static_reset_effectiveness, trim-and-respond, a
    #   G36 sequence the guides predate) and g36_afdd (cites Guideline 36 itself);
    # - the DX rules other than compressor_short_cycle and compressor_staging (the course covers
    #   built-up air handlers and central plants; the heat-pump rules map to slot #147's guides);
    # - the drift detectors other than the two plant ones mapped below.
    # packaged units (0.103, #125): the small-building course's measures chapter covers packaged
    # units and the thermostats that cycle them
    "compressor_short_cycle": ("pnnl-small-retuning-ch3",),
    # BEGIN slot #147 (heat pump): rule mappings
    # heat pumps (0.105, #147). NRCan's guide covers the defrost cycle (demand against
    # time-temperature control), the balance point and supplementary heat, and the reversing
    # valve; NEEP's installation guide covers where a unit's thermostat goes; EPRI's brief covers
    # loop pumps that run at constant speed through units without shutoff valves.
    "heatpump_defrost": ("nrcan-heat-pump-guide",),
    "hp_capacity_shortfall": ("nrcan-heat-pump-guide",),
    "hp_mode_vs_need": ("nrcan-heat-pump-guide", "neep-ashp-install-guide"),
    "hp_room_imbalance": ("neep-ashp-install-guide",),
    "source_loop_deltat": ("epri-tb-114673",),
    # staging is set by the same thermostat and packaged-unit settings as compressor starts
    "compressor_staging": ("pnnl-small-retuning-ch3",),
    # END slot #147
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
    # 0.103 (#132): the heating guide's warm-weather shutdown and ch. 8's pump lockout
    "hw_pump_summer_lockout": ("pnnl-guide-plant-heating", _CH8),
    "boiler_efficiency_drift": (_CH8,),  # 0.98 (#89): ch. 8's boiler-efficiency topic
}


#: The M&V standards, protocols and papers :mod:`camber.mandv.names` cites (0.105, #163).
MV_REFERENCES: tuple = (
    "ashrae-g14-2014",
    "ashrae-rp1050-imt",
    "fels-1986-prism",
    "mathieu-2011-towt",
    "lbnl-rmv2",
    "granderson-2015-baselines",
    "lbnl-187225",
    "lbnl-6963e",
    "caltrack-2",
    "evo-ipmvp-core-2022",
    "doe-sep-mv-2019",
)


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
