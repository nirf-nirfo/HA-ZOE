#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH=/data/options.json

export ANTHROPIC_API_KEY="$(jq -r '.anthropic_api_key' "$CONFIG_PATH")"
export HA_BASE_URL="http://192.168.10.150:8123"
export HA_LONG_LIVED_TOKEN="$(jq -r '.ha_long_lived_token' "$CONFIG_PATH")"
export WHATSAPP_PHONE_NUMBER_ID="$(jq -r '.whatsapp_phone_number_id' "$CONFIG_PATH")"
export WHATSAPP_ACCESS_TOKEN="$(jq -r '.whatsapp_access_token' "$CONFIG_PATH")"
export WHATSAPP_VERIFY_TOKEN="$(jq -r '.whatsapp_verify_token' "$CONFIG_PATH")"
export WHATSAPP_APP_SECRET="$(jq -r '.whatsapp_app_secret' "$CONFIG_PATH")"
export ALLOWED_SENDER_NUMBERS="$(jq -r '.allowed_sender_numbers' "$CONFIG_PATH")"
export CONFIRMATION_TTL_SECONDS="$(jq -r '.confirmation_ttl_seconds' "$CONFIG_PATH")"
export WHISPER_HOST="$(jq -r '.whisper_host' "$CONFIG_PATH")"
export WHISPER_PORT="$(jq -r '.whisper_port' "$CONFIG_PATH")"
export TRANSCRIBE_BACKEND="$(jq -r '.transcribe_backend' "$CONFIG_PATH")"
export OPENAI_API_KEY="$(jq -r '.openai_api_key' "$CONFIG_PATH")"
export EMAIL_IMAP_HOST="$(jq -r '.email_imap_host' "$CONFIG_PATH")"
export EMAIL_IMAP_PORT="$(jq -r '.email_imap_port' "$CONFIG_PATH")"
export EMAIL_ADDRESS="$(jq -r '.email_address' "$CONFIG_PATH")"
export EMAIL_PASSWORD="$(jq -r '.email_password' "$CONFIG_PATH")"
export EMAIL_AUTO_EXTRACT="$(jq -r '.email_auto_extract' "$CONFIG_PATH")"
export MODEL_ROUTING_HYBRID="$(jq -r '.model_routing_hybrid' "$CONFIG_PATH")"
export BRIEFING_MODEL_COMPOSE="$(jq -r '.briefing_model_compose' "$CONFIG_PATH")"
export ENTITIES_CONFIG_PATH="/app/config/entities.yaml"

cd /app
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
