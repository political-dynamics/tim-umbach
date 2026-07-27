"use strict";

const electionState = {
  payload: null,
  selected: "bundestag",
  model: "poll_only",
};

const electionElements = {
  dataStatus: document.querySelector("#election-data-status"),
  electionSelect: document.querySelector("#election-select"),
  modelSwitch: document.querySelector("#model-switch"),
  modelCaption: document.querySelector("#model-caption"),
  headTitle: document.querySelector("#head-title"),
  leaderInitials: document.querySelector("#leader-initials"),
  leaderName: document.querySelector("#leader-name"),
  leaderParty: document.querySelector("#leader-party"),
  leaderProbability: document.querySelector("#leader-probability"),
  topCoalition: document.querySelector("#top-coalition"),
  topCoalitionParties: document.querySelector("#top-coalition-parties"),
  coalitionProbability: document.querySelector("#coalition-probability"),
  majorityRing: document.querySelector("#majority-ring"),
  majorityProbability: document.querySelector("#majority-probability"),
  majoritySupport: document.querySelector("#majority-support"),
  pollCount: document.querySelector("#poll-count"),
  latestPoll: document.querySelector("#latest-poll"),
  seatTotal: document.querySelector("#seat-total"),
  seatRibbon: document.querySelector("#seat-ribbon"),
  voteChart: document.querySelector("#vote-chart"),
  coalitionList: document.querySelector("#coalition-list"),
  candidateTitle: document.querySelector("#candidate-title"),
  candidateList: document.querySelector("#candidate-list"),
  economyPeriod: document.querySelector("#economy-period"),
  gdpChange: document.querySelector("#gdp-change"),
  unemploymentChange: document.querySelector("#unemployment-change"),
  unemploymentRate: document.querySelector("#unemployment-rate"),
  modelDelta: document.querySelector("#model-delta"),
  logitMae: document.querySelector("#logit-mae"),
  probitMae: document.querySelector("#probit-mae"),
  winnerCalls: document.querySelector("#winner-calls"),
  backtestChart: document.querySelector("#backtest-chart"),
  sourceLinks: document.querySelector("#source-links"),
};

function electionEscape(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[character]);
}

function dateLabel(value) {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(new Date(`${value}T12:00:00`));
}

function signed(value, suffix = "") {
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(1)}${suffix}`;
}

function initials(name) {
  return String(name).split(/\s+/).map((part) => part[0]).slice(0, 2).join("");
}

function safeElectionUrl(value) {
  try {
    const url = new URL(String(value));
    return ["https:", "http:"].includes(url.protocol) ? electionEscape(url.href) : "";
  } catch (_) {
    return "";
  }
}

function selectedElection() {
  return electionState.payload.elections[electionState.selected];
}

function selectedModel() {
  return selectedElection()[electionState.model];
}

function populateElectionSelect() {
  const elections = Object.values(electionState.payload.elections);
  electionElements.electionSelect.innerHTML = elections.map((election) => (
    `<option value="${electionEscape(election.slug)}">${electionEscape(election.label)} · ${electionEscape(election.election_date)}</option>`
  )).join("");
  electionElements.electionSelect.value = electionState.selected;
}

function partyDot(party, color) {
  return `<span class="coalition-party" style="--party:${electionEscape(color)}"><i></i>${electionEscape(party)}</span>`;
}

function renderSummary() {
  const election = selectedElection();
  const model = selectedModel();
  const leader = model.candidates[0];
  const coalition = model.coalitions[0];
  electionElements.headTitle.textContent = election.head_title.toLowerCase();
  electionElements.leaderInitials.textContent = initials(leader.name);
  electionElements.leaderInitials.style.setProperty("--leader-color", leader.color);
  electionElements.leaderName.textContent = leader.name;
  electionElements.leaderParty.textContent = leader.party;
  electionElements.leaderParty.style.setProperty("--party-color", leader.color);
  electionElements.leaderProbability.textContent = `${leader.probability}%`;
  electionElements.topCoalition.textContent = coalition.name;
  electionElements.topCoalitionParties.innerHTML = coalition.parties.map((party, index) => partyDot(party, coalition.party_colors[index])).join("");
  electionElements.coalitionProbability.textContent = `${coalition.formation_probability}%`;
  electionElements.majorityProbability.textContent = `${coalition.majority_probability}%`;
  electionElements.majorityRing.style.setProperty("--value", coalition.majority_probability);
  electionElements.majoritySupport.textContent = `${coalition.seat_share}% projected seat share`;
  electionElements.pollCount.textContent = election.poll_count;
  electionElements.latestPoll.textContent = `Latest poll ${dateLabel(election.latest_poll)}`;
}

function renderSeatRibbon() {
  const election = selectedElection();
  const parties = selectedModel().parties.filter((party) => party.seats > 0 && party.party !== "Sonstige");
  const totalSeats = parties.reduce((total, party) => total + party.seats, 0) || election.seat_count;
  electionElements.seatTotal.textContent = `${election.seat_count} nominal seats`;
  electionElements.seatRibbon.innerHTML = parties.map((party) => {
    const width = party.seats / totalSeats * 100;
    return `<span style="width:${width}%;--party:${party.color}" title="${electionEscape(party.party)}: ${party.seats} modeled seats"><i>${width >= 8 ? electionEscape(party.party) : ""}</i></span>`;
  }).join("");
}

function renderVoteChart() {
  const parties = selectedModel().parties.filter((party) => party.vote >= 1).slice(0, 8);
  const width = 760;
  const rowHeight = 45;
  const padding = { left: 112, right: 53, top: 27, bottom: 33 };
  const height = padding.top + padding.bottom + parties.length * rowHeight;
  const largest = Math.max(...parties.map((party) => party.high));
  const xMax = Math.max(30, Math.ceil((largest + 2) / 10) * 10);
  const x = (value) => padding.left + value / xMax * (width - padding.left - padding.right);
  const ticks = Array.from({ length: xMax / 10 + 1 }, (_, index) => index * 10);
  const grids = ticks.map((tick) => (
    `<line class="vote-grid" x1="${x(tick)}" x2="${x(tick)}" y1="${padding.top - 12}" y2="${height - padding.bottom + 3}"></line>
     <text class="vote-tick" x="${x(tick)}" y="${height - 8}" text-anchor="middle">${tick}%</text>`
  )).join("");
  const threshold = `<line class="vote-threshold" x1="${x(5)}" x2="${x(5)}" y1="${padding.top - 12}" y2="${height - padding.bottom + 3}"></line>`;
  const rows = parties.map((party, index) => {
    const y = padding.top + index * rowHeight;
    return `<g class="vote-row">
      <text class="party-label" x="${padding.left - 15}" y="${y + 5}" text-anchor="end">${electionEscape(party.party)}</text>
      <line class="uncertainty-line" x1="${x(party.low)}" x2="${x(party.high)}" y1="${y}" y2="${y}" style="--party:${party.color}"></line>
      <circle class="vote-mean" cx="${x(party.vote)}" cy="${y}" r="7" style="--party:${party.color}"></circle>
      <text class="vote-value" x="${x(party.high) + 10}" y="${y + 4}">${party.vote.toFixed(1)}%</text>
    </g>`;
  }).join("");
  electionElements.voteChart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${grids}${threshold}${rows}</svg>`;
  electionElements.voteChart.setAttribute(
    "aria-label",
    parties.map((party) => `${party.party} ${party.vote.toFixed(1)} percent, interval ${party.low.toFixed(1)} to ${party.high.toFixed(1)}`).join("; "),
  );
}

