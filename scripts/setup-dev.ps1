# scripts/setup-dev.ps1 — first-time dev setup для SETKA на Windows.
#
# Что делает:
#   1. Проверяет что доступен py 3.11 (через `py -3.11`).
#   2. Создаёт venv в текущем каталоге, если его ещё нет.
#   3. Обновляет pip.
#   4. Устанавливает requirements.txt + pytest + pytest-asyncio.
#   5. Прогоняет pytest --collect-only как sanity-check (~244 tests collected).
#
# Запуск:
#   .\scripts\setup-dev.ps1
#
# Идемпотентно: повторный запуск только обновит зависимости, venv не пересоздаст.
# Локально на Windows pytest гоняется, но main.py не стартует (LOG_PATH хардкоды,
# см. PENDING_FOLLOWUPS). Прод и реальный запуск — на VPS через systemd.

$ErrorActionPreference = "Stop"

Write-Host "[setup-dev] checking py -3.11 …" -ForegroundColor Cyan
$pyVersion = & py -3.11 --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "py -3.11 is not available. Install Python 3.11 from python.org first." -ForegroundColor Red
    exit 1
}
Write-Host "[setup-dev] $pyVersion" -ForegroundColor Green

if (-not (Test-Path "venv\Scripts\python.exe")) {
    Write-Host "[setup-dev] creating venv …" -ForegroundColor Cyan
    py -3.11 -m venv venv
} else {
    Write-Host "[setup-dev] venv already exists" -ForegroundColor Green
}

Write-Host "[setup-dev] upgrading pip …" -ForegroundColor Cyan
.\venv\Scripts\python.exe -m pip install --upgrade pip --quiet

Write-Host "[setup-dev] installing requirements + test deps …" -ForegroundColor Cyan
.\venv\Scripts\python.exe -m pip install -r requirements.txt pytest pytest-asyncio --quiet

Write-Host "[setup-dev] pytest --collect-only sanity-check …" -ForegroundColor Cyan
$collected = (.\venv\Scripts\python.exe -m pytest --collect-only -q 2>&1) | Select-String "tests collected"
if (-not $collected) {
    Write-Host "[setup-dev] pytest --collect-only didn't print 'tests collected' — что-то не так" -ForegroundColor Yellow
} else {
    Write-Host "[setup-dev] $collected" -ForegroundColor Green
}

Write-Host ""
Write-Host "[setup-dev] DONE. Run tests:" -ForegroundColor Green
Write-Host "    .\venv\Scripts\python.exe -m pytest tests/ -q"
