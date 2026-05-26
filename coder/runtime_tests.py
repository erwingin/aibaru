import ast
import sys
import subprocess
import tempfile
from pathlib import Path


def is_curl_extract_url_task(task):
    task_lower = (task or "").lower()

    wants_url = (
        "tampilkan url" in task_lower
        or "ambil url" in task_lower
        or "ekstrak url" in task_lower
        or "print url" in task_lower
        or "menampilkan url" in task_lower
    )

    wants_converter = (
        "output.py" in task_lower
        or "ubah menjadi script python requests" in task_lower
        or "generate kode python requests" in task_lower
        or "headers" in task_lower
        or "cookie" in task_lower
        or "body" in task_lower
        or "--data-raw" in task_lower
    )

    return "curl" in task_lower and wants_url and not wants_converter


def candidate_executes_requests(source_code):
    try:
        tree = ast.parse(source_code or "")
    except Exception:
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func

        if isinstance(func, ast.Attribute):
            if func.attr in ["get", "post", "put", "delete", "patch", "request"]:
                value = func.value

                if isinstance(value, ast.Name) and value.id == "requests":
                    return True

    return False


def runtime_test_curl_extract_url(task, code):
    """
    Runtime test level 1B:
    - curl.txt berisi satu perintah curl asli multiline
    - kandidat harus menampilkan URL ke stdout
    - boleh positional: python script.py curl.txt
    - boleh --input: python script.py --input curl.txt
    - boleh default curl.txt: python script.py
    - tidak boleh menjalankan request asli
    """
    if not is_curl_extract_url_task(task):
        return None

    code_text = code or ""

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        expected_url = "https://example.com/api/profile/me?x=1"

        curl_lines = [
            f"curl '{expected_url}' \\",
            "  -H 'accept: */*' \\",
            "  -H 'accept-language: en-US,en;q=0.9,id;q=0.8' \\",
            "  -H 'authorization: Bearer <SECRET>' \\",
            "  -H 'content-type: application/json' \\",
            "  -H 'origin: https://app.example.com' \\",
            "  -H 'referer: https://app.example.com/' \\",
            "  -H 'user-agent: Mozilla/5.0' \\",
            "  --data-raw '{\"taps\":4,\"time\":1779782846304}'",
        ]

        curl_file = tmp_path / "curl.txt"
        curl_file.write_text("\n".join(curl_lines) + "\n", encoding="utf-8")

        script_path = tmp_path / "candidate.py"
        script_path.write_text(code_text, encoding="utf-8")

        commands = [
            [sys.executable, str(script_path), "curl.txt"],
            [sys.executable, str(script_path), "--input", "curl.txt"],
            [sys.executable, str(script_path)],
        ]

        errors = []

        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    cwd=tmp_path,
                    capture_output=True,
                    text=True,
                    timeout=20
                )
            except Exception as e:
                errors.append(f"cmd={cmd} error={e}")
                continue

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            code_lower = code_text.lower()

            uses_argparse = "argparse" in code_lower
            uses_shlex = "shlex.split" in code_text or "from shlex import split" in code_text
            prints_expected_url = expected_url in stdout
            no_requests_import = "import requests" not in code_lower and "from requests" not in code_lower
            candidate_safe = not candidate_executes_requests(code_text)

            if (
                result.returncode == 0
                and uses_argparse
                and uses_shlex
                and prints_expected_url
                and no_requests_import
                and candidate_safe
            ):
                return {
                    "enabled": True,
                    "passed": True,
                    "notes": "Runtime test CURL URL lulus: URL berhasil diekstrak ke stdout tanpa menjalankan request asli.",
                    "stdout": stdout
                }

            errors.append(
                "cmd gagal. "
                f"cmd={' '.join(cmd)}, "
                f"returncode={result.returncode}, "
                f"uses_argparse={uses_argparse}, "
                f"uses_shlex={uses_shlex}, "
                f"prints_expected_url={prints_expected_url}, "
                f"no_requests_import={no_requests_import}, "
                f"candidate_safe={candidate_safe}, "
                f"stdout={stdout}, "
                f"stderr={stderr}"
            )

        return {
            "enabled": True,
            "passed": False,
            "notes": " | ".join(errors[-3:])
        }
