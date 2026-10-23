// Page motion: staggered entrances, numbers that count up, notices that dismiss themselves.
// The animations themselves live in style.css; this only hands out order and values.
document.addEventListener('DOMContentLoaded', () => {
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Each group's children enter one after another (CSS reads --i).
  for (const group of document.querySelectorAll('main, .stats, .grid, tbody, nav, .alerts, .tabs, .form-grid')) {
    [...group.children].forEach((el, i) => el.style.setProperty('--i', Math.min(i, 16)));
  }

  // Numbers count up from zero, keeping their ₹ sign, commas, decimals and words.
  if (!still) for (const el of document.querySelectorAll('[data-count]')) {
    const m = el.textContent.match(/[\d,]+(?:\.\d+)?/);
    if (!m) continue;
    const target = parseFloat(m[0].replace(/,/g, '')), dec = (m[0].split('.')[1] || '').length;
    const [pre, post] = [el.textContent.slice(0, m.index), el.textContent.slice(m.index + m[0].length)];
    const fmt = n => pre + n.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }) + post;
    const t0 = performance.now(), ms = 900;
    const step = now => {
      const p = Math.min(1, (now - t0) / ms), eased = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt(target * eased);
      if (p < 1) requestAnimationFrame(step);
    };
    el.textContent = fmt(0);
    requestAnimationFrame(step);
  }

  // Success notices fade away after a few seconds; errors stay until read.
  for (const f of document.querySelectorAll('.flash.success')) {
    setTimeout(() => { f.classList.add('out'); f.addEventListener('transitionend', () => f.remove(), { once: true }); }, 4500);
  }

  // Buttons give a little ripple where they are pressed.
  if (!still) document.addEventListener('pointerdown', e => {
    const b = e.target.closest('.btn');
    if (!b) return;
    const r = b.getBoundingClientRect(), dot = document.createElement('i');
    dot.className = 'ripple';
    dot.style.left = `${e.clientX - r.left}px`; dot.style.top = `${e.clientY - r.top}px`;
    b.append(dot);
    dot.addEventListener('animationend', () => dot.remove());
  });
});
