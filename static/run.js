// The progress window: opens while a task runs, follows it every 1.5 seconds and says what it is doing.
(() => {
  const dialog = document.getElementById('run-dialog');
  if (!dialog) return;
  const bar = dialog.querySelector('.run-bar'), fill = bar.querySelector('span'), pct = document.getElementById('run-percent'), steps = document.getElementById('run-steps'),
        detail = document.getElementById('run-detail'), title = document.getElementById('run-title'), elapsed = document.getElementById('run-elapsed'),
        cancel = document.getElementById('run-cancel'), openBtn = document.getElementById('run-open');
  const started = Date.now();
  const open = () => { if (dialog.showModal && !dialog.open) dialog.showModal(); };
  dialog.querySelectorAll('[data-run-hide]').forEach((b) => b.addEventListener('click', () => dialog.close()));
  document.querySelectorAll('[data-run-show]').forEach((b) => b.addEventListener('click', open));
  if (dialog.dataset.open === 'yes') open();
  const clock = () => { const s = Math.floor((Date.now() - started) / 1000); elapsed.textContent = 'for ' + (s >= 60 ? Math.floor(s / 60) + ' min ' + (s % 60) + ' s' : s + ' s'); };
  clock(); const ticker = setInterval(clock, 1000);
  const paint = (p) => {
    if (!p || !p.stages) return;
    fill.style.width = p.percent + '%'; pct.textContent = p.percent + '%'; bar.setAttribute('aria-valuenow', p.percent);
    steps.innerHTML = '';
    p.stages.forEach((s) => { const li = document.createElement('li'); li.className = s.state; li.dataset.key = s.key; li.append(document.createElement('i'), Object.assign(document.createElement('span'), { textContent: s.label })); steps.append(li); });
    detail.textContent = p.detail;
  };
  const poll = async () => {
    let job = null;
    try { const r = await fetch(dialog.dataset.jobUrl, { headers: { Accept: 'application/json' } }); if (r.ok && !r.redirected) job = await r.json(); } catch (e) { detail.textContent = 'Connection interrupted. Reconnecting…'; }
    if (job && job.progress) {
      paint(job.progress);
      if (!['queued', 'running'].includes(job.status)) {
        clearInterval(ticker); cancel.hidden = true; open();
        title.textContent = job.status === 'complete' ? 'Done' : job.status === 'cancelled' ? 'Stopped' : 'This did not finish';
        dialog.classList.add(job.status === 'complete' ? 'ok' : 'bad');
        openBtn.hidden = false; openBtn.textContent = job.status === 'complete' ? 'Open the results' : 'Close';
        openBtn.onclick = () => location.reload();
        if (job.status === 'complete') setTimeout(() => location.reload(), 1800);
        return;
      }
    }
    setTimeout(poll, 1500);
  };
  setTimeout(poll, 1500);
})();
