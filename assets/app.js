"use strict";

const state = {
  jobs: [],
  archivedJobs: [],
  profile: null,
  meta: null,
  saved: new Set(JSON.parse(localStorage.getItem("job-radar-saved") || "[]")),
  savedOnly: false,
  showArchived: false,
  expandedCompanies: new Set(),
  selectedMapJob: null,
};

const $ = (selector) => document.querySelector(selector);
const elements = {
  dataStatus: $("#data-status"),
  profileHeadline: $("#profile-headline"),
  profileLocation: $("#profile-location"),
  profileTags: $("#profile-tags"),
  strongMatchCount: $("#strong-match-count"),
  medianSalary: $("#median-salary"),
  salaryReferenceLabel: $("#salary-reference-label"),
  salaryReferenceNumber: $("#salary-reference-number"),
  salaryReferenceTitle: $("#salary-reference-title"),
  salaryReferenceCopy: $("#salary-reference-copy"),
  salaryReferenceScale: $("#salary-reference-scale"),
  companyCount: $("#company-count"),
  jobCountLabel: $("#job-count-label"),
  savedCount: $("#saved-count"),
  showSaved: $("#show-saved"),
  chart: $("#opportunity-chart"),
  jobsTitle: $("#jobs-title"),
  resultsLabel: $("#results-label"),
  showCurrent: $("#show-current"),
  showArchived: $("#show-archived"),
  archiveCount: $("#archive-count"),
  searchInput: $("#search-input"),
  companyFilter: $("#company-filter"),
  matchFilter: $("#match-filter"),
  salaryFilter: $("#salary-filter"),
  salaryFilterValue: $("#salary-filter-value"),
  resetFilters: $("#reset-filters"),
  jobList: $("#job-list"),
  hamburgMap: $("#hamburg-map"),
  mapRoleDetail: $("#map-role-detail"),
  dialog: $("#job-dialog"),
  dialogContent: $("#dialog-content"),
};

function allJobs() {
  return [...state.jobs, ...state.archivedJobs];
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch { return "#"; }
}

