/* Static, inspectable Amrum snapshot. No client-side scraping or booking calls. */
(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const money = (n) => Number.isFinite(n) ? new Intl.NumberFormat('de-DE', {style: 'currency', currency: 'EUR', maximumFractionDigits: 0}).format(n) : 'Unverified';
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const link = (url) => { try { const u = new URL(url); return u.protocol === 'https:' ? esc(u.href) : '#'; } catch { return '#'; } };
  const dateLabel = (s) => new Date(`${s}T12:00:00Z`).toLocaleDateString('en-GB', {day:'numeric', month:'short', timeZone:'UTC'});
  const seasonNames = {A:'Hauptsaison', B:'Nebensaison', C:'Randsaison', christmas:'Christmas / New Year'};
  const state = {data:null, season:'A', flat:'wohnung-1', year:'2027', map:null, layer:null, targetMarker:null, mapHouse:'target', fitted:false};
  const currentFlat = () => state.data.apartments.find((a) => a.id === state.flat);
  const selectedPrice = (a) => a.market?.seasons[state.season];
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
        <div class="flat-tariff">amrum.sh ${state.year}: <strong>${money(tariff)}</strong><br>${a.market?.property_count || 0} comparable properties · low confidence</div>
        <button class="flat-action" type="button" data-flat="${a.id}" aria-pressed="${a.id === state.flat}">${a.id === state.flat ? 'Viewing evidence' : 'View map & evidence'} <span aria-hidden="true">↘</span></button>
      </article>`;
    }).join('');
  }

  function renderCalendar() {
    const a = currentFlat(), periods = state.data.seasons[state.year];
    $('#calendar-caption').textContent = `${a.name} · ${state.year} · all amounts per whole apartment`;
    const max = Math.max(...Object.values(a.market?.seasons || {}).map((p) => p.estimate), 1);
    $('#season-chart').innerHTML = periods.map((s) => {
      const p = a.market?.seasons[s.season];
      return `<div class="season-column ${s.season === state.season ? 'selected' : ''}"><strong>${p ? money(p.estimate) : '—'}</strong><div class="season-bar-box"><div class="season-bar" style="height:${p ? Math.round(p.estimate/max*100) : 0}%"></div></div><small>${esc(s.label)}</small></div>`;
    }).join('');
    $('#calendar-rows').innerHTML = periods.map((s,i) => {
      const p = a.market?.seasons[s.season], tariff = a.tariffs[state.year]?.[i];
      return `<tr class="${s.season === state.season ? 'active-row' : ''}"><td><strong>${esc(s.label)}</strong><small>${dates(s)}</small></td><td>${p ? money(p.estimate) : '—'}</td><td>${p ? `${money(p.low)}–${money(p.high)}` : '—'}</td><td>${money(tariff)}</td><td>${p ? money(p.estimate*7) : '—'}</td></tr>`;
    }).join('');
    const warning = $('#tariff-warning');
    warning.hidden = !a.warning;
    warning.textContent = a.warning ? `${a.name}: ${a.warning} Source table: ${a.ambiguous_tariff.map(money).join(' / ')}. These values are not used as verified 2027 tariffs.` : '';
  }

  function renderEvidence() {
    const a = currentFlat();
    const peers = (a.market?.peers || []).map((p) => ({...state.data.observations.find((r) => r.id === p.id), ...p}));
    $('#peer-note').textContent = `${a.name}: ${peers.length} distinct properties, selected by capacity and size. Adjusted amounts are B-season proxies after known cleaning and size adjustment. The same peers determine every season.`;
    $('#peer-rows').innerHTML = peers.map((r) => `<tr><td><a href="${link(r.url)}">${esc(r.property)}</a><small>${esc(r.name)}</small></td><td>${r.area} m² / ${r.guests} guests</td><td>${money(r.nightly)}</td><td>${r.cleaning == null ? 'Not specified' : money(r.cleaning)}</td><td>${money(r.adjusted)}</td></tr>`).join('');
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
    return (row.nightly + (row.cleaning || 0)/7) * currentFlat().market.seasons[state.season].factor;
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
    $('#year-note').textContent = state.year === '2027' ? '2027 calendar · estimates held at the observed 2026 market level, with no assumed inflation. Compare with the owner’s 2027 tariffs where verified.' : '2026 calendar · market benchmark from the September 2026 asking-price snapshot.';
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
    $('#source-evidence').innerHTML = `<p>${coverage} ${d.observations.length} unit records extracted; ${d.observations.filter((r) => !r.excluded).length} eligible before apartment-specific matching. ${d.meta.errors.length} source pages unavailable or unparseable. ${directory?.errors.length || 0} directory detail links could not be resolved.</p><ul>${Object.entries(excluded).map(([why,n]) => `<li>${n}: ${esc(why)}</li>`).join('')}</ul><p><strong>Seasonal evidence:</strong> providers receive equal weight, regardless of how many flats they list.</p><ul>${d.seasonal_evidence.map((s) => `<li><a href="${link(s.url)}">${esc(s.name)}</a> · ${s.tariff_year || 'tariff year not stated'}<br>${esc(s.note)}<br>Relative to B: A ${s.ratios.A.toFixed(3)}× · C ${s.ratios.C.toFixed(3)}× · Christmas ${s.ratios.christmas.toFixed(3)}×</li>`).join('')}</ul><p>Brave Search discovery (${esc(d.meta.discovery.retrieved_on)}):</p><ul>${d.meta.discovery.queries.map((q) => `<li><a href="https://search.brave.com/search?q=${encodeURIComponent(q)}">${esc(q)}</a></li>`).join('')}</ul><p>Search snippets are discovery clues, not price observations. Collection timestamps, source URLs, extraction failures, and source hashes are included in the downloadable snapshot. No source photographs or personal contact data are copied.</p>`;
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
      setupSources(); render();
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
