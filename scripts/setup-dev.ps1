# scripts/setup-dev.ps1 — first-time dev setup для SETKA на Windows.
#
# Что делает:
#   1. Подбирает Python: пробует py -3.11 (preferred), потом py -3.12 (=прод),
#      потом py -3 (default). Падает с понятной ошибкой, если ни один не нашёлся.
#   2. Создаёт venv в текущем каталоге, если его ещё нет.
#   3. Обновляет pip.
#   4. Устанавливает requirements.txt + pytest + pytest-asyncio + pre-commit.
#   5. Ставит pre-commit git-hook (если есть .pre-commit-config.yaml).
#   6. Прогоняет pytest --collect-only как sanity-check (~360 tests collected).
#
# Запуск:
#   .\scripts\setup-dev.ps1
#
# Идемпотентно: повторный запуск только обновит зависимости, venv не пересоздаст.
# Локально на Windows pytest гоняется, но main.py не стартует без Postgres+Redis.
# Прод и реальный запуск — на VPS через systemd.

$ErrorActionPreference = "Stop"

# Подобрать Python: 3.11 -> 3.12 -> default py (аналог setup-dev.sh fallback'а)
$candidates = @("-3.11", "-3.12", "-3")
$pyArgs = $null
$pyVersion = $null

foreach ($candidate in $candidates) {
    try {
        $version = & py $candidate --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pyArgs = $candidate
            $pyVersion = $version
            break
        }
    } catch {
        # py может вообще отсутствовать; ловим и перебираем дальше
    }
}

if ($null -eq $pyArgs) {
    Write-Host "[setup-dev] ERROR: ни py -3.11, ни py -3.12, ни py -3 не нашлись." -ForegroundColor Red
    Write-Host "[setup-dev] Установи Python с python.org (3.11 предпочтительно, 3.12 тоже OK)." -ForegroundColor Red
    exit 1
}

Write-Host "[setup-dev] using py $pyArgs ($pyVersion)" -ForegroundColor Green
if ($pyArgs -ne "-3.11") {
    Write-Host "[setup-dev] note: 3.11 предпочтительнее (исторически в .pre-commit-config), но 3.12 тоже работает." -ForegroundColor Yellow
}

if (-not (Test-Path "venv\Scripts\python.exe")) {
    Write-Host "[setup-dev] creating venv …" -ForegroundColor Cyan
    & py $pyArgs -m venv venv
} else {
    Write-Host "[setup-dev] venv already exists" -ForegroundColor Green
}

Write-Host "[setup-dev] upgrading pip …" -ForegroundColor Cyan
.\venv\Scripts\python.exe -m pip install --upgrade pip --quiet

Write-Host "[setup-dev] installing requirements + test deps + pre-commit …" -ForegroundColor Cyan
.\venv\Scripts\python.exe -m pip install -r requirements.txt pytest pytest-asyncio pre-commit --quiet

if (Test-Path ".pre-commit-config.yaml") {
    Write-Host "[setup-dev] installing pre-commit git hook …" -ForegroundColor Cyan
    .\venv\Scripts\pre-commit.exe install | Out-Null
}

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
