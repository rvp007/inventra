/**
 * INVENTRA Frontend Application Controller (v1.2 — Production Clean Rewrite)
 * Matches templates/index.html and Django REST API (/api/...).
 * Features:
 * - Live College Email OTP Registration with Assigned Lab selector for Lab Assistants
 * - Strict Lab-Scoped RBAC dashboards (Student, Subject Faculty, Lab Assistant, Admin)
 * - Universal Cross-Lab Search with persistent filter state
 * - Lab & Equipment CRUD + Universal 12-format Ingestion + Exact University PR Excel Exports
 * - Autonomous Borrow / Approve / Reject / Return / Maintenance Ticket workflows
 * - Strict 1–2 Hour Advance Machine Slot Reservations with 30-Min Auto-Expiry
 * - 5-Second Live Multi-Role Background Sync
 */

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

let currentUser = null;
let currentPage = 'dashboard';
let dashData = {
  stats: {},
  labs: [],
  all_requests: [],
  pending_requests: [],
  active_loans: [],
  reservations: [],
  maintenance_tickets: [],
  notifications: [],
};
let allAssets = [];
let searchResults = [];
let searchFilters = { q: '', lab: '', status: '' };
let selectedAssetId = null;
let searchTimer = null;
let livePollTimer = null;

const ROLE_LABELS = {
  STUDENT: 'Student / Researcher',
  FACULTY: 'Subject Faculty',
  ASSISTANT: 'Lab Assistant',
  ADMIN: 'Administrator',
};

const LAB_TYPE_OPTIONS = [
  ['COMPUTER_SCIENCE', 'Computer Science'],
  ['ELECTRONICS', 'Digital & Analog Electronics'],
  ['CHEMISTRY', 'Chemistry'],
  ['PHYSICS', 'Physics'],
  ['MECHANICAL_MANUFACTURING', 'Mechanical & Manufacturing'],
  ['IDEATION_DESIGN', 'Ideation & Design Thinking'],
];

const MENUS = {
  STUDENT: [
    ['dashboard', 'grid-1x2', 'Dashboard'],
    ['browse', 'search', 'Universal Search'],
    ['requests', 'file-earmark-text', 'My Requests'],
    ['reservations', 'calendar-check', 'Machine Slots'],
    ['labs', 'building', 'Labs Overview'],
    ['notifications', 'bell', 'Notifications'],
  ],
  FACULTY: [
    ['dashboard', 'grid-1x2', 'Dashboard'],
    ['browse', 'search', 'Universal Search'],
    ['manage', 'boxes', 'Manage & Import'],
    ['requests', 'file-earmark-text', 'My Requests'],
    ['reservations', 'calendar-check', 'Machine Slots'],
    ['labs', 'building', 'Labs Overview'],
    ['reports', 'file-earmark-spreadsheet', 'PR & Reports'],
    ['notifications', 'bell', 'Notifications'],
  ],
  ASSISTANT: [
    ['dashboard', 'grid-1x2', 'Dashboard'],
    ['browse', 'search', 'Universal Search'],
    ['approvals', 'clipboard-check', 'Approve Requests'],
    ['issue', 'arrow-left-right', 'Issue / Return'],
    ['reservations', 'calendar-check', 'Slot Check-In'],
    ['manage', 'boxes', 'My Lab Inventory'],
    ['labs', 'building', 'Labs Overview'],
    ['reports', 'file-earmark-spreadsheet', 'PR & Reports'],
    ['notifications', 'bell', 'Notifications'],
  ],
  ADMIN: [
    ['dashboard', 'grid-1x2', 'Dashboard'],
    ['browse', 'search', 'Universal Search'],
    ['approvals', 'clipboard-check', 'Approve Requests'],
    ['issue', 'arrow-left-right', 'Issue / Return'],
    ['reservations', 'calendar-check', 'Slot Check-In'],
    ['manage', 'boxes', 'Manage & Import'],
    ['labs', 'building', 'Labs Overview'],
    ['reports', 'file-earmark-spreadsheet', 'PR & Reports'],
    ['notifications', 'bell', 'Notifications'],
  ],
};

// ==============================================================================
// CORE HTTP & UI HELPERS
// ==============================================================================

async function api(url, options = {}) {
  const opts = { credentials: 'same-origin', ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(url, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || `Request failed (${resp.status})`);
  }
  return data;
}

function toast(msg, isError = false) {
  const el = $('#appToast');
  if (!el) return;
  el.classList.toggle('text-bg-danger', isError);
  el.classList.toggle('text-bg-dark', !isError);
  $('#toastMessage').textContent = msg;
  bootstrap.Toast.getOrCreateInstance(el).show();
}

function esc(str) {
  return String(str ?? '').replace(/[&<>"']/g, m => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[m]));
}

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function initials(name) {
  return (name || 'IN')
    .trim()
    .split(/\s+/)
    .map(w => w[0] || '')
    .join('')
    .slice(0, 2)
    .toUpperCase() || 'IN';
}

function statusBadge(s) {
  const key = String(s || '').toLowerCase().replaceAll(' ', '-');
  return `<span class="status status-${key}">${esc(s)}</span>`;
}

function pageHeading(title, sub, action = '') {
  return `
    <div class="page-heading">
      <div>
        <h1 class="page-title">${title}</h1>
        <p class="page-subtitle">${sub}</p>
      </div>
      <div class="d-flex gap-2 flex-wrap">${action}</div>
    </div>`;
}

function stat(label, value, icon, tone) {
  return `
    <div class="stat-card">
      <div>
        <div class="stat-label">${label}</div>
        <div class="stat-value">${value ?? 0}</div>
      </div>
      <div class="stat-icon tone-${tone}"><i class="bi ${icon}"></i></div>
    </div>`;
}

function setBtnBusy(btn, busy, busyLabel = 'Please wait...') {
  if (!btn) return;
  if (busy) {
    btn.dataset.origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-2"></span>${esc(busyLabel)}`;
  } else {
    btn.disabled = false;
    if (btn.dataset.origHtml) btn.innerHTML = btn.dataset.origHtml;
  }
}

function showModal(title, body, after) {
  $('#modalContent').innerHTML = `
    <div class="modal-header">
      <h5 class="modal-title">${title}</h5>
      <button class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
    </div>
    <div class="modal-body">${body}</div>`;
  bootstrap.Modal.getOrCreateInstance($('#genericModal')).show();
  setTimeout(() => after && after(), 60);
}

function hideModal() {
  const inst = bootstrap.Modal.getInstance($('#genericModal'));
  if (inst) inst.hide();
}

function findAsset(assetId) {
  return allAssets.find(a => a.asset_id === assetId) || searchResults.find(a => a.asset_id === assetId) || null;
}

// ==============================================================================
// DATA SYNC & NAVIGATION
// ==============================================================================

async function refreshData() {
  const q = encodeURIComponent(searchFilters.q || '');
  const lab = encodeURIComponent(searchFilters.lab || '');
  const st = encodeURIComponent(searchFilters.status || '');
  const hasFilters = Boolean(searchFilters.q || searchFilters.lab || searchFilters.status);

  const requests = [
    api('/api/dashboard/'),
    api('/api/search/'),
  ];
  if (hasFilters) {
    requests.push(api(`/api/search/?q=${q}&lab=${lab}&status=${st}`));
  }

  const [dash, allRes, filteredRes] = await Promise.all(requests);
  dashData = dash;
  allAssets = allRes.results || [];
  searchResults = hasFilters ? (filteredRes.results || []) : allAssets;
}

function startLiveSync() {
  stopLiveSync();
  livePollTimer = setInterval(async () => {
    if (!currentUser) return;
    const modalOpen = $('#genericModal')?.classList.contains('show');
    const activeTag = document.activeElement?.tagName;
    if (modalOpen || ['INPUT', 'TEXTAREA', 'SELECT'].includes(activeTag)) return;
    try {
      await refreshData();
      renderPage();
    } catch (_) {
      // Silently ignore transient polling blips
    }
  }, 5000);
}

function stopLiveSync() {
  if (livePollTimer) {
    clearInterval(livePollTimer);
    livePollTimer = null;
  }
}

function applyUserSession(user) {
  currentUser = user;
  const scopeTag = user.assigned_lab ? ` · ${user.assigned_lab.lab_code}` : '';
  const label = (ROLE_LABELS[user.role] || user.role) + scopeTag;
  const init = initials(user.full_name || user.username);

  $('#sideUser').textContent = user.full_name || user.username;
  $('#topUser').textContent = user.full_name || user.username;
  $('#sideRole').textContent = label;
  $('#topRole').textContent = label;
  $('#sideAvatar').textContent = init;
  $('#topAvatar').textContent = init;

  $('#loginView').classList.add('d-none');
  $('#appView').classList.remove('d-none');
  startLiveSync();
}

function renderNav() {
  const items = MENUS[currentUser?.role] || MENUS.STUDENT;
  $('#sideNav').innerHTML = items
    .map(([id, ico, label]) => `
      <a class="nav-link ${currentPage === id ? 'active' : ''}" href="#" data-page="${id}">
        <i class="bi bi-${ico}"></i>${label}
      </a>`)
    .join('');

  $$('[data-page]').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      go(a.dataset.page);
    });
  });
}

