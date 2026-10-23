'use strict';

// ------------------------------------------------------------------ basics

const $main = document.getElementById('main');
let META = { categories: [], statuses: [], asset_types: [], locations: [] };
let CATS = {};
const COUNTS = { assets: null, items: null, byCat: {} };
const NAV = { active: '', cat: '' };
let CRUMBS = [];

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  let value;
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'value') value = v;
    else if (k === 'checked') el.checked = true;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  if (value !== undefined) el.value = value;
  return el;
}

/** replaceChildren, but skipping null/false and flattening arrays like h() does. */
function setKids(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter(k => k != null && k !== false));
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (typeof body === 'string') { opts.body = body; opts.headers['Content-Type'] = 'text/plain'; }
    else { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = text;
  try { data = JSON.parse(text); } catch { /* plain text */ }
  if (!res.ok) {
    let msg = data && data.detail ? data.detail : `${res.status} ${res.statusText}`;
    if (Array.isArray(msg)) msg = msg.map(m => m.msg).join(', ');
    throw new Error(msg);
  }
  return data;
}

let toastTimer;
function toast(msg, isError) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ''; }, isError ? 5000 : 2500);
}

async function loadMeta() {
  META = await api('GET', '/api/meta');
  CATS = Object.fromEntries(META.categories.map(c => [c.key, c]));
  renderNav();
}

async function refreshCounts() {
  const [assets, items] = await Promise.all([api('GET', '/api/assets'), api('GET', '/api/items')]);
  COUNTS.assets = assets.filter(a => !isKeyboard(a)).length;
  COUNTS.keyboards = assets.filter(isKeyboard).length;
  COUNTS.items = items.filter(i => !isKbCat(i.category)).length;
  COUNTS.byCat = {};
  for (const it of items) COUNTS.byCat[it.category] = (COUNTS.byCat[it.category] || 0) + 1;
  renderNav();
}

function makerModel(a) {
  const maker = a.manufacturer || '', model = a.model || '';
  const first = maker.split(/[\s,]+/)[0].toLowerCase();
  if (model && first && model.toLowerCase().startsWith(first)) return model;
  return [maker, model].filter(Boolean).join(' ');
}

const catLabel = key => (CATS[key] && CATS[key].label) || key;
const catOrder = key => { const i = META.categories.findIndex(c => c.key === key); return i < 0 ? 999 : i; };

function fmtDate(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return isNaN(d) ? ts : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function ago(ts) {
  const d = new Date(ts);
  if (isNaN(d)) return ts || '';
  const s = (Date.now() - d) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
  return d.toLocaleDateString(undefined, { dateStyle: 'medium' });
}

// ------------------------------------------------------------------ icons

const ICONS = {
  cpu: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/>',
  ram: '<path d="M6 19v-3M10 19v-3M14 19v-3M18 19v-3M8 11V9M16 11V9M12 11V9M2 15h20"/><path d="M2 7a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v1.1a2 2 0 0 0 0 3.8V17a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-5.1a2 2 0 0 0 0-3.8Z"/>',
  storage: '<path d="M22 12H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><path d="M6 16h.01M10 16h.01"/>',
  gpu: '<rect x="2" y="6" width="20" height="11" rx="2"/><circle cx="9" cy="11.5" r="2.5"/><path d="M15 10h3M15 13h3M6 17v3M10 17v3M14 17v3"/>',
  board: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M11 9h4a2 2 0 0 0 2-2V3"/><circle cx="9" cy="9" r="2"/><path d="M7 21v-4a2 2 0 0 1 2-2h4"/><circle cx="15" cy="15" r="2"/>',
  psu: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
  nic: '<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3M12 12V8"/>',
  box: '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="M3.3 7 12 12l8.7-5M12 22V12"/>',
  boxes: '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="M3.3 7 12 12l8.7-5M12 22V12M7.5 4.5l9 5"/>',
  monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
  laptop: '<path d="M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9m16 0H4m16 0 1.28 2.55a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45L4 16"/>',
  server: '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><path d="M6 6h.01M6 18h.01M10 6h4M10 18h4"/>',
  tower: '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M10 6h4M10 9.5h4"/><circle cx="12" cy="16" r="1.6"/>',
  mini: '<rect x="3" y="7" width="18" height="10" rx="2"/><path d="M7 12h.01M11 12h6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10"/>',
  settings: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  edit: '<path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
  trash: '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  pin: '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20"/>',
  tag: '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z"/><circle cx="7.5" cy="7.5" r="1"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  terminal: '<path d="m4 17 6-6-6-6M12 19h8"/>',
  merge: '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  install: '<path d="M12 3v12M7 10l5 5 5-5M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
  theme: '<circle cx="12" cy="12" r="9"/><path d="M12 3v18a9 9 0 0 0 0-18z" fill="currentColor"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  switch: '<rect x="2" y="7" width="20" height="10" rx="2"/><path d="M6 11v2M9 11v2M12 11v2M15 11v2M18 11v2"/>',
  wifi: '<path d="M12 20h.01M2 8.8a15 15 0 0 1 20 0M5 12.9a10 10 0 0 1 14 0M8.5 16.4a5 5 0 0 1 7 0"/>',
  router: '<rect x="2" y="14" width="20" height="7" rx="2"/><path d="M6 17.5h.01M10 17.5h.01M15 10v4M17.8 7.2a4 4 0 0 0-5.6 0M20.7 4.3a8 8 0 0 0-11.4 0"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5"/>',
  minus: '<path d="M5 12h14"/>',
  topology: '<rect x="9" y="2" width="6" height="5" rx="1"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/><path d="M12 7v5M5 17v-5h14v5"/>',
  keyboard: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M8 12h.01M12 12h.01M16 12h.01M7 16h10"/>',
  kbswitch: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M12 8v8M8 12h8"/>',
  keycap: '<path d="M3 19h18L18.6 6.4a1.5 1.5 0 0 0-1.5-1.2H6.9a1.5 1.5 0 0 0-1.5 1.2z"/><path d="M8.5 10h7"/>',
};

function icon(name, cls) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'i' + (cls ? ' ' + cls : ''));
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = ICONS[name] || ICONS.box;
  return svg;
}

const CAT_STYLE = {
  cpu: ['cpu', 'blue'], ram: ['ram', 'green'], storage: ['storage', 'amber'], gpu: ['gpu', 'purple'],
  motherboard: ['board', 'teal'], psu: ['psu', 'orange'], nic: ['nic', 'pink'], other: ['box', 'gray'],
  switch: ['kbswitch', 'orange'], keycaps: ['keycap', 'purple'],
};
function catIcon(key, size) {
  const [ic, color] = CAT_STYLE[key] || ['box', 'gray'];
  return h('span', { class: `ico c-${color}${size ? ' ' + size : ''}` }, icon(ic));
}

const TYPE_STYLE = {
  laptop: ['laptop', 'purple'], server: ['server', 'teal'], 'mini pc': ['mini', 'green'], 'sff pc': ['mini', 'green'],
  'custom pc': ['tower', 'blue'], desktop: ['tower', 'blue'],
  switch: ['switch', 'teal'], 'access point': ['wifi', 'pink'], router: ['router', 'orange'], firewall: ['shield', 'red'],
};
const NETWORK_TYPES = ['switch', 'access point', 'router', 'firewall'];
const isNetwork = a => NETWORK_TYPES.includes((a.asset_type || '').toLowerCase()) || !!(a.ports && a.ports.length);
const fmtSpeed = mbps => !mbps ? '' : mbps >= 1000 ? `${+(mbps / 1000).toFixed(1)}G` : `${mbps}M`;
function typeIcon(type, size) {
  const [ic, color] = TYPE_STYLE[(type || '').toLowerCase()] || ['monitor', 'gray'];
  return h('span', { class: `ico c-${color}${size ? ' ' + size : ''}` }, icon(ic));
}

const STATUS_COLORS = { 'in use': 'green', spare: 'blue', wiped: 'amber', 'for sale': 'purple', broken: 'red', retired: 'gray' };
function statusPill(status) {
  if (!status) return null;
  return h('span', { class: 'pill p-' + (STATUS_COLORS[status.toLowerCase()] || 'gray') }, status);
}

const isKeyboard = a => a.kind === 'keyboard';
const isKbCat = key => !!(CATS[key] && CATS[key].group === 'keyboard');
const assetHref = a => `#/${isKeyboard(a) ? 'keyboards' : 'assets'}/${a.id}`;
function assetIcon(a, size) {
  if (!isKeyboard(a)) return typeIcon(a.asset_type, size);
  return h('span', { class: `ico c-teal${size ? ' ' + size : ''}` }, icon('keyboard'));
}
/** "Switches" -> "Switch", "Keycaps" -> "Keycap" */
const singular = label => String(label || '').replace(/(ch|sh|x)es$/i, '$1').replace(/s$/i, '');
const SWITCH_COLORS = { linear: 'red', tactile: 'amber', clicky: 'blue' };
function switchTypePill(type) {
  if (!type) return null;
  return h('span', { class: 'pill p-' + (/silent/i.test(type) ? 'gray' : SWITCH_COLORS[type.toLowerCase()] || 'gray') }, type);
}

function emptyState(ic, title, text, action) {
  return h('div', { class: 'empty' }, h('div', { class: 'big' }, icon(ic)), h('strong', null, title), text ? h('div', null, text) : null,
    action ? h('div', { style: 'margin-top:14px' }, action) : null);
}

function setCrumbs(...parts) { CRUMBS = parts; }

// ------------------------------------------------------------------ specs helpers

function splitLabel(label) {
  const m = /^(.*?)\s*\(([^)]+)\)\s*$/.exec(label || '');
  return m ? [m[1], m[2]] : [label, ''];
}

function linkHost(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
}

function specValue(field, value) {
  if (field.type === 'url') {
    return h('a', { href: value, target: '_blank', rel: 'noopener noreferrer', class: 'ext-link', title: value, onclick: e => e.stopPropagation() },
      linkHost(value), ' ↗');
  }
  const unit = splitLabel(field.label)[1];
  return unit && field.type !== 'select' ? `${value} ${unit}` : value;
}

function specChips(catKey, specs, opts = {}) {
  const cat = CATS[catKey];
  const fields = cat ? cat.fields : [];
  const shown = new Set();
  const chips = [];
  for (const f of fields) {
    const v = specs && specs[f.key];
    if (!v) continue;
    shown.add(f.key);
    const label = splitLabel(f.label)[0];
    let text = specValue(f, v), key = f.type === 'select' || f.type === 'url' ? null : label;
    if (opts.compact && key) {
      // "4 cores", "Boost 4.8 GHz", "16GB": short, but still says what the number is
      const unit = splitLabel(f.label)[1];
      if (unit) key = label.split(' ')[0];
      else if (f.type === 'number') { text = `${v} ${label.toLowerCase()}`; key = null; }
      else key = null;
    }
    chips.push(h('span', { class: 'chip', title: label }, key ? h('span', { class: 'k' }, key) : null, text));
  }
  for (const [k, v] of Object.entries(specs || {})) {
    if (!shown.has(k) && v) chips.push(h('span', { class: 'chip' }, h('span', { class: 'k' }, k), v));
  }
  return chips.length ? h('span', { class: 'chips' }, chips) : h('span', { class: 'faint' }, '—');
}

function renderName(cat, specs, fallback) {
  const tpl = (cat && cat.name_template) || '';
  if (!tpl) return fallback || '';
  // "XVX" brand + "XVX" profile gives "XVX Coffee", not "XVX XVX Coffee"
  const name = tpl.replace(/\{(\w+)\}/g, (_, k) => (specs && specs[k]) || '').replace(/\s+/g, ' ').trim()
    .replace(/(^|\s)(\S+)(?:\s\2)+(?=\s|$)/gi, '$1$2');
  return name || fallback || '';
}

const norm = v => String(v ?? '').replace(/[\s\-_]+/g, '').toLowerCase();
function matchItem(items, comp) {
  const cat = CATS[comp.category];
  const keys = cat && cat.match && cat.match.length ? cat.match : ['name'];
  let best = null, bestScore = 0;
  for (const it of items) {
    if (it.category !== comp.category) continue;
    let score = 0;
    for (const k of keys) {
      const a = norm(k === 'name' ? comp.name : comp.specs[k]);
      const b = norm(k === 'name' ? it.name : it.specs[k]);
      if (a && b) { if (a !== b) { score = -1; break; } score++; }
    }
    if (score > bestScore) { best = it; bestScore = score; }
  }
  return best;
}

function datalist(id, values) {
  return h('datalist', { id }, values.map(v => h('option', { value: v })));
}

function selectEl(options, value, props) {
  const opts = options.map(o => (typeof o === 'string' ? [o, o] : o));
  if (value && !opts.some(([v]) => v === value)) opts.push([value, value]);
  const s = h('select', props, opts.map(([v, l]) => h('option', { value: v }, l)));
  s.value = value ?? '';
  return s;
}

function field(label, input, cls) {
  return h('label', { class: 'field' + (cls ? ' ' + cls : '') }, label, input);
}

function section(title, ...kids) {
  return h('div', { class: 'form-section' }, title ? h('h3', null, title) : null, kids);
}

/** Inputs for a category's spec fields. onChange(key, value, committed) */
function specInputs(catKey, specs, onChange) {
  const cat = CATS[catKey];
  if (!cat) return [];
  return cat.fields.map(f => {
    let input;
    if (f.type === 'select') {
      input = selectEl([['', '—'], ...f.options], specs[f.key] || '', {
        onchange: e => onChange(f.key, e.target.value, true),
      });
    } else {
      input = h('input', {
        type: f.type === 'number' ? 'number' : f.type === 'url' ? 'url' : 'text', value: specs[f.key] || '',
        placeholder: f.type === 'url' ? 'https://…' : null,
        oninput: e => onChange(f.key, e.target.value, false),
        onchange: e => onChange(f.key, e.target.value, true),
      });
    }
    return field(f.label, input);
  });
}

// ------------------------------------------------------------------ sortable tables

/** Sort state per table, kept while the page is open so it survives redraws and navigation. */
const SORTS = {};

/** Total size in GB of text like "32GB LPDDR5" or "1TB NVMe SSD, 2× 4TB SATA HDD"; null when there is none. */
function sizeGB(text) {
  let total = 0;
  String(text || '').replace(/(?:(\d+)\s*×\s*)?(\d+(?:\.\d+)?)\s*(TB|GB|MB)\b/gi, (_, n, v, unit) => {
    total += (Number(n) || 1) * Number(v) * { tb: 1024, gb: 1, mb: 1 / 1024 }[unit.toLowerCase()];
  });
  return total || null;
}

/** A value that sorts sensibly: sizes and numbers as numbers, everything else as text. */
function sortVal(v) {
  if (v == null || v === '') return null;
  if (typeof v === 'number') return v;
  const s = String(v).trim();
  if (/^\d+(\.\d+)?\s*(TB|GB|MB)$/i.test(s)) return sizeGB(s);
  const m = /^-?\d+(\.\d+)?/.exec(s);
  return m ? Number(m[0]) : s;
}

/** Layouts sort by their size: "60%" < "TKL (80%)" < "Full size (100%)", then named ones like "Split". */
const layoutVal = v => { const m = /(\d+)%/.exec(v || ''); return m ? Number(m[1]) : (v || null); };