function money(value, compact = false) {
  if (!Number.isFinite(Number(value))) return "—";
  if (compact) return `€${Math.round(Number(value) / 1000)}k`;
  return new Intl.NumberFormat("en-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(value);
}

function initials(company) {
  return company.split(/\s+/).map((part) => part[0]).join("").replace(/[^A-Za-z]/g, "").slice(0, 2).toUpperCase();
}

function median(values) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function sourceIsAdvertised(job) {
  return String(job.salary_source).toLowerCase().includes("employer");
}

function roleSnapshot(job) {
  let description = String(job.description || "Open the career page for the full role description.");
  const repeatedPrefix = `${job.title} ${job.title}`.toLowerCase();
  while (description.toLowerCase().startsWith(repeatedPrefix)) {
    description = description.slice(job.title.length).trimStart();
  }
  return description;
}

function updateProfile() {
  const profile = state.profile;
  elements.profileHeadline.textContent = profile.headline;
  elements.profileLocation.textContent = `${profile.location} · ${profile.search_radius_km} km · ${profile.work_modes.join(" / ")}`;
  const highlights = profile.skills.slice().sort((a, b) => b.weight - a.weight).slice(0, 5);
  elements.profileTags.innerHTML = highlights.map((skill) => `<span class="tag">${escapeHtml(skill.name)}</span>`).join("");
}

function updateOverview() {
  const strong = state.jobs.filter((job) => job.match_score >= 75);
  const companies = new Set(state.jobs.map((job) => job.company));
  const opportunityMedian = median(strong.map((job) => job.salary_mid));
  elements.strongMatchCount.textContent = strong.length;
  elements.medianSalary.textContent = money(opportunityMedian, true);
  elements.companyCount.textContent = companies.size;
  elements.jobCountLabel.textContent = `${state.jobs.length} opportunities tracked`;
  elements.savedCount.textContent = state.saved.size;
  elements.archiveCount.textContent = state.archivedJobs.length;
  const publishedCount = Number(state.meta.published_salary_count || 0);
  const marketReference = Number(state.meta.market_salary_reference_eur || state.meta.salary_benchmark_annual_eur);
  elements.salaryReferenceNumber.textContent = money(marketReference, true);
  elements.salaryReferenceScale.style.width = `${Math.max(0, Math.min(100, (marketReference - 50000) / 40000 * 100))}%`;
  if (publishedCount) {
    elements.salaryReferenceLabel.textContent = "Live salary evidence";
    elements.salaryReferenceTitle.textContent = "Published-role median";
    elements.salaryReferenceCopy.textContent = `${publishedCount} active employer-published ${publishedCount === 1 ? "range updates" : "ranges update"} this signal. Modelled roles still use the official Entgeltatlas anchor.`;
  } else {
    elements.salaryReferenceLabel.textContent = "Official salary anchor";
    elements.salaryReferenceTitle.textContent = "Hamburg Data Scientist median";
    elements.salaryReferenceCopy.textContent = "No active employer range is available, so the signal uses the annualised Entgeltatlas median.";
  }
  const generated = state.meta.generated_at ? new Date(state.meta.generated_at) : null;
  const dateText = generated && !Number.isNaN(generated.valueOf())
    ? generated.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })
    : "snapshot";
  const braveStatus = String(state.meta.brave_search_status || "");
  const braveLabel = braveStatus.includes("success")
    ? " · Brave updated"
    : braveStatus.includes("already used") || braveStatus.includes("budget")
      ? " · Brave budget used"
      : "";
  const experimentationStatus = String(state.meta.experimentation_jobs_status || "");
  const experimentationLabel = experimentationStatus.includes("success")
    ? " · Experimentation Jobs updated"
    : experimentationStatus.includes("cached")
      ? " · Experimentation Jobs cached"
      : "";
  const baStatus = String(state.meta.ba_jobs_status || "");
  const baLabel = baStatus.includes("success") || baStatus.includes("partial")
    ? " · BA updated"
    : baStatus.includes("cached")
      ? " · BA cached"
      : "";
  elements.dataStatus.textContent = `${state.meta.mode}${braveLabel}${experimentationLabel}${baLabel} · ${dateText}`;
}

function populateCompanies() {
  const companies = [...new Set(allJobs().map((job) => job.company))].sort();
  elements.companyFilter.insertAdjacentHTML("beforeend", companies.map((company) => `<option value="${escapeHtml(company)}">${escapeHtml(company)}</option>`).join(""));
}

function filteredJobs() {
  const query = elements.searchInput.value.trim().toLowerCase();
  const company = elements.companyFilter.value;
  const minMatch = Number(elements.matchFilter.value);
  const minSalary = Number(elements.salaryFilter.value) * 1000;
  const source = state.showArchived ? state.archivedJobs : state.jobs;
  return source.filter((job) => {
    const searchable = [job.title, job.company, job.location, ...(job.matched_skills || [])].join(" ").toLowerCase();
    return (!query || searchable.includes(query))
      && (!company || job.company === company)
      && job.match_score >= minMatch
      && job.salary_mid >= minSalary
      && (!state.savedOnly || state.saved.has(job.id));
  });
}

