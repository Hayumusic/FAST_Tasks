const state = {
  nodes: [],
  pillars: [],
  useCases: [],
  hierarchy: [],
  filters: {},
  registryFilters: {},
  selectedNodeId: null,
  explorerView: "board",
  expandedNodes: new Set(),
  collapsedBoardNodes: new Set(),
  boardCollapseInitialized: false,
  pendingSetupOpen: false,
};

const TYPES = ["Automation", "AI", "Agentic AI"];
const STATUSES = ["Planned", "WIP", "Built", "Built and Deployed"];

const qs = (selector, root = document) => root.querySelector(selector);
const qsa = (selector, root = document) => [...root.querySelectorAll(selector)];
const today = () => new Date().toISOString().slice(0, 10);
const statusClass = (status) => `status-${status.replaceAll(" ", "-")}`;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let message = await response.text();
    try {
      message = JSON.parse(message).error || message;
    } catch (_) {
      // Keep plain text response.
    }
    if (response.status === 401 && path !== "/admin/login") showAdminLogin();
    throw new Error(message);
  }
  return response.json();
}

async function refresh() {
  const query = new URLSearchParams(cleanFilters()).toString();
  const data = await api(`/api/bootstrap${query ? `?${query}` : ""}`);
  Object.assign(state, data);
  state.explorerView = "board";
  if (!state.boardCollapseInitialized) {
    state.collapsedBoardNodes = new Set(expandableNodeIds());
    state.boardCollapseInitialized = true;
  }
  populateFilters();
  renderAll();
}

function cleanFilters() {
  return Object.fromEntries(Object.entries(state.filters).filter(([, value]) => value));
}

function populateSelect(select, options, value = "", label = "All") {
  const current = select.value || value;
  select.innerHTML = `<option value="">${label}</option>` + options.map((option) => (
    `<option value="${option.value}">${option.label}</option>`
  )).join("");
  select.value = current;
}

function populateFilters() {
  populateSelect(qs("#filter-pillar"), state.pillars.map((p) => ({ value: p.name, label: p.name })));
  populateSelect(qs("#filter-process"), state.nodes.filter((n) => n.type === "process").map((n) => ({ value: n.id, label: n.name })));
  const subprocesses = state.nodes.filter((n) => n.type === "subprocess" && (!state.filters.process || String(n.parent_id) === String(state.filters.process)));
  populateSelect(qs("#filter-subprocess"), subprocesses.map((n) => ({ value: n.id, label: n.name })));
  populateSelect(qs("#filter-type"), TYPES.map((value) => ({ value, label: value })));
  populateSelect(qs("#filter-status"), STATUSES.map((value) => ({ value, label: value })));
  const contributors = [...new Set(state.useCases.map((uc) => uc.contributor).filter(Boolean))].sort();
  populateSelect(qs("#filter-contributor"), contributors.map((value) => ({ value, label: value })));
  Object.entries(state.filters).forEach(([key, value]) => {
    const el = qs(`#filter-${key}`);
    if (el) el.value = value;
  });
  qs("#export-link").href = `/export?${new URLSearchParams(cleanFilters()).toString()}`;
}

function renderAll() {
  renderMetrics();
  renderDashboardLists();
  const tree = qs("#process-tree");
  if (tree) renderHierarchy(tree, state.hierarchy, { selectable: true });
  renderProcessBoard();
  renderSelectedNode();
  populateRegistryFilters();
  renderUseCaseTable();
  renderSetup();
  renderAdminState();
  fillFormSelects();
}

function renderAdminState() {
  const status = qs("#admin-status");
  const logout = qs("#admin-logout");
  const setup = qs("#setup");
  if (!status || !logout || !setup) return;
  setup.classList.toggle("setup-locked", !state.isAdmin);
  status.textContent = state.isAdmin ? "Admin unlocked" : "Admin access required";
  logout.hidden = !state.isAdmin;
  qsa("#hierarchy-import-form label, #add-node, #add-pillar", setup).forEach((el) => {
    el.classList.toggle("disabled", !state.isAdmin);
  });
}

function showAdminLogin() {
  qs("#admin-login-error").hidden = true;
  qs("#admin-login-error").textContent = "";
  qs("#admin-login-form").reset();
  showModal("#adminLoginModal");
}

