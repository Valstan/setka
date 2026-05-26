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

# Multiprocess metrics: общая папка для setka.service (uvicorn) и
# setka-celery-worker.service (Celery). Без неё counter'ы из worker'а
# не доходят до /metrics эндпоинта в web.
PROM_MULTIPROC_DIR="${PROM_MULTIPROC_DIR:-/var/lib/setka/prom_multiproc}"
SETKA_RUN_USER="${SETKA_RUN_USER:-valstan}"
SETKA_RUN_GROUP="${SETKA_RUN_GROUP:-valstan}"

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
  # Grafana с 2026 закрыл `https://apt.grafana.com/gpg.key` (403 за токен).
  # Поэтому ставим из официального `.deb` напрямую с dl.grafana.com — это
  # стабильный публичный URL без auth, поддерживается Grafana Labs.
  apt-get install -y --no-install-recommends wget
  GRAFANA_VERSION="${GRAFANA_VERSION:-11.4.0}"
  DEB="/tmp/grafana_${GRAFANA_VERSION}_amd64.deb"
  if [ ! -s "$DEB" ]; then
    wget -q -O "$DEB" "https://dl.grafana.com/oss/release/grafana_${GRAFANA_VERSION}_amd64.deb"
  fi
  apt-get install -y --no-install-recommends "$DEB"
else
  echo "grafana уже установлен"
fi

# ---------------------------------------------------------------------------
# 3. Prometheus конфиг + retention
# ---------------------------------------------------------------------------
step "Prometheus: конфиг + retention $PROM_RETENTION"

install -m 0644 "$PROM_CONFIG" /etc/prometheus/prometheus.yml

# В Debian unit задаёт ARGS через /etc/default/prometheus. Обязательно
# нужен --config.file (иначе Prometheus читает дефолтный пример и не
# скрейпит setka) и --storage.tsdb.path (иначе пишет в текущий рабочий
# каталог systemd-unit'а).
PROM_ARGS="--config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus/metrics2 --storage.tsdb.retention.time=$PROM_RETENTION --web.listen-address=127.0.0.1:9090"
if [ -f /etc/default/prometheus ]; then
  if grep -q "^ARGS=" /etc/default/prometheus; then
    sed -i "s|^ARGS=.*|ARGS=\"$PROM_ARGS\"|" /etc/default/prometheus
  else
    echo "ARGS=\"$PROM_ARGS\"" >> /etc/default/prometheus
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
# 5. Multiprocess Prometheus directory + systemd drop-in для setka/worker
# ---------------------------------------------------------------------------
step "Prometheus multiprocess: $PROM_MULTIPROC_DIR"

install -d -m 0750 -o "$SETKA_RUN_USER" -g "$SETKA_RUN_GROUP" "$PROM_MULTIPROC_DIR"

# Drop-in патчит и web (setka.service), и Celery worker. Beat ничего в метрики
# не пишет — env-var ему не нужен. `ExecStartPre=` чистит mmap-файлы от
# предыдущего PID; без чистки старый dead-процесс продолжал бы влиять на
# агрегации (особенно `digest_last_published_timestamp` с mode=max).
write_dropin() {
  local unit="$1"
  local dir="/etc/systemd/system/${unit}.d"
  install -d -m 0755 "$dir"
  cat > "$dir/prometheus-multiproc.conf" <<EOF
[Service]
Environment=PROMETHEUS_MULTIPROC_DIR=$PROM_MULTIPROC_DIR
ExecStartPre=/bin/rm -rf $PROM_MULTIPROC_DIR
ExecStartPre=/bin/mkdir -p $PROM_MULTIPROC_DIR
ExecStartPre=/bin/chown $SETKA_RUN_USER:$SETKA_RUN_GROUP $PROM_MULTIPROC_DIR
ExecStartPre=/bin/chmod 0750 $PROM_MULTIPROC_DIR
EOF
}

write_dropin "setka.service"
write_dropin "setka-celery-worker.service"

systemctl daemon-reload
systemctl restart setka setka-celery-worker

# ---------------------------------------------------------------------------
# 6. Проверка
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
