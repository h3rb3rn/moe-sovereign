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
      // merger's conditional edge (_should_replan) picks exactly one of these
      // three — not a strict DAG forward-edge, planner is a loop-back.
      ['merger', 'planner'], ['merger', 'resolve_conflicts'], ['merger', 'self_critique'],
      ['self_critique', 'merger'],
      ['resolve_conflicts', 'critic'],
      ['tool_entry', 'agent_cache'], ['agent_cache', 'agent_graphrag'],
      ['agent_graphrag', 'tool_model_call'], ['tool_model_call', 'agent_writeback'],
    ],
  };

  // Titled group boxes (Cytoscape compound-node parents) around each branch
  // — a request only ever travels through exactly one of the two, never
  // both, so without a label the untouched branch reads as "broken" rather
  // than "not applicable to this request".
  const GROUPS = {
    interactive: { id: 'grp_interactive', baseLabel: 'Interaktive Pipeline (LangGraph)' },
    agent:       { id: 'grp_agent',       baseLabel: 'Agent Tool Path (Claude Code / OpenCode)' },
  };
  const UNUSED_SUFFIX = ' — nicht genutzt in dieser Anfrage';

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
  let windowCtl = null;

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
      <div class="modal-dialog modal-xl modal-dialog-centered" id="pd-dialog">
        <div class="modal-content" style="position:relative">
          <div class="modal-header py-2" id="pd-header"
               style="cursor:move;user-select:none;flex-wrap:wrap;row-gap:.35rem">
            <h6 class="modal-title mb-0" style="flex:1 1 100%;min-width:0">
              <i class="bi bi-diagram-3 me-2"></i>Live-Pipeline —
              <code id="pd-chat-id" style="font-size:.8rem"></code>
            </h6>
            <div class="d-flex align-items-center flex-wrap ms-auto" style="row-gap:.35rem">
              <span class="badge bg-secondary ms-2" id="pd-status">…</span>
              <select id="pd-palette" class="form-select form-select-sm ms-2" style="width:auto" title="Farbschema">
                ${options}
              </select>
              <button type="button" class="btn btn-sm btn-outline-secondary ms-1" id="pd-logs-toggle" title="Log-Zeilen dieser Anfrage">
                <i class="bi bi-terminal"></i>
              </button>
              <button type="button" class="btn btn-sm btn-outline-secondary ms-1" id="pd-maximize" title="Maximieren">
                <i class="bi bi-arrows-fullscreen"></i>
              </button>
              <button type="button" class="btn-close ms-2" onclick="window.closePipelineDiagram()"></button>
            </div>
          </div>
          <div class="modal-body p-0">
            <div id="pd-cy" style="width:100%;height:600px;"></div>
            <div id="pd-logs" class="d-none border-top" style="height:180px;overflow-y:auto;background:#0d1117">
              <pre id="pd-logs-content" class="text-light m-0 p-2"
                   style="font-size:.72rem;line-height:1.4;white-space:pre-wrap;word-break:break-all"></pre>
            </div>
          </div>
          <div id="pd-resize-handle" title="Größe ändern"
               style="position:absolute;right:0;bottom:0;width:18px;height:18px;cursor:nwse-resize;
                      display:flex;align-items:center;justify-content:center;opacity:.6;z-index:5">
            <i class="bi bi-arrows-angle-expand" style="font-size:.75rem"></i>
          </div>
        </div>
      </div>`;
    document.body.appendChild(panelEl);
    panelEl.querySelector('#pd-palette').addEventListener('change', (e) => applyPalette(e.target.value));
    windowCtl = makeWindowControls(
      panelEl.querySelector('#pd-dialog'),
      panelEl.querySelector('#pd-header'),
      'pd-cy',
    );
    panelEl.querySelector('#pd-resize-handle').addEventListener('mousedown', (e) => windowCtl.startResize(e));
    panelEl.querySelector('#pd-maximize').addEventListener('click', () => windowCtl.toggleMaximize());
    panelEl.querySelector('#pd-logs-toggle').addEventListener('click', toggleLogs);
    return panelEl;
  }

  // Admin-only (no /user/api/live/process-logs/ endpoint exists — raw
  // server logs can contain internal details not meant for portal end
  // users). Loaded strictly on click, never as part of the regular
  // 1.5s diagram poll, to avoid doubling request volume for admins who
  // never open it.
  let currentChatId = null;
  let logsUrlBase = null;
  let logsOpen = false;
  let logsPollTimer = null;

  async function fetchLogsOnce() {
    if (!currentChatId || !logsUrlBase) return;
    const content = document.getElementById('pd-logs-content');
    try {
      const r = await fetch(`${logsUrlBase}${encodeURIComponent(currentChatId)}`);
      const data = await r.json();
      const wasAtBottom = content.scrollHeight - content.scrollTop <= content.clientHeight + 20;
      content.textContent = (data.lines || []).join('\n') || '(noch keine Log-Zeilen für diese Anfrage)';
      if (wasAtBottom) content.scrollTop = content.scrollHeight;
    } catch (e) {
      console.warn('process-logs fetch failed:', e);
    }
  }

  function toggleLogs() {
    const box = document.getElementById('pd-logs');
    logsOpen = !logsOpen;
    box.classList.toggle('d-none', !logsOpen);
    if (logsOpen) {
      fetchLogsOnce();
      if (logsPollTimer) clearInterval(logsPollTimer);
      logsPollTimer = setInterval(fetchLogsOnce, 1500);
    } else if (logsPollTimer) {
      clearInterval(logsPollTimer);
      logsPollTimer = null;
    }
  }

  // Turns the modal dialog into a free-floating, draggable, resizable,
  // maximizable window instead of Bootstrap's fixed centered/sized modal.
  // No new dependency — plain mouse-event dragging, matching this file's
  // existing "reuse what's vendored, add nothing new" approach.
  function makeWindowControls(dialogEl, headerEl, cyContainerId) {
    let dragging = false, resizing = false, maximized = false;
    let dragStartX = 0, dragStartY = 0, originX = 0, originY = 0;
    let resizeStartX = 0, resizeStartY = 0, startW = 0, startH = 0;
    let savedRect = null;

    function toFreePosition() {
      if (dialogEl.dataset.free === '1') return;
      const r = dialogEl.getBoundingClientRect();
      dialogEl.classList.remove('modal-dialog-centered');
      Object.assign(dialogEl.style, {
        position: 'fixed', margin: '0', maxWidth: 'none',
        left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`,
      });
      dialogEl.dataset.free = '1';
    }

    headerEl.addEventListener('mousedown', (e) => {
      if (e.target.closest('select, button, .btn-close')) return;
      toFreePosition();
      dragging = true;
      dragStartX = e.clientX; dragStartY = e.clientY;
      const r = dialogEl.getBoundingClientRect();
      originX = r.left; originY = r.top;
      e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
      if (dragging) {
        dialogEl.style.left = `${Math.max(0, originX + (e.clientX - dragStartX))}px`;
        dialogEl.style.top  = `${Math.max(0, originY + (e.clientY - dragStartY))}px`;
      } else if (resizing) {
        const newW = Math.max(480, startW + (e.clientX - resizeStartX));
        const newH = Math.max(360, startH + (e.clientY - resizeStartY));
        dialogEl.style.width = `${newW}px`;
        const cyEl = document.getElementById(cyContainerId);
        if (cyEl) cyEl.style.height = `${Math.max(200, newH - headerEl.offsetHeight)}px`;
        if (cy) cy.resize();
      }
    });

    document.addEventListener('mouseup', () => {
      if (!dragging && !resizing) return;
      dragging = false; resizing = false;
      if (cy) cy.fit(cy.elements(), 30);
    });

    return {
      startResize(e) {
        toFreePosition();
        resizing = true;
        resizeStartX = e.clientX; resizeStartY = e.clientY;
        const r = dialogEl.getBoundingClientRect();
        startW = r.width; startH = r.height;
        e.preventDefault(); e.stopPropagation();
      },
      toggleMaximize() {
        const cyEl = document.getElementById(cyContainerId);
        if (!maximized) {
          toFreePosition();
          savedRect = {
            left: dialogEl.style.left, top: dialogEl.style.top,
            width: dialogEl.style.width, cyHeight: cyEl.style.height,
          };
          dialogEl.style.left = '8px';
          dialogEl.style.top = '8px';
          dialogEl.style.width = `${window.innerWidth - 16}px`;
          cyEl.style.height = `${window.innerHeight - 16 - headerEl.offsetHeight}px`;
          maximized = true;
        } else if (savedRect) {
          dialogEl.style.left = savedRect.left;
          dialogEl.style.top = savedRect.top;
          dialogEl.style.width = savedRect.width;
          cyEl.style.height = savedRect.cyHeight;
          maximized = false;
        }
        if (cy) { cy.resize(); cy.fit(cy.elements(), 30); }
      },
    };
  }

  function buildStyle(p) {
    return [
      {
        // Compound "group box" nodes — titled containers around the
        // interactive-pipeline and agent-tool-path node sets, mirroring the
        // labelled "Parallel Execution" subgraph box style already used in
        // docs/ARCHITECTURE.md's mermaid diagram. Without these the two
        // node clusters had no indication of what they represent.
        selector: ':parent',
        style: {
          'background-opacity': 0.08,
          'background-color':   p.idle.text,
          'border-width':       1,
          'border-color':       p.edge,
          'border-style':       'dashed',
          'label':              'data(label)',
          'color':              p.idle.text,
          'font-size':          12,
          'font-weight':        'bold',
          'text-valign':        'top',
          'text-halign':        'center',
          'text-margin-y':      -8,
          'padding':            '28px',
        },
      },
      {
        // :childless excludes the group-box parent nodes above — without it
        // this rule (later in the array) would win over the :parent style
        // for group nodes too, since they also match the plain 'node' tag.
        selector: 'node:childless',
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
      { data: { id: GROUPS.interactive.id, label: GROUPS.interactive.baseLabel } },
      { data: { id: GROUPS.agent.id,       label: GROUPS.agent.baseLabel } },
      ...TOPOLOGY.nodes.map(n => ({
        data: { id: n.id, label: n.label, branch: n.branch, parent: GROUPS[n.branch].id },
      })),
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
    // The DAG layout's absolute coordinates span wider than the modal —
    // scale+center everything into view instead of relying on a fixed zoom.
    cy.fit(cy.elements(), 30);
  }

  // Explicit position map mirroring docs/ARCHITECTURE.md's mermaid flowchart
  // for the LangGraph pipeline: a genuine DAG shape with a visually grouped
  // "parallel execution" fan-out block (expert/research/mcp/graph_rag run
  // concurrently after fuzzy_router, not one after another), and merger's
  // conditional branch shown as siblings rather than a single line — a
  // straight row previously made every request look purely sequential even
  // though most of the pipeline genuinely isn't.
  const NODE_POSITIONS = {
    cache:              { x:   40, y: 260 },
    semantic_router:    { x:  170, y: 260 },
    planner:            { x:  300, y: 260 },
    fuzzy_router:       { x:  430, y: 260 },
    // Parallel Execution block — same x (depth), stacked vertically
    expert:             { x:  570, y: 100 },
    research:           { x:  570, y: 190 },
    mcp:                { x:  570, y: 280 },
    graph_rag:          { x:  570, y: 370 },
    research_fallback:  { x:  710, y: 260 },
    thinking:           { x:  840, y: 260 },
    strategy_review:    { x:  970, y: 260 },
    merger:             { x: 1100, y: 260 },
    // merger's conditional edge picks one of these three siblings
    resolve_conflicts:  { x: 1240, y: 180 },
    self_critique:      { x: 1240, y: 340 }, // loops back to merger
    critic:             { x: 1370, y: 180 },

    // Agentic tool-calling fast path — separate lane, genuinely serial per
    // the real code flow (each step's output gates the next).
    tool_entry:         { x:   40, y: 520 },
    agent_cache:        { x:  190, y: 520 },
    agent_graphrag:     { x:  340, y: 520 },
    tool_model_call:    { x:  490, y: 520 },
    agent_writeback:    { x:  640, y: 520 }, // async, fires after the response
  };

  function presetPositions() {
    return NODE_POSITIONS;
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
    // First TOPOLOGY node (in array order) whose id was actually traced
    // determines which branch this request took — a request only ever
    // travels through one of the two.
    const usedBranch = seenStages.size === 0 ? null
      : (TOPOLOGY.nodes.find(n => seenStages.has(n.id)) || {}).branch || null;

    TOPOLOGY.nodes.forEach(n => {
      const cls = classifyStage(byStage[n.id], n.id === latestStage);
      const node = cy.getElementById(n.id);
      if (cls !== 'idle') node.addClass(`pd-${cls}`);
      if (usedBranch && n.branch !== usedBranch) node.addClass('pd-dim');
    });

    // Label the untouched branch's group box explicitly instead of leaving
    // it as an unexplained grey block.
    cy.getElementById(GROUPS.interactive.id).data('label',
      GROUPS.interactive.baseLabel + (usedBranch === 'agent' ? UNUSED_SUFFIX : ''));
    cy.getElementById(GROUPS.agent.id).data('label',
      GROUPS.agent.baseLabel + (usedBranch === 'interactive' ? UNUSED_SUFFIX : ''));

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

    currentChatId = chatId;
    logsUrlBase = traceUrlBase.startsWith('/user/') ? null : traceUrlBase.replace('request-trace', 'process-logs');
    document.getElementById('pd-logs-toggle').classList.toggle('d-none', !logsUrlBase);
    // Reset the log view for the newly-selected request instead of showing
    // the previous row's stale content until the first poll lands.
    logsOpen = false;
    document.getElementById('pd-logs').classList.add('d-none');
    document.getElementById('pd-logs-content').textContent = '';
    if (logsPollTimer) { clearInterval(logsPollTimer); logsPollTimer = null; }

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
    if (logsPollTimer) { clearInterval(logsPollTimer); logsPollTimer = null; }
    logsOpen = false;
    if (cy) { cy.destroy(); cy = null; }
  };
})();