function renderMetrics() {
  const counts = {
    "Total Use Cases": state.useCases.length,
    Planned: countBy("status", "Planned"),
    WIP: countBy("status", "WIP"),
    Built: countBy("status", "Built"),
    "Built & Deployed": countBy("status", "Built and Deployed"),
    Automation: countBy("use_case_type", "Automation"),
    AI: countBy("use_case_type", "AI"),
    "Agentic AI": countBy("use_case_type", "Agentic AI"),
    "Monthly Cost": money(sumBy("estimated_monthly_cost")),
    "Monthly Savings": money(sumBy("estimated_monthly_savings")),
  };
  qs("#metric-grid").innerHTML = Object.entries(counts).map(([label, value]) => `
    <div class="metric-card"><strong>${value}</strong><span>${label}</span></div>
  `).join("");
}

function countBy(field, value) {
  return state.useCases.filter((uc) => uc[field] === value).length;
}

function sumBy(field) {
  return state.useCases.reduce((sum, uc) => sum + Number(uc[field] || 0), 0);
}

function groupedCounts(field) {
  const map = new Map();
  state.useCases.forEach((uc) => {
    const label = uc[field];
    if (!label) return;
    map.set(label, (map.get(label) || 0) + 1);
  });
  return [...map.entries()].sort((a, b) => b[1] - a[1]);
}

function groupedAttachedItems() {
  const map = new Map();
  state.useCases.forEach((uc) => {
    const level = hierarchyLevelLabel(uc.attached_level || "");
    const label = uc.attached_level === "process" ? uc.process : uc.attached_level === "subprocess" ? uc.subprocess : uc.task;
    if (!level || !label) return;
    const key = `${level}::${label}`;
    const current = map.get(key) || { level, label, count: 0 };
    current.count += 1;
    map.set(key, current);
  });
  return [...map.values()].sort((a, b) => b.count - a.count || a.level.localeCompare(b.level) || a.label.localeCompare(b.label));
}

function renderDashboardLists() {
  const concentration = groupedAttachedItems();
  const actualTotal = state.useCases.length;

  qs("#ranked-list").innerHTML = concentration.length ? `
    <div class="panel-total"><strong>${actualTotal}</strong><span>actual use case${actualTotal === 1 ? "" : "s"} in current filter</span></div>
    <div class="concentration-header" aria-hidden="true">
      <span>Level</span>
      <span>Attached Item</span>
      <span>Use Cases</span>
    </div>
    ${concentration.map((item) => `
    <div class="concentration-row">
      <span class="level-badge level-${item.level.toLowerCase().replace(/[^a-z]+/g, "-")}">${item.level}</span>
      <strong>${item.label}</strong>
      <span class="concentration-count">${item.count} use case${item.count === 1 ? "" : "s"}</span>
    </div>
  `).join("")}
  ` : empty("No matching use cases");
}

function titleCase(text = "") {
  return text.split(" ").map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}

function hierarchyLevelLabel(level = "") {
  if (level === "subprocess") return "Sub-process";
  return titleCase(level);
}

function renderHierarchy(root, nodes, options = {}) {
  root.innerHTML = nodes.map((node) => renderNode(node, options)).join("") || empty("No hierarchy items yet");
}

function renderNode(node, options) {
  const selected = Number(state.selectedNodeId) === Number(node.id) ? " selected" : "";
  const expanded = options.compact || state.expandedNodes.has(Number(node.id));
  const button = node.children?.length ? `<button class="caret" data-node-toggle="${node.id}" aria-label="${expanded ? "Collapse" : "Expand"}">${expanded ? "-" : "+"}</button>` : `<span class="caret"></span>`;
  return `
    <div class="node-block">
      <div class="node-row node-${node.type}${selected}" data-node-id="${node.id}">
        ${button}
        <div>
          <div class="node-title">${node.name}</div>
          <div class="node-meta">${titleCase(node.type)} · ${node.pillars || "No pillars"}</div>
        </div>
        <div class="node-actions">
          <span class="usecase-count">${node.counts?.total || 0}</span>
          <button class="node-add" data-add-usecase-node="${node.id}" aria-label="Add use case to ${node.name}">Add</button>
        </div>
      </div>
      <div class="node-children${expanded ? "" : " collapsed"}">${(node.children || []).map((child) => renderNode(child, options)).join("")}</div>
    </div>
  `;
}

