# PEP 541 request — the `camber` PyPI name

CAMBER is published to PyPI as **`camber-toolkit`** because the bare distribution name
[`camber`](https://pypi.org/project/camber/) was already registered by an unrelated party. The
package still *imports* as `camber` (the distribution name and the import name need not match), so
`camber-toolkit` is fully functional and is the **permanent** distribution name regardless of the
outcome here. Reclaiming `camber` would only let us also publish under the canonical name and redirect
newcomers who `pip install camber` by reflex.

This file is a **ready-to-file draft** for the repo owner. Filing is a repo-owner action — an AI
assistant cannot and should not file it. Fill the bracketed placeholders with real evidence before
submitting; do **not** submit unverified claims.

## Is this eligible?

[PEP 541](https://peps.python.org/pep-0541/) lets PyPI transfer or reclaim a project name in specific
cases. The relevant grounds here are one of:

- **Abandoned** — the project has no releases / no source repository / no activity, and the current
  maintainer is unreachable after a good-faith contact attempt; **or**
- **Invalid / name-squatting** — the project exists only to hold the name (no functional content).

Before filing, **verify the current state** of <https://pypi.org/project/camber/> and record it:

- [ ] Does it have any releases? When was the last one? `[fill in]`
- [ ] Is there a real, functional project behind it, or is it an empty placeholder? `[fill in]`
- [ ] Is a source repository / homepage linked and live? `[fill in]`
- [ ] Contact attempt: how and when did you try to reach the maintainer, and what was the response
      (or the elapsed time with no response)? `[fill in — PEP 541 expects a good-faith attempt]`

If it turns out to be an active, functional project, **do not file** — `camber-toolkit` stays and
that is a perfectly good outcome.

## How to file

Open an issue on the PyPI support tracker using the name-request template:
<https://github.com/pypi/support/issues/new?template=name-request.yml> (title prefix
`PEP 541 Request:`). Paste the body below.

## Request body (paste into the issue)

> **Project to be claimed:** `camber` — <https://pypi.org/project/camber/>
>
> **My PyPI username:** `[owner PyPI username]`
>
> **Grounds (PEP 541):** `[Abandoned | Invalid]`
>
> **Reason:** I maintain the actively-developed project currently published as
> [`camber-toolkit`](https://pypi.org/project/camber-toolkit/) (source:
> <https://github.com/yroussev/camber>), a vendor-neutral building-automation-system trend-analysis
> toolkit (FDD / M&V / RCx). It imports as the `camber` package. The existing `camber` distribution
> `[is an empty placeholder with no releases | had its last release on <date> and the maintainer has
> not responded to a contact attempt on <date>]`.
>
> **Evidence:**
> - Current `camber` project state: `[releases / last-release date / links, or "no releases"]`
> - Contact attempt: `[method, date, outcome]`
> - My project's activity: `camber-toolkit` — `[N releases, latest v0.12.0 on <date>], `
>   [CI, docs, and a multi-arch container image]; source and history at
>   <https://github.com/yroussev/camber>.
>
> **Note:** I am not asking to delete anyone's working software — only to reclaim an unused/abandoned
> name. If the current owner is active and wishes to keep it, I withdraw the request; `camber-toolkit`
> remains our distribution name either way.

## After a successful transfer

- Keep publishing `camber-toolkit` (do not delete it — existing users depend on it).
- Optionally publish `camber` as a thin alias, or point it at the same wheels, so `pip install camber`
  resolves. Decide deliberately; a hard cutover would break nothing today but adds a second name to
  maintain.
- Update `README.md` / `docs/` install instructions only once the name actually resolves.
