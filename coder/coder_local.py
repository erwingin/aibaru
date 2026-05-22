import json
import os
import time
import uuid


CODE_MEMORY_FILE = "code_memory.jsonl"


def make_id(prefix="code"):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    random_part = uuid.uuid4().hex[:8]
    return f"{prefix}_{timestamp}_{random_part}"


def save_code_memory(task, result, mode="dummy"):
    item = {
        "schema_version": "code_1.0",
        "id": make_id("code_memory"),
        "record_type": "code_result",
        "time": time.time(),
        "mode": mode,
        "task": task,
        "result": result
    }

    with open(CODE_MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def dummy_code_answer(prompt):
    prompt_lower = prompt.lower()

    if "jsonl" in prompt_lower:
        return """import json

def read_jsonl(path):
    data = []

    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                data.append(json.loads(line))
            except Exception as e:
                print(f"Baris rusak {line_no}: {e}")

    return data


if __name__ == "__main__":
    hasil = read_jsonl("data.jsonl")
    print("Total data:", len(hasil))
"""

    if "requests" in prompt_lower or "api" in prompt_lower:
        return """import requests

def get_api(url):
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print("Request gagal:", e)
        return None


if __name__ == "__main__":
    data = get_api("https://example.com/api")
    print(data)
"""

    return """# Kumar Coder dummy mode
# Model coder lokal belum dipasang.
# Nanti bagian ini akan diganti dengan model kecil seperti Qwen Coder atau DeepSeek Coder.

def main():
    print("Tugas diterima, tapi backend model coder belum aktif.")


if __name__ == "__main__":
    main()
"""


def ask_coder(prompt):
    """
    Untuk sekarang ini masih dummy backend.
    Nanti fungsi ini yang akan kita sambungkan ke model coder lokal.
    """
    result = dummy_code_answer(prompt)
    save_code_memory(prompt, result, mode="dummy")
    return result