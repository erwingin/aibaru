import json
import os

FILES = [
    "memory_guru.jsonl",
    "memory_koreksi.jsonl",
    "knowledge_memory.jsonl",
    "reflection_log.jsonl",
    "verifier_log.jsonl",
]


def extract_json_objects(text):
    objects = []
    decoder = json.JSONDecoder()
    i = 0

    while i < len(text):
        while i < len(text) and text[i] not in "{[":
            i += 1

        if i >= len(text):
            break

        try:
            obj, end = decoder.raw_decode(text, i)

            if isinstance(obj, list):
                for item in obj:
                    if isinstance(item, dict):
                        objects.append(item)

            elif isinstance(obj, dict):
                objects.append(obj)

            i = end

        except Exception:
            i += 1

    return objects


def dedupe_records(records):
    seen = set()
    clean = []

    for item in records:
        if not isinstance(item, dict):
            continue

        key = json.dumps(item, sort_keys=True, ensure_ascii=False)

        if key in seen:
            continue

        seen.add(key)
        clean.append(item)

    return clean


for path in FILES:
    if not os.path.exists(path):
        print(path, "tidak ada, dilewati")
        continue

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    records = extract_json_objects(text)
    records = dedupe_records(records)

    backup_path = path + ".bak"

    with open(backup_path, "w", encoding="utf-8") as f:
        f.write(text)

    with open(path, "w", encoding="utf-8") as f:
        for item in records:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(path, "dibersihkan:", len(records), "record")
    print("backup lama:", backup_path)