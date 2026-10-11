
async function applyLocationContext(opts) {
  const params = new URLSearchParams();
  if (opts.state) params.set('state', opts.state);
  if (opts.city) params.set('city', opts.city);
  if (opts.address) params.set('address', opts.address);
  if (opts.pincode) params.set('pincode', opts.pincode);
  if (opts.lat != null) params.set('lat', opts.lat);
  if (opts.lng != null) params.set('lng', opts.lng);
  try {
    const resp = await fetch(`/api/location-context?${params.toString()}`, {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
    });
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data || !data.found) return;

    const stateEl = document.getElementById('state');
    const tariffEl = document.getElementById('tariff-per-kwh');
    const netModeEl = document.getElementById('net-metering-mode');
    const discomEl = document.getElementById('discom-readout');

    if (stateEl && data.state) {
      // Select matching option if present
      const opt = Array.from(stateEl.options).find(
        (o) => o.value.toLowerCase() === String(data.state).toLowerCase()
      );
      if (opt) {
        stateEl.value = opt.value;
        stateEl.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }
    if (tariffEl && data.tariff && data.tariff.rate_per_kwh != null) {
      tariffEl.value = Number(data.tariff.rate_per_kwh).toFixed(2);
    }
    if (netModeEl && data.tariff && data.tariff.net_metering_mode) {
      netModeEl.value = data.tariff.net_metering_mode;
    }
    if (discomEl) {
      const schemes = (data.central_schemes || [])
        .map((s) => s.name)
        .concat((data.state_schemes || []).map((s) => s.name));
      discomEl.innerHTML =
        `<strong>${data.discom_name || data.discom_code || '—'}</strong>` +
        (data.state ? ` · ${data.state}` : '') +
        (schemes.length ? `<br><span class="discom-schemes">${schemes.join(' · ')}</span>` : '') +
        `<br><span class="discom-note">${data.note || ''}</span>`;
      discomEl.hidden = false;
    }
    SignixFormStore.locationContext = data;
  } catch (err) {
    console.warn('Location context lookup failed', err);
  }
}

/** Lightweight store for estimate form selection state. */
const SignixFormStore = {
  selectedAddress: null,
  selectedLatLng: null,
  locationContext: null,
};

// filename: app/static/js/main.js

// -------------------------------------------------- Landing page flow -----