function compareVals(a, b) {
  const na = typeof a === 'number', nb = typeof b === 'number';
  if (na && nb) return a - b;
  if (na !== nb) return na ? -1 : 1;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

/** Rows in the table's chosen order (blank values always last). cols: [{ key, sort: row => value }] */
function sortRows(id, rows, cols) {
  const s = SORTS[id];
  const col = s && cols.find(c => c.key === s.key && c.sort);
  if (!col) return rows;
  return rows.map(r => [sortVal(col.sort(r)), r]).sort(([a], [b]) => {
    if (a == null || b == null) return (a == null) - (b == null);
    return s.dir * compareVals(a, b);
  }).map(([, r]) => r);
}

/** Header cells; a click sorts A→Z / smallest first, a second click reverses. cols: [{ key, label, cls, sort }] */
function sortHeads(id, cols, redraw) {
  return cols.map(c => {
    if (!c.sort) return h('th', { class: c.cls || null }, c.label);
    const s = SORTS[id], on = !!(s && s.key === c.key);
    return h('th', { class: [c.cls, 'sortable', on ? 'sorted' : ''].filter(Boolean).join(' '), 'aria-sort': on ? (s.dir > 0 ? 'ascending' : 'descending') : null },
      h('button', {
        class: 'th-sort', title: 'Sort by ' + String(c.label).toLowerCase(), onclick: () => {
          SORTS[id] = { key: c.key, dir: on ? -s.dir : 1 };
          redraw();
        },
      }, c.label, h('span', { class: 'arrow', 'aria-hidden': 'true' }, on ? (s.dir > 0 ? '↑' : '↓') : '↕')));
  });
}

// ------------------------------------------------------------------ shell: nav, crumbs, search, theme

function renderNav() {
  const nav = document.getElementById('nav');
  if (!nav) return;
  const item = (href, key, ic, label, n, hot) => h('a', {
    class: 'nav-item' + (NAV.active === key && !(key === 'inventory' && NAV.cat) ? ' active' : ''), href,
  }, icon(ic), label, n != null ? h('span', { class: 'n' + (hot ? ' hot' : '') }, n) : null);
  const cats = META.categories.filter(c => COUNTS.byCat[c.key] && c.group !== 'keyboard');
  const catLink = c => h('a', {
    class: 'nav-item' + (NAV.active === 'inventory' && NAV.cat === c.key ? ' active' : ''), href: `#/inventory?cat=${c.key}`,
  }, c.group === 'keyboard' ? icon((CAT_STYLE[c.key] || ['box'])[0]) : null, c.label,
    COUNTS.byCat[c.key] ? h('span', { class: 'n' }, COUNTS.byCat[c.key]) : null);
  setKids(nav,
    h('div', { class: 'nav-label' }, 'Track'),
    item('#/assets', 'assets', 'monitor', 'Assets', COUNTS.assets),
    item('#/inventory', 'inventory', 'boxes', 'Inventory', COUNTS.items),
    cats.length ? h('div', { class: 'nav-sub' }, cats.map(catLink)) : null,
    item('#/map', 'map', 'topology', 'Network map'),
    h('div', { class: 'nav-label' }, 'Keyboards'),
    item('#/keyboards', 'keyboards', 'keyboard', 'Keyboards', COUNTS.keyboards),
    META.categories.filter(c => c.group === 'keyboard').map(catLink),
    h('div', { class: 'nav-label' }, 'Discover'),
    item('#/assets/new', 'new', 'plus', 'Add asset'),
    item('#/scans', 'scans', 'scan', 'Scans', META.pending_scans || null, true),
    h('div', { class: 'nav-label' }, 'Configure'),
    item('#/settings', 'settings', 'settings', 'Settings'),
  );
}

function renderCrumbs() {
  const el = document.getElementById('crumbs');
  const out = [];
  CRUMBS.forEach(([label, href], i) => {
    if (i) out.push(h('span', { class: 'sep' }, '/'));
    out.push(href && i < CRUMBS.length - 1 ? h('a', { href }, label) : h('span', { class: 'here' }, label));
  });
  setKids(el, out);
}

const THEMES = ['auto', 'light', 'dark'];
function getTheme() { try { return localStorage.getItem('rigbook-theme') || 'auto'; } catch { return 'auto'; } }
function applyTheme(t) {
  if (t === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  const foot = document.getElementById('sidebar-foot');
  setKids(foot, h('button', {
    class: 'small', onclick: () => {
      const next = THEMES[(THEMES.indexOf(getTheme()) + 1) % THEMES.length];
      try { localStorage.setItem('rigbook-theme', next); } catch { /* not saved */ }
      applyTheme(next);
    },
  }, icon('theme'), `Theme: ${t[0].toUpperCase()}${t.slice(1)}`));
}

function setupShell() {
  setKids(document.getElementById('menu-btn'), icon('menu'));
  document.getElementById('menu-btn').addEventListener('click', () => document.body.classList.toggle('nav-open'));
  document.getElementById('scrim').addEventListener('click', () => document.body.classList.remove('nav-open'));
  applyTheme(getTheme());
  setupSearch();
}

function setupSearch() {
  const wrap = document.getElementById('search');
  const input = h('input', { type: 'search', placeholder: 'Search assets, parts, serials…', 'aria-label': 'Search', autocomplete: 'off' });
  const results = h('div', { class: 'search-results', hidden: true });
  setKids(wrap, h('div', { class: 'box' }, icon('search'), input, h('kbd', null, '/')), results);
  let data = null, links = [], sel = 0;

  const hay = parts => parts.flat().filter(Boolean).join(' ').toLowerCase();
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    if (!q || !data) { results.hidden = true; return; }
    const words = q.split(/\s+/);
    const hit = text => words.every(w => text.includes(w));
    const assets = data.assets.filter(a => hit(hay([a.name, a.hostname, a.address, a.model, a.manufacturer, a.serial, a.os, a.location, a.asset_type, isKeyboard(a) ? ['keyboard', Object.values(a.specs).flat()] : [], Object.values(a.summary || {})]))).slice(0, 6);
    const items = data.items.filter(i => hit(hay([i.name, i.manufacturer, i.model_number, i.location, catLabel(i.category), Object.values(i.specs || {})]))).slice(0, 8);
    links = [
      ...assets.map(a => h('a', { href: assetHref(a) }, assetIcon(a, 'sm'),
        h('div', { class: 'ent' }, h('div', null, h('div', { class: 't' }, a.name), h('div', { class: 's' }, [makerModel(a), a.hostname, isKeyboard(a) ? a.specs.layout : ''].filter(Boolean).join(' · ')))))),
      ...items.map(i => h('a', { href: `#/inventory/${i.id}` }, catIcon(i.category, 'sm'),
        h('div', { class: 'ent' }, h('div', null, h('div', { class: 't' }, i.name),
          h('div', { class: 's' }, [catLabel(i.category), i.manufacturer, `${i.installed_qty} in use · ${i.spare_qty} spare`].filter(Boolean).join(' · ')))))),
    ];
    sel = 0;
    links.forEach((l, i) => l.classList.toggle('sel', i === sel));
    links.forEach(l => l.addEventListener('click', close));
    setKids(results,
      assets.length ? h('h3', null, 'Assets') : null, links.slice(0, assets.length),
      items.length ? h('h3', null, 'Inventory') : null, links.slice(assets.length),
      !links.length ? h('div', { class: 'empty' }, `Nothing found for “${input.value}”`) : null);
    results.hidden = false;
  };
  const close = () => { results.hidden = true; input.value = ''; input.blur(); };
  input.addEventListener('focus', async () => {
    try {
      const [assets, items] = await Promise.all([api('GET', '/api/assets'), api('GET', '/api/items')]);
      data = { assets, items };
      draw();
    } catch { /* search stays empty */ }
  });
  input.addEventListener('input', draw);
  input.addEventListener('keydown', e => {
    if (e.key === 'Escape') return close();
    if (!links.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + links.length) % links.length;
      links.forEach((l, i) => l.classList.toggle('sel', i === sel));
      links[sel].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      location.hash = links[sel].getAttribute('href');
      close();
    }
  });
  document.addEventListener('mousedown', e => { if (!wrap.contains(e.target)) results.hidden = true; });
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !e.target.closest('input, textarea, select') && !document.querySelector('.modal-backdrop')) {
      e.preventDefault();
      input.focus();
    }
  });
}

// ------------------------------------------------------------------ drawer / dialog

/** Forms open as a drawer from the right; opts.dialog gives a small centred dialog instead. */
function openModal(title, body, buttons, opts = {}) {
  const root = document.getElementById('modal-root');
  const close = () => {
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
    if (opts.onClose) opts.onClose();
  };
  const onKey = e => { if (e.key === 'Escape') close(); };
  const foot = h('div', { class: 'modal-foot' });
  for (const b of buttons || []) {
    const btn = h('button', { class: b.class || '' }, b.label);
    btn.addEventListener('click', async () => {
      if (!b.action) return close();
      btn.disabled = true;
      try { if ((await b.action(close)) !== false) close(); }
      catch (err) { toast(err.message, true); }
      finally { btn.disabled = false; }
    });
    foot.append(btn);
  }
  const modal = h('div', { class: 'modal' + (opts.wide ? ' wide' : ''), role: 'dialog', 'aria-modal': 'true' },
    h('div', { class: 'modal-head' }, h('h2', null, title), h('button', { class: 'icon-btn', onclick: close, 'aria-label': 'Close' }, icon('x'))),
    h('div', { class: 'modal-body' }, body),
    buttons && buttons.length ? foot : null);
  const backdrop = h('div', { class: 'modal-backdrop ' + (opts.dialog ? 'dialog' : 'drawer'), onmousedown: e => { if (e.target === backdrop) close(); } }, modal);
  root.append(backdrop);
  document.addEventListener('keydown', onKey);
  const first = modal.querySelector('.modal-body input:not([type=checkbox]), .modal-body select, .modal-body textarea');
  if (first) first.focus();
  return close;
}

function confirmModal(title, message, confirmLabel, extra) {
  return new Promise(resolve => {
    let answer = false;
    openModal(title, h('div', null, h('p', { style: 'margin-top:0' }, message), extra || null), [
      { label: 'Cancel' },
      { label: confirmLabel || 'Confirm', class: 'primary danger', action: () => { answer = true; } },
    ], { dialog: true, onClose: () => resolve(answer) });
  });
}

// ------------------------------------------------------------------ forms

const ASSET_SECTIONS = [
  ['Basics', [['name', 'Name'], ['asset_type', 'Type'], ['status', 'Status'], ['location', 'Location']]],
  ['Network', [['hostname', 'Hostname'], ['address', 'IP / address for scans'], ['os', 'Operating system / firmware'], ['macs', 'MAC addresses'],
    ['uplink_id', 'Connected to'], ['uplink_port', 'Port on that device']]],
  ['Hardware', [['manufacturer', 'Manufacturer'], ['model', 'Model'], ['model_number', 'Model number / SKU'], ['serial', 'Serial number']]],
];
const ASSET_FORM = ASSET_SECTIONS.flatMap(([, fields]) => fields);

function assetFormFields(data, others = []) {
  const input = key => {
    let el;
    if (key === 'uplink_id') {
      el = selectEl([['', 'Work it out from switch scans'], ...others.filter(o => o.id !== data.id && !isKeyboard(o))
        .sort((x, y) => isNetwork(y) - isNetwork(x)).map(o => [String(o.id), o.name])], data.uplink_id ? String(data.uplink_id) : '', {});
    } else if (key === 'uplink_port') el = h('input', { value: data[key] || '', placeholder: 'e.g. gi18, port 3' });
    else if (key === 'asset_type') el = selectEl([['', '—'], ...META.asset_types], data[key] || '', {});
    else if (key === 'status') el = selectEl([['', '—'], ...META.statuses], data[key] || '', {});
    else if (key === 'location') el = h('input', { list: 'dl-locations', value: data[key] || '' });
    else if (key === 'macs') el = h('input', { value: Array.isArray(data.macs) ? data.macs.join(', ') : data.macs || '', placeholder: 'Filled in by scans; used to find its switch port' });
    else el = h('input', { value: data[key] || '' });
    el.dataset.key = key;
    el.addEventListener('input', () => { data[key] = el.value; });
    el.addEventListener('change', () => { data[key] = el.value; });
    return el;
  };
  return h('div', null,
    ASSET_SECTIONS.map(([title, fields]) => section(title, h('div', { class: 'grid' }, fields.map(([k, label]) => field(label, input(k)))))),
    section('Notes', h('textarea', { value: data.notes || '', 'aria-label': 'Notes', oninput: e => { data.notes = e.target.value; } })),
    datalist('dl-locations', META.locations));
}

function assetBody(data) {
  return Object.fromEntries([...ASSET_FORM.map(([k]) => k), 'notes'].map(k => [k, data[k] || '']));
}

async function editAssetModal(asset, onSaved) {
  const data = { ...asset };
  const others = await api('GET', '/api/assets');
  openModal(asset.id ? `Edit ${asset.name}` : 'New asset', assetFormFields(data, others), [
    { label: 'Cancel' },
    {
      label: 'Save changes', class: 'primary', action: async () => {
        const body = assetBody(data);
        body.uplink_id = data.uplink_id ? Number(data.uplink_id) : null;
        const res = asset.id ? await api('PUT', `/api/assets/${asset.id}`, body) : await api('POST', '/api/assets', body);
        toast('Saved');
        onSaved(res.id);
      },
    },
  ]);
}

function itemFormFields(data, opts = {}) {
  data.specs = data.specs || {};
  const templateName = () => renderName(CATS[data.category], { ...data.specs, manufacturer: data.manufacturer, model_number: data.model_number }, '');
  let nameTouched = !!data.name && !(CATS[data.category] && CATS[data.category].name_template && data.name === templateName());
  const nameInput = h('input', { value: data.name || '', oninput: e => { data.name = e.target.value; nameTouched = !!e.target.value; } });
  const specsBox = h('div', { class: 'grid' });
  const kb = isKbCat(data.category);
  const autoName = () => {
    if (nameTouched) return;
    data.name = templateName();
    nameInput.value = data.name;
  };
  const drawSpecs = () => {
    const inputs = specInputs(data.category, data.specs, (k, v) => {
      if (v) data.specs[k] = v; else delete data.specs[k];
      autoName();
    });
    setKids(specsBox, inputs.length ? inputs : h('div', { class: 'muted small' }, kb ? 'Nothing else to fill in. Fields can be added under Settings.' : 'This category has no fields. Add some under Settings.'));
  };
  const catSelect = selectEl(META.categories.map(c => [c.key, c.label]), data.category, {
    disabled: opts.lockCategory || null,
    onchange: e => { data.category = e.target.value; drawSpecs(); autoName(); },
  });
  drawSpecs();
  const tpl = CATS[data.category] && CATS[data.category].name_template;
  return h('div', null,
    section(opts.title || 'Part', h('div', { class: 'grid' },
      opts.lockCategory ? null : field('Category', catSelect),
      kb ? null : field('Name', nameInput),
      field(kb ? 'Brand' : 'Manufacturer', h('input', { value: data.manufacturer || '', oninput: e => { data.manufacturer = e.target.value; autoName(); } })),
      field(kb ? 'Model' : 'Model / part number', h('input', { value: data.model_number || '', oninput: e => { data.model_number = e.target.value; autoName(); } })),
      kb ? field('Name', nameInput) : null,
      tpl && !nameTouched ? h('div', { class: 'muted small wide' }, kb ? `The name fills in from ${data.category === 'keycaps' ? 'the brand, profile and model' : 'the brand and model'}.` : 'Leave the name blank and it fills in from the specs below.') : null)),
    section('Specifications', specsBox),
    opts.hideStock ? null : section('Stock', h('div', { class: 'grid' },
      field(kb ? (data.category === 'switch' ? 'On hand, not in a keyboard (individual switches)' : 'On hand, not on a keyboard') : 'Spare (not installed)', h('input', { type: 'number', min: 0, value: data.spare_qty ?? 0, oninput: e => { data.spare_qty = e.target.value; } })),
      field(kb ? 'Stored at' : 'Spares stored at', h('input', { list: 'dl-locations', value: data.location || '', placeholder: 'e.g. Storage, parts bin 3', oninput: e => { data.location = e.target.value; } })),
      field('Notes', h('textarea', { value: data.notes || '', oninput: e => { data.notes = e.target.value; } }), 'wide'))),
    datalist('dl-locations', META.locations));
}

function editItemModal(item, onSaved) {
  const data = JSON.parse(JSON.stringify(item));
  data.category = data.category || META.categories[0].key;
  openModal(item.id ? `Edit ${item.name}` : 'New inventory item', itemFormFields(data), [
    { label: 'Cancel' },
    {
      label: item.id ? 'Save changes' : 'Create item', class: 'primary', action: async () => {
        const body = {
          category: data.category, name: data.name, manufacturer: data.manufacturer || '', model_number: data.model_number || '',
          specs: data.specs, spare_qty: Number(data.spare_qty || 0), location: data.location || '', notes: data.notes || '',
        };
        const res = item.id ? await api('PUT', `/api/items/${item.id}`, body) : await api('POST', '/api/items', body);
        toast('Saved');
        onSaved(res.id);
      },
    },
  ]);
}

function itemOptionLabel(it) {
  // Add list-type specs missing from the name, so "16GB DDR4 (SODIMM)" and "16GB DDR4 (DIMM)" differ
  const cat = CATS[it.category];
  const extra = (cat ? cat.fields : []).filter(f => f.type === 'select')
    .map(f => it.specs[f.key]).filter(v => v && !it.name.toLowerCase().includes(v.toLowerCase()));
  return `${it.name}${extra.length ? ` (${extra.join(', ')})` : ''} — ${it.spare_qty} spare`;
}

/** Add a component to an asset: pick an existing inventory item or create one. Keyboards only take keyboard parts. */
async function addComponentModal(asset, onDone, category) {
  const kb = isKeyboard(asset);
  const cats = META.categories.filter(c => isKbCat(c.key) === kb);
  const items = (await api('GET', '/api/items')).filter(i => isKbCat(i.category) === kb);
  const state = { category: category || cats[0].key, item_id: '', qty: 1, slot: '', serial: '', notes: '', take: true, newItem: null };
  const box = h('div');
  const draw = () => {
    const isSwitch = state.category === 'switch';
    const inCat = items.filter(i => i.category === state.category);
    const itemSel = selectEl([['', '+ New item…'], ...inCat.map(i => [String(i.id), itemOptionLabel(i)])], state.item_id, {
      onchange: e => {
        state.item_id = e.target.value;
        const it = items.find(i => String(i.id) === state.item_id);
        state.take = !!(it && it.spare_qty > 0);
        draw();
      },
    });
    if (!state.item_id && !state.newItem) state.newItem = { category: state.category, specs: {}, spare_qty: 0 };
    const chosen = items.find(i => String(i.id) === state.item_id);
    setKids(box,
      section('Part', h('div', { class: 'grid' },
        category ? null : field('Category', selectEl(cats.map(c => [c.key, c.label]), state.category, {
          onchange: e => { state.category = e.target.value; state.item_id = ''; state.newItem = null; draw(); },
        })),
        field(kb ? catLabel(state.category) : 'Inventory item', itemSel),
        chosen ? h('div', { class: 'wide' }, specChips(chosen.category, chosen.specs)) : null,
        chosen ? h('label', { class: 'check wide' },
          h('input', { type: 'checkbox', checked: state.take, onchange: e => { state.take = e.target.checked; } }),
          kb ? `Take from stock (${chosen.spare_qty} on hand)` : `Take from spare stock (${chosen.spare_qty} available)`) : null)),
      !state.item_id ? h('div', { class: 'form-section' }, itemFormFields(state.newItem, { lockCategory: true, hideStock: !kb, title: kb ? `New ${catLabel(state.category).toLowerCase()}` : 'New item' })) : null,
      kb ? (isSwitch ? section('In this keyboard', h('div', { class: 'grid' },
        field('How many', h('input', { type: 'number', min: 1, value: state.qty, oninput: e => { state.qty = e.target.value; } })),
        field('Where (optional)', h('input', { value: state.slot, placeholder: 'e.g. alphas, mods, spacebar', oninput: e => { state.slot = e.target.value; } })))) : null)
        : section('Install', h('div', { class: 'grid' },
          field('Quantity', h('input', { type: 'number', min: 1, value: state.qty, oninput: e => { state.qty = e.target.value; } })),
          field('Slot', h('input', { value: state.slot, placeholder: 'e.g. DIMM A1, M.2 #2', oninput: e => { state.slot = e.target.value; } })),
          field('Serial', h('input', { value: state.serial, oninput: e => { state.serial = e.target.value; } })),
          field('Notes', h('input', { value: state.notes, oninput: e => { state.notes = e.target.value; } })))),
    );
  };
  draw();
  const what = kb ? catLabel(state.category).toLowerCase() : 'component';
  openModal(`Add ${what} to ${asset.name}`, box, [
    { label: 'Cancel' },
    {
      label: `Add ${what}`, class: 'primary', action: async () => {
        const body = { qty: Number(state.qty) || 1, slot: state.slot, serial: state.serial, notes: state.notes };
        if (state.item_id) { body.item_id = Number(state.item_id); body.take_from_spare = state.take; }
        else body.new_item = { ...state.newItem, category: state.category };
        if (kb && state.category !== 'switch') body.qty = 1;
        await api('POST', `/api/assets/${asset.id}/components`, body);
        toast(kb ? 'Added' : 'Component added');
        onDone();
      },
    },
  ]);
}

async function editInstallModal(comp, assetName, onDone) {
  const items = (await api('GET', '/api/items')).filter(i => i.category === comp.category);
  const state = { item_id: String(comp.id), qty: comp.qty, slot: comp.slot || '', serial: comp.serial || '', notes: comp.install_notes || '' };
  const body = h('div', null,
    section('Inventory item',
      field('Linked to', selectEl(items.map(i => [String(i.id), itemOptionLabel(i)]), state.item_id, { onchange: e => { state.item_id = e.target.value; } })),
      h('p', { class: 'muted small', style: 'margin:8px 0 0' }, 'To change specs (like DDR type or capacity), edit the inventory item itself, or link this to a different item.')),
    section('This install', h('div', { class: 'grid' },
      field('Quantity', h('input', { type: 'number', min: 1, value: state.qty, oninput: e => { state.qty = e.target.value; } })),
      field('Slot', h('input', { value: state.slot, oninput: e => { state.slot = e.target.value; } })),
      field('Serial', h('input', { value: state.serial, oninput: e => { state.serial = e.target.value; } })),
      field('Notes', h('input', { value: state.notes, oninput: e => { state.notes = e.target.value; } }), 'wide'))),
  );
  openModal(`${comp.name} in ${assetName}`, body, [
    { label: 'Cancel' },
    {
      label: 'Save changes', class: 'primary', action: async () => {
        await api('PUT', `/api/installs/${comp.install_id}`, { ...state, item_id: Number(state.item_id), qty: Number(state.qty) || 1 });
        toast('Saved');
        onDone();
      },
    },
  ]);
}

