import json
import os
import re
import time
from pathlib import Path
from datetime import datetime, timezone
import difflib
import requests
import subprocess
import tempfile
from coder_local import ask_coder, validate_code


MIMO_URL = os.getenv("MIMO_URL", "https://api.xiaomimimo.com/v1/chat/completions")
MIMO_MODEL = os.getenv("MIMO_MODEL", "mimo-v2.5-pro")
MIMO_API_KEY = os.getenv("MIMO_API_KEY")

ARENA_LOG = "coder_arena_log.jsonl"
CODE_MISTAKES = "code_mistakes.jsonl"
LAST_CODE_FILE = "last_kumar_code.py"

CODE_MEMORY = "code_memory.jsonl"
CODE_LESSONS = "code_lessons.jsonl"
MIN_PASS_SCORE = 85
MAX_REVISION_ROUNDS = 1


def save_jsonl(path, data):
    data["created_at"] = datetime.now(timezone.utc).isoformat()

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")

def get_score(review):
    try:
        return int(review.get("score", 0) or 0)
    except Exception:
        return 0


def is_review_error(review):
    return review.get("verdict") == "error"


def save_code_memory(task, code, review):
    save_jsonl(CODE_MEMORY, {
        "schema": "code_memory_1.0",
        "source": "arena",
        "task": task,
        "code": code,
        "score": get_score(review),
        "verdict": review.get("verdict"),
        "notes": review.get("notes", ""),
    })

def code_similarity(a, b):
    a = (a or "").strip()
    b = (b or "").strip()

    if not a or not b:
        return 0.0

    return difflib.SequenceMatcher(None, a, b).ratio()


def is_code_too_similar(old_code, new_code, limit=0.90):
    return code_similarity(old_code, new_code) >= limit


def build_forced_rewrite_task(task, review):
    problems = review.get("problems", [])
    must_fix = review.get("must_fix", [])

    return f"""
TUGAS ASLI:
{task}

PERINTAH PENTING:
Kode sebelumnya gagal dan revisi sebelumnya terlalu mirip.
JANGAN menulis fungsi read_jsonl sederhana saja.
JANGAN hardcode data.jsonl.
TULIS ULANG PROGRAM LENGKAP DARI NOL.

KESALAHAN YANG HARUS DIHINDARI:
{json.dumps(problems, ensure_ascii=False, indent=2)}

FITUR WAJIB ADA:
{json.dumps(must_fix, ensure_ascii=False, indent=2)}

CHECKLIST WAJIB:
- import argparse
- menerima input folder, default: data
- menerima output file, default: output.jsonl
- mencari semua file .jsonl dalam folder
- membaca setiap file baris per baris
- memakai json.loads(line.strip())
- skip baris rusak dengan except json.JSONDecodeError
- menulis semua data valid ke output.jsonl
- output JSONL harus satu JSON per baris
- menghitung total file
- menghitung total baris valid
- menghitung total baris rusak
- print semua statistik di akhir
- jangan membuka file input dengan mode "w"

STRUKTUR PROGRAM:
1. parse_args()
2. process_file(file_path, output_handle)
3. main()
4. if __name__ == "__main__": main()

JAWAB HANYA KODE PYTHON LENGKAP.
Jangan pakai markdown.
Jangan beri penjelasan.
"""

def save_code_lesson(task, review):
    save_jsonl(CODE_LESSONS, {
        "schema": "code_lesson_1.0",
        "task": task,
        "score": get_score(review),
        "verdict": review.get("verdict"),
        "problems": review.get("problems", []),
        "must_fix": review.get("must_fix", []),
        "notes": review.get("notes", ""),
    })

def detect_task_type(task):
    task_lower = (task or "").lower()

    if "jsonl" in task_lower and ("folder" in task_lower or "semua file" in task_lower):
        return "jsonl_folder_cli"

    if "jsonl" in task_lower:
        return "jsonl"

    if "argparse" in task_lower or "cli" in task_lower:
        return "python_cli"

    return "general_python"

