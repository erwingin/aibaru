#!/usr/bin/env bash

MODEL=$(find models/qwen-coder-3b -name "*.gguf" | head -n 1)

if [ -z "$MODEL" ]; then
  echo "Model 3B tidak ditemukan di models/qwen-coder-3b"
  exit 1
fi

echo "Menjalankan Qwen Coder 3B:"
echo "$MODEL"

python -m llama_cpp.server \
  --model "$MODEL" \
  --host 127.0.0.1 \
  --port 8000 \
  --n_ctx 8192 \
  --n_threads 2
