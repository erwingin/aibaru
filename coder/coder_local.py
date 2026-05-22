import json
import os
import time
import uuid
import urllib.request
import urllib.error


CODE_MEMORY_FILE = "code_memory.jsonl"

CODER_BACKEND = os.environ.get("CODER_BACKEND", "dummy")
CODER_MODEL = os.environ.get("CODER_MODEL", "local-coder")

# Untuk llama-cpp-python server / llama.cpp server OpenAI compatible
CODER_OPENAI_URL = os.environ.get(
    "CODER_OPENAI_URL",
    "http://127.0.0.1:8000/v1/chat/completions"
)

# Untuk Ollama
OLLAMA_URL = os.environ.get(
    "OLLAMA_URL",
    "http://127.0.0.1:11434/api/generate"
)


def make_id(prefix="code"):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    random_part = uuid.uuid4().hex[:8]
    return f"{prefix}_{timestamp}_{random_part}"


def save_code_memory(task, result, mode="unknown", meta=None):
    item = {
        "schema_version": "code_1.0",
        "id": make_id("code_memory"),
        "record_type": "code_result",
        "time": time.time(),
        "mode": mode,
        "model": CODER_MODEL,
        "task": task,
        "result": result,
        "meta": meta or {}
    }

    with open(CODE_MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def build_coder_prompt(user_task):
    return f"""TUGAS:
{user_task}

FORMAT JAWABAN:
Tulis KODE PYTHON SAJA.
Jangan pakai markdown.
Jangan pakai ```python.
Jangan menulis penjelasan panjang.
Jangan menulis "Aturan", "Contoh Penggunaan", atau "Kesimpulan".

ATURAN KODE:
- Kode harus bisa langsung dijalankan.
- Pakai encoding="utf-8" saat membaca file.
- Jika membaca JSONL, baca baris per baris.
- Untuk JSONL, gunakan json.loads(line), bukan json.load(file).
- Jika ada baris rusak, skip baris itu dengan try/except.
- Print total data valid jika diminta.

KODE:
"""

def clean_code_output(text):
    text = text.strip()

    if "```python" in text:
        text = text.split("```python", 1)[1]
        if "```" in text:
            text = text.split("```", 1)[0]

    elif "```" in text:
        text = text.split("```", 1)[1]
        if "```" in text:
            text = text.split("```", 1)[0]

    # Potong kalau model mulai menjelaskan setelah kode
    stop_markers = [
        "\nUntuk menjalankan",
        "\n###",
        "\nAturan:",
        "\nContoh",
        "\nKesimpulan",
    ]

    for marker in stop_markers:
        if marker in text:
            text = text.split(marker, 1)[0]

    return text.strip()

def basic_code_warning(task, code):
    warnings = []

    task_lower = task.lower()

    if "jsonl" in task_lower:
        if "json.load(" in code:
            warnings.append(
                "PERINGATAN: Kode memakai json.load(), padahal JSONL harus dibaca baris per baris dengan json.loads(line)."
            )

        if "json.loads(line)" not in code:
            warnings.append(
                "PERINGATAN: Kode belum terlihat memakai json.loads(line) untuk membaca JSONL."
            )

    if warnings:
        return "\n\n# " + "\n# ".join(warnings)

    return ""

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
# Backend model coder lokal belum aktif.

def main():
    print("Tugas diterima, tapi backend model coder belum aktif.")


if __name__ == "__main__":
    main()
"""


def ask_openai_local(prompt):
    payload = {
    "model": CODER_MODEL,
    "messages": [
        {
            "role": "system",
            "content": "Kamu adalah model coding lokal. Jawab dengan kode yang benar, ringkas, dan langsung bisa dijalankan. Jangan mengulang instruksi user."
        },
        {
            "role": "user",
            "content": build_coder_prompt(prompt)
        }
    ],
    "temperature": 0.2,
    "top_p": 0.85,
    "max_tokens": 700,
    "repeat_penalty": 1.12,
}

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        CODER_OPENAI_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer local"
        },
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read().decode("utf-8")
        result = json.loads(raw)

    return result["choices"][0]["message"]["content"].strip()


def ask_ollama(prompt):
    payload = {
        "model": CODER_MODEL,
        "prompt": build_coder_prompt(prompt),
        "stream": False,
        "options": {
            "temperature": 0.2,
            "top_p": 0.9
        }
    }

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL,
        data=data,
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read().decode("utf-8")
        result = json.loads(raw)

    return result.get("response", "").strip()


def ask_coder(prompt):
    try:
        if CODER_BACKEND == "openai_local":
            result = ask_openai_local(prompt)
            result = clean_code_output(result)
            result = result + basic_code_warning(prompt, result)
            save_code_memory(prompt, result, mode="openai_local")
            return result

        if CODER_BACKEND == "ollama":
            result = ask_ollama(prompt)
            result = clean_code_output(result)
            result = result + basic_code_warning(prompt, result)
            save_code_memory(prompt, result, mode="ollama")
            return result

        result = dummy_code_answer(prompt)
        save_code_memory(prompt, result, mode="dummy")
        return result

    except urllib.error.URLError as e:
        result = (
            "[Kumar Coder] Backend model lokal belum aktif atau tidak bisa dihubungi.\n"
            f"Backend: {CODER_BACKEND}\n"
            f"Error: {e}\n\n"
            "Fallback ke dummy backend:\n\n"
            + dummy_code_answer(prompt)
        )
        save_code_memory(prompt, result, mode="fallback_dummy", meta={"error": str(e)})
        return result

    except Exception as e:
        result = (
            "[Kumar Coder] Terjadi error saat memanggil backend.\n"
            f"Backend: {CODER_BACKEND}\n"
            f"Error: {e}\n\n"
            "Fallback ke dummy backend:\n\n"
            + dummy_code_answer(prompt)
        )
        save_code_memory(prompt, result, mode="fallback_dummy", meta={"error": str(e)})
        return result