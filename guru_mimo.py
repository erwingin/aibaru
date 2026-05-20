import os
import re
import json
import urllib.request
import urllib.error


def load_env(path=".env"):
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def normalize_label(label):
    label = label.strip().lower()
    label = re.sub(r"[^a-z0-9_ ]", "", label)
    label = re.sub(r"\s+", "_", label)
    label = re.sub(r"_+", "_", label).strip("_")
    return label


def is_valid_label(label):
    return bool(re.match(r"^[a-z0-9_]{3,60}$", label))


def parse_line_format(text):
    data = {
        "target": "",
        "reason": "",
        "response": "",
        "subject": "",
        "facts": [],
        "examples": []
    }

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("TARGET="):
            data["target"] = line.split("=", 1)[1].strip()

        elif line.startswith("REASON="):
            data["reason"] = line.split("=", 1)[1].strip()

        elif line.startswith("RESPONSE="):
            data["response"] = line.split("=", 1)[1].strip()

        elif line.startswith("SUBJECT="):
            data["subject"] = line.split("=", 1)[1].strip()

        elif line.startswith("FACT="):
            fact = line.split("=", 1)[1].strip()
            if fact:
                data["facts"].append(fact)

        elif line.startswith("EXAMPLE="):
            example = line.split("=", 1)[1].strip()
            if example:
                data["examples"].append(example)

    return data


def ask_mimo_guru(user_input, labels):
    load_env()

    api_key = os.environ.get("MIMO_API_KEY")
    model = os.environ.get("MIMO_MODEL", "mimo-v2.5-pro")
    base_url = os.environ.get(
        "MIMO_BASE_URL",
        "https://api.xiaomimimo.com/v1/chat/completions"
    )

    if not api_key:
        return {
            "ok": False,
            "error": "MIMO_API_KEY belum ada di .env"
        }

    label_text = ", ".join(labels)

    prompt = f"""
Kamu adalah guru untuk AI kecil bernama Kumar.

Tugasmu:
1. Baca kalimat user.
2. Pilih label lama jika cocok.
3. Kalau tidak cocok, buat label baru dalam snake_case bahasa Indonesia.
4. Buat jawaban pendek yang benar.
5. Buat 5 contoh latihan tambahan.

Label lama:
{label_text}

Kalimat user:
{user_input}

PENTING:
- Jangan pakai JSON.
- Jangan pakai markdown.
- Jangan pakai bullet.
- Jawab hanya dengan format baris di bawah ini.
- TARGET harus satu label saja.
- Label baru harus huruf kecil dan pakai underscore.

Format wajib:
STATUS=approved
TARGET=nama_label
REASON=alasan verifier
RESPONSE=jawaban final yang aman
SUBJECT=subjek_inti_dalam_snake_case
FACT=fakta aman 1
FACT=fakta aman 2
FACT=fakta aman 3
EXAMPLE=contoh kalimat 1
EXAMPLE=contoh kalimat 2
EXAMPLE=contoh kalimat 3
EXAMPLE=contoh kalimat 4
EXAMPLE=contoh kalimat 5

STATUS hanya boleh salah satu dari:
approved
revised
rejected
"""

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are MiMo, an AI assistant developed by Xiaomi. You are helping train a tiny local AI model."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_completion_tokens": 900,
        "temperature": 0.2,
        "top_p": 0.95,
        "stream": False
    }

    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        base_url,
        data=data,
        headers={
            "api-key": api_key,
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            result = json.loads(raw)

        content = result["choices"][0]["message"]["content"].strip()

        teacher_data = parse_line_format(content)

        target = normalize_label(teacher_data.get("target", ""))
        reason = teacher_data.get("reason", "").strip()
        response_text = teacher_data.get("response", "").strip()
        examples = teacher_data.get("examples", [])

        if not target:
            return {
                "ok": False,
                "error": "Guru tidak memberi TARGET. Raw output: " + content
            }

        if not is_valid_label(target):
            return {
                "ok": False,
                "error": f"Label dari guru tidak valid: {target}. Raw output: {content}"
            }

        is_new_label = target not in labels

        if not response_text:
            response_text = f"Saya sedang belajar tentang {target.replace('_', ' ')}."

        clean_examples = []

        clean_examples.append({
            "input": user_input,
            "target": target
        })

        for ex in examples:
            ex = ex.strip()
            if ex:
                clean_examples.append({
                    "input": ex,
                    "target": target
                })

        return {
            "ok": True,
            "target": target,
            "is_new_label": is_new_label,
            "reason": reason,
            "response": response_text,
            "subject": normalize_label(teacher_data.get("subject", target)),
            "facts": teacher_data.get("facts", []),
            "examples": clean_examples
        }

    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "error": f"HTTP Error: {e.code} {e.read().decode('utf-8', errors='ignore')}"
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }
    
