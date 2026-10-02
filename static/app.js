/**
 * BOMARE FACTORY LINE BALANCER & SIMULATOR - CLIENT APPLICATION
 * High-performance UI logic for assembly line configuration, real-time DAG rendering,
 * and integration with the Simulated Annealing + Discrete Event Simulation engine.
 */

// Application State
const state = {
  table: [],
  templates: {},
  currentResult: null,
  rawTaskTimes: [],
  activeCandidateIndex: 0,
};

// DOM Elements Cache
const elements = {
  csvFileInput: document.getElementById('csvFileInput'),
  btnClearTable: document.getElementById('btnClearTable'),

  // Production Parameters
  inputWorkers: document.getElementById('inputWorkers'),
  inputTargetQty: document.getElementById('inputTargetQty'),
  inputShiftDuration: document.getElementById('inputShiftDuration'),
  inputUnitPrice: document.getElementById('inputUnitPrice'),
  inputWorkerSalary: document.getElementById('inputWorkerSalary'),
  inputWipCost: document.getElementById('inputWipCost'),

  // Table box: collapse toggle + print/export
  tableToggleBtn: document.getElementById('tableToggleBtn'),
  tableBodyWrap: document.getElementById('tableBodyWrap'),
  tableViewContainer: document.getElementById('tableViewContainer'),
  btnExportCsv: document.getElementById('btnExportCsv'),
  btnPrintTable: document.getElementById('btnPrintTable'),

  // Graph box: collapse toggle
  graphToggleBtn: document.getElementById('graphToggleBtn'),
  graphBodyWrap: document.getElementById('graphBodyWrap'),

  // Table
  tableBody: document.getElementById('tableBody'),
  rowCountBadge: document.getElementById('rowCountBadge'),
  btnAddTaskRow: document.getElementById('btnAddTaskRow'),
  btnAddResourceRow: document.getElementById('btnAddResourceRow'),
  btnValidateGraph: document.getElementById('btnValidateGraph'),
  validationBanner: document.getElementById('validationBanner'),
  bannerMessage: document.getElementById('bannerMessage'),
  btnCloseBanner: document.getElementById('btnCloseBanner'),

  // DAG Flow
  topoOrderText: document.getElementById('topoOrderText'),
  dagSvg: document.getElementById('dagSvg'),
  dagNodesLayer: document.getElementById('dagNodesLayer'),
  dagFlowContainer: document.getElementById('dagFlowContainer'),

  // Execution Action
  btnRunOptimization: document.getElementById('btnRunOptimization'),

  // Results
  resultsSection: document.getElementById('resultsSection'),
  resultFlagBanner: document.getElementById('resultFlagBanner'),
  kpiUnits: document.getElementById('kpiUnits'),
  kpiUnitsSub: document.getElementById('kpiUnitsSub'),
  kpiWorkers: document.getElementById('kpiWorkers'),
  kpiWorkersSub: document.getElementById('kpiWorkersSub'),
  kpiEfficiency: document.getElementById('kpiEfficiency'),
  kpiWip: document.getElementById('kpiWip'),
  kpiBottleneck: document.getElementById('kpiBottleneck'),
  kpiBottleneckSub: document.getElementById('kpiBottleneckSub'),
    kpiBottleneckSub: document.getElementById('kpiBottleneckSub'),
  kpiTotalTime: document.getElementById('kpiTotalTime'),
  kpiFitness: document.getElementById('kpiFitness'),
  stationCardsGrid: document.getElementById('stationCardsGrid'),
  stationCountSummary: document.getElementById('stationCountSummary'),
  rawTaskCycleChart: document.getElementById('rawTaskCycleChart'),
  groupedStationCycleChart: document.getElementById('groupedStationCycleChart'),
  candidateTabsContainer: document.getElementById('candidateTabsContainer'),
  candidateTabsList: document.getElementById('candidateTabsList'),
  historyGraphWrap: document.getElementById('historyGraphWrap'),
  convergenceSvg: document.getElementById('convergenceSvg'),

  // Loading
  loadingOverlay: document.getElementById('loadingOverlay'),
  loadingTitle: document.getElementById('loadingTitle'),
  loadingSubtitle: document.getElementById('loadingSubtitle'),
};

// Warn (once, in the console) about any element the script expects but the HTML lacks.
Object.entries(elements).forEach(([key, el]) => {
  if (!el) console.warn(`[Bomare UI] Missing HTML element for elements.${key}`);
});

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {
  setupEventListeners();
  await loadTemplates();
  loadTemplate('bomare_tv_line');
});

