# Reads the animation pane of a .pptx through the WPS Presentation COM interface.
# This is the closest thing to opening the "Animation Pane" by hand: it reports,
# per slide, how many animation entries exist and how each one is triggered.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/verify-pptx-animation.ps1 -Path path\to\deck.pptx
#
# Expected result for a deck exported with PPTX_BUILD_MODE=animation:
#   * every slide lists one or more effects (a cover may list exactly one)
#   * every effect is type=10 (Fade) with trigger=OnPageClick
#     (or WithPrevious for shapes that share a click, e.g. a KPI value + its label)
#   * no entry named "Shape N" — decoration shapes must stay static
param([Parameter(Mandatory = $true)][string]$Path)

$ErrorActionPreference = 'Stop'
$full = (Resolve-Path $Path).Path
Write-Output "file: $full"

# A browser-downloaded file carries Zone.Identifier ("from the Internet"); PowerPoint
# then opens it in Protected View, where animations do not play — even when the XML
# is perfect. This is the most common reason "animation is missing" in practice.
$zone = $null
try { $zone = Get-Content -LiteralPath $full -Stream Zone.Identifier -ErrorAction Stop } catch {}
if ($zone) {
  Write-Output 'WARNING: this file is marked as coming from the Internet (Zone.Identifier).'
  Write-Output '         PowerPoint will open it in Protected View, where animations are disabled.'
  Write-Output '         Fix: right-click > Properties > Unblock, or run: Unblock-File -LiteralPath <file>'
  Write-Output '         (The app can avoid this entirely: use its "save to project folder" export.)'
} else {
  Write-Output 'mark-of-the-web: none (no Protected View, animations can play)'
}

$app = New-Object -ComObject KWPP.Application
try { $app.Visible = $false } catch {}
$presentation = $null
$problems = @()
try {
  # Open(FileName, ReadOnly, Untitled, WithWindow)
  $presentation = $app.Presentations.Open($full, $true, $false, $false)
  Write-Output "slides: $($presentation.Slides.Count)"

  $triggerNames = @{ 1 = 'OnPageClick'; 2 = 'WithPrevious'; 3 = 'AfterPrevious' }
  $totalEffects = 0
  $decorEffects = 0

  for ($i = 1; $i -le $presentation.Slides.Count; $i++) {
    $slide = $presentation.Slides.Item($i)
    $seq = $null
    try { $seq = $slide.TimeLine.MainSequence } catch { Write-Output "  slide$i TimeLine error: $($_.Exception.Message)"; continue }
    $count = if ($seq) { $seq.Count } else { 0 }
    $totalEffects += $count
    $triggers = @{}
    for ($j = 1; $j -le $count; $j++) {
      $effect = $seq.Item($j)
      $trigger = 'unknown'
      try { $trigger = $triggerNames[[int]$effect.Timing.TriggerType] } catch {}
      if (-not $triggers.ContainsKey($trigger)) { $triggers[$trigger] = 0 }
      $triggers[$trigger] += 1
      if ($trigger -eq 'AfterPrevious') { $problems += "slide$i effect $j is AfterPrevious (should be OnPageClick)" }
      $name = ''
      try { $name = $effect.Shape.Name } catch {}
      if ($name -like 'Shape*') { $decorEffects += 1; $problems += "slide$i effect $j animates a decoration shape ($name)" }
      if ($effect.EffectType -ne 10) { $problems += "slide$i effect $j is not a Fade (type=$($effect.EffectType))" }
    }
    $summary = ($triggers.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ' '
    Write-Output ("  slide{0,-3} effects: {1,-3} {2}" -f $i, $count, $summary)
  }

  Write-Output "effects total: $totalEffects | decoration effects: $decorEffects"
  if ($problems.Count -eq 0) {
    Write-Output 'RESULT: PASS - every effect is a click-triggered fade, decorations untouched'
  } else {
    Write-Output 'RESULT: FAIL'
    $problems | Select-Object -First 20 | ForEach-Object { Write-Output "  - $_" }
  }
}
finally {
  if ($presentation) { $presentation.Close() | Out-Null }
  $app.Quit() | Out-Null
  [System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null
}