def save_code_mistake(task, code, review):
    task_type = detect_task_type(task)

    problems = review.get("problems", [])
    must_fix = review.get("must_fix", [])
    runtime_test = review.get("runtime_test", {})

    mistakes = []

    text = "\n".join(problems + must_fix + [runtime_test.get("notes", "")]).lower()

    if "jsonl" in (task or "").lower():
        if "indent" in text or "newline" in text or "valid_json=0" in text:
            mistakes.append({
                "mistake": "Output JSONL tidak valid karena tidak ditulis satu JSON per baris.",
                "bad_pattern": "json.dump(data, output_file, indent=4) atau json.dump(data, output_file) tanpa newline",
                "fix_rule": "Untuk JSONL, gunakan output.write(json.dumps(data, ensure_ascii=False) + '\\n').",
                "test_signal": "output.jsonl harus punya jumlah baris sama dengan total data valid dan setiap baris harus json.loads valid."
            })

        if "json.load(" in code:
            mistakes.append({
                "mistake": "JSONL dibaca seperti JSON biasa.",
                "bad_pattern": "json.load(file)",
                "fix_rule": "JSONL harus dibaca baris per baris dengan json.loads(line.strip()).",
                "test_signal": "File dengan satu baris rusak harus tetap memproses baris valid lainnya."
            })

    if "argparse" in (task or "").lower():
        if "argparse" in text or "unrecognized arguments" in text:
            mistakes.append({
                "mistake": "Argumen CLI tidak cocok dengan cara test/user menjalankan program.",
                "bad_pattern": "Hanya mendukung satu bentuk argumen, misalnya output_file positional saja.",
                "fix_rule": "Sediakan argumen input_folder positional dan --output opsional default output.jsonl.",
                "test_signal": "Program harus bisa dijalankan minimal dengan: python script.py data"
            })

    if "folder" in (task or "").lower() or "semua file" in (task or "").lower():
        if "total file" in text or "semua file" in text or "folder" in text:
            mistakes.append({
                "mistake": "Pemrosesan folder atau hitungan file belum tepat.",
                "bad_pattern": "Menghitung semua file di folder, bukan hanya file .jsonl yang diproses.",
                "fix_rule": "Loop hanya file dengan suffix .jsonl dan increment total_files hanya untuk file yang diproses.",
                "test_signal": "Jika folder berisi 2 file .jsonl, total_files harus 2."
            })

    for item in mistakes:
        save_jsonl(CODE_MISTAKES, {
            "schema": "code_mistake_1.0",
            "task": task,
            "task_type": task_type,
            "mistake": item["mistake"],
            "bad_pattern": item["bad_pattern"],
            "fix_rule": item["fix_rule"],
            "test_signal": item["test_signal"],
            "score": get_score(review),
            "verdict": review.get("verdict"),
        })