function renderProcessBoard() {
  if (qs("#process-tree")) qs("#process-tree").style.display = "none";
  qs("#process-board").style.display = "grid";
  qs("#process-board").innerHTML = state.hierarchy.map((process) => `
    <section class="process-column${state.collapsedBoardNodes.has(Number(process.id)) ? " collapsed" : ""}">
      <button class="board-process node-process${Number(state.selectedNodeId) === Number(process.id) ? " selected" : ""}" data-node-id="${process.id}">
        ${boardToggle(process)}
        <span class="node-bullet node-bullet-process" aria-hidden="true"></span>
        <strong>${process.name}</strong>
        <span class="board-count">${process.counts?.total || 0}</span>
        <span class="board-add" data-add-usecase-node="${process.id}" aria-label="Add use case to ${process.name}">Add</span>
      </button>
      <div class="subprocess-stack${state.collapsedBoardNodes.has(Number(process.id)) ? " collapsed" : ""}">
        ${(process.children || []).map((subprocess) => `
          <div class="subprocess-card${state.collapsedBoardNodes.has(Number(subprocess.id)) ? " collapsed" : ""}">
            <button class="board-subprocess node-subprocess${Number(state.selectedNodeId) === Number(subprocess.id) ? " selected" : ""}" data-node-id="${subprocess.id}">
              ${boardToggle(subprocess)}
              <span class="node-bullet node-bullet-subprocess" aria-hidden="true"></span>
              <strong>${subprocess.name}</strong>
              <span class="board-count">${subprocess.counts?.total || 0}</span>
              <span class="board-add" data-add-usecase-node="${subprocess.id}" aria-label="Add use case to ${subprocess.name}">Add</span>
            </button>
            <div class="task-stack${state.collapsedBoardNodes.has(Number(subprocess.id)) ? " collapsed" : ""}">
              ${(subprocess.children || []).map((task) => `
                <div class="task-branch">
                  <button class="task-chip node-task${Number(state.selectedNodeId) === Number(task.id) ? " selected" : ""}" data-node-id="${task.id}">
                    <span class="node-bullet node-bullet-task" aria-hidden="true"></span>
                    <span>${task.name}</span>
                    <strong>${task.counts?.total || 0}</strong>
                    <i class="board-add" data-add-usecase-node="${task.id}" aria-label="Add use case to ${task.name}">Add</i>
                  </button>
                </div>
              `).join("") || empty("No tasks uploaded")}
            </div>
          </div>
        `).join("") || empty("No sub-processes uploaded")}
      </div>
    </section>
  `).join("") || empty("Upload a hierarchy to build the board");
}

function directUseCases(nodeId) {
  return state.useCases.filter((uc) => Number(uc.node_id) === Number(nodeId));
}

function useCaseGroup(cases, label = "Use cases") {
  if (!cases.length) return "";
  return `
    <div class="usecase-group">
      <div class="usecase-group-label">${label}</div>
      ${cases.map(useCaseBranch).join("")}
    </div>
  `;
}

function useCaseBranch(uc) {
  return `
    <div class="usecase-branch" data-usecase-id="${uc.id}">
      <div>
        <strong>${uc.title}</strong>
        <span>${uc.use_case_type} · ${uc.status}</span>
      </div>
      <button class="btn btn-sm btn-link edit-usecase" data-id="${uc.id}">Edit</button>
    </div>
  `;
}

function expandableNodeIds() {
  const ids = [];
  const visit = (nodes) => {
    nodes.forEach((node) => {
      if (node.children?.length) ids.push(Number(node.id));
      visit(node.children || []);
    });
  };
  visit(state.hierarchy);
  return ids;
}

function boardToggle(node) {
  if (!node.children?.length) return `<span class="board-caret"></span>`;
  const collapsed = state.collapsedBoardNodes.has(Number(node.id));
  return `<span class="board-caret" data-board-toggle="${node.id}" aria-label="${collapsed ? "Expand" : "Collapse"}">${collapsed ? "+" : "-"}</span>`;
}

function renderSelectedNode() {
  const node = state.nodes.find((item) => Number(item.id) === Number(state.selectedNodeId));
  const hierarchyNode = findHierarchyNode(state.selectedNodeId);
  const panel = qs("#detail-panel");
  qs(".explorer-layout").classList.toggle("detail-open", Boolean(node));
  if (!node) {
    panel.hidden = true;
    qs("#selected-node-title").textContent = "Select a hierarchy item";
    qs("#selected-node-meta").textContent = "Pillars and status counts appear here";
    qs("#selected-node-description").textContent = "";
    qs("#selected-node-cases").innerHTML = "";
    return;
  }
  panel.hidden = false;
  qs("#selected-node-title").textContent = node.name;
  const total = hierarchyNode?.counts?.total || 0;
  qs("#selected-node-meta").textContent = `${titleCase(node.type)} · ${total} use case${total === 1 ? "" : "s"} · ${node.pillars || "No pillars"}`;
  qs("#selected-node-description").textContent = node.description || "No description provided.";
  const cases = state.useCases.filter((uc) => Number(uc.node_id) === Number(node.id));
  qs("#selected-node-cases").innerHTML = cases.map((uc) => useCaseCard(uc)).join("") || empty("No use cases attached directly to this item");
}

