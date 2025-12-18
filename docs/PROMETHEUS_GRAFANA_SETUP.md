# 📊 Prometheus + Grafana Setup для SETKA

**Полное руководство по настройке мониторинга**

---

## 🎯 Что мы получим

- ✅ Prometheus - сбор метрик каждые 15 секунд
- ✅ Grafana - красивые дашборды
- ✅ Alerting - уведомления при проблемах
- ✅ Метрики API, VK API, кэша, БД

---

## 📦 Установка Prometheus

### Шаг 1: Скачать Prometheus

```bash
cd /tmp
wget https://github.com/prometheus/prometheus/releases/download/v2.45.0/prometheus-2.45.0.linux-amd64.tar.gz
tar xvfz prometheus-2.45.0.linux-amd64.tar.gz
sudo mv prometheus-2.45.0.linux-amd64 /opt/prometheus
```

### Шаг 2: Скопировать конфигурацию

```bash
sudo cp /home/valstan/SETKA/config/prometheus.yml /opt/prometheus/prometheus.yml
```

### Шаг 3: Создать systemd service

```bash
sudo tee /etc/systemd/system/prometheus.service > /dev/null <<EOF
[Unit]
Description=Prometheus
After=network.target

[Service]
Type=simple
User=valstan
WorkingDirectory=/opt/prometheus
ExecStart=/opt/prometheus/prometheus --config.file=/opt/prometheus/prometheus.yml --storage.tsdb.path=/opt/prometheus/data
Restart=always

[Install]
WantedBy=multi-user.target
EOF
```

### Шаг 4: Запустить Prometheus

```bash
sudo systemctl daemon-reload
sudo systemctl enable prometheus
sudo systemctl start prometheus
sudo systemctl status prometheus
```

### Шаг 5: Проверить

Откройте в браузере: http://localhost:9090

Или проверьте:
```bash
curl http://localhost:9090/api/v1/targets
```

---

## 📊 Установка Grafana

### Шаг 1: Установить Grafana

```bash
sudo apt-get install -y software-properties-common
wget -q -O - https://packages.grafana.com/gpg.key | sudo apt-key add -
echo "deb https://packages.grafana.com/oss/deb stable main" | sudo tee /etc/apt/sources.list.d/grafana.list

sudo apt-get update
sudo apt-get install grafana
```

### Шаг 2: Запустить Grafana

```bash
sudo systemctl daemon-reload
sudo systemctl enable grafana-server
sudo systemctl start grafana-server
sudo systemctl status grafana-server
```

### Шаг 3: Открыть Grafana

URL: http://localhost:3000

**Логин по умолчанию:**
- Username: `admin`
- Password: `admin` (сменить при первом входе)

---

## 🔗 Подключение Prometheus к Grafana

### Шаг 1: Добавить Data Source

1. Открыть Grafana: http://localhost:3000
2. Меню → Configuration → Data Sources
3. Нажать "Add data source"
4. Выбрать "Prometheus"
5. Заполнить:
   - Name: `Prometheus`
   - URL: `http://localhost:9090`
   - Access: `Server (default)`
6. Нажать "Save & Test"

✅ Должно быть: "Data source is working"

---

## 📈 Создание дашборда

### Готовый дашборд для SETKA

Создайте новый Dashboard в Grafana и добавьте панели:

#### Panel 1: API Request Rate

```promql
# Запросов в секунду
rate(setka_api_requests_total[5m])
```

#### Panel 2: API Latency (p50, p95, p99)

```promql
# 50th percentile
histogram_quantile(0.5, rate(setka_api_request_duration_seconds_bucket[5m]))

# 95th percentile
histogram_quantile(0.95, rate(setka_api_request_duration_seconds_bucket[5m]))

# 99th percentile
histogram_quantile(0.99, rate(setka_api_request_duration_seconds_bucket[5m]))
```

#### Panel 3: Cache Hit Rate

```promql
# Cache hit rate (%)
rate(setka_cache_hits_total[5m]) / 
(rate(setka_cache_hits_total[5m]) + rate(setka_cache_misses_total[5m])) * 100
```

#### Panel 4: VK API Requests

```promql
# VK API requests per second by status
rate(setka_vk_api_requests_total[5m])
```

#### Panel 5: Error Rate

```promql
# API errors per second
rate(setka_api_requests_total{status="error"}[5m])
```

#### Panel 6: Active Communities

```promql
# Number of monitored communities
setka_communities_monitored
```

#### Panel 7: Active Regions

```promql
# Number of active regions
setka_regions_active
```

---

## 🚨 Alerting Rules

Создайте файл `/opt/prometheus/alerts.yml`:

