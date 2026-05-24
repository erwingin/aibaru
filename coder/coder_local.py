import json
import os
import re
import requests

CODER_BACKEND = os.environ.get("CODER_BACKEND", "dummy")
CODER_MODEL = os.environ.get("CODER_MODEL", "qwen2.5-coder-3b")
CODER_OPENAI_URL = os.environ.get(
    "CODER_OPENAI_URL",
    "http://127.0.0.1:8000/v1/chat/completions"
)

CODE_LESSONS = "code_lessons.jsonl"


def extract_code(text):
    text = text or ""
    text = text.strip()

    # Ambil isi ```python ... ``` kalau model memakai markdown.
    match = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return text


def detect_task_type(task):
    task_lower = (task or "").lower()

    if "curl" in task_lower:
        return "curl_to_requests"

    # TXT dicek sebelum JSON/JSONL agar tugas input.txt tidak kebawa JSONL.
    if (
        "input.txt" in task_lower
        or "clean.txt" in task_lower
        or ".txt" in task_lower
        or "baris kosong" in task_lower
        or "duplikat" in task_lower
    ):
        return "txt_file"

    if "jsonl" in task_lower and ("folder" in task_lower or "semua file" in task_lower):
        return "jsonl_folder_cli"

    if "jsonl" in task_lower:
        return "jsonl"

    if "csv" in task_lower:
        return "csv"

    if "requests" in task_lower or "url" in task_lower:
        return "http_requests"

    if "config.json" in task_lower:
        return "json_config"

    if "argparse" in task_lower or "cli" in task_lower:
        return "python_cli"

    return "general_python"


