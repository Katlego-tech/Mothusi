// Mockup only. Replays the log so the motion in console.css can be seen in a browser: open a run
// screen with ?replay. The live console gets the same motion from the SSE stream (one row per event) and does
// not ship this file.
(() => {
  if (!new URLSearchParams(location.search).has('replay')) return;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const rows = [...document.querySelectorAll('.log .ev, .log .ask')];
  const log = document.querySelector('.log');
  const keep = rows.length;
  rows.forEach(r => r.remove());
  let i = 0;
  const step = () => {
    if (i >= keep) return;
    const r = rows[i++];
    r.classList.add('arrive');
    log.appendChild(r);
    r.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    setTimeout(step, r.classList.contains('ask') ? 400 : 900 + Math.random() * 700);
  };
  setTimeout(step, 600);
})();
