#!/usr/bin/env bash

if [ -f .env.brain ]; then
  source .env.brain
fi


# Load API key lokal kalau ada
if [ -f ".env.local" ]; then
  source ".env.local"
fi

export CODER_BACKEND="${CODER_BACKEND:-openai_local}"
export CODER_MODEL="${CODER_MODEL:-qwen2.5-coder-3b}"
export CODER_OPENAI_URL="${CODER_OPENAI_URL:-http://127.0.0.1:8000/v1/chat/completions}"

echo "🧠 Menjalankan Kumar Arena"
echo "Backend: $CODER_BACKEND"
echo "Model  : $CODER_MODEL"
echo "URL    : $CODER_OPENAI_URL"

if [ -z "$MIMO_API_KEY" ]; then
  echo "⚠️  MIMO_API_KEY belum ada."
  echo "Isi dulu file .env.local"
else
  echo "MiMo   : API key terbaca"
fi

echo

python coder/coder_arena.py