async function removeInstall(installId, label, onDone) {
  let toSpare = true;
  const extra = h('label', { class: 'check' },
    h('input', { type: 'checkbox', checked: true, onchange: e => { toSpare = e.target.checked; } }), 'Put it back into spare stock');
  if (!(await confirmModal('Remove component', `Remove ${label}?`, 'Remove', extra))) return;
  await api('DELETE', `/api/installs/${installId}?return_to_spare=${toSpare}`);
  toast(toSpare ? 'Removed, returned to spares' : 'Removed');
  onDone();
}

function activityCard(events) {
  if (!events || !events.length) return null;
  const list = h('ul', { class: 'timeline' });
  let all = false;
  const more = events.length > 6 ? h('button', { class: 'link small', onclick: () => { all = !all; draw(); } }) : null;
  const draw = () => {
    setKids(list, (all ? events : events.slice(0, 6)).map(e => h('li', null, e.message, h('time', { datetime: e.ts, title: fmtDate(e.ts) }, ago(e.ts)))));
    if (more) more.textContent = all ? 'Show less' : `Show all ${events.length}`;
  };
  draw();
  return h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', null, 'Activity'), more), list);
}

function propsList(rows) {
  return h('dl', { class: 'props' }, rows.map(([k, v]) => h('div', null, h('dt', null, k),
    v ? h('dd', null, v) : h('dd', { class: 'unset' }, '—'))));
}

function rowActions(...buttons) {
  return h('td', { class: 'actions-cell' }, h('div', { class: 'row-actions' }, buttons));
}

function iconButton(ic, label, onclick, cls) {
  return h('button', { class: 'icon-btn' + (cls ? ' ' + cls : ''), title: label, 'aria-label': label, onclick: e => { e.stopPropagation(); onclick(e); } }, icon(ic));
}

function searchInput(placeholder, value, oninput) {
  return h('div', { class: 'search-in' }, icon('search'), h('input', { type: 'search', placeholder, value, oninput }));
}

// ------------------------------------------------------------------ assets list

const ASSET_FILTERS = { q: '', status: '', type: '', location: '' };

async function assetsView() {
  setCrumbs(['Assets']);
  const assets = (await api('GET', '/api/assets')).filter(a => !isKeyboard(a));
  const hasGpu = assets.some(a => a.summary.gpu);
  const body = h('tbody');
  const headRow = h('tr');
  const cols = [
    { key: 'name', label: 'Asset', sort: a => a.name },
    { key: 'status', label: 'Status', sort: a => a.status },
    { key: 'cpu', label: 'CPU', sort: a => a.summary.cpu },
    { key: 'ram', label: 'Memory', sort: a => sizeGB(a.summary.ram) },
    { key: 'storage', label: 'Storage', sort: a => sizeGB(a.summary.storage) },
    hasGpu ? { key: 'gpu', label: 'GPU', sort: a => a.summary.gpu } : null,
    { key: 'location', label: 'Location', cls: 'hide-sm', sort: a => a.location },
  ].filter(Boolean);
  const tabs = h('div', { class: 'tabs' });
  const count = h('span', { class: 'count' });
  const statuses = [...new Set([...META.statuses, ...assets.map(a => a.status).filter(Boolean)])];

  const drawTabs = () => {
    const n = s => assets.filter(a => a.status === s).length;
    setKids(tabs,
      h('button', { class: ASSET_FILTERS.status ? '' : 'active', onclick: () => { ASSET_FILTERS.status = ''; drawTabs(); draw(); } }, 'All ', h('span', { class: 'n' }, assets.length)),
      statuses.filter(s => n(s)).map(s => h('button', {
        class: ASSET_FILTERS.status === s ? 'active' : '', onclick: () => { ASSET_FILTERS.status = s; drawTabs(); draw(); },
      }, s, ' ', h('span', { class: 'n' }, n(s)))));
  };
  const cell = v => h('td', { class: 'small' }, v || h('span', { class: 'faint' }, '—'));
  const draw = () => {
    const q = ASSET_FILTERS.q.toLowerCase();
    const rows = sortRows('assets', assets.filter(a =>
      (!ASSET_FILTERS.status || a.status === ASSET_FILTERS.status) &&
      (!ASSET_FILTERS.type || a.asset_type === ASSET_FILTERS.type) &&
      (!ASSET_FILTERS.location || a.location === ASSET_FILTERS.location) &&
      (!q || JSON.stringify(a).toLowerCase().includes(q))), cols);
    setKids(headRow, sortHeads('assets', cols, draw));
    count.textContent = `${rows.length} of ${assets.length}`;
    setKids(body, ...rows.map(a => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/assets/${a.id}`; } },
      h('td', null, h('a', { class: 'ent', href: `#/assets/${a.id}`, onclick: e => e.stopPropagation() }, typeIcon(a.asset_type),
        h('div', null, h('div', { class: 't' }, a.name), h('div', { class: 's' }, [a.asset_type, makerModel(a), a.ports ? `${a.ports.up}/${a.ports.total} ports up` : ''].filter(Boolean).join(' · ') || a.hostname || '')))),
      h('td', null, statusPill(a.status)),
      cell(a.summary.cpu), cell(a.summary.ram), h('td', { class: 'small', style: 'max-width:260px' }, a.summary.storage || h('span', { class: 'faint' }, '—')),
      hasGpu ? cell(a.summary.gpu) : null,
      h('td', { class: 'small nowrap hide-sm' }, a.location ? [icon('pin', 'faint'), ' ', a.location] : h('span', { class: 'faint' }, '—')),
    )));
    if (!rows.length) body.append(h('tr', null, h('td', { colspan: 8 },
      assets.length ? emptyState('search', 'No matches', 'Try a different search or filter.')
        : emptyState('monitor', 'No assets yet', 'Scan a machine or add one by hand.', h('a', { class: 'btn primary', href: '#/assets/new' }, icon('plus'), 'Add asset')))));
  };
  const filterSel = (key, options, label) => selectEl([['', label], ...options], ASSET_FILTERS[key], {
    onchange: e => { ASSET_FILTERS[key] = e.target.value; draw(); },
  });
  drawTabs();
  draw();
  const types = [...new Set(assets.map(a => a.asset_type).filter(Boolean))];
  const locations = [...new Set(assets.map(a => a.location).filter(Boolean))].sort();
  return [
    h('div', { class: 'page-head' },
      h('div', null, h('h1', null, 'Assets'), h('div', { class: 'sub' }, 'Machines and what is inside them')),
      h('div', { class: 'actions' }, h('a', { class: 'btn primary', href: '#/assets/new' }, icon('plus'), 'Add asset'))),
    h('section', { class: 'card' },
      tabs,
      h('div', { class: 'filters' },
        searchInput('Search name, model, CPU…', ASSET_FILTERS.q, e => { ASSET_FILTERS.q = e.target.value; draw(); }),
        filterSel('type', types, 'All types'),
        filterSel('location', locations, 'All locations'),
        h('span', { class: 'grow' }), count),
      h('div', { class: 'table-wrap' }, h('table', null,
        h('thead', null, headRow),
        body))),
  ];
}

// ------------------------------------------------------------------ asset detail

async function assetView(id) {
  const a = await api('GET', `/api/assets/${id}`);
  if (isKeyboard(a)) return keyboardPage(a);
  setCrumbs(['Assets', '#/assets'], [a.name]);
  const reload = () => route();
  const groups = {};
  for (const c of a.components) (groups[c.category] = groups[c.category] || []).push(c);
  const catKeys = Object.keys(groups).sort((x, y) => catOrder(x) - catOrder(y));
  const nParts = a.components.reduce((s, c) => s + c.qty, 0);
  const net = isNetwork(a);

  const compTable = h('div', { class: 'table-wrap' }, h('table', null,
    h('thead', null, h('tr', null, h('th', null, 'Part'), h('th', { class: 'hide-sm' }, 'Specs'), h('th', { class: 'num' }, 'Qty'), h('th', { class: 'hide-sm' }, 'Slot'), h('th', { class: 'hide-sm' }, 'Serial'), h('th', null, ''))),
    h('tbody', null, catKeys.map(key => [
      h('tr', { class: 'group' }, h('td', { colspan: 6 }, h('div', { class: 'ent' }, catIcon(key, 'sm'),
        h('h3', null, catLabel(key)), h('span', { class: 'faint small' }, groups[key].reduce((s, c) => s + c.qty, 0))))),
      groups[key].map(c => h('tr', null,
        h('td', null, h('div', { class: 'ent' }, h('div', null,
          h('a', { class: 't', href: `#/inventory/${c.id}` }, c.name),
          h('div', { class: 's' }, [c.manufacturer, c.model_number].filter(Boolean).join(' · ') || (c.install_notes || '')),
          h('div', { class: 'show-sm' }, specChips(c.category, c.specs, { compact: true }), c.slot ? h('div', { class: 's' }, `Slot ${c.slot}`) : null)))),
        h('td', { class: 'hide-sm' }, specChips(c.category, c.specs, { compact: true })),
        h('td', { class: 'num' }, h('span', { class: 'qty' }, c.qty)),
        h('td', { class: 'small nowrap hide-sm' }, c.slot || h('span', { class: 'faint' }, '—')),
        h('td', { class: 'small hide-sm' }, c.serial ? h('code', null, c.serial) : h('span', { class: 'faint' }, '—')),
        rowActions(
          iconButton('edit', 'Edit', () => editInstallModal(c, a.name, reload)),
          iconButton('trash', 'Remove', () => removeInstall(c.install_id, `${c.name} from ${a.name}`, reload), 'danger')),
      )),
    ]))));

  const deleteAsset = async () => {
    let toSpare = true;
    const extra = a.components.length ? h('label', { class: 'check' },
      h('input', { type: 'checkbox', checked: true, onchange: e => { toSpare = e.target.checked; } }),
      `Put its ${a.components.length} component${a.components.length > 1 ? 's' : ''} back into spare stock`) : null;
    if (!(await confirmModal('Delete asset', `Delete ${a.name}? This can't be undone.`, 'Delete', extra))) return;
    await api('DELETE', `/api/assets/${a.id}?return_to_spare=${toSpare}`);
    toast('Asset deleted');
    location.hash = '#/assets';
  };

  const meta = [
    a.asset_type ? h('span', null, icon('tag'), a.asset_type) : null,
    makerModel(a) ? h('span', null, icon('monitor'), makerModel(a)) : null,
    a.location ? h('span', null, icon('pin'), a.location) : null,
    a.hostname ? h('span', null, icon('globe'), a.hostname) : null,
  ];

  return [
    h('div', { class: 'record-head' },
      typeIcon(a.asset_type, 'lg'),
      h('div', { class: 'body' },
        h('div', { class: 'title-row' }, h('h1', null, a.name), statusPill(a.status)),
        h('div', { class: 'meta' }, meta)),
      h('div', { class: 'actions' },
        h('button', { onclick: () => editAssetModal(a, reload) }, icon('edit'), 'Edit'),
        net && !a.components.length ? iconButton('plus', 'Add component', () => addComponentModal(a, reload)) : null,
        net ? h('button', { class: 'primary', onclick: () => snmpModal(a) }, icon('refresh'), 'Refresh via SNMP')
          : h('button', { class: 'primary', onclick: () => rescanModal(a) }, icon('scan'), 'Scan hardware'),
        iconButton('trash', 'Delete asset', deleteAsset, 'danger'))),
    h('div', { class: 'record-grid' },
      h('div', null,
        a.ports.length ? portsCard(a) : null,
        net && !a.components.length ? null : h('section', { class: 'card' },
          h('div', { class: 'card-head' },
            h('h2', null, 'Components', h('span', { class: 'n' }, nParts || '')),
            h('button', { class: 'small', onclick: () => addComponentModal(a, reload) }, icon('plus'), 'Add component')),
          catKeys.length ? compTable
            : emptyState('boxes', 'No components yet', 'Scan the machine or add parts by hand.',
              h('button', { class: 'primary', onclick: () => rescanModal(a) }, icon('scan'), 'Scan hardware')))),
      h('div', null,
        h('section', { class: 'card' },
          h('div', { class: 'card-head' }, h('h2', null, 'Details'), h('button', { class: 'link small', onclick: () => editAssetModal(a, reload) }, 'Edit')),
          propsList([
            ['Hostname', a.hostname], ['Address', a.address], [net ? 'Firmware' : 'Operating system', a.os],
            ['Manufacturer', a.manufacturer], ['Model', a.model], ['Model number', a.model_number],
            ['Serial', a.serial && h('code', null, a.serial)], ['Location', a.location],
            ['MAC addresses', a.macs && h('code', null, a.macs.split(', ').slice(0, 4).join(', ') + (a.macs.split(', ').length > 4 ? ' …' : ''))],
            ['Last scanned', a.last_scan ? h('span', null, ago(a.last_scan), ' · ', h('a', { href: `/api/assets/${a.id}/raw`, target: '_blank' }, 'raw')) : null],
          ])),
        networkCard(a),
        a.notes ? h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', null, 'Notes')), h('div', { class: 'card-body' }, h('p', { class: 'notes' }, a.notes))) : null,
        activityCard(a.events))),
  ];
}

/** Where a machine plugs in, worked out from switch MAC tables and LLDP. */
function networkCard(a) {
  if (!a.links.length && !a.uplink) return null;
  const reload = () => route();
  return h('section', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', null, 'Network'),
      h('div', { class: 'actions' }, h('a', { class: 'link small', href: '#/map' }, 'Map'),
        h('button', { class: 'link small', onclick: () => connectionModal(a, reload) }, 'Change'))),
    a.uplink ? h('div', { class: 'links' }, h('a', { class: 'link-row', href: `#/assets/${a.uplink.id}` }, typeIcon(a.uplink.asset_type, 'sm'),
      h('div', null, h('div', { class: 't' }, a.uplink.name, a.uplink_port ? [h('span', { class: 'muted' }, ' · '), h('span', { class: 'mono' }, a.uplink_port)] : null),
        h('div', { class: 's' }, (a.uplink.asset_type || '').toLowerCase() === 'access point' ? 'Wi-Fi · set by hand' : 'Plugged in here · set by hand')))) : null,
    a.uplink && a.links.length ? h('div', { class: 'faint small', style: 'padding:8px 16px 0' }, 'Switch scans see it on:') : null,
    h('div', { class: 'links' }, a.links.map(l => h('a', { class: 'link-row', href: `#/assets/${l.device.id}` },
      typeIcon(l.device.asset_type, 'sm'),
      h('div', null,
        h('div', { class: 't' }, l.device.name, h('span', { class: 'muted' }, ' · '), h('span', { class: 'mono' }, l.port), l.alias ? h('span', { class: 'muted' }, ` “${l.alias}”`) : null),
        h('div', { class: 's' }, [l.oper === 'up' && l.speed ? fmtSpeed(l.speed) : l.oper, l.vlan ? `VLAN ${l.vlan}` : '',
          l.via && !a.uplink ? `through ${l.via.name}` : '', l.lldp ? 'LLDP' : ''].filter(Boolean).join(' · ')))))));
}

function portLabel(p) {
  const m = /(\d+)$/.exec(p.name || '');
  return m ? m[1] : p.name;
}

function portState(p) {
  if (p.admin === 'down') return 'off';
  return p.oper === 'up' ? 'up' : 'down';
}

function portStatusPill(p) {
  const st = portState(p);
  if (st === 'up') return h('span', { class: 'pill p-green' }, ['Up', fmtSpeed(p.speed)].filter(Boolean).join(' · '));
  if (st === 'off') return h('span', { class: 'pill p-amber' }, 'Disabled');
  return h('span', { class: 'pill' }, 'Down');
}

/** Front-panel view: ports in pairs (odd on top, even below), in blocks of 12 like most switches. */
function faceplate(ports, onPick, selected) {
  const blocks = [];
  for (let i = 0; i < ports.length; i += 12) blocks.push(ports.slice(i, i + 12));
  return h('div', { class: 'faceplate' }, blocks.map(block => h('div', { class: 'fp-block' }, block.map(p => {
    const who = (p.devices || []).map(d => d.name);
    const n = (p.devices || []).length + (p.unknown || []).length;
    const tip = [`${p.name}${p.alias ? ` “${p.alias}”` : ''}`, portState(p) === 'up' ? `Up ${fmtSpeed(p.speed)}` : portState(p) === 'off' ? 'Disabled' : 'Down',
      who.length ? who.join(', ') : '', n > who.length ? `${n - who.length} other device${n - who.length > 1 ? 's' : ''}` : ''].filter(Boolean).join('\n');
    return h('button', {
      class: `fp-port ${portState(p)}${who.length ? ' known' : ''}${selected === p.name ? ' sel' : ''}`, title: tip,
      'aria-label': tip.replace(/\n/g, ', '), onclick: onPick ? () => onPick(p) : null,
    }, portLabel(p));
  }))));
}

function portsCard(a) {
  const state = { show: 'physical', q: '', sel: null };
  const eth = a.ports.filter(p => p.kind === 'ethernet');
  const up = eth.filter(p => p.oper === 'up').length;
  const plateBox = h('div');
  const body = h('tbody');
  const expanded = new Set();

  const pick = p => {
    state.sel = state.sel === p.name ? null : p.name;
    draw();
    const row = body.querySelector(`[data-port="${CSS.escape(p.name)}"]`);
    if (row) row.scrollIntoView({ block: 'center', behavior: 'smooth' });
  };
  const connected = p => {
    const out = (p.devices || []).map(d => h('a', { class: 'dev', href: `#/assets/${d.id}`, title: d.via === 'lldp' ? 'Seen via LLDP' : 'Seen in the MAC table' },
      typeIcon(d.asset_type, 'xs'), d.name));
    const nb = p.neighbor || {};
    const nbName = nb.name || nb.port_desc || nb.chassis || nb.port;
    if (!p.devices.length && nbName) {
      out.push(h('span', { class: 'dev plain', title: [nb.descr, nb.chassis].filter(Boolean).join('\n') }, icon('link'), nbName,
        nb.port && nb.port !== nbName ? h('span', { class: 'faint' }, ` (${nb.port})`) : null));
    }
    if (p.unknown.length) {
      const open = expanded.has(p.name);
      out.push(h('button', { class: 'link small', onclick: e => { e.stopPropagation(); open ? expanded.delete(p.name) : expanded.add(p.name); draw(); } },
        open ? 'Hide' : `${p.devices.length ? '+' : ''}${p.unknown.length} unknown device${p.unknown.length > 1 ? 's' : ''}`));
      if (open) out.push(h('div', { class: 'macs' }, p.unknown.map(u => h('div', null, h('code', null, u.mac), u.ip ? h('span', { class: 'muted' }, ` ${u.ip}`) : null))));
    }
    return out.length ? h('div', { class: 'devs' }, out) : h('span', { class: 'faint' }, '—');
  };
  const draw = () => {
    if (eth.length >= 5) setKids(plateBox, faceplate(eth, pick, state.sel));
    const q = state.q.toLowerCase();
    const rows = a.ports.filter(p =>
      (state.show === 'all' || (state.show === 'connected' ? p.devices.length || p.unknown.length || Object.keys(p.neighbor || {}).length : ['ethernet', 'wifi', 'lag'].includes(p.kind))) &&
      (!q || JSON.stringify([p.name, p.alias, p.vlan, p.devices.map(d => d.name), p.unknown, p.neighbor]).toLowerCase().includes(q)));
    setKids(body, rows.map(p => h('tr', { 'data-port': p.name, class: state.sel === p.name ? 'selected' : '' },
      h('td', { class: 'nowrap' }, h('div', { class: 't mono' }, p.name), p.alias ? h('div', { class: 's' }, p.alias) : null),
      h('td', null, portStatusPill(p)),
      h('td', { class: 'small nowrap hide-sm' }, p.vlan || h('span', { class: 'faint' }, '—')),
      h('td', null, connected(p)))));
    if (!rows.length) body.append(h('tr', null, h('td', { colspan: 4 }, emptyState('search', 'No ports match'))));
  };
  draw();
  return h('section', { class: 'card' },
    h('div', { class: 'card-head' },
      h('h2', null, 'Ports', h('span', { class: 'n' }, eth.length ? `${up} of ${eth.length} up` : '')),
      h('span', { class: 'hint' }, 'From SNMP; connected devices come from the MAC table and LLDP')),
    plateBox,
    h('div', { class: 'filters' },
      searchInput('Search ports, devices, MACs…', '', e => { state.q = e.target.value; draw(); }),
      selectEl([['physical', 'Physical ports'], ['connected', 'Only with devices'], ['all', 'All interfaces']], state.show, { onchange: e => { state.show = e.target.value; draw(); } })),
    h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, h('th', null, 'Port'), h('th', null, 'Status'), h('th', { class: 'hide-sm' }, 'VLAN'), h('th', null, 'Connected'))),
      body)));
}