function initSearch() {
  const searchBtn = document.getElementById('search-btn');
  const locateBtn = document.getElementById('locate-btn');
  const input = document.getElementById('address-search');
  const resultsBox = document.getElementById('search-results');
  const mapsInput = document.getElementById('maps-link-input');
  const mapsBtn = document.getElementById('maps-link-btn');

  if (!searchBtn) return; // not on the landing page

  async function applyMapsLink() {
    if (!mapsInput) return;
    const url = mapsInput.value.trim();
    if (!url) {
      alert('Paste a Google Maps link first.');
      return;
    }
    if (resultsBox) resultsBox.innerHTML = '<p class="search-status">Reading Google Maps link…</p>';
    try {
      const res = await fetch(`${PARSE_MAPS_URL}?url=${encodeURIComponent(url)}`);
      const data = await res.json();
      if (!res.ok) {
        if (resultsBox) resultsBox.innerHTML = '';
        alert(data.error || 'Could not read that Maps link.');
        return;
      }
      const lat = Number(data.lat);
      const lon = Number(data.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        alert('Invalid coordinates from Maps link.');
        return;
      }
      SignixFormStore.selectedLatLng = { lat, lng: lon };
      SignixFormStore.selectedAddress = data.address || (lat + ', ' + lon);
      if (input) input.value = SignixFormStore.selectedAddress;
      applyLocationContext({ address: SignixFormStore.selectedAddress, lat, lng: lon });
      if (typeof setMapCenter === 'function') {
        setMapCenter(lat, lon, 19);
        // Re-center after a tick in case the map was still settling
        setTimeout(() => setMapCenter(lat, lon, 19), 400);
      }
      if (resultsBox) {
        resultsBox.innerHTML =
          '<p class="search-status">Pinned at ' + lat.toFixed(5) + ', ' + lon.toFixed(5) +
          ' — draw your rooftop around the blue marker.</p>';
      }
    } catch (err) {
      if (resultsBox) resultsBox.innerHTML = '';
      alert('Failed to open Maps link. Check your connection and try again.');
    }
  }

  if (mapsBtn) mapsBtn.addEventListener('click', applyMapsLink);
  if (mapsInput) {
    mapsInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        applyMapsLink();
      }
    });
  }

  async function runSearch() {
    const query = input.value.trim();
    if (!query) return;
    resultsBox.innerHTML = '<p class="search-status">Searching for places…</p>';

    try {
      const res = await fetch(`${GEOCODE_URL}?q=${encodeURIComponent(query)}`);
      const data = await res.json();
      const list = data.results || [];
      if (data.source === 'mock_fallback') {
        resultsBox.innerHTML =
          '<p class="search-status">Live geocoding unavailable. Showing a fallback — try a fuller address (society / road / city).</p>';
      }
      renderSearchResults(list, data.source);
    } catch (err) {
      resultsBox.innerHTML = '<p class="search-status">Could not search right now. Check your connection and try again.</p>';
    }
  }

  searchBtn.addEventListener('click', runSearch);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      runSearch();
    }
  });

  locateBtn.addEventListener('click', () => {
    if (!navigator.geolocation) {
      alert('Geolocation is not supported by this browser.');
      return;
    }
    resultsBox.innerHTML = '<p class="search-status">Getting your location…</p>';
    navigator.geolocation.getCurrentPosition(async (pos) => {
      const { latitude, longitude } = pos.coords;
      SignixFormStore.selectedLatLng = { lat: latitude, lng: longitude };
      if (typeof setMapCenter === 'function') {
        setMapCenter(latitude, longitude, 19);
      }

      try {
        const res = await fetch(`${REVERSE_GEOCODE_URL}?lat=${latitude}&lon=${longitude}`);
        const data = await res.json();
        SignixFormStore.selectedAddress = data.address;
        input.value = SignixFormStore.selectedAddress;
        resultsBox.innerHTML = '';
      } catch (err) {
        SignixFormStore.selectedAddress = 'Current location';
        input.value = SignixFormStore.selectedAddress;
        resultsBox.innerHTML = '';
      }
    }, () => {
      resultsBox.innerHTML = '';
      alert('Unable to retrieve your location. Allow location access or search by address.');
    }, { enableHighAccuracy: true, timeout: 12000 });
  });

  function renderSearchResults(results, source) {
    if (!results.length) {
      resultsBox.innerHTML =
        '<p class="search-status">No results found. Try adding city/state (e.g. “GL Bajaj Greater Noida”) or a nearby landmark.</p>';
      return;
    }
    resultsBox.innerHTML = '';
    if (source) {
      const meta = document.createElement('p');
      meta.className = 'search-status';
      meta.textContent = results.length + ' place(s) found' + (source && source !== 'mock_fallback' ? '' : '');
      resultsBox.appendChild(meta);
    }
    results.forEach(r => {
      const div = document.createElement('div');
      div.className = 'search-result-item';
      const title = r.label || r.display_name;
      const sub = (r.label && r.display_name && r.label !== r.display_name)
        ? r.display_name
        : (r.type ? String(r.type) : '');
      div.innerHTML =
        '<div class="result-title"></div>' +
        (sub ? '<div class="result-sub"></div>' : '');
      div.querySelector('.result-title').textContent = title;
      if (sub) div.querySelector('.result-sub').textContent = sub;
      div.title = r.display_name || title;
      div.addEventListener('click', () => {
        SignixFormStore.selectedAddress = r.display_name || title;
        SignixFormStore.selectedLatLng = { lat: r.lat, lng: r.lon };
        input.value = SignixFormStore.selectedAddress;
        applyLocationContext({
          state: r.state,
          city: r.city,
          address: r.display_name,
          pincode: r.postcode,
          lat: r.lat,
          lng: r.lon,
        });
        if (typeof setMapCenter === 'function') {
          setMapCenter(r.lat, r.lon, 19);
        }
        resultsBox.innerHTML = '';
      });
      resultsBox.appendChild(div);
    });
  }
}

