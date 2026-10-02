import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

import { buildPptxFile, fetchHealth, inlineDeckImages, streamGenerate } from './api.js'
import Deck from './components/Deck.jsx'
import DeckView from './components/DeckView.jsx'
import Editor from './components/Editor.jsx'
import ProgressPanel from './components/ProgressPanel.jsx'
import { downloadHtml, safeFilename, submitDownloadForm } from './download.js'
import { designVars, resolveDesign } from './shared/design.js'
import { buildStandaloneHtml } from './shared/exportHtml.js'

const DEFAULT_MAX_SLIDES = 12

const TYPE_LABELS = {
  cover: '封面',
  section: '章节',
  bullets: '要点',
  stats: '数据',
  chart: '图表',
  compare: '对比',
  timeline: '流程',
  table: '表格',
  quote: '观点',
  references: '来源',
  closing: '收尾',
}

function pageSummary(slide) {
  if (slide.stats?.length) return slide.stats.map((stat) => `${stat.value}${stat.unit || ''} ${stat.label}`).join(' · ')
  if (slide.chart) return `${slide.chart.kind} 图表 · ${slide.chart.series?.[0]?.data?.length ?? 0} 组数据`
  if (slide.compare) return `${slide.compare.left.title} ↔ ${slide.compare.right.title}`
  if (slide.timeline?.length) return slide.timeline.map((item) => item.title).join(' → ')
  if (slide.table) return `${slide.table.headers.length} 列 × ${slide.table.rows.length} 行`
  if (slide.quote) return slide.quote.text
  return (slide.bullets || []).slice(0, 2).join(' · ')
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [healthError, setHealthError] = useState('')
  const [content, setContent] = useState('')
  const [stylePreference, setStylePreference] = useState('')
  const [showCitations, setShowCitations] = useState(false)
  const [maxSlides, setMaxSlides] = useState(DEFAULT_MAX_SLIDES)
  const [palette, setPalette] = useState('auto')
  const [phase, setPhase] = useState('idle') // idle | generating | ready
  const [liveSlides, setLiveSlides] = useState([])
  const [design, setDesign] = useState(null)
  const [search, setSearch] = useState(null)
  const [deck, setDeck] = useState(null)
  const [progress, setProgress] = useState({ message: '', stage: '', done: 0, index: 0 })
  const [error, setError] = useState('')
  const [presenting, setPresenting] = useState(false)
  const [exporting, setExporting] = useState('')
  const [savedPath, setSavedPath] = useState('')
  const [buildMode, setBuildMode] = useState('animation')
  const [resolvedImages, setResolvedImages] = useState({})
  const [elapsedMs, setElapsedMs] = useState(0)
  const abortRef = useRef(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchHealth({ signal: controller.signal })
      .then((payload) => {
        setHealth(payload)
        if (payload?.maxSlides) setMaxSlides((current) => Math.min(current, payload.maxSlides))
      })
      .catch((err) => {
        if (err?.name !== 'AbortError') setHealthError(err?.message || String(err))
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (phase !== 'generating') return undefined
    const startedAt = performance.now()
    const timer = setInterval(() => setElapsedMs(performance.now() - startedAt), 200)
    return () => {
      clearInterval(timer)
      setElapsedMs(0)
    }
  }, [phase])

  const problems = health?.problems ?? (healthError ? [healthError] : [])
  const effectiveDesign = useMemo(() => resolveDesign(deck?.design ?? design, palette), [deck?.design, design, palette])
  const shellStyle = useMemo(() => designVars(effectiveDesign), [effectiveDesign])

  const generate = useCallback(async () => {
    const controller = new AbortController()
    abortRef.current = controller
    setPhase('generating')
    setLiveSlides([])
    setDesign(null)
    setSearch(null)
    setDeck(null)
    setError('')
    setPresenting(false)
    setProgress({ message: '正在连接服务…', stage: 'connecting', done: 0, index: 0 })

    try {
      await streamGenerate(
        { content, stylePreference, showCitations, maxSlides, signal: controller.signal },
        {
          onStatus: (payload) =>
            setProgress((current) => ({ ...current, stage: payload.stage || current.stage, message: payload.message || current.message })),
          onSearch: (payload) => setSearch(payload),
          onDesign: (payload) => {
            setDesign(payload)
            setProgress((current) => ({ ...current, stage: 'design' }))
          },
          onProgress: (payload) =>
            setProgress({
              stage: payload.stage || 'slide',
              message: payload.message || '正在生成…',
              done: payload.done ?? 0,
              index: payload.index ?? 0,
            }),
          onSlide: (payload) => setLiveSlides((current) => [...current, payload.slide]),
          onDone: (payload) => {
            setDeck({
              title: payload.title,
              design: payload.design,
              slides: payload.slides,
              stats: payload.stats,
              search: payload.search,
              showCitations: payload.showCitations,
              stylePreference: payload.stylePreference,
              usage: payload.usage,
              truncated: payload.truncated,
              elapsedMs: payload.elapsedMs,
              model: payload.model,
            })
            setDesign(payload.design)
            setSearch(payload.search)
            setPhase('ready')
            setPresenting(true)
          },
          onError: (payload) => {
            setError(payload.message || '生成失败')
            setPhase('idle')
          },
        },
      )
    } catch (err) {
      if (err?.name === 'AbortError') {
        setPhase((current) => (current === 'generating' ? 'idle' : current))
        setProgress({ message: '已停止生成', stage: '', done: 0, index: 0 })
      } else {
        setError(err?.message || String(err))
        setPhase('idle')
      }
    } finally {
      abortRef.current = null
    }
  }, [content, maxSlides, showCitations, stylePreference])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  // Prefetch pictures as soon as a deck exists, so the export click can stay
  // fully synchronous: a download triggered after an `await` loses the user
  // activation window and browsers block it silently.
  useEffect(() => {
    if (!deck?.slides?.length) {
      setResolvedImages({})
      return undefined
    }
    let cancelled = false
    inlineDeckImages(deck.slides)
      .then((map) => {
        if (!cancelled) setResolvedImages(map)
      })
      .catch(() => {
        if (!cancelled) setResolvedImages({})
      })
    return () => {
      cancelled = true
    }
  }, [deck])

  const exportHtml = useCallback(() => {
    if (!deck?.slides?.length) return
    setExporting('html')
    try {
      // Rendered from the very same React components the live deck uses, so the
      // offline file cannot drift from the preview.
      const bodyHtml = renderToStaticMarkup(
        <Deck
          slides={deck.slides}
          design={effectiveDesign}
          activeIndex={0}
          resolvedImages={resolvedImages}
          showCitations={deck.showCitations}
          offline
        />,
      )
      const html = buildStandaloneHtml({
        bodyHtml,
        title: deck.title,
        slideCount: deck.slides.length,
        generatedAt: new Date().toLocaleString('zh-CN'),
        model: deck.model || health?.model || '',
        showCitations: deck.showCitations,
        designStyleName: effectiveDesign?.styleName || '',
        data: { title: deck.title, design: effectiveDesign, slides: deck.slides, showCitations: deck.showCitations },
      })
      downloadHtml(safeFilename(deck.title, '.html'), html)
    } catch (err) {
      setError(`导出 HTML 失败：${err?.message || err}`)
    } finally {
      setExporting('')
    }
  }, [deck, effectiveDesign, health?.model, resolvedImages])

  const exportPptx = useCallback(() => {
    if (!deck?.slides?.length) return
    setExporting('pptx')
    // Server renders backgrounds, images, charts and citations into the file and
    // applies the chosen reveal strategy.
    submitDownloadForm('/api/export/pptx', {
      deck: {
        title: deck.title,
        design: effectiveDesign,
        slides: deck.slides,
        meta: {
          model: deck.model || health?.model || '',
          generatedAt: new Date().toLocaleString('zh-CN'),
          citations: deck.showCitations,
        },
      },
      buildMode,
    })
    setTimeout(() => setExporting(''), 2500)
  }, [buildMode, deck, effectiveDesign, health?.model])

  /** Saves the pptx into the project folder — no mark-of-the-web, so PowerPoint
   *  does not open it in Protected View (which silently disables animations). */
  const savePptx = useCallback(async () => {
    if (!deck?.slides?.length) return
    setExporting('save')
    setSavedPath('')
    try {
      const response = await fetch('/api/export/pptx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          deck: {
            title: deck.title,
            design: effectiveDesign,
            slides: deck.slides,
            meta: {
              model: deck.model || health?.model || '',
              generatedAt: new Date().toLocaleString('zh-CN'),
              citations: deck.showCitations,
            },
          },
          buildMode,
          saveTo: 'project',
        }),
      })
      const payload = await response.json()
      if (!response.ok || !payload?.ok) throw new Error(payload?.error || `HTTP ${response.status}`)
      setSavedPath(payload.path)
    } catch (err) {
      setError(`保存到项目目录失败：${err?.message || err}`)
    } finally {
      setExporting('')
    }
  }, [buildMode, deck, effectiveDesign, health?.model])

  if (presenting && deck?.slides?.length) {
    return (
      <div className="deck-host" style={shellStyle}>
        <DeckView
          slides={deck.slides}
          design={effectiveDesign}
          title={deck.title}
          showCitations={deck.showCitations}
          palette={palette}
          onPalette={setPalette}
          onBack={() => setPresenting(false)}
          onExportHtml={exportHtml}
          onExportPptx={exportPptx}
          onSavePptx={savePptx}
          savedPath={savedPath}
          exporting={exporting}
          buildMode={buildMode}
          onBuildMode={setBuildMode}
          model={deck.model || health?.model || ''}
        />
      </div>
    )
  }

  return (
    <div className="app-root" style={shellStyle}>
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">AI</span>
          <div>
            <h1>AI 网页 PPT 生成器</h1>
            <p className="muted">文档 → 逐页信息设计 → 网页演示 / 离线 HTML / PPTX</p>
          </div>
        </div>
        <div className="header-right">
          {/* Only positive state is surfaced: capability/provider details are an
              implementation concern, never an error message in the UI. */}
          {health?.ok ? (
            <span className="badge ok" data-testid="health-badge">
              模型 {health.model}
            </span>
          ) : null}
        </div>
      </header>

      {problems.length > 0 && (
        <div className="banner warn" data-testid="config-warning">
          <strong>还差一步：</strong>
          <ul>
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
          <span className="muted">填写后重启后端（npm start / npm run dev）即可生成。</span>
        </div>
      )}

      {error && (
        <div className="banner danger" data-testid="error">
          <strong>出错了：</strong> {error}
        </div>
      )}

      <Editor
        content={content}
        onContent={setContent}
        stylePreference={stylePreference}
        onStylePreference={setStylePreference}
        showCitations={showCitations}
        onShowCitations={setShowCitations}
        maxSlides={maxSlides}
        onMaxSlides={setMaxSlides}
        maxLimit={health?.maxSlides ?? 20}
        maxBullets={health?.maxBulletsPerSlide ?? 6}
        onGenerate={generate}
        onCancel={cancel}
        generating={phase === 'generating'}
        canGenerate={content.trim().length >= 20 && problems.length === 0}
        searchEnabled={Boolean(health?.capabilities?.search?.enabled)}
      />

      {phase === 'generating' && (
        <ProgressPanel
          progress={progress}
          slides={liveSlides}
          elapsedMs={elapsedMs}
          design={design}
          search={search}
          truncated={false}
        />
      )}

      {deck?.slides?.length && phase !== 'generating' ? (
        <section className="panel" data-testid="preview">
          <div className="panel-head">
            <h2>生成完成</h2>
            <span className="muted">
              {deck.slides.length} 页
              {deck.elapsedMs ? ` · ${(deck.elapsedMs / 1000).toFixed(1)}s` : ''}
              {deck.stats?.splits ? ` · 自动拆分为 ${deck.stats.splits} 页` : ''}
              {deck.stats?.usedCitations ? ` · 来源 ${deck.stats.usedCitations} 条` : ''}
              {deck.stats?.droppedCitations ? ` · 丢弃不可验证来源 ${deck.stats.droppedCitations} 条` : ''}
            </span>
          </div>

          <div className="design-card" data-testid="deck-design">
            <div className="design-swatches" aria-hidden="true">
              {['bg', 'surface', 'accent', 'accent2', 'fg'].map((key) => (
                <span key={key} style={{ background: effectiveDesign?.palette?.[key] || 'transparent' }} />
              ))}
            </div>
            <div className="design-text">
              <strong>{effectiveDesign?.styleName}</strong>
              {effectiveDesign?.rationale ? <span className="muted"> · {effectiveDesign.rationale}</span> : null}
              {deck.stylePreference ? <span className="muted"> · 已应用你的风格偏好</span> : <span className="muted"> · AI 自主风格</span>}
            </div>
          </div>

          {deck.search ? (
            <p className="muted" data-testid="deck-search">
              {deck.search.enabled
                ? `联网检索：${deck.search.provider} · ${deck.search.results?.length ?? 0} 条参考`
                : '联网检索未配置：未引入外部来源（也不会编造链接）'}
            </p>
          ) : null}

          <div className="preview-grid">
            {deck.slides.map((slide, index) => (
              <article className="preview-card" key={`${index}-${slide.type}`} data-type={slide.type}>
                <div className="preview-card-head">
                  <span className="preview-index">{String(index + 1).padStart(2, '0')}</span>
                  <span className="preview-type">{TYPE_LABELS[slide.type] || slide.type}</span>
                </div>
                <h3>{slide.title}</h3>
                <p className="muted">{pageSummary(slide)}</p>
              </article>
            ))}
          </div>

          <div className="row wrap">
            <button type="button" className="btn primary" data-testid="present" onClick={() => setPresenting(true)}>
              ▶ 开始演示
            </button>
            <button type="button" className="btn" data-testid="export-html" onClick={exportHtml} disabled={Boolean(exporting)}>
              {exporting === 'html' ? '打包中…' : '⬇ 导出离线 HTML'}
            </button>
            <button type="button" className="btn" data-testid="export-pptx" onClick={exportPptx} disabled={Boolean(exporting)}>
              {exporting === 'pptx' ? '导出中…' : '⬇ 导出 PPTX'}
            </button>
            <button type="button" className="btn ghost" onClick={generate}>
              ↻ 重新生成
            </button>
          </div>
        </section>
      ) : null}

      <footer className="app-footer muted">
        API Key 只从 <code>.env</code> 读取，前端与源码中均不含任何密钥
        {health?.model ? ` · 当前模型 ${health.model}` : ''}
        {health?.baseUrl ? ` · ${health.baseUrl}` : ''}
      </footer>
    </div>
  )
}