const SNMP_DEFAULTS = { host: '', version: '2c', community: '', user: '', level: 'authPriv', auth_proto: 'SHA', auth_pass: '', priv_proto: 'AES', priv_pass: '', remember: false };

function snmpForm(state, opts = {}) {
  const box = h('div');
  const inp = (key, props = {}) => h('input', { value: state[key] || '', ...props, oninput: e => { state[key] = e.target.value; } });
  const sel = (key, options) => selectEl(options, state[key], { onchange: e => { state[key] = e.target.value; draw(); } });
  const secret = key => inp(key, { type: 'password', autocomplete: 'new-password', placeholder: opts.saved ? 'Saved; leave blank to keep' : '' });
  const draw = () => {
    const v3 = state.version === '3';
    setKids(box, h('div', { class: 'grid' },
      field('Host or IP', inp('host', { placeholder: '192.168.1.112' })),
      field('SNMP version', sel('version', [['2c', 'v2c'], ['3', 'v3'], ['1', 'v1 (slow, use v2c if the device has it)']])),
      !v3 ? field('Community string', secret('community')) : null,
      v3 ? field('Username', inp('user', { autocomplete: 'off' })) : null,
      v3 ? field('Security level', sel('level', [['authPriv', 'Auth + privacy (authPriv)'], ['authNoPriv', 'Auth only (authNoPriv)'], ['noAuthNoPriv', 'None (noAuthNoPriv)']])) : null,
      v3 && state.level !== 'noAuthNoPriv' ? field('Auth protocol', sel('auth_proto', ['SHA', 'MD5', 'SHA-224', 'SHA-256', 'SHA-384', 'SHA-512'])) : null,
      v3 && state.level !== 'noAuthNoPriv' ? field('Auth password', secret('auth_pass')) : null,
      v3 && state.level === 'authPriv' ? field('Privacy protocol', sel('priv_proto', ['AES', 'DES', 'AES192', 'AES256'])) : null,
      v3 && state.level === 'authPriv' ? field('Privacy password', secret('priv_pass')) : null,
      h('label', { class: 'check wide' }, h('input', { type: 'checkbox', checked: state.remember, onchange: e => { state.remember = e.target.checked; } }),
        'Remember the credentials so refreshing is one click (stored unencrypted in Rigbook’s database)'),
      h('div', { class: 'muted small wide' }, 'Read-only access is all Rigbook needs. A host:port like 192.168.1.112:1161 works for non-standard ports.')));
  };
  draw();
  return box;
}

async function runSnmp(state, btn, extra = {}) {
  if (!state.host) throw new Error('Host is required');
  return waitForJob(() => api('POST', '/api/discover/snmp', { ...state, ...extra }), btn);
}

function snmpModal(a) {
  const state = { ...SNMP_DEFAULTS, ...(a.snmp || {}), host: a.address || a.hostname || '', remember: a.snmp_saved };
  openModal(`Refresh ${a.name}`, h('div', null,
    a.snmp_saved ? h('div', { class: 'alert info' }, icon('info'), h('div', null, 'Saved credentials will be used. Fill a field in only to change it.')) : null,
    snmpForm(state, { saved: a.snmp_saved }),
    h('p', { class: 'muted small' }, 'Nothing changes until you review the results.')), [
    { label: 'Cancel' },
    {
      label: 'Read device', class: 'primary', action: async () => {
        const btn = document.querySelector('.modal-foot button.primary');
        const scanId = await runSnmp(state, btn, { asset_id: a.id, use_saved: a.snmp_saved });
        location.hash = `#/review/${scanId}?asset=${a.id}`;
      },
    },
  ]);
}

function sshForm(state) {
  return h('div', { class: 'grid' },
    field('Host or IP', h('input', { value: state.host, placeholder: 'proxub.bacon or 192.168.1.20', oninput: e => { state.host = e.target.value; } })),
    field('SSH port', h('input', { type: 'number', value: state.port, oninput: e => { state.port = e.target.value; } })),
    field('Username', h('input', { value: state.username, autocomplete: 'off', oninput: e => { state.username = e.target.value; } })),
    field('Password', h('input', { type: 'password', value: state.password, autocomplete: 'new-password', oninput: e => { state.password = e.target.value; } })),
    h('div', { class: 'muted small wide' },
      'Linux: the password is also used for sudo so memory details and serials can be read. ',
      'Windows: needs the OpenSSH Server feature. Passwords are never stored.'));
}

/** Start a discovery job and poll it until it has a scan id, showing progress on the button. */
async function waitForJob(start, btn) {
  const label = btn ? [...btn.childNodes] : null;
  const show = text => { if (btn) setKids(btn, h('span', { class: 'spinner' }), text); };
  show('Starting…');
  try {
    const { job_id } = await start();
    for (;;) {
      await new Promise(r => setTimeout(r, 1000));
      const job = await api('GET', `/api/jobs/${job_id}`);
      if (job.status === 'done') return job.scan_id;
      if (job.status === 'error') throw new Error(job.error);
      show(`${job.progress} · ${job.seconds}s`);
    }
  } finally {
    if (btn) setKids(btn, label);
  }
}

async function runDiscover(state, btn) {
  if (!state.host || !state.username) throw new Error('Host and username are required');
  return waitForJob(() => api('POST', '/api/discover', { host: state.host, port: Number(state.port) || 22, username: state.username, password: state.password, asset_id: state.asset_id || null }), btn);
}

function rescanModal(a) {
  const state = { host: a.address || a.hostname || '', port: 22, username: a.scan_user || '', password: '', asset_id: a.id };
  const close = openModal(`Scan ${a.name}`, h('div', null,
    section('Connect over SSH', sshForm(state)),
    section(null, h('div', { class: 'alert info', style: 'margin:0' }, icon('info'),
      h('div', null, 'Nothing changes until you review the results. No SSH? ',
        h('a', { href: '#/assets/new?method=script', onclick: () => close() }, 'Run the scan script on the machine'), ' instead.')))), [
    { label: 'Cancel' },
    {
      label: 'Scan now', class: 'primary', action: async () => {
        const btn = document.querySelector('.modal-foot button.primary');
        const scanId = await runDiscover(state, btn);
        location.hash = `#/review/${scanId}?asset=${a.id}`;
      },
    },
  ]);
}

// ------------------------------------------------------------------ new asset

const NEW_METHODS = [
  ['ssh', 'terminal', 'Discover over SSH', 'Connects with a username and password and reads the hardware. Linux, FreeBSD/OPNsense or Windows (OpenSSH).'],
  ['snmp', 'switch', 'Network device (SNMP)', 'Switches, access points and routers. Reads the model, serial, firmware, ports, and what is plugged into each port.'],
  ['script', 'file', 'Run a script', 'For wiped machines on a live USB or Windows boxes without SSH. Results show up under Scans.'],
  ['manual', 'edit', 'Enter manually', 'Type in the details now and add components afterwards.'],
];

async function newAssetView(method) {
  setCrumbs(['Assets', '#/assets'], ['Add asset']);
  const state = { method: NEW_METHODS.some(m => m[0] === method) ? method : 'ssh' };
  const ssh = { host: '', port: 22, username: '', password: '' };
  const manual = { status: META.statuses[0] || '' };
  const err = h('div');
  const seg = h('div', { class: 'segmented' });
  const panel = h('div', { class: 'card-body' });

  const scanBtn = h('button', { class: 'primary' }, icon('scan'), 'Scan now');
  scanBtn.addEventListener('click', async () => {
    scanBtn.disabled = true; setKids(err);
    try {
      const scanId = await runDiscover(ssh, scanBtn);
      location.hash = `#/review/${scanId}`;
    } catch (e) {
      setKids(err, h('div', { class: 'alert error' }, icon('alert'), e.message));
    } finally { scanBtn.disabled = false; }
  });
  const snmpState = { ...SNMP_DEFAULTS };
  const snmpBtn = h('button', { class: 'primary' }, icon('switch'), 'Read device');
  snmpBtn.addEventListener('click', async () => {
    snmpBtn.disabled = true; setKids(err);
    try {
      location.hash = `#/review/${await runSnmp(snmpState, snmpBtn)}`;
    } catch (e) {
      setKids(err, h('div', { class: 'alert error' }, icon('alert'), e.message));
    } finally { snmpBtn.disabled = false; }
  });
  const saveManual = h('button', { class: 'primary' }, 'Create asset');
  saveManual.addEventListener('click', async () => {
    try {
      const res = await api('POST', '/api/assets', assetBody(manual));
      toast('Asset created');
      location.hash = `#/assets/${res.id}`;
    } catch (e) { toast(e.message, true); }
  });

  const forms = {
    ssh: () => [err, sshForm(ssh), h('div', { class: 'actions', style: 'margin-top:18px' }, scanBtn)],
    snmp: () => [err, snmpForm(snmpState), h('div', { class: 'actions', style: 'margin-top:18px' }, snmpBtn)],
    script: () => scriptInstructions(),
    manual: () => [assetFormFields(manual), h('div', { class: 'actions', style: 'margin-top:18px' }, saveManual)],
  };
  const draw = () => {
    setKids(seg, NEW_METHODS.map(([key, ic, label]) => h('button', {
      class: state.method === key ? 'active' : '', onclick: () => { state.method = key; draw(); },
    }, icon(ic), label)));
    const [, ic, label, desc] = NEW_METHODS.find(m => m[0] === state.method);
    setKids(panel,
      h('div', { class: 'method-head' }, h('span', { class: 'ico c-blue' }, icon(ic)), h('div', null, h('h2', null, label), h('p', null, desc))),
      forms[state.method]());
  };
  draw();
  return [
    h('div', { class: 'page-head' },
      h('div', null, h('h1', null, 'Add asset'), h('div', { class: 'sub' }, 'Pull the hardware in automatically, or enter it by hand. Every scan is reviewed before it is saved.'))),
    h('div', { style: 'margin-bottom:14px' }, seg),
    h('section', { class: 'card', style: 'max-width:900px' }, panel),
  ];
}

function copyCmd(text) {
  return h('div', { class: 'cmd' }, h('code', null, text),
    h('button', { class: 'small', onclick: async () => {
      try { await navigator.clipboard.writeText(text); toast('Copied'); }
      catch { toast('Copy failed; select the text instead', true); }
    } }, icon('copy'), 'Copy'));
}

function scriptInstructions() {
  const origin = location.origin;
  return h('div', null,
    h('label', { class: 'field' }, 'Linux (live USB works too)'),
    copyCmd(`curl -fsSL ${origin}/scan.sh | sudo sh`),
    h('label', { class: 'field' }, 'Windows (PowerShell)'),
    copyCmd(`irm ${origin}/scan.ps1 | iex`),
    h('label', { class: 'field' }, 'FreeBSD / OPNsense / pfSense (as root, shell option 8)'),
    copyCmd(`curl -fsSL ${origin}/scan-freebsd.sh | sh`),
    h('div', { class: 'muted small' }, 'The result is sent back here and waits under ', h('a', { href: '#/scans' }, 'Scans'), ' for review.'));
}

// ------------------------------------------------------------------ scans

async function scansView() {
  setCrumbs(['Scans']);
  const scans = await api('GET', '/api/scans');
  const paste = h('textarea', { placeholder: 'Paste the output of scan.sh, scan-freebsd.sh or scan.ps1 here', style: 'min-height:120px' });
  const upload = async text => {
    try {
      const res = await api('POST', '/api/scans', text);
      location.hash = `#/review/${res.scan_id}`;
    } catch (e) { toast(e.message, true); }
  };
  const fileInput = h('input', { type: 'file', accept: '.txt,.json,text/plain,application/json', style: 'max-width:320px', onchange: async e => {
    const f = e.target.files[0];
    if (f) upload(await f.text());
  } });
  return [
    h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Scans'), h('div', { class: 'sub' }, 'Hardware scans waiting for review'))),
    h('section', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', null, 'Waiting for review', h('span', { class: 'n' }, scans.length || ''))),
      scans.length ? h('div', { class: 'table-wrap' }, h('table', null,
        h('thead', null, h('tr', null, h('th', null, 'Machine'), h('th', null, 'Source'), h('th', null, 'Received'), h('th', null, ''))),
        h('tbody', null, scans.map(s => {
          const href = `#/review/${s.id}${s.asset_id ? '?asset=' + s.asset_id : ''}`;
          return h('tr', { class: 'clickable', onclick: () => { location.hash = href; } },
            h('td', null, h('div', { class: 'ent' }, h('span', { class: 'ico c-blue' }, icon(/^snmp/.test(s.source) ? 'switch' : 'scan')),
              h('div', null, h('div', { class: 't' }, s.name || 'Unknown machine'), h('div', { class: 's' }, makerModel(s) || '—')))),
            h('td', { class: 'small' }, h('span', { class: 'pill plain' }, s.source)),
            h('td', { class: 'small nowrap', title: fmtDate(s.created_at) }, ago(s.created_at)),
            h('td', { class: 'actions-cell' },
              h('button', { class: 'small ghost', onclick: async e => { e.stopPropagation(); await api('DELETE', `/api/scans/${s.id}`); await loadMeta(); route(); } }, 'Discard'), ' ',
              h('a', { class: 'btn small primary', href, onclick: e => e.stopPropagation() }, 'Review')));
        })))) : emptyState('check', 'All caught up', 'New scans land here until you review them.')),
    h('div', { class: 'record-grid' },
      h('section', { class: 'card' },
        h('div', { class: 'card-head' }, h('h2', null, 'Scan with the script')),
        h('div', { class: 'card-body' }, scriptInstructions())),
      h('section', { class: 'card' },
        h('div', { class: 'card-head' }, h('h2', null, 'Upload saved output')),
        h('div', { class: 'card-body' },
          fileInput,
          h('div', { style: 'margin-top:12px' }, paste),
          h('div', { class: 'actions', style: 'margin-top:10px' }, h('button', { onclick: () => paste.value.trim() && upload(paste.value) }, icon('upload'), 'Upload pasted output'))))),
  ];
}

// ------------------------------------------------------------------ review

const REVIEW_FIELDS = [
  ['name', 'Name'], ['asset_type', 'Type'], ['hostname', 'Hostname'], ['address', 'Address'], ['os', 'Operating system'],
  ['manufacturer', 'Manufacturer'], ['model', 'Model'], ['model_number', 'Model number'], ['serial', 'Serial'],
];