def verify_mimo_lesson(user_input, labels, lesson, old_result=None):
    load_env()

    api_key = os.environ.get("MIMO_API_KEY")
    model = os.environ.get("MIMO_MODEL", "mimo-v2.5-pro")
    base_url = os.environ.get(
        "MIMO_BASE_URL",
        "https://api.xiaomimimo.com/v1/chat/completions"
    )

    if not api_key:
        return {
            "ok": False,
            "error": "MIMO_API_KEY belum ada di .env"
        }

    label_text = ", ".join(labels)

    facts_text = "\n".join([
        f"- {x}" for x in lesson.get("facts", [])
    ])

    examples_text = "\n".join([
        f"- {x.get('input', '')}" for x in lesson.get("examples", [])
    ])

    old_label = None
    old_output = None

    if old_result:
        old_label = old_result.get("label")
        old_output = old_result.get("output")

    prompt = f"""
Kamu adalah VERIFIER dan KRITIKUS otomatis untuk AI kecil bernama Kumar.

Tugasmu:
1. Periksa pelajaran dari guru.
2. Jangan asal setuju.
3. Kalau label kurang tepat, revisi label.
4. Kalau jawaban terlalu percaya diri, revisi menjadi lebih hati-hati.
5. Kalau jawaban spekulatif, wajib pakai kata seperti: "secara teori", "dalam simulasi sederhana", "kemungkinan", atau "belum tentu".
6. Kalau pelajaran salah berat, beri STATUS=rejected.
7. Kalau masih bisa diperbaiki, beri STATUS=revised.
8. Kalau sudah aman, beri STATUS=approved.
9. Jawab hanya dengan format baris, tanpa JSON, tanpa markdown.

Label lama:
{label_text}

Input user:
{user_input}

Prediksi lama Kumar:
OLD_LABEL={old_label}
OLD_OUTPUT={old_output}

Pelajaran dari guru:
GURU_TARGET={lesson.get("target")}
GURU_REASON={lesson.get("reason")}
GURU_RESPONSE={lesson.get("response")}
GURU_SUBJECT={lesson.get("subject")}
GURU_FACTS:
{facts_text}

GURU_EXAMPLES:
{examples_text}

Aturan penting:
- Jangan membuat klaim terlalu mutlak jika topiknya belum pasti.
- Untuk topik AI masa depan, ekonomi AI, dunia virtual, agent, AGI, robot, simulasi: gunakan bahasa hati-hati.
- Jangan pakai "pasti", "sudah terbukti", "semua", "selalu" kecuali benar-benar aman.
- Kalau respon guru terlalu yakin, revisi agar lebih aman.
- Tetap buat 5 contoh latihan.

Format wajib:
STATUS=approved_atau_revised_atau_rejected
TARGET=nama_label
REASON=alasan verifier
RESPONSE=jawaban final yang aman
SUBJECT=subjek_inti_dalam_snake_case
FACT=fakta aman 1
FACT=fakta aman 2
FACT=fakta aman 3
EXAMPLE=contoh kalimat 1
EXAMPLE=contoh kalimat 2
EXAMPLE=contoh kalimat 3
EXAMPLE=contoh kalimat 4
EXAMPLE=contoh kalimat 5
"""

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict verifier for a tiny AI training system. You must reduce overconfident claims and produce safe training data."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_completion_tokens": 900,
        "temperature": 0.1,
        "top_p": 0.9,
        "stream": False
    }

    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        base_url,
        data=data,
        headers={
            "api-key": api_key,
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            result = json.loads(raw)

        content = result["choices"][0]["message"]["content"].strip()

        status = "rejected"
        parsed = parse_line_format(content)

        for raw_line in content.splitlines():
            line = raw_line.strip()

            upper_line = line.upper()

            if upper_line.startswith("STATUS="):
                status = line.split("=", 1)[1].strip().lower()

            elif upper_line.startswith("STATUS:"):
                status = line.split(":", 1)[1].strip().lower()

        target = normalize_label(parsed.get("target", ""))
        response_text = parsed.get("response", "").strip()
        reason = parsed.get("reason", "").strip()
        subject = normalize_label(parsed.get("subject", target))
        facts = parsed.get("facts", [])
        examples = parsed.get("examples", [])

        if status not in ["approved", "revised", "rejected"]:
            status = "rejected"

        if status == "rejected":
            # Kalau verifier benar-benar memberi alasan jelas, baru tolak.
            # Kalau tidak ada alasan dan formatnya kemungkinan gagal dibaca,
            # jangan buang pelajaran guru begitu saja.
            if reason:
                return {
                    "ok": True,
                    "approved": False,
                    "status": "rejected",
                    "reason": reason,
                    "raw": content
                }

            # Fallback aman: pakai pelajaran guru, tapi tandai sebagai revised_fallback
            target = normalize_label(lesson.get("target", ""))
            response_text = lesson.get("response", "")
            subject = normalize_label(lesson.get("subject", target))
            facts = lesson.get("facts", [])
            examples = lesson.get("examples", [])

            if not target or not response_text:
                return {
                    "ok": True,
                    "approved": False,
                    "status": "rejected",
                    "reason": "Verifier gagal format dan pelajaran guru tidak lengkap.",
                    "raw": content
                }

            return {
                "ok": True,
                "approved": True,
                "status": "revised_fallback",
                "reason": "Verifier tidak memberi format jelas, pelajaran guru dipakai dengan mode fallback.",
                "lesson": {
                    "ok": True,
                    "target": target,
                    "is_new_label": target not in labels,
                    "reason": lesson.get("reason", ""),
                    "response": response_text,
                    "subject": subject,
                    "facts": facts,
                    "examples": examples
                },
                "raw": content
            }

        if not target or not is_valid_label(target):
            return {
                "ok": False,
                "error": "Verifier memberi label tidak valid. Raw: " + content
            }

        if not response_text:
            response_text = lesson.get("response", "")

        clean_examples = []

        clean_examples.append({
            "input": user_input,
            "target": target
        })

        for ex in examples:
            ex = ex.strip()
            if ex:
                clean_examples.append({
                    "input": ex,
                    "target": target
                })

        if len(clean_examples) <= 1:
            clean_examples = lesson.get("examples", clean_examples)

        verified_lesson = {
            "ok": True,
            "target": target,
            "is_new_label": target not in labels,
            "reason": reason,
            "response": response_text,
            "subject": subject,
            "facts": facts,
            "examples": clean_examples
        }

        return {
            "ok": True,
            "approved": True,
            "status": status,
            "reason": reason,
            "lesson": verified_lesson,
            "raw": content
        }

    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "error": f"HTTP Error: {e.code} {e.read().decode('utf-8', errors='ignore')}"
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }