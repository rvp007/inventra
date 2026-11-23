/**
 * INVENTRA Frontend Application Controller
 * Connects Sriraksha's Bootstrap UI directly to the Django + Supabase REST API.
 */
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

let currentUser = null;
let currentPage = 'dashboard';
let dashData = { stats: {}, labs: [], all_requests: [], pending_requests: [], active_loans: [], reservations: [], maintenance_tickets: [], notifications: [] };
let searchResults = [];
let selectedAsset = null;
let searchTimer = null;

async function api(url, options = {}) {
  const opts = { credentials: 'same-origin', ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(url, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `Request failed (${resp.status})`);
  return data;
}

function toast(msg, isError = false) {
  const el = $('#appToast');
  el.classList.toggle('text-bg-danger', isError);
  el.classList.toggle('text-bg-dark', !isError);
  $('#toastMessage').textContent = msg;
  bootstrap.Toast.getOrCreateInstance(el).show();
}

function esc(str) {
  return String(str ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function initials(name) {
  return (name || 'IN').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
}

function statusBadge(s) {
  const key = String(s || '').toLowerCase().replaceAll(' ', '-');
  return `<span class="status status-${key}">${esc(s)}</span>`;
}

function pageHeading(title, sub, action = '') {
  return `<div class="page-heading"><div><h1 class="page-title">${title}</h1><p class="page-subtitle">${sub}</p></div><div class="d-flex gap-2 flex-wrap">${action}</div></div>`;
}

function stat(label, value, icon, tone) {
  return `<div class="stat-card"><div><div class="stat-label">${label}</div><div class="stat-value">${value ?? 0}</div></div><div class="stat-icon tone-${tone}"><i class="bi ${icon}"></i></div></div>`;
}

const ROLE_LABELS = { STUDENT: 'Student / Researcher', FACULTY: 'Faculty', ASSISTANT: 'Lab Assistant', ADMIN: 'Administrator' };
const MENUS = {
  STUDENT: [['dashboard', 'grid-1x2', 'Dashboard'], ['browse', 'search', 'Universal Search'], ['requests', 'file-earmark-text', 'My Requests'], ['reservations', 'calendar-check', 'Machine Slots'], ['labs', 'building', 'Labs Overview'], ['notifications', 'bell', 'Notifications']],
  FACULTY: [['dashboard', 'grid-1x2', 'Dashboard'], ['browse', 'search', 'Universal Search'], ['requests', 'file-earmark-text', 'My Requests'], ['reservations', 'calendar-check', 'Machine Slots'], ['labs', 'building', 'Labs Overview'], ['reports', 'bar-chart', 'Reports'], ['notifications', 'bell', 'Notifications']],
  ASSISTANT: [['dashboard', 'grid-1x2', 'Dashboard'], ['browse', 'search', 'Universal Search'], ['approvals', 'clipboard-check', 'Approve Requests'], ['issue', 'arrow-left-right', 'Issue / Return'], ['reservations', 'calendar-check', 'Slot Check-In'], ['manage', 'boxes', 'Inventory & Import'], ['labs', 'building', 'Labs Overview'], ['reports', 'bar-chart', 'Reports'], ['notifications', 'bell', 'Notifications']],
  ADMIN: [['dashboard', 'grid-1x2', 'Dashboard'], ['browse', 'search', 'Universal Search'], ['approvals', 'clipboard-check', 'Approve Requests'], ['issue', 'arrow-left-right', 'Issue / Return'], ['reservations', 'calendar-check', 'Slot Check-In'], ['manage', 'boxes', 'Manage & Import'], ['labs', 'building', 'Labs Overview'], ['reports', 'bar-chart', 'Reports'], ['notifications', 'bell', 'Notifications']],
};

async function refreshData() {
  const [dash, search] = await Promise.all([api('/api/dashboard/'), api('/api/search/')]);
  dashData = dash;
  searchResults = search.results || [];
}

function applyUserSession(user) {
  currentUser = user;
  const label = ROLE_LABELS[user.role] || user.role;
  const init = initials(user.full_name || user.username);
  $('#sideUser').textContent = user.full_name || user.username;
  $('#topUser').textContent = user.full_name || user.username;
  $('#sideRole').textContent = label;
  $('#topRole').textContent = label;
  $('#sideAvatar').textContent = init;
  $('#topAvatar').textContent = init;
  $('#loginView').classList.add('d-none');
  $('#appView').classList.remove('d-none');
}

function renderNav() {
  const items = MENUS[currentUser?.role] || MENUS.STUDENT;
  $('#sideNav').innerHTML = items.map(([id, ico, label]) =>
    `<a class="nav-link ${currentPage === id ? 'active' : ''}" href="#" data-page="${id}"><i class="bi bi-${ico}"></i>${label}</a>`
  ).join('');
  $$('[data-page]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); go(a.dataset.page); }));
}

async function go(page, skipRefresh = false) {
  currentPage = page;
  const titles = { dashboard: 'Dashboard', browse: 'Universal Cross-Lab Search', labs: 'Labs Overview', requests: 'My Requests', reservations: 'Machine Reservations', approvals: 'Pending Approvals', issue: 'Issue / Return Desk', manage: 'Manage Equipment & Any-Format Import', reports: 'Reports & Export', notifications: 'Notifications', details: 'Equipment Details' };
  $('#crumb').textContent = titles[page] || 'Dashboard';
  renderNav();
  $('#sidebar').classList.remove('open');
  if (!skipRefresh && page !== 'details') {
    try { await refreshData(); } catch (e) { toast(e.message, true); }
  }
  renderPage();
}

function renderPage() {
  const pages = { dashboard, browse, labs, requests, reservations, approvals, issue, manage, reports, notifications, details };
  $('#pageContent').innerHTML = (pages[currentPage] || dashboard)();
}

function quick(icon, title, desc, page) {
  return `<div class="col-md-6"><a class="quick-card" href="#" onclick="go('${page}');return false"><span class="quick-icon"><i class="bi bi-${icon}"></i></span><span><strong>${title}</strong><small>${desc}</small></span><i class="bi bi-arrow-up-right ms-auto text-secondary"></i></a></div>`;
}

function dashboard() {
  const s = dashData.stats || {};
  const role = currentUser.role;
  const isStaff = role === 'ASSISTANT' || role === 'ADMIN';
  const cards = isStaff
    ? [stat('Total assets', s.total_assets, 'boxes', 'blue'), stat('Available items', s.available_assets, 'check-circle', 'green'), stat('Pending approvals', s.pending_requests_count, 'hourglass-split', 'amber'), stat('In maintenance', s.maintenance_assets, 'wrench-adjustable', 'red')]
    : [stat('Available equipment', s.available_assets, 'box-seam', 'green'), stat('My pending requests', s.pending_requests_count, 'hourglass-split', 'blue'), stat('Active machine slots', s.active_reservations_count, 'calendar-check', 'green'), stat('Active loans', s.active_loans_count, 'arrow-left-right', 'amber')];

  return `
    ${pageHeading('Dashboard', `Welcome, ${esc(currentUser.full_name)} (${ROLE_LABELS[role]}). Live campus lab activity.`,
      `<button class="btn btn-outline-primary btn-sm" onclick="openQrLookup()"><i class="bi bi-qr-code-scan me-1"></i>QR Tag Lookup</button>`)}
    <div class="row g-3 mb-4">${cards.map(c => `<div class="col-6 col-xl-3">${c}</div>`).join('')}</div>
    <div class="row g-3">
      <div class="col-xl-7">
        <div class="panel h-100">
          <div class="panel-head"><h2 class="section-title">Quick access</h2><span class="small text-secondary">${dashData.labs.length} active labs</span></div>
          <div class="row g-3">
            ${quick('search', 'Universal search', 'Compare stock across all campus labs', 'browse')}
            ${quick('calendar-check', 'Book machine slot', 'Strict 1–2 hr advance booking', 'reservations')}
            ${isStaff ? quick('clipboard-check', 'Approve requests', 'Verify borrow reasons & issue', 'approvals') + quick('cloud-upload', 'Import any dataset', 'Upload XLSX, PDF, DOCX, CSV, JSON', 'manage') : quick('file-earmark-text', 'My requests', 'Track approvals & due dates', 'requests')}
            ${quick('building', 'Labs overview', 'Explore equipment by lab', 'labs')}
          </div>
        </div>
      </div>
      <div class="col-xl-5">
        <div class="panel h-100">
          <div class="panel-head"><h2 class="section-title">Live alerts &amp; activity</h2><button class="btn btn-sm btn-light" onclick="go('notifications')">View all</button></div>
          ${(dashData.notifications || []).slice(0, 4).map(notificationRow).join('') || '<p class="text-secondary small">No alerts right now.</p>'}
        </div>
      </div>
    </div>
    <div class="panel mt-3">
      <div class="panel-head"><h2 class="section-title">Live campus inventory sample</h2><button class="btn btn-sm btn-light" onclick="go('browse')">Search all ${s.total_assets || 0} items</button></div>
      ${equipmentTable(searchResults.slice(0, 8))}
    </div>`;
}

function labs() {
  return `
    ${pageHeading('Labs Overview', 'All registered Chanakya University laboratories synced from Supabase.')}
    <div class="row g-3">
      ${(dashData.labs || []).map(l => `
        <div class="col-md-6 col-xl-4">
          <a href="#" class="lab-card" onclick="openLabInBrowse('${l.lab_id}');return false">
            <div class="lab-icon"><i class="bi bi-building"></i></div>
            <h3>${esc(l.lab_name)}</h3>
            <p class="mb-3">Prefix: <strong>INV-${esc(l.lab_code)}-</strong> · ${l.asset_count} tracked assets</p>
            <div class="d-flex justify-content-between align-items-center">
              <span class="small text-secondary">${esc(l.lab_code)}</span>
              <span class="small fw-bold text-primary">View equipment <i class="bi bi-arrow-right"></i></span>
            </div>
          </a>
        </div>`).join('') || '<div class="col-12"><div class="empty-state">No labs found. Run ingest_inventory or upload a dataset!</div></div>'}
    </div>`;
}

async function openLabInBrowse(labId) {
  await go('browse', true);
  const sel = $('#labFilter');
  if (sel) { sel.value = labId; runLiveSearch(); }
}

function equipmentTable(items) {
  return `<div class="table-responsive"><table class="table"><thead><tr><th>Asset Tag</th><th>Equipment</th><th>Lab</th><th>Stock</th><th>Condition</th><th>Status</th><th></th></tr></thead><tbody>
    ${items.map(e => `<tr>
      <td><code>${esc(e.asset_tag)}</code></td>
      <td class="fw-bold">${esc(e.name)}<div class="small text-secondary">${esc(e.category)}</div></td>
      <td>${esc(e.lab.lab_name)}</td>
      <td><strong>${e.stock_quantity}</strong> / ${e.total_quantity}${e.is_low_stock ? ' <span class="badge text-bg-warning">Low</span>' : ''}</td>
      <td>${statusBadge(e.condition)}</td>
      <td>${statusBadge(e.status)}</td>
      <td><button class="btn btn-sm btn-light" onclick="openDetails('${e.asset_id}')">View</button></td>
    </tr>`).join('') || '<tr><td colspan="7" class="text-center text-secondary py-4">No equipment found.</td></tr>'}
  </tbody></table></div>`;
}

function browse() {
  return `
    ${pageHeading('Universal Cross-Lab Search', 'Search once to see matching items across Physics, Chemistry, Electronics, CS, and all other labs side-by-side.')}
    <div class="panel">
      <div class="filter-bar">
        <input id="equipmentSearch" class="form-control" style="max-width:320px" placeholder="Search name, tag (INV-...), serial, or lab..." oninput="scheduleSearch()">
        <select id="labFilter" class="form-select" onchange="runLiveSearch()">
          <option value="">All laboratories (${dashData.labs.length})</option>
          ${(dashData.labs || []).map(l => `<option value="${l.lab_id}">${esc(l.lab_name)}</option>`).join('')}
        </select>
        <select id="statusFilter" class="form-select" onchange="runLiveSearch()">
          <option value="">All statuses</option>
          <option value="AVAILABLE">Available</option><option value="ISSUED">Issued</option>
          <option value="RESERVED">Reserved</option><option value="MAINTENANCE">Maintenance</option>
        </select>
      </div>
      <div class="small text-secondary mb-3" id="searchCount">Showing ${searchResults.length} matching records across all labs</div>
      <div class="row g-3" id="equipmentGrid">${equipmentCards(searchResults)}</div>
    </div>`;
}

function scheduleSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runLiveSearch, 250);
}

async function runLiveSearch() {
  const q = encodeURIComponent($('#equipmentSearch')?.value || '');
  const lab = encodeURIComponent($('#labFilter')?.value || '');
  const st = encodeURIComponent($('#statusFilter')?.value || '');
  try {
    const res = await api(`/api/search/?q=${q}&lab=${lab}&status=${st}`);
    searchResults = res.results || [];
    const cnt = $('#searchCount');
    if (cnt) cnt.textContent = `Showing ${searchResults.length} matching records across all labs`;
    const grid = $('#equipmentGrid');
    if (grid) grid.innerHTML = equipmentCards(searchResults);
  } catch (e) { toast(e.message, true); }
}

function equipmentCards(items) {
  return items.map(e => `
    <div class="col-sm-6 col-xl-4">
      <div class="equipment-card">
        <div class="equipment-body">
          <div class="d-flex justify-content-between align-items-center mb-2">
            <code class="small">${esc(e.asset_tag)}</code>
            ${statusBadge(e.status)}
          </div>
          <h3>${esc(e.name)}</h3>
          <p class="mb-2"><i class="bi bi-building me-1"></i><strong>${esc(e.lab.lab_name)}</strong></p>
          <div class="small text-secondary mb-3">Stock: <strong>${e.stock_quantity}/${e.total_quantity}</strong> · ${esc(e.condition)} · ${esc(e.category)}</div>
          <div class="equipment-meta">
            <span class="badge text-bg-light border">${esc(e.asset_type)}</span>
            <button class="btn btn-sm btn-outline-primary" onclick="openDetails('${e.asset_id}')">Details &amp; Actions</button>
          </div>
        </div>
      </div>
    </div>`).join('') || '<div class="col-12"><div class="empty-state">No matching equipment found across labs.</div></div>';
}

function openDetails(assetId) {
  selectedAsset = searchResults.find(a => a.asset_id === assetId);
  if (selectedAsset) go('details', true);
}

function details() {
  const e = selectedAsset || searchResults[0];
  if (!e) return '<div class="empty-state">Select an asset first.</div>';
  const extraRows = Object.entries(e.extra_metadata || {}).map(([k, v]) =>
    `<div class="col-6"><small class="text-secondary">${esc(k)}</small><div class="fw-semibold">${esc(v)}</div></div>`
  ).join('');

  return `
    ${pageHeading('Equipment Details', 'Live Supabase state, QR verification tag, and schema-agnostic metadata.',
      '<button class="btn btn-light" onclick="go(\'browse\', true)"><i class="bi bi-arrow-left me-1"></i>Back</button>')}
    <div class="row g-3">
      <div class="col-lg-4">
        <div class="panel text-center h-100">
          ${e.qr_code_url ? `<img src="${e.qr_code_url}" alt="QR Code" class="img-fluid rounded border p-2 mb-2" style="max-width:210px">` : '<div class="detail-icon mb-2"><i class="bi bi-qr-code"></i></div>'}
          <div><code>${esc(e.asset_tag)}</code></div>
          <small class="text-secondary">Auto-generated Lab QR Tag</small>
        </div>
      </div>
      <div class="col-lg-8">
        <div class="panel h-100">
          <div class="d-flex justify-content-between align-items-start">
            <div><span class="badge text-bg-light border mb-1">${esc(e.lab.lab_name)}</span><h2 class="section-title fs-4">${esc(e.name)}</h2></div>
            ${statusBadge(e.status)}
          </div>
          <div class="row g-3 mt-3">
            <div class="col-6 col-md-4"><small class="text-secondary">Available / Total</small><div class="fw-bold">${e.stock_quantity} / ${e.total_quantity}</div></div>
            <div class="col-6 col-md-4"><small class="text-secondary">Condition</small><div class="fw-bold">${esc(e.condition)}</div></div>
            <div class="col-6 col-md-4"><small class="text-secondary">Type</small><div class="fw-bold">${esc(e.asset_type)}</div></div>
            <div class="col-6 col-md-4"><small class="text-secondary">Category</small><div class="fw-bold">${esc(e.category)}</div></div>
            <div class="col-6 col-md-8"><small class="text-secondary">Serial Number</small><div class="fw-bold">${esc(e.serial_number)}</div></div>
            ${extraRows}
          </div>
          <hr>
          <div class="d-flex gap-2 flex-wrap">
            <button class="btn btn-primary" onclick="openBorrowModal('${e.asset_id}')" ${e.status !== 'AVAILABLE' || e.stock_quantity < 1 ? 'disabled' : ''}><i class="bi bi-hand-index-thumb me-1"></i>Request to Borrow</button>
            <button class="btn btn-outline-primary" onclick="openReservationModal('${e.asset_id}')" ${e.status === 'MAINTENANCE' || e.status === 'LOST' ? 'disabled' : ''}><i class="bi bi-calendar-plus me-1"></i>Book Machine Slot (1–2 hr advance)</button>
          </div>
        </div>
      </div>
    </div>`;
}

function openBorrowModal(assetId) {
  const e = searchResults.find(a => a.asset_id === assetId);
  showModal(`Borrow: ${esc(e.name)}`, `
    <form id="borrowForm">
      <div class="mb-2"><label class="form-label">Lab &amp; Tag</label><input class="form-control" value="${esc(e.lab.lab_name)} (${esc(e.asset_tag)})" disabled></div>
      <div class="mb-2"><label class="form-label">Quantity (Max ${e.stock_quantity})</label><input name="quantity" type="number" min="1" max="${e.stock_quantity}" value="1" class="form-control" required></div>
      <div class="mb-3">
        <label class="form-label">Reason for Borrowing <span class="text-danger">*</span></label>
        <textarea name="borrow_reason" class="form-control" rows="3" minlength="5" placeholder="Mandatory: state course/project purpose (Lab Assistant cannot approve without a valid reason)" required></textarea>
      </div>
      <button class="btn btn-primary w-100">Submit Borrow Request</button>
    </form>`, () => {
    $('#borrowForm').onsubmit = async ev => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      try {
        await api('/api/transactions/request/', {
          method: 'POST',
          body: { asset_id: assetId, quantity: Number(fd.get('quantity')), borrow_reason: fd.get('borrow_reason') }
        });
        bootstrap.Modal.getInstance($('#genericModal')).hide();
        toast('Borrow request submitted!');
        go('requests');
      } catch (err) { toast(err.message, true); }
    };
  });
}

function requests() {
  return `
    ${pageHeading('My Requests', 'Track all submitted borrowing requests, reasons, and due dates.',
      '<button class="btn btn-primary" onclick="go(\'browse\')"><i class="bi bi-plus-lg me-1"></i>New request</button>')}
    <div class="panel">${requestTable(dashData.all_requests || [])}</div>`;
}

function requestTable(items) {
  return `<div class="table-responsive"><table class="table"><thead><tr><th>Tag</th><th>Borrower</th><th>Equipment &amp; Lab</th><th>Mandatory Reason</th><th>Qty</th><th>Due Date</th><th>Status</th></tr></thead><tbody>
    ${items.map(r => `<tr>
      <td><code>${esc(r.asset_tag)}</code></td>
      <td>${esc(r.borrower)}</td>
      <td><strong>${esc(r.asset_name)}</strong><div class="small text-secondary">${esc(r.lab)}</div></td>
      <td class="small">${esc(r.borrow_reason || '—')}</td>
      <td>${r.quantity}</td>
      <td class="small">${fmtDate(r.due_date)}</td>
      <td>${statusBadge(r.request_status)}</td>
    </tr>`).join('') || '<tr><td colspan="7" class="text-center py-4 text-secondary">No requests yet.</td></tr>'}
  </tbody></table></div>`;
}

function reservations() {
  const isStaff = ['ASSISTANT', 'ADMIN'].includes(currentUser.role);
  return `
    ${pageHeading('Machine Time-Slot Reservations', 'Strict window: book 1 to 2 hours ahead only. Uncollected slots auto-expire 30 minutes after start.',
      '<button class="btn btn-primary" onclick="openReservationModal()"><i class="bi bi-plus-lg me-1"></i>New Slot Booking</button>')}
    <div class="panel">
      <div class="table-responsive"><table class="table"><thead><tr><th>Tag</th><th>Machine &amp; Lab</th><th>Booked By</th><th>Start Time</th><th>End Time</th><th>Status</th><th>Action</th></tr></thead><tbody>
        ${(dashData.reservations || []).map(r => `<tr>
          <td><code>${esc(r.asset_tag)}</code></td>
          <td><strong>${esc(r.asset_name)}</strong><div class="small text-secondary">${esc(r.lab)}</div></td>
          <td>${esc(r.user)}</td>
          <td class="small">${fmtDate(r.start_time)}</td>
          <td class="small">${fmtDate(r.end_time)}</td>
          <td>${statusBadge(r.status)}</td>
          <td>
            ${isStaff && r.status === 'ACTIVE' ? `<button class="btn btn-sm btn-success me-1" onclick="checkInRes('${r.reservation_id}')">Check In</button>` : ''}
            ${r.status === 'ACTIVE' ? `<button class="btn btn-sm btn-outline-danger" onclick="cancelRes('${r.reservation_id}')">Cancel</button>` : '—'}
          </td>
        </tr>`).join('') || '<tr><td colspan="7" class="text-center py-4 text-secondary">No reservations found.</td></tr>'}
      </tbody></table></div>
    </div>`;
}

function toLocalIsoInput(date) {
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function openReservationModal(preselectedAssetId = '') {
  const startDef = new Date(Date.now() + 75 * 60000);
  const endDef = new Date(Date.now() + 135 * 60000);
  showModal('Book Machine Slot (Strict 1–2 Hr Advance)', `
    <div class="alert alert-info py-2 small">Slots must start between <strong>60 and 120 minutes</strong> from now and expire automatically after 30 minutes if unchecked.</div>
    <form id="resForm">
      <div class="mb-2"><label class="form-label">Equipment / Machine</label>
        <select name="asset_id" class="form-select" required>
          ${searchResults.map(a => `<option value="${a.asset_id}" ${a.asset_id === preselectedAssetId ? 'selected' : ''}>[${esc(a.asset_tag)}] ${esc(a.name)} — ${esc(a.lab.lab_name)}</option>`).join('')}
        </select>
      </div>
      <div class="row g-2 mb-2">
        <div class="col-md-6"><label class="form-label">Start Time</label><input name="start_time" type="datetime-local" class="form-control" value="${toLocalIsoInput(startDef)}" required></div>
        <div class="col-md-6"><label class="form-label">End Time</label><input name="end_time" type="datetime-local" class="form-control" value="${toLocalIsoInput(endDef)}" required></div>
      </div>
      <div class="mb-3"><label class="form-label">Purpose</label><input name="purpose" class="form-control" placeholder="e.g. 3D printing rover chassis" required></div>
      <button class="btn btn-primary w-100">Confirm Strict Slot</button>
    </form>`, () => {
    $('#resForm').onsubmit = async ev => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      try {
        const res = await api('/api/reservations/book/', {
          method: 'POST',
          body: {
            asset_id: fd.get('asset_id'),
            start_time: new Date(fd.get('start_time')).toISOString(),
            end_time: new Date(fd.get('end_time')).toISOString(),
            purpose: fd.get('purpose'),
          }
        });
        bootstrap.Modal.getInstance($('#genericModal')).hide();
        toast(res.message);
        go('reservations');
      } catch (err) { toast(err.message, true); }
    };
  });
}

async function checkInRes(id) {
  try { const r = await api(`/api/reservations/${id}/checkin/`, { method: 'POST' }); toast(r.message); go('reservations'); }
  catch (e) { toast(e.message, true); }
}

async function cancelRes(id) {
  try { const r = await api(`/api/reservations/${id}/cancel/`, { method: 'POST' }); toast(r.message); go('reservations'); }
  catch (e) { toast(e.message, true); }
}

function approvals() {
  const pending = dashData.pending_requests || [];
  return `
    ${pageHeading('Pending Approvals', 'Lab Assistants can only approve requests with a valid stated borrow reason. Stock updates autonomously.')}
    <div class="panel mb-3">
      <div class="panel-head"><h2 class="section-title">Awaiting verification</h2><span class="status status-pending">${pending.length} pending</span></div>
      <div class="table-responsive"><table class="table"><thead><tr><th>Tag</th><th>Borrower</th><th>Equipment &amp; Lab</th><th>Qty</th><th>Stated Borrow Reason</th><th>Action</th></tr></thead><tbody>
        ${pending.map(r => `<tr>
          <td><code>${esc(r.asset_tag)}</code></td>
          <td>${esc(r.borrower)}<div class="small text-secondary">${esc(r.borrower_email)}</div></td>
          <td><strong>${esc(r.asset_name)}</strong><div class="small text-secondary">${esc(r.lab)}</div></td>
          <td>${r.quantity}</td>
          <td><div class="p-2 bg-light rounded small">${esc(r.borrow_reason || 'NO REASON PROVIDED')}</div></td>
          <td>
            <button class="btn btn-sm btn-success me-1" onclick="approveTx('${r.transaction_id}')">Approve &amp; Issue</button>
            <button class="btn btn-sm btn-outline-danger" onclick="rejectTx('${r.transaction_id}')">Reject</button>
          </td>
        </tr>`).join('') || '<tr><td colspan="6" class="text-center py-4 text-secondary">All caught up! No pending requests.</td></tr>'}
      </tbody></table></div>
    </div>
    <div class="panel"><h2 class="section-title mb-3">Full transaction audit trail</h2>${requestTable(dashData.all_requests || [])}</div>`;
}

async function approveTx(id) {
  try { const r = await api(`/api/transactions/${id}/approve/`, { method: 'POST', body: { loan_days: 7 } }); toast(r.message); go('approvals'); }
  catch (e) { toast(e.message, true); }
}

async function rejectTx(id) {
  const note = prompt('Optional rejection note:') || '';
  try { const r = await api(`/api/transactions/${id}/reject/`, { method: 'POST', body: { rejection_note: note } }); toast(r.message); go('approvals'); }
  catch (e) { toast(e.message, true); }
}

function issue() {
  const active = dashData.active_loans || [];
  const tickets = dashData.maintenance_tickets || [];
  return `
    ${pageHeading('Issue / Return & Maintenance Desk', 'Returns marked DAMAGED or UNUSABLE automatically move to Maintenance and generate repair tickets.')}
    <div class="panel mb-3">
      <div class="panel-head"><h2 class="section-title">Active issued equipment (${active.length})</h2></div>
      <div class="table-responsive"><table class="table"><thead><tr><th>Tag</th><th>Borrower</th><th>Equipment &amp; Lab</th><th>Qty</th><th>Due Date</th><th>Action</th></tr></thead><tbody>
        ${active.map(r => `<tr>
          <td><code>${esc(r.asset_tag)}</code></td>
          <td>${esc(r.borrower)}</td>
          <td><strong>${esc(r.asset_name)}</strong><div class="small text-secondary">${esc(r.lab)}</div></td>
          <td>${r.quantity}</td>
          <td class="small">${fmtDate(r.due_date)}</td>
          <td><button class="btn btn-sm btn-success" onclick="openReturnModal('${r.transaction_id}', '${esc(r.asset_name)}')">Log Return</button></td>
        </tr>`).join('') || '<tr><td colspan="6" class="text-center py-4 text-secondary">No active loans right now.</td></tr>'}
      </tbody></table></div>
    </div>
    <div class="panel">
      <div class="panel-head"><h2 class="section-title">Maintenance tickets (${tickets.length})</h2></div>
      <div class="table-responsive"><table class="table"><thead><tr><th>Tag</th><th>Asset</th><th>Fault Description</th><th>Status</th><th>Action</th></tr></thead><tbody>
        ${tickets.map(m => `<tr>
          <td><code>${esc(m.asset_tag)}</code></td>
          <td><strong>${esc(m.asset_name)}</strong><div class="small text-secondary">${esc(m.lab)}</div></td>
          <td class="small">${esc(m.description)}</td>
          <td>${statusBadge(m.resolution_status)}</td>
          <td>${m.resolution_status !== 'RESOLVED' ? `<button class="btn btn-sm btn-outline-success" onclick="resolveTicket('${m.ticket_id}')">Mark Resolved</button>` : 'Done'}</td>
        </tr>`).join('') || '<tr><td colspan="5" class="text-center py-4 text-secondary">No maintenance tickets.</td></tr>'}
      </tbody></table></div>
    </div>`;
}

function openReturnModal(txId, assetName) {
  showModal(`Return Check: ${assetName}`, `
    <form id="returnForm">
      <div class="mb-2"><label class="form-label">Returned Condition</label>
        <select name="return_condition" class="form-select">
          <option value="GOOD">Good (Restores to Available)</option>
          <option value="MINOR_DAMAGE">Minor Damage (Restores to Available)</option>
          <option value="DAMAGED">Damaged (Auto-routes to Maintenance)</option>
          <option value="UNUSABLE">Unusable (Auto-routes to Maintenance)</option>
        </select>
      </div>
      <div class="mb-3"><label class="form-label">Inspection Notes</label><textarea name="notes" class="form-control" rows="2" placeholder="Optional damage notes"></textarea></div>
      <button class="btn btn-success w-100">Confirm Return</button>
    </form>`, () => {
    $('#returnForm').onsubmit = async ev => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      try {
        const r = await api(`/api/transactions/${txId}/return/`, { method: 'POST', body: { return_condition: fd.get('return_condition'), notes: fd.get('notes') } });
        bootstrap.Modal.getInstance($('#genericModal')).hide();
        toast(r.message);
        go('issue');
      } catch (err) { toast(err.message, true); }
    };
  });
}

