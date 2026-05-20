import json
import math
import random
import re
import os
import time

DATASET_FILE = "dataset_kecil.json"
BRAIN_FILE = "brain.json"
CORRECTION_FILE = "memory_koreksi.jsonl"
GURU_FILE = "memory_guru.jsonl"

OLD_CORRECTION_FILE = "memory_koreksi.json"
OLD_GURU_FILE = "memory_guru.json"


HIDDEN_SIZE = 8
LEARNING_RATE = 0.05

# CPU friendly
EPOCHS = 1200
PRINT_EVERY = 100
TARGET_LOSS = 0.01
PATIENCE = 8
MIN_IMPROVEMENT = 0.0005

random.seed(42)


def tokenize(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.split()


def normalize_record(item):
    if isinstance(item, dict):
        return item

    if isinstance(item, str):
        item = item.strip()

        if not item:
            return None

        try:
            parsed = json.loads(item)

            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None

    return None


def load_json_list(path):
    if not os.path.exists(path):
        return []

    data = []

    # Baca JSONL
    if path.endswith(".jsonl"):
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()

                if not line:
                    continue

                try:
                    loaded = json.loads(line)
                except Exception as e:
                    print(f"[WARN] Baris rusak di {path}:{line_no} -> {e}")
                    continue

                if isinstance(loaded, list):
                    for item in loaded:
                        record = normalize_record(item)
                        if record:
                            data.append(record)
                else:
                    record = normalize_record(loaded)
                    if record:
                        data.append(record)

        return data

    # Baca JSON lama
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        if isinstance(loaded, list):
            for item in loaded:
                record = normalize_record(item)
                if record:
                    data.append(record)

        else:
            record = normalize_record(loaded)
            if record:
                data.append(record)

    except Exception as e:
        print(f"[WARN] Gagal baca {path}: {e}")

    return data


with open(DATASET_FILE, "r", encoding="utf-8") as f:
    dataset = json.load(f)

base_vocab = list(dataset["vocab"])
labels = list(dataset["labels"])
samples = list(dataset["samples"])
responses = dict(dataset["responses"])

corrections = load_json_list(OLD_CORRECTION_FILE) + load_json_list(CORRECTION_FILE)
guru_lessons = load_json_list(OLD_GURU_FILE) + load_json_list(GURU_FILE)

# Masukkan koreksi manual
for item in corrections:
    if not isinstance(item, dict):
        print("[WARN] Skip koreksi bukan object:", repr(item)[:80])
        continue

    target = item.get("target")
    input_text = item.get("input")

    if input_text and target:
        if target not in labels:
            labels.append(target)

        samples.append({
            "input": input_text,
            "target": target
        })

# Masukkan pelajaran dari guru MiMo
for lesson in guru_lessons:
    if not isinstance(lesson, dict):
        print("[WARN] Skip guru lesson bukan object:", repr(lesson)[:80])
        continue

    target = lesson.get("target")

    if not target:
        continue

    if target not in labels:
        labels.append(target)

    if target not in responses:
        responses[target] = lesson.get(
            "response",
            f"Saya sedang belajar tentang {target.replace('_', ' ')}."
        )

    for ex in lesson.get("examples", []):
        if not isinstance(ex, dict):
            continue

        input_text = ex.get("input")
        ex_target = ex.get("target")

        if input_text and ex_target:
            if ex_target not in labels:
                labels.append(ex_target)

            samples.append({
                "input": input_text,
                "target": ex_target
            })

# Vocab tumbuh otomatis dari semua sample
vocab_set = set(base_vocab)

for sample in samples:
    for token in tokenize(sample["input"]):
        vocab_set.add(token)

vocab = sorted(list(vocab_set))

word_to_id = {word: i for i, word in enumerate(vocab)}
label_to_id = {label: i for i, label in enumerate(labels)}
id_to_label = {i: label for label, i in label_to_id.items()}

vocab_size = len(vocab)
label_size = len(labels)

# =========================
# OTAK V3: INPUT -> HIDDEN -> OUTPUT
# =========================

# W1[hidden][word]
W1 = [
    [random.uniform(-0.1, 0.1) for _ in range(vocab_size)]
    for _ in range(HIDDEN_SIZE)
]

b1 = [0.0 for _ in range(HIDDEN_SIZE)]

# W2[label][hidden]
W2 = [
    [random.uniform(-0.1, 0.1) for _ in range(HIDDEN_SIZE)]
    for _ in range(label_size)
]

b2 = [0.0 for _ in range(label_size)]


def vectorize(text):
    vec = [0.0 for _ in range(vocab_size)]

    for token in tokenize(text):
        if token in word_to_id:
            vec[word_to_id[token]] = 1.0

    return vec


def relu(x):
    return x if x > 0 else 0.0


def relu_derivative(x):
    return 1.0 if x > 0 else 0.0


def softmax(logits):
    m = max(logits)
    exps = [math.exp(x - m) for x in logits]
    total = sum(exps)
    return [x / total for x in exps]


def forward(x_vec):
    hidden_raw = []

    for h in range(HIDDEN_SIZE):
        score = b1[h]

        for word_id in range(vocab_size):
            score += W1[h][word_id] * x_vec[word_id]

        hidden_raw.append(score)

    hidden = [relu(x) for x in hidden_raw]

    logits = []

    for label_id in range(label_size):
        score = b2[label_id]

        for h in range(HIDDEN_SIZE):
            score += W2[label_id][h] * hidden[h]

        logits.append(score)

    probs = softmax(logits)

    return hidden_raw, hidden, logits, probs


print("Mulai melatih Kumar Tiny Brain v3...")
print("Arsitektur     : input -> hidden neuron -> label")
print("Hidden neuron  :", HIDDEN_SIZE)
print("Sample dataset :", len(dataset["samples"]))
print("Sample koreksi :", len(corrections))
print("Sample guru    :", sum(len(x.get("examples", [])) for x in guru_lessons))
print("Total sample   :", len(samples))
print("Total vocab    :", len(vocab))
print("Total label    :", len(labels))

final_loss = 0.0
best_loss = float("inf")
no_improve_count = 0
stopped_reason = "max_epoch"

for epoch in range(EPOCHS):
    total_loss = 0.0
    random.shuffle(samples)

    for sample in samples:
        x_vec = vectorize(sample["input"])
        y = label_to_id[sample["target"]]

        hidden_raw, hidden, logits, probs = forward(x_vec)

        loss = -math.log(probs[y] + 1e-9)
        total_loss += loss

        # Gradient output
        grad_logits = probs[:]
        grad_logits[y] -= 1.0

        # Hitung gradient ke hidden sebelum W2 diubah
        grad_hidden = [0.0 for _ in range(HIDDEN_SIZE)]

        for h in range(HIDDEN_SIZE):
            for label_id in range(label_size):
                grad_hidden[h] += grad_logits[label_id] * W2[label_id][h]

        # Update W2 dan b2
        for label_id in range(label_size):
            b2[label_id] -= LEARNING_RATE * grad_logits[label_id]

            for h in range(HIDDEN_SIZE):
                W2[label_id][h] -= LEARNING_RATE * grad_logits[label_id] * hidden[h]

        # Update W1 dan b1
        for h in range(HIDDEN_SIZE):
            grad_h_raw = grad_hidden[h] * relu_derivative(hidden_raw[h])

            b1[h] -= LEARNING_RATE * grad_h_raw

            for word_id in range(vocab_size):
                W1[h][word_id] -= LEARNING_RATE * grad_h_raw * x_vec[word_id]

    final_loss = total_loss

    if epoch % PRINT_EVERY == 0:
        print(f"Epoch {epoch}, Loss: {total_loss:.4f}")

    # Stop kalau loss sudah cukup kecil
    if total_loss <= TARGET_LOSS:
        stopped_reason = "target_loss_reached"
        print(f"Early stop epoch {epoch}, Loss: {total_loss:.4f}")
        break

    # Stop kalau loss tidak membaik banyak
    improvement = best_loss - total_loss

    if improvement > MIN_IMPROVEMENT:
        best_loss = total_loss
        no_improve_count = 0
    else:
        no_improve_count += 1

    if no_improve_count >= PATIENCE:
        stopped_reason = "no_significant_improvement"
        print(f"Early stop epoch {epoch}, Loss: {total_loss:.4f}")
        break

brain = {
    "name": "kumar_tiny_brain_v3",
    "model_type": "tiny_mlp_hidden_layer",
    "task": dataset["task"],
    "created_at": time.time(),
    "hidden_size": HIDDEN_SIZE,
    "vocab": vocab,
    "labels": labels,
    "W1": W1,
    "b1": b1,
    "W2": W2,
    "b2": b2,
    "responses": responses,
    "stats": {
        "samples": len(samples),
        "vocab": len(vocab),
        "labels": len(labels),
        "hidden_size": HIDDEN_SIZE,
        "final_loss": final_loss,
        "max_epochs": EPOCHS,
        "stopped_reason": stopped_reason
    }
}

with open(BRAIN_FILE, "w", encoding="utf-8") as f:
    json.dump(brain, f, indent=2, ensure_ascii=False)

print(f"Selesai. Otak v3 disimpan sebagai {BRAIN_FILE}")
print("Final loss:", round(final_loss, 4))