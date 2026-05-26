#!/usr/bin/env bash
#
# setup-monitoring.sh — установка Prometheus + Grafana на прод-VPS.
#
# Однократный запуск. Идемпотентность через apt + проверки `systemctl is-enabled`.
# Сразу копирует конфиги из репо в /etc/prometheus, /etc/grafana, /var/lib/grafana/dashboards.
#
# Запуск: ssh setka 'cd /home/valstan/SETKA && sudo bash scripts/setup-monitoring.sh'
#
# Требования прод-сервера: Ubuntu/Debian, root доступ через sudo, свободные
# порты 9090 (Prometheus) и 3000 (Grafana). Memory budget — ~500MB RAM.
#
# Что НЕ делает:
# - Не настраивает nginx-роут для Grafana (доступ из браузера предполагается
#   через SSH tunnel: `ssh -L 3000:127.0.0.1:3000 setka`).
# - Не ставит alertmanager (экономим RAM на 1.5GB VPS).
# - Не открывает порты в firewall наружу — Prometheus/Grafana слушают только
#   127.0.0.1. Это by design.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/valstan/SETKA}"
PROM_CONFIG="$REPO_DIR/monitoring/prometheus/prometheus.yml"
GRAFANA_DASHBOARD="$REPO_DIR/monitoring/grafana/dashboards/digests.json"
GRAFANA_PROV_DS="$REPO_DIR/monitoring/grafana/provisioning/datasources/prometheus.yml"
GRAFANA_PROV_DASH="$REPO_DIR/monitoring/grafana/provisioning/dashboards/setka.yml"

# Prometheus storage retention. На тонком VPS — 5 дней.
PROM_RETENTION="${PROM_RETENTION:-5d}"

step() { echo; echo "==> $*"; }

# ---------------------------------------------------------------------------
# 1. Проверка предусловий
# ---------------------------------------------------------------------------
step "Проверка предусловий"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: запускай через sudo (нужен apt + правка /etc/)" >&2
  exit 1
fi

for f in "$PROM_CONFIG" "$GRAFANA_DASHBOARD" "$GRAFANA_PROV_DS" "$GRAFANA_PROV_DASH"; do
  if [ ! -f "$f" ]; then
    echo "ERROR: не найден файл конфига: $f" >&2
    exit 1
  fi
done

# Free RAM (MB).
free_mb=$(free -m | awk '/^Mem:/ {print $7}')
if [ "$free_mb" -lt 400 ]; then
  echo "WARN: свободной памяти $free_mb MB — Prometheus + Grafana могут не влезть" >&2
fi

# Free disk (GB).
free_gb=$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')
if [ "$free_gb" -lt 3 ]; then
  echo "WARN: свободного диска $free_gb GB — retention $PROM_RETENTION может не уместиться" >&2
fi

# ---------------------------------------------------------------------------
# 2. apt install
# ---------------------------------------------------------------------------
step "Установка пакетов"

if ! command -v prometheus >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y --no-install-recommends prometheus
else
  echo "prometheus уже установлен"
fi

if ! systemctl list-unit-files grafana-server.service >/dev/null 2>&1; then
  # Grafana не в стандартных apt-репах Debian/Ubuntu — добавим официальный.
  apt-get install -y --no-install-recommends gnupg2 software-properties-common wget
  if [ ! -f /etc/apt/sources.list.d/grafana.list ]; then
    wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor > /etc/apt/keyrings/grafana.gpg
    echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
      > /etc/apt/sources.list.d/grafana.list
    apt-get update -qq
  fi
  apt-get install -y --no-install-recommends grafana
else
  echo "grafana уже установлен"
fi

# ---------------------------------------------------------------------------
# 3. Prometheus конфиг + retention
# ---------------------------------------------------------------------------
step "Prometheus: конфиг + retention $PROM_RETENTION"

install -m 0644 "$PROM_CONFIG" /etc/prometheus/prometheus.yml

# В Debian unit задаёт ARGS через /etc/default/prometheus.
if [ -f /etc/default/prometheus ]; then
  if grep -q "^ARGS=" /etc/default/prometheus; then
    sed -i "s|^ARGS=.*|ARGS=\"--storage.tsdb.retention.time=$PROM_RETENTION --web.listen-address=127.0.0.1:9090\"|" /etc/default/prometheus
  else
    echo "ARGS=\"--storage.tsdb.retention.time=$PROM_RETENTION --web.listen-address=127.0.0.1:9090\"" >> /etc/default/prometheus
  fi
fi

systemctl enable prometheus
systemctl restart prometheus

# ---------------------------------------------------------------------------
# 4. Grafana provisioning + dashboard
# ---------------------------------------------------------------------------
step "Grafana: provisioning + dashboard"

# Слушаем только 127.0.0.1, доступ через SSH tunnel (см. шапку).
if [ -f /etc/grafana/grafana.ini ]; then
  if ! grep -q "^http_addr = 127.0.0.1" /etc/grafana/grafana.ini; then
    sed -i "s|^;\?http_addr = .*|http_addr = 127.0.0.1|" /etc/grafana/grafana.ini || true
  fi
fi

install -d -m 0755 /etc/grafana/provisioning/datasources
install -d -m 0755 /etc/grafana/provisioning/dashboards
install -d -o grafana -g grafana -m 0755 /var/lib/grafana/dashboards/setka

install -m 0644 "$GRAFANA_PROV_DS" /etc/grafana/provisioning/datasources/prometheus.yml
install -m 0644 "$GRAFANA_PROV_DASH" /etc/grafana/provisioning/dashboards/setka.yml
install -m 0644 -o grafana -g grafana "$GRAFANA_DASHBOARD" /var/lib/grafana/dashboards/setka/digests.json

systemctl enable grafana-server
systemctl restart grafana-server

# ---------------------------------------------------------------------------
# 5. Проверка
# ---------------------------------------------------------------------------
step "Sanity check"

sleep 3
prom_status=$(systemctl is-active prometheus || true)
graf_status=$(systemctl is-active grafana-server || true)
echo "prometheus:    $prom_status"
echo "grafana-server: $graf_status"

if curl -sf --max-time 5 http://127.0.0.1:9090/-/healthy >/dev/null; then
  echo "Prometheus /-/healthy: OK"
else
  echo "WARN: Prometheus /-/healthy не отвечает" >&2
fi

if curl -sf --max-time 10 http://127.0.0.1:3000/api/health >/dev/null; then
  echo "Grafana /api/health: OK"
else
  echo "WARN: Grafana /api/health не отвечает (может ещё стартует)" >&2
fi

echo
echo "Готово. Доступ:"
echo "  ssh -L 3000:127.0.0.1:3000 setka"
echo "  затем http://localhost:3000  (логин admin/admin при первом входе)"
echo
echo "Дашборд: SETKA → SETKA — состояние дайджестов"