async function resolveTicket(ticketId) {
  try { const r = await api(`/api/maintenance/${ticketId}/resolve/`, { method: 'POST' }); toast(r.message); go('issue'); }
  catch (e) { toast(e.message, true); }
}

function manage() {
  const isAdmin = currentUser.role === 'ADMIN';
  return `
    ${pageHeading('Manage Equipment & Any-Format Ingestion', 'Add/edit records or throw ANY file format (.xlsx, .csv, .pdf, .docx, .json, .html, .xml, .txt) at the backend.',
      `<button class="btn btn-outline-primary" onclick="openUploadModal()"><i class="bi bi-cloud-upload me-1"></i>Import Any File</button>
       <button class="btn btn-primary" onclick="openAssetModal()"><i class="bi bi-plus-lg me-1"></i>Add Equipment</button>`)}
    <div class="panel">
      <div class="table-responsive"><table class="table"><thead><tr><th>Tag</th><th>Equipment</th><th>Lab</th><th>Qty</th><th>Condition</th><th>Status</th><th>Actions</th></tr></thead><tbody>
        ${searchResults.map(e => `<tr>
          <td><code>${esc(e.asset_tag)}</code></td>
          <td class="fw-bold">${esc(e.name)}</td>
          <td>${esc(e.lab.lab_name)}</td>
          <td>${e.stock_quantity}</td>
          <td>${statusBadge(e.condition)}</td>
          <td>${statusBadge(e.status)}</td>
          <td>
            <button class="btn btn-sm btn-light" onclick="openAssetModal('${e.asset_id}')"><i class="bi bi-pencil"></i></button>
            ${isAdmin ? `<button class="btn btn-sm btn-light text-danger" onclick="deleteAsset('${e.asset_id}')"><i class="bi bi-trash"></i></button>` : ''}
          </td>
        </tr>`).join('')}
      </tbody></table></div>
    </div>`;
}

