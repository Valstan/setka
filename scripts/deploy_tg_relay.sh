#!/usr/bin/env bash
# Деплой TG egress-relay (infra/tg_relay/worker.js) на Cloudflare Workers
# через голый CF API (без wrangler/node). Запускать НА ПРОДЕ (setka), где
# в /etc/setka/setka.env лежат CLOUDFLARE_API_TOKEN и TG_RELAY_SECRET (#008):
#
#   ssh setka "cd /home/valstan/SETKA && sudo bash scripts/deploy_tg_relay.sh"
#
# Идемпотентен: повторный запуск перезаливает скрипт той же командой.
set -euo pipefail

SCRIPT_NAME="tg-relay"
WORKER_FILE="$(dirname "$0")/../infra/tg_relay/worker.js"

TOKEN=$(grep -oP '(?<=^CLOUDFLARE_API_TOKEN=).*' /etc/setka/setka.env)
SECRET=$(grep -oP '(?<=^TG_RELAY_SECRET=).*' /etc/setka/setka.env)
[ -n "$TOKEN" ] || { echo "CLOUDFLARE_API_TOKEN not found in /etc/setka/setka.env"; exit 1; }
[ -n "$SECRET" ] || { echo "TG_RELAY_SECRET not found in /etc/setka/setka.env"; exit 1; }

API="https://api.cloudflare.com/client/v4"
ACCOUNT=$(curl -sf -H "Authorization: Bearer $TOKEN" "$API/accounts" |
    python3 -c 'import sys,json;print(json.load(sys.stdin)["result"][0]["id"])')
echo "account: $ACCOUNT"

# Загрузка module-worker'а: multipart c metadata + сам модуль.
METADATA=$(python3 - "$SECRET" <<'EOF'
import json, sys
print(json.dumps({
    "main_module": "worker.js",
    "compatibility_date": "2026-06-01",
    "bindings": [{"type": "secret_text", "name": "RELAY_SECRET", "text": sys.argv[1]}],
}))
EOF
)

curl -sf -X PUT "$API/accounts/$ACCOUNT/workers/scripts/$SCRIPT_NAME" \
    -H "Authorization: Bearer $TOKEN" \
    -F "metadata=$METADATA;type=application/json" \
    -F "worker.js=@$WORKER_FILE;type=application/javascript+module" \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);print("upload:", "OK" if d["success"] else d["errors"])'

# Включить workers.dev-маршрут (https://tg-relay.<subdomain>.workers.dev).
curl -sf -X POST "$API/accounts/$ACCOUNT/workers/scripts/$SCRIPT_NAME/subdomain" \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"enabled": true}' \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);print("subdomain:", "OK" if d["success"] else d["errors"])'

SUBDOMAIN=$(curl -sf -H "Authorization: Bearer $TOKEN" "$API/accounts/$ACCOUNT/workers/subdomain" |
    python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["subdomain"])')
echo "relay URL: https://$SCRIPT_NAME.$SUBDOMAIN.workers.dev"
