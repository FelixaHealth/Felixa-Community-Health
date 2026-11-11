(() => {
  const root = document.documentElement;
  const toggle = document.getElementById('theme-toggle');
  const paint = () => { if (toggle) toggle.setAttribute('aria-pressed', root.dataset.theme === 'dark' ? 'true' : 'false'); };
  if (toggle) toggle.addEventListener('click', () => {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('felixa-theme', next); } catch (e) {}
    paint();

  const flash = document.getElementById('flash');       // a message after a redirect: move focus to it so it is read and the page does not restart at the top
  if (flash) flash.focus();
  });
  paint();

  const help = document.getElementById('help-dialog');
  document.querySelectorAll('[data-help-open]').forEach(b => b.addEventListener('click', () => { if (help && help.showModal) help.showModal(); }));
  document.querySelectorAll('[data-help-close]').forEach(b => b.addEventListener('click', () => help && help.close()));
  if (help) help.addEventListener('click', e => { if (e.target === help) help.close(); });

  document.querySelectorAll('.dropzone').forEach(zone => {
    const input = zone.querySelector('input[type=file]'), picked = zone.querySelector('.dz-picked');
    const show = () => { picked.textContent = [...input.files].map(f => f.name).join(', '); zone.classList.toggle('has-files', input.files.length > 0); };
    input.addEventListener('change', show);
    // A file that is chosen or dropped is added at once: the button below the page used to be the only way, and people waited for Next to appear.
    input.addEventListener('change', () => { const form = zone.closest('form'); if (form && input.files.length && form.dataset.autosubmit !== 'off') { zone.classList.add('busy'); picked.textContent = 'Adding ' + [...input.files].map(f => f.name).join(', ') + '…'; form.requestSubmit(); } });
    ['dragenter', 'dragover'].forEach(n => zone.addEventListener(n, () => zone.classList.add('drag')));
    ['dragleave', 'drop'].forEach(n => zone.addEventListener(n, () => zone.classList.remove('drag')));
    show();
  });
})();

// Other: a text box that appears when "Other" is chosen in a select, a radio group or a checkbox group.
(() => {
  const wraps = new Set([...document.querySelectorAll('[data-other]')].map(el => el.dataset.other));
  const refresh = () => wraps.forEach(name => {
    const wrap = document.getElementById(name + '_wrap'); if (!wrap) return;
    const controls = [...document.querySelectorAll(`[data-other="${name}"]`)];
    const on = controls.some(el => el.tagName === 'SELECT' ? el.value === 'Other' : el.checked && el.value === 'Other');
    wrap.hidden = !on;
    wrap.querySelectorAll('input,textarea').forEach(i => { i.required = on; });
  });
  document.addEventListener('change', e => { if (e.target.closest('[data-other]')) refresh(); });
  // Radios of one group share a name; the group's Other box must also react when another radio is picked.
  document.querySelectorAll('input[type=radio]').forEach(r => r.addEventListener('change', refresh));
  const offer = document.getElementById('discord-offer');
  if (offer) { const d = document.querySelector('input[name=channel][value=Discord]'); if (d) d.addEventListener('change', () => { offer.hidden = !d.checked; }); }
  refresh();
})();

// Typing a number for the volume chooses that option.
document.querySelectorAll('input[name=volume_custom]').forEach(n => n.addEventListener('input', () => {
  const radio = document.querySelector('input[name=volume][value=custom]'); if (radio) radio.checked = true;
}));

// Time frame: the person's own time zone, so "20:00" means 20:00 where they are. The server turns it into UTC.
(() => {
  const tz = document.querySelector('input[name=tz_offset]'), label = document.getElementById('tz-label');
  if (!tz) return;
  const behind = new Date().getTimezoneOffset();                     // minutes to add to local time to get UTC
  tz.value = String(behind);
  const ahead = -behind, h = Math.floor(Math.abs(ahead) / 60), m = Math.abs(ahead) % 60;
  if (label) label.textContent = ahead === 0 ? 'UTC' : `UTC${ahead > 0 ? '+' : '-'}${h}${m ? ':' + String(m).padStart(2, '0') : ''}`;
})();

// Setup: an empty time zone box starts with this computer's own zone, when it is on the list.
(() => {
  const box = document.querySelector('input[data-tz-auto]');
  if (!box || box.value) return;
  let zone = '';
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { return; }
  const list = document.getElementById('zone-list');
  if (zone && list && [...list.options].some((o) => o.value === zone)) box.value = zone;
})();

// "New analytics" in the side tab jumps to the box that asks before it replaces the messages: open it.
(() => {
  const open = () => { if (location.hash === '#new') { const d = document.querySelector('details.new-confirm'); if (d) { d.open = true; d.querySelector('summary').focus(); } } };
  open(); addEventListener('hashchange', open);
})();

// Edit workplace sits in the workplace page: "Edit" in the side bar opens it, and Cancel closes it.
(() => {
  const box = document.getElementById('edit-details');
  if (!box) return;
  const sync = () => { if (location.hash === '#edit') { box.open = true; box.scrollIntoView({ block: 'start' }); } };
  sync(); addEventListener('hashchange', sync);
  box.querySelectorAll('[data-edit-close]').forEach((b) => b.addEventListener('click', () => { box.open = false; if (location.hash === '#edit') history.replaceState(null, '', location.pathname); }));
})();