async function go(page, skipRefresh = false) {
  currentPage = page;
  const titles = {
    dashboard: 'Dashboard',
    browse: 'Universal Cross-Lab Search',
    labs: 'Labs Overview',
    requests: 'My Requests',
    reservations: 'Machine Time-Slot Booking',
    approvals: 'Pending Approvals',
    issue: 'Issue / Return Desk',
    manage: 'Equipment & Any-Format Import',
    reports: 'PR Templates & Reports',
    notifications: 'Live Notifications',
    details: 'Equipment Details',
  };
  $('#crumb').textContent = titles[page] || 'Dashboard';
  renderNav();
  $('#sidebar')?.classList.remove('open');

  if (!skipRefresh && page !== 'details') {
    try {
      await refreshData();
    } catch (e) {
      toast(e.message, true);
    }
  }
  renderPage();
}

function renderPage() {
  const pages = {
    dashboard,
    browse,
    labs,
    requests,
    reservations,
    approvals,
    issue,
    manage,
    reports,
    notifications,
    details,
  };
  $('#pageContent').innerHTML = (pages[currentPage] || dashboard)();
}

// ==============================================================================
// PAGE 1: DASHBOARD
// ==============================================================================

function quick(icon, title, desc, page) {
  return `
    <div class="col-md-6">
      <a class="quick-card" href="#" onclick="go('${page}');return false">
        <span class="quick-icon"><i class="bi bi-${icon}"></i></span>
        <span><strong>${title}</strong><small>${desc}</small></span>
        <i class="bi bi-arrow-up-right ms-auto text-secondary"></i>
      </a>
    </div>`;
}

function dashboard() {
  const s = dashData.stats || {};
  const role = currentUser.role;
  const isStaff = role === 'ASSISTANT' || role === 'ADMIN';

  const scopeBanner = role === 'ASSISTANT' && currentUser.assigned_lab
    ? `<div class="alert alert-primary py-2 small mb-3">
         <i class="bi bi-shield-lock me-2"></i>
         <strong>Strict Lab Scope Active:</strong> You are authorized exclusively for
         <strong>${esc(currentUser.assigned_lab.lab_name)} (${esc(currentUser.assigned_lab.lab_code)})</strong>.
       </div>`
    : (role === 'ASSISTANT'
      ? `<div class="alert alert-warning py-2 small mb-3">
           <i class="bi bi-exclamation-triangle me-2"></i>
           No laboratory is assigned to your account yet. Contact an Admin to assign your lab.
         </div>`
      : '');

  const cards = isStaff
    ? [
        stat('Scoped assets', s.total_assets, 'boxes', 'blue'),
        stat('Available items', s.available_assets, 'check-circle', 'green'),
        stat('Pending approvals', s.pending_requests_count, 'hourglass-split', 'amber'),
        stat('In maintenance', s.maintenance_assets, 'wrench-adjustable', 'red'),
      ]
    : [
        stat('Available equipment', s.available_assets, 'box-seam', 'green'),
        stat('My pending requests', s.pending_requests_count, 'hourglass-split', 'blue'),
        stat('Active machine slots', s.active_reservations_count, 'calendar-check', 'green'),
        stat('Active loans', s.active_loans_count, 'arrow-left-right', 'amber'),
      ];

  return `
    ${pageHeading(
      'Dashboard',
      `Welcome, ${esc(currentUser.full_name)} (${ROLE_LABELS[role] || role}). Live multi-role sync active.`,
      `<button class="btn btn-outline-primary btn-sm" onclick="openQrLookup()">
         <i class="bi bi-qr-code-scan me-1"></i>QR Tag Lookup
       </button>`
    )}
    ${scopeBanner}
    <div class="row g-3 mb-4">${cards.map(c => `<div class="col-6 col-xl-3">${c}</div>`).join('')}</div>
    <div class="row g-3">
      <div class="col-xl-7">
        <div class="panel h-100">
          <div class="panel-head">
            <h2 class="section-title">Quick access</h2>
            <span class="small text-secondary">${(dashData.labs || []).length} active labs</span>
          </div>
          <div class="row g-3">
            ${quick('search', 'Universal search', 'Search by Unique ID, name or lab', 'browse')}
            ${quick('calendar-check', 'Machine time-slots', 'Strict 1–2 hr advance booking', 'reservations')}
            ${isStaff
              ? quick('clipboard-check', 'Approve requests', 'Verify borrow reasons & issue', 'approvals') +
                quick('file-earmark-spreadsheet', 'PR template exports', 'Asset, Consumable & Service Excel', 'reports')
              : quick('file-earmark-text', 'My requests', 'Track approvals & due dates', 'requests')}
            ${quick('building', 'Labs overview', 'View, add or edit laboratories', 'labs')}
          </div>
        </div>
      </div>
      <div class="col-xl-5">
        <div class="panel h-100">
          <div class="panel-head">
            <h2 class="section-title">Live alerts &amp; notifications</h2>
            <button class="btn btn-sm btn-light" onclick="go('notifications')">View all</button>
          </div>
          ${(dashData.notifications || []).slice(0, 4).map(notificationRow).join('') || '<p class="text-secondary small mb-0">No alerts right now.</p>'}
        </div>
      </div>
    </div>
    <div class="panel mt-3">
      <div class="panel-head">
        <h2 class="section-title">Live campus inventory</h2>
        <button class="btn btn-sm btn-light" onclick="go('browse')">Browse all (${allAssets.length})</button>
      </div>
      ${equipmentTable(allAssets.slice(0, 8))}
    </div>`;
}

// ==============================================================================
// PAGE 2: LABS OVERVIEW (ADD / EDIT / DELETE LABS)
// ==============================================================================

function labs() {
  const canManage = Boolean(currentUser.permissions?.can_manage_labs);
  const canDel = Boolean(currentUser.permissions?.can_delete_labs);
  const assignedId = currentUser.role === 'ASSISTANT' && currentUser.assigned_lab ? currentUser.assigned_lab.lab_id : null;

  return `
    ${pageHeading(
      'Labs Overview',
      'All 6 campus laboratory types synced live from Supabase.',
      canManage && currentUser.role !== 'ASSISTANT'
        ? '<button class="btn btn-primary" onclick="openLabModal()"><i class="bi bi-plus-lg me-1"></i>Add Laboratory</button>'
        : ''
    )}
    <div class="row g-3">
      ${(dashData.labs || []).map(l => {
        const canEditThis = canManage && (!assignedId || assignedId === l.lab_id);
        return `
          <div class="col-md-6 col-xl-4">
            <div class="lab-card d-flex flex-column justify-content-between">
              <div>
                <div class="d-flex justify-content-between align-items-start mb-2">
                  <div class="lab-icon mb-0"><i class="bi bi-building"></i></div>
                  <span class="badge text-bg-light border">${esc((l.lab_type || 'GENERAL').replaceAll('_', ' '))}</span>
                </div>
                <h3 class="mt-3">${esc(l.lab_name)}</h3>
                <p class="mb-2">Prefix: <strong>INV-${esc(l.lab_code)}-</strong> · ${l.asset_count} tracked assets</p>
                ${l.location ? `<div class="small text-secondary"><i class="bi bi-geo-alt me-1"></i>${esc(l.location)}</div>` : ''}
                ${l.instructor_name ? `<div class="small text-secondary"><i class="bi bi-person-badge me-1"></i>${esc(l.instructor_name)}</div>` : ''}
              </div>
              <div class="d-flex justify-content-between align-items-center mt-3 pt-2 border-top">
                <a href="#" class="small fw-bold text-primary text-decoration-none" onclick="openLabInBrowse('${l.lab_id}');return false">
                  View equipment <i class="bi bi-arrow-right"></i>
                </a>
                <div class="d-flex gap-1">
                  ${canEditThis ? `<button class="btn btn-sm btn-light" title="Edit lab" onclick="openLabModal('${l.lab_id}')"><i class="bi bi-pencil"></i></button>` : ''}
                  ${canDel ? `<button class="btn btn-sm btn-light text-danger" title="Delete lab" onclick="deleteLab('${l.lab_id}')"><i class="bi bi-trash"></i></button>` : ''}
                </div>
              </div>
            </div>
          </div>`;
      }).join('') || '<div class="col-12"><div class="empty-state">No labs found. Click Add Laboratory to create one.</div></div>'}
    </div>`;
}

function openLabModal(labId = '') {
  const l = (dashData.labs || []).find(x => x.lab_id === labId) || {
    lab_name: '',
    lab_code: '',
    lab_type: 'COMPUTER_SCIENCE',
    location: '',
    instructor_name: '',
  };

  showModal(labId ? `Edit Laboratory: ${esc(l.lab_name)}` : 'Add New Laboratory', `
    <form id="labForm">
      <div class="mb-2">
        <label class="form-label">Laboratory Name</label>
        <input name="lab_name" class="form-control" value="${esc(l.lab_name)}" placeholder="e.g. Physics Optics Lab" required>
      </div>
      <div class="row g-2 mb-2">
        <div class="col-md-5">
          <label class="form-label">Lab Code Prefix</label>
          <input name="lab_code" class="form-control" value="${esc(l.lab_code)}" placeholder="e.g. PHYS01" required>
        </div>
        <div class="col-md-7">
          <label class="form-label">Lab Type (6 Categories)</label>
          <select name="lab_type" class="form-select">
            ${LAB_TYPE_OPTIONS.map(([val, label]) => `<option value="${val}" ${l.lab_type === val ? 'selected' : ''}>${label}</option>`).join('')}
          </select>
        </div>
      </div>
      <div class="row g-2 mb-3">
        <div class="col-md-6">
          <label class="form-label">Room / Location</label>
          <input name="location" class="form-control" value="${esc(l.location)}" placeholder="e.g. UB-44">
        </div>
        <div class="col-md-6">
          <label class="form-label">Lab Instructor Name</label>
          <input name="instructor_name" class="form-control" value="${esc(l.instructor_name)}" placeholder="e.g. Vinod Kalagi">
        </div>
      </div>
      <button class="btn btn-primary w-100" type="submit">${labId ? 'Save Changes' : 'Create Laboratory in Supabase'}</button>
    </form>`, () => {
    $('#labForm').onsubmit = async ev => {
      ev.preventDefault();
      const btn = ev.target.querySelector('button[type="submit"]');
      setBtnBusy(btn, true, 'Saving...');
      const fd = new FormData(ev.target);
      try {
        const res = await api('/api/labs/save/', {
          method: 'POST',
          body: {
            lab_id: labId || null,
            lab_name: fd.get('lab_name'),
            lab_code: fd.get('lab_code'),
            lab_type: fd.get('lab_type'),
            location: fd.get('location'),
            instructor_name: fd.get('instructor_name'),
          },
        });
        hideModal();
        toast(res.message);
        await go('labs');
      } catch (err) {
        toast(err.message, true);
        setBtnBusy(btn, false);
      }
    };
  });
}

async function deleteLab(labId) {
  const lab = (dashData.labs || []).find(l => l.lab_id === labId);
  const label = lab ? lab.lab_name : 'this laboratory';
  if (!confirm(`Permanently delete "${label}" and all assets inside it?`)) return;
  try {
    const r = await api(`/api/labs/${labId}/delete/`, { method: 'DELETE' });
    toast(r.message);
    await go('labs');
  } catch (e) {
    toast(e.message, true);
  }
}

async function openLabInBrowse(labId) {
  searchFilters = { q: '', lab: labId, status: '' };
  await go('browse');
}

// ==============================================================================
// PAGE 3: UNIVERSAL CROSS-LAB SEARCH & EQUIPMENT DETAILS
// ==============================================================================

function equipmentTable(items) {
  return `
    <div class="table-responsive">
      <table class="table">
        <thead>
          <tr><th>Unique ID</th><th>Equipment</th><th>Lab</th><th>Stock</th><th>Condition</th><th>Status</th><th></th></tr>
        </thead>
        <tbody>
          ${items.map(e => `
            <tr>
              <td><code>${esc(e.asset_tag)}</code></td>
              <td class="fw-bold">${esc(e.name)}<div class="small text-secondary">${esc(e.category)}</div></td>
              <td>${esc(e.lab.lab_name)}</td>
              <td><strong>${e.stock_quantity}</strong> / ${e.total_quantity}${e.is_low_stock ? ' <span class="badge text-bg-warning">Low</span>' : ''}</td>
              <td>${statusBadge(e.condition)}</td>
              <td>${statusBadge(e.status)}</td>
              <td><button class="btn btn-sm btn-light" onclick="openDetails('${e.asset_id}')">View</button></td>
            </tr>`).join('') || '<tr><td colspan="7" class="text-center text-secondary py-4">No equipment found.</td></tr>'}
        </tbody>
      </table>
    </div>`;
}

function browse() {
  return `
    ${pageHeading('Universal Cross-Lab Search', 'Search by Unique ID (INV-...), equipment name, serial number, or filter by laboratory.')}
    <div class="panel">
      <div class="filter-bar">
        <input id="equipmentSearch" class="form-control" style="max-width:340px" value="${esc(searchFilters.q)}" placeholder="Search Unique ID (INV-...), name, or lab..." oninput="scheduleSearch()">
        <select id="labFilter" class="form-select" onchange="runLiveSearch()">
          <option value="">All laboratories (${(dashData.labs || []).length})</option>
          ${(dashData.labs || []).map(l => `<option value="${l.lab_id}" ${searchFilters.lab === l.lab_id ? 'selected' : ''}>${esc(l.lab_name)}</option>`).join('')}
        </select>
        <select id="statusFilter" class="form-select" onchange="runLiveSearch()">
          <option value="">All statuses</option>
          ${['AVAILABLE', 'ISSUED', 'RESERVED', 'MAINTENANCE'].map(st =>
            `<option value="${st}" ${searchFilters.status === st ? 'selected' : ''}>${st}</option>`
          ).join('')}
        </select>
        ${(searchFilters.q || searchFilters.lab || searchFilters.status)
          ? '<button class="btn btn-sm btn-light" onclick="clearSearchFilters()">Reset</button>'
          : ''}
      </div>
      <div class="small text-secondary mb-3" id="searchCount">Showing ${searchResults.length} matching records across all labs</div>
      <div class="row g-3" id="equipmentGrid">${equipmentCards(searchResults)}</div>
    </div>`;
}

function clearSearchFilters() {
  searchFilters = { q: '', lab: '', status: '' };
  go('browse');
}

function scheduleSearch() {
  searchFilters.q = $('#equipmentSearch')?.value || '';
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runLiveSearch, 220);
}

