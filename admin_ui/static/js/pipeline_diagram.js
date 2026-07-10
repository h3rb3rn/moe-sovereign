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

  // Canvas background is #0d1117 (near-black, matches link_analysis.html's
  // knowledge-graph diagram). idle/skip fills must stay clearly lighter than
  // that or the not-yet-reached nodes — the majority at any given moment —
  // become indistinguishable dark blobs on a dark background.
  const COLORS = {
    idle:   { bg: '#374151', border: '#6b7280', text: '#d1d5db' },
    active: { bg: '#f59e0b', border: '#fbbf24', text: '#1f2937' },
    done:   { bg: '#16a34a', border: '#22c55e', text: '#e5e7eb' },
    skip:   { bg: '#374151', border: '#6b7280', text: '#9ca3af' },
    error:  { bg: '#dc2626', border: '#ef4444', text: '#e5e7eb' },
  };

  let cy = null;
  let pollTimer = null;
  let panelEl = null;

  function ensurePanel() {
    if (panelEl) return panelEl;
    panelEl = document.createElement('div');
    panelEl.id = 'pipeline-diagram-modal';
    panelEl.className = 'modal';
    panelEl.tabIndex = -1;
    panelEl.innerHTML = `
      <div class="modal-dialog modal-xl modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-header py-2">
            <h6 class="modal-title mb-0">
              <i class="bi bi-diagram-3 me-2"></i>Live-Pipeline —
              <code id="pd-chat-id" style="font-size:.8rem"></code>
            </h6>
            <span class="badge bg-secondary ms-2" id="pd-status">…</span>
            <button type="button" class="btn-close ms-auto" onclick="window.closePipelineDiagram()"></button>
          </div>
          <div class="modal-body p-0">
            <div id="pd-cy" style="width:100%;height:520px;background:#0d1117;"></div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(panelEl);
    return panelEl;
  }

  function initCy() {
    const elements = [
      ...TOPOLOGY.nodes.map(n => ({ data: { id: n.id, label: n.label, branch: n.branch } })),
      ...TOPOLOGY.edges.map(([s, t]) => ({ data: { id: `${s}__${t}`, source: s, target: t } })),
    ];
    if (cy) cy.destroy();
    cy = cytoscape({
      container: document.getElementById('pd-cy'),
      elements,
      style: [
        {
          selector: 'node',
          style: {
            'background-color': COLORS.idle.bg,
            'border-color':     COLORS.idle.border,
            'border-width':     2,
            'label':            'data(label)',
            'color':            COLORS.idle.text,
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
            'line-color': '#6b7280',
            'target-arrow-color': '#6b7280',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'opacity': 0.45,
          },
        },
        {
          selector: '.pd-active',
          style: { 'background-color': COLORS.active.bg, 'border-color': COLORS.active.border, 'color': COLORS.active.text },
        },
        {
          selector: '.pd-done',
          style: { 'background-color': COLORS.done.bg, 'border-color': COLORS.done.border, 'color': COLORS.done.text },
        },
        {
          selector: '.pd-skip',
          style: { 'background-color': COLORS.skip.bg, 'border-color': COLORS.skip.border, 'border-style': 'dashed', 'color': COLORS.skip.text },
        },
        {
          selector: '.pd-error',
          style: { 'background-color': COLORS.error.bg, 'border-color': COLORS.error.border, 'color': COLORS.error.text },
        },
        {
          selector: '.pd-dim',
          style: { 'opacity': 0.25 },
        },
        {
          selector: 'edge.pd-lit',
          style: { 'line-color': '#22c55e', 'target-arrow-color': '#22c55e', 'opacity': 0.9 },
        },
      ],
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

  const STATUS_ORDER_HINT = ['error', 'active', 'done', 'skip'];

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