function setupEventListeners() {
  // Table box chevron: click toggles the table open/closed
  elements.tableToggleBtn.addEventListener('click', () => toggleCollapse(elements.tableToggleBtn, elements.tableBodyWrap));

  // Graph box chevron: click toggles the graph open/closed
  elements.graphToggleBtn.addEventListener('click', () => toggleCollapse(elements.graphToggleBtn, elements.graphBodyWrap));

  // Table row additions
  elements.btnAddTaskRow.addEventListener('click', () => addRow('Task'));
  elements.btnAddResourceRow.addEventListener('click', () => addRow('Resource'));
  elements.btnClearTable.addEventListener('click', clearTable);
  elements.btnValidateGraph.addEventListener('click', () => validateTable(true));

  // CSV import / export / print
  elements.csvFileInput.addEventListener('change', importCsv);
  elements.btnExportCsv.addEventListener('click', exportCsv);
  elements.btnPrintTable.addEventListener('click', printTable);

  // Banner close
  elements.btnCloseBanner.addEventListener('click', () => {
    elements.validationBanner.classList.add('hidden');
  });

  // Action Trigger (Simulated Annealing + Discrete Event Simulator)
  elements.btnRunOptimization.addEventListener('click', runFullOptimization);

  // Window resize re-draws DAG SVG arrows
  window.addEventListener('resize', debounce(renderDagLines, 200));
}

// One click hides/reveals a box's body and flips its chevron. Used by both
// the table box and the graph box -- the card itself always stays visible,
// only its body collapses.
function toggleCollapse(btn, bodyEl) {
  const isExpanded = btn.getAttribute('aria-expanded') !== 'false';
  const next = !isExpanded;
  btn.setAttribute('aria-expanded', String(next));
  bodyEl.classList.toggle('hidden', !next);
  if (next && bodyEl === elements.graphBodyWrap) {
    requestAnimationFrame(() => renderDagLines());
  }
}

// Opens a print-only window containing just the assembly line table.
function printTable() {
  const win = window.open('', '_blank', 'width=1000,height=700');
  if (!win) return;
  win.document.write(`
    <!DOCTYPE html>
    <html>
      <head>
        <title>Assembly Line Table</title>
        <style>
          body { font-family: 'Plus Jakarta Sans', Arial, sans-serif; padding: 24px; color: #111; }
          h1 { font-size: 16px; margin-bottom: 12px; }
          table { width: 100%; border-collapse: collapse; font-size: 12px; }
          th, td { border: 1px solid #ccc; padding: 6px 8px; text-align: left; }
          th { background: #f2f2f2; }
        </style>
      </head>
      <body>
        <h1>Assembly Line Table</h1>
        ${elements.tableViewContainer.querySelector('table').outerHTML}
      </body>
    </html>
  `);
  win.document.close();
  win.focus();
  win.print();
}

// ---------------------------------------------------------------------------
// Templates Loading
// ---------------------------------------------------------------------------
async function loadTemplates() {
  try {
    const res = await fetch('/api/templates');
    const data = await res.json();
    if (data.status === 'success') {
      state.templates = data.templates;
    }
  } catch (err) {
    console.error('Failed to fetch templates:', err);
  }
}

function loadTemplate(key) {
  const template = state.templates[key];
  if (!template) return;

  // Set parameters
  const p = template.parameters || {};
  elements.inputWorkers.value = p.available_workers || '';
  elements.inputTargetQty.value = p.target_quantity || '';
  elements.inputShiftDuration.value = p.shift_hours ?? '';
  if (p.unit_price != null) elements.inputUnitPrice.value = p.unit_price;
  if (p.worker_salary != null) elements.inputWorkerSalary.value = p.worker_salary;
  if (p.wip_holding_cost != null) elements.inputWipCost.value = p.wip_holding_cost;

  // Deep copy table rows
  state.table = JSON.parse(JSON.stringify(template.table));
  renderTable();
  validateTable(false);

  showBanner('success', `Loaded preset: "${template.name}". ${template.description}`);
}