function findHierarchyNode(nodeId, nodes = state.hierarchy) {
  for (const node of nodes) {
    if (Number(node.id) === Number(nodeId)) return node;
    const match = findHierarchyNode(nodeId, node.children || []);
    if (match) return match;
  }
  return null;
}

function useCaseCard(uc) {
  return `
    <div class="case-card">
      <h3>${uc.title}</h3>
      <p>${uc.short_description}</p>
      <span class="status-pill ${statusClass(uc.status)}">${uc.status}</span>
      <span class="pill">${uc.use_case_type}</span>
      <button class="btn btn-sm btn-link float-end edit-usecase" data-id="${uc.id}">Edit</button>
    </div>
  `;
}

function renderUseCaseTable() {
  const rows = filteredRegistryUseCases();
  const appliedCount = Object.values(state.registryFilters).filter(Boolean).length;
  const total = state.useCases.length;
  qs("#registry-count").textContent = `${rows.length} of ${total} use case${total === 1 ? "" : "s"}`;
  qs("#registry-filter-note").textContent = appliedCount ? `${appliedCount} table filter${appliedCount === 1 ? "" : "s"} applied` : "No table filters applied";
  qs("#usecase-table tbody").innerHTML = rows.map((uc) => `
    <tr>
      <td>${pillHtml(uc.pillar)}</td>
      <td>${uc.process || ""}</td>
      <td>${uc.subprocess || ""}</td>
      <td>${uc.task || ""}</td>
      <td><div class="row-title">${uc.title}</div><small class="text-muted">${uc.problem_statement}</small></td>
      <td>${uc.use_case_type}</td>
      <td><span class="status-pill ${statusClass(uc.status)}">${uc.status}</span></td>
      <td>${uc.short_description}</td>
      <td>${money(uc.estimated_monthly_cost)}</td>
      <td>${money(uc.estimated_monthly_savings)}</td>
      <td>${uc.contributor}</td>
      <td>${uc.created_date}</td>
      <td>${uc.updated_date}</td>
      <td><button class="btn btn-sm btn-outline-secondary edit-usecase" data-id="${uc.id}">Edit</button></td>
    </tr>
  `).join("") || `<tr><td colspan="14">${empty("No use cases match the current filters")}</td></tr>`;
}

function populateRegistryFilters() {
  qsa(".table-filter").forEach((control) => {
    const key = control.dataset.tableFilter;
    if (control.tagName === "SELECT") {
      let options = [];
      let label = "All";
      if (key === "status") options = STATUSES;
      else if (key === "use_case_type") options = TYPES;
      else if (["process", "subprocess", "task"].includes(key)) options = uniqueRegistryValues(key);
      else if (key === "sort_cost" || key === "sort_savings") {
        label = "Sort";
        options = [
          { value: "asc", label: "Ascending" },
          { value: "desc", label: "Descending" },
        ];
      }
      control.innerHTML = `<option value="">${label}</option>` + options.map((option) => {
        const value = typeof option === "string" ? option : option.value;
        const text = typeof option === "string" ? option : option.label;
        return `<option value="${value}">${text}</option>`;
      }).join("");
    }
    control.value = state.registryFilters[key] || "";
  });
}

function filteredRegistryUseCases() {
  const filters = Object.fromEntries(Object.entries(state.registryFilters).filter(([key, value]) => value && !key.startsWith("sort_")));
  const sortKey = state.registryFilters.sort_cost ? "estimated_monthly_cost" : state.registryFilters.sort_savings ? "estimated_monthly_savings" : "";
  const sortDirection = state.registryFilters.sort_cost || state.registryFilters.sort_savings || "";
  let rows = state.useCases.filter((uc) => Object.entries(filters).every(([key, raw]) => {
    const value = String(raw || "").toLowerCase().trim();
    if (!value) return true;
    if (["process", "subprocess", "task", "use_case_type", "status"].includes(key)) {
      return String(uc[key] || "") === String(raw || "");
    }
    return String(uc[key] || "").toLowerCase().includes(value);
  }));
  if (sortKey) {
    rows = [...rows].sort((a, b) => {
      const delta = Number(a[sortKey] || 0) - Number(b[sortKey] || 0);
      return sortDirection === "desc" ? -delta : delta;
    });
  }
  return rows;
}

function uniqueRegistryValues(field) {
  return [...new Set(state.useCases.map((uc) => uc[field]).filter(Boolean))]
    .sort((a, b) => String(a).localeCompare(String(b)));
}

