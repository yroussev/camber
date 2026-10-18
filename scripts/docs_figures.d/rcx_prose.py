"""Docs screenshot of the RCx report's generated prose (0.108, #231): the worked example in
``docs/RCX-REPORT.md``. Loaded by ``scripts/docs_figures.py`` (see its "Per-entry figure
modules").

The report is the synthetic RCx demo, the same store and config as the golden report
(``tests/_rcx_fixture.py``), rendered with the example prose file
``examples/rcx_prose/prose.json``. The shot shows the executive summary, with its boxed prose
and the cited facts, and the Appendix A table of the slot that fell back. It is cut from the
rendered report's own HTML and styles; nothing is restyled. To retake it alone:
``python scripts/docs_figures.py --screenshots --no-figures --only shots/rcx-prose``.
"""

import os
import re

#: the example prose file, from the repository root
PROSE = os.path.join("examples", "rcx_prose", "prose.json")


def prose_example_report(root: str, tmp: str):
    """The synthetic RCx demo rendered with the example prose file: ``(report, html)``."""
    import pathlib
    import sys

    tests = os.path.join(root, "tests")
    if tests not in sys.path:
        sys.path.insert(0, tests)
    import _rcx_fixture as fx

    from camber.config import run_config
    from camber.report.rcx import build_rcx_report, load_prose

    fx.make_store(pathlib.Path(tmp))
    cfg = fx.config(loads={"DemoAHU2": {"heating_capacity_kbtuh": 400}})
    run = run_config(cfg, base_dir=tmp)
    rep = build_rcx_report(run, prose=load_prose(os.path.join(root, PROSE)))
    return rep, rep.to_html()


def excerpt(html: str) -> str:
    """The executive summary and Appendix A's prose-fallback table, on the report's styles."""
    head = html[: html.index("<body")]
    summary = re.search(r"<section id='summary'>.*?</section>", html, re.S).group(0)
    appendix = re.search(r"<section id='appendix-a'>.*?</section>", html, re.S).group(0)
    fallback = re.search(
        r"<div class='keep'><p class='lead'>Generated prose not shown.*?</table></div>",
        appendix,
        re.S,
    ).group(0)
    title = re.search(r"<h2>Appendix A[^<]*</h2>", appendix).group(0)
    return (
        f"{head}<body>\n{summary}\n<section id='appendix-a'>{title}\n{fallback}\n</section>\n"
        "</body></html>\n"
    )


def register(df) -> None:
    @df.screenshot(
        "shots/rcx-prose.png",
        "RCx report: generated prose in the executive summary and its Appendix A fallback row",
    )
    def _shot_rcx_prose(out: str) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="camber-prose-") as tmp:
            _rep, html = prose_example_report(df.ROOT, tmp)
            page = os.path.join(tmp, "prose.html")
            with open(page, "w", encoding="utf-8") as fh:
                fh.write(excerpt(html))
            df._chrome_shot("file://" + page, out, size=(1280, 700))
