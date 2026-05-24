import json
import os
import time
import uuid
import json
import os
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

def load_code_lessons(task, max_items=5):
    path = "code_lessons.jsonl"

    if not os.path.exists(path):
        return ""

    wanted_type = detect_task_type(task)
    lessons = []

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in reversed(lines):
            if len(lessons) >= max_items:
                break

            try:
                item = json.loads(line)
                old_task = item.get("task", "")
                old_type = detect_task_type(old_task)

                if old_type != wanted_type:
                    continue

                problems = item.get("problems", [])
                must_fix = item.get("must_fix", [])

                if problems:
                    lessons.append("Kesalahan lama: " + "; ".join(problems[:3]))

                if must_fix:
                    lessons.append("Wajib diperbaiki: " + "; ".join(must_fix[:3]))

            except Exception:
                continue

    except Exception:
        return ""

    if not lessons:
        return ""

    lessons = list(reversed(lessons))

    return "\nPELAJARAN KHUSUS UNTUK TIPE TUGAS INI:\n" + "\n".join(
        f"- {lesson}" for lesson in lessons
    )

def get_task_rules(task):
    task_type = detect_task_type(task)

    if task_type == "txt_file":
        return """
ATURAN KHUSUS TUGAS TXT:
- Ini tugas file teks biasa, bukan JSON dan bukan JSONL.
- DILARANG import json.
- DILARANG memakai json.loads.
- DILARANG memakai json.dumps.
- DILARANG memakai json.dump.
- DILARANG memakai input_folder.
- DILARANG scan folder dengan iterdir/glob.
- Baca satu file input.txt atau argumen input file.
- Simpan hasil ke clean.txt.
- Baris kosong dicek dengan line.strip() == "".
- Hapus duplikat memakai seen = set() dan list hasil agar urutan tetap.
- Output clean.txt harus berisi teks biasa, bukan JSON.
"""

    if task_type == "jsonl":
        return """
ATURAN KHUSUS TUGAS JSONL:
- JSONL harus dibaca baris per baris.
- Gunakan json.loads(line) atau json.loads(line.strip()).
- Jangan pakai json.load(file) untuk JSONL.
- Output JSONL harus satu JSON per baris.
"""

    if task_type == "curl_to_requests":
        return """
ATURAN KHUSUS TUGAS CURL:
- Ubah curl menjadi script Python requests.
- Ambil URL, method, headers, cookie, dan body dari curl.
- Jangan mengarang token/header yang tidak ada.
- Gunakan timeout.
- Cetak status_code dan response text/json.
"""

    return ""