function initSystemOptions() {
  const batteryToggle = document.getElementById('battery-toggle');
  const batteryWrap = document.getElementById('battery-size-wrap');
  const needsBackup = document.getElementById('needs-backup');
  const backupParams = document.getElementById('backup-params-wrap');
  if (batteryToggle && batteryWrap) {
    batteryToggle.addEventListener('change', () => {
      batteryWrap.classList.toggle('hidden', !batteryToggle.checked);
    });
  }
  if (needsBackup && backupParams) {
    needsBackup.addEventListener('change', () => {
      backupParams.classList.toggle('hidden', !needsBackup.checked);
    });
  }
}

function initEstimationButton() {
  const btn = document.getElementById('start-estimation-btn');
  if (!btn) return;

  function showEstimateResult(data) {
    const resultPanel = document.getElementById('estimate-result');
    if (!resultPanel) return;

    const ratingHost = document.getElementById('result-suitability-rating');
    if (ratingHost) {
      ratingHost.innerHTML = '';
      const rating = data.suitability_rating;
      if (rating) {
        const viability = rating.overall_viability || {};
        const confidence = rating.data_confidence || {};
        const factors = rating.factors || {};
        const score = Math.max(0, Math.min(100, Number(viability.score || 0)));
        const tier = (viability.tier || 'fair').toLowerCase();
        const confidenceTier = (confidence.tier || 'unknown').replace(/_/g, ' ');
        const priorityLabels = {
          fastest_payback: 'Fastest payback',
          maximum_savings: 'Maximum long-term savings',
          environmental_impact: 'Environmental impact',
          backup_power: 'Backup power',
          no_preference: 'No strong preference'
        };
        const labels = {
          payback: 'Payback',
          financial_viability: 'Financial viability',
          roof_fit: 'Roof fit',
          orientation: 'Orientation',
          co2_reduction: 'CO2 reduction',
          backup_capability: 'Backup capability'
        };
        const detail = (key, factor) => {
          if (key === 'payback') return `${factor.tier || 'Unavailable'} payback period`;
          if (key === 'financial_viability') return `${factor.tier || 'Unavailable'} lifetime savings margin`;
          if (key === 'roof_fit') return `${factor.usable_area_per_kw ?? '—'} m² usable area per kW`;
          if (key === 'orientation') return `${factor.factor ?? '—'} relative yield factor`;
          if (key === 'co2_reduction') return `${factor.tons ?? '—'} tons CO2 reduction per year`;
          return `${factor.score ?? 0}/100 backup capability`;
        };
        const factorMarkup = Object.entries(labels).filter(([key]) => factors[key]).map(([key, label]) => {
          const factor = factors[key];
          const factorScore = Math.max(0, Math.min(100, Number(factor.score || 0)));
          const factorTier = (factor.tier || 'fair').toLowerCase();
          return `<div class="suitability-factor"><div class="factor-heading"><strong>${label}</strong>${factor.tier ? `<span class="factor-tier suitability-${factorTier}">${factor.tier.replace(/_/g, ' ')}</span>` : ''}<span class="factor-score">${factorScore}/100</span></div><div class="factor-bar" role="progressbar" aria-valuenow="${factorScore}" aria-valuemin="0" aria-valuemax="100"><span class="factor-bar-fill suitability-${factorTier}" style="width: ${factorScore}%"></span></div><span class="factor-detail">${detail(key, factor)}</span></div>`;
        }).join('');
        ratingHost.innerHTML = `
          <section class="suitability-card">
            <div class="suitability-primary">
              <div class="score-ring-wrap" aria-label="Overall suitability score ${score} out of 100"><svg class="score-ring" viewBox="0 0 100 100" role="img" aria-hidden="true"><circle class="score-ring-track" cx="50" cy="50" r="40"></circle><circle class="score-ring-progress suitability-${tier}" cx="50" cy="50" r="40" pathLength="100" style="stroke-dasharray: ${score} 100"></circle></svg><span class="score-ring-value"><strong>${score}</strong><small>/100</small></span></div>
              <div class="suitability-primary-copy"><span class="suitability-label">Overall suitability</span><div class="suitability-badges"><span class="suitability-tier suitability-${tier}">${(viability.tier || 'Unknown').replace(/_/g, ' ')}</span><span class="confidence-badge">Estimate confidence: ${confidenceTier}</span></div>${rating.user_priority ? `<span class="suitability-priority">Priority: ${priorityLabels[rating.user_priority] || rating.user_priority.replace(/_/g, ' ')}</span>` : ''}</div>
            </div>
            <details class="suitability-details"><summary>View factor breakdown</summary><div class="suitability-factors">${factorMarkup}</div></details>
          </section>`;
      }
    }
    const formatCurrency = value => `₹${Number(value || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
    document.getElementById('result-system-size').textContent = Number(data.system_size || 0).toFixed(2);
    const panelEl = document.getElementById('result-panel-count');
    if (panelEl) {
      const n = data.panel_count != null ? data.panel_count
        : (data.panel_estimate && data.panel_estimate.panel_count);
      panelEl.textContent = n != null ? `approximately ${n} panels` : '';
    }
    const sizing = data.sizing || null;
    const sizingPanel = document.getElementById('result-sizing-panel');
    if (sizingPanel && sizing) {
      sizingPanel.classList.remove('hidden');
      const setTxt = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
      };
      setTxt('result-size-min', Number(sizing.min_kw || 0).toFixed(2));
      setTxt('result-size-recommended', Number(sizing.recommended_kw || data.system_size || 0).toFixed(2));
      setTxt('result-size-max', Number(sizing.max_kw || 0).toFixed(2));
      setTxt('result-sizing-mode', sizing.mode ? `Mode: ${String(sizing.mode).replace(/_/g, ' ')}` : '');
      const rat = document.getElementById('result-sizing-rationale');
      if (rat) {
        rat.innerHTML = '';
        (sizing.rationale || []).forEach(line => {
          const li = document.createElement('li');
          li.textContent = line;
          rat.appendChild(li);
        });
      }
      const cons = document.getElementById('result-sizing-constraints');
      if (cons) {
        const list = sizing.constraints || [];
        cons.textContent = list.length ? `Constraints: ${list.join(' · ')}` : '';
      }
    } else if (sizingPanel) {
      sizingPanel.classList.add('hidden');
    }
    const battLine = document.getElementById('result-battery-line');
    if (battLine) {
      const bk = Number(data.battery_kwh || 0);
      const bs = data.battery_sizing || {};
      if (bk > 0) {
        let msg = `Battery: ${bk.toFixed(2)} kWh`;
        if (bs.critical_load_kw && bs.backup_hours) {
          msg += ` (auto-sized for ${bs.critical_load_kw} kW × ${bs.backup_hours} h)`;
        } else if (bs.note) {
          msg += ` — ${bs.note}`;
        }
        if (data.battery_cost) {
          msg += ` · ~₹${Number(data.battery_cost).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
        }
        battLine.textContent = msg;
        battLine.classList.remove('hidden');
      } else {
        battLine.textContent = '';
        battLine.classList.add('hidden');
      }
    }
    document.getElementById('result-annual-generation').textContent = Number(data.annual_generation || 0).toLocaleString();
    document.getElementById('result-monthly-savings').textContent = formatCurrency(data.monthly_savings);
    document.getElementById('result-co2-reduction').textContent = Number(data.co2_reduction_tons || 0).toFixed(2);
    const nominalPayback = data.nominal_payback_years != null
      ? data.nominal_payback_years
      : data.payback_years;
    document.getElementById('result-payback').textContent = nominalPayback != null
      ? Number(nominalPayback).toFixed(1)
      : 'Indicative estimate';
    const simpleEl = document.getElementById('result-payback-simple');
    if (simpleEl) {
      const simplePayback = data.simple_payback_years;
      simpleEl.textContent = simplePayback != null
        ? `Simple (flat tariff): ${Number(simplePayback).toFixed(1)} yrs`
        : 'Simple (flat tariff): —';
    }
    document.getElementById('result-system-cost').textContent = formatCurrency(data.system_cost);
    document.getElementById('result-subsidy').textContent = `-${formatCurrency(data.subsidy_amount)}`;
    document.getElementById('result-net-investment').textContent = formatCurrency(data.net_investment);
    const subsidyNote = document.getElementById('result-subsidy-note');
    if (subsidyNote) {
      const caveat = data.subsidy_caveat
        || 'Central subsidy estimate — final amount subject to MNRE / DISCOM approval';
      const assumption = data.subsidy_eligibility_assumption
        || 'Assumes residential, grid-connected system';
      subsidyNote.textContent = `${caveat} ${assumption}`;
    }
    const caveatEl = document.getElementById('result-subsidy-caveat');
    if (caveatEl && data.subsidy_caveat) caveatEl.textContent = data.subsidy_caveat;
    const eligEl = document.getElementById('result-subsidy-eligibility');
    if (eligEl && data.subsidy_eligibility_assumption) {
      eligEl.textContent = data.subsidy_eligibility_assumption;
    }
    document.getElementById('result-area-summary').textContent =
      `Usable roof area: ${Number(data.usable_area_sqm || 0).toFixed(2)} m² ` +
      `of ${Number(data.roof_area_sqm || 0).toFixed(2)} m² total`;
    const sourceEl = document.getElementById('estimate-source');
    const sourceNames = {
      nasa_power: 'NASA POWER',
      pvgis: 'PVGIS',
      mock: 'Mock',
      mock_fallback: 'Mock',
      estimated: 'Mock'
    };
    const confTier = (
      data.irradiance_confidence
      || (data.suitability_rating && data.suitability_rating.data_confidence
          && data.suitability_rating.data_confidence.tier)
      || ''
    ).toLowerCase();
    const sourceKey = data.irradiance_source || '';
    const sourceName = sourceNames[sourceKey] || sourceKey || '';
    if (sourceEl) {
      sourceEl.classList.remove(
        'data-conf-high', 'data-conf-medium', 'data-conf-low', 'data-conf-unknown', 'hidden'
      );
      if (sourceName) {
        let confText = '';
        let confClass = 'data-conf-unknown';
        if (confTier === 'high') {
          confText = ' · High confidence';
          confClass = 'data-conf-high';
        } else if (confTier === 'medium') {
          confText = ' · Medium confidence';
          confClass = 'data-conf-medium';
        } else if (confTier === 'low') {
          confText = ' · Low confidence — verify locally';
          confClass = 'data-conf-low';
        }
        sourceEl.textContent = `Data: ${sourceName}${confText}`;
        sourceEl.classList.add(confClass);
      } else {
        sourceEl.textContent = '';
        sourceEl.classList.add('hidden', 'data-conf-unknown');
      }
    }
    const sourceNote = document.getElementById('estimate-source-note');
    if (sourceNote) {
      const isFallback = sourceKey === 'mock_fallback'
        || sourceKey === 'mock'
        || sourceKey === 'estimated'
        || confTier === 'low';
      sourceNote.textContent = isFallback
        ? 'Live irradiance services were unavailable. This result uses a rough location-adjusted estimate.'
        : '';
      sourceNote.classList.toggle('hidden', !isFallback);
    }

    const reportLink = document.getElementById('view-full-report');
    if (reportLink && data.redirect_url) reportLink.href = data.redirect_url;
    resultPanel.classList.remove('hidden');
  }

  btn.addEventListener('click', async () => {
    const polygon = getCurrentPolygon();
    const obstructions = typeof getCurrentObstructions === 'function' ? getCurrentObstructions() : [];
    if (!polygon) {
      alert('Please draw your rooftop boundary on the map first.');
      return;
    }
    if (!SignixFormStore.selectedLatLng) {
      // Fall back to the polygon's own centroid if no address was searched.
      const avgLat = polygon.reduce((s, p) => s + p[0], 0) / polygon.length;
      const avgLng = polygon.reduce((s, p) => s + p[1], 0) / polygon.length;
      SignixFormStore.selectedLatLng = { lat: avgLat, lng: avgLng };
    }
    if (!SignixFormStore.selectedAddress) {
      SignixFormStore.selectedAddress = document.getElementById('address-search').value || 'Selected rooftop';
    }

    const orientationSelect = document.getElementById('orientation-select');
    const orientationValue = orientationSelect ? orientationSelect.value : 'auto';
    const batteryEnabled = document.getElementById('battery-toggle')?.checked || false;
    const needsBackup = document.getElementById('needs-backup')?.checked || false;
    // Manual capacity only when toggle is on; otherwise 0 so backend can auto-size when needs_backup.
    const batteryKwh = batteryEnabled
      ? parseFloat(document.getElementById('battery-size')?.value || '10')
      : 0;
    const criticalLoadKw = needsBackup
      ? parseFloat(document.getElementById('critical-load-kw')?.value || '1')
      : null;
    const backupHours = needsBackup
      ? parseFloat(document.getElementById('backup-hours')?.value || '4')
      : null;
    const sizingMode = document.getElementById('sizing-mode')?.value || null;
    const loadProfile = document.getElementById('load-profile')?.value || 'flat';
    const netMeteringMode = document.getElementById('net-metering-mode')?.value || 'full_offset';
    const monthlyBillRaw = document.getElementById('monthly-bill')?.value;
    const monthlyBill = monthlyBillRaw !== undefined && monthlyBillRaw !== ''
      ? parseFloat(monthlyBillRaw)
      : null;
    const tariffRaw = document.getElementById('tariff-per-kwh')?.value;
    const tariffPerKwh = tariffRaw !== undefined && tariffRaw !== ''
      ? parseFloat(tariffRaw)
      : null;
    const state = document.getElementById('state')?.value || null;
    const propertyType = document.getElementById('property-type')?.value || 'residential';
    const userPriority = document.getElementById('user-priority')?.value || 'no_preference';
    const inverterPreference = document.getElementById('inverter-preference')?.value || 'auto';

    document.getElementById('loading-panel').classList.remove('hidden');
    btn.disabled = true;

    try {
      const csrfToken =
        document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') ||
        document.getElementById('assistant-csrf')?.value ||
        '';
      const res = await fetch(ESTIMATE_URL, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-CSRFToken': csrfToken,
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({
          address: SignixFormStore.selectedAddress,
          latitude: SignixFormStore.selectedLatLng.lat,
          longitude: SignixFormStore.selectedLatLng.lng,
          polygon: polygon,
          obstructions: obstructions,
          state: state,
          tariff_per_kwh: tariffPerKwh,
          orientation: orientationValue,
          battery_kwh: batteryKwh,
          monthly_bill: monthlyBill,
          sizing_mode: sizingMode || null,
          load_profile: loadProfile,
          net_metering_mode: netMeteringMode,
          needs_backup: needsBackup,
          critical_load_kw: criticalLoadKw,
          backup_hours: backupHours,
          property_type: propertyType,
          user_priority: userPriority,
          inverter_preference: inverterPreference
        })
      });
      let data;
      const rawText = await res.text();
      try {
        data = rawText ? JSON.parse(rawText) : {};
      } catch (parseErr) {
        const snippet = (rawText || '').replace(/\s+/g, ' ').slice(0, 180);
        alert(
          'Server returned an invalid response (HTTP ' + res.status + ').\n' +
          'Expected JSON but got: ' + (snippet || '(empty body)') + '\n' +
          'Check the Flask terminal for errors. If the DB is old, delete solar_app.db and restart.'
        );
        return;
      }
      if (res.status === 401) {
        alert(data.error || 'Please log in again, then retry the estimate.');
        if (data.login_url) window.location.href = data.login_url;
        return;
      }
      if (!res.ok) {
        alert(data.error || ('Estimation failed (HTTP ' + res.status + ').'));
        return;
      }
      if (data.redirect_url) {
        showEstimateResult(data);
      } else {
        alert(data.error || 'Something went wrong generating your estimate.');
      }
    } catch (err) {
      console.error(err);
      alert('Estimation failed: ' + (err && err.message ? err.message : 'network or server error. Check the Flask terminal.'));
    } finally {
      document.getElementById('loading-panel').classList.add('hidden');
      btn.disabled = false;
    }
  });
}