async function reviewView(scanId, assetIdParam) {
  const [scan, items, assets] = await Promise.all([
    api('GET', `/api/scans/${scanId}${assetIdParam ? '?asset_id=' + assetIdParam : ''}`),
    api('GET', '/api/items'),
    api('GET', '/api/assets'),
  ]);
  const scanned = { ...scan.asset, address: scan.address || scan.asset.ip || '' };
  setCrumbs(['Scans', '#/scans'], [`Review ${scanned.name || 'scan'}`]);
  const rv = {
    assetId: scan.asset_id || scan.suggested_asset_id || null,
    current: null,
    fields: { ...scanned },
    use: {},
    comps: scan.components.map(c => ({ ...c, specs: { ...c.specs }, take_from_spare: false, linkManual: false, autoName: c.name })),
    replace: {},
    toSpare: false,
  };
  rv.fields.status = rv.fields.status || META.statuses[0] || '';
  rv.fields.location = rv.fields.location || scanned.snmp_location || '';
  const net = scan.kind === 'network';
  const root = h('div');

  const loadCurrent = async () => {
    rv.current = rv.assetId ? await api('GET', `/api/assets/${rv.assetId}`) : null;
    rv.use = {};
    for (const [k] of REVIEW_FIELDS) {
      const cur = rv.current ? rv.current[k] || '' : '';
      rv.use[k] = !rv.current || (!cur && !!scanned[k]) || (k === 'address' && !!scanned[k] && cur !== scanned[k]);
    }
    rv.replace = {};
    // Unchanged parts: tick it (just refreshes serials/slots). Different from what's recorded: leave it to the
    // user, since the recorded parts may be a deliberate override of what the scan reports.
    for (const key of scanCats()) {
      const hasCurrent = rv.current && rv.current.components.some(c => c.category === key);
      rv.replace[key] = !hasCurrent || !catDiffers(key);
    }
  };
  const scanCats = () => [...new Set(rv.comps.map(c => c.category))].sort((x, y) => catOrder(x) - catOrder(y));
  const tally = list => {
    const m = {};
    for (const [k, q] of list) m[k] = (m[k] || 0) + q;
    return JSON.stringify(Object.entries(m).sort());
  };
  const catDiffers = key => {
    const cur = (rv.current ? rv.current.components : []).filter(c => c.category === key).map(c => [String(c.id), c.qty]);
    const sc = rv.comps.filter(c => c.category === key && c.include).map(c => [c.item_id ? String(c.item_id) : 'new:' + c.name, Number(c.qty) || 1]);
    return tally(cur) !== tally(sc);
  };

  const fieldsSection = () => {
    if (!rv.current) {
      return h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', null, 'New asset'), h('span', { class: 'hint' }, 'Filled in from the scan; change anything')),
        h('div', { class: 'card-body' }, assetFormFields(rv.fields)));
    }
    return h('section', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', null, `Details for ${rv.current.name}`), h('span', { class: 'hint' }, 'Ticked fields get the new value')),
      h('div', { class: 'table-wrap' }, h('table', { class: 'field-table' },
        h('thead', null, h('tr', null, h('th', null, ''), h('th', null, 'Field'), h('th', null, 'Current'), h('th', null, 'New value'))),
        h('tbody', null, REVIEW_FIELDS.map(([k, label]) => {
          const input = k === 'asset_type'
            ? selectEl([['', '—'], ...META.asset_types], rv.fields[k] || '', { onchange: e => { rv.fields[k] = e.target.value; rv.use[k] = true; cb.checked = true; } })
            : h('input', { value: rv.fields[k] || '', oninput: e => { rv.fields[k] = e.target.value; rv.use[k] = true; cb.checked = true; } });
          const cb = h('input', { type: 'checkbox', checked: rv.use[k], onchange: e => { rv.use[k] = e.target.checked; } });
          return h('tr', null, h('td', null, cb), h('td', { class: 'nowrap strong' }, k === 'os' && net ? 'Firmware' : label),
            h('td', { class: 'cur' }, rv.current[k] || '—'), h('td', null, input));
        })))));
  };

  const rowEl = c => {
    const cat = CATS[c.category];
    const inCat = items.filter(i => i.category === c.category);
    const linkSel = selectEl([['', '+ Create new item'], ...inCat.map(i => [String(i.id), itemOptionLabel(i)])], c.item_id ? String(c.item_id) : '', {
      onchange: e => {
        c.item_id = e.target.value ? Number(e.target.value) : null;
        c.linkManual = true;
        refreshRow();
      },
    });
    const nameInput = h('input', { value: c.name, oninput: e => { c.name = e.target.value; c.autoName = null; },
      onchange: () => { if (!c.linkManual) rematch(); refreshRow(); } });
    const linked = items.find(i => i.id === c.item_id);
    const refreshRow = () => { const fresh = rowEl(c); el.replaceWith(fresh); refreshCatHead(c.category); };
    const rematch = () => {
      const m = matchItem(items, c);
      c.item_id = m ? m.id : null;
    };
    const specs = h('div', { class: 'specs' }, specInputs(c.category, c.specs, (k, v, committed) => {
      if (v) c.specs[k] = v; else delete c.specs[k];
      if (committed) {
        if (c.autoName != null && cat && cat.name_template) { c.name = renderName(cat, c.specs, c.name); c.autoName = c.name; }
        if (!c.linkManual) rematch();
        refreshRow();
      }
    }));
    const el = h('div', { class: 'review-row' + (c.include ? '' : ' excluded') },
      h('input', { type: 'checkbox', checked: c.include, title: 'Include this component', onchange: e => { c.include = e.target.checked; refreshRow(); } }),
      h('div', null, field('Name', nameInput), c.notes ? h('div', { class: 'hint' }, c.notes) : null),
      specs,
      field('Qty', h('input', { type: 'number', min: 1, value: c.qty, oninput: e => { c.qty = Number(e.target.value) || 1; }, onchange: () => refreshCatHead(c.category) })),
      field('Slot', h('input', { value: c.slot, oninput: e => { c.slot = e.target.value; } })),
      field('Serial', h('input', { value: c.serial, oninput: e => { c.serial = e.target.value; } })),
      h('div', null, field('Inventory item', linkSel),
        h('div', { class: 'hint' + (linked ? ' linked' : '') },
          linked ? (c.linkManual ? '✓ Linked by you' : '✓ Matches an existing item') : 'Will be added to inventory'),
        linked && linked.spare_qty > 0 ? h('label', { class: 'check hint' },
          h('input', { type: 'checkbox', checked: c.take_from_spare, onchange: e => { c.take_from_spare = e.target.checked; } }), 'Take from spares') : null),
    );
    return el;
  };

  const catHeads = {};
  const refreshCatHead = key => {
    const head = catHeads[key];
    if (head) head.replaceWith(catHead(key));
  };
  const catHead = key => {
    const curList = rv.current ? rv.current.components.filter(c => c.category === key) : [];
    const curText = curList.length ? curList.map(c => (c.qty > 1 ? `${c.qty}× ` : '') + c.name).join(', ') : 'nothing';
    const el = h('div', { class: 'review-cat-head' },
      h('div', { class: 'left' },
        rv.current ? h('input', { type: 'checkbox', checked: rv.replace[key], 'aria-label': `Update ${catLabel(key)}`, onchange: e => {
          rv.replace[key] = e.target.checked;
          e.target.closest('.review-cat').classList.toggle('off', !e.target.checked);
        } }) : null,
        catIcon(key, 'sm'),
        h('h2', null, rv.current ? `Update ${catLabel(key)}` : catLabel(key))),
      rv.current ? h('span', { class: 'muted small' }, `Currently: ${curText}`,
        !curList.length ? '' : catDiffers(key) ? h('strong', null, ' · differs from scan, tick to replace') : ' · same parts, refreshes serials/slots') : null);
    catHeads[key] = el;
    return el;
  };

  const portsSection = () => {
    if (!scan.ports) return null;
    const eth = scan.ports.filter(p => p.kind === 'ethernet');
    const lldp = scan.ports.filter(p => Object.keys(p.neighbor || {}).length);
    const macs = scan.ports.reduce((n, p) => n + p.learned.length, 0);
    return h('section', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', null, 'Ports'),
        h('span', { class: 'hint' }, `${eth.filter(p => p.oper === 'up').length} of ${eth.length} up · ${macs} MAC${macs === 1 ? '' : 's'} learned · ${lldp.length} LLDP neighbour${lldp.length === 1 ? '' : 's'}`)),
      eth.length >= 5 ? faceplate(eth.map(p => ({ ...p, devices: [], unknown: p.learned.map(m => ({ mac: m })) }))) : null,
      lldp.length ? h('div', { class: 'card-body' }, h('h3', { style: 'margin-bottom:8px' }, 'LLDP neighbours'),
        h('div', { class: 'devs' }, lldp.map(p => h('span', { class: 'dev plain' }, h('span', { class: 'mono' }, p.name), ' → ', p.neighbor.name || p.neighbor.port_desc || p.neighbor.chassis || p.neighbor.port)))) : null,
      h('div', { class: 'card-foot' }, h('span', null, 'Saving replaces the stored port list. Devices are matched to your assets by MAC address and LLDP name.')));
  };

  const compsSection = () => !rv.comps.length ? null : h('section', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', null, 'Components'),
      h('span', { class: 'hint' }, 'Edit anything that looks wrong. Unticked rows are skipped.')),
    scanCats().map(key => h('div', { class: 'review-cat' + (rv.current && !rv.replace[key] ? ' off' : '') },
      catHead(key),
      h('div', { class: 'review-rows' }, rv.comps.filter(c => c.category === key).map(rowEl)))),
    rv.current ? h('div', { class: 'card-foot' },
      h('label', { class: 'check', style: 'color:var(--text)' }, h('input', { type: 'checkbox', checked: rv.toSpare, onchange: e => { rv.toSpare = e.target.checked; } }),
        'Put replaced parts back into spare stock'),
      h('span', null, 'Only for ticked categories. Leave off if the old entries were the same parts recorded differently.')) : null);

  const save = async btn => {
    btn.disabled = true;
    try {
      let asset;
      if (rv.current) {
        asset = {};
        for (const [k] of REVIEW_FIELDS) if (rv.use[k]) asset[k] = rv.fields[k] || '';
      } else {
        asset = assetBody(rv.fields);
      }
      if (scan.scan_user) asset.scan_user = scan.scan_user;
      const replace = rv.current ? scanCats().filter(k => rv.replace[k]) : scanCats();
      const res = await api('POST', `/api/scans/${scanId}/apply`, {
        asset_id: rv.current ? rv.current.id : null, asset, replace, removed_to_spare: rv.toSpare,
        components: rv.comps.map(c => ({ ...c, qty: Number(c.qty) || 1 })),
      });
      toast('Saved');
      await loadMeta();
      location.hash = `#/assets/${res.asset_id}`;
    } catch (e) { toast(e.message, true); btn.disabled = false; }
  };

  const discard = async () => { await api('DELETE', `/api/scans/${scanId}`); await loadMeta(); location.hash = '#/scans'; };

  const draw = () => {
    const target = selectEl([['', 'Create a new asset'], ...assets.filter(a => !isKeyboard(a)).map(a => [String(a.id), `Update ${a.name}`])], rv.assetId ? String(rv.assetId) : '', {
      style: 'width:auto',
      onchange: async e => {
        rv.assetId = e.target.value ? Number(e.target.value) : null;
        await loadCurrent();
        draw();
      },
    });
    const saveBtn = h('button', { class: 'primary' }, icon('check'), rv.current ? `Save to ${rv.current.name}` : 'Create asset');
    saveBtn.addEventListener('click', () => save(saveBtn));
    setKids(root,
      h('div', { class: 'record-head' },
        net ? typeIcon(scanned.asset_type, 'lg') : h('span', { class: 'ico lg c-blue' }, icon('scan')),
        h('div', { class: 'body' },
          h('div', { class: 'title-row' }, h('h1', null, scanned.name || 'Unknown machine'), h('span', { class: 'pill plain' }, 'Scan review')),
          h('div', { class: 'meta' },
            makerModel(scanned) ? h('span', null, icon('monitor'), makerModel(scanned)) : null,
            h('span', null, icon('terminal'), scan.source),
            h('span', { title: fmtDate(scan.created_at) }, ago(scan.created_at)),
            h('a', { href: `/api/scans/${scanId}/raw`, target: '_blank' }, 'Raw output')))),
      scan.suggested_asset_id && rv.assetId === scan.suggested_asset_id && !scan.asset_id
        ? h('div', { class: 'alert info' }, icon('info'), h('div', null, 'This looks like an asset you already have (same serial or hostname), so it is set to update it.')) : null,
      scan.warnings.length ? h('div', { class: 'alert warn' }, icon('alert'), h('div', null, h('strong', null, 'Check these'), h('ul', null, scan.warnings.map(w => h('li', null, w))))) : null,
      fieldsSection(),
      portsSection(),
      compsSection(),
      h('div', { class: 'sticky-foot' },
        h('div', { class: 'actions' }, h('span', { class: 'muted small' }, 'Save as'), target),
        h('div', { class: 'actions' }, h('button', { class: 'ghost', onclick: discard }, 'Discard scan'), saveBtn)),
    );
  };

  await loadCurrent();
  draw();
  return root;
}

// ------------------------------------------------------------------ inventory

const INV_FILTERS = { q: '', stock: '', location: '' };

async function inventoryView(catParam) {
  // Keyboard parts (switches, keycaps) are their own group, listed under Keyboards instead of Inventory
  const group = (CATS[catParam] && CATS[catParam].group) || '';
  const kb = group === 'keyboard';
  const items = (await api('GET', '/api/items')).filter(i => ((CATS[i.category] || {}).group || '') === group);
  const state = { cat: CATS[catParam] ? catParam : '' };
  const groupCats = META.categories.filter(c => (c.group || '') === group);
  const counts = {};
  for (const it of items) counts[it.category] = (counts[it.category] || 0) + 1;
  const tabs = h('div', { class: 'tabs' });
  const head = h('div', { class: 'page-head' });
  const tableBox = h('div', { class: 'table-wrap' });
  const count = h('span', { class: 'count' });
  const totals = h('div', { class: 'card-foot' });

  const setCat = key => {
    state.cat = key;
    history.replaceState(null, '', key ? `#/inventory?cat=${key}` : '#/inventory');
    NAV.cat = key;
    renderNav();
    drawHead(); drawTabs(); draw();
  };
  const drawHead = () => {
    const label = state.cat ? catLabel(state.cat) : 'Inventory';
    setCrumbs(...(state.cat ? [kb ? ['Keyboards', '#/keyboards'] : ['Inventory', '#/inventory'], [label]] : [['Inventory']]));
    renderCrumbs();
    const sub = kb ? (state.cat === 'switch' ? 'Every switch you own, in a keyboard or in the bag' : `Every ${label.toLowerCase()} set, on a keyboard or on the shelf`)
      : state.cat ? `${counts[state.cat] || 0} ${label} parts, installed or on the shelf` : 'Every part, installed or on the shelf';
    setKids(head,
      h('div', { class: 'ent' }, state.cat ? catIcon(state.cat, 'lg') : null,
        h('div', null, h('h1', null, label), h('div', { class: 'sub' }, sub))),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => editItemModal({ category: state.cat || groupCats[0].key, specs: {}, spare_qty: 1 }, id => { location.hash = `#/inventory/${id}`; }) }, icon('plus'),
        kb ? `New ${singular(label).toLowerCase()}${state.cat === 'keycaps' ? ' set' : ''}` : 'New item')));
  };
  const drawTabs = () => {
    setKids(tabs,
      kb ? null : h('button', { class: state.cat ? '' : 'active', onclick: () => setCat('') }, 'All ', h('span', { class: 'n' }, items.length)),
      ...groupCats.filter(c => counts[c.key] || c.key === state.cat).map(c => h('button', {
        class: state.cat === c.key ? 'active' : '', onclick: () => setCat(c.key),
      }, c.label, ' ', h('span', { class: 'n' }, counts[c.key] || 0))));
  };
  const qtyCell = (n, cls) => h('td', { class: 'num' }, h('span', { class: 'qty' + (n ? '' : ' zero') + (cls ? ' ' + cls : '') }, n));
  const spareCell = n => h('td', { class: 'num' }, n ? h('span', { class: 'pill p-green' }, kb ? `${n}` : `${n} spare`) : h('span', { class: 'qty zero' }, '0'));
  const where = n => kb ? `${n} keyboard${n > 1 ? 's' : ''}` : `${n} machine${n > 1 ? 's' : ''}`;
  const partCell = it => h('td', null, h('a', { class: 'ent', href: `#/inventory/${it.id}`, onclick: e => e.stopPropagation() }, catIcon(it.category),
    h('div', null, h('div', { class: 't' }, it.name), h('div', { class: 's' }, kb && norm(it.name).includes(norm(it.manufacturer)) && norm(it.name).includes(norm(it.model_number)) ? ''
      : [it.manufacturer, it.model_number].filter(Boolean).join(' · ') || catLabel(it.category)))));
  const usedCell = it => h('td', { class: 'num', title: it.asset_count ? `in ${it.asset_count} asset${it.asset_count > 1 ? 's' : ''}` : '' },
    h('span', { class: 'qty' + (it.installed_qty ? '' : ' zero') }, it.installed_qty),
    it.asset_count ? h('div', { class: 'faint small' }, where(it.asset_count)) : null);
  const locCell = it => h('td', { class: 'small nowrap hide-sm' }, it.spare_qty && it.location ? it.location : h('span', { class: 'faint' }, '—'));

  const headRow = h('tr');
  const baseCols = {
    part: { key: 'part', label: 'Part', sort: it => it.name },
    used: { key: 'used', label: kb ? 'In keyboards' : 'In use', cls: 'num', sort: it => it.installed_qty },
    spare: { key: 'spare', label: kb ? 'On hand' : 'Spare', cls: 'num', sort: it => it.spare_qty },
    total: { key: 'total', label: 'Total', cls: 'num', sort: it => it.installed_qty + it.spare_qty },
    loc: { key: 'loc', label: 'Stored at', cls: 'hide-sm', sort: it => (it.spare_qty ? it.location : '') },
  };
  const draw = () => {
    const q = INV_FILTERS.q.toLowerCase();
    const rows = items.filter(it =>
      (!state.cat || it.category === state.cat) &&
      (INV_FILTERS.stock !== 'spare' || it.spare_qty > 0) &&
      (INV_FILTERS.stock !== 'none' || it.spare_qty === 0) &&
      (INV_FILTERS.stock !== 'unused' || it.installed_qty === 0) &&
      (!INV_FILTERS.location || it.location === INV_FILTERS.location) &&
      (!q || JSON.stringify([it.name, it.manufacturer, it.model_number, it.specs, it.location, it.notes]).toLowerCase().includes(q)));
    count.textContent = `${rows.length} item${rows.length === 1 ? '' : 's'}`;
    const spare = rows.reduce((s, r) => s + r.spare_qty, 0), used = rows.reduce((s, r) => s + r.installed_qty, 0);
    if (kb) setKids(totals, h('span', null, `${used + spare} ${state.cat === 'switch' ? 'switches' : 'sets'} in total`), h('span', null, `${used} in keyboards · ${spare} on hand`));
    else setKids(totals, h('span', null, `${used + spare} parts in total`), h('span', null, `${used} installed · ${spare} spare`));
    if (!rows.length) {
      setKids(tableBox, items.length ? emptyState('search', 'No matches', 'Try a different search or filter.')
        : emptyState('boxes', 'No inventory yet', 'Parts are added when you scan machines, or add them by hand.'));
      return;
    }
    const tr = it => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/inventory/${it.id}`; } });
    if (state.cat) {
      const cat = CATS[state.cat] || { fields: [] };
      baseCols.part.label = kb ? singular(cat.label) : 'Part';
      const cols = [baseCols.part, ...cat.fields.map(f => ({ key: 'f:' + f.key, label: splitLabel(f.label)[0], sort: it => it.specs[f.key] })),
        baseCols.used, baseCols.spare, baseCols.total, baseCols.loc];
      const id = 'inv:' + state.cat;
      setKids(headRow, sortHeads(id, cols, draw));
      const specCell = (f, v) => {
        if (!v) return h('span', { class: 'faint' }, '—');
        return state.cat === 'switch' && f.key === 'type' ? switchTypePill(v) : specValue(f, v);
      };
      setKids(tableBox, h('table', null,
        h('thead', null, headRow),
        h('tbody', null, sortRows(id, rows, cols).map(it => {
          const row = tr(it);
          setKids(row, partCell(it),
            cat.fields.map(f => h('td', { class: 'small nowrap' }, specCell(f, it.specs[f.key]))),
            usedCell(it), spareCell(it.spare_qty), qtyCell(it.installed_qty + it.spare_qty), locCell(it));
          return row;
        }))));
      return;
    }
    const cats = [...new Set(rows.map(r => r.category))].sort((x, y) => catOrder(x) - catOrder(y));
    baseCols.part.label = 'Part';
    const cols = [baseCols.part, { key: 'specs', label: 'Specs', cls: 'hide-sm' }, baseCols.used, baseCols.spare, baseCols.total, baseCols.loc];
    setKids(headRow, sortHeads('inv', cols, draw));
    setKids(tableBox, h('table', null,
      h('thead', null, headRow),
      h('tbody', null, cats.map(key => {
        const list = sortRows('inv', rows.filter(r => r.category === key), cols);
        return [
          h('tr', { class: 'group' }, h('td', { colspan: 6 }, h('div', { class: 'ent' },
            h('h3', null, h('a', { href: `#/inventory?cat=${key}`, style: 'color:inherit', onclick: e => { e.preventDefault(); setCat(key); } }, catLabel(key))),
            h('span', { class: 'faint small' }, `${list.length} item${list.length === 1 ? '' : 's'}`)))),
          list.map(it => {
            const row = tr(it);
            setKids(row, partCell(it), h('td', { class: 'hide-sm' }, specChips(it.category, it.specs, { compact: true })),
              usedCell(it), spareCell(it.spare_qty), qtyCell(it.installed_qty + it.spare_qty), locCell(it));
            return row;
          }),
        ];
      }))));
  };
  const locations = [...new Set(items.map(i => i.location).filter(Boolean))].sort();
  drawHead();
  drawTabs();
  draw();
  return [
    head,
    h('section', { class: 'card' },
      tabs,
      h('div', { class: 'filters' },
        searchInput('Search parts, specs, part numbers…', INV_FILTERS.q, e => { INV_FILTERS.q = e.target.value; draw(); }),
        selectEl([['', 'Any stock'], ['spare', 'Has spares'], ['none', 'No spares'], ['unused', 'Not installed anywhere']], INV_FILTERS.stock, {
          onchange: e => { INV_FILTERS.stock = e.target.value; draw(); } }),
        selectEl([['', 'Stored anywhere'], ...locations], INV_FILTERS.location, { onchange: e => { INV_FILTERS.location = e.target.value; draw(); } }),
        h('span', { class: 'grow' }), count),
      tableBox,
      totals),
  ];
}

