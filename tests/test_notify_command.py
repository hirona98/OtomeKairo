import json
from pathlib import Path
import subprocess

import pytest


COMMAND = Path(__file__).resolve().parents[1] / "scripts" / "CocoroNotify.sh"


@pytest.fixture
def notify(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        '#!/bin/bash\n'
        'IFS= read -r authorization\n'
        '[[ "$authorization" == "Authorization: Bearer test-notification-token" ]] || exit 97\n'
        'printf "%s\\0" "$@" > "$MOCK_ARGS_FILE"\n'
        'printf "%s" "$MOCK_RESPONSE"\n'
        'exit "${MOCK_EXIT_CODE:-0}"\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)
    config = tmp_path / "notify.conf"
    token_config = "OTOMEKAIRO_TOKEN='test-notification-token'\n"
    base_config = "OTOMEKAIRO_URL='https://otomekairo.test:55601/'\n" + token_config
    args_file = tmp_path / "curl-args"

    def run(message="終了したと伝えてほしい。", *, source="Codex", result="noop", response=None,
            curl_status=0, settings="", missing_config=False, use_default_url=False):
        config.write_text((token_config if use_default_url else base_config) + settings, encoding="utf-8")
        if missing_config:
            config.unlink()
        args_file.unlink(missing_ok=True)
        process = subprocess.run(
            ["/bin/bash", str(COMMAND), "--config", str(config), source, message],
            env={
                "HOME": str(tmp_path),
                # curl 以外の外部コマンドがなくても実行できることを確認する。
                "PATH": str(bin_dir),
                "MOCK_ARGS_FILE": str(args_file),
                "MOCK_RESPONSE": response if response is not None else json.dumps(
                    {"ok": True, "data": {"result_kind": result}}, ensure_ascii=False,
                ),
                "MOCK_EXIT_CODE": str(curl_status),
            },
            text=True, capture_output=True, timeout=5,
        )
        arguments = args_file.read_bytes().decode().split("\0")[:-1] if args_file.exists() else []
        assert "test-notification-token" not in process.stdout + process.stderr
        assert all("test-notification-token" not in argument for argument in arguments)
        return process, arguments

    return run


def test_notify_preserves_unicode_and_every_json_control_character(notify):
    message = '日本語 "引用" \\path\n\r\t' + "".join(chr(value) for value in range(1, 32)) + "\n"
    source = 'Codex "端末A" \\通知\n'
    process, arguments = notify(message, source=source)
    assert process.returncode == 0, process.stderr
    body = json.loads(arguments[arguments.index("--data-binary") + 1])
    assert body == {"message": message, "client_context": {"source": source}}
    assert arguments[arguments.index("--url") + 1] == "https://otomekairo.test:55601/api/wake"
    assert "--insecure" not in arguments
    assert arguments.count("--request") == 1


@pytest.mark.parametrize("result", ["speech", "noop", "capability_request"])
def test_notify_accepts_normal_judgment_outcomes(notify, result):
    process, _ = notify(result=result)
    assert process.returncode == 0


def test_notify_defaults_to_cocoro_box_when_url_is_omitted(notify):
    process, arguments = notify(use_default_url=True)
    assert process.returncode == 0, process.stderr
    assert arguments[arguments.index("--url") + 1] == "https://cocoro-box:55601/api/wake"


@pytest.mark.parametrize("response", [
    '{"ok": true, "data": {"result_kind": "internal_failure"}}',
    '{"ok": false, "error": {"code": "invalid_token"}}',
    '{"ok": true, "data": {"result_kind": "skipped"}}',
    'invalid response',
    json.dumps({"ok": True, "data": {"message": '"result_kind": "noop"'}}),
])
def test_notify_rejects_failure_or_missing_judgment_result(notify, response):
    process, _ = notify(response=response)
    assert process.returncode != 0


def test_notify_does_not_read_result_kind_from_speech_text(notify):
    response = json.dumps({"ok": True, "data": {
        "speech": {"text": '"result_kind": "internal_failure"'}, "result_kind": "speech",
    }})
    process, _ = notify(response=response)
    assert process.returncode == 0


@pytest.mark.parametrize("status", [7, 22, 28, 60])
def test_notify_preserves_curl_failure_without_retry(notify, status):
    process, arguments = notify(curl_status=status, response="", settings="OTOMEKAIRO_TLS_VERIFY=false\n")
    assert process.returncode == status
    assert "--retry" not in arguments
    assert arguments.count("--url") == 1
    assert "--insecure" in arguments


@pytest.mark.parametrize("settings", [
    "OTOMEKAIRO_TLS_VERIFY=invalid\n",
    "OTOMEKAIRO_TLS_VERIFY=false\nOTOMEKAIRO_CA_FILE='/missing-ca'\n",
    "OTOMEKAIRO_TIMEOUT_SECONDS=0\n",
    "OTOMEKAIRO_URL='http://otomekairo.test'\n",
])
def test_notify_rejects_invalid_settings_before_sending(notify, settings):
    process, arguments = notify(settings=settings)
    assert process.returncode != 0
    assert arguments == []


def test_notify_fails_explicitly_when_config_is_missing(notify):
    process, arguments = notify(missing_config=True)
    assert process.returncode != 0
    assert arguments == []


def test_notify_rejects_empty_message_before_sending(notify):
    process, arguments = notify(" \n\t")
    assert process.returncode != 0
    assert arguments == []