// ---------------------------------------------------------------------------
// Table Rendering & Row Mutations
// ---------------------------------------------------------------------------
function renderTable() {
  elements.tableBody.innerHTML = '';
  elements.rowCountBadge.textContent = `${state.table.length} rows`;

  state.table.forEach((row, index) => {
    const tr = document.createElement('tr');
    tr.id = `row-${index}`;
    if (row.Type === 'Resource') {
      tr.classList.add('row-resource');
    }

    let relationStr = '';
    if (Array.isArray(row.Relation)) {
      relationStr = row.Relation.join(', ');
    } else if (row.Relation !== null && row.Relation !== undefined) {
      relationStr = String(row.Relation);
    }

    const isResource = row.Type === 'Resource';
    const isMachine = row.operation_type === 'machine';

    tr.innerHTML = `
      <td>
        <input type="text" class="tbl-input font-mono" data-col="Id" value="${escapeHtml(String(row.Id))}">
      </td>
      <td>
        <select class="type-select ${isResource ? 'type-resource' : 'type-task'}" data-col="Type">
          <option value="Task" ${!isResource ? 'selected' : ''}>Task</option>
          <option value="Resource" ${isResource ? 'selected' : ''}>Resource</option>
        </select>
      </td>
      <td>
        <input type="text" class="tbl-input font-mono" data-col="Relation" 
               placeholder="e.g. 1, 2 or R1" value="${escapeHtml(relationStr)}">
      </td>
      <td>
        <input type="number" class="tbl-input text-right" data-col="CycleTime" 
               step="1" min="0" value="${row.CycleTime}" ${isResource ? 'disabled' : ''}>
      </td>
      <td>
        <input type="number" class="tbl-input text-right" data-col="Quantity" 
               min="1" step="1" value="${row.Quantity || 1}">
      </td>
      <td>
        <input type="number" class="tbl-input text-right" data-col="Available" 
               min="0" step="1" value="${row.Available || 0}" ${!isResource ? 'disabled' : ''}>
      </td>
      <td>
        <select class="op-select" data-col="operation_type" ${isResource ? 'disabled' : ''}>
          <option value="manual" ${row.operation_type === 'manual' ? 'selected' : ''}>Manual</option>
          <option value="machine" ${row.operation_type === 'machine' ? 'selected' : ''}>Machine</option>
        </select>
      </td>
      <td>
        <input type="number" class="tbl-input text-center" data-col="min_operators" 
               min="1" max="10" step="1" value="${isResource ? 0 : (row.min_operators || 1)}" 
               ${isResource ? 'disabled' : ''}>
      </td>
      <td>
        <input type="number" class="tbl-input text-center" data-col="max_operators" 
               min="1" max="10" step="1" value="${isResource ? 0 : (row.max_operators || 1)}" 
               ${isResource || isMachine ? 'disabled' : ''}>
      </td>
      <td class="tbl-btn-cell">
        <button class="tbl-text-btn" title="Duplicate row" onclick="duplicateRow(${index})">Copy</button>
        <button class="tbl-text-btn tbl-text-btn-del" title="Delete row" onclick="deleteRow(${index})">Delete</button>
      </td>
    `;

    // Attach row input listeners
    tr.querySelectorAll('input, select').forEach((input) => {
      input.addEventListener('change', (e) => handleCellChange(index, e.target));
      input.addEventListener('blur', debounce(() => validateTable(false), 300));
    });

    elements.tableBody.appendChild(tr);
  });

  renderDagFlow();
}

