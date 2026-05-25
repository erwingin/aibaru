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
from coder_local import ask_coder, validate_code, ask_task_plan


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


def build_forced_rewrite_task(task, task_plan, review):
    problems = review.get("problems", [])
    must_fix = review.get("must_fix", [])

    return f"""
TUGAS USER:
{task}

TASK PLAN YANG SUDAH DISETUJUI:
{task_plan}

KODE SEBELUMNYA GAGAL DAN TERLALU MIRIP.
TULIS ULANG DARI NOL.

KESALAHAN YANG HARUS DIHINDARI:
{json.dumps(problems, ensure_ascii=False, indent=2)}

WAJIB DIPERBAIKI:
{json.dumps(must_fix, ensure_ascii=False, indent=2)}

ATURAN:
- Ikuti TASK PLAN.
- Jangan melanggar MUST_NOT_USE.
- Jangan membawa pola dari tugas lain.
- Jangan menambahkan fitur yang tidak diminta user.
- Tulis kode Python lengkap yang bisa langsung dijalankan.
- Jawab hanya kode Python.
- Jangan pakai markdown.
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

    if "curl" in task_lower:
        return "curl_to_requests"

    # TXT dicek sebelum JSONL/CLI.
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
    code_text = code or ""

    # ==================================================
    # RUNTIME TEST: CURL TO REQUESTS CONVERTER
    # ==================================================
    if "curl" in task_lower or "curl.txt" in task_lower:
        import ast

        def candidate_executes_requests(source_code):
            """
            Cek apakah candidate.py benar-benar memanggil requests.get/post.
            String yang berisi 'requests.get(' untuk output.py tidak dianggap eksekusi.
            """
            try:
                tree = ast.parse(source_code)
            except Exception:
                return False

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func

                    if isinstance(func, ast.Attribute):
                        if func.attr in ["get", "post", "put", "delete", "patch", "request"]:
                            value = func.value
                            if isinstance(value, ast.Name) and value.id == "requests":
                                return True

            return False

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            curl_file = tmp_path / "curl.txt"
            curl_file.write_text(
                "curl 'https://example.com/api/profile/me' "
                "-H 'accept: application/json' "
                "-H 'user-agent: Mozilla/5.0' "
                "-b 'session=<SECRET>; session_hint=1'\n",
                encoding="utf-8"
            )

            script_path = tmp_path / "candidate.py"
            script_path.write_text(code_text, encoding="utf-8")

            commands = [
                ["python", str(script_path), "curl.txt"],
                ["python", str(script_path), "curl.txt", "output.py"],
                ["python", str(script_path), "curl.txt", "--output", "output.py"],
            ]

            errors = []

            for cmd in commands:
                output_path = tmp_path / "output.py"

                if output_path.exists():
                    output_path.unlink()

                try:
                    result = subprocess.run(
                        cmd,
                        cwd=tmp_path,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                except Exception as e:
                    errors.append(str(e))
                    continue

                if result.returncode != 0:
                    errors.append(result.stderr.strip() or result.stdout.strip())
                    continue

                if not output_path.exists():
                    errors.append("output.py tidak dibuat.")
                    continue

                generated = output_path.read_text(encoding="utf-8")
                generated_lower = generated.lower()
                stdout = result.stdout.lower()

                has_requests = "import requests" in generated_lower
                has_url = "https://example.com/api/profile/me" in generated
                has_header = "accept" in generated_lower and "application/json" in generated_lower
                has_cookie = "session" in generated_lower and "<SECRET>" in generated
                has_timeout = "timeout" in generated_lower

                # candidate.py tidak boleh benar-benar menjalankan request.
                # output.py boleh berisi requests.get/post, karena itu hasil generate.
                candidate_safe = not candidate_executes_requests(code_text)

                has_summary = (
                    "method" in stdout
                    and "url" in stdout
                    and "headers" in stdout
                    and "cookie" in stdout
                    and "body" in stdout
                )

                if (
                    has_requests
                    and has_url
                    and has_header
                    and has_cookie
                    and has_timeout
                    and candidate_safe
                    and has_summary
                ):
                    return {
                        "enabled": True,
                        "passed": True,
                        "notes": "Runtime test CURL lulus: output.py dibuat dan converter tidak menjalankan request asli.",
                        "stdout": result.stdout.strip()
                    }

                errors.append(
                    "output.py belum sesuai. "
                    f"has_requests={has_requests}, "
                    f"has_url={has_url}, "
                    f"has_header={has_header}, "
                    f"has_cookie={has_cookie}, "
                    f"has_timeout={has_timeout}, "
                    f"candidate_safe={candidate_safe}, "
                    f"has_summary={has_summary}"
                )

            return {
                "enabled": True,
                "passed": False,
                "notes": " | ".join(errors[-3:])
            }

    # ==================================================
    # RUNTIME TEST: TXT CLEANER
    # ==================================================
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
            script_path.write_text(code_text, encoding="utf-8")

            commands = [
                ["python", str(script_path), "input.txt"],
                ["python", str(script_path), "input.txt", "clean.txt"],
                ["python", str(script_path), "input.txt", "--output", "clean.txt"],
            ]

            errors = []

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
                        timeout=20
                    )
                except Exception as e:
                    errors.append(str(e))
                    continue

                if result.returncode != 0:
                    errors.append(result.stderr.strip() or result.stdout.strip())
                    continue

                if not output_path.exists():
                    errors.append("clean.txt tidak dibuat.")
                    continue

                lines = output_path.read_text(encoding="utf-8").splitlines()
                stdout = result.stdout.lower()

                correct_output = lines == ["apel", "jeruk", "mangga"]

                has_total_lines = (
                    "total lines: 7" in stdout
                    or "total initial lines: 7" in stdout
                    or "total lines initially: 7" in stdout
                    or "total lines before cleaning: 7" in stdout
                    or "total baris awal: 7" in stdout
                    or "baris awal: 7" in stdout
                )

                has_empty_lines = (
                    "empty lines: 2" in stdout
                    or "total empty lines: 2" in stdout
                    or "empty lines removed: 2" in stdout
                    or "total empty lines removed: 2" in stdout
                    or "total baris kosong: 2" in stdout
                    or "baris kosong: 2" in stdout
                )

                has_duplicates = (
                    "duplicates: 2" in stdout
                    or "total duplicates: 2" in stdout
                    or "duplicated lines: 2" in stdout
                    or "total duplicated lines: 2" in stdout
                    or "duplicate lines removed: 2" in stdout
                    or "total duplicate lines removed: 2" in stdout
                    or "duplikat: 2" in stdout
                    or "total duplikat: 2" in stdout
                )

                has_final_lines = (
                    "lines after cleaning: 3" in stdout
                    or "total lines after cleaning: 3" in stdout
                    or "final lines: 3" in stdout
                    or "total final lines: 3" in stdout
                    or "clean lines: 3" in stdout
                    or "total clean lines: 3" in stdout
                    or "baris akhir: 3" in stdout
                    or "total baris akhir: 3" in stdout
                )

                has_total_info = (
                    has_total_lines
                    and has_empty_lines
                    and has_duplicates
                    and has_final_lines
                )

                if correct_output and has_total_info:
                    return {
                        "enabled": True,
                        "passed": True,
                        "notes": "Runtime test TXT lulus: clean.txt benar dan statistik tampil.",
                        "stdout": result.stdout.strip()
                    }

                if not correct_output:
                    errors.append(
                        f"clean.txt salah. Isi: {lines}, stdout={result.stdout.strip()}"
                    )
                else:
                    errors.append(
                        "clean.txt benar, tapi statistik stdout belum lengkap/tepat. "
                        f"stdout={result.stdout.strip()}"
                    )

            return {
                "enabled": True,
                "passed": False,
                "notes": " | ".join(errors[-3:])
            }

    # ==================================================
    # RUNTIME TEST: JSONL FOLDER
    # ==================================================
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
        script_path.write_text(code_text, encoding="utf-8")

        commands = [
            ["python", str(script_path), "data"],
            ["python", str(script_path), "data", "output.jsonl"],
            ["python", str(script_path), "data", "--output", "output.jsonl"],
            ["python", str(script_path), "data", "--output_file", "output.jsonl"],
        ]

        errors = []

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
                    timeout=20
                )
            except Exception as e:
                errors.append(str(e))
                continue

            if result.returncode != 0:
                errors.append(result.stderr.strip() or result.stdout.strip())
                continue

            if not output_path.exists():
                errors.append("output.jsonl tidak dibuat.")
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
                    "notes": "Runtime test JSONL lulus: output.jsonl berisi 4 JSON valid dan statistik tampil.",
                    "stdout": result.stdout.strip()
                }

            errors.append(
                f"Runtime test JSONL gagal. lines={len(lines)}, valid_json={valid_json}, "
                f"stdout={result.stdout.strip()}"
            )

        return {
            "enabled": True,
            "passed": False,
            "notes": " | ".join(errors[-3:])
        }

def replace_plan_field(plan_text, field_name, new_value):
    lines = (plan_text or "").splitlines()
    result = []
    replaced = False

    field_prefix = field_name.lower() + ":"

    for line in lines:
        if line.strip().lower().startswith(field_prefix):
            result.append(f"{field_name}: {new_value}")
            replaced = True
        else:
            result.append(line)

    if not replaced:
        result.append(f"{field_name}: {new_value}")

    return "\n".join(result)


def append_plan_field_value(plan_text, field_name, value):
    lines = (plan_text or "").splitlines()
    result = []
    added = False

    field_prefix = field_name.lower() + ":"

    for line in lines:
        if line.strip().lower().startswith(field_prefix):
            if value.lower() not in line.lower():
                line = line.rstrip() + ", " + value
            added = True

        result.append(line)

    if not added:
        result.append(f"{field_name}: {value}")

    return "\n".join(result)


def repair_plan_with_user_constraints(task, plan_text):
    """
    Ini bukan hardcode curl/TXT/JSON.
    Ini hanya mengambil fakta eksplisit dari task user:
    - nama file yang disebut user
    - output file yang disebut user
    - larangan seperti jangan menjalankan request asli
    lalu memasukkannya ke task plan agar tidak hilang.
    """

    task_lower = (task or "").lower()
    fixed_plan = plan_text or ""

    mentioned_files = re.findall(
        r"\b[\w.-]+\.(?:txt|py|json|jsonl|csv|log|md)\b",
        task_lower
    )

    # Deteksi output file dari pola umum: "simpan ke output.py", "save to x", "output file x"
    output_files = re.findall(
        r"(?:simpan(?:\s+hasil)?\s+ke|save\s+to|output(?:\s+file)?(?:\s+ke)?)\s+([\w.-]+\.(?:txt|py|json|jsonl|csv|log|md))",
        task_lower
    )

    output_files = list(dict.fromkeys(output_files))
    mentioned_files = list(dict.fromkeys(mentioned_files))

    input_files = [f for f in mentioned_files if f not in output_files]

    # Kalau INPUT/OUTPUT masih tidak disebut, isi dari file yang user sebut.
    if input_files and (
        "input: tidak disebut" in fixed_plan.lower()
        or "input:" not in fixed_plan.lower()
    ):
        fixed_plan = replace_plan_field(fixed_plan, "INPUT", ", ".join(input_files))

    if output_files and (
        "output: tidak disebut" in fixed_plan.lower()
        or "output:" not in fixed_plan.lower()
    ):
        fixed_plan = replace_plan_field(fixed_plan, "OUTPUT", ", ".join(output_files))

    # Pastikan semua file yang disebut user muncul di plan.
    if mentioned_files:
        fixed_plan = append_plan_field_value(
            fixed_plan,
            "NOTES",
            "File yang disebut user: " + ", ".join(mentioned_files)
        )

    user_forbids_running_request = (
        "jangan menjalankan request" in task_lower
        or "tidak menjalankan request" in task_lower
        or "jangan mengirim request" in task_lower
        or "tidak mengirim request" in task_lower
        or "cukup generate" in task_lower
        or "cukup membuat" in task_lower
    )

    if user_forbids_running_request:
        fixed_plan = append_plan_field_value(
            fixed_plan,
            "MUST_NOT_USE",
            "menjalankan request asli"
        )

        fixed_plan = append_plan_field_value(
            fixed_plan,
            "MUST_DO",
            "hanya generate kode, jangan menjalankan request asli"
        )

    return fixed_plan

def validate_task_plan(task, plan_text):
    task_lower = (task or "").lower()
    plan_text = plan_text or ""
    plan_lower = plan_text.lower()

    problems = []

    required_fields = [
        "task_type:",
        "input:",
        "output:",
        "must_use:",
        "must_not_use:",
        "must_do:",
        "notes:",
    ]

    for field in required_fields:
        if field not in plan_lower:
            problems.append(f"Plan belum punya field {field.upper()}")

    # Ambil nama file yang disebut user, misalnya:
    # input.txt, clean.txt, curl.txt, output.py, data.jsonl
    mentioned_files = re.findall(
        r"\b[\w.-]+\.(?:txt|py|json|jsonl|csv|log|md)\b",
        task_lower
    )

    for filename in mentioned_files:
        if filename not in plan_lower:
            problems.append(f"Plan belum menyebut file yang diminta user: {filename}")

    # Kalau user meminta argparse/CLI, plan harus sadar itu.
    if ("argparse" in task_lower or "cli" in task_lower) and "argparse" not in plan_lower:
        problems.append("Plan belum memasukkan argparse padahal user meminta CLI.")

    # Kalau user meminta --output, plan harus mencatat --output.
    if "--output" in task_lower and "--output" not in plan_lower:
        problems.append("Plan belum menyebut dukungan argumen --output.")

    # Kalau user melarang menjalankan request asli, plan juga harus melarang.
    user_forbids_running_request = (
        "jangan menjalankan request" in task_lower
        or "tidak menjalankan request" in task_lower
        or "cukup generate" in task_lower
        or "cukup membuat" in task_lower
    )

    if user_forbids_running_request:
        plan_blocks_request = (
            "jangan menjalankan request" in plan_lower
            or "tidak menjalankan request" in plan_lower
            or "jangan eksekusi request" in plan_lower
            or "tidak mengirim request" in plan_lower
            or "hanya generate" in plan_lower
        )

        if not plan_blocks_request:
            problems.append("Plan belum jelas melarang menjalankan request asli.")

    # Kalau planner error, langsung tolak.
    if "plan_error" in plan_lower:
        problems.append("Planner error, plan tidak valid.")

    return problems
    
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
            ("output.jsonl", "Tugas TXT tidak boleh memakai output.jsonl."),
            ("clean.jsonl", "Tugas TXT tidak boleh memakai clean.jsonl."),
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


def build_revision_task(task, task_plan, old_code, review):
    problems = review.get("problems", [])
    must_fix = review.get("must_fix", [])

    return f"""
