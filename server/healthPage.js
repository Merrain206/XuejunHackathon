// HTML rendering for GET /health — the page you open on a strange machine to see
// whether the deployment is actually usable, without reading any logs.
const STATUS_STYLE = {
  pass: { icon: '✓', color: '#16a34a', label: '通过' },
  warn: { icon: '!', color: '#d97706', label: '注意' },
  fail: { icon: '✕', color: '#dc2626', label: '失败' },
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function renderHealthPage(report, { config, envFileLoaded, version = '' } = {}) {
  const rows = report.checks
    .map((item) => {
      const style = STATUS_STYLE[item.status] ?? STATUS_STYLE.warn
      return `<tr>
      <td class="icon" style="color:${style.color}">${style.icon}</td>
      <td>
        <div class="label">${escapeHtml(item.label)}</div>
        <div class="detail">${escapeHtml(item.detail)}</div>
        ${item.fix && item.status !== 'pass' ? `<div class="fix">修复：${escapeHtml(item.fix)}</div>` : ''}
      </td>
      <td class="status" style="color:${style.color}">${style.label}</td>
    </tr>`
    })
    .join('\n')

  const headline = report.ok
    ? report.warned
      ? '可以运行，但有需要留意的项目'
      : '一切正常'
    : '还不能运行：请先修复下面标红的项目'

  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>环境自检 · AI 网页 PPT 生成器</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; padding: 32px 20px; font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #0b0f18; color: #eef2ff; }
  main { max-width: 860px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 6px; }
  .sub { color: #9aa7cc; font-size: 13px; margin-bottom: 22px; }
  .banner { padding: 12px 16px; border-radius: 12px; font-weight: 650; margin-bottom: 20px;
            border: 1px solid ${report.ok ? 'rgba(22,163,74,.55)' : 'rgba(220,38,38,.55)'};
            background: ${report.ok ? 'rgba(22,163,74,.12)' : 'rgba(220,38,38,.12)'}; }
  table { width: 100%; border-collapse: collapse; background: rgba(255,255,255,.04);
          border: 1px solid rgba(255,255,255,.12); border-radius: 12px; overflow: hidden; }
  td { padding: 12px 14px; border-bottom: 1px solid rgba(255,255,255,.08); vertical-align: top; font-size: 14px; }
  tr:last-child td { border-bottom: none; }
  td.icon { width: 28px; font-size: 18px; text-align: center; }
  td.status { width: 64px; text-align: right; font-size: 13px; white-space: nowrap; }
  .label { font-weight: 600; }
  .detail { color: #9aa7cc; font-size: 12.5px; margin-top: 3px; word-break: break-all; }
  .fix { color: #fbbf24; font-size: 12.5px; margin-top: 5px; }
  .meta { margin-top: 18px; color: #9aa7cc; font-size: 12.5px; line-height: 1.8; }
  .meta code { background: rgba(255,255,255,.08); border-radius: 5px; padding: 1px 6px; }
  a { color: #7c9cff; }
</style>
</head>
<body>
<main>
  <h1>环境自检</h1>
  <div class="sub">AI 网页 PPT 生成器 ${escapeHtml(version)} · 生成于 ${escapeHtml(report.generatedAt)}</div>
  <div class="banner">${escapeHtml(headline)}</div>
  <table>${rows}</table>
  <div class="meta">
    运行环境：<code>${escapeHtml(report.platform)}</code> · Node <code>${escapeHtml(report.node)}</code><br />
    监听地址：<code>${escapeHtml(config?.host ?? '')}:${escapeHtml(config?.port ?? '')}</code> ·
    模型：<code>${escapeHtml(config?.model || '未配置')}</code><br />
    接口地址：<code>${escapeHtml(config?.baseUrl || '未配置')}</code> ·
    .env：<code>${escapeHtml(envFileLoaded ? '已加载' : '未找到（使用系统环境变量）')}</code><br />
    输出目录：<code>${escapeHtml(config?.outputDir || '')}</code><br />
    应用首页：<a href="/">/</a> · 机器可读版本：<a href="/api/health">/api/health</a>
  </div>
</main>
</body>
</html>
`
}
