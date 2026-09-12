# Website Agent Guidance

## Scope

This repository is Tim Umbach's static personal website and its two data
products: Job Radar and Election Lab. Keep the presentation restrained,
evidence-led, responsive, and accessible while using available space well.

## Architecture

- The site is plain HTML, CSS, and browser JavaScript hosted by GitHub Pages.
- `index.html`, `contact.html`, and `cv.html` are the personal-site pages.
- `job-radar.html`, `assets/app.js`, and `scripts/collect_jobs.py` implement Job Radar.
- `election-lab.html`, `assets/election-models.js`, and
  `scripts/collect_election_models.py` implement Election Lab.
- Shared visual rules live in `assets/styles.css`; avoid adding a frontend build
  system unless the user explicitly asks for one.
- Generated snapshots live in `data/`. Update them through their collectors
  when collector behavior changes.

## Product invariants

- The standalone Economy Lab has been intentionally removed. Do not recreate
  `economy-lab.html` or its assets, model data, tests, navigation, or pipeline.
- Election Lab intentionally retains its poll-only logit and economy-enriched
  probit specifications. Do not confuse its economic overlay with the removed
  standalone Economy Lab.
- Completed elections must leave the live Election Lab selector after election day.
- Job cards must link to a job-specific employer or BA detail/application page,
  never an aggregator result or generic career-search page.
- Archive jobs after a stated application deadline. If no deadline is known,
  archive them after 60 days without confirmation.
- Employer-published salary ranges remain unchanged. The official Entgeltatlas
  value is the headline floor; only at least three comparable, higher published
  Data Scientist ranges may raise it. Never use vacancy evidence to lower it.
- Map Hamburg-area jobs to cached employer offices when an official-company or
  authoritative institutional source supports the address. If more than one
  plausible Hamburg office exists, choose one documented reference rather than
  using a random visual offset. Never imply it is the guaranteed vacancy worksite.
- Geocode only new or changed verified addresses, cache the result, respect the
  public Nominatim policy, and share the existing ten-request Brave daily budget.
- Keep LinkedIn integration out of scope unless the user explicitly reopens it.
- Do not publish private CV/contact source data beyond assets already intended
  for the public website.

## Working practice

- Inspect `git status`, the current branch, and the configured remote before editing.
- Fetch `origin/main` before preparing a push because the scheduled workflow may
  have committed refreshed data since the local checkout was last used.
- Preserve unrelated user changes and integrate remote commits without force-pushing.
- Prefer official career pages and direct application URLs when adding sources.
- Visually inspect meaningful layout changes at desktop and mobile widths.

## Validation

Run the checks relevant to the change; before deployment, run the complete set:

```bash
python -m unittest discover -s tests -v
node --check assets/app.js
node --check assets/election-models.js
python -m py_compile scripts/*.py
python -m json.tool data/jobs.json >/dev/null
python -m json.tool data/election_models.json >/dev/null
python -m json.tool data/company_locations.json >/dev/null
git diff --check
```

For Job Radar collector changes, also rebuild the checked-in snapshot with
`python scripts/collect_jobs.py --reindex` and audit that every retained URL
passes `is_direct_application_url`.

## Deployment

- Push to `origin/main` only when the user asks to publish.
- `.github/workflows/pages.yml` runs on schedule or manual dispatch; a normal
  push alone does not deploy the page.
- After pushing, start the workflow with
  `gh workflow run pages.yml --repo political-dynamics/tim-umbach --ref main`
  and monitor it through the Pages deploy job.
- Do not report the website as live until the deployment succeeds. Allow for a
  short CDN/browser-cache delay and verify the public URL when possible.
