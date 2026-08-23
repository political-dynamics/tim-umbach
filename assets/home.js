"use strict";

const homePlot = {
  jobs: [],
  chart: document.querySelector("#home-opportunity-chart"),
  view: document.querySelector("#home-plot-view"),
  title: document.querySelector("#home-role-title"),
  company: document.querySelector("#home-role-company"),
  match: document.querySelector("#home-role-match"),
  salary: document.querySelector("#home-role-salary"),
};

const svgNamespace = "http://www.w3.org/2000/svg";

function homeMoney(value) {
  return new Intl.NumberFormat("en-DE", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(value);
}

function hasPublishedSalary(job) {
  return String(job.salary_source || "").toLowerCase().includes("employer");
}

function displayedJobs() {
  const ranked = [...homePlot.jobs].sort((a, b) => b.match_score - a.match_score);
  if (homePlot.view.value === "advertised") {
    return ranked.filter(hasPublishedSalary).slice(0, 14);
  }
  return ranked.slice(0, 14);
}

function showHomeRole(job, point) {
  homePlot.chart.querySelectorAll(".home-job-dot").forEach((candidate) => candidate.classList.remove("is-active"));
  if (point) point.classList.add("is-active");
  homePlot.title.textContent = job.title;
  homePlot.company.textContent = `${job.company} · ${job.location || "Location not stated"}`;
  homePlot.match.textContent = `${job.match_score}%`;
  homePlot.salary.textContent = `${homeMoney(job.salary_min)}–${homeMoney(job.salary_max)}`;
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS(svgNamespace, name);
  Object.entries(attributes).forEach(([attribute, value]) => element.setAttribute(attribute, value));
  return element;
}

function addText(svg, value, attributes) {
  const label = svgElement("text", attributes);
  label.textContent = value;
  svg.append(label);
}

function renderHomePlot() {
  const jobs = displayedJobs();
  if (!jobs.length) {
    homePlot.chart.innerHTML = '<div class="empty-state"><strong>No published salary ranges in this snapshot.</strong><p>Switch to Top matches to explore the current roles.</p></div>';
    return;
  }

  const width = 760;
  const height = 310;
  const padding = { left: 58, right: 24, top: 24, bottom: 42 };
  const xMin = 45;
  const xMax = 100;
  const salaries = jobs.map((job) => Number(job.salary_mid));
  const yMin = Math.floor(Math.min(50000, ...salaries) / 10000) * 10000;
  const yMax = Math.ceil(Math.max(95000, ...salaries) / 10000) * 10000;
  const x = (value) => padding.left + ((value - xMin) / (xMax - xMin)) * (width - padding.left - padding.right);
  const y = (value) => height - padding.bottom - ((value - yMin) / (yMax - yMin)) * (height - padding.top - padding.bottom);
  const svg = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" });
  const xTicks = [50, 60, 70, 80, 90, 100];
  const yTicks = [];
  for (let tick = yMin; tick <= yMax; tick += 10000) yTicks.push(tick);

  xTicks.forEach((tick) => {
    svg.append(svgElement("line", { class: "home-grid-line", x1: x(tick), x2: x(tick), y1: padding.top, y2: height - padding.bottom }));
    addText(svg, tick, { x: x(tick), y: height - 16, "text-anchor": "middle" });
  });
  yTicks.forEach((tick) => {
    svg.append(svgElement("line", { class: "home-grid-line", x1: padding.left, x2: width - padding.right, y1: y(tick), y2: y(tick) }));
    addText(svg, `€${tick / 1000}k`, { x: padding.left - 10, y: y(tick) + 4, "text-anchor": "end" });
  });
  addText(svg, "Profile match →", { class: "home-axis-label", x: width - padding.right, y: height - 2, "text-anchor": "end" });

  jobs.forEach((job, index) => {
    const point = svgElement("circle", {
      class: `home-job-dot${hasPublishedSalary(job) ? " is-published" : ""}`,
      cx: x(Math.max(xMin, job.match_score)),
      cy: y(Number(job.salary_mid)),
      r: hasPublishedSalary(job) ? 9 : 7,
      tabindex: "0",
      role: "button",
      "aria-label": `${job.company}, ${job.title}: ${job.match_score}% match, ${homeMoney(job.salary_mid)}`,
    });
    const title = svgElement("title");
    title.textContent = `${job.company} — ${job.title}`;
    point.append(title);
    point.addEventListener("click", () => showHomeRole(job, point));
    point.addEventListener("focus", () => showHomeRole(job, point));
    point.addEventListener("mouseenter", () => showHomeRole(job, point));
    svg.append(point);
    if (index === 0) requestAnimationFrame(() => showHomeRole(job, point));
  });

  homePlot.chart.replaceChildren(svg);
}

async function initializeHomePlot() {
  try {
    const response = await fetch("data/jobs.json");
    if (!response.ok) throw new Error("Job Radar data could not be loaded");
    const payload = await response.json();
    homePlot.jobs = payload.jobs || [];
    homePlot.view.addEventListener("change", renderHomePlot);
    renderHomePlot();
  } catch (error) {
    console.error(error);
    homePlot.chart.innerHTML = '<div class="empty-state"><strong>The interactive preview is unavailable.</strong><p>Open Job Radar for the full current snapshot.</p></div>';
    homePlot.title.textContent = "Preview unavailable";
    homePlot.company.textContent = "The full work example is still available.";
  }
}

initializeHomePlot();
