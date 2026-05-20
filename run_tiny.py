import json
import math
import re
import os
import time
import sys
import subprocess
import shutil
import uuid
from guru_mimo import ask_mimo_guru, verify_mimo_lesson

BRAIN_FILE = "brain.json"
CORRECTION_FILE = "memory_koreksi.jsonl"
GURU_FILE = "memory_guru.jsonl"
KNOWLEDGE_FILE = "knowledge_memory.jsonl"
VERIFIER_FILE = "verifier_log.jsonl"
REFLECTION_FILE = "reflection_log.jsonl"
BACKUP_DIR = "backup_brain"
AUTO_VERIFY = True
SCHEMA_VERSION = "1.0"
PASSIVE_TRAIN_EVERY = 3
BACKUP_KEEP_LAST = 5

AUTO_GURU = True
AUTO_TRAIN = True
MIN_KNOWN_RATIO = 0.45
BASIC_LABELS = {"sapa", "identitas", "koreksi", "setuju"}

STOPWORDS = {
    "apa", "apakah", "yang", "di", "ke", "dari", "dan", "atau",
    "itu", "ini", "mereka", "dia", "saya", "kamu", "bisa",
    "kalau", "jika", "dalam", "sebuah", "ada", "banyak","daripada", "mana", "lebih"
}

MIN_IMPORTANT_UNKNOWN = 1
LOW_CONFIDENCE_REFINE = 0.75

brain = {}
vocab = []
labels = []
responses = {}
W1 = []
b1 = []
W2 = []
b2 = []
hidden_size = 0
word_to_id = {}
id_to_label = {}