function renderCoalitions() {
  const rows = selectedModel().coalitions.slice(0, 5);
  electionElements.coalitionList.innerHTML = rows.map((coalition, index) => (
    `<article class="coalition-row ${index === 0 ? "is-leading" : ""}">
      <div class="coalition-rank">0${index + 1}</div>
      <div class="coalition-detail">
        <div class="coalition-name-row">
          <h3>${electionEscape(coalition.name)}</h3>
          <strong>${coalition.formation_probability}%</strong>
        </div>
        <div class="coalition-dots">${coalition.parties.map((party, partyIndex) => partyDot(party, coalition.party_colors[partyIndex])).join("")}</div>
        <div class="coalition-track"><span style="width:${coalition.formation_probability}%"></span></div>
        <div class="coalition-foot"><span>${coalition.seat_share}% seats</span><span>${coalition.majority_probability}% majority chance</span><span>${coalition.formation_prior}% prior</span></div>
      </div>
    </article>`
  )).join("");
}

function renderCandidates() {
  const election = selectedElection();
  const candidates = selectedModel().candidates.slice(0, 4);
  electionElements.candidateTitle.textContent = election.head_title;
  electionElements.candidateList.innerHTML = candidates.map((candidate, index) => {
    const sourceUrl = safeElectionUrl(candidate.source_url);
    const evidence = sourceUrl
      ? `<a href="${sourceUrl}" target="_blank" rel="noopener noreferrer" title="${electionEscape(candidate.source_title || "Candidate evidence")}">Brave-checked source ↗</a>`
      : `<span>Curated fallback</span>`;
    return `<article class="candidate-row">
      <div class="candidate-rank">${index + 1}</div>
      <div class="candidate-avatar" style="--candidate:${candidate.color}">${electionEscape(initials(candidate.name))}</div>
      <div><h3>${electionEscape(candidate.name)}</h3><p>${electionEscape(candidate.party)}</p><div class="candidate-evidence">${evidence}</div></div>
      <div class="candidate-probability"><strong>${candidate.probability}%</strong><span style="width:${candidate.probability}%;--candidate:${candidate.color}"></span></div>
    </article>`;
  }).join("");
}

