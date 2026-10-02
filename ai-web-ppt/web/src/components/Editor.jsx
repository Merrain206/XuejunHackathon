import { SAMPLE_TEXT } from '../sample.js'

const SIZE_OPTIONS = [6, 8, 10, 12, 14, 16, 20]
const STYLE_HINTS = [
  '商务稳重、可信',
  '科技未来感，冷色高对比',
  '温暖亲和，适合内部沟通',
  '极简、大量留白、编辑感',
]

export default function Editor({
  content,
  onContent,
  stylePreference,
  onStylePreference,
  showCitations,
  onShowCitations,
  maxSlides,
  onMaxSlides,
  maxLimit = 20,
  maxBullets = 6,
  onGenerate,
  onCancel,
  generating = false,
  canGenerate = true,
}) {
  const options = SIZE_OPTIONS.filter((size) => size <= maxLimit)
  if (!options.includes(maxLimit)) options.push(maxLimit)

  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <h2>① 文档内容</h2>
          <span className="muted" data-testid="char-count">
            {content.length} 字符
          </span>
        </div>
        <textarea
          className="source-text"
          data-testid="source-text"
          value={content}
          spellCheck={false}
          placeholder="把要改造成 PPT 的文档粘贴到这里（至少 20 个字符）……"
          onChange={(event) => onContent(event.target.value)}
        />
        <div className="row wrap">
          <button type="button" className="btn ghost" data-testid="sample" onClick={() => onContent(SAMPLE_TEXT)}>
            载入示例文档
          </button>
          <button type="button" className="btn ghost" onClick={() => onContent('')} disabled={!content}>
            清空
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>② 风格偏好（选填）</h2>
          <span className="muted">配色 / 版式 / 配图风格 / 文案语气</span>
        </div>
        <textarea
          className="style-text"
          data-testid="style-preference"
          value={stylePreference}
          spellCheck={false}
          placeholder="用你自己的话描述期望，例如：面向投资人的年度复盘，稳重、克制、深色底，主色用深蓝，不要手绘插画风……留空则由 AI 根据文档语义自主决定。"
          onChange={(event) => onStylePreference(event.target.value)}
        />
        <div className="row wrap">
          {STYLE_HINTS.map((hint) => (
            <button
              type="button"
              className="chip small"
              key={hint}
              data-testid="style-hint"
              onClick={() => onStylePreference(stylePreference ? `${stylePreference}；${hint}` : hint)}
            >
              {hint}
            </button>
          ))}
          {stylePreference ? (
            <button type="button" className="btn ghost small" onClick={() => onStylePreference('')}>
              清空风格偏好
            </button>
          ) : null}
        </div>
        <p className="muted note">
          风格偏好<b>只影响视觉表现</b>，不会改变文档里的事实、数据与逻辑。
        </p>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>③ 来源标注</h2>
          <span className="muted">默认关闭</span>
        </div>
        <div className="row wrap">
          <button
            type="button"
            className={`switch${showCitations ? ' is-on' : ''}`}
            role="switch"
            aria-checked={showCitations}
            data-testid="citations-toggle"
            onClick={() => onShowCitations(!showCitations)}
          >
            <span className="switch-track" aria-hidden="true">
              <span className="switch-knob" />
            </span>
            <span className="switch-label">显示信息来源 / 标注数据出处</span>
          </button>
          <span className="muted note">
            {showCitations
              ? '开启后：数据与事实旁会加 [n] 角标，并在末尾生成「参考来源」页。'
              : '关闭时：只做内容融合，不输出任何角标与来源列表。'}
          </span>
        </div>
        <div className="row wrap">
          <span className="spacer" />
          <span className="muted note">
            可读性下限：单页要点 ≤ {maxBullets} 条 · 正文字号 ≥ 18px · 四周留白 ≥ 10%
          </span>
        </div>
      </section>

      <section className="panel panel--action">
        <div className="row wrap">
          <label className="field">
            页数上限
            <select
              data-testid="max-slides"
              value={maxSlides}
              onChange={(event) => onMaxSlides(Number(event.target.value))}
            >
              {options.map((size) => (
                <option key={size} value={size}>
                  {size} 页
                </option>
              ))}
            </select>
          </label>
          <span className="spacer" />
          {generating ? (
            <button type="button" className="btn danger" data-testid="cancel" onClick={onCancel}>
              停止生成
            </button>
          ) : (
            <button
              type="button"
              className="btn primary"
              data-testid="generate"
              disabled={!canGenerate}
              onClick={onGenerate}
            >
              ✨ 生成 PPT
            </button>
          )}
        </div>
      </section>
    </>
  )
}
