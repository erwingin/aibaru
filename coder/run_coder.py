import os
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
sys.path.append(str(CURRENT_DIR))

from coder_local import ask_coder


def main():
    print("Kumar Coder aktif.")
    print("Backend:", os.environ.get("CODER_BACKEND", "dummy"))
    print("Model  :", os.environ.get("CODER_MODEL", "local-coder"))
    print()
    print("Perintah:")
    print("- ketik tugas coding")
    print("- keluar = berhenti")
    print()

    while True:
        user = input("Code task: ").strip()

        if not user:
            continue

        if user.lower() in ["keluar", "exit", "quit"]:
            break

        print()
        print("[Kumar Coder] Membuat kode...")
        result = ask_coder(user)

        print()
        print("=" * 50)
        print(result)
        print("=" * 50)
        print()


if __name__ == "__main__":
    main()