def is_curl_analyze_structure_task(task):
    task_lower = (task or "").lower()

    wants_structure = (
        "curl" in task_lower
        and "method" in task_lower
        and "url" in task_lower
        and (
            "total headers" in task_lower
            or "total header" in task_lower
            or "authorization" in task_lower
            or "cookie" in task_lower
            or "body" in task_lower
        )
    )

    wants_converter = (
        "output.py" in task_lower
        or "generate kode python requests" in task_lower
        or "ubah menjadi script python requests" in task_lower
    )

    return wants_structure and not wants_converter


def runtime_test_curl_analyze_structure(task, code):
    """
    Runtime test Level 2:
    - parse curl multiline
    - tampilkan method, url, total headers, authorization, cookie, body
    - tidak menjalankan request asli
    """
    if not is_curl_analyze_structure_task(task):
        return None

    code_text = code or ""

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        expected_url = "https://example.test/api/v1/action/123"

        curl_lines = [
            f"curl '{expected_url}' \\",
            "  -X POST \\",
            "  -H 'accept: application/json' \\",
            "  -H 'authorization: Bearer <SECRET>' \\",
            "  -H 'content-type: application/json' \\",
            "  -H 'x-client: kumar-test' \\",
            "  -b 'session=<SECRET>; mode=test' \\",
            "  --data-raw '{\"hello\":\"world\"}'",
        ]

        curl_file = tmp_path / "curl.txt"
        curl_file.write_text("\n".join(curl_lines) + "\n", encoding="utf-8")

        script_path = tmp_path / "candidate.py"
        script_path.write_text(code_text, encoding="utf-8")

        commands = [
            [sys.executable, str(script_path), "curl.txt"],
            [sys.executable, str(script_path), "--input", "curl.txt"],
            [sys.executable, str(script_path), "--input-file", "curl.txt"],
            [sys.executable, str(script_path)],
        ]

        errors = []

        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    cwd=tmp_path,
                    capture_output=True,
                    text=True,
                    timeout=20
                )
            except Exception as e:
                errors.append(f"cmd={cmd} error={e}")
                continue

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            stdout_l = stdout.lower()
            code_lower = code_text.lower()

            uses_argparse = "argparse" in code_lower
            uses_shlex = "shlex.split" in code_text or "from shlex import split" in code_text
            no_requests_import = "import requests" not in code_lower and "from requests" not in code_lower
            candidate_safe = not candidate_executes_requests(code_text)

            has_method = "post" in stdout_l and "method" in stdout_l
            has_url = expected_url in stdout
            has_total_headers = "header" in stdout_l and "4" in stdout_l
            has_authorization = "authorization" in stdout_l
            has_cookie = "cookie" in stdout_l
            has_body = "body" in stdout_l

            if (
                result.returncode == 0
                and uses_argparse
                and uses_shlex
                and no_requests_import
                and candidate_safe
                and has_method
                and has_url
                and has_total_headers
                and has_authorization
                and has_cookie
                and has_body
            ):
                return {
                    "enabled": True,
                    "passed": True,
                    "notes": "Runtime test CURL STRUCTURE lulus: method, URL, headers, authorization, cookie, dan body terdeteksi.",
                    "stdout": stdout
                }

            errors.append(
                "cmd gagal. "
                f"cmd={' '.join(cmd)}, "
                f"returncode={result.returncode}, "
                f"uses_argparse={uses_argparse}, "
                f"uses_shlex={uses_shlex}, "
                f"no_requests_import={no_requests_import}, "
                f"candidate_safe={candidate_safe}, "
                f"has_method={has_method}, "
                f"has_url={has_url}, "
                f"has_total_headers={has_total_headers}, "
                f"has_authorization={has_authorization}, "
                f"has_cookie={has_cookie}, "
                f"has_body={has_body}, "
                f"stdout={stdout}, "
                f"stderr={stderr}"
            )

        return {
            "enabled": True,
            "passed": False,
            "notes": " | ".join(errors[-3:])
        }