import json
import os
import re
import time
from datetime import datetime, timezone
import difflib
import requests

from coder_local import ask_coder


MIMO_URL = os.getenv("MIMO_URL", "https://api.xiaomimimo.com/v1/chat/completions")
MIMO_MODEL = os.getenv("MIMO_MODEL", "mimo-v2.5-pro")
MIMO_API_KEY = os.getenv("MIMO_API_KEY")

ARENA_LOG = "coder_arena_log.jsonl"
LAST_CODE_FILE = "last_kumar_code.py"

CODE_MEMORY = "code_memory.jsonl"
CODE_LESSONS = "code_lessons.jsonl"
MIN_PASS_SCORE = 75


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
    print("\n[1] Kumar Coder membuat kode pertama...")
    first_code = ask_coder(task)

    print("\n[2] MiMo Guru mengkritik kode pertama...")
    first_review = ask_mimo_review(task, first_code)

    print("\n--- REVIEW PERTAMA ---")
    print(json.dumps(first_review, ensure_ascii=False, indent=2))

    save_code_lesson(task, first_review)

    if is_review_error(first_review):
        print("\n[STOP] Guru MiMo error. Revisi dibatalkan agar Kumar tidak belajar dari arahan rusak.")

        save_jsonl(ARENA_LOG, {
            "schema": "coder_arena_1.1",
            "status": "guru_error",
            "task": task,
            "first_code": first_code,
            "first_review": first_review,
            "revised_code": "",
            "final_review": {},
            "accepted": False,
        })

        print("\nKode percobaan tidak dimasukkan ke memory.")
        return

    first_score = get_score(first_review)

    if first_score >= MIN_PASS_SCORE:
        print("\n[3] Kode pertama sudah cukup bagus. Masuk memory Kumar.")
        save_last_code(first_code)
        save_code_memory(task, first_code, first_review)

        save_jsonl(ARENA_LOG, {
            "schema": "coder_arena_1.1",
            "status": "accepted_first_try",
            "task": task,
            "first_code": first_code,
            "first_review": first_review,
            "revised_code": "",
            "final_review": first_review,
            "best_code": first_code,
            "accepted": True,
        })

        print("\n" + "=" * 60)
        print("KODE DITERIMA")
        print("=" * 60)
        print(first_code)
        print("=" * 60)
        return

    print("\n[3] Skor belum cukup. Kumar memperbaiki kode berdasarkan kritik MiMo...")
    revision_task = build_revision_task(task, first_code, first_review)
    revised_code = ask_coder(revision_task)

    similarity = code_similarity(first_code, revised_code)
    print(f"\n[CEK] Kemiripan kode pertama vs revisi: {similarity:.2f}")

    if is_code_too_similar(first_code, revised_code):
        print("\n[ANTI-STUCK] Revisi terlalu mirip. Kumar dipaksa tulis ulang dari nol...")
        forced_task = build_forced_rewrite_task(task, first_review)
        revised_code = ask_coder(forced_task)

    print("\n--- KODE REVISI KUMAR ---")
    print(revised_code)

    print("\n[4] MiMo Guru mengecek ulang kode revisi...")
    final_review = ask_mimo_review(task, revised_code)

    print("\n--- REVIEW FINAL ---")
    print(json.dumps(final_review, ensure_ascii=False, indent=2))

    save_code_lesson(task, final_review)

    if is_review_error(final_review):
        print("\n[STOP] Review final error. Kode revisi tidak dimasukkan ke memory.")

        save_jsonl(ARENA_LOG, {
            "schema": "coder_arena_1.1",
            "status": "final_review_error",
            "task": task,
            "first_code": first_code,
            "first_review": first_review,
            "revised_code": revised_code,
            "final_review": final_review,
            "accepted": False,
        })

        return

    final_score = get_score(final_review)

    if final_score >= first_score:
        candidate_code = revised_code
        candidate_review = final_review
        candidate_score = final_score
    else:
        candidate_code = first_code
        candidate_review = first_review
        candidate_score = first_score

    if candidate_score >= MIN_PASS_SCORE:
        status = "accepted_after_revision"
        accepted = True

        save_last_code(candidate_code)
        save_code_memory(task, candidate_code, candidate_review)

        print("\n[HASIL] Kode diterima dan masuk memory Kumar.")
    else:
        status = "failed_but_logged"
        accepted = False

        print("\n[HASIL] Kode belum cukup bagus.")
        print("Kode tidak dimasukkan ke memory, hanya disimpan sebagai pelajaran kesalahan.")

    save_jsonl(ARENA_LOG, {
        "schema": "coder_arena_1.1",
        "status": status,
        "task": task,
        "first_code": first_code,
        "first_review": first_review,
        "revised_code": revised_code,
        "final_review": final_review,
        "best_candidate_code": candidate_code,
        "best_candidate_score": candidate_score,
        "accepted": accepted,
    })

    print("\n" + "=" * 60)

    if accepted:
        print("KODE DITERIMA")
    else:
        print("KODE BELUM LULUS")

    print("=" * 60)
    print(candidate_code)
    print("=" * 60)

    print(f"\nSkor kandidat: {candidate_score}")
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