async function runLiveSearch() {
  searchFilters.q = $('#equipmentSearch')?.value || '';
  searchFilters.lab = $('#labFilter')?.value || '';
  searchFilters.status = $('#statusFilter')?.value || '';

  const q = encodeURIComponent(searchFilters.q);
  const lab = encodeURIComponent(searchFilters.lab);
  const st = encodeURIComponent(searchFilters.status);

  try {
    const res = await api(`/api/search/?q=${q}&lab=${lab}&status=${st}`);
    searchResults = res.results || [];
    const cnt = $('#searchCount');
    if (cnt) cnt.textContent = `Showing ${searchResults.length} matching records across all labs`;
    const grid = $('#equipmentGrid');
    if (grid) grid.innerHTML = equipmentCards(searchResults);
  } catch (e) {
    toast(e.message, true);
  }
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
          <div class="small text-secondary mb-3">
            Stock: <strong>${e.stock_quantity}/${e.total_quantity}</strong> · ${esc(e.condition)} · ${esc(e.category)}
          </div>
          <div class="equipment-meta">
            <span class="badge text-bg-light border">${esc(e.asset_type)}</span>
            <button class="btn btn-sm btn-outline-primary" onclick="openDetails('${e.asset_id}')">Details &amp; Actions</button>
          </div>
        </div>
      </div>
    </div>`).join('') || '<div class="col-12"><div class="empty-state">No matching equipment found across labs.</div></div>';
}

function openDetails(assetId) {
  selectedAssetId = assetId;
  go('details', true);
}

function details() {
  const e = findAsset(selectedAssetId) || allAssets[0];
  if (!e) return '<div class="empty-state">Select an asset first.</div>';

  const extraRows = Object.entries(e.extra_metadata || {})
    .map(([k, v]) => `<div class="col-6"><small class="text-secondary">${esc(k)}</small><div class="fw-semibold">${esc(v)}</div></div>`)
    .join('');

  return `
    ${pageHeading(
      'Equipment Details',
      'Live Supabase state, auto-generated QR tag, and specifications.',
      '<button class="btn btn-light" onclick="go(\'browse\', true)"><i class="bi bi-arrow-left me-1"></i>Back</button>'
    )}
    <div class="row g-3">
      <div class="col-lg-4">
        <div class="panel text-center h-100">
          ${e.qr_code_url
            ? `<img src="${e.qr_code_url}" alt="QR Code" class="img-fluid rounded border p-2 mb-2" style="max-width:210px">`
            : '<div class="detail-icon mb-2"><i class="bi bi-qr-code"></i></div>'}
          <div><code>${esc(e.asset_tag)}</code></div>
          <small class="text-secondary">Unique Lab QR Identifier</small>
        </div>
      </div>
      <div class="col-lg-8">
        <div class="panel h-100">
          <div class="d-flex justify-content-between align-items-start">
            <div>
              <span class="badge text-bg-light border mb-1">${esc(e.lab.lab_name)}</span>
              <h2 class="section-title fs-4">${esc(e.name)}</h2>
            </div>
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
            ${e.asset_type !== 'TIME_SLOTTED' ? `
              <button class="btn btn-primary" onclick="openBorrowModal('${e.asset_id}')" ${e.status !== 'AVAILABLE' || e.stock_quantity < 1 ? 'disabled' : ''}>
                <i class="bi bi-hand-index-thumb me-1"></i>Request to Borrow
              </button>` : ''}
            ${e.asset_type === 'TIME_SLOTTED' ? `
              <button class="btn btn-primary" onclick="openReservationModal('${e.asset_id}')" ${e.status === 'MAINTENANCE' || e.status === 'LOST' ? 'disabled' : ''}>
                <i class="bi bi-calendar-plus me-1"></i>Book Machine Slot (1–2 hr advance)
              </button>` : ''}
            <button class="btn btn-outline-danger" onclick="openReportFaultModal('${e.asset_id}')">
              <i class="bi bi-exclamation-octagon me-1"></i>Report Fault
            </button>
          </div>
        </div>
      </div>
    </div>`;
}

// ==============================================================================
// PAGE 4: BORROW REQUESTS, APPROVALS & ISSUE/RETURN DESK
// ==============================================================================

function openBorrowModal(assetId) {
  const e = findAsset(assetId);
  if (!e) return;
  showModal(`Borrow: ${esc(e.name)}`, `
    <form id="borrowForm">
      <div class="mb-2">
        <label class="form-label">Lab &amp; Unique ID</label>
        <input class="form-control" value="${esc(e.lab.lab_name)} (${esc(e.asset_tag)})" disabled>
      </div>
      <div class="mb-2">
        <label class="form-label">Quantity (Max ${e.stock_quantity})</label>
        <input name="quantity" type="number" min="1" max="${e.stock_quantity}" value="1" class="form-control" required>
      </div>
      <div class="mb-3">
        <label class="form-label">Reason for Borrowing <span class="text-danger">*</span></label>
        <textarea name="borrow_reason" class="form-control" rows="3" minlength="5" placeholder="Mandatory: state lab experiment or project purpose" required></textarea>
      </div>
      <button class="btn btn-primary w-100" type="submit">Submit Borrow Request</button>
    </form>`, () => {
    $('#borrowForm').onsubmit = async ev => {
      ev.preventDefault();
      const btn = ev.target.querySelector('button[type="submit"]');
      setBtnBusy(btn, true, 'Submitting...');
      const fd = new FormData(ev.target);
      try {
        await api('/api/transactions/request/', {
          method: 'POST',
          body: {
            asset_id: assetId,
            quantity: Number(fd.get('quantity')),
            borrow_reason: fd.get('borrow_reason'),
          },
        });
        hideModal();
        toast('Borrow request submitted!');
        await go('requests');
      } catch (err) {
        toast(err.message, true);
        setBtnBusy(btn, false);
      }
    };
  });
}

function requests() {
  return `
    ${pageHeading(
      'My Requests',
      'Track submitted borrowing requests, reasons, approvals, and due dates.',
      '<button class="btn btn-primary" onclick="go(\'browse\')"><i class="bi bi-plus-lg me-1"></i>New request</button>'
    )}
    <div class="panel">${requestTable(dashData.all_requests || [])}</div>`;
}

function requestTable(items) {
  const isStaff = ['ASSISTANT', 'ADMIN'].includes(currentUser?.role);
  return `
    <div class="table-responsive">
      <table class="table">
        <thead>
          <tr><th>Unique ID</th><th>Borrower</th><th>Equipment &amp; Lab</th><th>Mandatory Reason</th><th>Qty</th><th>Due Date</th><th>Status</th><th>Action</th></tr>
        </thead>
        <tbody>
          ${items.map(r => {
            const canCancel = r.request_status === 'PENDING' || (isStaff && r.request_status === 'APPROVED');
            const borrowerCell = isStaff
              ? `<a href="#" class="fw-semibold text-decoration-none" onclick="openProfileModal('${esc(r.borrower_username)}');return false">${esc(r.borrower)}</a>`
              : esc(r.borrower);
            return `
              <tr>
                <td><code>${esc(r.asset_tag)}</code></td>
                <td>${borrowerCell}</td>
                <td><strong>${esc(r.asset_name)}</strong><div class="small text-secondary">${esc(r.lab)}</div></td>
                <td class="small">${esc(r.borrow_reason || '—')}</td>
                <td>${r.quantity}</td>
                <td class="small">${fmtDate(r.due_date)}</td>
                <td>${statusBadge(r.request_status)}</td>
                <td>
                  ${canCancel
                    ? `<button class="btn btn-sm btn-outline-danger" onclick="cancelTx('${r.transaction_id}')">Cancel</button>`
                    : '—'}
                </td>
              </tr>`;
          }).join('') || '<tr><td colspan="8" class="text-center py-4 text-secondary">No requests yet.</td></tr>'}
        </tbody>
      </table>
    </div>`;
}

async function cancelTx(id) {
  if (!confirm('Cancel this equipment request?')) return;
  try {
    const r = await api(`/api/transactions/${id}/cancel/`, { method: 'POST' });
    toast(r.message);
    await go(currentPage);
  } catch (e) {
    toast(e.message, true);
  }
}

function approvals() {
  const pending = dashData.pending_requests || [];
  return `
    ${pageHeading('Pending Approvals', 'Lab Assistants can only approve requests in their assigned lab with a valid borrow reason.')}
    <div class="panel mb-3">
      <div class="panel-head">
        <h2 class="section-title">Awaiting verification</h2>
        <span class="status status-pending">${pending.length} pending</span>
      </div>
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr><th>Unique ID</th><th>Borrower</th><th>Equipment &amp; Lab</th><th>Qty</th><th>Stated Borrow Reason</th><th>Action</th></tr>
          </thead>
          <tbody>
            ${pending.map(r => `
              <tr>
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
          </tbody>
        </table>
      </div>
    </div>
    <div class="panel">
      <h2 class="section-title mb-3">Lab transaction audit trail</h2>
      ${requestTable(dashData.all_requests || [])}
    </div>`;
}

async function approveTx(id) {
  try {
    const r = await api(`/api/transactions/${id}/approve/`, { method: 'POST', body: { loan_days: 7 } });
    toast(r.message);
    await go('approvals');
  } catch (e) {
    toast(e.message, true);
  }
}

async function rejectTx(id) {
  const note = prompt('Optional rejection note:') || '';
  try {
    const r = await api(`/api/transactions/${id}/reject/`, { method: 'POST', body: { rejection_note: note } });
    toast(r.message);
    await go('approvals');
  } catch (e) {
    toast(e.message, true);
  }
}

function issue() {
  const active = dashData.active_loans || [];
  const tickets = dashData.maintenance_tickets || [];
  return `
    ${pageHeading('Issue / Return & Maintenance Desk', 'Returns marked DAMAGED or UNUSABLE autonomously route to Maintenance and open repair tickets.')}
    <div class="panel mb-3">
      <div class="panel-head"><h2 class="section-title">Active issued equipment (${active.length})</h2></div>
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr><th>Unique ID</th><th>Borrower</th><th>Equipment &amp; Lab</th><th>Qty</th><th>Due Date</th><th>Action</th></tr>
          </thead>
          <tbody>
            ${active.map(r => `
              <tr>
                <td><code>${esc(r.asset_tag)}</code></td>
                <td>${esc(r.borrower)}</td>
                <td><strong>${esc(r.asset_name)}</strong><div class="small text-secondary">${esc(r.lab)}</div></td>
                <td>${r.quantity}</td>
                <td class="small">${fmtDate(r.due_date)}</td>
                <td><button class="btn btn-sm btn-success" onclick="openReturnModal('${r.transaction_id}')">Log Return</button></td>
              </tr>`).join('') || '<tr><td colspan="6" class="text-center py-4 text-secondary">No active loans right now.</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>
    <div class="panel">
      <div class="panel-head"><h2 class="section-title">Maintenance tickets (${tickets.length})</h2></div>
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr><th>Unique ID</th><th>Asset</th><th>Fault Description</th><th>Status</th><th>Action</th></tr>
          </thead>
          <tbody>
            ${tickets.map(m => `
              <tr>
                <td><code>${esc(m.asset_tag)}</code></td>
                <td><strong>${esc(m.asset_name)}</strong><div class="small text-secondary">${esc(m.lab)}</div></td>
                <td class="small">${esc(m.description)}</td>
                <td>${statusBadge(m.resolution_status)}</td>
                <td>
                  ${m.resolution_status !== 'RESOLVED'
                    ? `<button class="btn btn-sm btn-outline-success" onclick="resolveTicket('${m.ticket_id}')">Mark Resolved</button>`
                    : 'Done'}
                </td>
              </tr>`).join('') || '<tr><td colspan="5" class="text-center py-4 text-secondary">No maintenance tickets.</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>`;
}

function openReturnModal(txId) {
  const tx = (dashData.active_loans || []).find(t => t.transaction_id === txId);
  const label = tx ? tx.asset_name : 'Equipment';
  showModal(`Return Check: ${esc(label)}`, `
    <form id="returnForm">
      <div class="mb-2">
        <label class="form-label">Returned Condition</label>
        <select name="return_condition" class="form-select">
          <option value="GOOD">Good (Restores to Available)</option>
          <option value="MINOR_DAMAGE">Minor Damage (Restores to Available)</option>
          <option value="DAMAGED">Damaged (Auto-routes to Maintenance)</option>
          <option value="UNUSABLE">Unusable (Auto-routes to Maintenance)</option>
        </select>
      </div>
      <div class="mb-3">
        <label class="form-label">Inspection Notes</label>
        <textarea name="notes" class="form-control" rows="2" placeholder="Optional damage notes"></textarea>
      </div>
      <button class="btn btn-success w-100" type="submit">Confirm Return</button>
    </form>`, () => {
    $('#returnForm').onsubmit = async ev => {
      ev.preventDefault();
      const btn = ev.target.querySelector('button[type="submit"]');
      setBtnBusy(btn, true, 'Processing...');
      const fd = new FormData(ev.target);
      try {
        const r = await api(`/api/transactions/${txId}/return/`, {
          method: 'POST',
          body: {
            return_condition: fd.get('return_condition'),
            notes: fd.get('notes'),
          },
        });
        hideModal();
        toast(r.message);
        await go('issue');
      } catch (err) {
        toast(err.message, true);
        setBtnBusy(btn, false);
      }
    };
  });
}

async function resolveTicket(ticketId) {
  try {
    const r = await api(`/api/maintenance/${ticketId}/resolve/`, { method: 'POST' });
    toast(r.message);
    await go('issue');
  } catch (e) {
    toast(e.message, true);
  }
}

// ==============================================================================
// PAGE 5: STRICT TIME-SLOT MACHINE RESERVATIONS (1–2 HR ADVANCE + 30-MIN EXPIRY)
// ==============================================================================

function reservations() {
  const isStaff = ['ASSISTANT', 'ADMIN'].includes(currentUser.role);
  return `
    ${pageHeading(
      'Machine Time-Slot Reservations',
      'Strict window: book 1 to 2 hours ahead only. Uncollected slots auto-expire 30 minutes after start.',
      '<button class="btn btn-primary" onclick="openReservationModal()"><i class="bi bi-plus-lg me-1"></i>New Slot Booking</button>'
    )}
    <div class="panel">
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr><th>Unique ID</th><th>Machine &amp; Lab</th><th>Booked By</th><th>Start Time</th><th>End Time</th><th>Status</th><th>Action</th></tr>
          </thead>
          <tbody>
            ${(dashData.reservations || []).map(r => `
              <tr>
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
          </tbody>
        </table>
      </div>
    </div>`;
}

function toLocalIsoInput(date) {
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function openReservationModal(preselectedAssetId = '') {
  if (!allAssets.length) {
    toast('Add or import equipment first before booking a slot.', true);
    return;
  }
  const startDef = new Date(Date.now() + 75 * 60000);
  const endDef = new Date(Date.now() + 135 * 60000);
  const machines = allAssets.filter(a => a.asset_type === 'TIME_SLOTTED');
  if (!machines.length) {
    toast('No time-slotted machines (e.g., 3D Printer, Laser Cutter) are available to book.', true);
    return;
  }

  showModal('Book Machine Slot (Strict 1–2 Hr Advance)', `
    <div class="alert alert-info py-2 small">
      Slots must start between <strong>60 and 120 minutes</strong> from now and expire automatically after 30 minutes if unchecked.
    </div>
    <form id="resForm">
      <div class="mb-2">
        <label class="form-label">Equipment / Machine</label>
        <select name="asset_id" class="form-select" required>
          ${machines.map(a => `<option value="${a.asset_id}" ${a.asset_id === preselectedAssetId ? 'selected' : ''}>[${esc(a.asset_tag)}] ${esc(a.name)} — ${esc(a.lab.lab_name)}</option>`).join('')}
          ${allAssets.map(a => `<option value="${a.asset_id}" ${a.asset_id === preselectedAssetId ? 'selected' : ''}>[${esc(a.asset_tag)}] ${esc(a.name)} — ${esc(a.lab.lab_name)}</option>`).join('')}
        </select>
      </div>
      <div class="row g-2 mb-2">
        <div class="col-md-6">
          <label class="form-label">Start Time</label>
          <input name="start_time" type="datetime-local" class="form-control" value="${toLocalIsoInput(startDef)}" required>
        </div>
        <div class="col-md-6">
          <label class="form-label">End Time</label>
          <input name="end_time" type="datetime-local" class="form-control" value="${toLocalIsoInput(endDef)}" required>
        </div>
      </div>
      <div class="mb-3">
        <label class="form-label">Purpose</label>
        <input name="purpose" class="form-control" placeholder="e.g. Lab project fabrication" required>
      </div>
      <button class="btn btn-primary w-100" type="submit">Confirm Strict Slot</button>
    </form>`, () => {
    $('#resForm').onsubmit = async ev => {
      ev.preventDefault();
      const btn = ev.target.querySelector('button[type="submit"]');
      setBtnBusy(btn, true, 'Booking...');
      const fd = new FormData(ev.target);
      try {
        const res = await api('/api/reservations/book/', {
          method: 'POST',
          body: {
            asset_id: fd.get('asset_id'),
            start_time: new Date(fd.get('start_time')).toISOString(),
            end_time: new Date(fd.get('end_time')).toISOString(),
            purpose: fd.get('purpose'),
          },
        });
        hideModal();
        toast(res.message);
        await go('reservations');
      } catch (err) {
        toast(err.message, true);
        setBtnBusy(btn, false);
      }
    };
  });
}

async function checkInRes(id) {
  try {
    const r = await api(`/api/reservations/${id}/checkin/`, { method: 'POST' });
    toast(r.message);
    await go('reservations');
  } catch (e) {
    toast(e.message, true);
  }
}

async function cancelRes(id) {
  try {
    const r = await api(`/api/reservations/${id}/cancel/`, { method: 'POST' });
    toast(r.message);
    await go('reservations');
  } catch (e) {
    toast(e.message, true);
  }
}

// ==============================================================================
// PAGE 6: MANAGE EQUIPMENT & UNIVERSAL ANY-FORMAT INGESTION
// ==============================================================================

function manage() {
  const assignedId = currentUser.role === 'ASSISTANT' && currentUser.assigned_lab ? currentUser.assigned_lab.lab_id : null;
  const rows = assignedId ? allAssets.filter(e => e.lab.lab_id === assignedId) : allAssets;

  return `
    ${pageHeading(
      'Manage Equipment & Data Import',
      assignedId
        ? `Scoped strictly to ${esc(currentUser.assigned_lab.lab_name)}.`
        : 'Add, edit, delete, or bulk-import equipment datasets across labs.',
      `<button class="btn btn-outline-primary" onclick="openUploadModal()"><i class="bi bi-cloud-upload me-1"></i>Import Dataset</button>
       <button class="btn btn-primary" onclick="openAssetModal()"><i class="bi bi-plus-lg me-1"></i>Add Equipment</button>`
    )}
    <div class="panel">
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr><th>Unique ID</th><th>Equipment</th><th>Lab</th><th>Type</th><th>Qty</th><th>Condition</th><th>Status</th><th>Actions</th></tr>
          </thead>
          <tbody>
            ${rows.map(e => `
              <tr>
                <td><code>${esc(e.asset_tag)}</code></td>
                <td class="fw-bold">${esc(e.name)}</td>
                <td>${esc(e.lab.lab_name)}</td>
                <td class="small">${esc(e.asset_type)}</td>
                <td>${e.stock_quantity}</td>
                <td>${statusBadge(e.condition)}</td>
                <td>${statusBadge(e.status)}</td>
                <td>
                  <button class="btn btn-sm btn-light" onclick="openAssetModal('${e.asset_id}')"><i class="bi bi-pencil"></i></button>
                  <button class="btn btn-sm btn-light text-danger" onclick="deleteAsset('${e.asset_id}')"><i class="bi bi-trash"></i></button>
                </td>
              </tr>`).join('') || '<tr><td colspan="8" class="text-center py-4 text-secondary">No equipment in scope yet. Add or import items above.</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>`;
}

function openUploadModal() {
  const assignedNote = currentUser.role === 'ASSISTANT' && currentUser.assigned_lab
    ? `<div class="alert alert-primary py-2 small">All imported items will automatically be assigned to <strong>${esc(currentUser.assigned_lab.lab_name)}</strong>.</div>`
    : '';

  showModal('Universal Data Ingestion (.xlsx, .csv, .pdf, .docx, .json)', `
    ${assignedNote}
    <form id="uploadForm">
      <div class="mb-2">
        <label class="form-label">Select Dataset File</label>
        <input name="file" type="file" class="form-control" required>
      </div>
      ${currentUser.role !== 'ASSISTANT'
        ? `<div class="mb-3">
             <label class="form-label">Target Lab Override (Optional)</label>
             <input name="lab_name" class="form-control" placeholder="Leave blank to auto-detect">
           </div>`
        : ''}
      <button class="btn btn-primary w-100" type="submit">Analyze &amp; Ingest into Supabase</button>
    </form>`, () => {
    $('#uploadForm').onsubmit = async ev => {
      ev.preventDefault();
      const btn = ev.target.querySelector('button[type="submit"]');
      setBtnBusy(btn, true, 'Ingesting...');
      const fd = new FormData(ev.target);
      try {
        const res = await api('/api/inventory/ingest/', { method: 'POST', body: fd });
        hideModal();
        toast(`Ingested ${res.imported} items into ${res.lab}!`);
        await go('manage');
      } catch (err) {
        toast(err.message, true);
        setBtnBusy(btn, false);
      }
    };
  });
}

function openAssetModal(assetId = '') {
  const assigned = currentUser.role === 'ASSISTANT' ? currentUser.assigned_lab : null;
  const availLabs = assigned
    ? (dashData.labs || []).filter(l => l.lab_id === assigned.lab_id)
    : (dashData.labs || []);

  if (!availLabs.length) {
    toast('Create a laboratory first in Labs Overview before adding equipment.', true);
    return;
  }

  const e = findAsset(assetId) || {
    name: '',
    category: 'Lab Equipment',
    asset_type: 'SHARED',
    stock_quantity: 1,
    condition: 'GOOD',
    lab: { lab_id: availLabs[0].lab_id },
    extra_metadata: {},
  };

  showModal(assetId ? `Edit ${esc(e.asset_tag)}` : 'Add Equipment', `
    <form id="assetForm">
      <div class="mb-2">
        <label class="form-label">Equipment Name</label>
        <input name="name" class="form-control" value="${esc(e.name)}" required>
      </div>
      <div class="mb-2">
        <label class="form-label">Serial Number (Optional — must be unique)</label>
        <input name="serial_number" class="form-control" value="${esc(e.serial_number || '')}" placeholder="Leave blank to auto-assign asset tag">
      </div>
      <div class="row g-2 mb-2">
        <div class="col-md-6">
          <label class="form-label">Category</label>
          <input name="category" class="form-control" value="${esc(e.category)}" required>
        </div>
        <div class="col-md-6">
          <label class="form-label">Stock Quantity</label>
          <input name="stock_quantity" type="number" min="0" class="form-control" value="${e.stock_quantity}" required>
        </div>
      </div>
      <div class="row g-2 mb-2">
        <div class="col-md-6">
          <label class="form-label">Asset Type</label>
          <select name="asset_type" class="form-select">
            <option value="SHARED" ${e.asset_type === 'SHARED' ? 'selected' : ''}>Shared Equipment</option>
            <option value="CONSUMABLE" ${e.asset_type === 'CONSUMABLE' ? 'selected' : ''}>Consumable</option>
            <option value="TIME_SLOTTED" ${e.asset_type === 'TIME_SLOTTED' ? 'selected' : ''}>Time-Slotted Machine</option>
          </select>
        </div>
        <div class="col-md-6">
          <label class="form-label">Condition</label>
          <select name="condition" class="form-select">
            ${['GOOD', 'MINOR_DAMAGE', 'DAMAGED', 'UNUSABLE'].map(c =>
              `<option value="${c}" ${e.condition === c ? 'selected' : ''}>${c}</option>`
            ).join('')}
          </select>
        </div>
      </div>
      <div class="mb-2">
        <label class="form-label">Laboratory</label>
        <select name="lab_id" class="form-select" required>
          ${availLabs.map(l => `<option value="${l.lab_id}" ${l.lab_id === e.lab.lab_id ? 'selected' : ''}>${esc(l.lab_name)}</option>`).join('')}
        </select>
      </div>
      <div class="mb-3">
        <label class="form-label">Specifications (for PR Reports)</label>
        <input name="specifications" class="form-control" value="${esc(e.extra_metadata?.specifications || '')}" placeholder="Model, rating, brand...">
      </div>
      <button class="btn btn-primary w-100" type="submit">Save to Supabase</button>
    </form>`, () => {
    $('#assetForm').onsubmit = async ev => {
      ev.preventDefault();
      const btn = ev.target.querySelector('button[type="submit"]');
      setBtnBusy(btn, true, 'Saving...');
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
            specifications: fd.get('specifications'),
          },
        });
        hideModal();
        toast(res.message);
        await go('manage');
      } catch (err) {
        toast(err.message, true);
        setBtnBusy(btn, false);
      }
    };
  });
}