// -------------------------------------------------------- Report page -----
function initTabs() {
  const tabButtons = document.querySelectorAll('.tab-btn');
  if (!tabButtons.length) return;

  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const panel = document.getElementById(`tab-${btn.dataset.tab}`);
      if (panel) panel.classList.add('active');
      // Charts in previously-hidden panels need a resize after becoming visible
      requestAnimationFrame(() => {
        window.SIGNIX_REPORT_CHARTS?.resize?.();
      });
    });
  });
}


function initDialControls() {
  const panel = document.getElementById('dial-panel');
  if (!panel) return;

  const baseSystemKw = Number(typeof SYSTEM_SIZE !== 'undefined' ? SYSTEM_SIZE : 0) || 1;
  const baseGen = Number(typeof ANNUAL_GENERATION !== 'undefined' ? ANNUAL_GENERATION : 0) || 0;
  const baseSavings = Number(typeof MONTHLY_SAVINGS !== 'undefined' ? MONTHLY_SAVINGS : 0) || 0;
  const basePanels = Number(typeof PANEL_COUNT !== 'undefined' ? PANEL_COUNT : 0) || Math.round(baseSystemKw * 1000 / 540);

  const defaults = {
    systemSize: baseSystemKw,
    roofArea: Number(document.getElementById('range-roof-area')?.value) || 50,
    daytimePct: Number(document.getElementById('range-daytime-pct')?.value) || 75,
    shading: 0,
    units: Number(document.getElementById('range-units')?.value) || 0,
    tariff: Number(document.getElementById('range-tariff')?.value) || 0,
    pitch: 0,
  };

  const state = { ...defaults };

  function fmtNum(n, digits) {
    if (n == null || isNaN(n)) return '—';
    return Number(n).toLocaleString('en-IN', { maximumFractionDigits: digits != null ? digits : 0 });
  }

  function syncReadouts() {
    const el = (id, text) => { const n = document.getElementById(id); if (n) n.textContent = text; };
    el('dial-system-size', fmtNum(state.systemSize, 1));
    el('dial-roof-area', fmtNum(state.roofArea, 0));
    el('dial-daytime-pct', fmtNum(state.daytimePct, 0));
    el('dial-shading', fmtNum(state.shading, 0));
    if (document.getElementById('dial-units')) el('dial-units', fmtNum(state.units, 0));
    if (document.getElementById('dial-tariff')) el('dial-tariff', fmtNum(state.tariff, 2));

    // Indicative impact: scale generation by system size & shading; savings by gen + daytime + tariff
    const sizeFactor = state.systemSize / Math.max(baseSystemKw, 0.1);
    const shadeFactor = 1 - (state.shading / 100);
    const dayFactor = state.daytimePct / Math.max(defaults.daytimePct, 1);
    let tariffFactor = 1;
    if (defaults.tariff > 0 && state.tariff > 0) tariffFactor = state.tariff / defaults.tariff;

    const indGen = baseGen * sizeFactor * shadeFactor;
    const indSavings = baseSavings * sizeFactor * shadeFactor * dayFactor * tariffFactor;
    const indPanels = Math.max(1, Math.round(basePanels * sizeFactor));

    el('dial-impact-gen', fmtNum(indGen, 0) + ' kWh');
    el('dial-impact-savings', '₹' + fmtNum(indSavings, 0));
    el('dial-impact-panels', String(indPanels));
    const pcl = document.getElementById('panel-count-label');
    if (pcl) pcl.textContent = 'Showing approximately ' + indPanels + ' panels.';

    if (window.SIGNIX_ROOF3D) {
      window.SIGNIX_ROOF3D.setPitchAndPanels(state.pitch, indPanels);
    }
  }

  function bindRange(rangeId, key, parseFn) {
    const range = document.getElementById(rangeId);
    if (!range) return;
    const card = range.closest('.dial-card');
    const minus = card && card.querySelector('.dial-minus');
    const plus = card && card.querySelector('.dial-plus');
    const step = Number(range.step) || 1;
    const apply = () => {
      state[key] = parseFn ? parseFn(range.value) : Number(range.value);
      syncReadouts();
    };
    range.addEventListener('input', apply);
    if (minus) minus.addEventListener('click', () => {
      range.value = String(Math.max(Number(range.min), Number(range.value) - step));
      apply();
    });
    if (plus) plus.addEventListener('click', () => {
      range.value = String(Math.min(Number(range.max), Number(range.value) + step));
      apply();
    });
  }

  bindRange('range-system-size', 'systemSize', (v) => Number(v));
  bindRange('range-roof-area', 'roofArea', (v) => Number(v));
  bindRange('range-daytime-pct', 'daytimePct', (v) => Number(v));
  bindRange('range-shading', 'shading', (v) => Number(v));
  bindRange('range-units', 'units', (v) => Number(v));
  bindRange('range-tariff', 'tariff', (v) => Number(v));

  const pitchSelect = document.getElementById('roof-pitch-select');
  if (pitchSelect) {
    pitchSelect.addEventListener('change', () => {
      state.pitch = Number(pitchSelect.value) || 0;
      syncReadouts();
    });
  }

  const undoBtn = document.getElementById('roof-undo-btn');
  if (undoBtn) {
    undoBtn.addEventListener('click', () => {
      Object.assign(state, defaults);
      state.pitch = 0;
      const setRange = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.value = String(val);
      };
      setRange('range-system-size', defaults.systemSize);
      setRange('range-roof-area', defaults.roofArea);
      setRange('range-daytime-pct', defaults.daytimePct);
      setRange('range-shading', 0);
      setRange('range-units', defaults.units);
      setRange('range-tariff', defaults.tariff);
      if (pitchSelect) pitchSelect.value = '0';
      syncReadouts();
    });
  }

  syncReadouts();
}

