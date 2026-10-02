// Wraps an already-rendered deck (the React components, serialized with
// renderToStaticMarkup) into a fully self-contained HTML file: inlined CSS,
// inlined navigation script, embedded deck JSON. No CDN, no fonts, no network.
//
// This module is deliberately JSX-free so it can be unit tested in Node.
import { deckCss } from './deckCss.js'

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function embedJson(value) {
  return JSON.stringify(value)
    .replace(/</g, '\\u003c')
    .replace(/\u2028|\u2029/g, (char) => (char === '\u2028' ? '\\u2028' : '\\u2029'))
}

/**
 * @param {{bodyHtml: string, title?: string, slideCount?: number, generatedAt?: string,
 *   model?: string, showCitations?: boolean, designStyleName?: string, data?: object}} input
 */
export function buildStandaloneHtml({
  bodyHtml,
  title = 'AI 生成演示文稿',
  slideCount = 0,
  generatedAt = '',
  model = '',
  showCitations = false,
  designStyleName = '',
  data = null,
} = {}) {
  const safeTitle = escapeHtml(title)
  const meta = [designStyleName, generatedAt, model].filter(Boolean).join(' · ')

  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<meta name="generator" content="ai-web-ppt" />
<title>${safeTitle}</title>
<style>
${deckCss()}
html, body { margin: 0; height: 100%; }
</style>
</head>
<body>
<div class="deck-root">
  <div class="deck-toolbar">
    <span class="deck-toolbar-title">${safeTitle}</span>
${designStyleName ? `    <span class="deck-style-chip">🎨 ${escapeHtml(designStyleName)}</span>\n` : ''}    <span class="deck-spacer"></span>
${showCitations ? '    <span class="deck-style-chip">来源标注：开</span>\n' : ''}    <button class="deck-btn" type="button" id="fullBtn" title="全屏演示 (F)">⛶ 全屏</button>
  </div>

  <div class="deck-stage">
    <button class="deck-nav prev" type="button" id="prevBtn" aria-label="上一页">‹</button>
    <div class="slide-frame" id="deckFrame">
${bodyHtml || '<div class="deck-empty">没有可演示的页面</div>'}
    </div>
    <button class="deck-nav next" type="button" id="nextBtn" aria-label="下一页">›</button>
  </div>

  <div class="deck-dots" id="dots"></div>
  <div class="deck-hint"><span id="counter">1 / ${slideCount}</span> · ← → 翻页 · F 全屏${meta ? ` · ${escapeHtml(meta)}` : ''}</div>
</div>

<script type="application/json" id="deck-data">${embedJson(data ?? { title, slideCount })}</script>
<script>
(function () {
  var frame = document.getElementById('deckFrame');
  var slides = Array.prototype.slice.call(frame.querySelectorAll('.slide'));
  var counter = document.getElementById('counter');
  var dotsHost = document.getElementById('dots');
  var total = slides.length;
  var index = 0;

  for (var d = 0; d < total; d++) {
    (function (target) {
      var dot = document.createElement('button');
      dot.type = 'button';
      dot.className = 'deck-dot';
      dot.setAttribute('aria-label', '第 ' + (target + 1) + ' 页');
      dot.addEventListener('click', function () { go(target); });
      dotsHost.appendChild(dot);
    })(d);
  }
  var dots = Array.prototype.slice.call(dotsHost.children);

  function render() {
    for (var i = 0; i < total; i++) {
      var on = i === index;
      slides[i].classList.toggle('is-active', on);
      if (on) { slides[i].removeAttribute('aria-hidden'); } else { slides[i].setAttribute('aria-hidden', 'true'); }
    }
    for (var j = 0; j < dots.length; j++) dots[j].classList.toggle('is-active', j === index);
    if (counter) counter.textContent = (total ? index + 1 : 0) + ' / ' + total;
    try { history.replaceState(null, '', '#' + (index + 1)); } catch (e) { /* ignore */ }
  }

  function go(n) { if (!total) return; index = Math.max(0, Math.min(total - 1, n)); render(); }
  function next() { go(index + 1); }
  function prev() { go(index - 1); }

  function toggleFullscreen() {
    var el = document.documentElement;
    if (!document.fullscreenElement) {
      if (el.requestFullscreen) el.requestFullscreen().catch(function () {});
      else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
    } else if (document.exitFullscreen) {
      document.exitFullscreen().catch(function () {});
    }
  }

  document.addEventListener('keydown', function (event) {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    var key = event.key;
    if (key === 'ArrowRight' || key === 'ArrowDown' || key === ' ' || key === 'PageDown' || key === 'Enter') {
      event.preventDefault(); next();
    } else if (key === 'ArrowLeft' || key === 'ArrowUp' || key === 'PageUp') {
      event.preventDefault(); prev();
    } else if (key === 'Home') { event.preventDefault(); go(0); }
    else if (key === 'End') { event.preventDefault(); go(total - 1); }
    else if (key === 'f' || key === 'F') { toggleFullscreen(); }
  });

  var prevBtn = document.getElementById('prevBtn');
  var nextBtn = document.getElementById('nextBtn');
  var fullBtn = document.getElementById('fullBtn');
  if (prevBtn) prevBtn.addEventListener('click', prev);
  if (nextBtn) nextBtn.addEventListener('click', next);
  if (fullBtn) fullBtn.addEventListener('click', toggleFullscreen);

  if (frame) {
    frame.addEventListener('click', function (event) {
      if (event.target.closest('button, a')) return;
      var rect = frame.getBoundingClientRect();
      var ratio = rect.width ? (event.clientX - rect.left) / rect.width : 0.5;
      if (ratio < 0.3) prev(); else if (ratio > 0.7) next();
    });
  }

  var touchX = null, touchY = null;
  document.addEventListener('touchstart', function (event) {
    if (event.touches.length !== 1) return;
    touchX = event.touches[0].clientX; touchY = event.touches[0].clientY;
  }, { passive: true });
  document.addEventListener('touchend', function (event) {
    if (touchX === null) return;
    var touch = event.changedTouches[0];
    var dx = touch.clientX - touchX;
    var dy = touch.clientY - touchY;
    touchX = null; touchY = null;
    if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy)) { if (dx < 0) next(); else prev(); }
  }, { passive: true });

  var hash = /#(\\d+)/.exec(window.location.hash);
  if (hash) index = Math.min(total - 1, Math.max(0, parseInt(hash[1], 10) - 1));
  render();
})();
</script>
</body>
</html>
`
}

export { escapeHtml, embedJson }