def build_coder_prompt(user_task, feedback=None, previous_code=None):
    lessons = load_code_lessons(user_task)
    task_rules = get_task_rules(user_task)
    task_type = detect_task_type(user_task)

    extra = ""

    if feedback:
        extra += """
ATURAN REVISI PENTING:
- Jangan mengubah tugas TXT menjadi JSONL.
- Jika kode pertama sudah memakai teks biasa, pertahankan arah itu.
- Perbaiki bug kecil saja, jangan ganti pendekatan menjadi JSON/JSONL.
- Revisi tidak boleh menambahkan import json jika tugas tidak menyebut JSON.
"""
        for item in feedback:
            extra += f"- {item}\n"

        extra += """
PERINTAH REVISI:
- Jangan menambal kode lama jika arahnya sudah salah.
- Jika kode sebelumnya memakai pola yang dilarang, tulis ulang dari nol.
- Ikuti tipe tugas user, bukan pola tugas sebelumnya.
"""

    # Jangan masukkan kode lama terlalu panjang karena bisa membuat Kumar meniru kesalahan lama.
    if previous_code:
        extra += "\nCATATAN: Kode sebelumnya salah. Jangan ditiru jika bertentangan dengan aturan tugas.\n"

    return f"""Kamu adalah Kumar Coder.
Tugasmu menulis kode Python yang sesuai persis dengan permintaan user.

TIPE TUGAS TERDETEKSI:
{task_type}

{task_rules}

{lessons}

TUGAS USER:
{user_task}

{extra}

FORMAT JAWABAN:
Tulis KODE PYTHON SAJA.
Jangan pakai markdown.
Jangan pakai ```python.
Jangan menulis penjelasan panjang.
Jangan menulis "Aturan", "Contoh Penggunaan", atau "Kesimpulan".

ATURAN UMUM:
- Kode harus bisa langsung dijalankan.
- Jika tugas meminta CLI, gunakan argparse.
- Pakai encoding="utf-8" saat membaca atau menulis file teks.
- Jangan memakai pola JSON/JSONL kecuali user jelas menyebut JSON atau JSONL.
- Jangan scan folder kecuali user jelas meminta folder atau semua file dalam folder.
- Jangan mengarang nama file lain jika user sudah menyebut nama file tertentu.
- Print statistik sesuai permintaan user.

KODE PYTHON:
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

def validate_code(task, code):
    warnings = []
    
    task_lower = (task or "").lower()
    code_text = code or ""
    code_lower = code_text.lower()

    task_type = detect_task_type(task)

    if task_type == "txt_file":
        forbidden = []

        if "import json" in code_text:
            forbidden.append("Kode TXT tidak boleh import json.")

        if "json.loads" in code_text or "json.dumps" in code_text or "json.dump" in code_text:
            forbidden.append("Kode TXT tidak boleh memakai json.loads/json.dumps/json.dump.")

        if "jsonl" in code_lower:
            forbidden.append("Kode TXT tidak boleh membawa pola JSONL.")

        if "input_folder" in code_text or ".glob(" in code_text or ".iterdir(" in code_text:
            forbidden.append("Tugas TXT ini harus membaca satu file input.txt, bukan folder.")

        if forbidden:
            warnings.extend(forbidden)

    if "jsonl" in task_lower:
        if "json.load(" in code_text:
            warnings.append(
                "Kode memakai json.load(), padahal JSONL harus dibaca baris per baris dengan json.loads(line)."
            )

        if "json.loads(line)" not in code_text and "json.loads(line.strip())" not in code_text:
            warnings.append(
                "Kode belum memakai json.loads(line) atau json.loads(line.strip()) untuk membaca JSONL."
            )

        if "json.dump(" in code_text and "\\n" not in code_text:
            warnings.append(
                "Output JSONL belum menulis newline. Gunakan output.write(json.dumps(data, ensure_ascii=False) + '\\n')."
            )

    if "argparse" in task_lower:
        if "argparse" not in code_text:
            warnings.append(
                "Kode belum memakai argparse, padahal user meminta CLI."
            )

    if "semua file" in task_lower or "folder" in task_lower:
        has_folder_scan = (
            "os.listdir" in code_text
            or ".glob(" in code_text
            or "glob.glob" in code_text
            or ".iterdir(" in code_text
        )

        if not has_folder_scan:
            warnings.append(
                "Kode belum mencari semua file dalam folder."
            )

    if "output.jsonl" in task_lower or "gabungkan" in task_lower:
        has_output_write = (
            "open(" in code_text
            and ("'w'" in code_text or '"w"' in code_text or "'a'" in code_text or '"a"' in code_text)
        )

        if not has_output_write:
            warnings.append(
                "Kode belum terlihat menulis hasil gabungan ke file output."
            )

    if "total file" in task_lower:
        if "total_files" not in code_text and "total_file" not in code_text:
            warnings.append(
                "Kode belum menghitung total file."
            )

    if "valid" in task_lower:
        if "valid" not in code_text.lower():
            warnings.append(
                "Kode belum menghitung total baris valid."
            )

    if "rusak" in task_lower or "invalid" in task_lower:
        if "rusak" not in code_text.lower() and "invalid" not in code_text.lower() and "bad" not in code_text.lower():
            warnings.append(
                "Kode belum menghitung total baris rusak/invalid."
            )

    if 'open(file_path, "w"' in code_text or "open(file_path, 'w'" in code_text:
        warnings.append(
            "Berbahaya: kode membuka file input dengan mode write."
        )

    return warnings

def basic_code_warning(task, code):
    warnings = []

    task_lower = task.lower()

    if "jsonl" in task_lower:
        if "json.load(" in code:
            warnings.append(
                "PERINGATAN: Kode memakai json.load(), padahal JSONL harus dibaca baris per baris dengan json.loads(line)."
            )

        if "json.loads(line)" not in code and "json.loads(line.strip())" not in code:
            warnings.append(
                "PERINGATAN: Kode belum terlihat memakai json.loads(line) atau json.loads(line.strip()) untuk membaca JSONL."
            )
        if "output.jsonl" in task_lower or "jsonl" in task_lower:
            if "json.dump(" in code and "\\n" not in code:
                warnings.append(
                    "PERINGATAN: Output JSONL harus menulis newline tiap data. Gunakan out.write(json.dumps(data, ensure_ascii=False) + '\\n')."
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
    "max_tokens": 1200,
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

    with urllib.request.urlopen(req, timeout=300) as resp:
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

def detect_task_type(task):
    task_lower = (task or "").lower()

    if "curl" in task_lower:
        return "curl_to_requests"

    if "jsonl" in task_lower:
        return "jsonl"

    if ".txt" in task_lower or "input.txt" in task_lower or "clean.txt" in task_lower:
        return "txt_file"

    if "csv" in task_lower:
        return "csv"

    if "requests" in task_lower or "url" in task_lower:
        return "http_requests"

    if "config.json" in task_lower:
        return "json_config"

    return "general_python"

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
        result = f"""# SOURCE: openai_local_error
    # Backend model lokal belum aktif atau tidak bisa dihubungi.
    # Backend: {CODER_BACKEND}
    # Error: {e}
    # 
    # Dummy fallback dimatikan agar Kumar tidak belajar dari jawaban palsu.
    """
        save_code_memory(
            prompt,
            result,
            mode="openai_local_error",
            meta={"error": str(e)}
        )
        return result

    except Exception as e:
        result = f"""# SOURCE: openai_local_error
    # Backend lokal gagal menjawab.
    # Error: {e}
    """
        save_code_memory(
            prompt,
            result,
            mode="openai_local_error",
            meta={"error": str(e)}
        )
        return result