function openUploadModal() {
  showModal('Universal Any-Format Data Ingestion', `
    <p class="small text-secondary">Upload <strong>.xlsx, .xls, .csv, .tsv, .json, .pdf, .docx, .html, .xml, or .txt</strong>. Extra unrecognized columns are preserved automatically inside Supabase JSONB.</p>
    <form id="uploadForm">
      <div class="mb-2"><label class="form-label">Select Dataset File</label><input name="file" type="file" class="form-control" required></div>
      <div class="mb-3"><label class="form-label">Target Lab Override (Optional)</label><input name="lab_name" class="form-control" placeholder="Leave blank to auto-detect from filename/sheet"></div>
      <button class="btn btn-primary w-100">Analyze &amp; Ingest into Supabase</button>
    </form>`, () => {
    $('#uploadForm').onsubmit = async ev => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      try {
        const res = await api('/api/inventory/ingest/', { method: 'POST', body: fd });
        bootstrap.Modal.getInstance($('#genericModal')).hide();
        toast(`Ingested ${res.imported} items into ${res.lab}!`);
        go('manage');
      } catch (err) { toast(err.message, true); }
    };
  });
}

function openAssetModal(assetId = '') {
  const e = searchResults.find(a => a.asset_id === assetId) || { name: '', category: 'Lab Equipment', asset_type: 'SHARED', stock_quantity: 1, condition: 'GOOD', lab: { lab_id: '' } };
  showModal(assetId ? `Edit ${esc(e.asset_tag)}` : 'Add Equipment', `
    <form id="assetForm">
      <div class="mb-2"><label class="form-label">Equipment Name</label><input name="name" class="form-control" value="${esc(e.name)}" required></div>
      <div class="row g-2 mb-2">
        <div class="col-md-6"><label class="form-label">Category</label><input name="category" class="form-control" value="${esc(e.category)}" required></div>
        <div class="col-md-6"><label class="form-label">Stock Quantity</label><input name="stock_quantity" type="number" min="0" class="form-control" value="${e.stock_quantity}" required></div>
      </div>
      <div class="row g-2 mb-2">
        <div class="col-md-6"><label class="form-label">Type</label>
          <select name="asset_type" class="form-select">
            <option value="SHARED" ${e.asset_type === 'SHARED' ? 'selected' : ''}>Shared Equipment</option>
            <option value="CONSUMABLE" ${e.asset_type === 'CONSUMABLE' ? 'selected' : ''}>Consumable</option>
            <option value="TIME_SLOTTED" ${e.asset_type === 'TIME_SLOTTED' ? 'selected' : ''}>Time-Slotted Machine</option>
          </select>
        </div>
        <div class="col-md-6"><label class="form-label">Condition</label>
          <select name="condition" class="form-select">
            ${['GOOD', 'MINOR_DAMAGE', 'DAMAGED', 'UNUSABLE'].map(c => `<option value="${c}" ${e.condition === c ? 'selected' : ''}>${c}</option>`).join('')}
          </select>
        </div>
      </div>
      <div class="mb-3"><label class="form-label">Laboratory</label>
        <select name="lab_id" class="form-select" required>
          ${(dashData.labs || []).map(l => `<option value="${l.lab_id}" ${l.lab_id === e.lab.lab_id ? 'selected' : ''}>${esc(l.lab_name)}</option>`).join('')}
        </select>
      </div>
      <button class="btn btn-primary w-100">Save to Supabase</button>
    </form>`, () => {
    $('#assetForm').onsubmit = async ev => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      try {
        const res = await api('/api/assets/save/', {
          method: 'POST',
          body: {
            asset_id: assetId || null,
            name: fd.get('name'),
            category: fd.get('category'),
            asset_type: fd.get('asset_type'),
            stock_quantity: Number(fd.get('stock_quantity')),
            condition: fd.get('condition'),
            lab_id: fd.get('lab_id'),
          }
        });
        bootstrap.Modal.getInstance($('#genericModal')).hide();
        toast(res.message);
        go('manage');
      } catch (err) { toast(err.message, true); }
    };
  });
}