async function deleteAsset(assetId) {
  if (!confirm('Permanently delete this equipment record from Supabase?')) return;
  try {
    const r = await api(`/api/assets/${assetId}/delete/`, { method: 'DELETE' });
    toast(r.message);
    await go('manage');
  } catch (e) {
    toast(e.message, true);
  }
}

// ==============================================================================
// PAGE 7: QR LOOKUP, PR TEMPLATE EXPORTS & NOTIFICATIONS
// ==============================================================================

function openQrLookup() {
  showModal('QR / Unique ID Asset Lookup', `
    <form id="qrForm" class="mb-3">
      <div class="input-group">
        <input id="qrInput" class="form-control" placeholder="Scan QR or type tag/serial e.g. INV-CSUB44-0001" required>
        <button class="btn btn-primary" type="submit">Lookup</button>
      </div>
    </form>
    <div id="qrResult"></div>`, () => {
    $('#qrForm').onsubmit = async ev => {
      ev.preventDefault();
      const raw = $('#qrInput').value.trim();
      try {
        const a = await api(`/api/qr/lookup/?q=${encodeURIComponent(raw)}`);
        // Ensure asset is available in state if user clicks Open Full Details
        if (!findAsset(a.asset_id)) {
          allAssets.push({
            ...a,
            lab: { lab_id: a.lab_id, lab_name: a.lab, lab_code: '' },
          });
        }
        $('#qrResult').innerHTML = `
          <div class="p-3 border rounded bg-light">
            <div class="d-flex justify-content-between align-items-center">
              <strong>${esc(a.name)}</strong>${statusBadge(a.status)}
            </div>
            <div class="small text-secondary mt-1">
              <code>${esc(a.asset_tag)}</code> · ${esc(a.lab)} · Stock: ${a.stock_quantity} · ${esc(a.condition)}
            </div>
            ${a.current_borrower ? `<div class="small mt-1 text-primary">Currently borrowed by: <strong>${esc(a.current_borrower)}</strong></div>` : ''}
            <button class="btn btn-sm btn-primary mt-2" onclick="hideModal();openDetails('${a.asset_id}')">
              Open Full Equipment Details
            </button>
          </div>`;
      } catch (err) {
        $('#qrResult').innerHTML = `<div class="alert alert-danger py-2 small">${esc(err.message)}</div>`;
      }
    };
  });
}