def run_runtime_test(task, code):
    task_lower = (task or "").lower()
    if "input.txt" in task_lower or "clean.txt" in task_lower or ".txt" in task_lower:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            input_file = tmp_path / "input.txt"
            input_file.write_text(
                "apel\n"
                "\n"
                "jeruk\n"
                "apel\n"
                "mangga\n"
                "\n"
                "jeruk\n",
                encoding="utf-8"
            )

            script_path = tmp_path / "candidate.py"
            script_path.write_text(code, encoding="utf-8")

            commands = [
                ["python", str(script_path), "input.txt"],
                ["python", str(script_path), "input.txt", "clean.txt"],
                ["python", str(script_path), "input.txt", "--output", "clean.txt"],
            ]

            last_error = ""

            for cmd in commands:
                output_path = tmp_path / "clean.txt"

                if output_path.exists():
                    output_path.unlink()

                try:
                    result = subprocess.run(
                        cmd,
                        cwd=tmp_path,
                        capture_output=True,
                        text=True,
                        timeout=15
                    )
                except Exception as e:
                    last_error = str(e)
                    continue

                if result.returncode != 0:
                    last_error = result.stderr.strip() or result.stdout.strip()
                    continue

                if not output_path.exists():
                    last_error = "clean.txt tidak dibuat."
                    continue

                lines = output_path.read_text(encoding="utf-8").splitlines()

                if lines == ["apel", "jeruk", "mangga"]:
                    stdout = result.stdout.lower()
                    if "7" in stdout and "2" in stdout and "3" in stdout:
                        return {
                            "enabled": True,
                            "passed": True,
                            "notes": "Runtime test TXT lulus: clean.txt benar dan statistik tampil.",
                            "stdout": result.stdout.strip()
                        }

                last_error = f"clean.txt salah. Isi: {lines}, stdout={result.stdout.strip()}"

            return {
                "enabled": True,
                "passed": False,
                "notes": last_error
            }

    # Untuk sekarang runtime test khusus tugas JSONL folder.
    if "jsonl" not in task_lower:
        return {
            "enabled": False,
            "passed": False,
            "notes": "Runtime test belum tersedia untuk tugas ini."
        }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        (data_dir / "a.jsonl").write_text(
            '{"id": 1, "name": "A"}\n'
            'ini baris rusak\n'
            '{"id": 2, "name": "B"}\n',
            encoding="utf-8"
        )

        (data_dir / "b.jsonl").write_text(
            '{"id": 3, "name": "C"}\n'
            '{rusak lagi}\n'
            '{"id": 4, "name": "D"}\n',
            encoding="utf-8"
        )

        script_path = tmp_path / "candidate.py"
        script_path.write_text(code, encoding="utf-8")

        commands = [
            ["python", str(script_path), "data"],
            ["python", str(script_path), "data", "output.jsonl"],
            ["python", str(script_path), "data", "--output", "output.jsonl"],
            ["python", str(script_path), "data", "--output_file", "output.jsonl"],
        ]

        last_error = ""

        for cmd in commands:
            output_path = tmp_path / "output.jsonl"
            if output_path.exists():
                output_path.unlink()

            try:
                result = subprocess.run(
                    cmd,
                    cwd=tmp_path,
                    capture_output=True,
                    text=True,
                    timeout=15
                )
            except Exception as e:
                last_error = str(e)
                continue

            if result.returncode != 0:
                last_error = result.stderr.strip() or result.stdout.strip()
                continue

            if not output_path.exists():
                last_error = "output.jsonl tidak dibuat."
                continue

            lines = output_path.read_text(encoding="utf-8").splitlines()

            valid_json = 0
            for line in lines:
                try:
                    json.loads(line)
                    valid_json += 1
                except Exception:
                    pass

            stdout = result.stdout.lower()

            has_total_info = (
                "total" in stdout
                and "4" in stdout
                and "2" in stdout
            )

            if valid_json == 4 and len(lines) == 4 and has_total_info:
                return {
                    "enabled": True,
                    "passed": True,
                    "notes": "Runtime test lulus: output.jsonl berisi 4 JSON valid dan statistik tampil.",
                    "stdout": result.stdout.strip()
                }

            last_error = (
                f"Runtime test gagal. lines={len(lines)}, valid_json={valid_json}, "
                f"stdout={result.stdout.strip()}"
            )

        return {
            "enabled": True,
            "passed": False,
            "notes": last_error
        }
def block_repeated_mistake(task, code):
    task_lower = (task or "").lower()
    code_text = code or ""
    code_lower = code_text.lower()

    blocked = []

    is_txt_task = (
        "input.txt" in task_lower
        or "clean.txt" in task_lower
        or ".txt" in task_lower
        or "baris kosong" in task_lower
        or "duplikat" in task_lower
        or "hapus baris kosong" in task_lower
        or "hapus duplikat" in task_lower
    )

    if is_txt_task:
        forbidden_patterns = [
            ("import json", "Tugas TXT tidak boleh import json."),
            ("json.loads", "Tugas TXT tidak boleh memakai json.loads."),
            ("json.dumps", "Tugas TXT tidak boleh memakai json.dumps."),
            ("json.dump", "Tugas TXT tidak boleh memakai json.dump."),
            ("jsonl", "Tugas TXT tidak boleh membawa pola JSONL."),
            ("input_folder", "Tugas TXT harus membaca satu file input.txt, bukan input_folder."),
            (".iterdir(", "Tugas TXT tidak boleh scan folder."),
            (".glob(", "Tugas TXT tidak boleh scan folder."),
            ("folder containing", "Tugas TXT tidak boleh menganggap input sebagai folder."),
            ("data.jsonl", "Tugas TXT tidak boleh hardcode data.jsonl."),
        ]

        for pattern, message in forbidden_patterns:
            if pattern in code_lower:
                blocked.append("Kumar mengulang kesalahan: " + message)

    return blocked
    
def apply_local_validator(task, code, review):
    review = dict(review)

    warnings = validate_code(task, code)

    if warnings:
        old_score = get_score(review)
        new_score = min(old_score, 60)

        review["score"] = new_score
        review["verdict"] = "perlu_revisi"

        review.setdefault("problems", [])
        review.setdefault("must_fix", [])

        review["problems"].extend(warnings)
        review["must_fix"].append(
            "Perbaiki semua warning validator lokal sebelum kode boleh masuk memory."
        )

    runtime_result = run_runtime_test(task, code)

    if runtime_result.get("enabled"):
        review.setdefault("problems", [])
        review.setdefault("must_fix", [])

        review["runtime_test"] = runtime_result

        if runtime_result.get("passed"):
            review["score"] = max(get_score(review), 90)
            review["verdict"] = "lulus"
            review["notes"] = (
                str(review.get("notes", "")) +
                " | Runtime test lulus."
            ).strip()
        else:
            review["score"] = min(get_score(review), 60)
            review["verdict"] = "perlu_revisi"
            review["problems"].append(
                "Runtime test gagal: " + runtime_result.get("notes", "")
            )
            review["must_fix"].append(
                "Perbaiki kode sampai lulus runtime test otomatis."
            )

    return review

