/* POE AI Build Generator – frontend logic */

const $ = id => document.getElementById(id);

// ── Example tags ─────────────────────────────────────────────────────────────
document.querySelectorAll('.example-tag').forEach(tag => {
  tag.addEventListener('click', () => {
    $('prompt-input').value = tag.dataset.text;
    $('prompt-input').focus();
  });
});

// ── Tab switching ─────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    $(`tab-${btn.dataset.tab}`).classList.add('active');
  });
});

// ── Copy button ───────────────────────────────────────────────────────────────
$('copy-btn').addEventListener('click', async () => {
  const code = $('import-code-box').value;
  if (!code) return;
  try {
    await navigator.clipboard.writeText(code);
  } catch {
    $('import-code-box').select();
    document.execCommand('copy');
  }
  const fb = $('copy-feedback');
  fb.hidden = false;
  setTimeout(() => { fb.hidden = true; }, 2000);
});

// ── Generate ──────────────────────────────────────────────────────────────────
$('generate-btn').addEventListener('click', generateBuild);
$('prompt-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) generateBuild();
});

async function generateBuild() {
  const prompt = $('prompt-input').value.trim();
  if (!prompt) return;

  setLoading(true);
  hideError();
  $('results').hidden = true;

  try {
    const res = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    });

    const data = await res.json();
    if (!res.ok) {
      showError(data.detail || 'An error occurred. Please try again.');
      return;
    }

    renderBuild(data.build, data.import_code, data.tree_info || {});
    $('results').hidden = false;

    // Switch to overview tab
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.querySelector('[data-tab="overview"]').classList.add('active');
    $('tab-overview').classList.add('active');

    // Scroll to results
    $('results').scrollIntoView({ behavior: 'smooth', block: 'start' });

  } catch (err) {
    showError('Network error. Is the server running?');
  } finally {
    setLoading(false);
  }
}

// ── Render ────────────────────────────────────────────────────────────────────
function renderBuild(b, importCode, treeInfo) {
  // Header
  $('build-name').textContent = b.build_name || 'Generated Build';
  $('build-summary').textContent = b.summary || '';
  $('chip-class').textContent = b.class_name || '';
  $('chip-asc').textContent = b.ascendancy_name || '';
  $('chip-budget').textContent = formatBudget(b.budget);

  // Overview
  $('playstyle-text').textContent = b.playstyle || '';
  $('bandit-val').textContent = b.bandit || 'Kill All';
  $('pantheon-major-val').textContent = b.pantheon_major || '—';
  $('pantheon-minor-val').textContent = b.pantheon_minor || '—';

  renderList($('strengths-list'), b.strengths || []);
  renderList($('weaknesses-list'), b.weaknesses || []);

  // Skills
  renderSkills(b.skill_setups || []);

  // Tree
  $('passive-path-desc').textContent = b.passive_path_description || '';
  renderNotables(b.passive_notables || []);

  // Leveling
  renderLeveling(b.gem_leveling || []);

  // Gear
  renderGear(b.gear_guide || {});

  // Tree info
  renderTreeInfo(treeInfo);

  // Import
  $('import-code-box').value = importCode || '';

}

function renderList(el, items) {
  el.innerHTML = items.map(s => `<li>${escHtml(s)}</li>`).join('');
}

function renderSkills(setups) {
  const container = $('skill-setups-container');
  container.innerHTML = setups.map(setup => {
    const gems = (setup.gems || []).map(gem => {
      const cls = gem.is_support ? 'support' : 'active';
      return `<div class="gem-pill ${cls}">
        <span>${escHtml(gem.name)}</span>
        <span class="gem-level">L${gem.level || 20} Q${gem.quality || 0}</span>
      </div>`;
    }).join('');

    const mainBadge = setup.is_main ? '<span class="skill-main-badge">Main</span>' : '';
    return `
      <div class="skill-group">
        <div class="skill-group-header">
          <span class="skill-group-label">${escHtml(setup.label || setup.slot)}</span>
          <span class="skill-group-slot">${escHtml(setup.slot)}</span>
          ${mainBadge}
        </div>
        <div class="gem-list">${gems}</div>
      </div>`;
  }).join('');
}

function renderNotables(names) {
  const KEYSTONES = new Set([
    'chaos inoculation', 'acrobatics', 'phase acrobatics', 'mind over matter',
    'eldritch battery', 'resolute technique', 'elemental overload',
    'avatar of fire', 'blood magic', 'iron reflexes', 'unwavering stance',
    'ghost reaver', 'pain attunement', 'call to arms', 'the agnostic',
    'eternal youth', 'conduit', 'ancestral bond', 'vaal pact',
    'arrow dancing', 'necromantic aegis', 'crimson dance', 'runebinder',
    'split personality', 'the ivory tower',
  ]);

  const grid = $('notables-grid');
  grid.innerHTML = names.map(name => {
    const isKeystone = KEYSTONES.has(name.toLowerCase());
    return `<span class="notable-tag ${isKeystone ? 'keystone' : ''}">${escHtml(name)}</span>`;
  }).join('');
}