function handleCellChange(rowIndex, input) {
  const col = input.dataset.col;
  let val = input.value.trim();
  const row = state.table[rowIndex];

  if (col === 'Id') {
    row.Id = val;
  } else if (col === 'Type') {
    row.Type = val;
    if (val === 'Resource') {
      row.CycleTime = 0.0;
      row.min_operators = 0;
      row.max_operators = 0;
      if (!row.Available || row.Available <= 0) row.Available = 1000;
    } else {
      if (row.CycleTime <= 0) row.CycleTime = 120;
      if (row.min_operators <= 0) row.min_operators = 1;
      if (row.max_operators <= 0) row.max_operators = 1;
      row.Available = 0;
    }
    renderTable();
    return;
  } else if (col === 'Relation') {
    row.Relation = val
      ? val.split(',').map((x) => x.trim().replace(/[\[\]'"]/g, '')).filter(Boolean)
      : [];
  } else if (col === 'CycleTime') {
    row.CycleTime = parseFloat(val) || 0.0;
  } else if (col === 'Quantity') {
    row.Quantity = parseInt(val, 10) || 1;
  } else if (col === 'Available') {
    row.Available = parseFloat(val) || 0.0;
  } else if (col === 'operation_type') {
    row.operation_type = val;
    if (val === 'machine') {
      row.max_operators = row.min_operators;
    }
    renderTable();
    return;
  } else if (col === 'min_operators') {
    row.min_operators = parseInt(val, 10) || 1;
    if (row.max_operators < row.min_operators || row.operation_type === 'machine') {
      row.max_operators = row.min_operators;
    }
    renderTable();
    return;
  } else if (col === 'max_operators') {
    row.max_operators = Math.max(parseInt(val, 10) || 1, row.min_operators);
  }

  renderDagFlow();
}

function addRow(type = 'Task') {
  const newIndex = state.table.length + 1;
  const newId = type === 'Task' ? `T${newIndex}` : `R${newIndex}`;

  const newRow = {
    Id: newId,
    Type: type,
    Relation: [],
    CycleTime: type === 'Task' ? 120 : 0.0,
    Quantity: 1,
    Available: type === 'Resource' ? 1000 : 0,
    operation_type: 'manual',
    min_operators: type === 'Task' ? 1 : 0,
    max_operators: type === 'Task' ? 1 : 0,
  };

  state.table.push(newRow);
  renderTable();
  validateTable(false);
}

function duplicateRow(index) {
  const source = state.table[index];
  const clone = JSON.parse(JSON.stringify(source));
  clone.Id = `${clone.Id}_COPY`;
  state.table.splice(index + 1, 0, clone);
  renderTable();
  validateTable(false);
}

function deleteRow(index) {
  state.table.splice(index, 1);
  renderTable();
  validateTable(false);
}

function clearTable() {
  if (confirm('Are you sure you want to clear all rows from the table?')) {
    state.table = [];
    renderTable();
    validateTable(false);
    elements.resultsSection.classList.add('hidden');
  }
}

// ---------------------------------------------------------------------------
// Table & Graph Validation
// ---------------------------------------------------------------------------
async function validateTable(showSuccessBanner = true) {
  if (state.table.length === 0) {
    showBanner('error', 'The assembly line table is empty. Please add rows or load a preset template.');
    return false;
  }

  try {
    const res = await fetch('/api/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ table: state.table }),
    });

    const data = await res.json();

    if (!data.is_valid) {
      showBanner('error', data.errors.join('<br>'));
      elements.topoOrderText.textContent = 'Invalid DAG detected';
      elements.topoOrderText.style.color = '#dc2626';
      return false;
    }

    const topo = data.summary.topological_order || [];
    elements.topoOrderText.textContent = `${topo.join(' → ')}`;
    elements.topoOrderText.style.color = 'var(--text-secondary)';

    if (showSuccessBanner) {
      const warns = data.warnings && data.warnings.length > 0 ? `<br>Notice: ${data.warnings.join('; ')}` : '';
      showBanner('success', `Line DAG is valid: ${data.summary.tasks_count} tasks, ${data.summary.resources_count} resources. Work content: ${data.summary.total_work_content} s.${warns}`);
    } else if (data.warnings && data.warnings.length > 0) {
      showBanner('success', `Notice: ${data.warnings.join('; ')}`);
    } else {
      elements.validationBanner.classList.add('hidden');
    }

    return true;
  } catch (err) {
    showBanner('error', `Connection error during validation: ${err.message}`);
    return false;
  }
}

function showBanner(type, message) {
  elements.validationBanner.className = `validation-banner ${type === 'error' ? 'banner-error' : 'banner-success'}`;
  elements.bannerMessage.innerHTML = message;
  elements.validationBanner.classList.remove('hidden');
}

// ---------------------------------------------------------------------------
// Visual DAG Flow Precedence Rendering
// ---------------------------------------------------------------------------
function renderDagFlow() {
  elements.dagNodesLayer.innerHTML = '';
  elements.dagSvg.innerHTML = '';

  if (state.table.length === 0) return;

  const idMap = new Map();
  state.table.forEach((r) => idMap.set(String(r.Id), r));

  const depths = new Map();
  function getDepth(id, visited = new Set()) {
    if (depths.has(id)) return depths.get(id);
    if (visited.has(id)) return 0;
    visited.add(id);

    const r = idMap.get(id);
    if (!r || !r.Relation || r.Relation.length === 0) {
      depths.set(id, 0);
      return 0;
    }

    let maxPredDepth = 0;
    for (const p of r.Relation) {
      maxPredDepth = Math.max(maxPredDepth, getDepth(String(p), new Set(visited)) + 1);
    }
    depths.set(id, maxPredDepth);
    return maxPredDepth;
  }

  state.table.forEach((r) => getDepth(String(r.Id)));

  const layerMap = new Map();
  state.table.forEach((r) => {
    const d = depths.get(String(r.Id)) || 0;
    if (!layerMap.has(d)) layerMap.set(d, []);
    layerMap.get(d).push(r);
  });

  const sortedDepths = Array.from(layerMap.keys()).sort((a, b) => a - b);

  sortedDepths.forEach((d) => {
    const colDiv = document.createElement('div');
    colDiv.className = 'dag-node-col';

    layerMap.get(d).forEach((item) => {
      const card = document.createElement('div');
      card.id = `dag-node-${item.Id}`;
      card.className = `dag-node-card ${item.Type === 'Resource' ? 'dag-node-resource' : 'dag-node-task'}`;

      const isRes = item.Type === 'Resource';
      card.innerHTML = `
        <div class="dag-node-id">${escapeHtml(String(item.Id))}</div>
        <div class="dag-node-meta">
          <span>${isRes ? 'Stock: ' + item.Available : item.CycleTime + 's'}</span>
          <span>${isRes ? 'Resource' : item.operation_type}</span>
        </div>
      `;
      colDiv.appendChild(card);
    });

    elements.dagNodesLayer.appendChild(colDiv);
  });

  requestAnimationFrame(() => renderDagLines());
}

function renderDagLines() {
  elements.dagSvg.innerHTML = '';
  const containerRect = elements.dagFlowContainer.getBoundingClientRect();

  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  defs.innerHTML = `
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#4f46e5" />
    </marker>
  `;
  elements.dagSvg.appendChild(defs);

  state.table.forEach((child) => {
    const childEl = document.getElementById(`dag-node-${child.Id}`);
    if (!childEl) return;
    const childRect = childEl.getBoundingClientRect();
    const childX = childRect.left - containerRect.left;
    const childY = childRect.top - containerRect.top + childRect.height / 2;

    const relations = Array.isArray(child.Relation) ? child.Relation : [child.Relation];
    relations.forEach((parentId) => {
      const parentEl = document.getElementById(`dag-node-${parentId}`);
      if (!parentEl) return;
      const parentRect = parentEl.getBoundingClientRect();
      const parentX = parentRect.right - containerRect.left;
      const parentY = parentRect.top - containerRect.top + parentRect.height / 2;

      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      const dx = Math.max(40, (childX - parentX) / 2);
      const d = `M ${parentX} ${parentY} C ${parentX + dx} ${parentY}, ${childX - dx} ${childY}, ${childX} ${childY}`;

      path.setAttribute('d', d);
      path.setAttribute('fill', 'none');
      path.setAttribute('stroke', '#4f46e5');
      path.setAttribute('stroke-width', '1.5');
      path.setAttribute('stroke-opacity', '0.7');
      path.setAttribute('marker-end', 'url(#arrow)');

      elements.dagSvg.appendChild(path);
    });
  });
}

// ---------------------------------------------------------------------------
// Execution: Full Simulated Annealing & Next-Event Simulation
// ---------------------------------------------------------------------------
async function runFullOptimization() {
  const isValid = await validateTable(false);
  if (!isValid) {
    // The error banner lives next to the table; bring it into view so the click never looks dead.
    elements.validationBanner.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }

  const { params, errors } = getGlobalParams();
  if (errors.length > 0) {
    showBanner('error', errors.join('<br>'));
    elements.validationBanner.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }

  showLoading('Running Simulated Annealing & Discrete Event Simulation...', 'Compiling line layout, evaluating stochastic cycle times and inventory buffers');

  const rawTaskTimes = state.table
    .filter((row) => row.Type === 'Task')
    .map((row) => ({ label: row.Id, value: row.CycleTime }));
  const payload = {
    table: state.table,
    params,
  };

  try {
    const res = await fetch('/api/optimize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    hideLoading();

    if (data.status !== 'success') {
      showBanner('error', data.errors ? data.errors.join('<br>') : 'Optimization engine failed.');
      return;
    }

    displayOptimizationResults(data, rawTaskTimes);
  } catch (err) {
    hideLoading();
    showBanner('error', `Optimization engine error: ${err.message}`);
  }
}

// 0.34 -> "340 ms", 2.8 -> "2.80 s", 75 -> "1 min 15 s"
function formatDuration(sec) {
  if (sec < 1) return `${Math.round(sec * 1000)} ms`;
  if (sec < 60) return `${sec.toFixed(2)} s`;
  const m = Math.floor(sec / 60);
  return `${m} min ${Math.round(sec - m * 60)} s`;
}

function displayOptimizationResults(data, rawTaskTimes) {
  state.rawTaskTimes = rawTaskTimes || [];
  elements.resultsSection.classList.remove('hidden');

  // Format clean flag without emojis
  let rawFlag = data.flag || '';
  let cleanFlag = rawFlag.replace(/[🟢🔴🟡]/g, '').trim();

  elements.resultFlagBanner.textContent = cleanFlag;
  if (rawFlag.includes('OPTIMAL')) {
    elements.resultFlagBanner.className = 'result-flag-badge flag-optimal';
  } else if (rawFlag.includes('OVERFLOW')) {
    elements.resultFlagBanner.className = 'result-flag-badge flag-overflow';
  } else {
    elements.resultFlagBanner.className = 'result-flag-badge flag-deficit';
  }

  const m = data.metrics || {};
  elements.kpiUnits.textContent = Math.round(m.units || 0);
  elements.kpiUnitsSub.textContent = `Target: ${elements.inputTargetQty.value || 'None'} units`;
  elements.kpiWorkers.textContent = m.workers || 0;
  elements.kpiWorkersSub.textContent = `Budget: ${elements.inputWorkers.value || 'None'} workers`;
  elements.kpiEfficiency.textContent = `${(m.efficiency_pct || 0).toFixed(1)}%`;
  elements.kpiWip.textContent = (m.avg_wip || 0).toFixed(1);
  elements.kpiFitness.textContent = `$${(m.fitness || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  const stations = data.best_layout || [];
  let maxTime = 0;
  stations.forEach((st) => {
    if (st.station_time > maxTime) maxTime = st.station_time;
  });
  elements.kpiBottleneck.textContent = `${maxTime.toFixed(1)} s`;
  elements.kpiBottleneckSub.textContent = `Slowest Station Cycle`;
  elements.kpiTotalTime.textContent = formatDuration(data.total_time_sec || 0);
  renderStationCards(stations);
  renderCycleTimeCharts(rawTaskTimes, stations);

  // Alternatives tabs if present
  if (data.alternatives && data.alternatives.length > 0) {
    const allCands = [
      { candidate_id: 1, label: 'Optimal Found', stations, metrics: m },
      ...data.alternatives.map((alt, i) => ({
        candidate_id: i + 2,
        label: `Alternative #${i + 1}`,
        stations: alt.stations,
        metrics: alt,
      })),
    ];
    renderCandidateTabs(allCands, (c) => {
      renderStationCards(c.stations);
      renderCycleTimeCharts(state.rawTaskTimes, c.stations);
      elements.kpiBottleneck.textContent = `${Math.max(0, ...c.stations.map((s) => s.station_time || 0)).toFixed(1)} s`;
      if (c.metrics) {
        elements.kpiUnits.textContent = Math.round(c.metrics.units || 0);
        elements.kpiWorkers.textContent = c.metrics.workers || 0;
        elements.kpiEfficiency.textContent = `${(c.metrics.efficiency_pct || 0).toFixed(1)}%`;
        elements.kpiWip.textContent = (c.metrics.avg_wip || 0).toFixed(1);
        elements.kpiFitness.textContent = `$${(c.metrics.fitness || 0).toFixed(2)}`;
      }
    });
  } else {
    elements.candidateTabsContainer.classList.add('hidden');
  }

  // Annealing Convergence Graph
  if (data.history && data.history.length > 0) {
    elements.historyGraphWrap.classList.remove('hidden');
    drawConvergenceChart(data.history);
  } else {
    elements.historyGraphWrap.classList.add('hidden');
  }

  elements.resultsSection.scrollIntoView({ behavior: 'smooth' });
}

