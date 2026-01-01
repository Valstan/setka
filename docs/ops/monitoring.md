# Мониторинг (Prometheus/Grafana, метрики)

## /metrics

FastAPI отдаёт метрики на:

- `GET /metrics` (см. `main.py`)

## Prometheus

Конфиг в репозитории:

- `config/prometheus.yml`

Минимальная цель:
- `localhost:8000` с `metrics_path: /metrics`

## Grafana

Grafana подключается к Prometheus как data source и строит дашборды.

Примечание:
- если node_exporter не установлен на `:9100`, соответствующий target будет DOWN — это нормально.


