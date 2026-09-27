const state = {
  calendarYear: new Date().getFullYear(),
  calendarMonth: new Date().getMonth(), // 0-indexed
  calendarView: "month", // "month" | "list" — forced to "list" under MOBILE_BREAKPOINT regardless
  allReleases: [],
};

const CAL_MAX_ITEMS_PER_CELL = 3;
const MOBILE_BREAKPOINT = 768;

function $(sel) { return document.querySelector(sel); }
function $all(sel) { return document.querySelectorAll(sel); }

// ---------- Admin auth ----------
// Lightweight, single-shared-secret gate: not a real login system. The key
// only ever travels as the X-Admin-Key header (never a query param on the
// actual API calls), and the server is the sole source of truth — this
// client-side state just controls which buttons/forms are shown.
const ADMIN_KEY_STORAGE = "freshHopAdminKey";
let adminKey = null;

async function initAuth() {
  const params = new URLSearchParams(location.search);
  const urlKey = params.get("admin_key");

  if (urlKey) {
    // Strip it from the visible URL immediately, whether or not it turns
    // out to be valid, so it doesn't linger in the address bar/history.
    params.delete("admin_key");
    const rest = params.toString();
    history.replaceState({}, "", location.pathname + (rest ? `?${rest}` : "") + location.hash);
  }

  const candidate = urlKey || localStorage.getItem(ADMIN_KEY_STORAGE);
  if (!candidate) {
    applyAuthUI();
    return;
  }

  try {
    const res = await fetch("/api/admin/verify", { method: "POST", headers: { "X-Admin-Key": candidate } });
    if (res.ok) {
      adminKey = candidate;
      localStorage.setItem(ADMIN_KEY_STORAGE, candidate);
    } else {
      localStorage.removeItem(ADMIN_KEY_STORAGE);
    }
  } catch {
    // Network hiccup on verify — fail closed to guest mode rather than
    // trusting an unverified key.
  }
  applyAuthUI();
}

function applyAuthUI() {
  const isAdmin = !!adminKey;
  $all(".admin-only").forEach((el) => el.classList.toggle("hidden", !isAdmin));
  if (!isAdmin) {
    const breweriesBtn = $('.tab-btn[data-tab="breweries"]');
    if (breweriesBtn && breweriesBtn.classList.contains("active")) {
      $('.tab-btn[data-tab="today"]').click();
    }
  }
}

async function adminFetch(url, options = {}) {
  const headers = Object.assign({}, options.headers, adminKey ? { "X-Admin-Key": adminKey } : {});
  const res = await fetch(url, Object.assign({}, options, { headers }));
  if (res.status === 401) {
    localStorage.removeItem(ADMIN_KEY_STORAGE);
    adminKey = null;
    applyAuthUI();
    alert("Your admin session is no longer valid. Reload the page with ?admin_key=... to continue.");
  }
  return res;
}