document.addEventListener('DOMContentLoaded', () => {
  initSearch();
  initSystemOptions();
  initEstimationButton();
  initTabs();
  window.SIGNIX_REPORT_CHARTS?.init?.();
  initDialControls();
});

(function wireStateTariffDefaults() {
  const stateEl = document.getElementById('state');
  const tariffEl = document.getElementById('tariff-per-kwh');
  const netModeEl = document.getElementById('net-metering-mode');
  if (!stateEl) return;

  async function applyStateDefaults() {
    const state = stateEl.value;
    if (!state) return;
    try {
      const resp = await fetch(`/api/tariff?state=${encodeURIComponent(state)}`, {
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin',
      });
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data || !data.found) return;
      if (tariffEl && data.rate_per_kwh != null) {
        tariffEl.value = Number(data.rate_per_kwh).toFixed(2);
        tariffEl.dispatchEvent(new Event('change', { bubbles: true }));
      }
      if (netModeEl && data.net_metering_mode) {
        netModeEl.value = data.net_metering_mode;
        netModeEl.dispatchEvent(new Event('change', { bubbles: true }));
      }
      // Surface export credit as optional feed-in hint via title/tooltip on net mode
      if (netModeEl && data.export_credit_rate != null) {
        netModeEl.title = `State default export credit ≈ ₹${Number(data.export_credit_rate).toFixed(2)}/kWh`;
      }
    } catch (err) {
      console.warn('State tariff lookup failed', err);
    }
  }

  stateEl.addEventListener('change', applyStateDefaults);
})();