def tokenize(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.split()


def load_json_list(path):
    if not os.path.exists(path):
        return []

    data = []

    if path.endswith(".jsonl"):
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()

                if not line:
                    continue

                try:
                    obj = json.loads(line)

                    if isinstance(obj, dict):
                        data.append(obj)
                    else:
                        print(f"[WARN] Skip bukan object di {path}:{line_no}")

                except Exception as e:
                    print(f"[WARN] Baris rusak di {path}:{line_no} -> {e}")

        return data

    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        if isinstance(loaded, list):
            return [x for x in loaded if isinstance(x, dict)]

        if isinstance(loaded, dict):
            return [loaded]

    except Exception as e:
        print(f"[WARN] Gagal baca {path}: {e}")

    return []


def save_json_list(path, item):
    if path.endswith(".jsonl"):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        return

    data = load_json_list(path)
    data.append(item)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def make_record_id(prefix):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    random_part = uuid.uuid4().hex[:8]
    return f"{prefix}_{timestamp}_{random_part}"


def base_record(record_type, source):
    return {
        "schema_version": SCHEMA_VERSION,
        "id": make_record_id(record_type),
        "record_type": record_type,
        "source": source,
        "time": time.time(),
        "created_by": "kumar"
    }

def save_reflection_log(
    user_input,
    old_result,
    guru_result=None,
    verifier_result=None,
    final_lesson=None,
    training_success=False
):
    item = {
        **base_record("reflection", "kumar_learning_loop"),
        "input": user_input,

        "kumar_before": {
            "label": old_result.get("label") if old_result else None,
            "confidence": old_result.get("confidence") if old_result else None,
            "output": old_result.get("output") if old_result else None,
            "response_source": old_result.get("response_source") if old_result else None,
            "known_tokens": old_result.get("known_tokens") if old_result else [],
            "unknown_tokens": old_result.get("unknown_tokens") if old_result else [],
            "important_unknown": old_result.get("important_unknown") if old_result else []
        },

        "guru": {
            "ok": guru_result.get("ok") if guru_result else None,
            "target": guru_result.get("target") if guru_result else None,
            "reason": guru_result.get("reason") if guru_result else None,
            "response": guru_result.get("response") if guru_result else None,
            "is_new_label": guru_result.get("is_new_label") if guru_result else None
        },

        "verifier": {
            "ok": verifier_result.get("ok") if verifier_result else None,
            "approved": verifier_result.get("approved") if verifier_result else None,
            "status": verifier_result.get("status") if verifier_result else None,
            "reason": verifier_result.get("reason") if verifier_result else None
        },

        "final_lesson": {
            "target": final_lesson.get("target") if final_lesson else None,
            "response": final_lesson.get("response") if final_lesson else None,
            "subject": final_lesson.get("subject") if final_lesson else None,
            "facts": final_lesson.get("facts") if final_lesson else [],
            "examples_count": len(final_lesson.get("examples", [])) if final_lesson else 0
        },

        # kompatibel dengan versi lama
        "training_success": training_success,

        "training": {
            "success": training_success,
            "status": "success" if training_success else "failed_or_skipped"
        }
    }

    save_json_list(REFLECTION_FILE, item)

def backup_brain():
    if not os.path.exists(BRAIN_FILE):
        return None

    os.makedirs(BACKUP_DIR, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"brain_{timestamp}.json")

    shutil.copy2(BRAIN_FILE, backup_path)

    cleanup_old_backups()

    return backup_path


def restore_brain(backup_path):
    if not backup_path:
        return False

    if not os.path.exists(backup_path):
        return False

    shutil.copy2(backup_path, BRAIN_FILE)
    return True

def save_knowledge_from_guru(text, hasil_guru):
    facts = hasil_guru.get("facts", [])

    if not facts:
        response = hasil_guru.get("response", "")
        if response:
            facts = [response]

    item = {
        **base_record("knowledge", "mimo_guru_verified"),
        "input": text,
        "input_asli": text,

        # kompatibel dengan pencarian lama
        "label": hasil_guru.get("target"),
        "target": hasil_guru.get("target"),

        "subject": hasil_guru.get("subject", hasil_guru.get("target")),
        "response": hasil_guru.get("response", ""),
        "facts": facts,

        "metadata": {
            "fact_count": len(facts),
            "is_new_label": hasil_guru.get("is_new_label", False)
        }
    }

    save_json_list(KNOWLEDGE_FILE, item)


def load_brain():
    global brain, vocab, labels, responses
    global W1, b1, W2, b2, hidden_size
    global word_to_id, id_to_label

    if not os.path.exists(BRAIN_FILE):
        raise FileNotFoundError("brain.json belum ada. Jalankan dulu: python train_tiny.py")

    with open(BRAIN_FILE, "r", encoding="utf-8") as f:
        brain = json.load(f)

    if "W1" not in brain:
        raise RuntimeError("brain.json masih versi lama. Jalankan dulu: python train_tiny.py")

    vocab = brain["vocab"]
    labels = brain["labels"]
    responses = brain["responses"]

    W1 = brain["W1"]
    b1 = brain["b1"]
    W2 = brain["W2"]
    b2 = brain["b2"]

    hidden_size = brain["hidden_size"]

    word_to_id = {word: i for i, word in enumerate(vocab)}
    id_to_label = {i: label for i, label in enumerate(labels)}


def softmax(logits):
    m = max(logits)
    exps = [math.exp(x - m) for x in logits]
    total = sum(exps)
    return [x / total for x in exps]


def relu(x):
    return x if x > 0 else 0.0


def vectorize(text):
    vec = [0.0 for _ in range(len(vocab))]
    known_tokens = []
    unknown_tokens = []

    for token in tokenize(text):
        if token in word_to_id:
            vec[word_to_id[token]] = 1.0
            known_tokens.append(token)
        else:
            unknown_tokens.append(token)

    return vec, known_tokens, unknown_tokens

def important_unknown_tokens(unknown_tokens):
    hasil = []

    for token in unknown_tokens:
        if token in STOPWORDS:
            continue

        if len(token) <= 2:
            continue

        hasil.append(token)

    return hasil


def forward(x_vec):
    hidden_raw = []

    for h in range(hidden_size):
        score = b1[h]

        for word_id in range(len(vocab)):
            score += W1[h][word_id] * x_vec[word_id]

        hidden_raw.append(score)

    hidden = [relu(x) for x in hidden_raw]

    logits = []

    for label_id in range(len(labels)):
        score = b2[label_id]

        for h in range(hidden_size):
            score += W2[label_id][h] * hidden[h]

        logits.append(score)

    probs = softmax(logits)

    return hidden_raw, hidden, logits, probs


def similarity_score(text1, text2):
    tokens1 = set(tokenize(text1))
    tokens2 = set(tokenize(text2))

    if not tokens1 or not tokens2:
        return 0.0

    same = tokens1 & tokens2
    total = tokens1 | tokens2

    return len(same) / len(total)

def important_token_set(text):
    tokens = tokenize(text)
    hasil = set()

    for token in tokens:
        if token in STOPWORDS:
            continue

        if len(token) <= 2:
            continue

        hasil.add(token)

    return hasil


def semantic_similarity_score(text1, text2):
    tokens1 = important_token_set(text1)
    tokens2 = important_token_set(text2)

    if not tokens1 or not tokens2:
        return similarity_score(text1, text2)

    same = tokens1 & tokens2
    union = tokens1 | tokens2

    jaccard = len(same) / len(union)

    coverage_small = len(same) / min(len(tokens1), len(tokens2))
    coverage_big = len(same) / max(len(tokens1), len(tokens2))

    return max(jaccard, coverage_small, coverage_big)


def normalized_text_key(text):
    return " ".join(tokenize(text))


def collect_lesson_texts(lesson):
    texts = []

    for key in ["input", "input_asli"]:
        value = lesson.get(key, "")
        if value:
            texts.append(value)

    for ex in lesson.get("examples", []):
        if isinstance(ex, dict):
            value = ex.get("input", "")
            if value:
                texts.append(value)

    return texts


def find_similar_lesson(text, threshold=0.65):
    lessons = load_json_list(GURU_FILE)

    text_key = normalized_text_key(text)

    best = None
    best_score = 0.0
    best_matched_text = None

    for lesson in lessons:
        target = lesson.get("target")

        if not target:
            continue

        for candidate in collect_lesson_texts(lesson):
            candidate_key = normalized_text_key(candidate)

            # Kalau exact match, langsung pakai
            if candidate_key and candidate_key == text_key:
                return lesson, 1.0, candidate

            score = semantic_similarity_score(text, candidate)

            if score > best_score:
                best_score = score
                best = lesson
                best_matched_text = candidate

    if best and best_score >= threshold:
        return best, best_score, best_matched_text

    return None, best_score, best_matched_text


def training_example_exists(text, target):
    text_key = normalized_text_key(text)

    if not text_key:
        return False

    # cek dari pelajaran guru
    for lesson in load_json_list(GURU_FILE):
        if lesson.get("target") != target:
            continue

        for candidate in collect_lesson_texts(lesson):
            if normalized_text_key(candidate) == text_key:
                return True

    # cek dari koreksi/manual/derived example
    for item in load_json_list(CORRECTION_FILE):
        if item.get("target") != target:
            continue

        if normalized_text_key(item.get("input", "")) == text_key:
            return True

    return False


def save_derived_example(text, target, source_lesson, score, matched_text):
    item = {
        **base_record("derived_example", "similarity_duplicate_guard"),
        "input": text,
        "target": target,

        "matched_text": matched_text,
        "similarity_score": score,

        "source_lesson_id": source_lesson.get("id"),
        "source_label": source_lesson.get("target"),
        "source_response": source_lesson.get("response", ""),

        "metadata": {
            "reason": "Pertanyaan mirip dengan pelajaran lama, jadi tidak perlu tanya guru MiMo.",
            "training_required": True
        }
    }

    save_json_list(CORRECTION_FILE, item)

def count_untrained_passive_examples():
    count = 0

    for item in load_json_list(CORRECTION_FILE):
        if item.get("record_type") != "derived_example":
            continue

        metadata = item.get("metadata", {})

        if metadata.get("training_required") is True:
            count += 1

    return count


def mark_passive_examples_trained():
    items = load_json_list(CORRECTION_FILE)
    changed = False

    for item in items:
        if item.get("record_type") != "derived_example":
            continue

        metadata = item.setdefault("metadata", {})

        if metadata.get("training_required") is True:
            metadata["training_required"] = False
            metadata["trained_at"] = time.time()
            changed = True

    if not changed:
        return False

    with open(CORRECTION_FILE, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    return True

def jsonl_health(path):
    result = {
        "file": path,
        "exists": os.path.exists(path),
        "valid": 0,
        "bad": 0,
        "missing_schema": 0
    }

    if not result["exists"]:
        return result

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            try:
                obj = json.loads(line)

                if not isinstance(obj, dict):
                    result["bad"] += 1
                    continue

                result["valid"] += 1

                if (
                    "schema_version" not in obj
                    or "id" not in obj
                    or "record_type" not in obj
                ):
                    result["missing_schema"] += 1

            except Exception:
                result["bad"] += 1

    return result


def count_backups():
    if not os.path.exists(BACKUP_DIR):
        return 0

    return len([
        name for name in os.listdir(BACKUP_DIR)
        if name.endswith(".json")
    ])


def print_data_health():
    files = [
        GURU_FILE,
        CORRECTION_FILE,
        KNOWLEDGE_FILE,
        REFLECTION_FILE,
        VERIFIER_FILE
    ]

    print()
    print("=== DATA HEALTH ===")

    for path in files:
        h = jsonl_health(path)

        if not h["exists"]:
            print(path, "belum ada")
            continue

        print(
            path,
            "valid:", h["valid"],
            "bad:", h["bad"],
            "missing_schema:", h["missing_schema"]
        )

    print()
    print("Passive pending:", count_untrained_passive_examples())
    print("Backup brain   :", count_backups())
    print("===================")

def cleanup_old_backups():
    if not os.path.exists(BACKUP_DIR):
        return

    backups = []

    for name in os.listdir(BACKUP_DIR):
        if not name.endswith(".json"):
            continue

        path = os.path.join(BACKUP_DIR, name)
        backups.append((os.path.getmtime(path), path))

    backups.sort(reverse=True)

    old_backups = backups[BACKUP_KEEP_LAST:]

    for _, path in old_backups:
        try:
            os.remove(path)
            print("[Kumar] Backup lama dihapus:", path)
        except Exception as e:
            print("[WARN] Gagal hapus backup lama:", path, e)

def passive_learn_from_memory(text, result):
    if result.get("response_source") != "memory_guru":
        return False

    label = result.get("label")

    if not label:
        return False

    if training_example_exists(text, label):
        return False

    similar_lesson, similar_score, matched_text = find_similar_lesson(text, threshold=0.65)

    if not similar_lesson:
        return False

    save_derived_example(
        text,
        label,
        similar_lesson,
        similar_score,
        matched_text
    )

    print("[Kumar] Passive learning: contoh mirip disimpan ke memory_koreksi.jsonl.")
    return True

def find_guru_response(text, label):
    lessons = load_json_list(GURU_FILE)

    text_key = normalized_text_key(text)

    best_score = 0.0
    best_response = None
    best_input = None

    for lesson in lessons:
        if lesson.get("target") != label:
            continue

        response = lesson.get("response", "").strip()

        if not response:
            continue

        for candidate in collect_lesson_texts(lesson):
            candidate_key = normalized_text_key(candidate)

            # Prioritas 1: kalau kalimatnya sama persis setelah dinormalisasi
            if candidate_key and candidate_key == text_key:
                return response, 1.0, candidate

            # Prioritas 2: pakai similarity semantik
            score = semantic_similarity_score(text, candidate)

            if score > best_score:
                best_score = score
                best_response = response
                best_input = candidate

    if best_score >= 0.65:
        return best_response, best_score, best_input

    return None, best_score, best_input

def find_knowledge_response(text, label):
    knowledge_items = load_json_list(KNOWLEDGE_FILE)

    best_score = 0.0
    best_item = None

    for item in knowledge_items:
        if item.get("label") != label:
            continue

        base_text = " ".join([
            item.get("input_asli", ""),
            item.get("subject", ""),
            " ".join(item.get("facts", []))
        ])

        score = similarity_score(text, base_text)

        if score > best_score:
            best_score = score
            best_item = item

    if not best_item or best_score < 0.25:
        return None, best_score, None

    facts = best_item.get("facts", [])
    response = best_item.get("response", "")

    if facts:
        answer = " ".join(facts[:3])
    else:
        answer = response

    return answer, best_score, best_item.get("subject")


def predict(text):
    x_vec, known_tokens, unknown_tokens = vectorize(text)
    important_unknown = important_unknown_tokens(unknown_tokens)

    total_tokens = len(known_tokens) + len(unknown_tokens)

    if total_tokens == 0:
        known_ratio = 0.0
    else:
        known_ratio = len(known_tokens) / total_tokens

    if len(known_tokens) == 0:
        return {
            "label": None,
            "confidence": 0.0,
            "output": "Saya belum mengenal kata-kata itu.",
            "known_tokens": known_tokens,
            "unknown_tokens": unknown_tokens,
            "important_unknown": important_unknown,
            "known_ratio": known_ratio,
            "active_neurons": [],
            "response_source": "unknown_words",
            "guru_match_score": 0.0,
            "guru_match_input": None
        }

    if known_ratio < MIN_KNOWN_RATIO:
        return {
            "label": None,
            "confidence": 0.0,
            "output": "Kosakata baru terlalu banyak. Saya perlu belajar dari guru dulu.",
            "known_tokens": known_tokens,
            "unknown_tokens": unknown_tokens,
            "important_unknown": important_unknown,
            "known_ratio": known_ratio,
            "active_neurons": [],
            "response_source": "unknown_words",
            "guru_match_score": 0.0,
            "guru_match_input": None
        }

    hidden_raw, hidden, logits, probs = forward(x_vec)

    best_id = max(range(len(probs)), key=lambda i: probs[i])
    label = id_to_label[best_id]
    confidence = probs[best_id]

    active_neurons = sorted(
        [(i, value) for i, value in enumerate(hidden)],
        key=lambda x: x[1],
        reverse=True
    )[:3]

    if confidence < 0.55:
        return {
            "label": None,
            "confidence": confidence,
            "output": "Saya belum yakin maksudnya, Bos.",
            "known_tokens": known_tokens,
            "unknown_tokens": unknown_tokens,
            "important_unknown": important_unknown,
            "known_ratio": known_ratio,
            "active_neurons": active_neurons,
            "response_source": "low_confidence",
            "guru_match_score": 0.0,
            "guru_match_input": None
        }

    guru_response, guru_score, guru_input = find_guru_response(text, label)
    knowledge_response, knowledge_score, knowledge_subject = find_knowledge_response(text, label)

    if guru_response:
        final_output = guru_response
        response_source = "memory_guru"

    elif knowledge_response:
        final_output = knowledge_response
        response_source = "knowledge_memory"
        guru_score = knowledge_score
        guru_input = knowledge_subject

    elif label.startswith("pengetahuan"):
        final_output = "Saya tahu ini termasuk pengetahuan, tapi saya belum punya jawaban spesifik."
        response_source = "butuh_guru"

    else:
        final_output = responses.get(label, label)
        response_source = "label_default"

    return {
        "label": label,
        "confidence": confidence,
        "output": final_output,
        "known_tokens": known_tokens,
        "unknown_tokens": unknown_tokens,
        "important_unknown": important_unknown,
        "known_ratio": known_ratio,
        "active_neurons": active_neurons,
        "response_source": response_source,
        "guru_match_score": guru_score,
        "guru_match_input": guru_input
    }


def should_auto_learn(result):
    if not AUTO_GURU:
        return False

    source = result.get("response_source")
    label = result.get("label")
    confidence = result.get("confidence", 0.0)
    known_ratio = result.get("known_ratio", 0.0)
    important_unknown = result.get("important_unknown", [])

    # Kasus jelas tidak tahu
    if source in ["unknown_words", "low_confidence", "butuh_guru"]:
        return True

    # Kasus jawaban default, tapi ada konsep baru
    if source == "label_default" and label:
        
        # Kalau label dasar seperti identitas/sapa/koreksi/setuju,
        # tapi kalimatnya kompleks atau ada banyak kata penting baru,
        # berarti kemungkinan salah klasifikasi.
        if label in BASIC_LABELS:
            if len(important_unknown) >= 2:
                return True

            if confidence < LOW_CONFIDENCE_REFINE and known_ratio < 0.8:
                return True

            return False

        # Kalau label non-basic seperti ekonomi_ai/skenario_ai,
        # satu kata penting baru saja cukup untuk memperdalam ilmu.
        if len(important_unknown) >= MIN_IMPORTANT_UNKNOWN:
            return True

        # Kalau confidence tidak terlalu kuat, tetap refine.
        if confidence < LOW_CONFIDENCE_REFINE and known_ratio < 0.8:
            return True

    return False


def train_brain_again():
    print()
    print("[Kumar] Membackup brain lama...")

    backup_path = backup_brain()

    if backup_path:
        print("[Kumar] Backup brain:", backup_path)
    else:
        print("[Kumar] Tidak ada brain lama untuk dibackup.")

    print("[Kumar] Melatih ulang otak sendiri...")

    try:
        result = subprocess.run(
            [sys.executable, "train_tiny.py"],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            print("[Kumar] Training gagal.")
            print(result.stderr)

            if restore_brain(backup_path):
                print("[Kumar] Brain lama berhasil dipulihkan dari backup.")

            return False

        lines = result.stdout.strip().splitlines()
        last_lines = lines[-8:]

        for line in last_lines:
            print(line)

        print("[Kumar] Training selesai.")
        return True

    except Exception as e:
        print("[Kumar] Gagal menjalankan training:", e)

        if restore_brain(backup_path):
            print("[Kumar] Brain lama berhasil dipulihkan dari backup.")

        return False


def auto_learn_from_guru(text, old_result):
    print()
    print("[Kumar] Saya belum punya jawaban aman.")

    similar_lesson, similar_score, matched_text = find_similar_lesson(text)

    if similar_lesson:
        target = similar_lesson.get("target")

        print("[Kumar] Menemukan pelajaran lama yang mirip.")
        print("[Kumar] Label lama:", target)
        print("[Kumar] Skor mirip:", round(similar_score, 3))
        print("[Kumar] Tidak perlu bertanya ke MiMo.")

        if not training_example_exists(text, target):
            save_derived_example(
                text,
                target,
                similar_lesson,
                similar_score,
                matched_text
            )
            print("[Kumar] Contoh baru disimpan ke memory_koreksi.jsonl.")
        else:
            print("[Kumar] Contoh ini sudah pernah disimpan.")

        training_success = False

        if AUTO_TRAIN:
            ok = train_brain_again()

            if ok:
                print("[Kumar] Memuat ulang brain.json...")
                load_brain()
                print("[Kumar] Otak baru aktif.")
                training_success = True
                if count_untrained_passive_examples() > 0:
                    mark_passive_examples_trained()
                    print("[Kumar] Passive example yang ikut training ditandai selesai.")

        save_reflection_log(
            text,
            old_result,
            guru_result={
                "ok": True,
                "target": target,
                "reason": "Dipakai dari pelajaran lama yang mirip.",
                "response": similar_lesson.get("response", ""),
                "is_new_label": False
            },
            verifier_result={
                "ok": True,
                "approved": True,
                "status": "skipped_duplicate",
                "reason": "Tidak memanggil verifier karena memakai pelajaran lama yang mirip."
            },
            final_lesson=similar_lesson,
            training_success=training_success
        )

        print("[Kumar] Refleksi duplicate-skip disimpan ke reflection_log.jsonl.")
        return True

    print("[Kumar] Bertanya ke guru MiMo...")

    hasil_guru = ask_mimo_guru(text, labels)

    hasil_verifier = None
    final_lesson = None
    training_success = False

    if not hasil_guru.get("ok"):
        print("[Kumar] Guru gagal memberi pelajaran.")
        print("Error:", hasil_guru.get("error"))

        save_reflection_log(
            text,
            old_result,
            guru_result=hasil_guru,
            verifier_result=None,
            final_lesson=None,
            training_success=False
        )

        print("[Kumar] Refleksi gagal belajar disimpan ke reflection_log.json.")
        return False

    # =========================
    # VERIFIER OTOMATIS
    # =========================
    if AUTO_VERIFY:
        print("[Kumar] Mengirim pelajaran ke verifier otomatis...")

        hasil_verifier = verify_mimo_lesson(
            text,
            labels,
            hasil_guru,
            old_result
        )

        save_json_list(VERIFIER_FILE, {
            **base_record("verifier_result", "mimo_verifier"),
            "input": text,
            "guru_lesson": hasil_guru,
            "verifier_result": hasil_verifier,
            "verifier_status": hasil_verifier.get("status"),
            "approved": hasil_verifier.get("approved"),
            "reason": hasil_verifier.get("reason")
        })

        if not hasil_verifier.get("ok"):
            print("[Verifier] Gagal memeriksa pelajaran.")
            print("Error:", hasil_verifier.get("error"))

            save_reflection_log(
                text,
                old_result,
                guru_result=hasil_guru,
                verifier_result=hasil_verifier,
                final_lesson=None,
                training_success=False
            )

            print("[Kumar] Refleksi gagal verifier disimpan ke reflection_log.json.")
            return False

        if not hasil_verifier.get("approved"):
            print("[Verifier] Pelajaran ditolak.")
            print("Alasan:", hasil_verifier.get("reason"))

            raw = hasil_verifier.get("raw")
            if raw:
                print("[Verifier raw]:", raw[:500])

            print("[Kumar] Pelajaran tidak disimpan ke otak.")

            save_reflection_log(
                text,
                old_result,
                guru_result=hasil_guru,
                verifier_result=hasil_verifier,
                final_lesson=None,
                training_success=False
            )

            print("[Kumar] Refleksi penolakan disimpan ke reflection_log.json.")
            return False

        hasil_guru = hasil_verifier["lesson"]
        final_lesson = hasil_guru

        print("[Verifier] Status:", hasil_verifier.get("status"))
        print("[Verifier] Catatan:", hasil_verifier.get("reason", "-"))

    if final_lesson is None:
        final_lesson = hasil_guru

        # =========================
    # SIMPAN PELAJARAN FINAL
    # =========================
    lesson = {
        **base_record("teacher_lesson", "mimo_guru_verified"),
        "auto_learn": True,

        # input utama
        "input": text,
        "input_asli": text,

        # kondisi Kumar sebelum belajar
        "old_result": {
            "label": old_result.get("label"),
            "output": old_result.get("output"),
            "confidence": old_result.get("confidence"),
            "response_source": old_result.get("response_source"),
            "known_tokens": old_result.get("known_tokens", []),
            "unknown_tokens": old_result.get("unknown_tokens", []),
            "important_unknown": old_result.get("important_unknown", [])
        },

        # field lama agar train_tiny.py tetap aman
        "old_label": old_result.get("label"),
        "old_output": old_result.get("output"),
        "old_confidence": old_result.get("confidence"),

        # hasil pelajaran final
        "target": hasil_guru["target"],
        "is_new_label": hasil_guru.get("is_new_label", False),
        "reason": hasil_guru.get("reason", ""),
        "response": hasil_guru.get("response", ""),
        "subject": hasil_guru.get("subject", hasil_guru.get("target")),
        "facts": hasil_guru.get("facts", []),
        "examples": hasil_guru.get("examples", []),

        # verifier
        "verifier_status": hasil_verifier.get("status") if hasil_verifier else None,
        "verifier_reason": hasil_verifier.get("reason") if hasil_verifier else None,

        "metadata": {
            "example_count": len(hasil_guru.get("examples", [])),
            "fact_count": len(hasil_guru.get("facts", [])),
            "training_required": True
        }
    }

    save_json_list(GURU_FILE, lesson)
    save_knowledge_from_guru(text, hasil_guru)

    print("[Guru] Label:", hasil_guru["target"])

    if hasil_guru.get("is_new_label"):
        print("[Guru] Status: LABEL BARU")
    else:
        print("[Guru] Status: label lama")

    print("[Guru] Alasan:", hasil_guru.get("reason", "-"))
    print("[Guru] Jawaban:", hasil_guru.get("response", "-"))
    print("[Kumar] Pelajaran disimpan ke memory_guru.jsonl")

    # =========================
    # TRAINING ULANG
    # =========================
    if AUTO_TRAIN:
        ok = train_brain_again()

        if not ok:
            save_reflection_log(
                text,
                old_result,
                guru_result=hasil_guru,
                verifier_result=hasil_verifier,
                final_lesson=final_lesson,
                training_success=False
            )

            print("[Kumar] Refleksi training gagal disimpan ke reflection_log.json.")
            return False

        print("[Kumar] Memuat ulang brain.json...")
        load_brain()
        print("[Kumar] Otak baru aktif.")
        training_success = True

    # =========================
    # REFLECTION LOG
    # =========================
    save_reflection_log(
        text,
        old_result,
        guru_result=hasil_guru,
        verifier_result=hasil_verifier,
        final_lesson=final_lesson,
        training_success=training_success
    )

    print("[Kumar] Refleksi belajar disimpan ke reflection_log.jsonl.")

    return True


def print_result(result):
    if result["label"]:
        print("Label:", result["label"])

    print("Confidence:", round(result["confidence"], 4))
    print("Known tokens:", result["known_tokens"])
    print("Unknown tokens:", result["unknown_tokens"])
    print("Important unknown:", result.get("important_unknown", []))
    print("Known ratio:", round(result.get("known_ratio", 0.0), 3))

    if result["active_neurons"]:
        neuron_text = ", ".join(
            [f"n{i}={round(v, 3)}" for i, v in result["active_neurons"]]
        )
        print("Neuron aktif:", neuron_text)

    if result.get("response_source"):
        print("Sumber jawaban:", result["response_source"])

    if result.get("guru_match_input"):
        print("Cocok dengan sumber:", result["guru_match_input"])
        print("Skor cocok:", round(result.get("guru_match_score", 0.0), 3))

    print("Output:", result["output"])


load_brain()

print("Kumar Tiny Brain v3 Auto-Learn aktif.")
print("Arsitektur: input -> hidden neuron -> label")
print("Hidden neuron:", hidden_size)
print("Mode:")
print("- Jika tahu, Kumar menjawab sendiri")
print("- Jika tidak tahu, Kumar otomatis bertanya ke MiMo")
print("- Setelah diajari, Kumar otomatis training ulang")
print()
print("Perintah debug:")
print("- /salah  = koreksi jawaban terakhir secara manual")
print("- /label  = lihat daftar label")
print("- /brain  = lihat info otak")
print("- /data   = cek kesehatan dataset")
print("- keluar  = berhenti")
print()

last_input = None
last_result = None
waiting_correction = False

while True:
    user = input("\nInput: ").strip()

    if not user:
        continue

    user_lower = user.lower()

    if user_lower in ["keluar", "exit", "quit"]:
        break

    if user_lower == "/brain":
        print("Nama model :", brain.get("name"))
        print("Tipe model :", brain.get("model_type"))
        print("Vocab      :", len(vocab))
        print("Label      :", len(labels))
        print("Neuron     :", hidden_size)
        print("Stats      :", brain.get("stats", {}))
        continue

    if user_lower == "/data":
        print_data_health()
        continue

    if user_lower == "/label":
        print("Label yang tersedia:")
        for label in labels:
            print("-", label)
        continue

    if user_lower == "/salah":
        if not last_input or not last_result:
            print("Belum ada jawaban yang bisa dikoreksi.")
            continue

        print("Jawaban terakhir akan dikoreksi.")
        print("Input terakhir :", last_input)
        print("Label lama     :", last_result.get("label"))
        print("Output lama    :", last_result.get("output"))
        print()
        print("Masukkan label yang benar.")
        print("Pilihan:", ", ".join(labels))
        waiting_correction = True
        continue

    if waiting_correction:
        correct_label = user_lower.strip()

        if correct_label not in labels:
            print("Label tidak dikenal.")
            print("Pakai salah satu:", ", ".join(labels))
            continue

        save_json_list(CORRECTION_FILE, {
            **base_record("manual_correction", "user_feedback"),
            "input": last_input,
            "target": correct_label,

            # kompatibel dengan train_tiny.py
            "wrong_label": last_result.get("label"),
            "wrong_output": last_result.get("output"),
            "confidence": last_result.get("confidence"),

            "old_result": {
                "label": last_result.get("label"),
                "output": last_result.get("output"),
                "confidence": last_result.get("confidence")
            }
        })

        print("Koreksi disimpan ke memory_koreksi.jsonl.")
        print("Melatih ulang dari koreksi manual...")

        if train_brain_again():
            load_brain()
            print("Otak baru dari koreksi manual aktif.")

        waiting_correction = False
        continue

    result = predict(user)

    last_input = user
    last_result = result

    passive_saved = passive_learn_from_memory(user, result)

    if passive_saved and AUTO_TRAIN:
        pending_passive = count_untrained_passive_examples()

        print(f"[Kumar] Passive pending: {pending_passive}/{PASSIVE_TRAIN_EVERY}")

        if pending_passive >= PASSIVE_TRAIN_EVERY:
            print("[Kumar] Passive example sudah cukup, training ulang...")

            if train_brain_again():
                load_brain()
                mark_passive_examples_trained()
                print("[Kumar] Otak diperkuat dari batch passive learning.")
        else:
            print("[Kumar] Training ditunda sampai passive example cukup.")

    if should_auto_learn(result):
        learned = auto_learn_from_guru(user, result)

        if learned:
            print()
            print("[Kumar] Saya coba jawab ulang setelah belajar...")
            result = predict(user)
            last_result = result

    print()
    print_result(result)