async function openProfileModal(username = '') {
  const target = username || currentUser?.username;
  if (!target) return;
  showModal('Student / Researcher Profile', '<div class="text-center py-3 text-secondary">Loading profile...</div>');
  try {
    const data = await api(`/api/users/${encodeURIComponent(target)}/profile/`);
    const u = data.user;
    const s = data.stats;
    $('#modalContent .modal-body').innerHTML = `
      <div class="d-flex align-items-center gap-3 mb-3">
        <div class="avatar" style="width:48px;height:48px;font-size:1rem">${esc(initials(u.full_name))}</div>
        <div>
          <h5 class="mb-0">${esc(u.full_name)}</h5>
          <div class="small text-secondary">${esc(u.email)} · ${esc(ROLE_LABELS[u.role] || u.role)}</div>
        </div>
      </div>
      <div class="row g-2 mb-3">
        <div class="col-6"><small class="text-secondary">Username</small><div class="fw-semibold">${esc(u.username)}</div></div>
        <div class="col-6"><small class="text-secondary">Department</small><div class="fw-semibold">${esc(u.department || 'Not specified')}</div></div>
        <div class="col-6"><small class="text-secondary">Assigned Lab</small><div class="fw-semibold">${esc(u.assigned_lab ? u.assigned_lab.lab_name : 'Campus-wide')}</div></div>
        <div class="col-6"><small class="text-secondary">Penalty Flags</small><div class="fw-semibold">${u.penalty_flags}</div></div>
      </div>
      <div class="row g-2 text-center mb-3">
        <div class="col-3"><div class="p-2 border rounded"><div class="fw-bold">${s.total_requests}</div><small class="text-secondary">Requests</small></div></div>
        <div class="col-3"><div class="p-2 border rounded"><div class="fw-bold">${s.active_loans}</div><small class="text-secondary">Active</small></div></div>
        <div class="col-3"><div class="p-2 border rounded"><div class="fw-bold">${s.returned_count}</div><small class="text-secondary">Returned</small></div></div>
        <div class="col-3"><div class="p-2 border rounded"><div class="fw-bold">${s.active_reservations}</div><small class="text-secondary">Slots</small></div></div>
      </div>
      <h6 class="small text-uppercase text-secondary mb-2">Recent Activity</h6>
      ${(data.recent_requests || []).slice(0, 4).map(r => `
        <div class="d-flex justify-content-between align-items-center border-bottom py-1 small">
          <span><strong>${esc(r.asset_name)}</strong> (${esc(r.lab)})</span>
          ${statusBadge(r.request_status)}
        </div>`).join('') || '<div class="small text-secondary">No borrow history yet.</div>'}
    `;
  } catch (err) {
    $('#modalContent .modal-body').innerHTML = `<div class="alert alert-danger mb-0 small">${esc(err.message)}</div>`;
  }
}