// ---------- Tabs ----------
$all(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    $all(".tab-btn").forEach((b) => b.classList.remove("active"));
    $all(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// ---------- Today list ----------
async function loadToday() {
  const res = await fetch("/api/today");
  const releases = await res.json();
  const list = $("#todayList");
  const empty = $("#todayEmpty");
  list.innerHTML = "";
  if (releases.length === 0) {
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  for (const r of releases) {
    list.appendChild(buildBeerCard(r));
  }
}

// Shared card markup used by Today's List, the calendar list view, and the
// day-detail modal — keeps all three in sync instead of drifting apart.
function buildBeerCard(r) {
  const card = document.createElement("div");
  card.className = "beer-card";
  card.innerHTML = `
    <div class="brewery">${escapeHtml(r.brewery_name)}</div>
    <div class="beer-name">${escapeHtml(r.beer_name)}</div>
    <div class="release-date">Released ${formatDate(r.release_date)}</div>
    <div><span class="status-pill status-${r.status}">${labelForStatus(r.status)}</span>${sourceBadge(r.source_type)}</div>
    ${r.source_url ? `<a class="source-link" href="${escapeAttr(r.source_url)}" target="_blank" rel="noopener">Source &rarr;</a>` : ""}
    ${r.evidence_snippet ? `<div class="evidence-snippet">"${escapeHtml(r.evidence_snippet)}"</div>` : ""}
  `;
  return card;
}

// ---------- Calendar ----------
async function loadCalendarData() {
  const res = await fetch("/api/calendar");
  state.allReleases = await res.json();
  renderCalendar();
}

function isMobileViewport() {
  return window.innerWidth <= MOBILE_BREAKPOINT;
}

// Mobile always gets the list view regardless of the toggle's own
// selection — the grid simply doesn't work at that width (per the design
// brief), so there's no point offering it there.
function effectiveCalendarView() {
  return isMobileViewport() ? "list" : state.calendarView;
}

function releasesByDateForMonth(year, month) {
  const byDate = {};
  for (const r of state.allReleases) {
    const [ry, rm] = r.release_date.split("-").map(Number);
    if (ry === year && rm === month + 1) {
      if (!byDate[r.release_date]) byDate[r.release_date] = [];
      byDate[r.release_date].push(r);
    }
  }
  return byDate;
}

function renderCalendar() {
  const { calendarYear: year, calendarMonth: month } = state;
  const monthLabel = new Date(year, month, 1).toLocaleString("en-US", { month: "long", year: "numeric" });
  $("#calendarMonthLabel").textContent = monthLabel;

  const view = effectiveCalendarView();
  $("#calendarGrid").classList.toggle("hidden", view !== "month");
  $("#calendarListView").classList.toggle("hidden", view !== "list");

  const byDate = releasesByDateForMonth(year, month);
  if (view === "month") {
    renderCalendarGrid(year, month, byDate);
  } else {
    renderCalendarListView(year, month, byDate);
  }
}

function renderCalendarGrid(year, month, byDate) {
  const grid = $("#calendarGrid");
  grid.innerHTML = "";

  ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].forEach((d) => {
    const label = document.createElement("div");
    label.className = "calendar-daylabel";
    label.textContent = d;
    grid.appendChild(label);
  });

  const firstDay = new Date(year, month, 1);
  const startOffset = firstDay.getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();

  for (let i = 0; i < startOffset; i++) {
    const cell = document.createElement("div");
    cell.className = "calendar-cell empty";
    grid.appendChild(cell);
  }

  for (let day = 1; day <= daysInMonth; day++) {
    const dateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const dayItems = byDate[dateStr] || [];
    const cell = document.createElement("div");
    cell.className = "calendar-cell";

    // Cap visible items per cell so one busy day can't stretch the whole
    // week's row height; the rest is one click away via the day modal.
    const visible = dayItems.slice(0, CAL_MAX_ITEMS_PER_CELL);
    const overflow = dayItems.length - visible.length;
    const itemsHtml = visible
      .map((item) => `<div class="cal-item" data-date="${dateStr}">${escapeHtml(item.brewery_name)}: ${escapeHtml(item.beer_name)}</div>`)
      .join("");
    const moreHtml = overflow > 0
      ? `<button type="button" class="cal-more-btn" data-date="${dateStr}">+${overflow} more &middot; View all</button>`
      : "";

    cell.innerHTML = `
      <div class="day-num" data-date="${dateStr}">${day}</div>
      <div class="cal-items">${itemsHtml}${moreHtml}</div>
    `;
    grid.appendChild(cell);
  }

  grid.querySelectorAll(".cal-item, .cal-more-btn, .day-num").forEach((el) => {
    el.addEventListener("click", () => {
      const dateStr = el.dataset.date;
      const items = byDate[dateStr] || [];
      if (items.length > 0) openDayModal(dateStr, items);
    });
  });
}

function renderCalendarListView(year, month, byDate) {
  const container = $("#calendarListView");
  container.innerHTML = "";
  const dates = Object.keys(byDate).sort();

  if (dates.length === 0) {
    const monthLabel = new Date(year, month, 1).toLocaleString("en-US", { month: "long", year: "numeric" });
    container.innerHTML = `<div class="date-group-empty">No fresh hop releases found for ${monthLabel}.</div>`;
    return;
  }

  for (const dateStr of dates) {
    const weekday = new Date(dateStr + "T00:00:00").toLocaleDateString("en-US", { weekday: "long" });
    const group = document.createElement("div");
    group.className = "date-group";
    group.innerHTML = `<div class="date-group-header">${formatDate(dateStr)} &middot; ${weekday}</div><div class="card-grid date-group-cards"></div>`;
    const cardsEl = group.querySelector(".date-group-cards");
    for (const item of byDate[dateStr]) {
      cardsEl.appendChild(buildBeerCard(item));
    }
    container.appendChild(group);
  }
}

function openDayModal(dateStr, items) {
  const weekday = new Date(dateStr + "T00:00:00").toLocaleDateString("en-US", { weekday: "long" });
  $("#dayModalTitle").textContent =
    `${formatDate(dateStr)} (${weekday}) — ${items.length} release${items.length === 1 ? "" : "s"}`;
  const body = $("#dayModalBody");
  body.innerHTML = "";
  items.forEach((item) => body.appendChild(buildBeerCard(item)));
  $("#dayModal").classList.remove("hidden");
}

$("#dayModalClose").addEventListener("click", () => $("#dayModal").classList.add("hidden"));
$("#dayModal").addEventListener("click", (e) => {
  if (e.target.id === "dayModal") $("#dayModal").classList.add("hidden");
});

$all(".view-toggle-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.calendarView = btn.dataset.view;
    $all(".view-toggle-btn").forEach((b) => b.classList.toggle("active", b === btn));
    renderCalendar();
  });
});

let calendarResizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(calendarResizeTimer);
  calendarResizeTimer = setTimeout(renderCalendar, 150);
});

$("#prevMonth").addEventListener("click", () => {
  state.calendarMonth -= 1;
  if (state.calendarMonth < 0) { state.calendarMonth = 11; state.calendarYear -= 1; }
  renderCalendar();
});
$("#nextMonth").addEventListener("click", () => {
  state.calendarMonth += 1;
  if (state.calendarMonth > 11) { state.calendarMonth = 0; state.calendarYear += 1; }
  renderCalendar();
});

// ---------- Breweries / Settings ----------
async function loadBreweries() {
  const res = await fetch("/api/breweries");
  const breweries = await res.json();
  const tbody = $("#breweryTableBody");
  tbody.innerHTML = "";
  for (const b of breweries) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(b.name)}</td>
      <td>${escapeHtml(b.city || "")}</td>
      <td><input type="text" class="untappd-url-input ${b.untappd_url ? "is-set" : ""}" data-id="${b.id}" placeholder="https://untappd.com/.../beer" value="${escapeAttr(b.untappd_url || "")}"></td>
      <td class="last-checked">${b.last_untappd_checked_at ? formatDateTime(b.last_untappd_checked_at) : "never"}</td>
      <td class="last-checked">${b.last_refreshed_at ? formatDateTime(b.last_refreshed_at) : "never"}</td>
      <td>${b.active ? "Yes" : "No"}</td>
      <td><button class="toggle-active-btn ${b.active ? "is-active" : ""}" data-id="${b.id}" data-active="${b.active ? "1" : "0"}">${b.active ? "Deactivate" : "Activate"}</button></td>
    `;
    tbody.appendChild(tr);
  }
  $all(".toggle-active-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const nowActive = btn.dataset.active !== "1";
      await adminFetch(`/api/breweries/${btn.dataset.id}/active`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: nowActive }),
      });
      loadBreweries();
    });
  });
  $all(".untappd-url-input").forEach((input) => {
    input.addEventListener("change", async () => {
      const res = await adminFetch(`/api/breweries/${input.dataset.id}/untappd_url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ untappd_url: input.value.trim() }),
      });
      const data = await res.json();
      if (!data.ok) {
        alert(data.error || "Could not save Untappd URL.");
        return;
      }
      loadBreweries();
    });
  });

  const datalist = $("#breweryNames");
  datalist.innerHTML = breweries.map((b) => `<option value="${escapeAttr(b.name)}">`).join("");
}

$("#addBreweryForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#newBreweryName").value.trim();
  const city = $("#newBreweryCity").value.trim();
  if (!name) return;
  await adminFetch("/api/breweries", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, city }),
  });
  $("#newBreweryName").value = "";
  $("#newBreweryCity").value = "";
  loadBreweries();
});

// ---------- Manual release entry ----------
$("#toggleManualForm").addEventListener("click", () => {
  $("#manualReleaseForm").classList.toggle("hidden");
});

$("#manualReleaseForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const brewery_name = $("#manualBrewery").value.trim();
  const beer_name = $("#manualBeer").value.trim();
  const release_date = $("#manualDate").value;
  const source_url = $("#manualUrl").value.trim();
  if (!brewery_name || !beer_name || !release_date) return;

  const res = await adminFetch("/api/releases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ brewery_name, beer_name, release_date, source_url }),
  });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Could not add release.");
    return;
  }
  $("#manualReleaseForm").reset();
  $("#manualReleaseForm").classList.add("hidden");
  loadToday();
  loadCalendarData();
  loadBreweries();
});

async function loadSettings() {
  const res = await fetch("/api/settings");
  const settings = await res.json();
  $("#expirationDays").value = settings.expiration_days;
  $("#skipRecentDays").value = settings.skip_recent_days;
  $("#lastRefreshed").textContent = settings.last_refresh_completed_at
    ? `Data last updated: ${formatDateTime(settings.last_refresh_completed_at)}`
    : "Data not refreshed yet";
}

