import { useCallback, useEffect, useRef, useState } from 'react'

import { PALETTE_OPTIONS } from '../shared/design.js'
import Deck from './Deck.jsx'

const NEXT_KEYS = new Set(['ArrowRight', 'ArrowDown', ' ', 'PageDown', 'Enter'])
const PREV_KEYS = new Set(['ArrowLeft', 'ArrowUp', 'PageUp'])

function toggleFullscreen() {
  const element = document.documentElement
  if (!document.fullscreenElement) {
    if (element.requestFullscreen) element.requestFullscreen().catch(() => {})
    else if (element.webkitRequestFullscreen) element.webkitRequestFullscreen()
  } else if (document.exitFullscreen) {
    document.exitFullscreen().catch(() => {})
  }
}

export default function DeckView({
  slides,
  design,
  title,
  showCitations = false,
  palette = 'auto',
  onPalette,
  onBack,
  onExportHtml,
  onExportPptx,
  onSavePptx,
  savedPath = '',
  exporting = '',
  buildMode = 'animation',
  onBuildMode,
  model = '',
}) {
  const [index, setIndex] = useState(0)
  const total = slides.length
  const touch = useRef({ x: 0, y: 0 })

  const go = useCallback(
    (target) => {
      if (!total) return
      setIndex(Math.max(0, Math.min(total - 1, target)))
    },
    [total],
  )

  useEffect(() => {
    if (index > total - 1) setIndex(Math.max(0, total - 1))
  }, [index, total])

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      const target = event.target
      if (target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return
      const key = event.key
      if (NEXT_KEYS.has(key)) {
        event.preventDefault()
        setIndex((current) => Math.min(Math.max(total - 1, 0), current + 1))
      } else if (PREV_KEYS.has(key)) {
        event.preventDefault()
        setIndex((current) => Math.max(0, current - 1))
      } else if (key === 'Home') {
        event.preventDefault()
        setIndex(0)
      } else if (key === 'End') {
        event.preventDefault()
        setIndex(Math.max(0, total - 1))
      } else if (key === 'f' || key === 'F') {
        toggleFullscreen()
      } else if (key === 'Escape' && !document.fullscreenElement) {
        onBack?.()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onBack, total])

  const handleFrameClick = (event) => {
    if (event.target.closest('button, a')) return
    const rect = event.currentTarget.getBoundingClientRect()
    const ratio = rect.width ? (event.clientX - rect.left) / rect.width : 0.5
    if (ratio < 0.3) go(index - 1)
    else if (ratio > 0.7) go(index + 1)
  }

  const onTouchStart = (event) => {
    if (event.touches.length !== 1) return
    touch.current = { x: event.touches[0].clientX, y: event.touches[0].clientY }
  }
  const onTouchEnd = (event) => {
    const start = touch.current
    if (!start.x) return
    const point = event.changedTouches[0]
    const dx = point.clientX - start.x
    const dy = point.clientY - start.y
    touch.current = { x: 0, y: 0 }
    if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy)) go(index + (dx < 0 ? 1 : -1))
  }

  return (
    <div className="deck-root" data-testid="deck" onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
      <div className="deck-toolbar">
        <button type="button" className="deck-btn" onClick={onBack}>
          ← 返回编辑
        </button>
        <span className="deck-toolbar-title" title={title}>
          {title}
        </span>
        {design?.styleName ? (
          <span className="deck-style-chip" data-testid="style-chip" title={design.rationale || ''}>
            🎨 {design.styleName}
          </span>
        ) : null}
        <span className="deck-spacer" />
        <select
          className="deck-select"
          data-testid="palette-select"
          value={palette}
          onChange={(event) => onPalette?.(event.target.value)}
          title="配色由 AI 决定，也可以手动覆盖"
        >
          {PALETTE_OPTIONS.map((option) => (
            <option key={option.id} value={option.id}>
              配色：{option.name}
            </option>
          ))}
        </select>
        <select
          className="deck-select"
          data-testid="build-mode"
          value={buildMode}
          onChange={(event) => onBuildMode?.(event.target.value)}
          title="PPTX 逐条出现的方式：原生入场动画，或把每次揭示拆成一页（任何阅读器都生效）"
        >
          <option value="animation">PPTX 动画：入场效果</option>
          <option value="steps">PPTX 动画：逐条分页</option>
          <option value="both">PPTX 动画：两者都要</option>
        </select>
        <button
          type="button"
          className="deck-btn"
          data-testid="export-html"
          onClick={onExportHtml}
          disabled={Boolean(exporting)}
        >
          {exporting === 'html' ? '打包中…' : '⬇ 导出 HTML'}
        </button>
        <button
          type="button"
          className="deck-btn"
          data-testid="export-pptx"
          onClick={onExportPptx}
          disabled={Boolean(exporting)}
        >
          {exporting === 'pptx' ? '导出中…' : '⬇ 导出 PPTX'}
        </button>
        <button
          type="button"
          className="deck-btn"
          data-testid="save-pptx"
          onClick={onSavePptx}
          disabled={Boolean(exporting)}
          title="直接写进项目 .exports 目录；从磁盘打开不会进 PowerPoint 受保护视图（那里动画不播放）"
        >
          {exporting === 'save' ? '保存中…' : '💾 存到项目目录'}
        </button>
        <button type="button" className="deck-btn" onClick={toggleFullscreen} title="全屏演示 (F)">
          ⛶ 全屏
        </button>
      </div>

      <div className="deck-stage">
        <button
          type="button"
          className="deck-nav prev"
          onClick={() => go(index - 1)}
          disabled={index === 0}
          aria-label="上一页"
        >
          ‹
        </button>
        <div className="slide-frame" data-testid="slide-frame" onClick={handleFrameClick}>
          <Deck
            slides={slides}
            design={design}
            activeIndex={index}
            showCitations={showCitations}
            className="deck-surface--live"
          />
        </div>
        <button
          type="button"
          className="deck-nav next"
          onClick={() => go(index + 1)}
          disabled={index >= total - 1}
          aria-label="下一页"
        >
          ›
        </button>
      </div>

      <div className="deck-dots">
        {slides.map((slide, i) => (
          <button
            key={`dot-${i}`}
            type="button"
            className={`deck-dot${i === index ? ' is-active' : ''}`}
            aria-label={`第 ${i + 1} 页`}
            aria-current={i === index ? 'true' : 'false'}
            onClick={() => go(i)}
          />
        ))}
      </div>
      <div className="deck-hint">
        <span data-testid="counter">
          {total ? index + 1 : 0} / {total}
        </span>
        {' · ← → 翻页 · F 全屏'}
        {showCitations ? ' · 来源标注已开启' : ''}
        {model ? ` · ${model}` : ''}
      </div>
      {savedPath ? (
        <div className="deck-hint deck-hint--path" data-testid="saved-path">
          已保存到 <code>{savedPath}</code> —— 从磁盘直接打开，动画才会播放（浏览器下载的文件会被 Windows 标记为"来自 Internet"，PowerPoint 受保护视图下动画被禁用）
        </div>
      ) : null}
    </div>
  )
}
