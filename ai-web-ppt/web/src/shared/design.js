// Design tokens -> CSS custom properties.
//
// All visual decisions (palette, type, radius, decor) come from the model's
// `design` object at runtime. This module only maps them onto CSS variables and
// supplies neutral fallbacks — it never contains content-specific styling.

export const FALLBACK_PALETTES = {
  auto: null, // means: use whatever the model produced
  midnight: {
    bg: '#0a0e1a',
    bgAlt: '#131a33',
    fg: '#eef2ff',
    muted: '#9aa7cc',
    accent: '#7c9cff',
    accent2: '#37d6c0',
    surface: 'rgba(255,255,255,0.06)',
    border: 'rgba(255,255,255,0.14)',
  },
  paper: {
    bg: '#f6f5f1',
    bgAlt: '#ffffff',
    fg: '#1d2333',
    muted: '#5d6478',
    accent: '#2f5bd7',
    accent2: '#0f9d8f',
    surface: 'rgba(29,35,51,0.05)',
    border: 'rgba(29,35,51,0.16)',
  },
  ocean: {
    bg: '#04222b',
    bgAlt: '#083344',
    fg: '#e6fbff',
    muted: '#8fc4d0',
    accent: '#22d3ee',
    accent2: '#a3e635',
    surface: 'rgba(255,255,255,0.07)',
    border: 'rgba(255,255,255,0.16)',
  },
  sunset: {
    bg: '#1b1023',
    bgAlt: '#3b1c3a',
    fg: '#fff3e9',
    muted: '#d3a9a2',
    accent: '#fb923c',
    accent2: '#f472b6',
    surface: 'rgba(255,255,255,0.07)',
    border: 'rgba(255,255,255,0.16)',
  },
}

export const PALETTE_OPTIONS = [
  { id: 'auto', name: 'AI 自主' },
  { id: 'midnight', name: '午夜蓝' },
  { id: 'paper', name: '素纸白' },
  { id: 'ocean', name: '深海青' },
  { id: 'sunset', name: '日落橙' },
]

const FONT_STACKS = {
  'system-ui': 'system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
  'Noto Sans SC': '"Noto Sans SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif',
  'Source Han Sans SC': '"Source Han Sans SC", "Noto Sans SC", "PingFang SC", sans-serif',
  Inter: 'Inter, system-ui, "Segoe UI", "PingFang SC", sans-serif',
  Arial: 'Arial, Helvetica, "PingFang SC", sans-serif',
  Georgia: 'Georgia, "Songti SC", "SimSun", serif',
  'Times New Roman': '"Times New Roman", "Songti SC", "SimSun", serif',
  Consolas: 'Consolas, "SFMono-Regular", "Courier New", monospace',
  'ui-monospace': 'ui-monospace, SFMono-Regular, Consolas, monospace',
}

export function fontStack(name) {
  return FONT_STACKS[name] || FONT_STACKS['system-ui']
}

function hexToRgb(hex) {
  if (typeof hex !== 'string') return null
  const value = hex.trim().replace('#', '')
  const expanded = value.length === 3 ? value.split('').map((c) => c + c).join('') : value
  if (!/^[0-9a-f]{6}$/i.test(expanded)) return null
  return {
    r: Number.parseInt(expanded.slice(0, 2), 16),
    g: Number.parseInt(expanded.slice(2, 4), 16),
    b: Number.parseInt(expanded.slice(4, 6), 16),
  }
}

function luminance(hex) {
  const rgb = hexToRgb(hex)
  if (!rgb) return null
  const channel = (value) => {
    const normalized = value / 255
    return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(rgb.r) + 0.7152 * channel(rgb.g) + 0.0722 * channel(rgb.b)
}

/** Relative luminance of the palette background, used to pick decor opacity. */
export function isLightPalette(palette) {
  const value = luminance(palette?.bg)
  return value === null ? false : value > 0.45
}

export function rgba(hex, alpha) {
  const rgb = hexToRgb(hex)
  if (!rgb) return `rgba(127,127,127,${alpha})`
  return `rgba(${rgb.r},${rgb.g},${rgb.b},${alpha})`
}

/**
 * Merge an optional manual palette override into the model's design, then expose
 * everything as CSS variables (deck scope + shell scope).
 */
export function resolveDesign(design, paletteId = 'auto') {
  const base = design || {}
  const palette = { ...(base.palette || {}) }
  const override = FALLBACK_PALETTES[paletteId]
  const merged = override ? { ...palette, ...override } : palette
  return {
    ...base,
    palette: merged,
    typography: base.typography || { heading: 'system-ui', body: 'system-ui', scale: 1, headingWeight: 700, letterSpacing: 0 },
    decor: base.decor || { style: 'geometric', imageStyle: '', radius: 14, pattern: 'grid' },
  }
}

export function designVars(design) {
  if (!design) return {}
  const { palette = {}, typography = {}, decor = {} } = design
  const light = isLightPalette(palette)
  const headingFont = fontStack(typography.heading)
  const bodyFont = fontStack(typography.body || typography.heading)
  return {
    '--s-bg': palette.bg,
    '--s-bg-alt': palette.bgAlt || palette.bg,
    '--s-fg': palette.fg,
    '--s-muted': palette.muted,
    '--s-accent': palette.accent,
    '--s-accent2': palette.accent2 || palette.accent,
    '--s-surface': palette.surface || (light ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.06)'),
    '--s-border': palette.border || (light ? 'rgba(0,0,0,0.14)' : 'rgba(255,255,255,0.14)'),
    '--s-radius': `${Number(decor.radius ?? 14)}px`,
    '--s-heading-font': headingFont,
    '--s-body-font': bodyFont,
    '--s-scale': String(Number(typography.scale ?? 1)),
    '--s-heading-weight': String(Number(typography.headingWeight ?? 700)),
    '--s-letter-spacing': `${Number(typography.letterSpacing ?? 0)}px`,
    '--s-decor-alpha': light ? '0.1' : '0.16',
    // shell (toolbar / editor chrome) follows the same palette
    '--ui-bg': palette.bgAlt || palette.bg,
    '--ui-panel': palette.surface || (light ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.06)'),
    '--ui-border': palette.border || (light ? 'rgba(0,0,0,0.14)' : 'rgba(255,255,255,0.14)'),
    '--ui-fg': palette.fg,
    '--ui-muted': palette.muted,
    '--ui-accent': palette.accent,
  }
}

/** Short human summary of the model's design, shown in the UI. */
export function describeDesign(design) {
  if (!design) return ''
  const parts = [design.styleName, design.palette?.accent].filter(Boolean)
  return parts.join(' · ')
}