def extract_json(text):
    if not text:
        return None

    text = text.strip()
    text = text.replace("```json", "")
    text = text.replace("```JSON", "")
    text = text.replace("```", "")
    text = text.strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    decoder = json.JSONDecoder()

    for i, ch in enumerate(text):
        if ch != "{":
            continue

        try:
            data, _ = decoder.raw_decode(text[i:])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue

    return None

def parse_mimo_lines(text):
    result = {
        "score": 0,
        "verdict": "error",
        "problems": [],
        "must_fix": [],
        "notes": ""
    }

    if not text:
        return None

    for line in text.splitlines():
        line = line.strip()

        if not line or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip().upper()
        value = value.strip()

        if key == "SCORE":
            try:
                result["score"] = int(value)
            except Exception:
                result["score"] = 0

        elif key == "VERDICT":
            result["verdict"] = value

        elif key == "PROBLEM":
            result["problems"].append(value)

        elif key == "FIX":
            result["must_fix"].append(value)

        elif key == "NOTES":
            result["notes"] = value

    if result["verdict"] == "error" and result["problems"]:
        result["verdict"] = "perlu_revisi"

    return result

def get_mimo_message_text(data):
    try:
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {}) or {}

        texts = []

        content = message.get("content")
        reasoning_content = message.get("reasoning_content")

        if isinstance(content, str) and content.strip():
            texts.append(content.strip())

        if isinstance(reasoning_content, str) and reasoning_content.strip():
            texts.append(reasoning_content.strip())

        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        texts.append(text.strip())
                elif isinstance(item, str):
                    texts.append(item.strip())

        return "\n".join(texts).strip()

    except Exception:
        return ""


def ask_mimo_review(task, code):
    if not MIMO_API_KEY:
        return {
            "score": 0,
            "verdict": "error",
            "problems": ["MIMO_API_KEY belum diset di environment."],
            "must_fix": ["Set MIMO_API_KEY dulu."],
            "notes": "MiMo tidak bisa dipanggil."
        }

    prompt = f"""
Review kode Kumar secara singkat.

JANGAN berpikir panjang.
JANGAN tulis analisa.
JANGAN pakai markdown.
JANGAN pakai JSON.
Langsung isi format berikut.

FORMAT WAJIB:
SCORE=angka 0 sampai 100
VERDICT=lulus/perlu_revisi/berbahaya
PROBLEM=masalah utama 1
PROBLEM=masalah utama 2
PROBLEM=masalah utama 3
FIX=perbaikan utama 1
FIX=perbaikan utama 2
FIX=perbaikan utama 3
NOTES=catatan sangat pendek

TUGAS USER:
{task}

KODE KUMAR:
{code}
"""

    headers = {
        "api-key": MIMO_API_KEY,
        "Content-Type": "application/json",
    }

    payload = {
        "model": MIMO_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Kamu adalah reviewer kode. "
                    "Jawab sangat singkat. "
                    "Jangan tulis reasoning. "
                    "Jangan tulis markdown. "
                    "Ikuti format SCORE=, VERDICT=, PROBLEM=, FIX=, NOTES=."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.0,
        "top_p": 0.7,
        "max_completion_tokens": 2000,
        "stream": False,
    }

    try:
        r = requests.post(MIMO_URL, headers=headers, json=payload, timeout=60)
        r.raise_for_status()

        data = r.json()

        with open("last_mimo_full.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        content = get_mimo_message_text(data)

        with open("last_mimo_raw.txt", "w", encoding="utf-8") as f:
            f.write(content or "")
        parsed = parse_mimo_lines(content)

        if parsed and parsed.get("verdict") != "error":
            return parsed

        parsed_json = extract_json(content)

        if parsed_json:
            return parsed_json

        return {
            "score": 0,
            "verdict": "error",
            "problems": ["MiMo tidak memberi format review yang valid."],
            "must_fix": ["Paksa MiMo memakai format SCORE=, VERDICT=, PROBLEM=, FIX=."],
            "notes": content[:300]
        }

        if not content:
            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {}) or {}

            return {
                "score": 0,
                "verdict": "error",
                "problems": ["MiMo response ada, tapi message.content kosong."],
                "must_fix": ["Cek last_mimo_full.json untuk melihat struktur response asli."],
                "notes": f"message keys: {list(message.keys())}, finish_reason: {choice.get('finish_reason')}"
            }

        parsed = extract_json(content)

        if parsed:
            return parsed

        return {
            "score": 0,
            "verdict": "error",
            "problems": ["MiMo tidak membalas JSON valid."],
            "must_fix": ["Perbaiki prompt MiMo atau parser JSON."],
            "notes": content[:500]
        }

    except Exception as e:
        return {
            "score": 0,
            "verdict": "error",
            "problems": [str(e)],
            "must_fix": ["Cek koneksi, API key, atau endpoint MiMo."],
            "notes": "Request ke MiMo gagal."
        }


