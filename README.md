# Tim Umbach · Hamburg Job Radar

A static, personal job dashboard for Hamburg data roles. It ranks opportunities against a local profile and shows either an employer-published salary range or a transparent estimate.

## Recommended architecture

Use official company career pages as the primary source, not scraped Google result pages. Google Custom Search JSON API is closed to new customers and is scheduled to be discontinued for existing customers on 1 January 2027. Direct search-result scraping is brittle, difficult to test, and can conflict with provider terms.

This prototype therefore uses:

1. Permitted official career pages for Beiersdorf, Gebr. Heinemann, Freenet, EDEKA, Fielmann, Tchibo, Adobe, OTTO, HELM AG, and Publicis Groupe. ABOUT YOU and Google are included through Brave discovery; their current robots policies disallow this collector, so the direct sources are disabled. Maxingvest is monitored through Brave because its applications are routed through Tchibo's shared career center.
2. Up to ten Brave Web Search API requests per UTC day, guarded individually before each request.
3. One daily refresh from Experimentation Jobs' public WP Job Manager listing route, which its `robots.txt` permits.
4. One daily search through the community-documented BA Jobsuche endpoint, followed by at most ten detail requests for relevant candidates.
5. A 21-day rolling cache of normalized discoveries plus a dated fallback snapshot.
6. Work-model labelling: relevant Hamburg-area and remote roles are retained, then marked as a fit, unclear, or likely mismatch. Unclear/on-site roles are ranked lower instead of silently discarded.
7. A fully static HTML/CSS/JavaScript frontend that GitHub Pages can host. Results are grouped by company and initially show only each employer's highest-ranked role; every multi-role company can be expanded.

The Bundesagentur für Arbeit website is deliberately not scraped. Instead, the
collector uses the community-documented Jobsuche endpoint with the published
`jobboerse-jobsuche` client ID and retains a normal outbound link for manual BA
searches. This is not an official public BA API and may change without notice.

## Run locally

From this folder:

```bash
python scripts/collect_jobs.py --offline
python -m http.server 8000 --bind 0.0.0.0
```

Because this project runs in a remote VM, open the VS Code **Ports** panel,
forward port `8000`, and use **Open in Browser** on the forwarded address. This
is more reliable than opening `index.html` directly because browser `fetch()`
does not load local JSON consistently from `file://` URLs. Stop the preview
with `Ctrl+C` in its terminal.

Try a live refresh of the official sources, Experimentation Jobs, BA, and the
daily Brave budget:

```bash
python scripts/collect_jobs.py
```

The initial repository setup may populate every rotation exactly once:

```bash
python scripts/collect_jobs.py --initialize-brave
```

This is an explicit one-time operation for a new installation. It records
`bootstrap_completed_at`, skips the query already used that day, and refuses
to run a second time. The scheduled workflow never passes this option.

Career sites change frequently. The collector merges permitted live results,
recent Brave, Experimentation Jobs, and BA API discoveries, and the dated
snapshot, then deduplicates, applies safety exclusions, and labels work-model
fit. The dashboard shows the active collection mode in its header.

Locally, the collector reads the Brave key from
`../docu/brave_search_api_key`. It can alternatively read
`BRAVE_SEARCH_API_KEY` or the path in `BRAVE_SEARCH_API_KEY_FILE`. The key is
never copied into dashboard files or logs. `data/search_state.json` records the
UTC request count before each request is made, so repeated or interrupted runs
cannot exceed ten requests that day—even when requests fail.

## Tailoring

- `config/profile.json`: target roles, salary floor, experience, and weighted skills. It contains no address, phone number, email, or CV file.
- `config/sources.json`: allowlisted companies and link/location filters.
- `config/discovery.json`: ten-request Brave budget, query set, company watchlist, location rules, and industry exclusions.
- `data/seed_jobs.json`: dated fallback only; refresh or remove entries when roles close.
- `data/experimentation_jobs_cache.json`: 21-day cache of relevant niche-board listings.
- `data/ba_jobs_cache.json`: 21-day cache of relevant BA API listings.
- `scripts/collect_jobs.py`: scoring and salary logic.

The current profile was distilled from the locally supplied CV and public LinkedIn summary. Personal contact details were deliberately excluded.

The broader watchlist includes Statista, New Work/XING, Freenet, Tchibo,
Maxingvest, EDEKA, Fielmann, OTTO, HELM AG, Publicis Groupe, Google, Adobe,
Airbus commercial roles, MOIA, FREE NOW, Hapag-Lloyd, HHLA,
Lufthansa Technik, Jungheinrich, DESY, NDR, ZEIT, SPIEGEL, Bertelsmann, RTL
Deutschland/Gruner + Jahr, Hamburg public service, Techniker Krankenkasse,
Eppendorf, and Berenberg. Bauer Media is explicitly excluded. Airbus Defence
and other weapons, military, gambling, betting, tobacco, and adult-industry
roles are blocked by company and keyword filters.

Engineering-titled roles are also excluded: any vacancy whose title contains
the standalone word `engineer` is removed before scoring. Mentions of engineers
inside an otherwise relevant analyst or scientist job description do not hide
that vacancy.

Generic scientist roles are qualified separately. A scientist title is retained
only when the title itself signals a relevant field such as data,
experimentation, causal inference, decision science, quantitative work, or
machine learning. This prevents laboratory and natural-science vacancies from
ranking through incidental matches such as Python or stakeholder management.

## Salary model

Employer ranges are passed through unchanged and marked `high` confidence. Otherwise the estimate begins with these annual gross anchors:

- Data Scientist, Hamburg: €6,168 × 12 = €74,016.
- Data Analyst, Hamburg: €6,447 × 12 = €77,364.

The collector then applies a role-family factor, a seniority factor, and at most a small fit adjustment. The displayed range is ±10% around the midpoint. These are prioritization estimates, not compensation advice; bonuses, equity, pension, working hours, and benefits are excluded. The source is the Bundesagentur für Arbeit Entgeltatlas, data year 2024.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests cover salary parsing, score direction, preservation of employer salary ranges, and output schema/IDs.

## GitHub Pages

This repository is the standalone personal deployment of the dashboard. The
included `.github/workflows/pages.yml` refreshes and deploys it without a
separate application server.

Choose **Settings → Pages → Source: GitHub Actions** and create the repository
Actions secret `BRAVE_SEARCH_API_KEY`. Then run the workflow manually. It runs
once daily at 04:17 UTC, commits the refreshed data and request-state files, and
deploys the result.

## Limits and safe use

- Review each site's terms and robots policy before increasing frequency or adding sources.
- Confirm that the selected Brave API plan permits storing normalized search results; Brave's plan terms vary on storage rights.
- Do not bypass authentication, CAPTCHAs, or rate limits.
- A role in the snapshot may have closed; always verify on the original career page.
- The match score is a deterministic heuristic, not a hiring probability.
- Saved jobs live only in browser `localStorage`.