function money(value) {
  return Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function pillHtml(text = "") {
  return String(text || "").split(",").filter(Boolean).map((x) => `<span class="pill">${x.trim()}</span>`).join("");
}

function renderSetup() {
  qs("#setup-nodes").innerHTML = state.nodes.map((node) => `
    <div class="setup-item">
      <div><strong>${node.name}</strong><small>${titleCase(node.type)} · ${node.pillars || "No pillars"} · ${node.description || ""}</small></div>
      <button class="btn btn-sm btn-outline-secondary edit-node" data-id="${node.id}">Edit</button>
    </div>
  `).join("");
  qs("#setup-pillars").innerHTML = state.pillars.map((pillar) => `
    <div class="setup-item">
      <div><strong>${pillar.name}</strong><small>${pillar.description || ""}</small></div>
      <button class="btn btn-sm btn-outline-secondary edit-pillar" data-id="${pillar.id}">Edit</button>
    </div>
  `).join("");
}

function fillFormSelects() {
  fillHierarchyPicker();
  qsa('[name="use_case_type"]').forEach((select) => { select.innerHTML = TYPES.map((t) => `<option>${t}</option>`).join(""); });
  qsa('[name="status"]').forEach((select) => { select.innerHTML = STATUSES.map((s) => `<option>${s}</option>`).join(""); });
  qsa('[name="type"]').forEach((select) => { select.innerHTML = ["process", "subprocess", "task"].map((t) => `<option value="${t}">${titleCase(t)}</option>`).join(""); });
  qsa('[name="parent_id"]').forEach((select) => { select.innerHTML = `<option value="">No parent</option>` + state.nodes.map((n) => `<option value="${n.id}">${titleCase(n.type)} · ${pathForNode(n)}</option>`).join(""); });
  qsa('[name="pillar_ids"]').forEach((select) => { select.innerHTML = state.pillars.map((p) => `<option value="${p.id}">${p.name}</option>`).join(""); });
}

function optionsHtml(nodes, placeholder) {
  return `<option value="">${placeholder}</option>` + nodes.map((node) => `<option value="${node.id}">${node.name}</option>`).join("");
}

function fillHierarchyPicker(selection = {}) {
  const form = qs("#usecase-form");
  if (!form) return;
  const processSelect = form.elements.process_id;
  const subprocessSelect = form.elements.subprocess_id;
  const taskSelect = form.elements.task_id;
  if (!processSelect || !subprocessSelect || !taskSelect) return;

  const processes = state.nodes.filter((node) => node.type === "process");
  processSelect.innerHTML = optionsHtml(processes, "Select process");
  processSelect.value = selection.process_id || processSelect.value || "";

  const subprocesses = state.nodes.filter((node) => node.type === "subprocess" && String(node.parent_id) === String(processSelect.value));
  subprocessSelect.innerHTML = optionsHtml(subprocesses, processSelect.value ? "Attach at process level" : "Select process first");
  subprocessSelect.disabled = !processSelect.value;
  subprocessSelect.value = selection.subprocess_id || "";

  const tasks = state.nodes.filter((node) => node.type === "task" && String(node.parent_id) === String(subprocessSelect.value));
  taskSelect.innerHTML = optionsHtml(tasks, subprocessSelect.value ? "Attach at sub-process level" : "Select sub-process first");
  taskSelect.disabled = !subprocessSelect.value;
  taskSelect.value = selection.task_id || "";

  form.elements.node_id.value = taskSelect.value || subprocessSelect.value || processSelect.value || "";
}

function hierarchySelectionForNode(nodeId) {
  const node = state.nodes.find((item) => Number(item.id) === Number(nodeId));
  if (!node) return {};
  if (node.type === "process") return { process_id: String(node.id) };
  if (node.type === "subprocess") return { process_id: String(node.parent_id || ""), subprocess_id: String(node.id) };
  const subprocess = state.nodes.find((item) => Number(item.id) === Number(node.parent_id));
  return {
    process_id: String(subprocess?.parent_id || ""),
    subprocess_id: String(node.parent_id || ""),
    task_id: String(node.id),
  };
}

function syncHierarchyNodeId(form = qs("#usecase-form")) {
  form.elements.node_id.value = form.elements.task_id.value || form.elements.subprocess_id.value || form.elements.process_id.value || "";
}

function pathForNode(node) {
  const parent = state.nodes.find((n) => n.id === node.parent_id);
  return parent ? `${pathForNode(parent)} / ${node.name}` : node.name;
}

function empty(message) {
  return `<div class="text-muted p-2">${message}</div>`;
}

function formData(form) {
  syncHierarchyNodeId(form);
  const data = Object.fromEntries(new FormData(form).entries());
  if (form.id === "node-form") data.pillar_ids = qsa('[name="pillar_ids"] option:checked', form).map((o) => Number(o.value));
  if (data.node_id) data.node_id = Number(data.node_id);
  if (data.parent_id) data.parent_id = Number(data.parent_id);
  ["effort_hours", "cost_per_run", "frequency_per_month", "savings_percent"].forEach((field) => {
    if (field in data) data[field] = Number(data[field] || 0);
  });
  delete data.process_id;
  delete data.subprocess_id;
  delete data.task_id;
  return data;
}

function updateEstimatePreview() {
  const form = qs("#usecase-form");
  const cost = Number(form.elements.cost_per_run.value || 0);
  const frequency = Number(form.elements.frequency_per_month.value || 0);
  const savings = Number(form.elements.savings_percent.value || 0);
  const monthlyCost = cost * frequency;
  const monthlySavings = monthlyCost * savings / 100;
  qs("#estimate-preview").innerHTML = `
    <strong>Current monthly cost: ${money(monthlyCost)}</strong>
    <strong>Monthly savings from automation: ${money(monthlySavings)}</strong>
  `;
}

function openUseCase(id = null, nodeId = null) {
  const form = qs("#usecase-form");
  form.reset();
  fillFormSelects();
  qs("#delete-usecase").style.display = id ? "block" : "none";
  form.elements.id.value = id || "";
  form.elements.created_date.value = today();
  form.elements.updated_date.value = today();
  if (nodeId) fillHierarchyPicker(hierarchySelectionForNode(nodeId));
  updateEstimatePreview();
  if (id) {
    const uc = state.useCases.find((item) => Number(item.id) === Number(id));
    Object.entries(uc).forEach(([key, value]) => {
      if (form.elements[key]) form.elements[key].value = value ?? "";
    });
    fillHierarchyPicker(hierarchySelectionForNode(uc.node_id));
    updateEstimatePreview();
  }
  showModal("#useCaseModal");
}

function openNode(id = null) {
  const form = qs("#node-form");
  form.reset();
  fillFormSelects();
  qs("#delete-node").style.display = id ? "block" : "none";
  form.elements.id.value = id || "";
  if (id) {
    const node = state.nodes.find((item) => Number(item.id) === Number(id));
    Object.entries(node).forEach(([key, value]) => {
      if (form.elements[key]) form.elements[key].value = value ?? "";
    });
    const ids = String(node.pillar_ids || "").split(",");
    qsa('[name="pillar_ids"] option', form).forEach((option) => { option.selected = ids.includes(option.value); });
  }
  showModal("#nodeModal");
}

function openPillar(id = null) {
  const form = qs("#pillar-form");
  form.reset();
  qs("#delete-pillar").style.display = id ? "block" : "none";
  form.elements.id.value = id || "";
  if (id) {
    const pillar = state.pillars.find((item) => Number(item.id) === Number(id));
    Object.entries(pillar).forEach(([key, value]) => {
      if (form.elements[key]) form.elements[key].value = value ?? "";
    });
  }
  showModal("#pillarModal");
}

function showModal(selector) {
  const modal = qs(selector);
  modal.classList.add("show");
  modal.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
}

function hideModal(selectorOrElement) {
  const modal = typeof selectorOrElement === "string" ? qs(selectorOrElement) : selectorOrElement;
  modal.classList.remove("show");
  modal.setAttribute("aria-hidden", "true");
  if (!qs(".modal.show")) document.body.style.overflow = "";
}

function setActiveSection(sectionId) {
  qsa("[data-section-target]").forEach((btn) => btn.classList.toggle("active", btn.dataset.sectionTarget === sectionId));
  qsa(".app-section").forEach((section) => section.classList.toggle("active", section.id === sectionId));
  document.body.dataset.activeSection = sectionId;
}

document.addEventListener("click", async (event) => {
  const target = event.target;
  if (target.matches(".sidebar-toggle")) {
    const collapsed = document.body.classList.toggle("sidebar-collapsed");
    target.setAttribute("aria-expanded", collapsed ? "false" : "true");
    target.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
    target.textContent = collapsed ? "›" : "‹";
    return;
  }
  if (target.matches("[data-node-toggle]")) {
    event.stopPropagation();
    const id = Number(target.dataset.nodeToggle);
    if (state.expandedNodes.has(id)) state.expandedNodes.delete(id);
    else state.expandedNodes.add(id);
    renderAll();
    return;
  }
  if (target.matches("[data-board-toggle]")) {
    event.stopPropagation();
    const id = Number(target.dataset.boardToggle);
    if (state.collapsedBoardNodes.has(id)) state.collapsedBoardNodes.delete(id);
    else state.collapsedBoardNodes.add(id);
    renderAll();
    return;
  }
  if (target.closest("[data-add-usecase-node]")) {
    event.stopPropagation();
    const nodeId = target.closest("[data-add-usecase-node]").dataset.addUsecaseNode;
    state.selectedNodeId = nodeId;
    renderAll();
    openUseCase(null, nodeId);
    return;
  }
  if (target.matches("#close-detail-panel")) {
    state.selectedNodeId = null;
    renderAll();
    return;
  }
  if (target.matches("[data-expand-all]")) {
    expandableNodeIds().forEach((id) => state.expandedNodes.add(id));
    state.collapsedBoardNodes.clear();
    renderAll();
    return;
  }
  if (target.matches("[data-collapse-all]")) {
    state.expandedNodes.clear();
    state.collapsedBoardNodes = new Set(expandableNodeIds());
    renderAll();
    return;
  }
  const sectionButton = target.closest("[data-section-target]");
  if (sectionButton) {
    if (sectionButton.dataset.sectionTarget === "setup" && !state.isAdmin) {
      state.pendingSetupOpen = true;
      showAdminLogin();
      return;
    }
    setActiveSection(sectionButton.dataset.sectionTarget);
    return;
  }
  if (target.matches("[data-explorer-view]")) {
    state.explorerView = target.dataset.explorerView;
    renderAll();
  }
  if (target.closest("[data-node-id]")) {
    state.selectedNodeId = target.closest("[data-node-id]").dataset.nodeId;
    renderAll();
  }
  if (target.matches("#add-usecase, #add-usecase-dashboard")) openUseCase();
  if (target.matches("#add-usecase-from-explorer")) openUseCase(null, state.selectedNodeId);
  if (target.matches(".edit-usecase")) openUseCase(target.dataset.id);
  if (target.matches("#add-node")) openNode();
  if (target.matches(".edit-node")) openNode(target.dataset.id);
  if (target.matches("#add-pillar")) openPillar();
  if (target.matches(".edit-pillar")) openPillar(target.dataset.id);
  if (target.matches("#admin-logout")) {
    await api("/admin/logout", { method: "POST", body: JSON.stringify({}) });
    state.isAdmin = false;
    setActiveSection("dashboard");
    renderAll();
  }
  if (target.matches("[data-bs-dismiss='modal'], .btn-close")) hideModal(target.closest(".modal"));
  if (target.matches("#reset-filters")) {
    state.filters = {};
    qsa(".filter-panel select").forEach((select) => { select.value = ""; });
    await refresh();
  }
  if (target.matches("#reset-registry-filters")) {
    state.registryFilters = {};
    qsa(".table-filter").forEach((control) => { control.value = ""; });
    qs("#global-search").value = "";
    renderUseCaseTable();
  }
});

document.addEventListener("input", (event) => {
  if (event.target.matches("#global-search")) {
    state.registryFilters.title = event.target.value;
    const titleFilter = qs('[data-table-filter="title"]');
    if (titleFilter) titleFilter.value = event.target.value;
    renderUseCaseTable();
    return;
  }
  if (!event.target.matches(".table-filter")) return;
  state.registryFilters[event.target.dataset.tableFilter] = event.target.value;
  if (event.target.dataset.tableFilter === "sort_cost") {
    state.registryFilters.sort_savings = "";
    qs('[data-table-filter="sort_savings"]').value = "";
  }
  if (event.target.dataset.tableFilter === "sort_savings") {
    state.registryFilters.sort_cost = "";
    qs('[data-table-filter="sort_cost"]').value = "";
  }
  if (event.target.dataset.tableFilter === "title") qs("#global-search").value = event.target.value;
  renderUseCaseTable();
});

document.addEventListener("change", (event) => {
  if (!event.target.matches(".table-filter")) return;
  state.registryFilters[event.target.dataset.tableFilter] = event.target.value;
  if (event.target.dataset.tableFilter === "sort_cost") {
    state.registryFilters.sort_savings = "";
    qs('[data-table-filter="sort_savings"]').value = "";
  }
  if (event.target.dataset.tableFilter === "sort_savings") {
    state.registryFilters.sort_cost = "";
    qs('[data-table-filter="sort_cost"]').value = "";
  }
  if (event.target.dataset.tableFilter === "title") qs("#global-search").value = event.target.value;
  renderUseCaseTable();
});

qsa("#usecase-form input").forEach((input) => {
  input.addEventListener("input", () => {
    if (["cost_per_run", "frequency_per_month", "savings_percent"].includes(input.name)) updateEstimatePreview();
  });
});

qs("#usecase-form").addEventListener("change", (event) => {
  const form = qs("#usecase-form");
  if (event.target.name === "process_id") {
    fillHierarchyPicker({ process_id: event.target.value });
  }
  if (event.target.name === "subprocess_id") {
    fillHierarchyPicker({ process_id: form.elements.process_id.value, subprocess_id: event.target.value });
  }
  if (event.target.name === "task_id") {
    syncHierarchyNodeId(form);
  }
});

qs("#hierarchy-file").addEventListener("change", async () => {
  const form = qs("#hierarchy-import-form");
  const file = qs("#hierarchy-file").files[0];
  if (!file) return;
  const data = new FormData();
  data.append("file", file);
  data.append("replace", qs('[name="replace"]', form).checked ? "true" : "false");
  const response = await fetch("/api/hierarchy/import", { method: "POST", body: data });
  if (!response.ok) {
    qs("#hierarchy-file").value = "";
    if (response.status === 401) showAdminLogin();
    const text = await response.text();
    let message = text || "Upload failed";
    try {
      message = JSON.parse(text).error || message;
    } catch (_) {
      // Keep plain text response.
    }
    alert(message);
    return;
  }
  const result = await response.json();
  form.reset();
  alert(`Imported ${result.processes} processes, ${result.subprocesses} sub-processes, ${result.tasks} task entries, and ${result.pillars} pillars.`);
  state.selectedNodeId = null;
  await refresh();
});

qs("#admin-login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const error = qs("#admin-login-error");
  error.hidden = true;
  error.textContent = "";
  try {
    await api("/admin/login", {
      method: "POST",
      body: JSON.stringify({
        admin_id: form.elements.admin_id.value,
        password: form.elements.password.value,
      }),
    });
    state.isAdmin = true;
    hideModal("#adminLoginModal");
    if (state.pendingSetupOpen) {
      state.pendingSetupOpen = false;
      setActiveSection("setup");
    }
    await refresh();
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
});

qsa(".filter-panel select").forEach((select) => {
  select.addEventListener("change", async () => {
    state.filters[select.id.replace("filter-", "")] = select.value;
    if (select.id === "filter-process") state.filters.subprocess = "";
    await refresh();
  });
});

qs("#usecase-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = formData(event.target);
  const id = data.id;
  delete data.id;
  await api(id ? `/api/use-cases/${id}` : "/api/use-cases", { method: id ? "PUT" : "POST", body: JSON.stringify(data) });
  state.filters = {};
  qsa(".filter-panel select").forEach((select) => { select.value = ""; });
  hideModal("#useCaseModal");
  await refresh();
});

