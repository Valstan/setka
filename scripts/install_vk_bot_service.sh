#!/usr/bin/env bash
# Установить/обновить systemd-юнит демона ВК-бота на прод-боксе.
# Запускать на боксе из корня деплоя: bash scripts/install_vk_bot_service.sh
# Пути берутся из окружения бокса, в репозитории их нет (D-038).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SETKA_USER:-$(id -un)}"
ENV_FILE="${SETKA_ENV_FILE:-/etc/setka/setka.env}"
SECRETS_FILE="${SETKA_SECRETS_FILE:-/etc/setka/secrets-token.env}"
UNIT=/etc/systemd/system/setka-vk-bot.service

sed -e "s#__SETKA_ROOT__#${ROOT}#g" \
    -e "s#__USER__#${USER_NAME}#g" \
    -e "s#__ENV_FILE__#${ENV_FILE}#g" \
    -e "s#__SECRETS_FILE__#${SECRETS_FILE}#g" \
    "${ROOT}/config/setka-vk-bot.service.template" | sudo tee "${UNIT}" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now setka-vk-bot
sudo systemctl restart setka-vk-bot
sleep 3
systemctl is-active setka-vk-bot