function renderLeveling(steps) {
  const tl = $('leveling-timeline');
  tl.innerHTML = steps.map(step => `
    <div class="level-step">
      <div class="level-badge">${step.level || '?'}</div>
      <div class="level-action">${escHtml(step.action || '')}</div>
    </div>`).join('');
}

function renderGear(guide) {
  const SLOT_ORDER = ['helmet','body_armour','gloves','boots','weapon','offhand','amulet','rings','belt','flasks'];
  const SLOT_LABELS = {
    helmet: 'Helmet', body_armour: 'Body Armour', gloves: 'Gloves',
    boots: 'Boots', weapon: 'Weapon', offhand: 'Off-hand',
    amulet: 'Amulet', rings: 'Rings', belt: 'Belt', flasks: 'Flasks',
  };

  // Include any slots returned by Claude even if not in our order list
  const keys = SLOT_ORDER.filter(k => guide[k]).concat(
    Object.keys(guide).filter(k => !SLOT_ORDER.includes(k) && guide[k])
  );

  $('gear-grid').innerHTML = keys.map(k => `
    <div class="gear-slot">
      <div class="gear-slot-name">${SLOT_LABELS[k] || k}</div>
      <div class="gear-slot-desc">${escHtml(guide[k])}</div>
    </div>`).join('');
}

function renderTreeInfo(info) {
  const card = $('node-debug-card');
  const total = info.total_nodes || 0;
  const matched = info.matched || [];
  const unmatched = info.unmatched || [];
  if (total === 0 && matched.length === 0) { card.hidden = true; return; }
  card.hidden = false;

  const jewelSockets = info.jewel_sockets || 0;
  let html = `<div style="font-size:0.85rem;color:var(--gold);font-weight:600;margin-bottom:0.5rem">${total} total nodes allocated (including connecting passives)</div>`;

  if (jewelSockets > 0) {
    html += `<div style="font-size:0.78rem;color:#7090d0;font-weight:600;margin-bottom:0.25rem">💎 ${jewelSockets} jewel socket${jewelSockets > 1 ? 's' : ''} with custom jewels</div>`;
  }

  if (matched.length) {
    html += `<div style="font-size:0.78rem;color:#5ab060;font-weight:600;margin-bottom:0.25rem">✓ ${matched.length} notables found &amp; pathed to</div>`;
    html += matched.map(m => `<div style="font-size:0.75rem;color:var(--text-dim);font-family:monospace">${escHtml(m)}</div>`).join('');
  }
  if (unmatched.length) {
    html += `<div style="font-size:0.78rem;color:#b05050;font-weight:600;margin:0.5rem 0 0.25rem">✗ ${unmatched.length} notables not found in tree</div>`;
    html += unmatched.map(n => `<div style="font-size:0.75rem;color:var(--text-dim);font-family:monospace">${escHtml(n)}</div>`).join('');
  }
  $('node-debug-card').innerHTML = `<h3 class="card-title" style="color:var(--text-dim)">Pathfinder Results</h3>` + html;
}

function renderNodeDebug(debug) {
  const card = $('node-debug-card');
  const matched = debug.matched || [];
  const unmatched = debug.unmatched || [];
  if (matched.length === 0 && unmatched.length === 0) { card.hidden = true; return; }
  card.hidden = false;

  $('node-matched').innerHTML = matched.length
    ? `<div style="font-size:0.78rem;color:#5ab060;font-weight:600;margin-bottom:0.25rem">✓ ${matched.length} matched</div>`
      + matched.map(m => `<div style="font-size:0.75rem;color:var(--text-dim);font-family:monospace">${escHtml(m)}</div>`).join('')
    : '';

  $('node-unmatched').innerHTML = unmatched.length
    ? `<div style="font-size:0.78rem;color:#b05050;font-weight:600;margin:0.4rem 0 0.25rem">✗ ${unmatched.length} not found in tree data (not added to POB)</div>`
      + unmatched.map(n => `<div style="font-size:0.75rem;color:var(--text-dim);font-family:monospace">${escHtml(n)}</div>`).join('')
    : '';
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function formatBudget(b) {
  const map = {
    league_starter: 'League Starter',
    low: 'Low Budget',
    mid: 'Mid Budget',
    high: 'High Budget',
    mirror: 'Mirror Tier',
  };
  return map[b] || (b ? b.replace(/_/g, ' ') : 'Budget Unknown');
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setLoading(on) {
  const btn = $('generate-btn');
  $('btn-label').textContent = on ? 'Generating…' : 'Generate Build';
  $('btn-spinner').hidden = !on;
  btn.disabled = on;
}

function showError(msg) {
  const el = $('error-banner');
  el.textContent = msg;
  el.hidden = false;
}
function hideError() { $('error-banner').hidden = true; }