qs("#node-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = formData(event.target);
  const id = data.id;
  delete data.id;
  await api(id ? `/api/nodes/${id}` : "/api/nodes", { method: id ? "PUT" : "POST", body: JSON.stringify(data) });
  hideModal("#nodeModal");
  await refresh();
});

qs("#pillar-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = formData(event.target);
  const id = data.id;
  delete data.id;
  await api(id ? `/api/pillars/${id}` : "/api/pillars", { method: id ? "PUT" : "POST", body: JSON.stringify(data) });
  hideModal("#pillarModal");
  await refresh();
});

qs("#delete-usecase").addEventListener("click", async () => {
  const id = qs("#usecase-form").elements.id.value;
  if (id && confirm("Delete this use case?")) {
    await api(`/api/use-cases/${id}`, { method: "DELETE" });
    hideModal("#useCaseModal");
    await refresh();
  }
});

qs("#delete-node").addEventListener("click", async () => {
  const id = qs("#node-form").elements.id.value;
  if (id && confirm("Delete this hierarchy item and its children/use cases?")) {
    await api(`/api/nodes/${id}`, { method: "DELETE" });
    hideModal("#nodeModal");
    await refresh();
  }
});

qs("#delete-pillar").addEventListener("click", async () => {
  const id = qs("#pillar-form").elements.id.value;
  if (id && confirm("Delete this pillar label?")) {
    await api(`/api/pillars/${id}`, { method: "DELETE" });
    hideModal("#pillarModal");
    await refresh();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") qsa(".modal.show").forEach((modal) => hideModal(modal));
});

setActiveSection(qs(".app-section.active")?.id || "dashboard");
refresh();