async function itemView(id) {
  const it = await api('GET', `/api/items/${id}`);
  const kb = isKbCat(it.category);
  setCrumbs(kb ? ['Keyboards', '#/keyboards'] : ['Inventory', '#/inventory'], [catLabel(it.category), `#/inventory?cat=${it.category}`], [it.name]);
  NAV.cat = it.category;
  renderNav();
  const machines = n => kb ? `${n} keyboard${n > 1 ? 's' : ''}` : `${n} machine${n > 1 ? 's' : ''}`;
  const reload = () => route();
  const setSpare = async n => {
    await api('PUT', `/api/items/${id}`, { spare_qty: Math.max(0, n) });
    reload();
  };
  const installModal = async () => {
    const assets = (await api('GET', '/api/assets')).filter(a => isKeyboard(a) === kb);
    if (!assets.length) return toast(kb ? 'Add a keyboard first' : 'Create an asset first', true);
    const state = { asset_id: String(assets[0].id), qty: 1, slot: '', serial: '', take: it.spare_qty > 0 };
    const sw = it.category === 'switch';
    openModal(kb ? `Put ${it.name} on a keyboard` : `Install ${it.name}`, h('div', null,
      section('Into', field(kb ? 'Keyboard' : 'Asset', selectEl(assets.map(a => [String(a.id), a.name]), state.asset_id, { onchange: e => { state.asset_id = e.target.value; } }))),
      section(kb ? null : 'Install', h('div', { class: 'grid' },
        !kb || sw ? field(sw ? 'How many' : 'Quantity', h('input', { type: 'number', min: 1, value: 1, oninput: e => { state.qty = e.target.value; } })) : null,
        !kb || sw ? field(sw ? 'Where (optional)' : 'Slot', h('input', { placeholder: sw ? 'e.g. alphas, mods' : '', oninput: e => { state.slot = e.target.value; } })) : null,
        kb ? null : field('Serial', h('input', { oninput: e => { state.serial = e.target.value; } })),
        h('label', { class: 'check wide' }, h('input', { type: 'checkbox', checked: state.take, onchange: e => { state.take = e.target.checked; } }),
          kb ? `Take from stock (${it.spare_qty} on hand)` : `Take from spare stock (${it.spare_qty} available)`)))), [
      { label: 'Cancel' },
      { label: kb ? 'Add to keyboard' : 'Install', class: 'primary', action: async () => {
        await api('POST', `/api/assets/${state.asset_id}/components`, { item_id: it.id, qty: Number(state.qty) || 1, slot: state.slot, serial: state.serial, take_from_spare: state.take });
        toast('Installed');
        reload();
      } },
    ]);
  };
  const mergeModal = async () => {
    const others = (await api('GET', '/api/items')).filter(o => o.category === it.category && o.id !== it.id);
    if (!others.length) return toast(`No other ${catLabel(it.category)} items to merge into`, true);
    const state = { target: String(others[0].id) };
    openModal(`Merge ${it.name}`, h('div', null,
      h('p', { style: 'margin-top:0' }, 'Moves every install and the spare count of this item into the one you pick, then deletes this one. Handy when a scan created a duplicate.'),
      field('Merge into', selectEl(others.map(o => [String(o.id), itemOptionLabel(o)]), state.target, { onchange: e => { state.target = e.target.value; } }))), [
      { label: 'Cancel' },
      { label: 'Merge', class: 'primary', action: async () => {
        const res = await api('POST', `/api/items/${it.id}/merge`, { target_id: Number(state.target) });
        toast('Merged');
        location.hash = `#/inventory/${res.id}`;
      } },
    ], { dialog: true });
  };
  const del = async () => {
    if (!(await confirmModal('Delete item', `Delete ${it.name} from inventory?`, 'Delete'))) return;
    await api('DELETE', `/api/items/${id}`);
    toast('Deleted');
    location.hash = `#/inventory?cat=${it.category}`;
  };

  const total = it.installed_qty + it.spare_qty;
  it.asset_count = new Set(it.installs.map(i => i.asset_id)).size;
  const cat = CATS[it.category] || { fields: [] };
  const specRows = cat.fields.map(f => [splitLabel(f.label)[0], it.specs[f.key] ? specValue(f, it.specs[f.key]) : null]);
  for (const [k, v] of Object.entries(it.specs || {})) if (!cat.fields.some(f => f.key === k)) specRows.push([k, v]);

  return [
    h('div', { class: 'record-head' },
      catIcon(it.category, 'lg'),
      h('div', { class: 'body' },
        h('div', { class: 'title-row' }, h('h1', null, it.name), h('span', { class: 'pill plain' }, catLabel(it.category)),
          it.category === 'switch' ? switchTypePill(it.specs.type) : null,
          it.spare_qty ? h('span', { class: 'pill p-green' }, `${it.spare_qty} ${kb ? 'on hand' : 'spare'}`) : null),
        h('div', { class: 'meta' },
          it.manufacturer ? h('span', null, icon('tag'), it.manufacturer) : null,
          it.model_number ? h('span', null, h('code', null, it.model_number)) : null,
          it.spare_qty && it.location ? h('span', null, icon('pin'), `Spares at ${it.location}`) : null,
          h('span', null, icon(kb ? 'keyboard' : 'monitor'), it.asset_count ? `In ${machines(it.asset_count)}` : kb ? 'Not on a keyboard' : 'Not installed'))),
      h('div', { class: 'actions' },
        h('button', { onclick: () => editItemModal(it, reload) }, icon('edit'), 'Edit'),
        h('button', { onclick: mergeModal }, icon('merge'), 'Merge'),
        h('button', { class: 'primary', onclick: installModal }, icon(kb ? 'keyboard' : 'install'), kb ? 'Add to keyboard' : 'Install into asset'),
        iconButton('trash', 'Delete item', del, 'danger'))),
    h('div', { class: 'record-grid' },
      h('div', null,
        h('section', { class: 'card' },
          h('div', { class: 'card-head' }, h('h2', null, kb ? 'On keyboards' : 'Installed in', h('span', { class: 'n' }, it.installs.length || ''))),
          it.installs.length ? h('div', { class: 'table-wrap' }, h('table', null,
            h('thead', null, h('tr', null, h('th', null, kb ? 'Keyboard' : 'Asset'), h('th', null, 'Status'), h('th', { class: 'num' }, 'Qty'), h('th', null, kb ? 'Where' : 'Slot'), kb ? null : h('th', null, 'Serial'), h('th', null, ''))),
            h('tbody', null, it.installs.map(i => h('tr', { class: 'clickable', onclick: () => { location.hash = assetHref({ id: i.asset_id, kind: i.asset_kind }); } },
              h('td', null, h('a', { class: 'ent', href: assetHref({ id: i.asset_id, kind: i.asset_kind }), onclick: e => e.stopPropagation() }, assetIcon({ asset_type: i.asset_type, kind: i.asset_kind }, 'sm'),
                h('div', null, h('div', { class: 't' }, i.asset_name), h('div', { class: 's' }, [i.asset_location, i.notes].filter(Boolean).join(' · '))))),
              h('td', null, statusPill(i.asset_status)),
              h('td', { class: 'num' }, h('span', { class: 'qty' }, i.qty)),
              h('td', { class: 'small nowrap' }, i.slot || h('span', { class: 'faint' }, '—')),
              kb ? null : h('td', { class: 'small' }, i.serial ? h('code', null, i.serial) : h('span', { class: 'faint' }, '—')),
              rowActions(iconButton('trash', kb ? 'Take off this keyboard' : 'Remove from this asset', () => removeInstall(i.install_id, `${it.name} from ${i.asset_name}`, reload), 'danger')),
            ))))) : emptyState(kb ? 'keyboard' : 'monitor', kb ? 'Not on a keyboard' : 'Not installed anywhere', it.spare_qty ? `All ${it.spare_qty} are on the shelf.` : null,
            h('button', { onclick: installModal }, icon(kb ? 'keyboard' : 'install'), kb ? 'Add to keyboard' : 'Install into asset'))),
        it.notes ? h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', null, 'Notes')), h('div', { class: 'card-body' }, h('p', { class: 'notes' }, it.notes))) : null),
      h('div', null,
        h('section', { class: 'card' },
          h('div', { class: 'card-head' }, h('h2', null, 'Stock')),
          h('div', { class: 'stock' },
            h('div', null, h('div', { class: 'k' }, kb ? 'In keyboards' : 'In use'), h('div', { class: 'v' }, it.installed_qty)),
            h('div', null, h('div', { class: 'k' }, kb ? 'On hand' : 'Spare'), h('div', { class: 'v' }, it.spare_qty)),
            h('div', null, h('div', { class: 'k' }, 'Total'), h('div', { class: 'v' }, total))),
          total ? h('div', { class: 'stock-bar', title: `${it.installed_qty} in use, ${it.spare_qty} spare` },
            h('span', { style: `width:${(it.installed_qty / total) * 100}%` }), h('span', { style: `width:${(it.spare_qty / total) * 100}%` })) : null,
          h('div', { class: 'stock-adjust' },
            h('span', { class: 'muted small' }, kb ? 'On hand, not in a keyboard' : 'Spares on the shelf'),
            h('div', { class: 'stepper' },
              h('button', { onclick: () => setSpare(it.spare_qty - 1), 'aria-label': 'One fewer spare', disabled: !it.spare_qty || null }, '−'),
              h('span', null, it.spare_qty),
              h('button', { onclick: () => setSpare(it.spare_qty + 1), 'aria-label': 'One more spare' }, '+')))),
        h('section', { class: 'card' },
          h('div', { class: 'card-head' }, h('h2', null, 'Specifications'), h('button', { class: 'link small', onclick: () => editItemModal(it, reload) }, 'Edit')),
          specRows.length ? propsList(specRows) : h('div', { class: 'card-body muted' }, 'No fields for this category.')),
        h('section', { class: 'card' },
          h('div', { class: 'card-head' }, h('h2', null, 'Details')),
          propsList(kb ? [['Category', catLabel(it.category)], ['Brand', it.manufacturer], ['Model', it.model_number], ['Stored at', it.location]]
            : [['Category', catLabel(it.category)], ['Manufacturer', it.manufacturer], ['Part number', it.model_number && h('code', null, it.model_number)], ['Spares stored at', it.location]])),
        activityCard(it.events))),
  ];
}

// ------------------------------------------------------------------ network map

/** Hints for MACs the map can't match to an asset: VMs, containers and phones' private addresses. */
const MAC_HINTS = [['bc:24:11', 'Proxmox VM/LXC'], ['00:15:5d', 'Hyper-V VM'], ['02:42:', 'Docker'], ['52:54:00', 'KVM VM'],
  ['00:0c:29', 'VMware VM'], ['00:50:56', 'VMware VM'], ['08:00:27', 'VirtualBox VM']];
function macHint(mac) {
  if (!mac) return '';
  const hit = MAC_HINTS.find(([p]) => mac.startsWith(p));
  if (hit) return hit[1];
  return /^.[26ae]/.test(mac) ? 'Private (randomized) MAC' : '';
}
function hintSummary(devices) {
  const counts = {};
  for (const d of devices) { const k = macHint(d.mac); if (k) counts[k] = (counts[k] || 0) + 1; }
  return Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${n} ${k.replace(/^Private \(randomized\) MAC$/, 'private MAC')}`).join(' · ');
}
function nameSummary(devices) {
  const names = devices.filter(d => d.name && d.mac).map(d => d.name);
  return names.length ? names.slice(0, 3).join(', ') + (names.length > 3 ? ` +${names.length - 3}` : '') : '';
}
// Behind an unmanaged switch, the speed is the managed switch's port, not this link's
const guestLabel = g => `${g.guest_type === 'lxc' ? 'LXC' : 'VM'} ${g.vmid}`;
const linkText = l => [l && l.port, l && l.speed && l.source !== 'inferred' ? fmtSpeed(l.speed) : ''].filter(Boolean).join(' · ');
const MAP = { view: null, showUnknown: true };

/** Which device this is plugged into, set by hand. Used from the map and the asset page. */
async function connectionModal(node, onDone) {
  const assets = (await api('GET', '/api/assets')).filter(a => !isKeyboard(a) && a.id !== node.id);
  const current = await api('GET', `/api/assets/${node.id}`);
  const state = { uplink_id: current.uplink_id ? String(current.uplink_id) : '', uplink_port: current.uplink_port || '' };
  const net = assets.filter(isNetwork), rest = assets.filter(a => !isNetwork(a));
  const sel = h('select', { onchange: e => { state.uplink_id = e.target.value; } },
    h('option', { value: '' }, 'Work it out from switch scans'),
    h('optgroup', { label: 'Network devices' }, net.map(a => h('option', { value: String(a.id) }, a.name))),
    h('optgroup', { label: 'Other devices (e.g. a Proxmox host for a VM, or a dock)' }, rest.map(a => h('option', { value: String(a.id) }, a.name))));
  sel.value = state.uplink_id;
  openModal(`Where ${node.name} plugs in`, h('div', null,
    section(null, h('div', { class: 'grid' },
      field('Connected to', sel),
      field('Port (optional)', h('input', { value: state.uplink_port, placeholder: 'e.g. gi18, port 3', oninput: e => { state.uplink_port = e.target.value; } })),
      h('p', { class: 'muted small wide', style: 'margin:0' },
        'Use this for anything scans can’t see, like unmanaged switches. If you give a managed switch’s port, ',
        'everything else the switch sees on that port is placed behind this device. Connecting to an access point draws a wireless link.')))), [
    { label: 'Cancel' },
    { label: 'Save', class: 'primary', action: async () => {
      await api('PUT', `/api/assets/${node.id}`, { uplink_id: state.uplink_id ? Number(state.uplink_id) : null, uplink_port: state.uplink_port });
      toast('Saved');
      onDone();
    } },
  ]);
}

async function mapView() {
  setCrumbs(['Network map']);
  const data = await api('GET', '/api/map');
  const reload = () => route();
  const W = 210, COLW = 300, GAP = 12, H_ASSET = 54, H_UNK = 44, PAD = 24;

  const byId = new Map(data.nodes.map(n => [n.id, n]));
  const kids = new Map();
  for (const n of data.nodes) if (n.parent != null) {
    if (!kids.has(n.parent)) kids.set(n.parent, []);
    kids.get(n.parent).push(n);
  }
  const order = n => n.type === 'unknown' ? 3 : n.type === 'guest' ? 2 : n.managed || isNetwork(n) ? 0 : 1;
  for (const list of kids.values()) list.sort((a, b) => order(a) - order(b) || String(a.name || a.seen).localeCompare(String(b.name || b.seen), undefined, { numeric: true }));

  const canvas = h('div', { class: 'map-canvas' });
  const inner = h('div', { class: 'map-inner' });
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('class', 'map-edges');
  inner.append(svg);
  canvas.append(inner);
  const panel = h('div', { class: 'map-panel', hidden: true });
  const pos = new Map();
  let selected = null;

  const visible = n => MAP.showUnknown || n.type !== 'unknown';
  const draw = () => {
    pos.clear();
    let cursor = PAD;
    const place = (n, depth) => {
      const ch = (kids.get(n.id) || []).filter(visible);
      const hgt = n.type === 'unknown' || n.type === 'guest' ? H_UNK : H_ASSET;
      if (!ch.length) { pos.set(n.id, { x: PAD + depth * COLW, y: cursor, h: hgt }); cursor += hgt + GAP; return; }
      ch.forEach(c => place(c, depth + 1));
      const first = pos.get(ch[0].id), last = pos.get(ch[ch.length - 1].id);
      const mid = (first.y + first.h / 2 + last.y + last.h / 2) / 2;
      pos.set(n.id, { x: PAD + depth * COLW, y: Math.max(mid - hgt / 2, 0), h: hgt });
    };
    data.nodes.filter(n => n.parent == null && visible(n)).forEach(r => { place(r, 0); cursor += GAP * 2; });

    let maxX = 0, maxY = 0;
    for (const p of pos.values()) { maxX = Math.max(maxX, p.x + W); maxY = Math.max(maxY, p.y + p.h); }
    const width = maxX + PAD, height = maxY + PAD;
    inner.style.width = width + 'px';
    inner.style.height = height + 'px';
    svg.setAttribute('width', width);
    svg.setAttribute('height', height);

    const path = new Set();
    for (let n = selected && byId.get(selected); n; n = byId.get(n.parent)) path.add(n.id);
    svg.replaceChildren();
    inner.querySelectorAll('.map-node').forEach(el => el.remove());
    for (const n of data.nodes) {
      const p = pos.get(n.id);
      if (!p) continue;
      const parent = n.parent != null && pos.get(n.parent);
      if (parent) {
        const sx = parent.x + W, sy = parent.y + parent.h / 2, ex = p.x, ey = p.y + p.h / 2, tx = sx + 22;
        const e = document.createElementNS(svgNS, 'path');
        e.setAttribute('d', `M${sx},${sy} H${tx} V${ey} H${ex}`);
        const l = n.link || {};
        e.setAttribute('class', ['edge', l.wireless ? 'wireless' : '', l.source === 'inferred' || l.source === 'proxmox' ? 'inferred' : '', l.offline ? 'offline' : '',
          path.has(n.id) ? 'hot' : ''].filter(Boolean).join(' '));
        svg.append(e);
        const label = [linkText(l) || (l.wireless ? 'Wi-Fi' : ''), l.offline ? 'offline' : ''].filter(Boolean).join(' · ');
        if (label) {
          const t = document.createElementNS(svgNS, 'text');
          t.setAttribute('x', ex - 8);
          t.setAttribute('y', ey - 6);
          t.setAttribute('text-anchor', 'end');
          t.setAttribute('class', 'edge-label' + (path.has(n.id) ? ' hot' : ''));
          t.textContent = label;
          svg.append(t);
        }
      }
      const el = n.type === 'unknown'
        ? h('button', { class: 'map-node unknown' + (selected === n.id ? ' sel' : ''), type: 'button' },
          h('span', { class: 'ico sm c-gray' }, icon('search')),
          h('span', { class: 'txt' }, h('span', { class: 't' }, `${n.devices.length} unknown device${n.devices.length > 1 ? 's' : ''}`),
            h('span', { class: 's' }, nameSummary(n.devices) || hintSummary(n.devices) || `seen on ${n.seen}`)))
        : n.type === 'guest'
          ? h('button', { class: 'map-node guest' + (selected === n.id ? ' sel' : '') + (n.status !== 'running' ? ' offline' : ''), type: 'button' },
            h('span', { class: 'ico sm c-purple' }, icon(n.guest_type === 'lxc' ? 'box' : 'server')),
            h('span', { class: 'txt' }, h('span', { class: 't' }, n.name),
              h('span', { class: 's' }, [guestLabel(n), n.status !== 'running' ? n.status : n.ips[0]].filter(Boolean).join(' · '))))
        : n.type === 'internet'
          ? h('button', { class: 'map-node internet' + (selected === n.id ? ' sel' : ''), type: 'button' },
            h('span', { class: 'ico sm c-blue' }, icon('globe')), h('span', { class: 'txt' }, h('span', { class: 't' }, 'Internet')))
          : h('button', { class: 'map-node' + (selected === n.id ? ' sel' : '') + (path.has(n.id) ? ' on-path' : '') + (n.link && n.link.offline ? ' offline' : ''), type: 'button', ondblclick: () => { location.hash = `#/assets/${n.id}`; } },
            typeIcon(n.asset_type, 'sm'),
            h('span', { class: 'txt' }, h('span', { class: 't' }, n.name),
              h('span', { class: 's' }, [n.address && !/^\d+$/.test(n.address) ? n.address : '', n.location].filter(Boolean).join(' · ') || n.asset_type || '')));
      el.style.cssText = `left:${p.x}px;top:${p.y}px;width:${W}px;height:${p.h}px`;
      el.addEventListener('click', e => { e.stopPropagation(); if (!dragged) select(n.id); });
      inner.append(el);
    }
  };

  // ---- side panel
  const select = id => {
    selected = id;
    draw();
    const n = byId.get(id);
    if (!n) { panel.hidden = true; return; }
    panel.hidden = false;
    const close = iconButton('x', 'Close', () => { selected = null; panel.hidden = true; draw(); });
    const parent = byId.get(n.parent);
    const l = n.link || {};
    if (n.type === 'guest') {
      const fmtMem = b => b >= 2 ** 30 ? `${+(b / 2 ** 30).toFixed(1)} GB` : `${Math.round(b / 2 ** 20)} MB`;
      setKids(panel,
        h('div', { class: 'panel-head' }, h('div', { class: 'ent' }, h('span', { class: 'ico sm c-purple' }, icon(n.guest_type === 'lxc' ? 'box' : 'server')), h('h3', null, n.name)), close),
        h('div', { class: 'small', style: 'display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px' },
          h('span', { class: 'pill ' + (n.status === 'running' ? 'p-green' : 'p-gray') }, n.status), h('span', { class: 'pill plain' }, guestLabel(n)),
          n.tags.map(t => h('span', { class: 'pill plain' }, t))),
        propsList([['IP', n.ips.join(', ')], ['MAC', n.macs.join(', ')], ['Node', `${n.node} (${parent ? parent.name : '?'})`],
          ['Resources', [n.cpus ? `${n.cpus} vCPU` : '', n.mem ? fmtMem(n.mem) : ''].filter(Boolean).join(' · ')]]),
        h('p', { class: 'faint small' }, 'From Proxmox. Add it as an asset to give it a location, notes or parts; it stays under its host.'),
        h('div', { class: 'actions', style: 'margin-top:12px' },
          data.proxmox ? h('a', { class: 'btn small', href: `${data.proxmox}/#v1:0:=${n.guest_type}%2F${n.vmid}`, target: '_blank', rel: 'noopener' }, 'Open in Proxmox') : null,
          h('button', { class: 'small', onclick: () => editAssetModal({ name: n.name, macs: n.macs.join(', '), address: n.ips[0] || '', status: 'In use',
            os: n.guest_type === 'lxc' ? 'LXC container' : 'Virtual machine', uplink_id: '', uplink_port: '' }, reload) }, icon('plus'), 'Add as asset')));
      return;
    }
    const how = { proxmox: `Proxmox (${l.guest || 'guest'})`, lldp: 'the switch’s LLDP neighbour table', mac: 'the switch’s MAC table', omada: 'the Omada controller’s client list', manual: 'set by hand', inferred: '' }[l.source] || '';
    if (n.type === 'internet') { setKids(panel, h('div', { class: 'panel-head' }, h('h3', null, 'Internet'), close), h('p', { class: 'muted small' }, 'Through the firewall’s WAN port.')); return; }
    if (n.type === 'unknown') {
      setKids(panel,
        h('div', { class: 'panel-head' }, h('h3', null, `${n.devices.length} unknown device${n.devices.length > 1 ? 's' : ''}`), close),
        h('p', { class: 'muted small' }, `Seen on ${n.seen}`, n.on_host ? `, the same port as ${parent.name}, so these are most likely its VMs or containers.` : '.',
          ' Add one as a device to give it a name and place it.'),
        !n.on_host && n.devices.some(d => macHint(d.mac) === 'Proxmox VM/LXC')
          ? h('div', { class: 'alert hint small', style: 'margin:0 0 10px' }, 'This looks like a Proxmox host and its guests. If the host is already in Rigbook, scan it so its MAC address is recorded, and the guests will group under it.') : null,
        h('div', { class: 'panel-list' }, n.devices.map(d => h('div', { class: 'panel-row' },
          h('div', null, d.name && d.mac ? h('div', { class: 'strong small' }, d.name, h('span', { class: 'faint', title: d.dns || '' }, ` · from ${d.source}`)) : null,
            h('div', { class: 'mono small' }, d.mac || d.name), h('div', { class: 'faint small' },
            [d.ip, macHint(d.mac)].filter(Boolean).join(' · ') || 'no IP in the ARP table')),
          h('button', { class: 'small', onclick: () => editAssetModal({ name: d.name || '', hostname: d.dns || '', address: d.ip, macs: d.mac, status: 'In use',
            // With a MAC the scans place it, and keep it current; a hand-set connection would pin it
            uplink_id: d.mac || n.on_host ? '' : parent && parent.type === 'asset' ? parent.id : '', uplink_port: '' }, reload) }, icon('plus'), 'Add')))));
      return;
    }
    setKids(panel,
      h('div', { class: 'panel-head' }, h('div', { class: 'ent' }, typeIcon(n.asset_type, 'sm'), h('h3', null, n.name)), close),
      h('div', { class: 'small', style: 'display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px' }, statusPill(n.status), n.asset_type ? h('span', { class: 'pill plain' }, n.asset_type) : null),
      propsList([['Address', n.address], ['Hostname', n.hostname], ['Model', n.maker_model], ['Location', n.location],
        [isNetwork(n) ? 'Firmware' : 'OS', n.os]]),
      h('div', { class: 'panel-link' },
        h('div', { class: 'k' }, 'Plugged into'),
        parent ? h('div', null, h('strong', null, parent.name || 'Internet'), linkText(l) ? ` · ${linkText(l)}` : '', l.wireless ? ' · Wi-Fi' : '',
          h('div', { class: 'faint small' }, l.source === 'inferred' ? `Seen on ${l.seen}, placed behind ${parent.name} because it plugs into that port` : how ? `From ${how}` : ''),
          l.offline ? h('div', { class: 'faint small' }, `Not on Wi-Fi now. Last seen here ${ago(l.last_seen)}`) : null,
          n.matched_by ? h('div', { class: 'faint small' }, `Matched by name: ${n.matched_by.name} (from ${n.matched_by.source}) on MAC ${n.matched_by.mac}`) : null)
          : h('div', { class: 'faint' }, 'Nothing (top of the map)')),
      h('div', { class: 'actions', style: 'margin-top:12px' },
        h('a', { class: 'btn small', href: `#/assets/${n.id}` }, 'Open'),
        h('button', { class: 'small', onclick: () => connectionModal(n, reload) }, icon('link'), 'Change connection')));
  };

  // ---- pan + zoom
  const view = MAP.view || { x: 0, y: 0, k: 1 };
  const apply = () => { inner.style.transform = `translate(${view.x}px,${view.y}px) scale(${view.k})`; MAP.view = { ...view, cw: canvas.clientWidth }; };
  const fit = () => {
    const cw = canvas.clientWidth, ch = canvas.clientHeight;
    const iw = inner.offsetWidth, ih = inner.offsetHeight;
    // A phone can't show the whole width readably: fit the height and let it pan sideways from the left
    const narrow = cw < 720;
    view.k = (narrow ? Math.max(0.55, Math.min(0.85, ch / ih)) : Math.min(1, cw / iw, ch / ih)) || 1;
    view.x = narrow ? 0 : (cw - iw * view.k) / 2;
    view.y = Math.max(0, (ch - ih * view.k) / 2);
    apply();
  };
  const zoom = (f, cx, cy) => {
    const k = Math.min(2.5, Math.max(0.2, view.k * f));
    cx = cx ?? canvas.clientWidth / 2; cy = cy ?? canvas.clientHeight / 2;
    view.x = cx - (cx - view.x) * (k / view.k);
    view.y = cy - (cy - view.y) * (k / view.k);
    view.k = k;
    apply();
  };
  let drag = null, dragged = false;
  canvas.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    dragged = false;
  });
  window.addEventListener('pointermove', e => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!dragged && Math.hypot(dx, dy) < 4) return;
    if (!dragged) { dragged = true; canvas.setPointerCapture(e.pointerId); canvas.classList.add('dragging'); }
    view.x = drag.vx + dx; view.y = drag.vy + dy;
    apply();
  });
  window.addEventListener('pointerup', () => { drag = null; canvas.classList.remove('dragging'); setTimeout(() => { dragged = false; }, 0); });
  canvas.addEventListener('wheel', e => {
    e.preventDefault();
    const r = canvas.getBoundingClientRect();
    zoom(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  canvas.addEventListener('click', () => { if (!dragged && selected) { selected = null; panel.hidden = true; draw(); } });

  draw();
  // Keep the zoom and position between visits, unless the window is a different size now
  requestAnimationFrame(() => { if (MAP.view && MAP.view.cw === canvas.clientWidth) { Object.assign(view, MAP.view); apply(); } else fit(); });

  const nUnknown = data.nodes.filter(n => n.type === 'unknown').reduce((s, n) => s + n.devices.length, 0);
  const nAssets = data.nodes.filter(n => n.type === 'asset').length;
  const nGuests = data.nodes.filter(n => n.type === 'guest').length;
  const unknownToggle = h('label', { class: 'check small' }, h('input', { type: 'checkbox', checked: MAP.showUnknown, onchange: e => {
    MAP.showUnknown = e.target.checked; draw(); fit();
  } }), `Unknown devices (${nUnknown})`);
  const addSwitch = () => editAssetModal({ asset_type: 'Switch', status: 'In use', name: '' }, reload);

  return [
    h('div', { class: 'page-head' },
      h('div', null, h('h1', null, 'Network map'), h('div', { class: 'sub' }, `${nAssets} devices${nGuests ? ` and ${nGuests} VMs/containers` : ''}, from switch scans${data.omada ? ', the Omada controller' : ''}${data.proxmox ? ', Proxmox' : ''} and connections set by hand`)),
      h('div', { class: 'actions' }, h('button', { onclick: addSwitch }, icon('plus'), 'Add unmanaged switch'))),
    h('section', { class: 'card map-card' },
      h('div', { class: 'filters' },
        unknownToggle,
        h('span', { class: 'grow' }),
        h('span', { class: 'map-legend small muted' }, h('i', { class: 'lg-wired' }), 'Wired', h('i', { class: 'lg-wifi' }), 'Wi-Fi', h('i', { class: 'lg-inferred' }), 'Inferred (behind an unmanaged switch), or a VM on its host'),
        h('div', { class: 'btn-group' },
          iconButton('minus', 'Zoom out', () => zoom(1 / 1.25)), iconButton('plus', 'Zoom in', () => zoom(1.25)),
          h('button', { class: 'small', onclick: fit }, 'Fit'))),
      h('div', { class: 'map-wrap' }, canvas, panel),
      data.unplaced.length ? h('div', { class: 'map-unplaced' },
        h('span', { class: 'muted small' }, 'Not on the map:'),
        data.unplaced.map(u => h('button', { class: 'chip-btn', title: u.reason, onclick: () => connectionModal(u, reload) },
          typeIcon(u.asset_type, 'sm'), u.name, h('span', { class: 'faint' }, ` · ${u.reason}`)))) : null),
  ];
}

// ------------------------------------------------------------------ keyboards

const KB_FILTERS = { q: '', status: '', layout: '' };
const connText = a => (a.specs.connections || []).join(', ');

function editKeyboardModal(kbd, onSaved) {
  const data = { status: META.statuses[0] || '', ...kbd, specs: { ...(kbd.specs || {}) } };
  data.specs.connections = [...(data.specs.connections || [])];
  const inp = (key, props) => h('input', { value: data[key] || '', oninput: e => { data[key] = e.target.value; }, ...props });
  const body = h('div', null,
    section('Keyboard', h('div', { class: 'grid' },
      field('Brand', inp('manufacturer', { placeholder: 'e.g. Keychron' })),
      field('Model', inp('model', { placeholder: 'e.g. Q1 Pro' })),
      field('Name', inp('name', { placeholder: 'Defaults to brand and model' })),
      field('Status', selectEl([['', '—'], ...META.statuses], data.status || '', { onchange: e => { data.status = e.target.value; } })))),
    section('Layout', h('div', { class: 'grid' },
      field('Size', selectEl([['', '—'], ...META.keyboard_layouts], data.specs.layout || '', { onchange: e => { data.specs.layout = e.target.value; } })),
      field('Total keys', h('input', { type: 'number', min: 1, value: data.specs.keys || '', oninput: e => { data.specs.keys = e.target.value; } })),
      h('div', { class: 'field wide' }, h('span', null, 'Connection'),
        h('div', { class: 'checks' }, META.keyboard_connections.map(c => h('label', { class: 'check' },
          h('input', { type: 'checkbox', checked: data.specs.connections.includes(c), onchange: e => {
            const on = new Set(data.specs.connections);
            if (e.target.checked) on.add(c); else on.delete(c);
            data.specs.connections = META.keyboard_connections.filter(x => on.has(x));
          } }), c)))))),
    section('Where', h('div', { class: 'grid' }, field('Location', inp('location', { list: 'dl-locations', placeholder: 'e.g. Desk, closet' })))),
    section('Notes', h('textarea', { value: data.notes || '', 'aria-label': 'Notes', oninput: e => { data.notes = e.target.value; } })),
    datalist('dl-locations', META.locations));
  openModal(kbd.id ? `Edit ${kbd.name}` : 'New keyboard', body, [
    { label: 'Cancel' },
    {
      label: kbd.id ? 'Save changes' : 'Add keyboard', class: 'primary', action: async () => {
        const name = (data.name || '').trim() || makerModel(data);
        if (!name) throw new Error('Give it a name, or a brand and model');
        const payload = { kind: 'keyboard', name, manufacturer: data.manufacturer || '', model: data.model || '', status: data.status || '',
          location: data.location || '', notes: data.notes || '', specs: data.specs };
        const res = kbd.id ? await api('PUT', `/api/assets/${kbd.id}`, payload) : await api('POST', '/api/assets', payload);
        toast('Saved');
        onSaved(res.id);
      },
    },
  ]);
}

async function keyboardsView() {
  setCrumbs(['Keyboards']);
  const kbs = (await api('GET', '/api/assets')).filter(isKeyboard);
  const body = h('tbody');
  const headRow = h('tr');
  const count = h('span', { class: 'count' });
  const cols = [
    { key: 'name', label: 'Keyboard', sort: a => a.name },
    { key: 'layout', label: 'Layout', sort: a => layoutVal(a.specs.layout) },
    { key: 'keys', label: 'Keys', cls: 'num', sort: a => a.specs.keys },
    { key: 'conn', label: 'Connection', cls: 'hide-sm', sort: connText },
    { key: 'switches', label: 'Switches', cls: 'hide-sm', sort: a => a.summary.switch },
    { key: 'keycaps', label: 'Keycaps', cls: 'hide-sm', sort: a => a.summary.keycaps },
    { key: 'status', label: 'Status', sort: a => a.status },
  ];
  const faint = () => h('span', { class: 'faint' }, '—');
  const add = () => editKeyboardModal({}, id => { location.hash = `#/keyboards/${id}`; });
  const draw = () => {
    const q = KB_FILTERS.q.toLowerCase();
    const rows = sortRows('keyboards', kbs.filter(a =>
      (!KB_FILTERS.status || a.status === KB_FILTERS.status) &&
      (!KB_FILTERS.layout || a.specs.layout === KB_FILTERS.layout) &&
      (!q || JSON.stringify(a).toLowerCase().includes(q))), cols);
    count.textContent = `${rows.length} of ${kbs.length}`;
    setKids(headRow, sortHeads('keyboards', cols, draw));
    setKids(body, rows.map(a => {
      const keys = Number(a.specs.keys) || 0, nSw = a.counts.switch || 0;
      return h('tr', { class: 'clickable', onclick: () => { location.hash = `#/keyboards/${a.id}`; } },
        h('td', null, h('a', { class: 'ent', href: `#/keyboards/${a.id}`, onclick: e => e.stopPropagation() }, assetIcon(a),
          h('div', null, h('div', { class: 't' }, a.name), h('div', { class: 's' }, a.name !== makerModel(a) ? makerModel(a) : (a.location || '')),
            a.summary.switch ? h('div', { class: 's show-sm' }, a.summary.switch) : null))),
        h('td', { class: 'small nowrap' }, a.specs.layout || faint()),
        h('td', { class: 'num' }, keys ? h('span', { class: 'qty' }, keys) : faint()),
        h('td', { class: 'hide-sm' }, (a.specs.connections || []).length ? h('span', { class: 'chips' }, a.specs.connections.map(c => h('span', { class: 'chip' }, c))) : faint()),
        h('td', { class: 'small hide-sm', style: 'max-width:280px' }, a.summary.switch || faint(),
          keys && nSw && nSw !== keys ? h('div', { class: 'faint small' }, `${nSw} of ${keys} keys`) : null),
        h('td', { class: 'small hide-sm', style: 'max-width:220px' }, a.summary.keycaps || faint()),
        h('td', null, statusPill(a.status)));
    }));
    if (!rows.length) body.append(h('tr', null, h('td', { colspan: cols.length },
      kbs.length ? emptyState('search', 'No matches', 'Try a different search or filter.')
        : emptyState('keyboard', 'No keyboards yet', 'Add one, then put switches and keycaps on it.', h('button', { class: 'primary', onclick: add }, icon('plus'), 'Add keyboard')))));
  };
  draw();
  const layouts = [...new Set([...META.keyboard_layouts.filter(l => kbs.some(a => a.specs.layout === l)), ...kbs.map(a => a.specs.layout).filter(Boolean)])];
  const statuses = [...new Set(kbs.map(a => a.status).filter(Boolean))];
  return [
    h('div', { class: 'page-head' },
      h('div', null, h('h1', null, 'Keyboards'), h('div', { class: 'sub' }, 'Boards and the switches and keycaps on them')),
      h('div', { class: 'actions' },
        h('a', { class: 'btn', href: '#/inventory?cat=switch' }, icon('kbswitch'), 'Switches'),
        h('a', { class: 'btn', href: '#/inventory?cat=keycaps' }, icon('keycap'), 'Keycaps'),
        h('button', { class: 'primary', onclick: add }, icon('plus'), 'Add keyboard'))),
    h('section', { class: 'card' },
      h('div', { class: 'filters' },
        searchInput('Search name, switches, keycaps…', KB_FILTERS.q, e => { KB_FILTERS.q = e.target.value; draw(); }),
        selectEl([['', 'All layouts'], ...layouts], KB_FILTERS.layout, { onchange: e => { KB_FILTERS.layout = e.target.value; draw(); } }),
        selectEl([['', 'Any status'], ...statuses], KB_FILTERS.status, { onchange: e => { KB_FILTERS.status = e.target.value; draw(); } }),
        h('span', { class: 'grow' }), count),
      h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, headRow), body))),
  ];
}

