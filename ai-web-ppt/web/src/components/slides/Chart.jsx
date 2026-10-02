// Pure-SVG charts (bar / line / pie / donut). No chart library, no external
// requests, so the same component works in the live deck and in the offline
// export. Colours come exclusively from the design tokens.
const WIDTH = 640
const HEIGHT = 320
const PAD = { top: 26, right: 18, bottom: 46, left: 46 }

function seriesColor(index) {
  if (index === 0) return 'var(--s-accent)'
  if (index === 1) return 'var(--s-accent2)'
  if (index === 2) return 'var(--s-muted)'
  return 'var(--s-border)'
}

function niceMax(values) {
  const max = Math.max(...values, 0)
  if (max <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(max))
  return Math.ceil(max / magnitude) * magnitude
}

function formatValue(value, unit) {
  const rounded = Math.abs(value) >= 100 ? Math.round(value) : Math.round(value * 100) / 100
  return `${rounded}${unit || ''}`
}

function EmptyChart({ label }) {
  return (
    <div className="chart-empty">
      <span className="chart-empty-mark" aria-hidden="true">
        ⃠
      </span>
      <span>{label}</span>
    </div>
  )
}

function BarChart({ chart, labels }) {
  const series = chart.series
  const allValues = series.flatMap((item) => item.data.map((point) => point.value))
  const max = niceMax(allValues)
  const plotWidth = WIDTH - PAD.left - PAD.right
  const plotHeight = HEIGHT - PAD.top - PAD.bottom
  const groupWidth = plotWidth / Math.max(labels.length, 1)
  const barWidth = Math.max(6, (groupWidth * 0.62) / series.length)
  const baseline = PAD.top + plotHeight

  const ticks = Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4
    return { y: PAD.top + plotHeight * (1 - ratio), value: max * ratio }
  })

  return (
    <svg className="chart-svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={chart.title || 'bar chart'}>
      {ticks.map((tick) => (
        <g key={tick.y}>
          <line x1={PAD.left} x2={WIDTH - PAD.right} y1={tick.y} y2={tick.y} stroke="var(--s-border)" strokeWidth="1" />
          <text x={PAD.left - 8} y={tick.y + 4} textAnchor="end" fontSize="13" fill="var(--s-muted)">
            {formatValue(tick.value, chart.unit)}
          </text>
        </g>
      ))}
      {labels.map((label, labelIndex) => {
        const groupStart = PAD.left + groupWidth * labelIndex + groupWidth * 0.19
        return (
          <g key={`${label}-${labelIndex}`}>
            {series.map((item, seriesIndex) => {
              const point = item.data[labelIndex]
              if (!point) return null
              const height = Math.max(2, (point.value / max) * plotHeight)
              const x = groupStart + barWidth * seriesIndex
              return (
                <g key={`${item.name}-${seriesIndex}`}>
                  <rect
                    x={x}
                    y={baseline - height}
                    width={barWidth - 3}
                    height={height}
                    rx="3"
                    fill={seriesColor(seriesIndex)}
                  />
                  <text
                    x={x + (barWidth - 3) / 2}
                    y={baseline - height - 6}
                    textAnchor="middle"
                    fontSize="13"
                    fill="var(--s-fg)"
                  >
                    {formatValue(point.value, '')}
                  </text>
                </g>
              )
            })}
            <text
              x={PAD.left + groupWidth * labelIndex + groupWidth / 2}
              y={baseline + 20}
              textAnchor="middle"
              fontSize="13"
              fill="var(--s-muted)"
            >
              {label.length > 8 ? `${label.slice(0, 7)}…` : label}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

function LineChart({ chart, labels }) {
  const series = chart.series
  const allValues = series.flatMap((item) => item.data.map((point) => point.value))
  const max = niceMax(allValues)
  const plotWidth = WIDTH - PAD.left - PAD.right
  const plotHeight = HEIGHT - PAD.top - PAD.bottom
  const baseline = PAD.top + plotHeight
  const stepX = labels.length > 1 ? plotWidth / (labels.length - 1) : 0

  return (
    <svg className="chart-svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={chart.title || 'line chart'}>
      {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
        <line
          key={ratio}
          x1={PAD.left}
          x2={WIDTH - PAD.right}
          y1={PAD.top + plotHeight * (1 - ratio)}
          y2={PAD.top + plotHeight * (1 - ratio)}
          stroke="var(--s-border)"
          strokeWidth="1"
        />
      ))}
      {series.map((item, seriesIndex) => {
        const points = item.data.map((point, index) => ({
          x: PAD.left + stepX * index,
          y: baseline - (point.value / max) * plotHeight,
          point,
        }))
        const path = points.map((entry) => `${entry.x},${entry.y}`).join(' ')
        return (
          <g key={`${item.name}-${seriesIndex}`}>
            <polyline
              points={path}
              fill="none"
              stroke={seriesColor(seriesIndex)}
              strokeWidth="3"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {points.map((entry, index) => (
              <circle key={index} cx={entry.x} cy={entry.y} r="4" fill={seriesColor(seriesIndex)} />
            ))}
          </g>
        )
      })}
      {labels.map((label, index) => (
        <text
          key={`${label}-${index}`}
          x={PAD.left + stepX * index}
          y={baseline + 20}
          textAnchor="middle"
          fontSize="13"
          fill="var(--s-muted)"
        >
          {label.length > 8 ? `${label.slice(0, 7)}…` : label}
        </text>
      ))}
    </svg>
  )
}

function polar(cx, cy, radius, angle) {
  const radians = ((angle - 90) * Math.PI) / 180
  return { x: cx + radius * Math.cos(radians), y: cy + radius * Math.sin(radians) }
}

function PieChart({ chart, donut }) {
  const first = chart.series[0]
  const data = first.data
  const total = data.reduce((sum, point) => sum + Math.max(0, point.value), 0) || 1
  const cx = WIDTH / 2
  const cy = HEIGHT / 2
  const radius = Math.min(WIDTH, HEIGHT) / 2 - 24
  const inner = donut ? radius * 0.56 : 0
  let angle = 0

  const arcs = data.map((point, index) => {
    const sweep = (Math.max(0, point.value) / total) * 360
    const start = polar(cx, cy, radius, angle)
    const end = polar(cx, cy, radius, angle + sweep)
    const largeArc = sweep > 180 ? 1 : 0
    const startInner = polar(cx, cy, inner, angle + sweep)
    const endInner = polar(cx, cy, inner, angle)
    const path = donut
      ? [
          `M ${start.x} ${start.y}`,
          `A ${radius} ${radius} 0 ${largeArc} 1 ${end.x} ${end.y}`,
          `L ${startInner.x} ${startInner.y}`,
          `A ${inner} ${inner} 0 ${largeArc} 0 ${endInner.x} ${endInner.y}`,
          'Z',
        ].join(' ')
      : [`M ${cx} ${cy}`, `L ${start.x} ${start.y}`, `A ${radius} ${radius} 0 ${largeArc} 1 ${end.x} ${end.y}`, 'Z'].join(' ')
    const labelAt = polar(cx, cy, radius * 0.72, angle + sweep / 2)
    angle += sweep
    return { path, point, index, labelAt, share: (point.value / total) * 100 }
  })

  return (
    <svg className="chart-svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={chart.title || 'pie chart'}>
      {arcs.map((arc) => (
        <path key={arc.index} d={arc.path} fill={seriesColor(arc.index)} opacity={arc.index > 3 ? 0.6 : 1} />
      ))}
      {arcs.map((arc) =>
        arc.share >= 6 ? (
          <text
            key={`label-${arc.index}`}
            x={arc.labelAt.x}
            y={arc.labelAt.y + 4}
            textAnchor="middle"
            fontSize="13"
            fill="var(--s-bg)"
            fontWeight="700"
          >
            {`${Math.round(arc.share)}%`}
          </text>
        ) : null,
      )}
    </svg>
  )
}

export default function Chart({ chart }) {
  if (!chart || !Array.isArray(chart.series) || !chart.series.length) {
    return <EmptyChart label="暂无图表数据" />
  }
  const labels = chart.series[0].data.map((point) => point.label)
  if (!labels.length) return <EmptyChart label="暂无图表数据" />

  return (
    <figure className="chart">
      {chart.title ? <figcaption className="chart-title">{chart.title}</figcaption> : null}
      <div className="chart-body">
        {chart.kind === 'line' || chart.kind === 'area' ? (
          <LineChart chart={chart} labels={labels} />
        ) : chart.kind === 'pie' || chart.kind === 'donut' ? (
          <PieChart chart={chart} donut={chart.kind === 'donut'} />
        ) : (
          <BarChart chart={chart} labels={labels} />
        )}
      </div>
      {chart.series.length > 1 || chart.kind === 'pie' || chart.kind === 'donut' ? (
        <ul className="chart-legend">
          {(chart.kind === 'pie' || chart.kind === 'donut' ? chart.series[0].data : chart.series).map((item, index) => (
            <li key={index}>
              <span className="chart-swatch" style={{ background: seriesColor(index) }} aria-hidden="true" />
              <span>{chart.kind === 'pie' || chart.kind === 'donut' ? item.label : item.name}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </figure>
  )
}