async function deleteAsset(assetId) {
  if (!confirm('Permanently delete this asset from Supabase?')) return;
  try { const r = await api(`/api/assets/${assetId}/delete/`, { method: 'DELETE' }); toast(r.message); go('manage'); }
  catch (e) { toast(e.message, true); }
}

function openQrLookup() {
  showModal('QR Asset Tag Lookup', `
    <form id="qrForm" class="mb-3">
      <div class="input-group">
        <input id="qrInput" class="form-control" placeholder="Scan or type tag e.g. INV-CSUB44-0001" required>
        <button class="btn btn-primary">Lookup</button>
      </div>
    </form>
    <div id="qrResult"></div>`, () => {
    $('#qrForm').onsubmit = async ev => {
      ev.preventDefault();
      const tag = $('#qrInput').value.trim();
      try {
        const a = await api(`/api/qr/${encodeURIComponent(tag)}/`);
        $('#qrResult').innerHTML = `<div class="p-3 border rounded bg-light">
          <div class="d-flex justify-content-between"><strong>${esc(a.name)}</strong>${statusBadge(a.status)}</div>
          <div class="small text-secondary mt-1">${esc(a.lab)} · Stock: ${a.stock_quantity} · Condition: ${esc(a.condition)}</div>
          ${a.current_borrower ? `<div class="small mt-1 text-primary">Currently borrowed by: <strong>${esc(a.current_borrower)}</strong></div>` : ''}
        </div>`;
      } catch (err) { $('#qrResult').innerHTML = `<div class="alert alert-danger py-2 small">${esc(err.message)}</div>`; }
    };
  });
}