async function keyboardView(id) {
  return keyboardPage(await api('GET', `/api/assets/${id}`));
}

/** One card per keyboard part category (switches, keycaps, anything added under Settings). */
function keyboardPartsCard(a, cat, reload) {
  const parts = a.components.filter(c => c.category === cat.key);
  const sw = cat.key === 'switch';
  const n = parts.reduce((s, c) => s + c.qty, 0);
  const keys = Number(a.specs.keys) || 0;
  const showQty = sw || parts.some(c => c.qty > 1);
  const addBtn = (cls) => h('button', { class: cls, onclick: () => addComponentModal(a, reload, cat.key) }, icon('plus'),
    sw ? 'Add switches' : `Add ${cat.label.toLowerCase()}`);
  let foot = null;
  if (sw && (n || keys)) {
    const diff = n - keys;
    foot = h('div', { class: 'card-foot' },
      h('span', null, `${n} switch${n === 1 ? '' : 'es'}${keys ? ` for ${keys} keys` : ''}`),
      keys && n ? (diff ? h('span', { class: 'pill p-amber' }, diff > 0 ? `${diff} more than keys` : `${-diff} keys without a switch`) : h('span', { class: 'pill p-green' }, 'Every key has a switch')) : null);
  }
  return h('section', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', null, cat.label, h('span', { class: 'n' }, (sw ? n : parts.length) || '')), parts.length ? addBtn('small') : null),
    parts.length ? h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, h('th', null, singular(cat.label)), sw ? h('th', null, 'Type') : cat.fields.map(f => h('th', { class: 'hide-sm' }, splitLabel(f.label)[0])),
        sw ? h('th', { class: 'num', title: 'Actuation / bottom-out force' }, 'Actuation / bottom-out') : null, showQty ? h('th', { class: 'num' }, 'Qty') : null, sw ? h('th', { class: 'hide-sm' }, 'Where') : null, h('th', null, ''))),
      h('tbody', null, parts.map(c => h('tr', null,
        h('td', null, h('a', { class: 'ent', href: `#/inventory/${c.id}` }, catIcon(c.category, 'sm'),
          h('div', null, h('div', { class: 't' }, c.name), c.spare_qty ? h('div', { class: 's' }, `${c.spare_qty} more on hand`) : null))),
        sw ? h('td', null, switchTypePill(c.specs.type) || h('span', { class: 'faint' }, '—'))
          : cat.fields.map(f => h('td', { class: 'small hide-sm' }, c.specs[f.key] ? specValue(f, c.specs[f.key]) : h('span', { class: 'faint' }, '—'))),
        sw ? h('td', { class: 'num small nowrap' }, c.specs.actuation || c.specs.bottom_out
          ? `${c.specs.actuation || '—'} / ${c.specs.bottom_out || '—'} gf` : h('span', { class: 'faint' }, '—')) : null,
        showQty ? h('td', { class: 'num' }, h('span', { class: 'qty' }, c.qty)) : null,
        sw ? h('td', { class: 'small hide-sm' }, c.slot || h('span', { class: 'faint' }, '—')) : null,
        rowActions(
          sw ? iconButton('edit', 'Change count or where', () => editInstallModal(c, a.name, reload)) : null,
          iconButton('trash', 'Take off', () => removeInstall(c.install_id, `${sw ? `${c.qty}× ` : ''}${c.name} from ${a.name}`, reload), 'danger')),
      ))))) : emptyState(sw ? 'kbswitch' : 'keycap', sw ? 'No switches recorded' : `No ${cat.label.toLowerCase()} recorded`,
      sw ? 'Add each kind of switch on this board with how many, like 70 linears and 17 tactiles.' : 'Add one or more sets.', addBtn('primary')),
    foot);
}