function openReportFaultModal(assetId) {
  const e = findAsset(assetId);
  if (!e) return;
  const isStaff = ['FACULTY', 'ASSISTANT', 'ADMIN'].includes(currentUser?.role);
  showModal(`Report Fault: ${esc(e.name)}`, `
    <form id="faultForm">
      <div class="mb-2">
        <label class="form-label">Equipment</label>
        <input class="form-control" value="${esc(e.asset_tag)} — ${esc(e.lab.lab_name)}" disabled>
      </div>
      <div class="mb-3">
        <label class="form-label">Fault Description <span class="text-danger">*</span></label>
        <textarea name="description" class="form-control" rows="3" minlength="5" placeholder="Describe the issue clearly..." required></textarea>
      </div>
      ${isStaff ? `
        <div class="form-check mb-3">
          <input class="form-check-input" type="checkbox" name="mark_maintenance" id="markMaintCheck" checked>
          <label class="form-check-label small" for="markMaintCheck">Immediately move equipment to Maintenance</label>
        </div>` : ''}
      <button class="btn btn-danger w-100" type="submit">Submit Fault Report</button>
    </form>`, () => {
    $('#faultForm').onsubmit = async ev => {
      ev.preventDefault();
      const btn = ev.target.querySelector('button[type="submit"]');
      setBtnBusy(btn, true, 'Reporting...');
      const fd = new FormData(ev.target);
      try {
        const res = await api('/api/maintenance/report/', {
          method: 'POST',
          body: {
            asset_id: assetId,
            description: fd.get('description'),
            mark_maintenance: Boolean(fd.get('mark_maintenance')),
          },
        });
        hideModal();
        toast(res.message);
        await refreshData();
        renderPage();
      } catch (err) {
        toast(err.message, true);
        setBtnBusy(btn, false);
      }
    };
  });
}