function reports() {
  const s = dashData.stats || {};
  return `
    ${pageHeading('Reports & Multi-Format Export', 'Export live Supabase inventory (including JSONB extra metadata) in multiple formats.')}
    <div class="row g-3 mb-4">
      <div class="col-6 col-xl-3">${stat('Total assets', s.total_assets, 'boxes', 'blue')}</div>
      <div class="col-6 col-xl-3">${stat('Low stock alerts', s.low_stock_count, 'exclamation-triangle', 'amber')}</div>
      <div class="col-6 col-xl-3">${stat('Overdue loans', s.overdue_count, 'clock-history', 'red')}</div>
      <div class="col-6 col-xl-3">${stat('Open repairs', s.open_maintenance_tickets, 'wrench', 'red')}</div>
    </div>
    <div class="row g-3">
      ${[['csv', 'CSV Spreadsheet', 'bi-filetype-csv'], ['xlsx', 'Excel Workbook (.xlsx)', 'bi-file-earmark-excel'], ['json', 'Structured JSON', 'bi-filetype-json'], ['html', 'Printable HTML Table', 'bi-filetype-html']].map(([fmt, title, ico]) => `
        <div class="col-md-6 col-xl-3">
          <div class="report-tile">
            <i class="bi ${ico}"></i><h3>${title}</h3>
            <p>Full live inventory snapshot with lab tags and extra metadata.</p>
            <a class="btn btn-sm btn-outline-primary mt-3" href="/api/inventory/export/?format=${fmt}">Download ${fmt.toUpperCase()}</a>
          </div>
        </div>`).join('')}
    </div>`;
}

