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
    """
    Deteksi tipe task secara aman.

    Penting:
    - curl yang hanya minta URL jangan masuk curl_to_requests.
    - curl yang minta method/header/cookie/body tapi tidak minta output.py
      masuk curl_analyze_structure.
    - curl_to_requests hanya untuk generate kode requests / output.py.
    """

    def _extract_original_task_local(text):
        text = str(text or "")

        marker = "TUGAS USER ASLI:"
        if marker not in text:
            return text.strip()

        part = text.split(marker, 1)[1]

        stop_markers = [
            "PLAN SEBELUMNYA DITOLAK.",
            "Kesalahan plan:",
            "Peringatan:",
            "Buat ulang TASK PLAN.",
        ]

        for stop in stop_markers:
            if stop in part:
                part = part.split(stop, 1)[0]

        return part.strip()

    task = _extract_original_task_local(task)
    task_lower = (task or "").lower()

    if "curl" in task_lower:
        wants_url_only = (
            "tampilkan url" in task_lower
            or "ambil url" in task_lower
            or "ekstrak url" in task_lower
            or "print url" in task_lower
            or "menampilkan url" in task_lower
        )

        wants_analyze_structure = (
            "method" in task_lower
            and "url" in task_lower
            and (
                "total headers" in task_lower
                or "total header" in task_lower
                or "authorization" in task_lower
                or "cookie" in task_lower
                or "body" in task_lower
            )
            and "output.py" not in task_lower
            and "generate kode python requests" not in task_lower
            and "ubah menjadi script python requests" not in task_lower
            and "ubah menjadi kode python requests" not in task_lower
            and "script python requests" not in task_lower
        )

        wants_requests_converter = (
            "ubah menjadi script python requests" in task_lower
            or "ubah menjadi kode python requests" in task_lower
            or "generate kode python requests" in task_lower
            or "script python requests" in task_lower
            or "output.py" in task_lower
        )

        if wants_analyze_structure:
            return "curl_analyze_structure"

        if wants_url_only and not wants_requests_converter:
            return "curl_extract_url"

        if wants_requests_converter:
            return "curl_to_requests"

        return "curl_general"

    # TXT dicek sebelum JSON/JSONL agar tugas input.txt tidak kebawa JSONL.
    if (
        "input.txt" in task_lower
        or "clean.txt" in task_lower
        or ".txt" in task_lower
        or "baris kosong" in task_lower
        or "duplikat" in task_lower
        or "hapus baris kosong" in task_lower
        or "hapus duplikat" in task_lower
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

def extract_original_task(text):
    text = str(text or "")

    marker = "TUGAS USER ASLI:"
    if marker not in text:
        return text.strip()

    part = text.split(marker, 1)[1]

    stop_markers = [
        "PLAN SEBELUMNYA DITOLAK.",
        "Kesalahan plan:",
        "Peringatan:",
        "Buat ulang TASK PLAN.",
    ]

    for stop in stop_markers:
        if stop in part:
            part = part.split(stop, 1)[0]

    return part.strip()

def ask_task_plan(user_task):
    """
    Planner stabil dan deterministic.
    Ini tidak memakai LLM, supaya plan tidak rusak sebelum coding.
    """
    original_task = extract_original_task(user_task)
    task_lower = (original_task or "").lower()
    task_type = detect_task_type(original_task)

    input_file = "tidak disebut"
    output_file = "tidak disebut"

    if "input.txt" in task_lower:
        input_file = "input.txt"

    if "clean.txt" in task_lower:
        output_file = "clean.txt"
    
    if "curl.txt" in task_lower:
        input_file = "curl.txt"

    if task_type == "curl_extract_url":
        output_file = "stdout"

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

    elif task_type == "curl_extract_url":
        must_use.extend([
            "argparse",
            "shlex.split",
        ])

        must_not_use.extend([
            "requests",
            "requests.get",
            "requests.post",
            "requests.request",
            "menjalankan request asli",
            "output.py",
            "shlex.split per baris",
            "command.split()[1]",
            "replace('\\\\n', ' ')",
            "posix=False",
            "hanya --input tanpa positional input_file",
        ])

        must_do.extend([
            "buat argumen input_file sebagai positional argument agar bisa dijalankan: python script.py curl.txt",
            "baca seluruh isi file curl.txt dengan file.read() sebagai satu string",
            "gabungkan curl multiline memakai ' '.join(curl_content.splitlines())",
            "pecah command gabungan memakai shlex.split dengan default atau posix=True",
            "setelah shlex.split, iterasi semua token/argumen",
            "ambil URL dengan mencari token yang diawali http:// atau https://",
            "tampilkan URL ke stdout",
        ])
    elif task_type == "curl_analyze_structure":
        must_use.extend([
            "argparse",
            "shlex.split",
        ])

        must_not_use.extend([
            "requests",
            "requests.get",
            "requests.post",
            "requests.request",
            "menjalankan request asli",
            "output.py",
            "command.split()[1]",
            "replace('\\\\n', ' ')",
            "posix=False",
            "shlex.split per baris",
        ])

        must_do.extend([
            "buat argumen input_file sebagai positional argument agar bisa dijalankan: python script.py curl.txt",
            "baca seluruh isi file curl.txt dengan file.read() sebagai satu string",
            "gabungkan curl multiline memakai ' '.join(curl_content.splitlines())",
            "pecah command gabungan memakai shlex.split dengan default atau posix=True",
            "setelah shlex.split, iterasi semua token/argumen",
            "ambil method dari -X atau --request, default GET, dan POST jika ada --data-raw atau --data",
            "ambil URL dengan mencari token yang diawali http:// atau https://",
            "hitung total headers dari jumlah pasangan -H atau --header",
            "cek apakah ada authorization dari header authorization",
            "cek apakah ada cookie dari -b, --cookie, atau header cookie",
            "cek apakah ada body dari --data-raw, --data, atau -d",
            "tampilkan method, url, total headers, apakah ada authorization, apakah ada cookie, apakah ada body ke stdout",
        ])

    elif task_type == "curl_to_requests":
        must_use.extend([
            "requests",
            "argparse",
            "shlex.split",
            "headers",
            "cookies jika ada",
            "timeout",
        ])

        must_not_use.extend([
            "mengarang token",
            "menyimpan secret asli ke memory",
            "menjalankan request asli saat converter berjalan",
            "command.split()[1]",
            "replace('\\\\n', ' ')",
            "posix=False",
            "shlex.split per baris",
        ])

        must_do.extend([
            "buat argumen input_file sebagai positional argument agar bisa dijalankan: python script.py curl.txt",
            "baca seluruh isi file curl.txt dengan file.read() sebagai satu string",
            "gabungkan curl multiline memakai ' '.join(curl_content.splitlines())",
            "pecah command gabungan memakai shlex.split dengan default atau posix=True",
            "ambil URL dengan mencari token yang diawali http:// atau https://",
            "ambil method dari -X atau --request, default GET, dan POST jika ada body",
            "ambil headers dari pasangan -H",
            "ambil cookie jika ada -b atau header cookie",
            "ambil body dari --data-raw jika ada",
            "simpan hasil generate ke output.py",
            "converter hanya membuat kode requests, jangan menjalankan request asli",
            "tampilkan ringkasan: method, url, total headers, apakah ada cookie, apakah ada body",
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
    if task_type == "curl_extract_url":
        task_plan = """TASK_TYPE: curl_extract_url
INPUT: curl.txt
OUTPUT: stdout
MUST_USE: argparse, shlex.split
MUST_NOT_USE: requests, requests.get, requests.post, requests.request, menjalankan request asli, output.py
MUST_DO: baca curl.txt, pecah perintah curl dengan shlex.split, ambil URL http/https, tampilkan URL ke stdout
NOTES: Tugas ini hanya ekstrak URL dari curl. Jangan convert ke requests. Jangan buat output.py."""
    return task_plan

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

    r = requests.post(CODER_OPENAI_URL, json=payload, timeout=600)
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
    # ============================================================
# MEMORY-FIRST OVERRIDE
# Kumar membaca pengalaman lama sebelum coding.
# Ini bukan aturan TXT/JSON manual.
# Ini mengambil pelajaran dari code_lessons.jsonl dan code_mistakes.jsonl.
# ============================================================

import os as _memory_os
import json as _memory_json
import re as _memory_re


def _memory_tokens(text):
    text = (text or "").lower()
    return set(_memory_re.findall(r"[a-z0-9_./-]+", text))


def _memory_to_text(item):
    parts = []

    for key in [
        "task",
        "mistake",
        "bad_pattern",
        "fix_rule",
        "test_signal",
        "notes",
        "verdict",
    ]:
        value = item.get(key)
        if value:
            parts.append(str(value))

    for key in ["problems", "must_fix"]:
        value = item.get(key)
        if isinstance(value, list):
            parts.extend(str(x) for x in value)
        elif value:
            parts.append(str(value))

    return "\n".join(parts)


def _extract_plan_field(text, field_name):
    field_name = field_name.lower().strip()

    for line in (text or "").splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue

        if line_clean.lower().startswith(field_name + ":"):
            return line_clean.split(":", 1)[1].strip()

    return ""


def _extract_must_not_use(text):
    raw = _extract_plan_field(text, "MUST_NOT_USE")
    if not raw:
        return []

    items = []

    for part in raw.split(","):
        part = part.strip().lower()
        if part:
            items.append(part)

    return items


def load_relevant_experience(user_task, max_items=5):
    """
    Mengambil pengalaman yang relevan dari memory.
    Tidak memakai aturan per tipe tugas.
    Sistem ini memilih berdasarkan kemiripan task + plan.
    """

    current_text = user_task or ""
    current_tokens = _memory_tokens(current_text)
    forbidden_terms = _extract_must_not_use(current_text)

    memory_files = [
        ("code_lessons.jsonl", "lesson"),
        ("code_mistakes.jsonl", "mistake"),
    ]

    candidates = []

    for path, source in memory_files:
        if not _memory_os.path.exists(path):
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            continue

        for line in lines:
            try:
                item = _memory_json.loads(line)
            except Exception:
                continue

            old_task = str(item.get("task", "") or "")
            old_task_lower = old_task.lower()

            # Kalau plan sekarang melarang sesuatu,
            # jangan ambil pengalaman dari tugas lama yang memang meminta hal terlarang itu.
            # Contoh: plan sekarang MUST_NOT_USE jsonl,
            # maka lesson dari tugas lama yang memang meminta jsonl tidak dipakai.
            conflict = False
            for forbidden in forbidden_terms:
                forbidden = forbidden.strip().lower()
                if not forbidden:
                    continue

                if forbidden in old_task_lower:
                    conflict = True
                    break

            if conflict:
                continue

            item_text = _memory_to_text(item)
            item_tokens = _memory_tokens(item_text)

            if not item_tokens:
                continue

            overlap = len(current_tokens & item_tokens)

            # Tambah bobot kecil kalau task lama mirip task sekarang.
            task_overlap = len(_memory_tokens(old_task) & current_tokens)

            score = overlap + (task_overlap * 2)

            if score <= 0:
                continue

            candidates.append({
                "score": score,
                "source": source,
                "item": item,
                "text": item_text,
            })

    candidates.sort(key=lambda x: x["score"], reverse=True)

    selected = candidates[:max_items]

    if not selected:
        return "PENGALAMAN RELEVAN DARI MEMORY:\n- Belum ada pengalaman relevan. Kerjakan berdasarkan task plan dan hasil runtime test."

    lines = ["PENGALAMAN RELEVAN DARI MEMORY:"]

    for idx, cand in enumerate(selected, start=1):
        item = cand["item"]

        problems = item.get("problems", [])
        must_fix = item.get("must_fix", [])

        if not isinstance(problems, list):
            problems = [str(problems)]

        if not isinstance(must_fix, list):
            must_fix = [str(must_fix)]

        mistake = item.get("mistake", "")
        fix_rule = item.get("fix_rule", "")
        notes = item.get("notes", "")

        lines.append(f"\nPengalaman {idx}:")

        if mistake:
            lines.append(f"- Kesalahan lama: {mistake}")

        for problem in problems[:3]:
            if problem:
                lines.append(f"- Masalah lama: {problem}")

        if fix_rule:
            lines.append(f"- Cara menghindari: {fix_rule}")

        for fix in must_fix[:3]:
            if fix:
                lines.append(f"- Wajib diperbaiki: {fix}")

        if notes:
            lines.append(f"- Catatan: {notes}")

    return "\n".join(lines)


def build_coder_prompt(user_task, feedback=None, previous_code=None):
    """
    Prompt baru:
    - Tidak menambah aturan TXT/JSON manual.
    - Mengandalkan task plan + pengalaman memory.
    """

    experiences = load_relevant_experience(user_task, max_items=5)

    extra = ""

    if feedback:
        extra += "\nFEEDBACK TERBARU YANG HARUS DIPERBAIKI:\n"
        for item in feedback:
            extra += f"- {item}\n"

    if previous_code:
        extra += """
KODE SEBELUMNYA ADA, TAPI JANGAN DITIRU BUTA-BUTA.
Kalau kode lama bertentangan dengan task plan atau memory, tulis ulang dari nol.
"""

    return f"""Kamu adalah Kumar Coder.

Tugasmu:
- Baca task user.
- Baca task plan jika ada.
- Baca pengalaman relevan dari memory.
- Tulis kode Python yang sesuai.
- Jangan mengulang kesalahan lama yang sudah muncul di memory.

TASK DAN PLAN:
{user_task}

{experiences}

{extra}

ATURAN UMUM:
- Ikuti TASK PLAN jika ada.
- Jangan melanggar MUST_NOT_USE jika ada.
- Gunakan MUST_USE jika ada.
- Kerjakan MUST_DO jika ada.
- Kode harus bisa langsung dijalankan.
- Jika tugas meminta CLI, gunakan argparse.
- Pakai encoding="utf-8" saat membaca/menulis file.
- Jangan menambahkan fitur yang tidak diminta.
- Print output/statistik sesuai permintaan user.

FORMAT JAWABAN:
Tulis KODE PYTHON SAJA.
Jangan pakai markdown.
Jangan pakai ```python.
Jangan menulis penjelasan.

KODE PYTHON:
"""
