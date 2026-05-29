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
    Runtime test level CURL URL:
    - mendukung URL biasa, --url URL, --url=URL
    - jika tidak ada URL, wajib print "URL tidak ditemukan"
    - tidak boleh menjalankan request asli
    """
    if not is_curl_extract_url_task(task):
        return None

    code_text = code or ""
    code_lower = code_text.lower()

    uses_argparse = "argparse" in code_lower
    uses_shlex = "shlex.split" in code_text or "from shlex import split" in code_text
    no_requests_import = "import requests" not in code_lower and "from requests" not in code_lower
    candidate_safe = not candidate_executes_requests(code_text)

    if not uses_argparse or not uses_shlex or not no_requests_import or not candidate_safe:
        return {
            "enabled": True,
            "passed": False,
            "notes": (
                f"Static check gagal. "
                f"uses_argparse={uses_argparse}, "
                f"uses_shlex={uses_shlex}, "
                f"no_requests_import={no_requests_import}, "
                f"candidate_safe={candidate_safe}"
            )
        }

    test_cases = [
        {
            "name": "url_biasa",
            "expected": "https://example.com/api/profile/me?x=1",
            "content": "\n".join([
                "curl 'https://example.com/api/profile/me?x=1' \\",
                "  -H 'accept: */*' \\",
                "  -H 'authorization: Bearer <SECRET>' \\",
                "  --data-raw '{\"taps\":4}'",
            ]) + "\n",
        },
        {
            "name": "flag_url_spasi",
            "expected": "https://example.com/api/from-url-space?x=2",
            "content": "\n".join([
                "curl --url 'https://example.com/api/from-url-space?x=2' \\",
                "  -H 'accept: */*'",
            ]) + "\n",
        },
        {
            "name": "flag_url_sama_dengan",
            "expected": "https://example.com/api/from-url-equal?x=3",
            "content": "\n".join([
                "curl --url='https://example.com/api/from-url-equal?x=3' \\",
                "  -H 'accept: */*'",
            ]) + "\n",
        },
        {
            "name": "tanpa_url",
            "expected": "url tidak ditemukan",
            "content": "\n".join([
                "curl \\",
                "  -H 'accept: */*' \\",
                "  --data-raw '{\"hello\":\"world\"}'",
            ]) + "\n",
        },
    ]

    errors = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        script_path = tmp_path / "candidate.py"
        script_path.write_text(code_text, encoding="utf-8")

        curl_file = tmp_path / "curl.txt"

        commands = [
            [sys.executable, str(script_path), "curl.txt"],
            [sys.executable, str(script_path), "--input", "curl.txt"],
            [sys.executable, str(script_path)],
        ]

        for case in test_cases:
            curl_file.write_text(case["content"], encoding="utf-8")

            case_passed = False
            case_errors = []

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
                    case_errors.append(f"cmd={cmd} error={e}")
                    continue

                stdout = (result.stdout or "").strip()
                stderr = (result.stderr or "").strip()

                if case["name"] == "tanpa_url":
                    prints_expected = case["expected"] in stdout.lower()
                else:
                    prints_expected = case["expected"] in stdout

                if result.returncode == 0 and prints_expected:
                    case_passed = True
                    break

                case_errors.append(
                    "cmd gagal. "
                    f"case={case['name']}, "
                    f"cmd={' '.join(cmd)}, "
                    f"returncode={result.returncode}, "
                    f"prints_expected={prints_expected}, "
                    f"stdout={stdout}, "
                    f"stderr={stderr}"
                )

            if not case_passed:
                errors.extend(case_errors[-3:])
                return {
                    "enabled": True,
                    "passed": False,
                    "notes": " | ".join(errors[-3:])
                }

    return {
        "enabled": True,
        "passed": True,
        "notes": "Runtime test CURL URL lulus: URL biasa, --url URL, --url=URL, dan kondisi tanpa URL berhasil.",
        "stdout": "semua case lulus"
    }

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