// ---------------------------------------------------------------------------
// Station Cards & Allocation Renderer
// ---------------------------------------------------------------------------
function renderStationCards(stations) {
  elements.stationCardsGrid.innerHTML = '';
  elements.stationCountSummary.textContent = `${stations.length} Workstations`;

  stations.forEach((st, idx) => {
    const card = document.createElement('div');
    card.className = 'station-card';

    const stId = st.station_id || idx + 1;
    const stTime = (st.station_time || 0).toFixed(2);
    const headcount = st.headcount || st.required_workers || 0;
    const busyPct = st.busy_pct !== undefined ? st.busy_pct : null;
    const starvedPct = st.starved_pct !== undefined ? st.starved_pct : null;

    let tasksHtml = '';
    const taskList = st.tasks || [];
    const pFactors = st.workers_per_task || st.p_factors || {};

    taskList.forEach((t) => {
      const ops = pFactors[t] || 1;
      tasksHtml += `
        <div class="station-task-item">
          <span class="station-task-name">${escapeHtml(String(t))}</span>
          <span class="station-task-workers">${ops} ${ops === 1 ? 'worker' : 'workers'}</span>
        </div>
      `;
    });

    let barHtml = '';
    if (busyPct !== null) {
      barHtml = `
        <div class="station-bars">
          <div class="bar-meta">
            <span>Busy Utilization</span>
            <strong class="highlight-red">${busyPct.toFixed(1)}%</strong>
          </div>
          <div class="progress-track" title="Busy: ${busyPct.toFixed(1)}%, Starved: ${(starvedPct || 0).toFixed(1)}%">
            <div class="progress-busy" style="width: ${Math.min(100, busyPct)}%;"></div>
            <div class="progress-starved" style="width: ${Math.min(100 - busyPct, starvedPct || 0)}%;"></div>
          </div>
        </div>
      `;
    }

    card.innerHTML = `
      <div class="station-card-header">
        <span class="station-card-title">Workstation ${stId}</span>
        <span class="station-time-badge">${stTime} s</span>
      </div>
      <div class="station-card-body">
        <div class="station-headcount">
          <span>Station Headcount:</span>
          <span class="headcount-badge">${headcount} Workers</span>
        </div>
        <div class="station-task-list">
          ${tasksHtml}
        </div>
        ${barHtml}
      </div>
    `;

    elements.stationCardsGrid.appendChild(card);
  });
}

// ---------------------------------------------------------------------------
// Cycle-time comparison: raw tasks (before grouping) vs stations (after grouping)
// ---------------------------------------------------------------------------
function stationCycleItems(stations) {
  return (stations || []).map((station, index) => {
    const workers = station.headcount || station.required_workers || 0;
    const tasks = (station.tasks || []).join(', ');
    return {
      label: `Station ${station.station_id || station.station_index || index + 1}`,
      value: station.station_time,
      tip: `Tasks: ${tasks} | ${workers} ${workers === 1 ? 'worker' : 'workers'}`,
    };
  });
}

function renderCycleTimeCharts(rawTaskTimes, stations) {
  const stationTimes = stationCycleItems(stations);

  // Both charts share the SAME y-axis maximum so bar heights are directly comparable.
  const allValues = [...(rawTaskTimes || []), ...stationTimes]
    .map((i) => Number(i.value))
    .filter((v) => Number.isFinite(v) && v >= 0);
  const sharedMax = allValues.length ? Math.max(...allValues) : 0;

  renderCycleTimeBarChart(elements.rawTaskCycleChart, rawTaskTimes, 'Raw task cycle time', 'Task', 'raw', { sharedMax });
  renderCycleTimeBarChart(elements.groupedStationCycleChart, stationTimes, 'Grouped station cycle time', 'Station', 'grouped', { sharedMax });
}

function renderCycleTimeBarChart(container, items, chartLabel, xAxisLabel, barType, opts = {}) {
  const data = (items || []).map((item) => ({
    label: String(item.label),
    value: Number(item.value),
    tip: item.tip ? String(item.tip) : '',
  })).filter((item) => Number.isFinite(item.value) && item.value >= 0);

  if (!data.length) {
    container.innerHTML = '<div class="cycle-time-chart-empty">No cycle-time data available.</div>';
    return;
  }

  const containerWidth = Math.ceil(container.getBoundingClientRect().width) || 640;
  const width = Math.max(containerWidth - 16, data.length * 48 + 76, 480);
  const height = 280;
  const pad = { top: 28, right: 18, bottom: 42, left: 56 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const dataMax = Math.max(...data.map((item) => item.value), 0);
  const dataMin = Math.min(...data.map((item) => item.value));
  const dataAvg = data.reduce((sum, item) => sum + item.value, 0) / data.length;
  // shared scale (both charts) if provided, otherwise this chart's own maximum
  const maxValue = Math.max(dataMax, Number(opts.sharedMax) || 0);
  const roughStep = maxValue / 5 || 1;
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const normalizedStep = roughStep / magnitude;
  const stepFactor = normalizedStep <= 1 ? 1 : normalizedStep <= 2 ? 2 : normalizedStep <= 5 ? 5 : 10;
  const step = stepFactor * magnitude;
  const axisMax = Math.ceil(maxValue / step) * step || step;
  const tickCount = Math.round(axisMax / step);
  const slotWidth = plotWidth / data.length;
  const barWidth = Math.min(40, slotWidth * 0.62);
  const formatValue = (value) => Number(value.toFixed(2)).toString();
  const yOf = (value) => pad.top + plotHeight - (value / axisMax) * plotHeight;

  const grid = Array.from({ length: tickCount + 1 }, (_, index) => {
    const value = index * step;
    const y = yOf(value);
    return `
      <line class="cycle-time-grid-line" x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}"></line>
      <text class="cycle-time-tick" x="${pad.left - 9}" y="${y + 3}" text-anchor="end">${formatValue(value)}</text>
    `;
  }).join('');

  const bars = data.map((item, index) => {
    const center = pad.left + slotWidth * (index + 0.5);
    const barHeight = (item.value / axisMax) * plotHeight;
    const barY = yOf(item.value);
    const shortLabel = item.label.length > 9 ? `${item.label.slice(0, 8)}...` : item.label;
    const safeLabel = escapeHtml(item.label);
    const tip = item.tip ? `\n${escapeHtml(item.tip)}` : '';
    // the slowest bar is flagged, unless everything is perfectly equal
    const isBottleneck = item.value === dataMax && dataMax > dataMin;
    return `
      <g class="cycle-time-bar-group">
        <title>${safeLabel}: ${formatValue(item.value)} seconds${tip}</title>
        <rect class="cycle-time-bar ${barType}${isBottleneck ? ' bottleneck' : ''}" x="${center - barWidth / 2}" y="${barY}" width="${barWidth}" height="${barHeight}" rx="2"></rect>
        <text class="cycle-time-value" x="${center}" y="${Math.max(pad.top - 5, barY - 7)}" text-anchor="middle">${formatValue(item.value)}</text>
        <text class="cycle-time-x-label" x="${center}" y="${height - pad.bottom + 18}" text-anchor="middle"><title>${safeLabel}</title>${escapeHtml(shortLabel)}</text>
      </g>
    `;
  }).join('');

  // reference lines: slowest item (bottleneck) and average
  const refLines = `
    <line class="cycle-time-ref-line avg" x1="${pad.left}" y1="${yOf(dataAvg)}" x2="${width - pad.right}" y2="${yOf(dataAvg)}"><title>Average: ${formatValue(dataAvg)} s</title></line>
    <line class="cycle-time-ref-line max" x1="${pad.left}" y1="${yOf(dataMax)}" x2="${width - pad.right}" y2="${yOf(dataMax)}"><title>Slowest: ${formatValue(dataMax)} s</title></line>
  `;

  container.innerHTML = `
    <svg class="cycle-time-svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${chartLabel} bar chart">
      <text class="cycle-time-y-title" transform="translate(16 ${height / 2}) rotate(-90)" text-anchor="middle">Cycle time (seconds)</text>
      ${grid}
      <line class="cycle-time-axis-line" x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}"></line>
      ${bars}
      ${refLines}
      <text class="cycle-time-x-title" x="${pad.left + plotWidth / 2}" y="${height - 4}" text-anchor="middle">${xAxisLabel}</text>
    </svg>
  `;
}

function renderCandidateTabs(candidates, onSelect) {
  elements.candidateTabsContainer.classList.remove('hidden');
  elements.candidateTabsList.innerHTML = '';

  candidates.forEach((cand, idx) => {
    const btn = document.createElement('button');
    btn.className = `tab-btn ${idx === 0 ? 'active' : ''}`;
    btn.textContent = cand.label || `Layout #${cand.candidate_id || idx + 1}`;
    btn.addEventListener('click', () => {
      elements.candidateTabsList.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      onSelect(cand);
    });
    elements.candidateTabsList.appendChild(btn);
  });
}

// ---------------------------------------------------------------------------
// Convergence Chart (SVG Drawing)
// ---------------------------------------------------------------------------
function drawConvergenceChart(history) {
  elements.convergenceSvg.innerHTML = '';
  if (!history || history.length < 2) return;

  const width = 800;
  const height = 180;
  const pad = { top: 20, right: 30, bottom: 30, left: 60 };

  const minVal = Math.min(...history);
  const maxVal = Math.max(...history);
  const range = maxVal - minVal || 1;

  const points = history.map((val, i) => {
    const x = pad.left + (i / (history.length - 1)) * (width - pad.left - pad.right);
    const y = height - pad.bottom - ((val - minVal) / range) * (height - pad.top - pad.bottom);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
  polyline.setAttribute('points', points.join(' '));
  polyline.setAttribute('fill', 'none');
  polyline.setAttribute('stroke', '#4f46e5');
  polyline.setAttribute('stroke-width', '2.5');

  const axes = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  axes.setAttribute('d', `M ${pad.left} ${pad.top} L ${pad.left} ${height - pad.bottom} L ${width - pad.right} ${height - pad.bottom}`);
  axes.setAttribute('fill', 'none');
  axes.setAttribute('stroke', '#e5e7ef');
  axes.setAttribute('stroke-width', '1.5');

  const textMax = createSvgText(pad.left - 8, pad.top + 5, `$${maxVal.toFixed(0)}`, 'end');
  const textMin = createSvgText(pad.left - 8, height - pad.bottom, `$${minVal.toFixed(0)}`, 'end');
  const textIter = createSvgText(width - pad.right, height - 10, `${history.length} iterations`, 'end');

  elements.convergenceSvg.appendChild(axes);
  elements.convergenceSvg.appendChild(polyline);
  elements.convergenceSvg.appendChild(textMax);
  elements.convergenceSvg.appendChild(textMin);
  elements.convergenceSvg.appendChild(textIter);
}

function createSvgText(x, y, text, align = 'start') {
  const el = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  el.setAttribute('x', x);
  el.setAttribute('y', y);
  el.setAttribute('fill', '#6b7280');
  el.setAttribute('font-size', '10');
  el.setAttribute('font-family', 'JetBrains Mono, monospace');
  el.setAttribute('text-anchor', align);
  el.textContent = text;
  return el;
}

// ---------------------------------------------------------------------------
// CSV Import / Export
// ---------------------------------------------------------------------------
async function exportCsv() {
  if (state.table.length === 0) {
    showBanner('error', 'Cannot export an empty table.');
    return;
  }

  try {
    const res = await fetch('/api/export-csv', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ table: state.table }),
    });

    if (!res.ok) throw new Error('Export failed');
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `assembly_line_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    showBanner('error', `CSV Export error: ${err.message}`);
  }
}

async function importCsv(e) {
  const file = e.target.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append('file', file);

  showLoading('Importing CSV...', 'Parsing and verifying table structure');

  try {
    const res = await fetch('/api/import-csv', {
      method: 'POST',
      body: formData,
    });

    const data = await res.json();
    hideLoading();
    elements.csvFileInput.value = '';

    if (data.status !== 'success') {
      showBanner('error', data.errors ? data.errors.join('<br>') : 'CSV Import failed.');
      return;
    }

    state.table = data.table;
    renderTable();
    validateTable(false);
    showBanner('success', `Imported ${data.table.length} rows from CSV.`);
  } catch (err) {
    hideLoading();
    elements.csvFileInput.value = '';
    showBanner('error', `CSV Import error: ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
// Reads the parameter inputs exactly as typed -- nothing is silently replaced by a default.
// Returns { params, errors }. Unit revenue, worker salary, WIP cost and shift duration are required.
function readNumber(el) {
  const raw = el.value.trim();
  if (raw === '') return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function getGlobalParams() {
  const errors = [];
  const shift = readNumber(elements.inputShiftDuration);
  const price = readNumber(elements.inputUnitPrice);
  const salary = readNumber(elements.inputWorkerSalary);
  const wip = readNumber(elements.inputWipCost);

  if (shift === null || shift <= 0) errors.push('Shift Duration is required (in hours) and must be greater than 0.');
  if (price === null || price < 0) errors.push('Unit Revenue is required (0 or more).');
  if (salary === null || salary < 0) errors.push('Worker Salary is required (0 or more).');
  if (wip === null || wip < 0) errors.push('WIP Holding Cost is required (0 or more).');

  const workers = readNumber(elements.inputWorkers);
  const target = readNumber(elements.inputTargetQty);

  return {
    errors,
    params: {
      available_workers: workers,   // optional -> null when empty
      target_quantity: target,      // optional -> null when empty
      shift_hours: shift,           // hours; the backend converts to seconds
      unit_price: price,
      worker_salary: salary,
      wip_holding_cost: wip,
    },
  };
}

function showLoading(title, subtitle) {
  elements.loadingTitle.textContent = title;
  elements.loadingSubtitle.textContent = subtitle;
  elements.loadingOverlay.classList.remove('hidden');
}

function hideLoading() {
  elements.loadingOverlay.classList.add('hidden');
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function debounce(func, wait) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}