TUGAS USER:
{task}

TASK PLAN YANG SUDAH DISETUJUI:
{task_plan}

KODE SEBELUMNYA SALAH.
JANGAN MENIRU KESALAHAN KODE LAMA.
JANGAN MENGUBAH TIPE TUGAS.

KESALAHAN KODE LAMA:
{json.dumps(problems, ensure_ascii=False, indent=2)}

WAJIB DIPERBAIKI:
{json.dumps(must_fix, ensure_ascii=False, indent=2)}

ATURAN REVISI:
- Ikuti TASK PLAN.
- Jangan melanggar MUST_NOT_USE.
- Jika kode lama arahnya salah, tulis ulang dari nol.
- Jangan membawa pola dari tugas lain.
- Jangan menambahkan fitur yang tidak diminta user.
- Tulis kode Python lengkap yang bisa langsung dijalankan.
- Jawab hanya kode Python.
- Jangan pakai markdown.
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

    print("\n[0] Kumar membuat rencana tugas dulu...")
    task_plan = ask_task_plan(task)
    task_plan = repair_plan_with_user_constraints(task, task_plan)

    print("\n--- TASK PLAN KUMAR ---")
    print(task_plan)

    plan_problems = validate_task_plan(task, task_plan)

    if plan_problems:
        print("\n[PLAN DITOLAK] Rencana Kumar masih salah:")
        for item in plan_problems:
            print("-", item)

        save_code_lesson(
            task,
            {
                "score": 0,
                "verdict": "plan_salah",
                "problems": plan_problems,
                "must_fix": [
                    "Pahami tipe tugas sebelum menulis kode.",
                    "Buat plan yang sesuai dengan input, output, larangan, dan kewajiban user."
                ],
                "notes": "Kode tidak dibuat karena task_plan salah."
            }
        )
        return

    coder_task = f"""
    TUGAS USER:
    {task}

    TASK PLAN YANG SUDAH DISETUJUI:
    {task_plan}

    Tulis kode Python berdasarkan TASK PLAN ini.
    Jangan melanggar MUST_NOT_USE.
    """

    print("\n[1] Kumar Coder membuat kode pertama berdasarkan task plan...")
    current_code = ask_coder(coder_task)

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
    save_code_lesson(task, current_review)

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

        revision_task = build_revision_task(task, task_plan, current_code, current_review)
        revised_code = ask_coder(revision_task)

        similarity = code_similarity(current_code, revised_code)
        print(f"\n[CEK] Kemiripan kode sebelumnya vs revisi {round_no}: {similarity:.2f}")

        if is_code_too_similar(current_code, revised_code):
            print("\n[ANTI-STUCK] Revisi terlalu mirip. Kumar dipaksa tulis ulang dari nol...")
            forced_task = build_forced_rewrite_task(task, task_plan, current_review)
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
        save_code_lesson(task, revised_review)

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