def load_code_lessons(task=None, max_items=5):
    if not os.path.exists(CODE_LESSONS):
        return ""

    wanted_type = detect_task_type(task or "")
    lessons = []

    try:
        with open(CODE_LESSONS, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in reversed(lines):
            if len(lessons) >= max_items:
                break

            try:
                item = json.loads(line)
            except Exception:
                continue

            old_task = item.get("task", "")
            old_type = detect_task_type(old_task)

            # Jika task kosong, jangan ambil lesson lintas tipe.
            if task and old_type != wanted_type:
                continue

            problems = item.get("problems", []) or []
            must_fix = item.get("must_fix", []) or []

            for problem in problems[:3]:
                lessons.append("Kesalahan lama: " + str(problem))

            for fix in must_fix[:3]:
                lessons.append("Wajib diperbaiki: " + str(fix))

    except Exception:
        return ""

    if not lessons:
        return ""

    lessons = list(reversed(lessons))
    return "\nPELAJARAN RELEVAN DARI PENGALAMAN KUMAR:\n" + "\n".join(
        f"- {lesson}" for lesson in lessons
    )


def ask_task_plan(user_task):
    """
    Planner stabil dan deterministic.
    Ini tidak memakai LLM, supaya plan tidak rusak sebelum coding.
    """
    task_lower = (user_task or "").lower()
    task_type = detect_task_type(user_task)

    input_file = "tidak disebut"
    output_file = "tidak disebut"

    if "input.txt" in task_lower:
        input_file = "input.txt"

    if "clean.txt" in task_lower:
        output_file = "clean.txt"

    must_use = []
    must_not_use = []
    must_do = []

    if "argparse" in task_lower or "cli" in task_lower:
        must_use.append("argparse")

    if task_type == "txt_file":
        must_use.extend([
            "open",
            "encoding=utf-8",
            "seen set untuk hapus duplikat",
            "list untuk menjaga urutan hasil",
        ])
        must_not_use.extend([
            "json",
            "jsonl",
            "json.loads",
            "json.dumps",
            "json.dump",
            "input_folder",
            "folder scan",
            "iterdir",
            "glob",
        ])
        must_do.extend([
            "baca file teks biasa",
            "hitung total baris awal",
            "hapus baris kosong dengan line.strip() == ''",
            "hapus duplikat tanpa merusak urutan",
            "simpan hasil ke clean.txt",
            "tampilkan total baris awal",
            "tampilkan total baris kosong",
            "tampilkan total duplikat",
            "tampilkan total baris akhir",
        ])

    elif task_type in ["jsonl", "jsonl_folder_cli"]:
        must_use.extend([
            "json",
            "json.loads(line.strip())",
            "try/except json.JSONDecodeError",
        ])
        must_not_use.append("json.load(file) untuk JSONL")
        must_do.extend([
            "baca JSONL baris per baris",
            "skip baris rusak",
            "tulis output satu JSON per baris",
        ])

    elif task_type == "curl_to_requests":
        must_use.extend([
            "requests",
            "headers",
            "cookies jika ada",
            "timeout",
        ])
        must_not_use.extend([
            "mengarang token",
            "menyimpan secret asli ke memory",
        ])
        must_do.extend([
            "ubah curl menjadi Python requests",
            "print status_code",
            "print response",
        ])

    else:
        must_use.append("kode Python standar")
        must_do.append("ikuti permintaan user secara ketat")

    return f"""TASK_TYPE: {task_type}
INPUT: {input_file}
OUTPUT: {output_file}
MUST_USE: {", ".join(must_use)}
MUST_NOT_USE: {", ".join(must_not_use)}
MUST_DO: {", ".join(must_do)}
NOTES: Buat kode hanya setelah plan ini dipahami. Jangan membawa pola dari tugas lain.
"""


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

    if task_type in ["jsonl", "jsonl_folder_cli"]:
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
    task_type = detect_task_type(user_task)
    task_rules = get_task_rules(user_task)
    lessons = load_code_lessons(user_task)

    extra = ""

    if feedback:
        extra += "\nKESALAHAN SEBELUMNYA YANG TIDAK BOLEH DIULANG:\n"
        for item in feedback:
            extra += f"- {item}\n"

        extra += """
PERINTAH REVISI:
- Jangan menambal kode lama jika arahnya sudah salah.
- Jika kode sebelumnya memakai pola yang dilarang, tulis ulang dari nol.
- Ikuti tipe tugas user, bukan pola tugas sebelumnya.
- Jangan mengubah tugas TXT menjadi JSONL.
- Jangan menambahkan import json jika tugas tidak menyebut JSON.
"""

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


def dummy_code_answer(prompt):
    return """# SOURCE: dummy
print("Dummy backend aktif. Jalankan dengan CODER_BACKEND=openai_local agar Kumar memakai model lokal.")
"""


def ask_openai_local(prompt):
    payload = {
        "model": CODER_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Kamu adalah coder Python. "
                    "Jawab hanya kode Python lengkap. "
                    "Jangan pakai markdown."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.1,
        "top_p": 0.9,
        "max_tokens": 1800,
        "stream": False,
    }

    r = requests.post(CODER_OPENAI_URL, json=payload, timeout=180)
    r.raise_for_status()
    data = r.json()

    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        return json.dumps(data, ensure_ascii=False, indent=2)


def ask_coder(user_task, feedback=None, previous_code=None):
    prompt = build_coder_prompt(user_task, feedback=feedback, previous_code=previous_code)

    if CODER_BACKEND == "openai_local":
        try:
            answer = ask_openai_local(prompt)
            return extract_code(answer)
        except Exception as e:
            return f"""# SOURCE: openai_local_error
# Backend lokal gagal menjawab.
# Error: {e}
"""

    if CODER_BACKEND == "dummy":
        return dummy_code_answer(prompt)

    return f"""# SOURCE: backend_error
# CODER_BACKEND tidak dikenal: {CODER_BACKEND}
"""


def validate_code(task, code):
    warnings = []

    task_lower = (task or "").lower()
    code_text = code or ""
    code_lower = code_text.lower()
    task_type = detect_task_type(task)

    if task_type == "txt_file":
        forbidden = []

        if "import json" in code_lower:
            forbidden.append("Kode TXT tidak boleh import json.")

        if "json.loads" in code_lower or "json.dumps" in code_lower or "json.dump" in code_lower:
            forbidden.append("Kode TXT tidak boleh memakai json.loads/json.dumps/json.dump.")

        if "jsonl" in code_lower:
            forbidden.append("Kode TXT tidak boleh membawa pola JSONL.")

        if "input_folder" in code_lower or ".glob(" in code_lower or ".iterdir(" in code_lower:
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

    if "argparse" in task_lower or "cli" in task_lower:
        if "argparse" not in code_text:
            warnings.append("Kode belum memakai argparse, padahal user meminta CLI.")

    if "semua file" in task_lower or "folder" in task_lower:
        has_folder_scan = (
            "os.listdir" in code_text
            or ".glob(" in code_text
            or "glob.glob" in code_text
            or ".iterdir(" in code_text
        )
        if not has_folder_scan:
            warnings.append("Kode belum mencari semua file dalam folder.")

    if "output.jsonl" in task_lower or "gabungkan" in task_lower:
        has_output_write = (
            "open(" in code_text
            and ("'w'" in code_text or '"w"' in code_text or "'a'" in code_text or '"a"' in code_text)
        )
        if not has_output_write:
            warnings.append("Kode belum terlihat menulis hasil gabungan ke file output.")

    if "total file" in task_lower:
        if "total_files" not in code_text and "total_file" not in code_text:
            warnings.append("Kode belum menghitung total file.")

    if "valid" in task_lower:
        if "valid" not in code_lower:
            warnings.append("Kode belum menghitung total baris valid.")

    if "rusak" in task_lower or "invalid" in task_lower:
        if "rusak" not in code_lower and "invalid" not in code_lower and "bad" not in code_lower:
            warnings.append("Kode belum menghitung total baris rusak/invalid.")

    if 'open(file_path, "w"' in code_text or "open(file_path, 'w'" in code_text:
        warnings.append("Berbahaya: kode membuka file input dengan mode write.")

    return warnings