```yaml
groups:
  - name: setka_alerts
    interval: 30s
    rules:
      # High API latency
      - alert: HighAPILatency
        expr: histogram_quantile(0.95, rate(setka_api_request_duration_seconds_bucket[5m])) > 1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High API latency detected"
          description: "95th percentile latency is {{ $value }}s"
      
      # High error rate
      - alert: HighErrorRate
        expr: rate(setka_api_requests_total{status="error"}[5m]) > 0.1
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "High API error rate"
          description: "Error rate is {{ $value }} errors/s"
      
      # Low cache hit rate
      - alert: LowCacheHitRate
        expr: |
          rate(setka_cache_hits_total[5m]) / 
          (rate(setka_cache_hits_total[5m]) + rate(setka_cache_misses_total[5m])) < 0.5
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Low cache hit rate"
          description: "Cache hit rate is {{ $value | humanizePercentage }}"
      
      # VK API rate limit
      - alert: VKRateLimitHit
        expr: rate(setka_vk_api_rate_limit_hits_total[5m]) > 0
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "VK API rate limit hit"
          description: "Rate limit exceeded"
```

Добавьте в `prometheus.yml`:
```yaml
rule_files:
  - 'alerts.yml'
```

Перезапустите Prometheus:
```bash
sudo systemctl restart prometheus
```

---

## 📱 Telegram Alerting (опционально)

### Установить Alertmanager

```bash
cd /tmp
wget https://github.com/prometheus/alertmanager/releases/download/v0.26.0/alertmanager-0.26.0.linux-amd64.tar.gz
tar xvfz alertmanager-0.26.0.linux-amd64.tar.gz
sudo mv alertmanager-0.26.0.linux-amd64 /opt/alertmanager
```

### Конфигурация для Telegram

`/opt/alertmanager/alertmanager.yml`:

```yaml
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  receiver: 'telegram'

receivers:
  - name: 'telegram'
    telegram_configs:
      - bot_token: 'YOUR_BOT_TOKEN'
        chat_id: YOUR_CHAT_ID
        parse_mode: 'HTML'
        message: |
          <b>Alert: {{ .GroupLabels.alertname }}</b>
          {{ range .Alerts }}
          Status: {{ .Status }}
          {{ .Annotations.summary }}
          {{ .Annotations.description }}
          {{ end }}
```

Добавьте в `prometheus.yml`:
```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets: ['localhost:9093']
```

---

## 📊 Полезные метрики SETKA

### API Performance

```promql
# Requests per second
rate(setka_api_requests_total[5m])

# Average latency
rate(setka_api_request_duration_seconds_sum[5m]) / 
rate(setka_api_request_duration_seconds_count[5m])

# Success rate
rate(setka_api_requests_total{status="success"}[5m]) / 
rate(setka_api_requests_total[5m]) * 100
```

### Cache Performance

```promql
# Cache hit rate
rate(setka_cache_hits_total[5m]) / 
(rate(setka_cache_hits_total[5m]) + rate(setka_cache_misses_total[5m])) * 100

# Cache hits per second
rate(setka_cache_hits_total[5m])

# Cache misses per second
rate(setka_cache_misses_total[5m])
```

### VK API

```promql
# VK API requests per second
rate(setka_vk_api_requests_total[5m])

# VK API latency
rate(setka_vk_api_request_duration_seconds_sum[5m]) / 
rate(setka_vk_api_request_duration_seconds_count[5m])

# VK API errors
rate(setka_vk_api_errors_total[5m])
```

### Business Metrics

```promql
# Active communities
setka_communities_monitored

# Active regions
setka_regions_active

# Posts processed per hour
rate(setka_posts_processed_total[1h]) * 3600
```

---

## 🎨 Готовые Grafana Dashboards

### Import готовых дашбордов:

1. **FastAPI Dashboard**: ID `14424`
   - Общие метрики FastAPI

2. **Node Exporter Dashboard**: ID `1860`
   - Системные метрики (CPU, RAM, Disk)

3. **Redis Dashboard**: ID `11835`
   - Метрики Redis

**Как импортировать:**
1. Grafana → Dashboards → Import
2. Введите ID
3. Выберите Prometheus data source
4. Нажмите "Import"

---

## 🔍 Проверка метрик

### Проверить все метрики:

```bash
curl http://localhost:8000/metrics | grep "setka_"
```

### Проверить конкретную метрику:

```bash
curl http://localhost:8000/metrics | grep "setka_api_requests_total"
```

### Prometheus UI - Targets:

http://localhost:9090/targets

Должно быть: `setka-api` - UP (зелёный)

---

## ✅ Checklist

- [ ] Prometheus установлен и запущен
- [ ] Grafana установлена и запущена
- [ ] Prometheus подключён к Grafana
- [ ] `/metrics` endpoint работает
- [ ] Создан основной дашборд
- [ ] Настроены alert rules
- [ ] (Опционально) Alertmanager настроен
- [ ] (Опционально) Telegram уведомления работают

---

## 📚 Полезные ссылки

- Prometheus: https://prometheus.io/docs/
- Grafana: https://grafana.com/docs/
- PromQL: https://prometheus.io/docs/prometheus/latest/querying/basics/
- Alertmanager: https://prometheus.io/docs/alerting/latest/alertmanager/

---

**Создано:** 11 октября 2025  
**Версия:** 1.0

🎉 **Happy Monitoring!**