function keyboardPage(a) {
  setCrumbs(['Keyboards', '#/keyboards'], [a.name]);
  NAV.active = 'keyboards';
  renderNav();
  const reload = () => route();
  const deleteKb = async () => {
    let back = true;
    const extra = a.components.length ? h('label', { class: 'check' },
      h('input', { type: 'checkbox', checked: true, onchange: e => { back = e.target.checked; } }), 'Put its switches and keycaps back into stock') : null;
    if (!(await confirmModal('Delete keyboard', `Delete ${a.name}? This can't be undone.`, 'Delete', extra))) return;
    await api('DELETE', `/api/assets/${a.id}?return_to_spare=${back}`);
    toast('Keyboard deleted');
    location.hash = '#/keyboards';
  };
  const conns = a.specs.connections || [];
  return [
    h('div', { class: 'record-head' },
      assetIcon(a, 'lg'),
      h('div', { class: 'body' },
        h('div', { class: 'title-row' }, h('h1', null, a.name), statusPill(a.status)),
        h('div', { class: 'meta' },
          makerModel(a) && makerModel(a) !== a.name ? h('span', null, icon('tag'), makerModel(a)) : null,
          a.specs.layout ? h('span', null, icon('keyboard'), a.specs.layout) : null,
          a.specs.keys ? h('span', null, `${a.specs.keys} keys`) : null,
          conns.length ? h('span', null, conns.join(' · ')) : null,
          a.location ? h('span', null, icon('pin'), a.location) : null)),
      h('div', { class: 'actions' },
        h('button', { onclick: () => editKeyboardModal(a, reload) }, icon('edit'), 'Edit'),
        iconButton('trash', 'Delete keyboard', deleteKb, 'danger'))),
    h('div', { class: 'record-grid' },
      h('div', null, META.categories.filter(c => c.group === 'keyboard').map(c => keyboardPartsCard(a, c, reload))),
      h('div', null,
        h('section', { class: 'card' },
          h('div', { class: 'card-head' }, h('h2', null, 'Details'), h('button', { class: 'link small', onclick: () => editKeyboardModal(a, reload) }, 'Edit')),
          propsList([
            ['Brand', a.manufacturer], ['Model', a.model], ['Layout', a.specs.layout], ['Total keys', a.specs.keys],
            ['Connection', conns.length ? h('span', { class: 'chips' }, conns.map(c => h('span', { class: 'chip' }, c))) : null],
            ['Status', a.status], ['Location', a.location],
          ])),
        a.notes ? h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', null, 'Notes')), h('div', { class: 'card-body' }, h('p', { class: 'notes' }, a.notes))) : null,
        activityCard(a.events))),
  ];
}

// ------------------------------------------------------------------ settings

async function settingsView() {
  setCrumbs(['Settings']);
  const cats = JSON.parse(JSON.stringify(META.categories));
  const lists = { statuses: META.statuses.join('\n'), asset_types: META.asset_types.join('\n'), keyboard_layouts: META.keyboard_layouts.join('\n') };
  const catBox = h('div');
  const open = new Set();
  const drawCats = () => {
    setKids(catBox, ...cats.map((c, ci) => {
      const fieldLines = c.fields.map((f, fi) => h('div', { class: 'field-line' },
        h('input', { value: f.label, placeholder: 'Field label', oninput: e => { f.label = e.target.value; } }),
        selectEl([['text', 'Text'], ['number', 'Number'], ['select', 'List'], ['url', 'Link']], f.type, { onchange: e => { f.type = e.target.value; f.options = f.options || []; drawCats(); } }),
        f.type === 'select'
          ? h('input', { value: (f.options || []).join(', '), placeholder: 'Options, comma separated', oninput: e => { f.options = e.target.value.split(','); } })
          : h('span', { class: 'muted small' }, f.key ? `{${f.key}}` : ''),
        iconButton('trash', 'Remove field', () => { c.fields.splice(fi, 1); c.match = c.match.filter(m => m !== f.key); drawCats(); }, 'danger')));
      const matchOpts = [['name', 'Name'], ...c.fields.filter(f => f.key).map(f => [f.key, f.label])];
      const det = h('details', { class: 'cat-card', open: open.has(ci) || null, ontoggle: e => { if (e.target.open) open.add(ci); else open.delete(ci); } },
        h('summary', null, catIcon(c.key, 'sm'),
          h('div', null, h('div', { class: 'strong' }, c.label || 'Untitled'),
            h('div', { class: 'muted small' }, `${c.fields.length} field${c.fields.length === 1 ? '' : 's'}`, c.name_template ? ` · names like ${c.name_template}` : '')),
          icon('chevron', 'chev')),
        h('div', { class: 'inner' },
          h('div', { class: 'grid' },
            field('Category name', h('input', { value: c.label, oninput: e => { c.label = e.target.value; } })),
            field('Auto name template', h('input', { value: c.name_template || '', placeholder: 'e.g. {capacity} {type}', oninput: e => { c.name_template = e.target.value; } })),
            field('Listed under', selectEl([['', 'Inventory (computer parts)'], ['keyboard', 'Keyboards']], c.group || '', { onchange: e => { c.group = e.target.value; } })),
            h('div', { class: 'actions', style: 'align-self:end;justify-content:flex-end' },
              h('button', { class: 'small', disabled: ci === 0 || null, onclick: () => { cats.splice(ci - 1, 0, cats.splice(ci, 1)[0]); open.clear(); drawCats(); } }, '↑ Up'),
              h('button', { class: 'small', disabled: ci === cats.length - 1 || null, onclick: () => { cats.splice(ci + 1, 0, cats.splice(ci, 1)[0]); open.clear(); drawCats(); } }, '↓ Down'),
              h('button', { class: 'small danger', onclick: () => { cats.splice(ci, 1); open.clear(); drawCats(); } }, 'Delete'))),
          section('Fields', fieldLines,
            h('button', { class: 'small', style: 'margin-top:10px', onclick: () => { c.fields.push({ label: '', type: 'text' }); open.add(ci); drawCats(); } }, icon('plus'), 'Add field')),
          section('Scans match existing items on',
            h('div', { class: 'actions' }, matchOpts.map(([k, l]) => h('label', { class: 'check' },
              h('input', { type: 'checkbox', checked: c.match.includes(k), onchange: e => {
                c.match = e.target.checked ? [...c.match, k] : c.match.filter(m => m !== k);
              } }), l))))));
      return det;
    }));
  };
  drawCats();
  const save = async () => {
    try {
      await api('PUT', '/api/settings', {
        categories: cats,
        statuses: lists.statuses.split('\n'),
        asset_types: lists.asset_types.split('\n'),
        keyboard_layouts: lists.keyboard_layouts.split('\n'),
      });
      await loadMeta();
      toast('Settings saved');
      route();
    } catch (e) { toast(e.message, true); }
  };
  const [omadaCard, proxmoxCard] = await Promise.all([omadaSettings(), proxmoxSettings()]);
  return [
    h('div', { class: 'page-head' },
      h('div', null, h('h1', null, 'Settings'), h('div', { class: 'sub' }, 'Inventory categories, their fields, and the lists used on assets')),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: save }, icon('check'), 'Save settings'))),
    h('div', { class: 'record-grid' },
      h('section', { class: 'card' },
        h('div', { class: 'card-head' }, h('h2', null, 'Inventory categories'),
          h('button', { class: 'small', onclick: () => { cats.push({ label: 'New category', fields: [], match: ['name'], name_template: '' }); open.add(cats.length - 1); drawCats(); } }, icon('plus'), 'Add category')),
        h('div', { class: 'card-body' },
          h('p', { class: 'muted small', style: 'margin-top:0' },
            'Each category gets its own fields. The name template builds item names from fields, like {capacity} {type} → “16GB DDR4”; {manufacturer} and {model_number} work too. New fields show up as {key} once saved.'),
          catBox)),
      h('section', { class: 'card' },
        h('div', { class: 'card-head' }, h('h2', null, 'Lists')),
        h('div', { class: 'card-body' },
          field('Asset statuses (one per line)', h('textarea', { value: lists.statuses, style: 'min-height:150px', oninput: e => { lists.statuses = e.target.value; } })),
          h('div', { style: 'height:14px' }),
          field('Asset types (one per line)', h('textarea', { value: lists.asset_types, style: 'min-height:150px', oninput: e => { lists.asset_types = e.target.value; } })),
          h('div', { style: 'height:14px' }),
          field('Keyboard sizes (one per line)', h('textarea', { value: lists.keyboard_layouts, style: 'min-height:150px', oninput: e => { lists.keyboard_layouts = e.target.value; } }))))),
    omadaCard,
    proxmoxCard,
  ];
}

/**
 * Settings card for a service Rigbook syncs from (Omada, Proxmox). `fields` are [key, label, placeholder]; the last
 * one is the secret, which the server never sends back.
 */
async function integrationCard({ title, path, intro, fields, synced }) {
  const info = await api('GET', path);
  const secretKey = fields[fields.length - 1][0];
  const st = Object.fromEntries(fields.map(([k]) => [k, k === secretKey ? '' : info[k] || '']));
  const status = h('div');
  const showStatus = i => {
    const s = i.status || {};
    setKids(status, !i.url ? h('p', { class: 'muted small', style: 'margin:0' }, 'Not set up.')
      : s.ok ? h('div', { class: 'alert hint small', style: 'margin:0' }, icon('check'), `Synced ${ago(s.at)}: ${synced(s)}. Syncs every 5 minutes.`)
        : s.error ? h('div', { class: 'alert error small', style: 'margin:0' }, icon('alert'), `Last sync ${ago(s.at)} failed: ${s.error}`)
          : h('p', { class: 'muted small', style: 'margin:0' }, 'Not synced yet.'));
  };
  showStatus(info);
  const run = async (fn, btn) => {
    btn.disabled = true;
    try { showStatus(await fn()); toast(`${title} synced`); } catch (e) { toast(e.message, true); setKids(status, h('div', { class: 'alert error small', style: 'margin:0' }, icon('alert'), e.message)); }
    btn.disabled = false;
  };
  const saveBtn = h('button', { class: 'small primary', onclick: e => run(() => api('PUT', path, st), e.currentTarget) }, icon('check'), 'Save and test');
  const syncBtn = h('button', { class: 'small', onclick: e => run(() => api('POST', `${path}/sync`), e.currentTarget) }, 'Sync now');
  return h('section', { class: 'card', style: 'margin-top:18px' },
    h('div', { class: 'card-head' }, h('h2', null, title)),
    h('div', { class: 'card-body' },
      h('p', { class: 'muted small', style: 'margin-top:0' }, intro),
      h('div', { class: 'grid' }, fields.map(([k, label, placeholder], i) => k === secretKey
        ? field(label, h('input', { type: 'password', autocomplete: 'new-password', placeholder: info.has_secret ? 'Saved (leave blank to keep)' : placeholder, oninput: e => { st[k] = e.target.value; } }))
        : field(label, h('input', { value: st[k], placeholder, autocomplete: 'off', oninput: e => { st[k] = e.target.value; } }), i === 0 ? 'wide' : ''))),
      h('p', { class: 'faint small' }, 'The secret is stored in rigbook.db and is never sent back to the browser. Clear the address and save to disconnect.'),
      status,
      h('div', { class: 'actions', style: 'margin-top:12px' }, saveBtn, info.url ? syncBtn : null)));
}

const omadaSettings = () => integrationCard({
  title: 'Omada controller', path: '/api/omada',
  intro: 'Access points don’t report their Wi-Fi clients over SNMP. With the controller connected, the network map puts each wireless device under the AP it’s on and uses the controller’s client names. Create the credentials under Settings → Platform Integration → Open API → Add New App, in Client Credentials mode.',
  fields: [['url', 'Controller address', 'https://192.168.1.99:8043'], ['client_id', 'Client ID', ''], ['client_secret', 'Client secret', '']],
  synced: s => `${s.clients} clients, ${s.devices} Omada devices`,
});

const proxmoxSettings = () => integrationCard({
  title: 'Proxmox VE', path: '/api/proxmox',
  intro: 'Lists every VM and container with its node, so the network map draws them by name under their host instead of as unknown MACs. Use an API token with the PVEAuditor role: Datacenter → Permissions → API Tokens.',
  fields: [['url', 'Proxmox address', 'https://192.168.1.88:8006'], ['token_id', 'Token ID', 'root@pam!rigbook'], ['secret', 'Token secret', '']],
  synced: s => `${s.guests} VMs and containers on ${s.nodes} node${s.nodes === 1 ? '' : 's'}`,
});

// ------------------------------------------------------------------ router

const ROUTES = [
  [/^#\/assets\/new(?:\?method=(\w+))?$/, 'new', m => newAssetView(m[1])],
  [/^#\/assets\/(\d+)$/, 'assets', m => assetView(m[1])],
  [/^#\/keyboards\/(\d+)$/, 'keyboards', m => keyboardView(m[1])],
  [/^#\/map$/, 'map', () => mapView()],
  [/^#\/keyboards$/, 'keyboards', () => keyboardsView()],
  [/^#\/inventory\/(\d+)$/, 'inventory', m => itemView(m[1])],
  [/^#\/inventory(?:\?cat=(\w+))?$/, 'inventory', m => inventoryView(m[1])],
  [/^#\/review\/(\d+)(?:\?asset=(\d+))?$/, 'scans', m => reviewView(m[1], m[2])],
  [/^#\/scans$/, 'scans', () => scansView()],
  [/^#\/settings$/, 'settings', () => settingsView()],
  [/^(#\/assets)?$|^#\/?$/, 'assets', () => assetsView()],
];

let routeToken = 0;
async function route() {
  const token = ++routeToken;
  const hash = location.hash;
  const hit = ROUTES.find(([re]) => re.test(hash)) || ROUTES[ROUTES.length - 1];
  const m = hash.match(hit[0]) || [];
  NAV.active = hit[1];
  NAV.cat = '';
  if (hit[1] === 'inventory' && !/^#\/inventory\/\d/.test(hash)) NAV.cat = m[1] || '';
  renderNav();
  document.body.classList.remove('nav-open');
  document.getElementById('modal-root').replaceChildren();
  try {
    const content = await hit[2](m);
    if (token !== routeToken) return;
    setKids($main, ...[content].flat(Infinity).filter(Boolean));
    renderCrumbs();
  } catch (e) {
    if (token !== routeToken) return;
    setKids($main, h('div', { class: 'alert error' }, icon('alert'), e.message));
  }
  refreshCounts().catch(() => { /* counts are optional */ });
}

setupShell();
window.addEventListener('hashchange', () => { route(); window.scrollTo(0, 0); });
loadMeta().then(route).catch(e => { setKids($main, h('div', { class: 'alert error' }, icon('alert'), `Could not reach the server: ${e.message}`)); });
