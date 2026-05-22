#!/usr/bin/env bash

export CODER_BACKEND=openai_local
export CODER_MODEL=qwen2.5-coder-3b
export CODER_OPENAI_URL=http://127.0.0.1:8000/v1/chat/completions

python coder/run_coder.py