function notificationRow(n) {
  return `<div class="notification-item"><div class="notification-symbol"><i class="bi ${n.icon || 'bi-bell'}"></i></div><div><strong>${esc(n.title)}</strong><p>${esc(n.detail)}</p><small>${esc(n.time)}</small></div></div>`;
}

function notifications() {
  return `${pageHeading('Live Notifications', 'Generated dynamically from overdue loans, low-stock thresholds, and request updates.')}<div class="panel">${(dashData.notifications || []).map(notificationRow).join('') || '<p class="text-secondary">No notifications.</p>'}</div>`;
}

function showModal(title, body, after) {
  $('#modalContent').innerHTML = `<div class="modal-header"><h5 class="modal-title">${title}</h5><button class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body">${body}</div>`;
  bootstrap.Modal.getOrCreateInstance($('#genericModal')).show();
  setTimeout(() => after && after(), 80);
}

// ==============================================================================
// AUTH TAB HANDLERS & INITIAL BOOT
// ==============================================================================
$('#tabSignIn').addEventListener('click', () => {
  $('#tabSignIn').classList.add('active'); $('#tabRegister').classList.remove('active');
  $('#signInPanel').classList.remove('d-none'); $('#registerPanel').classList.add('d-none');
});
$('#tabRegister').addEventListener('click', () => {
  $('#tabRegister').classList.add('active'); $('#tabSignIn').classList.remove('active');
  $('#registerPanel').classList.remove('d-none'); $('#signInPanel').classList.add('d-none');
});