def build_revision_task(task, old_code, review):
    problems = review.get("problems", [])
    must_fix = review.get("must_fix", [])

    return f"""
TUGAS ASLI:
{task}

KODE SEBELUMNYA DINILAI GAGAL.
JANGAN MENIRU STRUKTUR KODE LAMA.
TULIS ULANG DARI NOL.

KESALAHAN KODE LAMA:
{json.dumps(problems, ensure_ascii=False, indent=2)}

WAJIB DIPERBAIKI:
{json.dumps(must_fix, ensure_ascii=False, indent=2)}

SYARAT WAJIB KODE BARU:
- Wajib memakai argparse.
- Wajib membaca semua file .jsonl dalam folder input.
- Wajib skip baris rusak dengan try/except json.JSONDecodeError.
- Wajib menggabungkan semua data valid ke output.jsonl.
- Wajib menulis output dengan format JSONL, satu JSON per baris.
- Wajib menampilkan total file.
- Wajib menampilkan total baris valid.
- Wajib menampilkan total baris rusak.
- Jangan hardcode hanya data.jsonl.
- Jangan hanya membuat fungsi read_jsonl satu file.
- Jangan membuka file input dengan mode "w".
- Gunakan pathlib atau os.listdir.

CONTOH STRUKTUR YANG DIHARAPKAN:
1. parse_args()
2. process_file(file_path, output_handle)
3. main()
4. if __name__ == "__main__": main()

JAWAB HANYA KODE PYTHON LENGKAP.
Jangan pakai markdown.
Jangan beri penjelasan.
"""


def pick_best(first_code, first_review, revised_code, final_review):
    first_score = int(first_review.get("score", 0) or 0)
    final_score = int(final_review.get("score", 0) or 0)

    if final_score >= first_score:
        return revised_code, final_score

    return first_code, first_score


def save_last_code(code):
    with open(LAST_CODE_FILE, "w", encoding="utf-8") as f:
        f.write(code.strip() + "\n")