// Dashboard: choosing a workplace, a kind or a platform applies at once.
(() => { document.querySelectorAll('select[data-nav]').forEach((s) => s.addEventListener('change', () => s.form.requestSubmit())); })();
(() => { document.querySelectorAll('#dash-form select[data-auto]').forEach((s) => s.addEventListener('change', () => s.form.requestSubmit())); })();

// Tabs: the results page shows one panel at a time. A link to a part inside another panel (#alerts, #vc-flagged) switches to that panel first.
(() => {
  const bar = document.querySelector('[data-tabs]'); if (!bar) return;
  const panels = [...document.querySelectorAll('.tabpanel')]; const tabs = [...bar.querySelectorAll('[role=tab]')];
  document.documentElement.classList.add('js-tabs'); let current = null;
  const route = () => {
    let id = ''; try { id = decodeURIComponent(location.hash.slice(1)); } catch (e) { id = ''; }
    const alias = { 'tab-alerts': 'tab-act', 'tab-badges': 'tab-act', 'tab-guidance': 'tab-act', 'tab-signals': 'tab-understand', 'tab-vibe': 'tab-understand' };   // the names of the tabs before they were merged
    if (alias[id]) id = alias[id];
    const el = id && document.getElementById(id);
    const inside = el && el.closest('.tabpanel');
    if (!inside && current) return;                                  // #main, a stray fragment: leave the tab that is open
    const panel = inside || panels[0]; current = panel;
    panels.forEach((p) => { p.hidden = p !== panel; });
    tabs.forEach((t) => { const on = t.getAttribute('href') === '#' + panel.id; t.classList.toggle('on', on); t.setAttribute('aria-selected', String(on)); });
    if (el && el !== panel) el.scrollIntoView({ block: 'start' });
  };
  window.addEventListener('hashchange', route); route();
})();

// Settings: the theme choice (system, light or dark), kept in this browser.
(() => {
  const radios = [...document.querySelectorAll('input[name="theme-choice"]')]; if (!radios.length) return;
  const root = document.documentElement;
  let saved = ''; try { saved = localStorage.getItem('felixa-theme') || ''; } catch (e) { saved = ''; }
  const now = saved === 'light' || saved === 'dark' ? saved : 'system';
  radios.forEach((r) => { r.checked = r.value === now; r.addEventListener('change', () => {
    try { if (r.value === 'system') localStorage.removeItem('felixa-theme'); else localStorage.setItem('felixa-theme', r.value); } catch (e) {}
    if (r.value === 'system') delete root.dataset.theme; else root.dataset.theme = r.value;
  }); });
})();

// Badges and alerts: the Dashboard / Trends switch, and the "Compare to" menu that reloads with its choice.
(() => {
  document.querySelectorAll('select[data-submit], input[type=file][data-submit]').forEach((s) => s.addEventListener('change', () => s.form.submit()));
  document.querySelectorAll('[data-act]').forEach((box) => {
    const buttons = [...box.querySelectorAll('[data-act-view]')];
    buttons.forEach((b) => b.addEventListener('click', () => {
      const trends = b.dataset.actView === 'trends';
      buttons.forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      document.querySelectorAll('.act-dash').forEach((el) => { el.hidden = trends; });
    }));
  });
})();


// Upload rows: highlight while a file is dragged over; the file input covers the row, so a drop is a choice.
(() => {
  document.querySelectorAll('[data-drop]').forEach((row) => {
    ['dragenter', 'dragover'].forEach((t) => row.addEventListener(t, () => row.classList.add('drag')));
    ['dragleave', 'drop'].forEach((t) => row.addEventListener(t, () => row.classList.remove('drag')));
  });
})();


// Phone: the menu button slides the side menu in; the dark area, a link or Escape closes it.
(() => {
  const btn = document.querySelector('[data-menu]'); if (!btn) return;
  const set = (open) => { document.body.classList.toggle('menu-open', open); btn.setAttribute('aria-expanded', String(open)); };
  btn.addEventListener('click', () => set(!document.body.classList.contains('menu-open')));
  document.querySelectorAll('[data-menu-close], .sidebar a').forEach((el) => el.addEventListener('click', () => set(false)));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') set(false); });
})();

// The Track dropdown in the results row closes when anything else is tapped.
document.addEventListener('click', e => { document.querySelectorAll('.rn-more[open]').forEach(d => { if (!d.contains(e.target)) d.removeAttribute('open'); }); });

// Back to the top: a round arrow that appears once the page has been scrolled down.
(() => {
  const btn = document.querySelector('[data-to-top]');
  if (!btn) return;
  const sync = () => { btn.hidden = window.scrollY < 400; };
  window.addEventListener('scroll', sync, { passive: true });
  btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
  sync();
})();

// High Risk, Over time: tapping a bar opens that day's exact messages.
document.addEventListener('click', e => {
  const g = e.target.closest && e.target.closest('.hr-bar'); if (!g) return;
  const box = document.getElementById('hr-days'), day = document.getElementById('hr-day-' + g.dataset.day);
  if (box) box.open = true; if (day) { day.open = true; day.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
});
