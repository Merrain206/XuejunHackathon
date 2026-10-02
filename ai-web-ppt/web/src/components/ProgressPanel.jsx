const STAGES = [
  { id: 'search', label: '联网检索' },
  { id: 'design', label: '风格设计' },
  { id: 'slide', label: '逐页生成' },
]

/** Live feedback for the three generation stages plus pages as they land. */
export default function ProgressPanel({ progress, slides, elapsedMs, design, search, truncated }) {
  const seconds = (elapsedMs / 1000).toFixed(1)
  const activeStage = progress?.stage === 'connecting' ? null : progress?.stage

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>生成中</h2>
        <span className="muted">
          已收到 {slides.length} 页 · 用时 {seconds}s
        </span>
      </div>

      <div className="stage-row">
        {STAGES.map((stage) => {
          const state = activeStage === stage.id ? 'is-active' : activeStage ? 'is-done' : ''
          return (
            <span className={`stage-chip ${state}`} key={stage.id}>
              {stage.label}
            </span>
          )
        })}
      </div>

      <div className="progress-line" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <span data-testid="progress-message">{progress?.message || '正在连接服务…'}</span>
      </div>

      {search?.enabled && search.results?.length ? (
        <p className="muted" data-testid="search-summary">
          参考了 {search.results.length} 条资料
        </p>
      ) : null}

      {design ? (
        <div className="design-card" data-testid="design-summary">
          <div className="design-swatches" aria-hidden="true">
            {['bg', 'surface', 'accent', 'accent2', 'fg'].map((key) => (
              <span key={key} style={{ background: design.palette?.[key] || 'transparent' }} />
            ))}
          </div>
          <div className="design-text">
            <strong>{design.styleName}</strong>
            {design.rationale ? <span className="muted"> · {design.rationale}</span> : null}
          </div>
        </div>
      ) : null}

      {slides.length > 0 && (
        <ul className="live-list" data-testid="live-list">
          {slides.map((slide, index) => (
            <li key={`${index}-${slide.title}`} className="live-item">
              <span className="live-index">{String(index + 1).padStart(2, '0')}</span>
              <span className="live-type">{slide.type || slide.kind}</span>
              <span className="live-title">{slide.title}</span>
            </li>
          ))}
        </ul>
      )}

      {truncated && <p className="muted">已达到页数上限，后续内容被截断。</p>}
    </section>
  )
}
