# 바탕화면 아이콘이 실행하는 파일. 대시보드 웹사이트를 켜고 브라우저를 연다. (AI 분석은 하지 않는다)
$Host.UI.RawUI.WindowTitle = '매수에 이의 있습니다 - 대시보드'
Set-Location (Join-Path $PSScriptRoot 'dashboard')
$url = 'http://localhost:3000'

# 이미 켜져 있으면 브라우저만 연다
try {
    Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3 | Out-Null
    Start-Process $url
    exit
} catch {}

# 웹사이트가 준비되면 브라우저를 자동으로 연다
$open = "for(`$i=0;`$i -lt 60;`$i++){ try { Invoke-WebRequest '$url' -UseBasicParsing -TimeoutSec 30 | Out-Null; Start-Process '$url'; break } catch { Start-Sleep 2 } }"
Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', $open

Write-Host ''
Write-Host '  웹사이트를 켜는 중입니다. 잠시 뒤 브라우저가 자동으로 열립니다.'
Write-Host '  다 보고 나면 이 창을 닫으세요. (창을 닫으면 웹사이트도 꺼집니다)'
Write-Host ''
npm run dev
Read-Host '웹사이트가 멈췄습니다. 엔터를 누르면 창이 닫힙니다'