$("#saveSettingsBtn").addEventListener("click", async () => {
  const expirationDays = parseInt($("#expirationDays").value, 10);
  const skipRecentDays = parseInt($("#skipRecentDays").value, 10);
  if (!expirationDays || expirationDays < 1) return;
  if (isNaN(skipRecentDays) || skipRecentDays < 0) return;
  await adminFetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expiration_days: expirationDays, skip_recent_days: skipRecentDays }),
  });
  loadToday();
  loadCalendarData();
});

// ---------- Refresh ----------
$("#refreshBtn").addEventListener("click", async () => {
  const btn = $("#refreshBtn");
  const statusEl = $("#refreshStatus");
  const detailsEl = $("#refreshDetails");
  const force = $("#forceRefresh").checked;
  btn.disabled = true;
  btn.textContent = "Refreshing...";
  statusEl.className = "refresh-status ok";
  statusEl.textContent = "Searching breweries (recently-confirmed ones are skipped unless \"Force re-search all\" is checked) — this can take a few minutes, feel free to leave this tab open and check back.";
  statusEl.classList.remove("hidden");
  detailsEl.classList.add("hidden");

  try {
    const res = await adminFetch("/api/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force }),
    });
    const data = await res.json().catch(() => null);
    const s = data?.summary ?? {};
    const details = s?.details ?? [];
    const errors = s?.errors ?? [];

    if (data?.ok) {
      statusEl.className = "refresh-status ok";
      statusEl.textContent = `Untappd: checked ${s.untappd_checked ?? 0} breweries. ` +
        `Instagram: searched ${s.ig_searched ?? 0}, skipped ${s.ig_skipped ?? 0} (recently confirmed). ` +
        `${s.inserted ?? 0} new, ${s.updated ?? 0} confirmed, ${s.merged ?? 0} upgraded from upcoming to on-draft.` +
        (s.discarded_ungrounded ? ` ${s.discarded_ungrounded} discarded as unverified.` : "") +
        (s.discarded_stale_source ? ` ${s.discarded_stale_source} discarded as stale/off-domain sources.` : "") +
        (errors.length ? ` ${errors.length} error(s) — see details below.` : "");
      loadSettings(); // pulls the server-side last_refresh_completed_at
    } else {
      statusEl.className = "refresh-status error";
      statusEl.textContent = data?.error || "Refresh failed (no response from server).";
    }
    renderRefreshDetails(details);
  } catch (err) {
    statusEl.className = "refresh-status error";
    statusEl.textContent = "Refresh failed: " + err.message;
  } finally {
    statusEl.classList.remove("hidden");
    btn.disabled = false;
    btn.textContent = "Refresh";
    loadToday();
    loadCalendarData();
    loadBreweries();
  }
});

function renderRefreshDetails(details) {
  const detailsEl = $("#refreshDetails");
  const list = $("#refreshDetailsList");
  if (!details || details.length === 0) {
    detailsEl.classList.add("hidden");
    return;
  }
  const labels = {
    found: "Found",
    no_results: "No results",
    no_matches: "No fresh hop match",
    no_url: "No Untappd URL",
    skipped: "Skipped (recently confirmed)",
    error: "Error",
  };
  const trackLabels = { untappd: "Untappd", instagram: "IG search" };
  const order = { error: 0, no_matches: 1, no_results: 2, no_url: 3, skipped: 4, found: 5 };
  list.innerHTML = details
    .slice()
    .sort((a, b) => (order[a.status] ?? 6) - (order[b.status] ?? 6))
    .map((d) => {
      const track = d.track ? `<span class="track-tag">${escapeHtml(trackLabels[d.track] || d.track)}</span>` : "";
      return `<li class="detail-${d.status}">${track}<strong>${escapeHtml(d.brewery)}</strong> — ${labels[d.status] || d.status}: ${escapeHtml(d.message)}</li>`;
    })
    .join("");
  $("#refreshDetailsSummary").textContent = `Per-brewery results (${details.length})`;
  detailsEl.classList.remove("hidden");
}

// ---------- Helpers ----------
function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
function formatDateTime(isoUtc) {
  const d = new Date(isoUtc.endsWith("Z") ? isoUtc : isoUtc + "Z");
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function labelForStatus(status) {
  return { upcoming: "Upcoming", on_draft: "On Draft", unknown_end: "Likely Gone" }[status] || status;
}
function sourceBadge(sourceType) {
  const labels = { untappd: "Untappd", instagram: "IG search", manual: "Manual" };
  if (!sourceType || !labels[sourceType]) return "";
  return ` <span class="source-badge source-badge-${sourceType}">${labels[sourceType]}</span>`;
}
function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeAttr(str) { return escapeHtml(str); }

// ---------- Init ----------
initAuth().then(() => {
  loadToday();
  loadCalendarData();
  loadBreweries();
  loadSettings();
});
