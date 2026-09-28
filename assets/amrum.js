/* Static, inspectable Amrum snapshot. No client-side scraping or booking calls. */
(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const money = (n) => Number.isFinite(n) ? new Intl.NumberFormat('de-DE', {style: 'currency', currency: 'EUR', maximumFractionDigits: 0}).format(n) : 'Unverified';
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const link = (url) => { try { const u = new URL(url); return u.protocol === 'https:' ? esc(u.href) : '#'; } catch { return '#'; } };
  const dateLabel = (s) => new Date(`${s}T12:00:00Z`).toLocaleDateString('en-GB', {day:'numeric', month:'short', timeZone:'UTC'});
  const seasonNames = {A:'Hauptsaison', B:'Nebensaison', C:'Off-season / Vor- & Nachsaison', christmas:'Christmas / New Year'};
  const state = {data:null, season:'A', flat:'wohnung-1', year:'2027', map:null, layer:null, targetMarker:null, mapHouse:'target', fitted:false};
  const currentFlat = () => state.data.apartments.find((a) => a.id === state.flat);
  const pricesFor = (a) => a.market?.seasons_by_year?.[state.year] || a.market?.seasons;
  const selectedPrice = (a) => pricesFor(a)?.[state.season];
  const dates = (s) => `${dateLabel(s.start)} – ${dateLabel(s.end)}${s.end.slice(0,4) !== state.year ? ` ${s.end.slice(0,4)}` : ''}`;
  function advertised(a, season) {
    const index = {C:0, B:1, A:2, christmas:5}[season];
    return a.tariffs[state.year]?.[index] ?? null;
  }

  function renderCards() {
    $('#apartments').innerHTML = state.data.apartments.map((a) => {
      const p = selectedPrice(a), tariff = advertised(a, state.season);
      return `<article class="apartment-card ${a.id === state.flat ? 'selected' : ''}">
        <h3>${esc(a.name)}</h3><p class="flat-meta">${a.area} m² · ${a.guests} guests · ${a.bedrooms ? `${a.bedrooms} bedroom${a.bedrooms > 1 ? 's' : ''}` : 'Studio'}</p>
        <span class="price-label">Estimated market price / night</span><strong class="flat-price">${p ? money(p.estimate) : '—'}</strong>
        <p class="flat-range">${p ? `${money(p.low)} – ${money(p.high)}` : 'Insufficient comparable evidence'}</p>
        <div class="flat-tariff">amrum.sh ${state.year}: <strong>${money(tariff)}</strong><br>${a.market?.observation_count || 0} apartments · ${a.market?.property_count || 0} buildings in model</div>
        <button class="flat-action" type="button" data-flat="${a.id}" aria-pressed="${a.id === state.flat}">${a.id === state.flat ? 'Viewing evidence' : 'View map & evidence'} <span aria-hidden="true">↘</span></button>
      </article>`;
    }).join('');
  }

  function renderCalendar() {
    const a = currentFlat(), periods = state.data.seasons[state.year];
    $('#calendar-caption').textContent = `${a.name} · ${state.year} · all amounts per whole apartment`;
    const max = Math.max(...Object.values(pricesFor(a) || {}).map((p) => p.estimate), 1);
    $('#season-chart').innerHTML = periods.map((s) => {
      const p = pricesFor(a)?.[s.season];
      return `<div class="season-column ${s.season === state.season ? 'selected' : ''}"><strong>${p ? money(p.estimate) : '—'}</strong><div class="season-bar-box"><div class="season-bar" style="height:${p ? Math.round(p.estimate/max*100) : 0}%"></div></div><small>${esc(s.label)}</small></div>`;
    }).join('');
    $('#calendar-rows').innerHTML = periods.map((s,i) => {
      const p = pricesFor(a)?.[s.season], tariff = a.tariffs[state.year]?.[i];
      return `<tr class="${s.season === state.season ? 'active-row' : ''}"><td><strong>${esc(s.label)}</strong><small>${dates(s)}</small></td><td>${p ? money(p.estimate) : '—'}</td><td>${p ? `${money(p.low)}–${money(p.high)}` : '—'}</td><td>${money(tariff)}</td><td>${p ? money(p.estimate*7) : '—'}</td></tr>`;
    }).join('');
    const warning = $('#tariff-warning');
    warning.hidden = !a.warning && !(state.year === '2027' && a.id === 'wohnung-1');
    warning.textContent = a.warning || a.tariff_note || '';
  }

  function renderEvidence() {
    const a = currentFlat();
    const peers = (a.market?.peers || []).map((p) => ({...state.data.observations.find((r) => r.id === p.id), ...p}));
    $('#peer-note').textContent = `${a.name}: ${peers.length} nearby examples for inspection. The regression uses ${a.market?.observation_count || 0} apartments across ${a.market?.property_count || 0} buildings island-wide. Adjusted examples use the fitted size, capacity, bedroom and location effects; this table is not the training sample.`;
    $('#peer-rows').innerHTML = peers.map((r) => `<tr><td><a href="${link(r.url)}">${esc(r.property)}</a><small>${esc(r.name)}</small></td><td>${r.area} m² / ${r.guests} guests</td><td>${money(r.nightly)}</td><td>${r.cleaning == null ? 'Not specified' : money(r.cleaning)}</td><td>${money(r.adjusted)}</td></tr>`).join('');
  }

  function renderHistory() {
    const history = state.data.history_series;
    if (!history?.periods?.length) {
      $('#history-chart').textContent = 'No historical price observations are available.';
      return;
    }
    const metric = $('#history-metric').value || 'matched_index';
    const rows = history.periods.filter((r) => Number.isFinite(r[metric]));
    const label = (m) => new Date(`${m}-01T12:00:00Z`).toLocaleDateString('en-GB', {month:'short',year:'numeric',timeZone:'UTC'});
    const monthNumber = (m) => Number(m.slice(0,4))*12 + Number(m.slice(5,7))-1;
    const fmt = (v) => metric === 'median_nightly' ? money(v) : v.toFixed(1);
    $('#history-caption').textContent = metric === 'matched_index' ? `Same-apartment asking-price index · ${label(history.reference_month)} = 100. Each historical price is divided by that apartment’s current price before taking the monthly median.` : 'Published nightly asking prices · monthly medians for apartments also in the current eligible sample. Different monthly samples can move this series.';
    if (!rows.length) { $('#history-chart').textContent = 'No matching apartment prices are available.'; return; }
    const width=760, height=300, left=65, right=25, top=30, bottom=65;
    const minMonth=monthNumber(rows[0].month), maxMonth=monthNumber(rows[rows.length-1].month);
    const min=Math.floor(Math.min(...rows.map((r)=>r[metric]), metric === 'matched_index' ? 100 : Infinity)/10)*10-10;
    const max=Math.ceil(Math.max(...rows.map((r)=>r[metric]), metric === 'matched_index' ? 100 : -Infinity)/10)*10+10;
    const x=(r)=>left+(monthNumber(r.month)-minMonth)/Math.max(1,maxMonth-minMonth)*(width-left-right);
    const y=(v)=>height-bottom-(v-min)/(max-min)*(height-top-bottom);
    let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="history-svg-title history-svg-desc"><title id="history-svg-title">${metric === 'median_nightly' ? 'Historical asking prices in euros per night' : 'Same-apartment asking-price index'}</title><desc id="history-svg-desc">${esc(history.note)} Full values and sample sizes are in the monthly table below.</desc>`;
    for(let i=0;i<=4;i++) {
      const v=min+(max-min)*i/4;
      svg+=`<line x1="${left}" y1="${y(v)}" x2="${width-right}" y2="${y(v)}" class="history-grid"/><text x="${left-10}" y="${y(v)+4}" text-anchor="end">${esc(fmt(v))}</text>`;
    }
    if (metric === 'matched_index') svg+=`<line x1="${left}" y1="${y(100)}" x2="${width-right}" y2="${y(100)}" class="history-reference"/>`;
    svg+=`<polyline points="${rows.map((r)=>`${x(r)},${y(r[metric])}`).join(' ')}" class="history-line"/>`;
    rows.forEach((r)=>{
      const caption=`${label(r.month)}: ${fmt(r[metric])}; ${r.apartments} apartments, ${r.buildings} buildings, ${r.matched_apartments} matched apartments`;
      svg+=`<circle cx="${x(r)}" cy="${y(r[metric])}" r="5" tabindex="0" aria-label="${esc(caption)}"><title>${esc(caption)}</title></circle><text x="${x(r)}" y="${height-bottom+22}" text-anchor="end" transform="rotate(-30 ${x(r)} ${height-bottom+22})">${esc(label(r.month))}</text>`;
    });
    $('#history-chart').innerHTML=svg+'</svg>';
    $('#history-rows').innerHTML=history.periods.map((r)=>`<tr><td>${esc(label(r.month))}</td><td>${r.apartments} / ${r.buildings}</td><td>${money(r.median_nightly)}</td><td>${r.matched_index == null ? '—' : r.matched_index.toFixed(1)}</td><td>${r.matched_apartments}</td></tr>`).join('');
  }

  function propertyGroups() {
    const peers = new Set(currentFlat().market?.peers.map((p) => p.id) || []);
    const groups = new Map();
    for (const r of state.data.observations) {
      if (r.excluded || !r.coordinates || (!$('#all-properties').checked && !peers.has(r.id))) continue;
      const group = r.building || r.house;
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(r);
    }
    return groups;
  }

  function proxy(row) {
    return (row.nightly + (row.cleaning || 0)/7) * pricesFor(currentFlat())[state.season].factor;
  }

  function showMapDetail(house) {
    state.mapHouse = house;
    const a = currentFlat(), p = selectedPrice(a);
    if (house === 'target') {
      $('#map-detail').innerHTML = `<p class="overline">Die Alte Schule · model estimate</p><h3>${esc(a.name)}</h3><p>${a.area} m² · ${a.guests} guests<br>${seasonNames[state.season]} · ${state.year}</p><strong class="map-price">${p ? money(p.estimate) : '—'}</strong><p>per apartment / night<br>${p ? `${money(p.low)}–${money(p.high)} sensitivity range` : 'Insufficient evidence'}</p><p>All four flats share this building. Select another flat above to compare it.</p><a href="${link(a.url)}">Apartment details on amrum.sh ↗</a>`;
    } else {
      const rows = propertyGroups().get(house);
      if (!rows?.length) return showMapDetail('target');
      $('#map-detail').innerHTML = `<p class="overline">Comparable · seasonal proxy</p><h3>${esc(rows[0].property)}</h3><p>${seasonNames[state.season]} · ${state.year}</p><ul class="map-detail-list">${rows.map((r) => `<li><strong>${esc(r.name)}: ${money(proxy(r))}</strong><br>${r.area} m² · ${r.guests} guests<br>Observed portal rate: ${money(r.nightly)}<br>Cleaning: ${r.cleaning == null ? 'not specified' : `${money(r.cleaning)} per stay`}</li>`).join('')}</ul><p>Seasonally scaled asking price; availability and full fees unverified.</p><a href="${link(rows[0].url)}">Official Amrum listing ↗</a>`;
    }
  }

  function renderMap() {
    const groups = propertyGroups();
    $('#map-description').textContent = `${seasonNames[state.season]} · ${currentFlat().name} · ${groups.size} comparison properties shown. Click or focus a marker and press Enter for its evidence.`;
    if (!window.L) {
      $('#amrum-map').innerHTML = '<p class="research-note">The map library could not load. Every selected property and its source remain available in the evidence table below.</p>';
      showMapDetail('target');
      return;
    }
    if (!state.map) {
      state.map = window.L.map('amrum-map', {scrollWheelZoom:false}).setView([54.650,8.351], 14);
      window.L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19, attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'}).addTo(state.map);
      state.layer = (window.L.markerClusterGroup ? window.L.markerClusterGroup({maxClusterRadius:35, disableClusteringAtZoom:16, iconCreateFunction:(cluster) => window.L.divIcon({className:'amrum-cluster', html:`<span>${cluster.getChildCount()}</span>`, iconSize:[36,36]})}) : window.L.layerGroup()).addTo(state.map);
    }
    state.layer.clearLayers();
    if (state.targetMarker) state.map.removeLayer(state.targetMarker);
    const points = [];
    function marker(coords, label, title, house, target=false) {
      if (!coords) return;
      points.push(coords);
      const icon = window.L.divIcon({className:'amrum-marker', html:`<span class="amrum-pin ${target ? 'target' : ''}">${esc(label)}</span>`, iconSize:[75,30], iconAnchor:[35,15]});
      const pin = window.L.marker(coords, {icon, title, keyboard:true, zIndexOffset:target ? 1000 : 0}).addTo(target ? state.map : state.layer).on('click', () => showMapDetail(house));
      if (target) state.targetMarker = pin;
    }
    for (const [house, rows] of groups) {
      const values = rows.map(proxy), min = Math.min(...values), max = Math.max(...values);
      const label = Math.round(min) === Math.round(max) ? money(min) : `${Math.round(min)}–${Math.round(max)} €`;
      marker(rows[0].coordinates, label, `${rows[0].property}: ${label}, seasonal proxy`, house);
    }
    const a = currentFlat(), p = selectedPrice(a);
    marker(a.coordinates, `Alte Schule ${p ? money(p.estimate) : ''}`, `${a.name}, ${p ? money(p.estimate) : 'unavailable'}, market estimate`, 'target', true);
    if (!state.fitted && points.length) {
      state.map.fitBounds(points, {padding:[45,45], maxZoom:15});
      state.fitted = true;
    }
    showMapDetail(state.mapHouse);
  }

  function render() {
    $('#year-note').textContent = state.year === '2027' ? '2027 calendar · estimates held at the observed 2026 market level, with no assumed inflation. Compare with the owner’s published main-table tariffs.' : '2026 calendar · market benchmark from the September 2026 asking-price snapshot.';
    const validation = state.data.model?.validation;
    $('#model-validation').textContent = validation ? `Model check: ${money(validation.mae_eur)} average absolute error per night on held-out buildings, compared with ${money(validation.baseline_mae_eur)} for a median-price baseline. Five building-grouped folds also select the ridge penalty; this is not a separate final test or a booking-price validation.` : 'Insufficient data to fit the market model.';
    const coverage = state.data.seasonal_coverage;
    $('#season-coverage').textContent = `${state.season === 'C' ? 'Off-season covers January–February and late October–December. ' : ''}Seasonal adjustments use ${coverage.off_season_tariff_apartments} apartments at ${coverage.independent_tariff_providers} independent owners. The broad portal sample contains no date-specific off-season quotes.`;
    const archive = state.data.archive_coverage;
    $('#archive-note').textContent = archive ? `Internet Archive: ${archive.observation_count} saved amrum.de unit records from ${archive.page_count} property-page captures (${archive.by_year['2025']} from 2025; ${archive.by_year['2026']} from 2026). ${archive.errors} captures remain unavailable or deferred. These are observation dates, not stay dates; the archived from-prices are retained separately from the current model.` : '';
    const yearEffect = state.data.year_effects?.['2027'];
    $('#history-note').textContent = yearEffect ? `${state.data.historical_tariffs.length} dated apartment/season/year tariffs from four independent owners cover 2025–2027. A 2027 year effect was tested on ${yearEffect.matched_season_pairs} matched apartment-season pairs from ${yearEffect.provider_count} owners. Held-owner log error: ${yearEffect.validation.year_dummy_log_rmse} with the year effect versus ${yearEffect.validation.no_year_dummy_log_rmse} without it. ${yearEffect.accepted ? 'This descriptive test favors a year effect.' : 'The common year adjustment did not improve prediction.'} Historical year effects are not applied to the current-data model. The 2025 comparison has only one owner and cannot establish an island-wide annual effect.` : '';
    $('#season-dates').textContent = state.data.seasons[state.year].filter((s) => s.season === state.season).map(dates).join(' / ');
    document.querySelectorAll('[data-season]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.season === state.season)));
    $('#flat').value = state.flat;
    $('#map-season').value = state.season;
    renderCards(); renderCalendar(); renderEvidence(); renderMap();
  }

  function setupSources() {
    const d = state.data;
    const excluded = {};
    for (const row of d.observations) if (row.excluded) excluded[row.excluded] = (excluded[row.excluded] || 0) + 1;
    const directory = d.meta.directory;
    const eligible = d.observations.filter((r) => !r.excluded);
    $('#coverage').innerHTML = [[d.observations.length,'official unit records'],[d.observations.filter((r) => r.nightly != null).length,'with an advertised price'],[eligible.length,'eligible island apartments'],[eligible.filter((r) => r.locality.toLowerCase().startsWith('nebel')).length,'eligible Nebel apartments']].map(([n,label]) => `<div><strong>${n}</strong><span>${label}</span></div>`).join('');
    const coverage = directory ? `${directory.directory_entries || directory.entries} directory entries across ${directory.pages} pages; ${directory.entries} links to booking records; ${directory.provider_records} distinct provider URLs discovered (including Brave sources).` : `${d.sources.filter((s) => s.url.includes('/ukv/house/')).length} provider pages retrieved.`;
    $('#source-evidence').innerHTML = `<p>${coverage} ${d.observations.length} unit records extracted; ${d.observations.filter((r) => !r.excluded).length} eligible for the island-wide regression. ${d.meta.errors.length} source pages unavailable or unparseable. ${directory?.errors.length || 0} directory detail links could not be resolved.</p><ul>${Object.entries(excluded).map(([why,n]) => `<li>${n}: ${esc(why)}</li>`).join('')}</ul><p><strong>Seasonal evidence:</strong> providers receive equal weight, regardless of how many flats they list.</p><ul>${d.seasonal_evidence.map((s) => `<li><a href="${link(s.url)}">${esc(s.name)}</a> · ${s.tariff_year || 'tariff year not stated'}<br>${esc(s.note)}<br>Relative to B: A ${s.ratios.A.toFixed(3)}× · C ${s.ratios.C.toFixed(3)}× · Christmas ${s.ratios.christmas.toFixed(3)}×</li>`).join('')}</ul><p><strong>Historical tariffs:</strong> ${d.historical_tariffs?.length || 0} dated records, with native season codes and fee provenance in the snapshot. ${[...new Set((d.historical_tariffs || []).map((r) => r.url))].map((url) => `<a href="${link(url)}">${esc(new URL(url).hostname)}</a>`).join(' · ')}.</p><p>Brave Search discovery (${esc(d.meta.discovery.retrieved_on)}):</p><ul>${d.meta.discovery.queries.map((q) => `<li><a href="https://search.brave.com/search?q=${encodeURIComponent(q)}">${esc(q)}</a></li>`).join('')}</ul><p>Search snippets are discovery clues, not price observations. Collection timestamps, source URLs, extraction failures, and source hashes are included in the downloadable snapshot. No source photographs or personal contact data are copied.</p>`;
  }

  async function init() {
    try {
      const response = await fetch('data/amrum_market.json');
      if (!response.ok) throw new Error('Snapshot unavailable');
      const data = await response.json();
      if (!Array.isArray(data.apartments) || data.apartments.length !== 4) throw new Error('Invalid apartment snapshot');
      state.data = data;
      const stamp = new Date(data.meta.retrieved_at).toLocaleDateString('en-GB', {day:'numeric',month:'long',year:'numeric'});
      const age = (Date.now()-new Date(data.meta.retrieved_at))/86400000;
      $('#snapshot').textContent = `Observed ${stamp} · ${data.observations.length} official unit records${age > 30 ? ' · snapshot over 30 days old' : ''}`;
      $('#flat').innerHTML = data.apartments.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join('');
      setupSources(); render(); renderHistory();
      $('#history-metric').addEventListener('change', renderHistory);
      $('.season-control').addEventListener('click', (event) => {
        const button = event.target.closest('[data-season]');
        if (button) { state.season = button.dataset.season; render(); }
      });
      $('#apartments').addEventListener('click', (event) => {
        const button = event.target.closest('[data-flat]');
        if (button) { state.flat=button.dataset.flat; state.mapHouse='target'; state.fitted=false; render(); $(`[data-flat="${state.flat}"]`).focus({preventScroll:true}); }
      });
      $('#flat').addEventListener('change', (event) => { state.flat=event.target.value; state.mapHouse='target'; state.fitted=false; render(); });
      $('#year').addEventListener('change', (event) => { state.year=event.target.value; render(); });
      $('#map-season').addEventListener('change', (event) => { state.season=event.target.value; render(); });
      $('#all-properties').addEventListener('change', () => { state.fitted=false; renderMap(); });
    } catch (error) {
      $('#snapshot').textContent = 'Price data could not load. Reload the page or download the snapshot below.';
      $('#apartments').innerHTML = '<p class="research-note">The price explorer is temporarily unavailable. <a href="data/amrum_market.json">Open the source snapshot</a>.</p>';
      console.error(error);
    }
  }
  init();
})();
