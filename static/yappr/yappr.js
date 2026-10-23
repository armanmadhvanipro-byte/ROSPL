// Yappr: the animated companion and its chat (ported from the Katachi call desk). The server
// (/yappr/chat) runs the model and the data tools and hands back what to do on the page: open a
// page, point at something. Plain commands ("open warranties", "how do I upload an invoice?") are
// answered here with no model call. The chat rides in sessionStorage across page loads.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const ANCHOR = CSS.supports?.('anchor-name: --y') && CSS.supports('top: anchor(top)'); // CSS anchor positioning
const CSRF = document.querySelector('meta[name=csrf-token]')?.content || '';
const ADMIN = document.body.dataset.role === 'admin';
const KEY = 'yappr:v1';
const ICON = {
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  send: '<svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  stop: '<svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>',
};
let buddy, A;

// ---------- what Yappr can open and point at ----------
const SECTIONS = { dashboard: ['/', 'Dashboard'], purchases: ['/purchases', 'Purchases'], vendors: ['/vendors', 'Vendors'], assets: ['/assets', 'Assets'], warranties: ['/warranties', 'Warranties'], services: ['/services', 'Services'], reports: ['/reports', 'Reports'], users: ['/users', 'Users'] };
// [selector, page it lives on]. The same keys are POINT_TARGETS in app.py.
const TARGETS = {
  kpis: ['[data-t=kpis]', '/'], attention: ['[data-t=attention]', '/'], expiring: ['[data-t=expiring]', '/'],
  vendor_chart: ['[data-t=vendor_chart]', '/'], category_chart: ['[data-t=category_chart]', '/'], renewals: ['[data-t=renewals]', '/'],
  expiry_window: ['select[name=days]', '/'],
  new_purchase: ['[data-t=new_purchase]', '/purchases'], invoice_upload: ['input[name=invoice_file]', '/purchases/new'],
  purchase_filters: ['.filters', '/purchases'],
  register_asset: ['[data-t=register_asset]', '/assets'], warranty_fields: ['input[name=warranty_months]', '/assets/new'],
  amc_fields: ['input[name=amc_start]', '/assets/new'], asset_search: ['input[name=q]', '/assets'],
  warranty_tabs: ['[data-t=warranty_tabs]', '/warranties'], add_service: ['[data-t=add_service]', '/services'],
  new_vendor: ['[data-t=new_vendor]', '/vendors'], report_tabs: ['[data-t=report_tabs]', '/reports'], export: ['[data-t=export]', '/reports'],
  users: ['form.form', '/users'], sections: ['.sidebar nav', null], sign_out: ['.me a', null],
};
const SUGG = ['What needs attention?', 'Which warranties expire soon?', 'How do I upload an invoice?', 'Top vendors by spend', 'Open service cases'];
// After an answer, the questions that usually come next.
const NEXT = {
  get_summary: ['Which warranties expire soon?', 'Purchases missing invoices', 'Open service cases'],
  find_assets: ['Open the first one', 'Export this as PDF'],
  find_purchases: ['Open the latest one', 'Which are unpaid?'],
  open_asset: ['Log a service for it', 'Show its invoice'],
  open_purchase: ['Which assets came from it?', 'Is the invoice uploaded?'],
  find_services: ['Total repair cost this year', 'Which assets break down most?'],
  find_vendors: ['Purchases from the top vendor'],
  get_report: ['Export it as CSV'],
};

