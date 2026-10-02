// Deck stylesheet, returned as a string so the live app (injected into <head>)
// and the exported HTML use byte-identical CSS.
//
// Everything visual is driven by the design tokens the model produced
// (--s-bg, --s-accent, --s-heading-font, --s-radius, …). This file contributes
// the *craft*: a spacing scale, a type scale with contrast, three explicit page
// zones (head / body / foot), page texture, hairlines and elevation.
//
// Two fixed readability floors live here and nowhere else:
//   * body text is never smaller than 18px
//   * every edge keeps at least 10% of the slide height as margin
const DECK_CSS = String.raw`
*, *::before, *::after { box-sizing: border-box; }

html, body { height: 100%; }

body {
  margin: 0;
  background: #0b0f18;
  color: #eef2ff;
  font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

/* ---------- presentation shell ---------- */

.deck-root {
  height: 100vh;
  height: 100dvh;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: var(--ui-bg, #0b0f18);
  color: var(--ui-fg, #eef2ff);
}

.deck-stage {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  place-items: center;
  padding: clamp(8px, 1.8vh, 26px) clamp(8px, 1.6vw, 26px);
  overflow: hidden;
}

.slide-frame {
  position: relative;
  width: min(100%, 1400px);
  aspect-ratio: 16 / 9;
  max-height: 100%;
  container-type: size;
  border-radius: clamp(10px, 1.4vw, 20px);
  border: 1px solid var(--ui-border, rgba(255,255,255,0.14));
  background: var(--s-bg, #0f1420);
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
  overflow: hidden;
}

/* ---------- design system ---------- */

.deck-surface {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(120% 110% at 8% 0%, var(--s-bg-alt, #1b2436) 0%, var(--s-bg, #0f1420) 62%),
    var(--s-bg, #0f1420);
  color: var(--s-fg, #f2f5fb);
  font-family: var(--s-body-font, system-ui);

  /* spacing scale — every gap in a page comes from here */
  --sp-1: max(6px, 0.8cqh);
  --sp-2: max(10px, 1.25cqh);
  --sp-3: max(15px, 1.9cqh);
  --sp-4: max(22px, 2.7cqh);
  --sp-5: max(30px, 3.7cqh);

  /* radii + elevation + hairlines */
  --r-sm: calc(var(--s-radius, 14px) * 0.5);
  --r-md: var(--s-radius, 14px);
  --r-lg: calc(var(--s-radius, 14px) * 1.35);
  --hair: 1px solid var(--s-border);
  --elev-1: 0 1px 2px color-mix(in srgb, var(--s-bg) 45%, transparent), 0 8px 24px color-mix(in srgb, var(--s-bg) 30%, transparent);

  /* type scale — body sizes carry an 18px floor */
  --f-body: max(18px, calc(1.3cqw * var(--s-scale, 1)));
  --f-body-lg: max(19px, calc(1.55cqw * var(--s-scale, 1)));
  --f-lead: max(18px, calc(1.85cqw * var(--s-scale, 1)));
  --f-stat: max(32px, calc(3.5cqw * var(--s-scale, 1)));
  --f-title: max(26px, calc(2.85cqw * var(--s-scale, 1)));
  --f-hero: max(36px, calc(4.7cqw * var(--s-scale, 1)));
  --f-label: max(12px, calc(0.88cqw * var(--s-scale, 1)));
  --f-meta: max(13px, calc(0.95cqw * var(--s-scale, 1)));

  --s-pad-block: 10cqh;
  --s-pad-inline: min(10cqh, 10cqw);
}

/* ---------- page ---------- */

.slide {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  padding: var(--s-pad-block) var(--s-pad-inline);
  opacity: 0;
  visibility: hidden;
  transform: translateY(6px);
  transition: opacity 0.28s ease, transform 0.28s ease, visibility 0.28s;
  overflow: auto;
  scrollbar-width: thin;
}

.slide.is-active { opacity: 1; visibility: visible; transform: none; }
.slide::-webkit-scrollbar { width: 8px; }
.slide::-webkit-scrollbar-thumb { background: var(--s-border); border-radius: 8px; }

/* very subtle texture so pages never look flat */
.slide-texture {
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0.55;
  z-index: 0;
}
.deck-surface[data-pattern="grid"] .slide-texture {
  background-image:
    linear-gradient(color-mix(in srgb, var(--s-fg) 4%, transparent) 1px, transparent 1px),
    linear-gradient(90deg, color-mix(in srgb, var(--s-fg) 4%, transparent) 1px, transparent 1px);
  background-size: 44px 44px;
}
.deck-surface[data-pattern="dots"] .slide-texture {
  background-image: radial-gradient(color-mix(in srgb, var(--s-fg) 8%, transparent) 1.2px, transparent 1.3px);
  background-size: 26px 26px;
}
.deck-surface[data-pattern="waves"] .slide-texture {
  background-image: repeating-radial-gradient(circle at 110% 120%, transparent 0 60px, color-mix(in srgb, var(--s-accent) 8%, transparent) 60px 61px);
}
.deck-surface[data-pattern="plain"] .slide-texture,
.deck-surface[data-pattern="none"] .slide-texture { display: none; }

/* ---------- three zones: head · body · foot ---------- */

.slide-inner {
  position: relative;
  z-index: 1;
  height: 100%;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: var(--sp-3);
  margin: 0;
  min-height: 0;
}

.slide-head { display: flex; flex-direction: column; gap: var(--sp-1); min-width: 0; }

.slide-body {
  min-height: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: var(--sp-3);
}
.slide-body--full { justify-content: center; }

.slide-foot { display: flex; flex-direction: column; gap: var(--sp-2); }
.slide-foot-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  border-top: var(--hair);
  padding-top: var(--sp-1);
  min-height: 1.6em;
}
.slide-foot-spacer { flex: 1 1 auto; }
.slide-page {
  margin-left: auto;
  font-size: var(--f-label);
  letter-spacing: 0.14em;
  color: var(--s-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.slide-page-total { opacity: 0.55; }

/* split composition: text column + media column */
.slide-split {
  display: grid;
  grid-template-columns: minmax(0, 1.12fr) minmax(0, 0.88fr);
  gap: calc(var(--sp-4) * 1.2);
  align-items: center;
  min-height: 0;
}
.slide-main { min-width: 0; display: flex; flex-direction: column; gap: var(--sp-3); justify-content: center; }

figure.slide-media {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  height: 100%;
  min-width: 0;
}
.slide-image {
  width: 100%;
  border-radius: var(--r-md);
  border: var(--hair);
  object-fit: cover;
  box-shadow: var(--elev-1);
}
.slide-image--side { height: 100%; min-height: min(44cqh, 300px); }
.slide-image--background { height: 100%; border: 0; border-radius: 0; box-shadow: none; }
.slide-media-caption {
  font-size: var(--f-meta);
  color: var(--s-muted);
  line-height: 1.4;
  text-align: center;
}

.slide-bg { position: absolute; inset: 0; z-index: 0; }
.slide-scrim {
  position: absolute;
  inset: 0;
  background: linear-gradient(96deg, var(--s-bg) 4%, color-mix(in srgb, var(--s-bg) 78%, transparent) 48%, color-mix(in srgb, var(--s-bg) 35%, transparent) 100%);
}
.slide--cover .slide-scrim {
  background: linear-gradient(180deg, color-mix(in srgb, var(--s-bg) 45%, transparent), color-mix(in srgb, var(--s-bg) 88%, transparent));
}

/* CSS-gradient decoration — the silent fallback when no picture is available.
   Deliberately visible: it must read as a designed accent panel, not a missing
   image. */
.decor {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: min(40cqh, 280px);
  border-radius: var(--r-md);
  border: var(--hair);
  overflow: hidden;
  background:
    radial-gradient(70% 110% at 16% 8%, color-mix(in srgb, var(--s-accent) 55%, transparent), transparent 68%),
    radial-gradient(80% 100% at 86% 92%, color-mix(in srgb, var(--s-accent2) 48%, transparent), transparent 70%),
    linear-gradient(145deg, color-mix(in srgb, var(--s-accent) 18%, var(--s-surface)), transparent 62%),
    var(--s-surface);
  box-shadow: var(--elev-1);
}
.decor::after {
  content: "";
  position: absolute;
  inset: auto -10% -35% auto;
  width: 62%;
  aspect-ratio: 1;
  border-radius: 50%;
  background: radial-gradient(circle, color-mix(in srgb, var(--s-accent2) 30%, transparent), transparent 70%);
}
.decor--grid {
  background-image:
    linear-gradient(color-mix(in srgb, var(--s-fg) 10%, transparent) 1px, transparent 1px),
    linear-gradient(90deg, color-mix(in srgb, var(--s-fg) 10%, transparent) 1px, transparent 1px),
    radial-gradient(70% 110% at 16% 8%, color-mix(in srgb, var(--s-accent) 52%, transparent), transparent 68%),
    linear-gradient(145deg, color-mix(in srgb, var(--s-accent) 16%, var(--s-surface)), transparent 62%);
  background-size: 30px 30px, 30px 30px, auto, auto;
}
.decor--dots {
  background-image:
    radial-gradient(color-mix(in srgb, var(--s-fg) 16%, transparent) 1.6px, transparent 1.7px),
    radial-gradient(70% 110% at 16% 8%, color-mix(in srgb, var(--s-accent) 50%, transparent), transparent 68%),
    linear-gradient(145deg, color-mix(in srgb, var(--s-accent) 16%, var(--s-surface)), transparent 62%);
  background-size: 24px 24px, auto, auto;
}
.decor--waves {
  background-image:
    repeating-radial-gradient(circle at 10% 94%, transparent 0 22px, color-mix(in srgb, var(--s-accent) 30%, transparent) 22px 23px),
    radial-gradient(70% 110% at 16% 8%, color-mix(in srgb, var(--s-accent) 45%, transparent), transparent 68%),
    linear-gradient(145deg, color-mix(in srgb, var(--s-accent) 14%, var(--s-surface)), transparent 62%);
}
.decor--plain {
  background-image:
    radial-gradient(70% 110% at 16% 8%, color-mix(in srgb, var(--s-accent) 48%, transparent), transparent 68%),
    linear-gradient(145deg, color-mix(in srgb, var(--s-accent) 20%, var(--s-surface)), transparent 62%);
}

/* ---------- typography ---------- */

.slide-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 0.6em;
  font-size: var(--f-label);
  letter-spacing: 0.2em;
  font-weight: 700;
  color: var(--s-accent);
  text-transform: uppercase;
}
.slide-eyebrow::before {
  content: "";
  width: 1.8em;
  height: 2px;
  background: currentColor;
  opacity: 0.75;
  flex: none;
}

.slide-title {
  margin: 0;
  font-family: var(--s-heading-font, var(--s-body-font, system-ui));
  font-size: var(--f-title);
  font-weight: var(--s-heading-weight, 700);
  letter-spacing: var(--s-letter-spacing, 0);
  line-height: 1.16;
  text-wrap: balance;
  max-width: 30ch;
}
.slide-title--hero { font-size: var(--f-hero); line-height: 1.08; max-width: 22ch; }
.slide-title--section { font-size: max(30px, calc(3.7cqw * var(--s-scale, 1))); }

.slide-subtitle {
  margin: 0;
  font-size: var(--f-lead);
  color: var(--s-muted);
  line-height: 1.5;
  max-width: 58ch;
}

.cite-mark {
  font-size: 0.66em;
  vertical-align: super;
  color: var(--s-accent);
  font-weight: 700;
  letter-spacing: 0.02em;
}

/* ---------- bullets ---------- */

.slide-bullets {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.slide-bullets li {
  display: flex;
  gap: 0.75em;
  align-items: flex-start;
  font-size: var(--f-body);
  line-height: 1.6;
}
.slide-bullets--tight { gap: var(--sp-1); }
.slide-bullets--tight li { font-size: var(--f-body); line-height: 1.55; }
.slide-bullets--hero { margin-top: var(--sp-2); }
.slide-bullets--hero li { font-size: var(--f-lead); }

.slide-bullet-mark {
  flex: none;
  width: 0.4em;
  height: 0.4em;
  margin-top: 0.72em;
  border-radius: 1px;
  background: var(--s-accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--s-accent) 16%, transparent);
}

/* numbered list */
.numbered-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-2); }
.numbered-list li { display: flex; gap: 0.8em; align-items: baseline; font-size: var(--f-body); line-height: 1.55; }
.numbered-index {
  flex: none;
  font-family: var(--s-heading-font, var(--s-body-font, system-ui));
  font-size: var(--f-body-lg);
  font-weight: 800;
  color: var(--s-accent);
  font-variant-numeric: tabular-nums;
}

/* ---------- covers / sections / quotes / closing ---------- */

.layout { display: flex; flex-direction: column; gap: var(--sp-3); min-width: 0; }

.layout--cover { justify-content: center; height: 100%; gap: var(--sp-4); }
.cover-lead { display: flex; flex-direction: column; gap: var(--sp-2); }
.cover-rule {
  width: clamp(56px, 9cqw, 150px);
  height: 6px;
  border-radius: 4px;
  background: linear-gradient(90deg, var(--s-accent), var(--s-accent2));
}
.slide-subtitle--hero { font-size: max(19px, calc(1.9cqw * var(--s-scale, 1))); max-width: 46ch; }
.layout--cover-image { justify-content: flex-end; }
.layout--cover-image .slide-title { text-shadow: 0 2px 30px color-mix(in srgb, var(--s-bg) 80%, transparent); }

.layout--section {
  position: relative;
  flex-direction: row;
  align-items: center;
  gap: calc(var(--sp-4) * 1.2);
  height: 100%;
}
.section-body { display: flex; flex-direction: column; gap: var(--sp-2); min-width: 0; }
.section-number {
  font-family: var(--s-heading-font, var(--s-body-font, system-ui));
  font-size: max(52px, calc(7.2cqw * var(--s-scale, 1)));
  font-weight: var(--s-heading-weight, 700);
  line-height: 0.86;
  color: color-mix(in srgb, var(--s-accent) 60%, transparent);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}
.section-bar {
  width: clamp(56px, 10cqw, 180px);
  height: 3px;
  border-radius: 3px;
  background: linear-gradient(90deg, var(--s-accent), transparent);
}

.layout--quote { justify-content: center; height: 100%; gap: var(--sp-2); }
.quote-mark {
  font-family: Georgia, serif;
  font-size: max(46px, 5.4cqw);
  line-height: 0.7;
  color: color-mix(in srgb, var(--s-accent) 75%, transparent);
}
.quote-text {
  margin: 0;
  font-size: max(21px, calc(2.15cqw * var(--s-scale, 1)));
  line-height: 1.45;
  font-style: italic;
  color: var(--s-fg);
  max-width: 34ch;
}
.quote-attribution {
  display: flex;
  align-items: center;
  gap: 0.7em;
  font-size: var(--f-body);
  color: var(--s-muted);
}
.quote-rule { width: 2.2em; height: 2px; background: var(--s-border); flex: none; }

.layout--closing { height: 100%; justify-content: center; }
.closing-panel {
  background: var(--s-surface);
  border: var(--hair);
  border-left: 4px solid var(--s-accent);
  border-radius: var(--r-md);
  padding: calc(var(--sp-3) * 1.1);
  box-shadow: var(--elev-1);
}

/* ---------- cards ---------- */

.card-grid {
  display: grid;
  gap: var(--sp-3);
  grid-template-columns: repeat(auto-fit, minmax(min(230px, 100%), 1fr));
  align-content: center;
}
.card-grid--2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.card-grid--3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.card-grid--4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }

.info-card {
  position: relative;
  overflow: hidden;
  background: var(--s-surface);
  border: var(--hair);
  border-radius: var(--r-md);
  padding: calc(var(--sp-3) * 1.05);
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  min-width: 0;
  box-shadow: var(--elev-1);
}
.info-card::before {
  content: "";
  position: absolute;
  inset: 0 0 auto 0;
  height: 3px;
  background: linear-gradient(90deg, var(--s-accent), color-mix(in srgb, var(--s-accent2) 70%, transparent));
  opacity: 0.9;
}
.info-card-index {
  font-family: var(--s-heading-font, var(--s-body-font, system-ui));
  font-size: var(--f-label);
  letter-spacing: 0.16em;
  color: var(--s-accent);
  font-weight: 800;
}
.info-card-title { margin: 0; font-size: var(--f-body-lg); font-weight: 650; color: var(--s-fg); line-height: 1.35; }
.info-card-text { margin: 0; font-size: var(--f-body); color: var(--s-muted); line-height: 1.55; }

/* ---------- data ---------- */

.kpi-strip {
  display: grid;
  gap: var(--sp-3);
  grid-template-columns: repeat(auto-fit, minmax(min(170px, 100%), 1fr));
  align-content: center;
}
.kpi {
  display: flex;
  flex-direction: column;
  gap: 0.25em;
  min-width: 0;
  padding-inline: calc(var(--sp-3) * 0.7);
  border-left: 2px solid color-mix(in srgb, var(--s-accent) 55%, transparent);
}
.kpi:first-child { padding-left: 0; border-left: 0; }
.kpi-value { display: flex; align-items: baseline; gap: 0.12em; color: var(--s-accent); }
.kpi-number {
  font-family: var(--s-heading-font, var(--s-body-font, system-ui));
  font-size: var(--f-stat);
  font-weight: var(--s-heading-weight, 700);
  line-height: 1;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.kpi-unit { font-size: var(--f-body); color: color-mix(in srgb, var(--s-accent) 70%, var(--s-fg)); }
.kpi-label { font-size: var(--f-body); color: var(--s-fg); line-height: 1.4; }
.kpi-delta {
  align-self: flex-start;
  font-size: var(--f-meta);
  color: var(--s-accent2);
  background: color-mix(in srgb, var(--s-accent2) 14%, transparent);
  border-radius: 999px;
  padding: 1px 8px;
}

.stat-grid {
  display: grid;
  gap: var(--sp-3);
  grid-template-columns: repeat(auto-fit, minmax(min(210px, 100%), 1fr));
  align-content: center;
}
.stat-grid--2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.stat-grid--3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.stat-grid--4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.stat-card {
  background: var(--s-surface);
  border: var(--hair);
  border-radius: var(--r-md);
  padding: calc(var(--sp-3) * 1.05);
  display: flex;
  flex-direction: column;
  gap: 0.3em;
  min-width: 0;
  box-shadow: var(--elev-1);
}
.stat-value { display: flex; align-items: baseline; gap: 0.12em; color: var(--s-accent); }
.stat-number {
  font-family: var(--s-heading-font, var(--s-body-font, system-ui));
  font-size: var(--f-stat);
  font-weight: var(--s-heading-weight, 700);
  line-height: 1;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.stat-unit { font-size: var(--f-body); color: color-mix(in srgb, var(--s-accent) 70%, var(--s-fg)); }
.stat-label { font-size: var(--f-body); color: var(--s-fg); line-height: 1.4; }
.stat-delta {
  align-self: flex-start;
  font-size: var(--f-meta);
  padding: 1px 8px;
  border-radius: 999px;
  border: var(--hair);
  color: var(--s-accent2);
}

/* ---------- charts ---------- */

.chart-stack, .table-stack { display: flex; flex-direction: column; gap: var(--sp-3); min-height: 0; }
.chart-frame {
  margin: 0;
  background: var(--s-surface);
  border: var(--hair);
  border-radius: var(--r-md);
  padding: calc(var(--sp-3) * 0.9);
  min-width: 0;
  box-shadow: var(--elev-1);
}
.chart { margin: 0; display: flex; flex-direction: column; gap: 0.5em; min-width: 0; }
.chart-title { font-size: var(--f-body); color: var(--s-fg); font-weight: 650; }
.chart-body { min-width: 0; }
.chart-svg { width: 100%; height: auto; max-height: min(46cqh, 380px); display: block; }
.chart-legend {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.4em 1.1em;
  font-size: var(--f-meta);
  color: var(--s-muted);
}
.chart-legend li { display: inline-flex; align-items: center; gap: 0.45em; }
.chart-swatch { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.chart-empty { display: flex; align-items: center; gap: 0.5em; color: var(--s-muted); font-size: var(--f-body); padding: var(--sp-3); }

.chart-split-grid,
.table-split-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(0, 0.7fr);
  gap: var(--sp-4);
  align-items: center;
  min-width: 0;
}
.chart-side { min-width: 0; }

/* ---------- compare ---------- */

.compare-grid {
  position: relative;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-4);
  align-content: center;
}
.compare-grid::after {
  content: "";
  position: absolute;
  left: 50%;
  top: 6%;
  bottom: 6%;
  width: 1px;
  background: var(--s-border);
}
.compare-col {
  border: var(--hair);
  border-radius: var(--r-md);
  padding: calc(var(--sp-3) * 1.05);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  min-width: 0;
  box-shadow: var(--elev-1);
}
.compare-col--a { background: color-mix(in srgb, var(--s-accent) 10%, var(--s-bg)); }
.compare-col--b { background: color-mix(in srgb, var(--s-accent2) 10%, var(--s-bg)); }
.compare-title { margin: 0; font-size: var(--f-body-lg); font-weight: 700; }
.compare-col--a .compare-title { color: var(--s-accent); }
.compare-col--b .compare-title { color: var(--s-accent2); }
.compare-grid--table::after { display: none; }
.compare-grid--table .compare-col { box-shadow: none; border-radius: 0; border-width: 0 0 1px 0; background: transparent; padding-inline: 0; }
.compare-grid--table .compare-col:first-child { border-right: var(--hair); padding-right: var(--sp-3); }
.compare-grid--table .compare-col:last-child { padding-left: var(--sp-3); }

/* ---------- timeline ---------- */

.timeline { list-style: none; margin: 0; padding: 0; display: flex; gap: var(--sp-3); }
.timeline--horizontal { flex-direction: row; align-items: stretch; }
.timeline--horizontal .timeline-item { flex: 1 1 0; flex-direction: column; }
.timeline--vertical { flex-direction: column; gap: var(--sp-2); }
.timeline-item { display: flex; gap: var(--sp-2); min-width: 0; }
.timeline--vertical .timeline-item { align-items: flex-start; }
.timeline-node {
  flex: none;
  display: grid;
  place-items: center;
  min-width: 2.2em;
  height: 2.2em;
  padding: 0 0.5em;
  border-radius: 999px;
  background: color-mix(in srgb, var(--s-accent) 18%, transparent);
  border: 1px solid color-mix(in srgb, var(--s-accent) 65%, transparent);
  color: var(--s-accent);
  font-size: var(--f-meta);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.timeline-body { display: flex; flex-direction: column; gap: 0.2em; min-width: 0; }
.timeline-title { font-size: var(--f-body); font-weight: 650; }
.timeline-text { font-size: var(--f-body); color: var(--s-muted); line-height: 1.5; }
.timeline--horizontal .timeline-item + .timeline-item { border-left: 1px dashed var(--s-border); padding-left: var(--sp-3); }
.timeline--vertical .timeline-item + .timeline-item { border-top: 1px dashed var(--s-border); padding-top: var(--sp-2); }

/* ---------- table ---------- */

.table-wrap { overflow: auto; border: var(--hair); border-radius: var(--r-md); box-shadow: var(--elev-1); }
.data-table { border-collapse: collapse; width: 100%; font-size: var(--f-body); }
.data-table th,
.data-table td {
  text-align: left;
  padding: 0.55em 0.85em;
  border-bottom: var(--hair);
  line-height: 1.45;
}
.data-table th {
  background: color-mix(in srgb, var(--s-accent) 12%, var(--s-surface));
  color: var(--s-fg);
  font-weight: 700;
  font-size: var(--f-meta);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.data-table td.is-key { color: var(--s-fg); font-weight: 600; }
.data-table tbody tr:last-child td { border-bottom: none; }

/* ---------- references ---------- */

.layout--references { height: 100%; align-content: center; }
.reference-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-2); }
.reference-item { display: flex; gap: 0.7em; font-size: var(--f-body); line-height: 1.45; padding-bottom: var(--sp-1); border-bottom: var(--hair); }
.reference-item:last-child { border-bottom: none; }
.reference-number { color: var(--s-accent); font-weight: 700; flex: none; font-variant-numeric: tabular-nums; }
.reference-body { display: flex; flex-direction: column; gap: 0.1em; min-width: 0; }
.reference-title { color: var(--s-fg); }
.reference-url { color: var(--s-muted); font-size: var(--f-meta); text-decoration: none; word-break: break-all; }
.reference-url:hover { color: var(--s-accent); text-decoration: underline; }

/* ---------- conclusion + citations ---------- */

.takeaway-bar {
  margin: 0;
  display: flex;
  gap: 0.6em;
  align-items: flex-start;
  font-size: var(--f-body);
  font-weight: 650;
  line-height: 1.5;
  color: var(--s-fg);
  background: color-mix(in srgb, var(--s-accent) 11%, transparent);
  border-left: 4px solid var(--s-accent);
  border-radius: var(--r-sm);
  padding: 0.6em 0.9em;
}
.takeaway-mark { color: var(--s-accent); flex: none; }

.slide-citations {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3em 1.1em;
  font-size: var(--f-meta);
  color: var(--s-muted);
  min-width: 0;
}
.slide-citation { display: inline-flex; gap: 0.25em; align-items: baseline; }

/* ---------- shell chrome ---------- */

.deck-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: clamp(8px, 1.4vh, 16px) clamp(10px, 2vw, 24px);
  border-bottom: 1px solid var(--ui-border, rgba(255,255,255,0.14));
  background: color-mix(in srgb, var(--ui-bg, #0b0f18) 88%, transparent);
  backdrop-filter: blur(8px);
}
.deck-toolbar .deck-spacer { flex: 1 1 auto; }
.deck-toolbar-title {
  font-size: clamp(12px, 1.2vw, 15px);
  font-weight: 650;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 30vw;
}
.deck-style-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: clamp(11px, 1.05vw, 13px);
  padding: 5px 10px;
  border-radius: 999px;
  border: 1px solid var(--ui-border, rgba(255,255,255,0.14));
  background: var(--ui-panel, rgba(255,255,255,0.06));
  color: var(--ui-accent, #7c9cff);
  white-space: nowrap;
  max-width: 26vw;
  overflow: hidden;
  text-overflow: ellipsis;
}
.deck-btn {
  appearance: none;
  border: 1px solid var(--ui-border, rgba(255,255,255,0.14));
  background: var(--ui-panel, rgba(255,255,255,0.06));
  color: var(--ui-fg, #eef2ff);
  border-radius: 9px;
  padding: 7px 12px;
  font-size: clamp(12px, 1.1vw, 14px);
  font-family: inherit;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: background 0.18s ease, border-color 0.18s ease, transform 0.08s ease;
  white-space: nowrap;
}
.deck-btn:hover { border-color: var(--ui-accent, #7c9cff); }
.deck-btn:active { transform: translateY(1px); }
.deck-btn[aria-pressed="true"] { border-color: var(--ui-accent, #7c9cff); color: var(--ui-accent, #7c9cff); }
.deck-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.deck-select {
  font-family: inherit;
  font-size: clamp(12px, 1.1vw, 14px);
  color: var(--ui-fg, #eef2ff);
  background: var(--ui-panel, rgba(255,255,255,0.06));
  border: 1px solid var(--ui-border, rgba(255,255,255,0.14));
  border-radius: 9px;
  padding: 7px 9px;
}
.deck-counter {
  font-variant-numeric: tabular-nums;
  color: var(--ui-muted, #9aa7cc);
  font-size: clamp(12px, 1.1vw, 14px);
  min-width: 5em;
  text-align: center;
}
.deck-dots { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; justify-content: center; padding: 4px 0; }
.deck-dot {
  width: 9px;
  height: 9px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: var(--ui-border, rgba(255,255,255,0.14));
  cursor: pointer;
  transition: transform 0.14s ease, background 0.14s ease;
}
.deck-dot:hover { background: var(--s-accent2, #5fd4c0); }
.deck-dot.is-active { background: var(--ui-accent, #7c9cff); transform: scale(1.4); }
.deck-nav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: clamp(34px, 4vw, 46px);
  height: clamp(34px, 4vw, 46px);
  border-radius: 50%;
  border: 1px solid var(--ui-border, rgba(255,255,255,0.14));
  background: color-mix(in srgb, var(--ui-bg, #0b0f18) 72%, transparent);
  color: var(--ui-fg, #eef2ff);
  font-size: clamp(15px, 1.6vw, 20px);
  line-height: 1;
  display: grid;
  place-items: center;
  cursor: pointer;
  opacity: 0.5;
  transition: opacity 0.18s ease, border-color 0.18s ease;
  z-index: 3;
}
.deck-nav:hover { opacity: 1; border-color: var(--ui-accent, #7c9cff); }
.deck-nav.prev { left: clamp(4px, 1vw, 14px); }
.deck-nav.next { right: clamp(4px, 1vw, 14px); }
.deck-nav:disabled { opacity: 0.18; cursor: default; }
.deck-hint {
  text-align: center;
  color: var(--ui-muted, #9aa7cc);
  font-size: clamp(11px, 1vw, 13px);
  padding: 6px 10px clamp(8px, 1.4vh, 14px);
}
.deck-empty { display: grid; place-items: center; height: 100%; color: var(--s-muted); font-size: var(--f-body); }

/* ---------- responsive ---------- */

@media (max-width: 720px) {
  .deck-stage { padding: 6px; }
  .slide-frame { aspect-ratio: auto; height: 100%; width: 100%; max-height: none; border-radius: 12px; }
  .deck-hint { display: none; }
  .deck-nav { width: 34px; height: 34px; opacity: 0.75; }
  .slide-split { grid-template-columns: minmax(0, 1fr); }
  figure.slide-media { max-height: 28cqh; }
  .compare-grid { grid-template-columns: minmax(0, 1fr); }
  .compare-grid::after { display: none; }
  .card-grid--3, .card-grid--4, .stat-grid--3, .stat-grid--4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .timeline--horizontal { flex-direction: column; }
  .timeline--horizontal .timeline-item + .timeline-item { border-left: none; border-top: 1px dashed var(--s-border); padding-left: 0; padding-top: var(--sp-2); }
  .chart-split-grid, .table-split-grid { grid-template-columns: minmax(0, 1fr); }
}

@media (max-height: 560px) and (orientation: landscape) {
  .deck-hint { display: none; }
  .chart-svg { max-height: 38cqh; }
}

@media (prefers-reduced-motion: reduce) {
  .slide { transition: none; }
}
`

export function deckCss() {
  return DECK_CSS.trim() + '\n'
}