function renderEconomy() {
  const economy = electionState.payload.meta.economy;
  const election = selectedElection();
  const pollLeader = election.poll_only.candidates[0];
  const economyMatch = election.economy.candidates.find((candidate) => candidate.name === pollLeader.name);
  const economyProbability = economyMatch ? economyMatch.probability : 0;
  const delta = economyProbability - pollLeader.probability;
  electionElements.economyPeriod.textContent = economy.period;
  electionElements.gdpChange.textContent = signed(economy.gdp_change, "%");
  electionElements.unemploymentChange.textContent = signed(economy.unemployment_change, " pp");
  electionElements.unemploymentRate.textContent = `${economy.unemployment_rate.toFixed(1)}%`;
  electionElements.modelDelta.textContent = `${signed(delta, " pp")} · ${pollLeader.name}`;
  electionElements.modelDelta.classList.toggle("negative", delta < 0);
}

function renderBacktests() {
  const summary = electionState.payload.backtest_summary;
  const rows = electionState.payload.backtests;
  electionElements.logitMae.textContent = `${summary.logit_mae.toFixed(2)} pp`;
  electionElements.probitMae.textContent = `${summary.probit_mae.toFixed(2)} pp`;
  electionElements.winnerCalls.textContent = `${electionState.model === "poll_only" ? summary.logit_winners : summary.probit_winners}/${summary.count}`;
  const max = 4;
  electionElements.backtestChart.innerHTML = rows.map((row) => {
    const logitPosition = Math.min(row.logit_mae / max * 100, 100);
    const probitPosition = Math.min(row.probit_mae / max * 100, 100);
    const left = Math.min(logitPosition, probitPosition);
    const width = Math.max(Math.abs(probitPosition - logitPosition), 0.8);
    return `<article class="backtest-row">
      <div><strong>${electionEscape(row.target)}</strong><span>${row.poll_count} pre-election polls · winner ${electionEscape(row.actual_winner)}</span></div>
      <div class="backtest-track">
        <i class="backtest-connector" style="left:${left}%;width:${width}%"></i>
        <button class="backtest-point logit-point ${electionState.model === "poll_only" ? "active" : ""}" style="left:${logitPosition}%" type="button" aria-label="${electionEscape(row.target)} poll-only MAE ${row.logit_mae} percentage points"><span>${row.logit_mae.toFixed(2)}</span></button>
        <button class="backtest-point probit-point ${electionState.model === "economy" ? "active" : ""}" style="left:${probitPosition}%" type="button" aria-label="${electionEscape(row.target)} economy MAE ${row.probit_mae} percentage points"><span>${row.probit_mae.toFixed(2)}</span></button>
      </div>
    </article>`;
  }).join("");
}

function renderSources() {
  electionElements.sourceLinks.innerHTML = electionState.payload.meta.sources.map((source) => (
    `<a href="${electionEscape(source.url)}" target="_blank" rel="noopener noreferrer">${electionEscape(source.label)} <span aria-hidden="true">↗</span></a>`
  )).join("");
}

function updateModelControls() {
  electionElements.modelSwitch.querySelectorAll("button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.model === electionState.model));
  });
  electionElements.modelCaption.textContent = electionState.model === "poll_only"
    ? "Recency and sample-weighted polling on the log-odds scale."
    : "Polling plus capped GDP and unemployment effects on a latent-normal scale.";
}

function renderElectionPage() {
  updateModelControls();
  renderSummary();
  renderSeatRibbon();
  renderVoteChart();
  renderCoalitions();
  renderCandidates();
  renderEconomy();
  renderBacktests();
}

function bindElectionEvents() {
  electionElements.electionSelect.addEventListener("change", () => {
    electionState.selected = electionElements.electionSelect.value;
    renderElectionPage();
  });
  electionElements.modelSwitch.addEventListener("click", (event) => {
    const button = event.target.closest("[data-model]");
    if (!button) return;
    electionState.model = button.dataset.model;
    renderElectionPage();
  });
}

async function initializeElectionLab() {
  try {
    const response = await fetch("data/election_models.json");
    if (!response.ok) throw new Error("Election model data could not be loaded");
    electionState.payload = await response.json();
    if (!electionState.payload.elections[electionState.selected]) {
      electionState.selected = Object.keys(electionState.payload.elections)[0];
    }
    populateElectionSelect();
    bindElectionEvents();
    renderSources();
    renderElectionPage();
    const candidateMeta = electionState.payload.meta.candidate_search || {};
    const candidateDate = String(candidateMeta.last_successful_at || "").slice(0, 10);
    const candidateStatus = candidateDate ? ` · candidates checked ${dateLabel(candidateDate)}` : " · candidate cache pending";
    electionElements.dataStatus.textContent = `DAWUM updated ${dateLabel(electionState.payload.meta.dawum_updated_at.slice(0, 10))}${candidateStatus} · model v${electionState.payload.meta.method_version}`;
  } catch (error) {
    console.error(error);
    electionElements.dataStatus.textContent = "Forecast data unavailable";
    electionElements.voteChart.innerHTML = `<div class="empty-state"><strong>Could not load the election snapshot.</strong><p>Serve this folder over HTTP when testing locally.</p></div>`;
  }
}

initializeElectionLab();
