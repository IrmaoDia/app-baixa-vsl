$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExe = Join-Path $PSScriptRoot '.runtime/python/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    New-Item -ItemType Directory -Force -Path '.runtime' | Out-Null
    Write-Host 'Preparando Python local...'
    Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.13.9/python-3.13.9-embed-amd64.zip' -OutFile '.runtime/python.zip'
    Expand-Archive -LiteralPath '.runtime/python.zip' -DestinationPath '.runtime/python' -Force
    Set-Content -LiteralPath '.runtime/python/python313._pth' -Value "python313.zip`n.`n../../`nimport site"
}
& $pythonExe -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '.runtime/get-pip.py'
    & $pythonExe '.runtime/get-pip.py'
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao preparar pip.' }
}
$dependencyHash = (Get-FileHash -LiteralPath 'requirements.txt').Hash
$markerPath = Join-Path $PSScriptRoot '.runtime/dependencies.txt'
if (-not (Test-Path -LiteralPath $markerPath) -or (Get-Content -LiteralPath $markerPath -Raw).Trim() -ne $dependencyHash) {
    & $pythonExe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependências.' }
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PSScriptRoot '.runtime/browsers'
    & $pythonExe -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao preparar navegador para páginas dinâmicas.' }
    Set-Content -LiteralPath $markerPath -Value $dependencyHash
}
Write-Host 'VideoScope: http://127.0.0.1:8765 (Ctrl+C para encerrar)'
Start-Process 'http://127.0.0.1:8765'
& $pythonExe -m uvicorn app:app --host 127.0.0.1 --port 8765