$('#loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    const res = await api('/api/auth/login/', { method: 'POST', body: { email: $('#loginName').value.trim(), password: $('#loginPassword').value } });
    applyUserSession(res.user);
    toast(`Signed in as ${ROLE_LABELS[res.user.role]}.`);
    go('dashboard');
  } catch (err) { toast(err.message, true); }
});

$('#otpRequestForm').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    const res = await api('/api/auth/request-otp/', {
      method: 'POST',
      body: { email: $('#regEmail').value.trim(), role: $('#regRole').value, department: $('#regDept').value.trim() }
    });
    $('#otpVerifyForm').classList.remove('d-none');
    $('#otpStatusBanner').innerHTML = res.dev_otp
      ? `OTP sent! <strong>Dev Mode OTP: <code>${res.dev_otp}</code></strong> (also printed in your PowerShell terminal).`
      : esc(res.message);
    toast('OTP generated! Enter it below.');
  } catch (err) { toast(err.message, true); }
});

$('#otpVerifyForm').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    const res = await api('/api/auth/verify-otp/', {
      method: 'POST',
      body: {
        email: $('#regEmail').value.trim(),
        code: $('#regOtpCode').value.trim(),
        password: $('#regPassword').value,
        full_name: $('#regName').value.trim(),
      }
    });
    applyUserSession(res.user);
    toast('Account verified & created!');
    go('dashboard');
  } catch (err) { toast(err.message, true); }
});

$('#logoutBtn').addEventListener('click', async () => {
  await api('/api/auth/logout/', { method: 'POST' }).catch(() => {});
  currentUser = null;
  $('#appView').classList.add('d-none');
  $('#loginView').classList.remove('d-none');
  toast('Logged out.');
});

$('#togglePassword').addEventListener('click', () => {
  const p = $('#loginPassword');
  p.type = p.type === 'password' ? 'text' : 'password';
});
$('#mobileMenu').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
$('#topNotifications').addEventListener('click', () => go('notifications'));

// Auto-restore active session on page refresh
(async function boot() {
  try {
    const me = await api('/api/auth/me/');
    applyUserSession(me);
    go('dashboard');
  } catch (_) { /* Not logged in yet — stay on login view */ }
})();