// ---------- answered on the page, no model call: "open X", "add a purchase", "how do I / where is" ----------
// The first match wins, so specific things come first and generic words last.
const HOWTO = [ // [words, target, what to say]
  [/upload|attach|invoice (?:file|document|pdf|copy|photo|scan)|proof of purchase|bill (?:copy|photo|scan)/, 'invoice_upload', 'Attach the invoice PDF or photo here when you save a purchase.'],
  [/export|download|\bpdf\b|\bcsv\b|excel|print/, 'export', 'Pick a report, set the filters, then export it as CSV or PDF here.'],
  [/\bamc\b|maintenance contract/, 'amc_fields', 'Add the AMC provider with its start and end dates here on the asset form.'],
  [/window|how far ahead|expiring soon (?:means|period|days)/, 'expiry_window', 'Change how many days ahead counts as “expiring soon” here.'],
  [/warranty (?:period|duration|months|start)|calculate|expiry date|(?:add|set|enter) (?:a |the )?warranty/, 'warranty_fields', 'Enter the warranty start and months here. The expiry date is calculated for you.'],
  [/expir|warrant/, 'warranty_tabs', 'Switch between Active, Expiring Soon, Expired and Pending warranties here.'],
  [/service|repair|maintenance|complaint|breakdown|fix/, 'add_service', 'Log a repair or service visit here. Open cases mark the asset Under Repair.'],
  [/register|(?:add|new|create) (?:an? )?(?:asset|item|equipment)/, 'register_asset', 'Register an asset here and link it to its purchase invoice.'],
  [/(?:add|new|record|enter|create) (?:an? )?(?:purchase|invoice|bill)/, 'new_purchase', 'Record a new purchase and its invoice here.'],
  [/missing invoice|without (?:an? )?invoice|no invoice/, 'purchase_filters', 'Tick “Missing invoice” here to list purchases with no document.'],
  [/purchase|invoice|bill|unpaid|payment/, 'purchase_filters', 'Search and filter purchases by vendor, payment, dates or missing invoice here.'],
  [/spend|vendor.?wise|top vendors?/, 'vendor_chart', 'Spend per vendor is shown here.'],
  [/vendor|supplier|dealer|gst/, 'new_vendor', 'Add a vendor here with contact details and GSTIN.'],
  [/categor/, 'category_chart', 'Asset value by category is shown here.'],
  [/renewal|\bdue\b|upcoming/, 'renewals', 'Upcoming services and AMC renewals show up here.'],
  [/attention|alerts?|pending (?:items|tasks)|to.?do/, 'attention', 'Everything that needs action is listed here. Click one to see it.'],
  [/report/, 'report_tabs', 'Choose a report here: purchases, vendors, assets, categories or services.'],
  [/serial|asset|search|find|look ?up|department|location/, 'asset_search', 'Search assets by name, serial number, department or location here.'],
  [/totals?|summary|kpis?|numbers|overview|dashboard/, 'kpis', 'The headline numbers are up here. Click any card to dig in.'],
  [/users?|password|roles?|account|staff/, 'users', 'Add a user or reset a password here.'],
  [/sign ?out|log ?out|logout/, 'sign_out', 'Log out here when you’re done.'],
  [/menu|sections?|pages?|navigat|sidebar/, 'sections', 'Every page is in this menu.'],
];
// "How do I / where is / show me how / kaise / kahan…": a request to be shown, not a data question.
const GUIDE = /^(?:how (?:do|can|to|should|would) i|how to|where(?:'s| is| are| do| can| would)?|show me(?: how| where)|teach me|guide me|help me(?: find)?|tell me (?:how|where)|walk me through)\b|\b(?:kaise|kaisey|kahan|kahaan|kaha|kidhar|batao|samjhao)\b|कैसे|कहाँ|कहां|बताओ|समझाओ/;
const DATA_Q = /^how (?:many|much|did|was|is|are|long|often)\b|\bkitn[aie]\b|कितन/;
const SECTION_WORDS = { dashboard: 'dashboard', home: 'dashboard', overview: 'dashboard', purchases: 'purchases', invoices: 'purchases', vendors: 'vendors', suppliers: 'vendors', assets: 'assets', warranties: 'warranties', warranty: 'warranties', services: 'services', repairs: 'services', reports: 'reports', users: 'users' };
const CREATE = [ // "add a purchase" etc: open the form
  [/^(?:add|new|create|record|enter) (?:a |an )?(?:purchase|invoice|bill)$/, '/purchases/new', 'New purchase form'],
  [/^(?:add|new|create|register) (?:a |an )?(?:asset|item)$/, '/assets/new', 'Register asset form'],
  [/^(?:add|new|create|log) (?:a |an )?(?:service|repair)(?: record)?$/, '/services/new', 'New service record form'],
  [/^(?:add|new|create) (?:a |an )?(?:vendor|supplier)$/, '/vendors/new', 'New vendor form'],
];
const isGuide = t => GUIDE.test(t) && !DATA_Q.test(t) && t.split(' ').length <= 16;
/** A request that needs no thinking: {go, label, point, reply}, or null for the model. */
function local(text) {
  const t = text.toLowerCase().replace(/’/g, "'").replace(/\b(?:please|pls|can you|could you)\b/g, '').replace(/[.!?]+$/, '').replace(/\s+/g, ' ').trim();
  let m;
  if ((m = t.match(/^(?:open|show(?: me)?|go to|take me to)\s+(?:the\s+)?(.+?)(?:\s+(?:page|section|tab))?$/)) && SECTION_WORDS[m[1]]) {
    const s = SECTION_WORDS[m[1]];
    if (s === 'users' && !ADMIN) return { reply: 'Only admins can manage users. Ask your admin.' };
    return { go: SECTIONS[s][0], label: `Opened ${SECTIONS[s][1]}`, reply: `Here’s ${SECTIONS[s][1]}.` };
  }
  const c = CREATE.find(([re]) => re.test(t));
  if (c) return { go: c[1], label: `Opened the ${c[2]}`, reply: `Here’s the ${c[2].toLowerCase()}. Fields with * are required.` };
  if (isGuide(t)) {
    const hit = HOWTO.find(([re]) => re.test(t));
    if (hit?.[1] === 'users' && !ADMIN) return { reply: 'Only admins can add users or reset passwords. Ask your admin.' };
    if (hit) return { point: { target: hit[1], text: hit[2] }, reply: hit[2] };
  }
  return null;
}

// ---------- the chat ----------
const logEl = () => $('yLog');
const nearEnd = () => logEl().scrollHeight - logEl().scrollTop - logEl().clientHeight < 80;
const put = html => { const stick = nearEnd(); logEl().insertAdjacentHTML('beforeend', html); if (stick) logEl().scrollTop = logEl().scrollHeight; return logEl().lastElementChild; };
const fmt = text => esc(String(text || '').trim()).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/^[-*] /gm, '• ');
const say = (who, text) => put(`<div class="y-msg ${who}">${fmt(text)}</div>`);
const chips = list => list?.length && put(`<div class="y-sugg">${list.map(s => `<button type="button">${esc(s)}</button>`).join('')}</div>`);
const act = (label, href) => put(`<div class="y-act">${href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(label)}</a>` : esc(label)}</div>`);
function intro() {
  logEl().innerHTML = '';
  say('a', `Hi${document.body.dataset.user ? ' ' + document.body.dataset.user : ''}, I’m Yappr! Ask me about your purchases, invoices, assets and warranties, or tell me what to open. Stuck? Ask “how do I…” and I’ll show you on the page.`);
  chips(SUGG);
}
/** The answer types itself out, a few words a frame. */
function typeOut(text) {
  const el = put('<div class="y-msg a live"></div>'), words = String(text || '').trim().split(/(\s+)/);
  if (reduceMotion || words.length < 4) { el.innerHTML = fmt(text); el.classList.remove('live'); return Promise.resolve(); }
  return new Promise(done => {
    let i = 0;
    const step = () => {
      i = Math.min(words.length, i + Math.max(2, Math.ceil(words.length / 40)));
      const stick = nearEnd(); el.innerHTML = fmt(words.slice(0, i).join('')); if (stick) logEl().scrollTop = logEl().scrollHeight;
      if (i < words.length) requestAnimationFrame(step); else { el.classList.remove('live'); done(); }
    };
    requestAnimationFrame(step);
  });
}
// History is cut at a question, never between a tool call and its result; earlier tool results shrink to a stub.
function chatHistory() {
  let m = A.msgs.slice(-24);
  while (m.length && m[0].role !== 'user') m = m.slice(1);
  const lastQ = m.map(x => x.role).lastIndexOf('user');
  return m.map((x, i) => (x.role === 'tool' && i < lastQ && x.content.length > 300 ? { ...x, content: x.content.slice(0, 300) + '…(earlier result, trimmed)' } : x));
}

// ---------- state across page loads ----------
function save(pending) {
  try {
    while (logEl().innerHTML.length > 60000 && logEl().children.length > 4) logEl().firstElementChild.remove();
    sessionStorage.setItem(KEY, JSON.stringify({ msgs: A.msgs.slice(-40), log: logEl().innerHTML, open: !$('yAgent').hidden, pending: pending || null }));
  } catch {}
}
function load() { try { return JSON.parse(sessionStorage.getItem(KEY)) || {}; } catch { return {}; } }
const here = () => location.pathname.replace(/\/+$/, '') || '/';
/** Go to a page (the chat follows), or point if what it should show is on this page already. */
function goto(url) { save(); buddy.feel('excited', 3000); setTimeout(() => { location.href = url; }, reduceMotion ? 0 : 450); }
/** Returns true when it has to load another page first (it points once that page is up). */
function showPoint({ target, text }) {
  const t = TARGETS[target];
  if (!t) return false;
  if (t[1] && t[1] !== here()) { save({ target, text }); setTimeout(() => { location.href = t[1]; }, reduceMotion ? 0 : 450); return true; }
  setTimeout(() => buddy.point(t[0], text || 'Right here.', { mood: 'happy', actions: [['Back to chat', () => toggle(true)]] }), 150);
  return false;
}

function busyUI(on) {
  const b = $('ySend');
  b.innerHTML = on ? ICON.stop : ICON.send;
  b.setAttribute('aria-label', on ? 'Stop' : 'Send'); b.classList.toggle('stop', on);
}

// The face follows the conversation: how the question sounds, what each tool does, how it turned out.
const VIBES = [
  [/\b(?:thanks|thank you|thx|great|awesome|nice|good job|well done|perfect|love (?:it|you))\b|शुक्रिया|धन्यवाद|बढ़िया/i, 'shy'],
  [/\b(?:lol|lmao|haha+|hehe+)\b/i, 'laughing'],
  [/^\s*(?:hi|hello|hey|namaste|good (?:morning|afternoon|evening))\b/i, 'excited'],
  [/\b(?:useless|stupid|dumb|bad bot|not working|wrong again)\b/i, 'sad'],
  [/\b(?:urgent|asap|hurry)\b/i, 'surprised'],
];
const TOOL_MOOD = { find_assets: 'searching', find_purchases: 'searching', find_services: 'searching', find_vendors: 'searching', open_asset: 'curious', open_purchase: 'curious', get_summary: 'working', get_report: 'working', navigate: 'curious' };
function resultMood(name, out) {
  if (name === 'point_at') return null;
  if (out?.found === 0) return 'confused';
  const w = out?.warranties;
  if (name === 'get_summary' && w && (w.Expired || w['Expiring Soon'])) return 'surprised';
  if (name === 'get_report') return 'proud';
  return 'happy';
}

async function ask(text) {
  text = String(text || '').trim();
  if (A.busy || !text) return;
  $('yIn').value = ''; A.last = text;
  logEl().querySelectorAll('.y-sugg').forEach(s => s.remove());
  say('u', text);
  const vibe = VIBES.find(([re]) => re.test(text))?.[1];
  if (vibe) buddy.feel(vibe, 1600);

  // Plain commands run here, instantly, with no model call.
  const hit = local(text);
  if (hit) {
    A.msgs.push({ role: 'user', content: text }, { role: 'assistant', content: hit.reply });
    if (hit.label) act(hit.label);
    say('a', hit.reply);
    if (hit.point) { save(); showPoint(hit.point); }
    else if (hit.go) goto(hit.go);
    else { buddy.feel('happy', 2000); save(); }
    return;
  }

  A.msgs.push({ role: 'user', content: text });
  A.busy = true; A.ctrl = new AbortController(); busyUI(true);
  if (vibe) buddy.rest('thinking'); else buddy.set('thinking');
  const dots = put('<div class="y-dots" role="img" aria-label="Thinking"><i></i><i></i><i></i></div>');
  let failed = false, failMood = 'sad', endMood = 'happy', lastTool = null, go = null, point = null, leaving = false;
  try {
    let r;
    try {
      r = await fetch('/yappr/chat', {
        method: 'POST', signal: A.ctrl.signal, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
        body: JSON.stringify({ messages: chatHistory(), mode: isGuide(text.toLowerCase()) ? 'guide' : undefined }),
      });
    } catch (e) {
      if (e.name === 'AbortError') { put('<div class="y-act stopped">Stopped</div>'); return; }
      throw new Error('Couldn’t reach Yappr. Check your connection.');
    }
    if (r.redirected || r.status === 401) { location.href = '/login'; return; }
    const j = await r.json().catch(() => ({}));
    for (const m of j.messages || []) A.msgs.push(m);
    if (!r.ok) throw new Error(j.error || `Yappr couldn’t answer (${r.status}).`);
    dots.remove();
    // What each tool did, said in a line, so actions are never silent.
    for (const m of j.messages || []) {
      if (m.role !== 'tool') continue;
      const call = j.messages.flatMap(x => x.tool_calls || []).find(c => c.id === m.tool_call_id);
      const name = call?.function?.name; let out = {};
      try { out = JSON.parse(m.content); } catch {}
      if (name) buddy.feel(TOOL_MOOD[name] || 'working', 1200);
      if (name && !out.error) { lastTool = name; endMood = resultMood(name, out); }
    }
    for (const a of j.actions || []) {
      if (a.link) act(a.label, a.link);
      else if (a.go) { go = a.go; act(a.label); }
      else if (a.point) point = { target: a.point, text: a.text };
    }
    const reply = [...(j.messages || [])].reverse().find(m => m.role === 'assistant' && m.content)?.content;
    buddy.set('idle');
    if (reply) await typeOut(reply);
    if (point) { endMood = null; save(); leaving = showPoint(point); }
    else if (go && go !== location.pathname + location.search) { leaving = true; goto(go); }
    else chips(NEXT[lastTool]);
  } catch (e) {
    failed = true;
    failMood = /reach|connection/i.test(e.message) ? 'scared' : /busy|no ai model/i.test(e.message) ? 'confused' : 'sad';
    say('a err', e.message);
    put('<div class="y-sugg"><button type="button" data-retry>Try again</button></div>');
  } finally {
    dots.remove();
    A.busy = false; A.ctrl = null; busyUI(false);
    if (!$('yAgent').hidden) $('yIn').focus();
    if (buddy.pointing) buddy.rest('idle'); else buddy.set('idle'); // never cut short the face it's pointing with
    if (failed) buddy.feel(failMood, 3500);
    else if (endMood && !buddy.pointing) buddy.feel(endMood, 2400);
    if (!leaving) save();
  }
}

function toggle(open = $('yAgent').hidden) {
  $('yAgent').hidden = !open; $('yBtn').setAttribute('aria-expanded', open);
  if (open) buddy.dock(); else buddy.home(); // the chat opens beside the companion; closed, it goes back to its corner
  if (open) { if (!logEl().children.length) intro(); $('yIn').focus(); }
  else if ($('yAgent').contains(document.activeElement)) $('yBtn').focus();
  save();
}

function build() {
  A = { msgs: [], busy: false, ctrl: null, last: '' };
  document.body.insertAdjacentHTML('beforeend', `
<button class="ybtn" id="yBtn" type="button" aria-label="Ask Yappr, the assistant (Ctrl+K)" aria-expanded="false" aria-controls="yAgent"><span class="yav" data-av aria-hidden="true"></span></button>
<div class="y-bub" id="yBub" role="status" aria-live="polite" hidden><p id="yBubText"></p><div class="y-bub-acts" id="yBubActs"></div><button class="y-x" type="button" id="yBubX" aria-label="Dismiss">${ICON.x}</button></div>
<div class="y-spot" id="ySpot" hidden></div>
<section class="y-agent" id="yAgent" role="dialog" aria-label="Yappr, the assistant" hidden>
  <header class="y-top"><span class="yav" data-av style="--s:36px" aria-hidden="true"></span><div><b>Yappr</b><small id="yMood">Here to help</small></div>
    <button class="y-icon" id="yNew" type="button" title="New conversation" aria-label="New conversation">${ICON.plus}</button>
    <button class="y-icon" id="yClose" type="button" title="Close (Esc)" aria-label="Close Yappr">${ICON.x}</button></header>
  <div class="y-log" id="yLog" role="log" aria-live="polite"></div>
  <form class="y-form" id="yForm"><input id="yIn" placeholder="Ask, or tell me what to open…" autocomplete="off" aria-label="Message Yappr"><button class="y-send" id="ySend" type="submit" aria-label="Send">${ICON.send}</button></form>
</section>`);
  const state = load();
  let firstVisit = false;
  try { firstVisit = !sessionStorage.getItem('yappr:hi'); sessionStorage.setItem('yappr:hi', '1'); } catch {}
  buddy = makeBuddy(firstVisit);
  const input = $('yIn');
  input.addEventListener('input', e => { if (!A.busy) buddy.set(e.target.value ? 'listening' : 'idle'); });
  input.addEventListener('blur', () => { if (!A.busy) buddy.set('idle'); });
  // Up arrow in an empty box brings back the last question.
  input.addEventListener('keydown', e => { if (e.key === 'ArrowUp' && !e.target.value && A.last) { e.preventDefault(); e.target.value = A.last; } });
  $('yBtn').addEventListener('click', () => { buddy.hush(); toggle(); buddy.unpoint(); });
  $('yClose').onclick = () => toggle(false);
  $('yNew').onclick = () => { A.ctrl?.abort(); A.msgs = []; intro(); save(); };
  // While an answer is coming, the send button is a stop button.
  $('yForm').onsubmit = e => { e.preventDefault(); if (A.busy) A.ctrl?.abort(); else ask(input.value); };
  logEl().addEventListener('click', e => {
    const b = e.target.closest('.y-sugg button'); if (!b) return;
    if (b.hasAttribute('data-retry')) {
      const i = A.msgs.findLastIndex(x => x.role === 'user'), q = A.msgs[i]?.content;
      if (q) { A.msgs = A.msgs.slice(0, i); b.closest('.y-sugg').remove(); ask(q); }
      return;
    }
    ask(b.textContent);
  });
  addEventListener('keydown', e => {
    if (e.key === 'Escape' && !$('yAgent').hidden) toggle(false);
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); toggle(true); }
  });

  // Pick up where the last page left off: the chat, whether it was open, and anything it was about to point at.
  A.msgs = Array.isArray(state.msgs) ? state.msgs : [];
  if (state.log) logEl().innerHTML = state.log;
  if (state.open) { toggle(true); logEl().scrollTop = logEl().scrollHeight; }
  if (state.pending) { save(); setTimeout(() => showPoint(state.pending), reduceMotion ? 0 : 650); }
}

// ---------- the companion: avatar.json draws the face, page events set the mood ----------
// A mood is one of the file's animations (idle, thinking, happy…): a loop of expressions with
// its own blink rhythm. One timer per step and per blink, none while the tab is hidden.
const MOODTEXT = { thinking: 'Thinking…', working: 'On it…', searching: 'Looking…', listening: 'Listening', sleeping: 'Napping', drowsy: 'Getting sleepy', bored: 'Here if you need me', sad: 'Hmm, that didn’t work', confused: 'Let me help with that', surprised: 'Oh!', scared: 'Uh oh…', curious: 'Have a look', waking: 'Waking up', proud: 'Done!', excited: 'Yay!', celebrate: 'All good!', playful: 'Hi there', shy: 'Aw, thanks', laughing: 'Ha!', suspicious: 'Hmm…' };
const HOPPY = new Set(['happy', 'excited', 'celebrate', 'laughing', 'playful', 'surprised', 'proud']);
function makeBuddy(firstVisit) {
  const NEUTRAL = { head: { x: 0, y: 0, z: 0 }, eyes: { left: { width: 20, height: 50, x: 0, y: -7 }, right: { width: 20, height: 50, x: 0, y: -7 }, spacing: 35 } };
  let def = { expressions: { neutral: NEUTRAL }, animations: {}, colors: { body: '#8fa67e', eyes: '#2a1e16' } };
  const btn = $('yBtn'), bub = $('yBub'), spot = $('ySpot'), agent = $('yAgent');
  const avs = [...document.querySelectorAll('[data-av]')].map(el => {
    el.innerHTML = '<span class="mot"><span class="bod"><i class="ball"></i><span class="face"><span class="lids"><i class="eye"></i><i class="eye"></i></span></span></span></span>';
    const lids = el.querySelector('.lids'), bod = el.querySelector('.bod');
    lids.addEventListener('animationend', () => lids.classList.remove('blink'));
    bod.addEventListener('animationend', e => { if (e.target === bod) bod.classList.remove('hop'); });
    return { el, bod, face: el.querySelector('.face'), lids, eyes: el.querySelectorAll('.eye') };
  });
  const hop = () => { if (!reduceMotion) for (const a of avs) a.bod.classList.add('hop'); };
  // Sizes are in the file's units (a 240-wide sphere), so CSS alone sets how big an avatar is.
  const u = n => `calc(var(--s) * ${(+n || 0).toFixed(2)} / 240)`;
  const clamp = (v, m) => Math.max(-m, Math.min(m, v));
  const vp = () => [document.documentElement.clientWidth, document.documentElement.clientHeight];
  let base = 'idle', mood = '', cur = NEUTRAL, gaze = [0, 0], stepT, blinkT, backT;

  /** Head turn: the face slides across the ball and narrows as it turns away. */
  function paintFace(t) {
    const h = cur.head || {}, r = Math.PI / 180, yaw = clamp((h.y || 0) + gaze[0], 42) * r, pitch = clamp((h.x || 0) + gaze[1], 38) * r;
    for (const a of avs) {
      a.face.style.setProperty('--t', `${t}ms`);
      a.face.style.transform = `translate(${u(Math.sin(yaw) * 100)}, ${u(-Math.sin(pitch) * 100)}) rotate(${(h.z || 0).toFixed(1)}deg) scale(${(0.55 + 0.45 * Math.cos(yaw)).toFixed(3)}, ${(0.6 + 0.4 * Math.cos(pitch)).toFixed(3)})`;
    }
  }
  function show(name, t = 500) {
    cur = def.expressions[name] || def.expressions.neutral || NEUTRAL;
    const E = cur.eyes || NEUTRAL.eyes, half = (E.spacing ?? 35) / 2, c = { ...def.colors, ...cur.colors };
    for (const a of avs) {
      a.el.style.setProperty('--bd', c.body || '#8fa67e'); a.el.style.setProperty('--ey', c.eyes || '#2a1e16');
      a.el.classList.toggle('m-shake', cur.motion?.body === 'shake'); a.el.classList.toggle('m-drift', cur.motion?.body === 'slowDrift'); a.el.classList.toggle('e-shake', cur.motion?.eyes === 'shake');
      [[E.left, -half], [E.right, half]].forEach(([g, dx], i) => {
        const s = a.eyes[i].style;
        s.width = u(g.width); s.height = u(g.height);
        s.transform = `translate(calc(${u(dx + (g.x || 0))} - 50%), calc(${u(g.y || 0)} - 50%)) rotate(${(g.angle || 0).toFixed(1)}deg)`;
      });
    }
    paintFace(t);
  }
  function loop(name) {
    if (name !== mood && HOPPY.has(name)) hop();
    mood = name; clearTimeout(stepT); btn.dataset.mood = name;
    const steps = def.animations[name]?.steps || [];
    let i = 0;
    const step = () => {
      const s = steps[i++ % (steps.length || 1)];
      show(s?.expression || 'neutral', s?.transitionMs ?? 500);
      if (steps.length > 1 && !reduceMotion && !document.hidden) stepT = setTimeout(step, (s.transitionMs || 500) + (s.holdMs || 2300));
    };
    step(); blink(true);
    $('yMood').textContent = MOODTEXT[name] || 'Here to help';
  }
  function blink(first) {
    clearTimeout(blinkT);
    const b = def.animations[mood]?.blink;
    if (!b?.enabled || reduceMotion || document.hidden) return;
    blinkT = setTimeout(() => {
      for (const a of avs) { a.lids.style.setProperty('--bt', `${b.durationMs}ms`); a.lids.classList.add('blink'); }
      blink();
    }, first ? b.initialDelayMs : b.minIntervalMs + Math.random() * (b.maxIntervalMs - b.minIntervalMs));
  }
  /** A lasting state (idle, listening, thinking…). */
  function set(name) { base = name; clearTimeout(backT); if (mood !== name) loop(name); }
  /** The lasting state to return to, without changing the face now. */
  function rest(name) { base = name; }
  /** A passing feeling, then back to the lasting state. */
  function feel(name, ms = 2600) { clearTimeout(backT); if (mood !== name) loop(name); backT = setTimeout(() => loop(base), ms); }
  function look(dx, dy, t = 260) {
    const g = [clamp(dx / 18, 26), clamp(-dy / 18, 20)];
    if (Math.abs(g[0] - gaze[0]) + Math.abs(g[1] - gaze[1]) < 1.5) return;
    gaze = g; paintFace(t);
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) { clearTimeout(stepT); clearTimeout(blinkT); clearTimeout(wanderT); } else { loop(mood); wander(); } });

  // Left alone it glances around, now and then pulls a face, and strolls a little from its corner and back.
  const IDLE_FACES = ['curious', 'playful', 'happy', 'shy', 'proud', 'suspicious'];
  let wanderT, roamT, lastMove = 0;
  const free = () => !pointing && ['idle', 'listening', 'bored'].includes(mood);
  function wander() {
    clearTimeout(wanderT);
    if (document.hidden || reduceMotion) return;
    wanderT = setTimeout(() => {
      if (free() && Date.now() - lastMove > 2000) { gaze = [(Math.random() * 2 - 1) * 16, (Math.random() * 2 - 1) * 10]; paintFace(420); }
      const calm = mood === 'idle' && base === 'idle' && !pointing && !away && bub.hidden;
      if (calm && Math.random() < 0.08) feel(IDLE_FACES[Math.floor(Math.random() * IDLE_FACES.length)], 2600);
      else if (calm && !roamT && agent.hidden && Math.random() < 0.15) {
        const dx = -(30 + Math.random() * 90), dy = -(Math.random() * 40);
        btn.style.translate = `${dx.toFixed(0)}px ${dy.toFixed(0)}px`; gaze = [-18, 6]; paintFace(500);
        roamT = setTimeout(() => { btn.style.translate = ''; gaze = [14, 0]; paintFace(500); roamT = setTimeout(() => (roamT = null), 1800); }, 2500 + Math.random() * 2500);
      }
      wander();
    }, 1300 + Math.random() * 2200);
  }
  const stopRoam = () => { if (btn.style.translate) btn.style.translate = ''; };
  wander();
  btn.addEventListener('pointerenter', () => { if (['idle', 'bored'].includes(mood)) feel('playful', 1800); });
  btn.addEventListener('click', hop);
  // Its eyes follow the pointer while it has nothing else to do (mouse only, at most once a frame).
  if (matchMedia('(pointer: fine)').matches && !reduceMotion) {
    let p = null;
    addEventListener('pointermove', e => {
      lastMove = Date.now();
      if (!p) requestAnimationFrame(() => {
        if (!pointing && ['idle', 'listening', 'bored'].includes(mood)) { const r = btn.getBoundingClientRect(); look(p.clientX - r.left - r.width / 2, p.clientY - r.top - r.height / 2); }
        p = null;
      });
      p = e;
    }, { passive: true });
  }

  // ---- speech bubble
  let bubT, acts = [];
  function say(text, { actions = [], ms, mood: m } = {}) {
    if (m) feel(m, Math.max(2600, Math.min(ms || 4000, 6000)));
    stopRoam();
    $('yBubText').textContent = text;
    acts = actions;
    $('yBubActs').innerHTML = actions.map(([label], i) => `<button type="button" data-i="${i}">${esc(label)}</button>`).join('');
    bub.hidden = false; place();
    clearTimeout(bubT); bubT = setTimeout(() => { hush(); unpoint(); }, ms || Math.min(15000, 4500 + text.length * 55));
  }
  function hush() { clearTimeout(bubT); bub.hidden = true; }
  /** Where the companion is headed (not where it is mid-flight): beside a field, or its corner. */
  function spotOf() {
    const size = btn.offsetWidth;
    if (away) return { x: away.x, y: away.y, size };
    const cs = getComputedStyle(btn), [vw, vh] = vp();
    return { x: vw - parseFloat(cs.right) - size, y: vh - parseFloat(cs.bottom) - size, size };
  }
  /** Beside the companion's resting place: above it, or below when it sits near the top. */
  function place() {
    if (bub.hidden) return;
    const b = spotOf(), w = bub.offsetWidth, h = bub.offsetHeight, [vw] = vp();
    const right = b.x + b.size / 2 > vw / 2;
    let y = b.y - h - 10, oy = '100%';
    if (y < 70) { y = b.y + b.size + 10; oy = '0%'; }
    bub.style.left = `${Math.max(12, Math.min(vw - w - 12, right ? b.x + b.size - w : b.x))}px`; bub.style.top = `${y}px`;
    bub.style.setProperty('--ox', right ? '100%' : '0%'); bub.style.setProperty('--oy', oy);
  }
  // The action runs first, so "Back to chat" opens the panel while the companion is still where it is.
  $('yBubActs').onclick = e => { const b = e.target.closest('button'); if (!b) return; const f = acts[+b.dataset.i]?.[1]; hush(); f?.(); unpoint(); };
  $('yBubX').onclick = () => { hush(); unpoint(); };
  addEventListener('resize', () => { place(); dock(); });

  // ---- pointing: scroll the thing into view, fly beside it, ring it, look at it, then say what to do there.
  let pointing = null, queued = false, away = null, ring = false; // away: where it flew to, {x, y}; null = home
  function afterScroll(fn) {
    let done = false;
    const fire = () => { if (done) return; done = true; removeEventListener('scrollend', fire, true); fn(); };
    addEventListener('scrollend', fire, true); setTimeout(fire, 650);
  }
  function afterFlight(fn) {
    let done = false;
    const fire = () => { if (done) return; done = true; btn.removeEventListener('transitionend', end); fn(); };
    const end = e => { if (e.target === btn && e.propertyName === 'transform') fire(); };
    btn.addEventListener('transitionend', end); setTimeout(fire, reduceMotion ? 0 : 950);
  }
  function point(target, text, opts = {}) {
    const el = typeof target === 'string' ? [...document.querySelectorAll(target)].find(x => x.getClientRects().length) : target;
    if (!el?.getClientRects().length) { if (text) say(text, opts); return false; }
    if (!agent.hidden && !agent.contains(el)) toggle(false);
    unpoint(true);
    pointing = el; ring = true; stopRoam(); hush();
    feel(opts.mood || 'curious', 5000);
    // Clicking or typing in it means they've found it: the ring goes, the hint stays a moment, then home.
    const found = () => { if (pointing !== el) return; ring = false; spot.hidden = true; clearTimeout(bubT); bubT = setTimeout(() => { hush(); unpoint(); }, 2500); };
    el.addEventListener('input', found, { once: true }); el.addEventListener('click', found, { once: true });
    const go = () => {
      if (pointing !== el) return;
      btn.classList.remove('tracking'); fly();
      afterFlight(() => {
        if (pointing !== el) return;
        btn.classList.add('tracking');
        addEventListener('scroll', track, { passive: true, capture: true }); addEventListener('resize', track);
        say(text, { ...opts, mood: null, ms: opts.ms || Math.min(16000, 6000 + text.length * 60) });
      });
    };
    const r = el.getBoundingClientRect(), [, vh] = vp();
    if (r.top < 80 || r.bottom > vh - 24) { el.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' }); afterScroll(go); } else go();
    return true;
  }
  /** Sets the flight target beside the element (also used to keep up while the page scrolls). */
  function fly() {
    if (!pointing) return;
    const r = pointing.getBoundingClientRect(), pad = 6, size = btn.offsetWidth, gap = 14, [vw, vh] = vp();
    // The ring follows the element natively where anchor positioning exists; else it's placed here.
    if (ANCHOR) { pointing.style.anchorName = '--y-pt'; spot.classList.add('anchored'); }
    else Object.assign(spot.style, { left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px` });
    spot.hidden = !ring;
    // right of it if there's room, else left, else just below
    let x = r.right + gap, y = r.top + Math.min(r.height / 2, 60) - size / 2;
    if (x + size > vw - 8) x = r.left - gap - size;
    const floor = vh - size - 8;
    if (x < 8) {
      x = Math.min(vw - size - 8, Math.max(8, r.left + r.width / 2 - size / 2)); y = r.bottom + gap;
      if (y > floor) y = r.top - gap - size; // no room below: above it, so the bubble doesn't cover it
    }
    y = Math.max(8, Math.min(floor, y));
    const cs = getComputedStyle(btn), hx = vw - parseFloat(cs.right) - size, hy = vh - parseFloat(cs.bottom) - size;
    btn.style.transform = `translate(${(x - hx).toFixed(1)}px, ${(y - hy).toFixed(1)}px)`; btn.classList.add('away'); away = { x, y };
    look(r.left + r.width / 2 - x - size / 2, r.top + r.height / 2 - y - size / 2, 500);
    place(); dock();
  }
  function track() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; fly(); });
  }
  /** Done pointing. It stays put while the chat is open beside it (or when about to fly on), else flies home. */
  function unpoint(staying) {
    if (!pointing) return;
    if (ANCHOR) pointing.style.anchorName = '';
    pointing = null; ring = false; spot.hidden = true;
    removeEventListener('scroll', track, { capture: true }); removeEventListener('resize', track);
    if (!staying && agent.hidden) home();
  }
  function home() {
    if (pointing || !away) return;
    away = null; btn.classList.remove('tracking'); btn.style.transform = ''; btn.classList.remove('away'); gaze = [0, 0]; paintFace(400); place(); dock();
  }
  /** The chat sits beside the companion, wherever it is (its corner when home; phones keep the full sheet). */
  function dock() {
    if (agent.hidden) return;
    if (!away || matchMedia('(max-width: 760px)').matches) { agent.style.left = agent.style.top = agent.style.right = agent.style.bottom = agent.style.transformOrigin = ''; return; }
    const [vw, vh] = vp(), w = agent.offsetWidth, h = agent.offsetHeight, size = btn.offsetWidth, gap = 12;
    let x = away.x - gap - w;
    if (x < 8) x = away.x + size + gap;
    x = Math.max(8, Math.min(vw - w - 8, x));
    const y = Math.max(8, Math.min(vh - h - 8, away.y + size / 2 - h / 2));
    Object.assign(agent.style, { left: `${x}px`, top: `${y}px`, right: 'auto', bottom: 'auto', transformOrigin: x < away.x ? 'right center' : 'left center' });
  }
  addEventListener('keydown', e => { if (e.key === 'Escape') { hush(); unpoint(); } });

  fetch(new URL('avatar.json', import.meta.url)).then(r => r.json()).then(j => { if (j?.expressions && j?.animations) { def = { ...j, colors: { ...def.colors, ...j.colors } }; loop(mood); } }).catch(() => {});
  // First page of the session: it wakes up and says hello. After that it is just there.
  if (firstVisit) {
    loop('waking'); backT = setTimeout(() => loop(base), 1600);
    if (!reduceMotion) setTimeout(() => { if (agent.hidden && !pointing) say('Hi! I’m Yappr. Ask me anything about your purchases and warranties.', { mood: 'excited', ms: 5000 }); }, 2200);
  } else loop('idle');
  return { set, rest, feel, say, hush, point, unpoint, home, dock, get pointing() { return pointing; }, get mood() { return mood; } };
}

build();
export const _test = { local, TARGETS, HOWTO };
