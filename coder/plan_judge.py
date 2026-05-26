import re


def _to_text(value):
    if isinstance(value, dict):
        lines = []

        for key, item in value.items():
            if isinstance(item, list):
                item = ", ".join(str(x) for x in item)
            elif isinstance(item, dict):
                item = ", ".join(f"{k}={v}" for k, v in item.items())
            else:
                item = str(item)

            lines.append(f"{key}: {item}")

        return "\n".join(lines)

    if isinstance(value, list):
        return ", ".join(str(x) for x in value)

    return str(value or "")


def _lower(text):
    return _to_text(text).lower()


def _has_any(text, words):
    text = _lower(text)
    return any(word in text for word in words)


def _extract_filenames(text):
    text = _to_text(text)
    return set(re.findall(r"\b[\w.-]+\.(?:txt|py|json|jsonl|csv|log|md)\b", text))


def _user_wants_requests_library(task):
    t = _lower(task)

    return (
        "python requests" in t
        or "script requests" in t
        or "kode requests" in t
        or "generate requests" in t
        or "ubah menjadi script python requests" in t
        or "ubah menjadi kode python requests" in t
    )


def _user_wants_only_url(task):
    t = _lower(task)

    wants_url = (
        "tampilkan url" in t
        or "ambil url" in t
        or "ekstrak url" in t
        or "print url" in t
        or "menampilkan url" in t
    )

    wants_big_converter = (
        "output.py" in t
        or "generate kode" in t
        or "simpan ke output" in t
        or "ubah menjadi script python requests" in t
        or "headers" in t
        or "cookie" in t
        or "body" in t
        or "--data-raw" in t
    )

    return "curl" in t and wants_url and not wants_big_converter


def _make_review(errors, warnings):
    return {
        "score": 0,
        "verdict": "plan_salah",
        "problems": errors,
        "must_fix": [
            "Jangan memperluas tugas melebihi permintaan user.",
            "Ikuti input, output, larangan, dan kewajiban yang user sebutkan.",
            "Kalau user hanya minta satu bagian kecil, jangan ubah menjadi tugas besar.",
            "Kalau user melarang menjalankan request asli, jangan masukkan requests.get/post/request asli."
        ],
        "notes": "Plan ditolak sebelum coding agar Kumar belajar memahami batas tugas."
    }

def _parse_plan_fields(plan):
    text = _to_text(plan)
    fields = {}

    current_key = None
    current_value = []

    valid_keys = {
        "TASK_TYPE",
        "INPUT",
        "OUTPUT",
        "MUST_USE",
        "MUST_NOT_USE",
        "MUST_DO",
        "NOTES",
    }

    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip().upper()
            value = value.strip()

            if key in valid_keys:
                if current_key:
                    fields[current_key] = "\n".join(current_value).strip()

                current_key = key
                current_value = [value]
                continue

        if current_key:
            current_value.append(line)

    if current_key:
        fields[current_key] = "\n".join(current_value).strip()

    return fields


def _positive_plan_text(fields):
    """
    Bagian plan yang berarti 'dipakai/dilakukan'.
    MUST_NOT_USE sengaja tidak dimasukkan.
    """
    positive_keys = [
        "TASK_TYPE",
        "INPUT",
        "OUTPUT",
        "MUST_USE",
        "MUST_DO",
    ]

    return "\n".join(fields.get(key, "") for key in positive_keys).lower()

def detect_plan_mismatch(task, plan):
    """
    Mengecek apakah task_plan Kumar ngelantur dari permintaan user.

    Penting:
    Kata yang muncul di MUST_NOT_USE tidak dianggap sebagai fitur yang dipakai.
    Contoh:
    - MUST_USE: requests     => salah jika user tidak minta requests
    - MUST_NOT_USE: requests => benar, karena requests dilarang
    """
    task_l = _lower(task)
    plan_l = _lower(plan)

    fields = _parse_plan_fields(plan)
    positive_l = _positive_plan_text(fields)

    errors = []
    warnings = []

    task_files = _extract_filenames(task)

    # 1. Semua file yang user sebut harus muncul di plan.
    for file_name in sorted(task_files):
        if file_name.lower() not in plan_l:
            errors.append(f"Plan tidak menyebut file penting dari user: {file_name}")

    # 2. Plan jangan membuat OUTPUT file yang tidak diminta.
    # File di MUST_NOT_USE tidak dihitung sebagai output tambahan.
    output_files = _extract_filenames(fields.get("OUTPUT", ""))

    for file_name in sorted(output_files - task_files):
        if file_name in {"output.py", "clean.txt", "output.jsonl"}:
            errors.append(
                f"Plan menjadikan {file_name} sebagai OUTPUT, padahal user tidak meminta file itu."
            )

    # 3. Kalau user hanya minta URL dari curl, plan jangan berubah jadi converter besar.
    if _user_wants_only_url(task):
        forbidden_for_url_only = {
            "requests": "Plan memakai library requests padahal user hanya minta tampilkan URL.",
            "headers": "Plan memakai headers padahal user hanya minta URL.",
            "cookies": "Plan memakai cookies padahal user hanya minta URL.",
            "cookie": "Plan memakai cookie padahal user hanya minta URL.",
            "body": "Plan memakai body padahal user hanya minta URL.",
            "output.py": "Plan memakai output.py padahal user hanya minta print URL.",
            "status_code": "Plan memakai status_code padahal user hanya minta URL.",
            "response": "Plan memakai response padahal user hanya minta URL.",
        }

        for word, message in forbidden_for_url_only.items():
            if word in positive_l:
                errors.append(message)

        if "shlex" not in plan_l:
            errors.append("User meminta shlex.split, tapi plan tidak menegaskan shlex.split.")

    # 4. Kalau user tidak meminta library requests, plan jangan memakai requests.
    # Tapi requests di MUST_NOT_USE boleh.
    if "requests" in positive_l and not _user_wants_requests_library(task):
        errors.append(
            "Plan memakai requests padahal user tidak meminta generate kode Python requests."
        )

    # 5. Kalau user melarang menjalankan request asli,
    # dangerous word hanya salah kalau ada di bagian positif plan.
    if "jangan menjalankan request asli" in task_l:
        dangerous_words = [
            "requests.get",
            "requests.post",
            "requests.request",
            "print status_code",
            "print response",
        ]

        for word in dangerous_words:
            if word in positive_l:
                errors.append(
                    f"Plan memakai '{word}', padahal user melarang menjalankan request asli."
                )

        if "menjalankan request asli" not in plan_l:
            warnings.append(
                "User melarang menjalankan request asli, tapi plan kurang jelas menyebut larangan itu."
            )

    # 6. Kalau user minta argparse, plan harus menyebut argparse.
    if "argparse" in task_l and "argparse" not in plan_l:
        errors.append("User meminta argparse, tapi plan tidak menyebut argparse.")

    ok = len(errors) == 0

    return {
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "review": _make_review(errors, warnings),
    }


def build_replan_note(plan_check):
    errors = plan_check.get("errors", [])
    warnings = plan_check.get("warnings", [])

    lines = []
    lines.append("PLAN SEBELUMNYA DITOLAK.")
    lines.append("Kesalahan plan:")

    for item in errors:
        lines.append(f"- {item}")

    if warnings:
        lines.append("Peringatan:")
        for item in warnings:
            lines.append(f"- {item}")

    lines.append("")
    lines.append("Buat ulang TASK PLAN.")
    lines.append("Jangan memperluas tugas.")
    lines.append("Jangan menambah output/library/fitur yang tidak diminta user.")

    return "\n".join(lines)