/*
 * pipeline_diagram.js — Live-Pipeline-Visualisierung (Augmented Tool Path Blackbox fix)
 *
 * Renders a fixed two-branch node/edge diagram (interactive LangGraph pipeline
 * vs. agentic tool-calling path) and colors nodes/edges by the most recent
 * stage-trace entry fetched on-demand from /api/live/request-trace/{chat_id}
 * (admin) or /user/api/live/request-trace/{chat_id} (portal).
 *
 * Reuses the vendored cytoscape.min.js already used by link_analysis.html.
 * Requires: <script src="/static/js/cytoscape.min.js"></script> loaded first.
 */

(function () {
  'use strict';

  const TOPOLOGY = {
    nodes: [
      // Interactive LangGraph pipeline (existing StateGraph, main.py)
      { id: 'cache',              label: 'Cache (L0–L4)',     branch: 'interactive' },
      { id: 'semantic_router',    label: 'Semantic Router',   branch: 'interactive' },
      { id: 'planner',            label: 'Planner',           branch: 'interactive' },
      { id: 'fuzzy_router',       label: 'Fuzzy Router',      branch: 'interactive' },
      { id: 'expert',             label: 'Experts',           branch: 'interactive' },
      { id: 'research',           label: 'Research',          branch: 'interactive' },
      { id: 'mcp',                label: 'MCP Tools',         branch: 'interactive' },
      { id: 'graph_rag',          label: 'GraphRAG',          branch: 'interactive' },
      { id: 'research_fallback',  label: 'Research Fallback', branch: 'interactive' },
      { id: 'thinking',           label: 'Thinking',          branch: 'interactive' },
      { id: 'strategy_review',    label: 'Strategy Review',   branch: 'interactive' },
      { id: 'merger',             label: 'Merger',            branch: 'interactive' },
      { id: 'resolve_conflicts',  label: 'Resolve Conflicts', branch: 'interactive' },
      { id: 'self_critique',      label: 'Self-Critique',     branch: 'interactive' },
      { id: 'critic',             label: 'Critic',            branch: 'interactive' },
      // Agentic tool-calling fast path (Augmented Tool Path enrichment)
      { id: 'tool_entry',         label: 'Tool Entry',        branch: 'agent' },
      { id: 'agent_cache',        label: 'Agent Cache',       branch: 'agent' },
      { id: 'agent_graphrag',     label: 'Agent GraphRAG',    branch: 'agent' },
      { id: 'tool_model_call',    label: 'Tool Model',        branch: 'agent' },
      { id: 'agent_writeback',    label: 'Write-Back (async)',branch: 'agent' },
    ],
    edges: [
      ['cache', 'semantic_router'], ['semantic_router', 'planner'], ['planner', 'fuzzy_router'],
      ['fuzzy_router', 'expert'], ['fuzzy_router', 'research'], ['fuzzy_router', 'mcp'], ['fuzzy_router', 'graph_rag'],
      ['expert', 'research_fallback'], ['research', 'research_fallback'],
      ['mcp', 'research_fallback'], ['graph_rag', 'research_fallback'],
      ['research_fallback', 'thinking'], ['thinking', 'strategy_review'], ['strategy_review', 'merger'],
      ['merger', 'resolve_conflicts'], ['merger', 'self_critique'], ['self_critique', 'merger'],
      ['resolve_conflicts', 'critic'],
      ['tool_entry', 'agent_cache'], ['agent_cache', 'agent_graphrag'],
      ['agent_graphrag', 'tool_model_call'], ['tool_model_call', 'agent_writeback'],
    ],
  };

  // Three self-contained palettes, independent of the page's own light/dark
  // theme toggle — selectable per-user, persisted in localStorage. idle/skip
  // fills must always stay clearly distinguishable from canvasBg, since the
  // not-yet-reached nodes are the majority at any given moment.
  const PALETTES = {
    dark: {
      label: 'Dunkel',
      canvasBg: '#0d1117',
      idle:   { bg: '#374151', border: '#6b7280', text: '#d1d5db' },
      active: { bg: '#f59e0b', border: '#fbbf24', text: '#1f2937' },
      done:   { bg: '#16a34a', border: '#22c55e', text: '#e5e7eb' },
      skip:   { bg: '#374151', border: '#6b7280', text: '#9ca3af' },
      error:  { bg: '#dc2626', border: '#ef4444', text: '#e5e7eb' },
      edge: '#6b7280', edgeOpacity: 0.45, litEdge: '#22c55e', dimOpacity: 0.55,
    },
    light: {
      label: 'Hell',
      canvasBg: '#f1f5f9',
      idle:   { bg: '#e2e8f0', border: '#94a3b8', text: '#1e293b' },
      active: { bg: '#f59e0b', border: '#d97706', text: '#1f2937' },
      done:   { bg: '#16a34a', border: '#15803d', text: '#ffffff' },
      skip:   { bg: '#e2e8f0', border: '#94a3b8', text: '#64748b' },
      error:  { bg: '#dc2626', border: '#b91c1c', text: '#ffffff' },
      edge: '#64748b', edgeOpacity: 0.5, litEdge: '#15803d', dimOpacity: 0.45,
    },
    contrast: {
      label: 'Hoher Kontrast',
      canvasBg: '#000000',
      idle:   { bg: '#000000', border: '#ffffff', text: '#ffffff' },
      active: { bg: '#ffeb3b', border: '#ffffff', text: '#000000' },
      done:   { bg: '#00e676', border: '#ffffff', text: '#000000' },
      skip:   { bg: '#000000', border: '#9e9e9e', text: '#9e9e9e' },
      error:  { bg: '#ff1744', border: '#ffffff', text: '#ffffff' },
      edge: '#ffffff', edgeOpacity: 0.7, litEdge: '#00e676', dimOpacity: 0.35,
    },
  };
  const PALETTE_STORAGE_KEY = 'moe-pipeline-diagram-palette';

  function loadPaletteName() {
    const stored = localStorage.getItem(PALETTE_STORAGE_KEY);
    return PALETTES[stored] ? stored : 'dark';
  }

  let currentPaletteName = loadPaletteName();
  let cy = null;
  let pollTimer = null;
  let panelEl = null;

  function ensurePanel() {
    if (panelEl) return panelEl;
    panelEl = document.createElement('div');
    panelEl.id = 'pipeline-diagram-modal';
    panelEl.className = 'modal';
    panelEl.tabIndex = -1;
    const options = Object.keys(PALETTES).map(key =>
      `<option value="${key}"${key === currentPaletteName ? ' selected' : ''}>${PALETTES[key].label}</option>`
    ).join('');
    panelEl.innerHTML = `
      <div class="modal-dialog modal-xl modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-header py-2">
            <h6 class="modal-title mb-0">
              <i class="bi bi-diagram-3 me-2"></i>Live-Pipeline —
              <code id="pd-chat-id" style="font-size:.8rem"></code>
            </h6>
            <span class="badge bg-secondary ms-2" id="pd-status">…</span>
            <select id="pd-palette" class="form-select form-select-sm ms-2" style="width:auto" title="Farbschema">
              ${options}
            </select>
            <button type="button" class="btn-close ms-2" onclick="window.closePipelineDiagram()"></button>
          </div>
          <div class="modal-body p-0">
            <div id="pd-cy" style="width:100%;height:520px;"></div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(panelEl);
    panelEl.querySelector('#pd-palette').addEventListener('change', (e) => applyPalette(e.target.value));
    return panelEl;
  }

  function buildStyle(p) {
    return [
      {
        selector: 'node',
        style: {
          'background-color': p.idle.bg,
          'border-color':     p.idle.border,
          'border-width':     2,
          'label':            'data(label)',
          'color':            p.idle.text,
          'font-size':        10,
          'text-valign':      'bottom',
          'text-margin-y':    4,
          'width':            34,
          'height':           34,
          'transition-property': 'background-color, border-color',
          'transition-duration': '300ms',
        },
      },
      {
        selector: 'edge',
        style: {
          'width': 1.5,
          'line-color': p.edge,
          'target-arrow-color': p.edge,
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
          'opacity': p.edgeOpacity,
        },
      },
      {
        selector: '.pd-active',
        style: { 'background-color': p.active.bg, 'border-color': p.active.border, 'color': p.active.text },
      },
      {
        selector: '.pd-done',
        style: { 'background-color': p.done.bg, 'border-color': p.done.border, 'color': p.done.text },
      },
      {
        selector: '.pd-skip',
        style: { 'background-color': p.skip.bg, 'border-color': p.skip.border, 'border-style': 'dashed', 'color': p.skip.text },
      },
      {
        selector: '.pd-error',
        style: { 'background-color': p.error.bg, 'border-color': p.error.border, 'color': p.error.text },
      },
      {
        // Dim opacity is palette-specific: a flat 0.25 blended a lightened
        // fill back into near-invisibility on the dark palette, so each
        // palette tunes its own value instead of sharing one constant.
        selector: '.pd-dim',
        style: { 'opacity': p.dimOpacity },
      },
      {
        selector: 'edge.pd-lit',
        style: { 'line-color': p.litEdge, 'target-arrow-color': p.litEdge, 'opacity': 0.9 },
      },
    ];
  }

  function applyPalette(name) {
    if (!PALETTES[name]) return;
    currentPaletteName = name;
    localStorage.setItem(PALETTE_STORAGE_KEY, name);
    const cyEl = document.getElementById('pd-cy');
    if (cyEl) cyEl.style.background = PALETTES[name].canvasBg;
    if (cy) cy.style(buildStyle(PALETTES[name])).update();
    const sel = document.getElementById('pd-palette');
    if (sel && sel.value !== name) sel.value = name;
  }

  function initCy() {
    const elements = [
      ...TOPOLOGY.nodes.map(n => ({ data: { id: n.id, label: n.label, branch: n.branch } })),
      ...TOPOLOGY.edges.map(([s, t]) => ({ data: { id: `${s}__${t}`, source: s, target: t } })),
    ];
    const palette = PALETTES[currentPaletteName];
    document.getElementById('pd-cy').style.background = palette.canvasBg;
    if (cy) cy.destroy();
    cy = cytoscape({
      container: document.getElementById('pd-cy'),
      elements,
      style: buildStyle(palette),
      layout: { name: 'preset', positions: presetPositions() },
      userZoomingEnabled: true,
      userPanningEnabled: true,
      boxSelectionEnabled: false,
    });
  }

  // Fixed two-lane layout: interactive branch on top, agent branch below —
  // avoids a re-layout jitter on every poll tick.
  function presetPositions() {
    const pos = {};
    const interactive = TOPOLOGY.nodes.filter(n => n.branch === 'interactive');
    const agent = TOPOLOGY.nodes.filter(n => n.branch === 'agent');
    interactive.forEach((n, i) => { pos[n.id] = { x: 60 + i * 90, y: 90 }; });
    agent.forEach((n, i) => { pos[n.id] = { x: 60 + i * 130, y: 320 }; });
    return pos;
  }

  function classifyStage(entries, isLatestOverall) {
    if (!entries || entries.length === 0) return 'idle';
    const last = entries[entries.length - 1];
    const status = String(last.status || '');
    if (status.includes('error')) return 'error';
    if (status === 'started') return isLatestOverall ? 'active' : 'done';
    if (status.includes('miss') || status.includes('skip')) return 'skip';
    return 'done'; // hit_*, done, matched, confirmed, corrected, cache_hit, fast_path, ...
  }

  function applyTrace(stageTrace) {
    if (!cy) return;
    cy.nodes().removeClass('pd-active pd-done pd-skip pd-error pd-dim');
    cy.edges().removeClass('pd-lit');

    const byStage = {};
    (stageTrace || []).forEach(e => {
      if (!e || !e.stage) return;
      (byStage[e.stage] = byStage[e.stage] || []).push(e);
    });
    Object.values(byStage).forEach(list => list.sort((a, b) => (a.ts || 0) - (b.ts || 0)));

    let latestTs = -Infinity, latestStage = null;
    (stageTrace || []).forEach(e => {
      if (e && typeof e.ts === 'number' && e.ts > latestTs) { latestTs = e.ts; latestStage = e.stage; }
    });

    const seenStages = new Set(Object.keys(byStage));
    const usedBranch = seenStages.size === 0 ? null
      : (TOPOLOGY.nodes.find(n => n.id === [...seenStages].find(s => n.id === s))?.branch
         || (seenStages.has('tool_entry') ? 'agent' : 'interactive'));

    TOPOLOGY.nodes.forEach(n => {
      const cls = classifyStage(byStage[n.id], n.id === latestStage);
      const node = cy.getElementById(n.id);
      if (cls !== 'idle') node.addClass(`pd-${cls}`);
      if (usedBranch && n.branch !== usedBranch) node.addClass('pd-dim');
    });

    TOPOLOGY.edges.forEach(([s, t]) => {
      if (seenStages.has(s) && seenStages.has(t)) {
        cy.getElementById(`${s}__${t}`).addClass('pd-lit');
      }
    });

    const statusEl = document.getElementById('pd-status');
    if (statusEl) {
      const terminal = latestStage && ['critic', 'agent_writeback', 'merger'].includes(latestStage)
        && classifyStage(byStage[latestStage], true) === 'done';
      statusEl.textContent = seenStages.size === 0 ? 'waiting…' : (terminal ? 'complete' : 'running');
      statusEl.className = 'badge ms-2 ' + (terminal ? 'bg-success' : (seenStages.size === 0 ? 'bg-secondary' : 'bg-warning text-dark'));
    }
    return { seenStages, latestStage };
  }

  async function pollOnce(chatId, traceUrlBase) {
    try {
      const r = await fetch(`${traceUrlBase}${encodeURIComponent(chatId)}`);
      const data = await r.json();
      const { seenStages } = applyTrace(data.stage_trace || []);
      return seenStages.size;
    } catch (e) {
      console.warn('pipeline trace fetch failed:', e);
      return -1;
    }
  }

  window.openPipelineDiagram = function (chatId, traceUrlBase) {
    ensurePanel();
    document.getElementById('pd-chat-id').textContent = (chatId || '').slice(-16);
    // eslint-disable-next-line no-undef
    const modal = bootstrap.Modal.getOrCreateInstance(panelEl);
    modal.show();
    initCy();

    if (pollTimer) clearInterval(pollTimer);
    let stableTicks = 0;
    pollOnce(chatId, traceUrlBase);
    pollTimer = setInterval(async () => {
      const n = await pollOnce(chatId, traceUrlBase);
      // Auto-stop once the trace stops growing for a few ticks (request finished
      // or expired) — avoids polling forever after the panel is left open.
      if (n === (pollOnce._lastN || -1)) stableTicks++; else stableTicks = 0;
      pollOnce._lastN = n;
      if (stableTicks >= 8) { // ~12-16s of no change
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }, 1500);

    panelEl.addEventListener('hidden.bs.modal', window.closePipelineDiagram, { once: true });
  };

  window.closePipelineDiagram = function () {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (cy) { cy.destroy(); cy = null; }
  };
})();