function reports() {
  const s = dashData.stats || {};
  return `
    ${pageHeading('University PR Line Items & Inventory Exports', 'Export reports formatted exactly to Chanakya University PR Excel templates (sheet: "PR Lines").')}
    <div class="row g-3 mb-4">
      <div class="col-6 col-xl-3">${stat('Total assets', s.total_assets, 'boxes', 'blue')}</div>
      <div class="col-6 col-xl-3">${stat('Low stock alerts', s.low_stock_count, 'exclamation-triangle', 'amber')}</div>
      <div class="col-6 col-xl-3">${stat('Overdue loans', s.overdue_count, 'clock-history', 'red')}</div>
      <div class="col-6 col-xl-3">${stat('Open repairs', s.open_maintenance_tickets, 'wrench', 'red')}</div>
    </div>
    <h2 class="section-title mb-3">Official University PR Templates (.xlsx)</h2>
    <div class="row g-3 mb-4">
      ${[
        ['pr_asset', 'PR Template: Assets', 'Exact columns: Type, Category, Quantity, Unit Cost, UOM, Specification, GST Rate (%)', 'bi-pc-display'],
        ['pr_consumable', 'PR Template: Consumables', 'Exact columns: Type, Category, Item, Variant, Quantity, Unit Cost, UOM, Specification, GST', 'bi-droplet-half'],
        ['pr_service', 'PR Template: Services / Repairs', 'Exports Maintenance & damaged equipment logs matching Service PR template', 'bi-tools'],
      ].map(([fmt, title, desc, ico]) => `
        <div class="col-md-4">
          <div class="report-tile h-100 d-flex flex-column justify-content-between">
            <div><i class="bi ${ico}"></i><h3>${title}</h3><p>${desc}</p></div>
            <a class="btn btn-sm btn-primary mt-3" href="/api/inventory/export/?format=${fmt}">
              <i class="bi bi-download me-1"></i>Download .XLSX
            </a>
          </div>
        </div>`).join('')}
    </div>
    <h2 class="section-title mb-3">General Multi-Format Exports</h2>
    <div class="row g-3">
      ${[
        ['csv', 'CSV Spreadsheet', 'bi-filetype-csv'],
        ['xlsx', 'Full Excel (.xlsx)', 'bi-file-earmark-excel'],
        ['json', 'Structured JSON', 'bi-filetype-json'],
        ['html', 'HTML Table', 'bi-filetype-html'],
      ].map(([fmt, title, ico]) => `
        <div class="col-md-6 col-xl-3">
          <div class="report-tile">
            <i class="bi ${ico}"></i><h3>${title}</h3>
            <a class="btn btn-sm btn-outline-primary mt-3" href="/api/inventory/export/?format=${fmt}">Download ${fmt.toUpperCase()}</a>
          </div>
        </div>`).join('')}
    </div>`;
}

function notificationRow(n) {
  return `
    <div class="notification-item">
      <div class="notification-symbol"><i class="bi ${n.icon || 'bi-bell'}"></i></div>
      <div>
        <strong>${esc(n.title)}</strong>
        <p>${esc(n.detail)}</p>
        <small>${esc(n.time)}</small>
      </div>
    </div>`;
}

function notifications() {
  return `
    ${pageHeading('Live Notifications', 'Updates automatically every 5 seconds from Supabase.')}
    <div class="panel">
      ${(dashData.notifications || []).map(notificationRow).join('') || '<p class="text-secondary mb-0">No notifications.</p>'}
    </div>`;
}

// ==============================================================================
// AUTHENTICATION, LIVE OTP REGISTRATION & PUBLIC LABS DROPDOWN
// ==============================================================================

async function loadPublicLabsForReg() {
  const sel = $('#regLabId');
  if (!sel) return;
  try {
    const res = await api('/api/labs/public/');
    const list = res.labs || [];
    if (!list.length) {
      sel.innerHTML = '<option value="">No labs in database — run ingest_canonical first</option>';
      return;
    }
    const prevVal = sel.value;
    sel.innerHTML =
      '<option value="">Select assigned laboratory...</option>' +
      list.map(l => `<option value="${l.lab_id}">${esc(l.lab_name)} (${esc(l.lab_code)})</option>`).join('');
    if (prevVal) sel.value = prevVal;
  } catch (err) {
    sel.innerHTML = '<option value="">Unable to load labs (check server)</option>';
  }
}

function syncRegRoleField() {
  const isAsst = $('#regRole')?.value === 'ASSISTANT';
  $('#regLabWrap')?.classList.toggle('d-none', !isAsst);
  if ($('#regLabId')) $('#regLabId').required = isAsst;
  if (isAsst) loadPublicLabsForReg();
}

$('#regRole')?.addEventListener('change', syncRegRoleField);

$('#tabSignIn')?.addEventListener('click', () => {
  $('#tabSignIn').classList.add('active');
  $('#tabRegister').classList.remove('active');
  $('#signInPanel').classList.remove('d-none');
  $('#registerPanel').classList.add('d-none');
});

$('#tabRegister')?.addEventListener('click', () => {
  $('#tabRegister').classList.add('active');
  $('#tabSignIn').classList.remove('active');
  $('#registerPanel').classList.remove('d-none');
  $('#signInPanel').classList.add('d-none');
  loadPublicLabsForReg();
  syncRegRoleField();
});

$('#loginForm')?.addEventListener('submit', async e => {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  setBtnBusy(btn, true, 'Signing in...');
  try {
    const res = await api('/api/auth/login/', {
      method: 'POST',
      body: {
        email: $('#loginName').value.trim(),
        password: $('#loginPassword').value,
      },
    });
    applyUserSession(res.user);
    toast(`Signed in as ${ROLE_LABELS[res.user.role] || res.user.role}.`);
    await go('dashboard');
  } catch (err) {
    toast(err.message, true);
  } finally {
    setBtnBusy(btn, false);
  }
});

$('#otpRequestForm')?.addEventListener('submit', async e => {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  setBtnBusy(btn, true, 'Sending OTP...');
  try {
    const role = $('#regRole').value;
    const res = await api('/api/auth/request-otp/', {
      method: 'POST',
      body: {
        email: $('#regEmail').value.trim(),
        role,
        department: $('#regDept').value.trim(),
        assigned_lab_id: role === 'ASSISTANT' ? ($('#regLabId')?.value || '') : '',
      },
    });
    $('#otpVerifyForm').classList.remove('d-none');
    $('#otpStatusBanner').innerHTML = res.dev_otp
      ? `OTP generated! <strong>Dev Mode OTP: <code>${esc(res.dev_otp)}</code></strong>`
      : `<i class="bi bi-envelope-check me-1"></i>${esc(res.message)} Check your Chanakya University inbox!`;
    toast('6-digit OTP dispatched!');
  } catch (err) {
    toast(err.message, true);
  } finally {
    setBtnBusy(btn, false);
  }
});

$('#otpVerifyForm')?.addEventListener('submit', async e => {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  setBtnBusy(btn, true, 'Verifying...');
  try {
    const res = await api('/api/auth/verify-otp/', {
      method: 'POST',
      body: {
        email: $('#regEmail').value.trim(),
        code: $('#regOtpCode').value.trim(),
        password: $('#regPassword').value,
        full_name: $('#regName').value.trim(),
      },
    });
    applyUserSession(res.user);
    toast('College email verified & account created!');
    await go('dashboard');
  } catch (err) {
    toast(err.message, true);
  } finally {
    setBtnBusy(btn, false);
  }
});

$('#logoutBtn')?.addEventListener('click', async () => {
  stopLiveSync();
  await api('/api/auth/logout/', { method: 'POST' }).catch(() => {});
  currentUser = null;
  searchFilters = { q: '', lab: '', status: '' };
  $('#appView').classList.add('d-none');
  $('#loginView').classList.remove('d-none');
  loadPublicLabsForReg();
  toast('Logged out.');
});

$('#togglePassword')?.addEventListener('click', () => {
  const p = $('#loginPassword');
  if (p) p.type = p.type === 'password' ? 'text' : 'password';
});

$('#mobileMenu')?.addEventListener('click', () => $('#sidebar')?.classList.toggle('open'));
$('#topNotifications')?.addEventListener('click', () => go('notifications'));
$('.top-user')?.addEventListener('click', () => openProfileModal());
$('.user-mini')?.addEventListener('click', () => openProfileModal());
if ($('.top-user')) $('.top-user').style.cursor = 'pointer';
if ($('.user-mini')) $('.user-mini').style.cursor = 'pointer';

// ==============================================================================
// BOOTSTRAP APP ON PAGE LOAD
// ==============================================================================
(async function boot() {
  loadPublicLabsForReg();
  syncRegRoleField();
  try {
    const me = await api('/api/auth/me/');
    applyUserSession(me);
    await go('dashboard');
  } catch (_) {
    // Stay on login view until authenticated
  }
})();