function renderChart(jobs) {
  const points = jobs.slice(0, 14);
  const width = 720;
  const height = 260;
  const padding = { left: 47, right: 22, top: 18, bottom: 37 };
  const xMin = 45;
  const xMax = 100;
  const salaryValues = points.map((job) => job.salary_mid);
  const yMin = Math.min(50000, ...salaryValues);
  const yMax = Math.max(95000, ...salaryValues);
  const x = (value) => padding.left + ((value - xMin) / (xMax - xMin)) * (width - padding.left - padding.right);
  const y = (value) => height - padding.bottom - ((value - yMin) / (yMax - yMin)) * (height - padding.top - padding.bottom);
  const xTicks = [50, 60, 70, 80, 90, 100];
  const yTicks = [50000, 60000, 70000, 80000, 90000].filter((value) => value >= yMin && value <= yMax);
  const grids = [
    ...xTicks.map((value) => `<line class="grid-line" x1="${x(value)}" x2="${x(value)}" y1="${padding.top}" y2="${height - padding.bottom}"/><text x="${x(value)}" y="${height - 15}" text-anchor="middle">${value}</text>`),
    ...yTicks.map((value) => `<line class="grid-line" x1="${padding.left}" x2="${width - padding.right}" y1="${y(value)}" y2="${y(value)}"/><text x="${padding.left - 8}" y="${y(value) + 3}" text-anchor="end">€${value / 1000}k</text>`),
  ].join("");
  const dots = points.map((job) => {
    const advertised = sourceIsAdvertised(job);
    const radius = advertised ? 8 : job.salary_confidence === "medium" ? 6.5 : 5;
    const color = advertised ? "#743129" : "#aa9284";
    return `<circle class="job-dot" data-job-id="${escapeHtml(job.id)}" cx="${x(Math.max(xMin, job.match_score))}" cy="${y(job.salary_mid)}" r="${radius}" fill="${color}" opacity=".9"><title>${escapeHtml(job.company)} — ${escapeHtml(job.title)}: ${job.match_score} match, ${money(job.salary_mid, true)}</title></circle>`;
  }).join("");
  elements.chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${grids}${dots}<text class="axis-label" x="${width - padding.right}" y="${height - 2}" text-anchor="end">Match score →</text></svg>`;
  elements.chart.querySelectorAll(".job-dot").forEach((dot) => dot.addEventListener("click", () => openJob(dot.dataset.jobId)));
}

function updateMapDetail(job) {
  if (!job) {
    elements.mapRoleDetail.innerHTML = `<p class="overline">Map coverage</p><h3>No Hamburg-area points</h3><p>Remote-only and unstated locations remain in the ranked list.</p>`;
    return;
  }
  state.selectedMapJob = job.id;
  elements.mapRoleDetail.innerHTML = `<p class="overline">${escapeHtml(job.map?.label || "Hamburg area")}</p>
    <h3>${escapeHtml(job.title)}</h3>
    <p>${escapeHtml(job.company)} · ${job.match_score}% match · ${money(job.salary_mid, true)}</p>
    <span>${escapeHtml(job.work_mode || "Work mode not stated")} · approximate city-level position</span>
    <button type="button" data-map-open="${escapeHtml(job.id)}">Inspect role <span aria-hidden="true">→</span></button>`;
  elements.mapRoleDetail.querySelector("[data-map-open]").addEventListener("click", () => openJob(job.id));
  elements.hamburgMap.querySelectorAll(".map-marker").forEach((marker) => {
    const active = marker.dataset.mapJob === job.id;
    marker.classList.toggle("active", active);
    marker.setAttribute("aria-pressed", String(active));
  });
}

function renderHamburgMap(jobs = state.jobs) {
  const mapped = jobs.filter((job) => Number.isFinite(Number(job.map?.latitude)) && Number.isFinite(Number(job.map?.longitude)));
  const bounds = { north: 53.73, south: 53.36, west: 9.62, east: 10.32 };
  const point = (job) => ({
    left: (Number(job.map.longitude) - bounds.west) / (bounds.east - bounds.west) * 100,
    top: (bounds.north - Number(job.map.latitude)) / (bounds.north - bounds.south) * 100,
  });
  const base = `<svg viewBox="0 0 800 430" aria-hidden="true">
    <path class="map-boundary" d="M97 246 132 132 238 58 367 73 438 38 545 91 688 109 739 205 680 307 574 351 492 397 356 366 266 405 163 345Z"/>
    <path class="map-water" d="M-20 285 C120 240 178 292 286 280 S455 231 550 266 685 333 825 277"/>
    <path class="map-water-branch" d="M272 280 C294 247 323 220 353 180"/>
    <g class="map-roads"><path d="M154 146 630 319"/><path d="M250 382 420 56"/><path d="M102 252 697 178"/></g>
  </svg>
  <span class="map-label altona">Altona</span><span class="map-label center">Mitte</span><span class="map-label north">Nord</span><span class="map-label wandsbek">Wandsbek</span><span class="map-label harburg">Harburg</span><span class="map-label elbe">Elbe</span>`;
  const markers = mapped.map((job) => {
    const position = point(job);
    const salaryClass = sourceIsAdvertised(job) ? "published" : "estimated";
    return `<button class="map-marker ${salaryClass}" style="left:${position.left.toFixed(2)}%;top:${position.top.toFixed(2)}%" type="button" data-map-job="${escapeHtml(job.id)}" aria-label="${escapeHtml(job.company)}, ${escapeHtml(job.title)}" aria-pressed="false"><span>${escapeHtml(initials(job.company))}</span></button>`;
  }).join("");
  elements.hamburgMap.innerHTML = `${base}${markers}<div class="map-key"><span><i class="published"></i>Published salary</span><span><i class="estimated"></i>Estimated</span></div>`;
  elements.hamburgMap.querySelectorAll("[data-map-job]").forEach((marker) => marker.addEventListener("click", () => {
    updateMapDetail(mapped.find((job) => job.id === marker.dataset.mapJob));
  }));
  updateMapDetail(mapped.find((job) => job.id === state.selectedMapJob) || mapped[0]);
}

function cardMarkup(job) {
  const saved = state.saved.has(job.id);
  const salaryLabel = `${money(job.salary_min, true)}–${money(job.salary_max, true)}`;
  const sourceClass = sourceIsAdvertised(job) ? "advertised" : "estimated";
  const sourceLabel = sourceIsAdvertised(job) ? "Employer range" : `${job.salary_confidence} confidence estimate`;
  const tags = (job.matched_skills || []).slice(0, 3).map((skill) => `<span class="job-tag">${escapeHtml(skill)}</span>`).join("");
  const workFit = ["fit", "unclear", "mismatch"].includes(job.work_mode_fit) ? job.work_mode_fit : "unclear";
  const archiveNote = state.showArchived
    ? `<span class="archive-note">Archived · ${escapeHtml(job.archive_reason || "No longer active")}</span>`
    : job.application_deadline
      ? `<span class="deadline-note">Apply by ${escapeHtml(job.application_deadline)}</span>`
      : "";
  return `<article class="job-card ${state.showArchived ? "archived" : ""}">
    <button class="card-open" type="button" data-open-job="${escapeHtml(job.id)}" aria-label="Open ${escapeHtml(job.title)} details"></button>
    <div class="match-ring" style="--score:${job.match_score}"><span>${job.match_score}%</span></div>
    <div class="job-main">
      <div class="job-kicker"><span class="company-avatar">${escapeHtml(initials(job.company))}</span>${escapeHtml(job.company)} · ${escapeHtml(job.level)}</div>
      <h3>${escapeHtml(job.title)}</h3>
      <div class="job-tags">${tags}${archiveNote}</div>
    </div>
    <div class="job-location"><p>Location</p><strong>${escapeHtml(job.location || "Not stated")}</strong><span class="work-fit ${workFit}">${escapeHtml(job.work_mode || "Work mode unclear")}</span><small>${escapeHtml(job.work_mode_note || "Verify work model")}</small></div>
    <div class="job-salary"><p>Annual gross</p><strong>${salaryLabel}</strong><span class="salary-source ${sourceClass}">${escapeHtml(sourceLabel)}</span></div>
    <button class="save-button ${saved ? "saved" : ""}" type="button" data-save-job="${escapeHtml(job.id)}" aria-label="${saved ? "Remove from" : "Add to"} shortlist" aria-pressed="${saved}">${saved ? "★" : "☆"}</button>
  </article>`;
}

function groupJobsByCompany(jobs) {
  const groups = new Map();
  jobs.forEach((job) => {
    if (!groups.has(job.company)) groups.set(job.company, []);
    groups.get(job.company).push(job);
  });
  return [...groups.entries()];
}

function companyGroupMarkup([company, jobs]) {
  const expanded = state.expandedCompanies.has(company);
  const visibleJobs = expanded ? jobs : jobs.slice(0, 1);
  const toggle = jobs.length > 1
    ? `<button class="company-toggle" type="button" data-toggle-company="${escapeHtml(company)}" aria-expanded="${expanded}">${expanded ? "Show top job only" : `Show all ${jobs.length} jobs`} <span aria-hidden="true">${expanded ? "−" : "+"}</span></button>`
    : "";
  return `<section class="company-group">
    <div class="company-group-heading">
      <div><span class="company-avatar">${escapeHtml(initials(company))}</span><div><h3>${escapeHtml(company)}</h3><p>${jobs.length} matching ${jobs.length === 1 ? "role" : "roles"} · top match ${jobs[0].match_score}%</p></div></div>
      ${toggle}
    </div>
    <div class="company-job-list">${visibleJobs.map(cardMarkup).join("")}</div>
  </section>`;
}

function renderJobs() {
  const jobs = filteredJobs();
  const total = state.showArchived ? state.archivedJobs.length : state.jobs.length;
  const groups = groupJobsByCompany(jobs);
  elements.resultsLabel.textContent = `${groups.length} companies · ${jobs.length} of ${total} ${state.showArchived ? "archived" : "current"} opportunities`;
  elements.salaryFilterValue.textContent = `€${elements.salaryFilter.value}k`;
  if (!jobs.length) {
    elements.jobList.innerHTML = `<div class="empty-state"><strong>No ${state.showArchived ? "archived" : "current"} roles match these filters.</strong><p>Lower the salary or match threshold, or reset all filters.</p></div>`;
  } else {
    elements.jobList.innerHTML = groups.map(companyGroupMarkup).join("");
  }
  renderChart(jobs.length ? jobs : (state.showArchived ? state.archivedJobs : state.jobs));
  if (!state.showArchived) renderHamburgMap(jobs.length ? jobs : state.jobs);
  elements.jobList.querySelectorAll("[data-open-job]").forEach((button) => button.addEventListener("click", () => openJob(button.dataset.openJob)));
  elements.jobList.querySelectorAll("[data-save-job]").forEach((button) => button.addEventListener("click", () => toggleSave(button.dataset.saveJob)));
  elements.jobList.querySelectorAll("[data-toggle-company]").forEach((button) => button.addEventListener("click", () => {
    const company = button.dataset.toggleCompany;
    if (state.expandedCompanies.has(company)) state.expandedCompanies.delete(company); else state.expandedCompanies.add(company);
    renderJobs();
  }));
}

function toggleSave(id) {
  if (state.saved.has(id)) state.saved.delete(id); else state.saved.add(id);
  localStorage.setItem("job-radar-saved", JSON.stringify([...state.saved]));
  updateOverview();
  renderJobs();
}

function openJob(id) {
  const job = allJobs().find((candidate) => candidate.id === id);
  if (!job) return;
  const advertised = sourceIsAdvertised(job);
  const reasons = (job.fit_reasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("");
  const gaps = (job.skill_gaps || []).map((gap) => `<span>${escapeHtml(gap)}</span>`).join("");
  const workFit = ["fit", "unclear", "mismatch"].includes(job.work_mode_fit) ? job.work_mode_fit : "unclear";
  elements.dialogContent.innerHTML = `<div class="dialog-inner">
    <p class="overline">${escapeHtml(job.company)} · ${escapeHtml(job.level)}</p>
    <h2>${escapeHtml(job.title)}</h2>
    <div class="dialog-meta"><span>⌖ ${escapeHtml(job.location || "Location not stated")}</span><span>◫ ${escapeHtml(job.work_mode || "Work mode not stated")}</span><span>↻ ${escapeHtml(job.freshness || "Date not stated")}</span></div>
    <p class="work-fit-note ${workFit}"><strong>Work-model fit:</strong> ${escapeHtml(job.work_mode_note || "Needs verification on the job page")}</p>
    <div class="dialog-score-grid">
      <div class="dialog-score"><span>Profile match</span><strong>${job.match_score}%</strong></div>
      <div class="dialog-score"><span>${advertised ? "Published salary" : "Estimated salary"}</span><strong>${money(job.salary_min, true)}–${money(job.salary_max, true)}</strong></div>
    </div>
    <h3>Why it matches</h3><ul class="reason-list">${reasons || "<li>General role and location alignment</li>"}</ul>
    <h3>Role snapshot</h3><p>${escapeHtml(roleSnapshot(job))}</p>
    ${gaps ? `<h3>Skills to validate</h3><div class="gap-list">${gaps}</div>` : ""}
    <h3>Salary evidence</h3><p>${escapeHtml(job.salary_source)}. Confidence: ${escapeHtml(job.salary_confidence)}. This is annual gross base pay and excludes bonus, equity, pension, and benefits.</p>
    <a class="apply-link" href="${safeUrl(job.url)}" target="_blank" rel="noopener noreferrer">Open original job <span>↗</span></a>
  </div>`;
  elements.dialog.showModal();
}

function bindEvents() {
  [elements.searchInput, elements.companyFilter, elements.matchFilter, elements.salaryFilter].forEach((element) => element.addEventListener("input", renderJobs));
  elements.resetFilters.addEventListener("click", () => {
    elements.searchInput.value = "";
    elements.companyFilter.value = "";
    elements.matchFilter.value = "0";
    elements.salaryFilter.value = "60";
    state.savedOnly = false;
    state.expandedCompanies.clear();
    elements.showSaved.textContent = "Show saved only";
    renderJobs();
  });
  elements.showSaved.addEventListener("click", () => {
    state.savedOnly = !state.savedOnly;
    elements.showSaved.textContent = state.savedOnly ? "Show all roles" : "Show saved only";
    renderJobs();
    $("#opportunities").scrollIntoView({ behavior: "smooth" });
  });
  elements.showCurrent.addEventListener("click", () => setListingMode(false));
  elements.showArchived.addEventListener("click", () => setListingMode(true));
  $(".dialog-close").addEventListener("click", () => elements.dialog.close());
  elements.dialog.addEventListener("click", (event) => {
    const bounds = elements.dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) elements.dialog.close();
  });
}

function setListingMode(showArchived) {
  state.showArchived = showArchived;
  state.expandedCompanies.clear();
  elements.showCurrent.classList.toggle("active", !showArchived);
  elements.showArchived.classList.toggle("active", showArchived);
  elements.showCurrent.setAttribute("aria-pressed", String(!showArchived));
  elements.showArchived.setAttribute("aria-pressed", String(showArchived));
  elements.jobsTitle.textContent = showArchived ? "Past opportunities, kept tidy." : "Best company matches first.";
  renderJobs();
}

async function initialize() {
  try {
    const [jobsResponse, profileResponse] = await Promise.all([fetch("data/jobs.json"), fetch("config/profile.json")]);
    if (!jobsResponse.ok || !profileResponse.ok) throw new Error("Dashboard data could not be loaded");
    const jobsPayload = await jobsResponse.json();
    state.jobs = jobsPayload.jobs;
    state.archivedJobs = jobsPayload.archived_jobs || [];
    state.meta = jobsPayload.meta;
    state.profile = await profileResponse.json();
    updateProfile();
    populateCompanies();
    bindEvents();
    updateOverview();
    renderJobs();
  } catch (error) {
    console.error(error);
    elements.dataStatus.textContent = "Data unavailable";
    elements.jobList.innerHTML = `<div class="empty-state"><strong>Could not load dashboard data.</strong><p>When running locally, serve this folder with <code>python -m http.server</code> instead of opening the HTML file directly.</p></div>`;
  }
}

initialize();