def run_arena(task):
    attempts = []

    print("\n[1] Kumar Coder membuat kode pertama...")
    current_code = ask_coder(task)

    print("\n[2] Mengecek apakah Kumar mengulang kesalahan lama...")

    blocked = block_repeated_mistake(task, current_code)

    if blocked:
        print("\n[BLOCKED] Kumar mengulang kesalahan lama:")
        for item in blocked:
            print("-", item)

        current_review = {
            "score": 0,
            "verdict": "perlu_revisi",
            "problems": blocked,
            "must_fix": [
                "Tulis ulang dari nol sesuai tipe tugas.",
                "Jangan membawa pola dari tugas lain.",
                "Gunakan aturan khusus task_type sebelum membuat kode."
            ],
            "notes": "Diblokir oleh mistake gate sebelum review MiMo."
        }
    else:
        print("\n[2] MiMo Guru mengkritik kode pertama...")
        current_review = ask_mimo_review(task, current_code)
        current_review = apply_local_validator(task, current_code, current_review)

    print("\n--- REVIEW PERTAMA ---")
    print(json.dumps(current_review, ensure_ascii=False, indent=2))

    save_code_mistake(task, current_code, current_review)

    attempts.append({
        "round": 0,
        "type": "first_answer",
        "code": current_code,
        "review": current_review,
        "score": get_score(current_review),
    })

    if is_review_error(current_review):
        print("\n[STOP] Guru/reviewer error. Revisi dibatalkan agar Kumar tidak belajar dari arahan rusak.")

        save_jsonl(ARENA_LOG, {
            "schema": "coder_arena_1.2",
            "status": "review_error",
            "task": task,
            "attempts": attempts,
            "accepted": False,
        })

        print("\nKode percobaan tidak dimasukkan ke memory.")
        return

    best_code = current_code
    best_review = current_review
    best_score = get_score(current_review)

    accepted = best_score >= MIN_PASS_SCORE

    for round_no in range(1, MAX_REVISION_ROUNDS + 1):
        if accepted:
            break

        print(f"\n[REVISI {round_no}] Skor belum cukup. Kumar memperbaiki kode berdasarkan kritik...")

        revision_task = build_revision_task(task, current_code, current_review)
        revised_code = ask_coder(revision_task)

        similarity = code_similarity(current_code, revised_code)
        print(f"\n[CEK] Kemiripan kode sebelumnya vs revisi {round_no}: {similarity:.2f}")

        if is_code_too_similar(current_code, revised_code):
            print("\n[ANTI-STUCK] Revisi terlalu mirip. Kumar dipaksa tulis ulang dari nol...")
            forced_task = build_forced_rewrite_task(task, current_review)
            revised_code = ask_coder(forced_task)

        print(f"\n--- KODE REVISI {round_no} KUMAR ---")
        print(revised_code)

        print(f"\n[REVIEW {round_no}] Mengecek apakah revisi mengulang kesalahan lama...")

        blocked = block_repeated_mistake(task, revised_code)

        if blocked:
            print("\n[BLOCKED] Revisi Kumar mengulang kesalahan lama:")
            for item in blocked:
                print("-", item)

            revised_review = {
                "score": 0,
                "verdict": "perlu_revisi",
                "problems": blocked,
                "must_fix": [
                    "Tulis ulang dari nol. Kesalahan ini sudah pernah terjadi.",
                    "Jangan memakai pola JSONL untuk tugas TXT.",
                    "Ikuti tipe tugas user dengan ketat."
                ],
                "notes": "Revisi diblokir karena mengulang kesalahan lama."
            }
        else:
            print(f"\n[REVIEW {round_no}] MiMo Guru mengecek kode revisi...")
            revised_review = ask_mimo_review(task, revised_code)
            revised_review = apply_local_validator(task, revised_code, revised_review)

        print(f"\n--- REVIEW REVISI {round_no} ---")
        print(json.dumps(revised_review, ensure_ascii=False, indent=2))

        save_code_mistake(task, revised_code, revised_review)

        revised_score = get_score(revised_review)

        attempts.append({
            "round": round_no,
            "type": "revision",
            "code": revised_code,
            "review": revised_review,
            "score": revised_score,
            "similarity": similarity,
        })

        if revised_score > best_score:
            best_code = revised_code
            best_review = revised_review
            best_score = revised_score

        current_code = revised_code
        current_review = revised_review

        if best_score >= MIN_PASS_SCORE:
            accepted = True
            break

    if accepted:
        status = "accepted"
        save_last_code(best_code)
        save_code_memory(task, best_code, best_review)

        print("\n[HASIL] Kode diterima dan masuk memory Kumar.")
    else:
        status = "failed_but_logged"

        print("\n[HASIL] Kode belum cukup bagus.")
        print("Kode tidak dimasukkan ke memory, hanya disimpan sebagai pelajaran kesalahan.")

    save_jsonl(ARENA_LOG, {
        "schema": "coder_arena_1.2",
        "status": status,
        "task": task,
        "attempts": attempts,
        "best_candidate_code": best_code,
        "best_candidate_review": best_review,
        "best_candidate_score": best_score,
        "accepted": accepted,
    })

    print("\n" + "=" * 60)

    if accepted:
        print("KODE DITERIMA")
    else:
        print("KODE BELUM LULUS")

    print("=" * 60)
    print(best_code)
    print("=" * 60)

    print(f"\nSkor kandidat: {best_score}")
    print(f"Minimal lulus : {MIN_PASS_SCORE}")


def main():
    print("Arena Kumar Coder vs MiMo Guru aktif.")
    print("Ketik tugas coding.")
    print("keluar = berhenti\n")

    while True:
        task = input("Arena task: ").strip()

        if not task:
            continue

        if task.lower() in ["keluar", "exit", "quit"]:
            print("Arena berhenti.")
            break

        start = time.time()
        run_arena(task)
        print(f"\nSelesai dalam {time.time() - start:.2f} detik.\n")


if __name__ == "__main__":
    main()