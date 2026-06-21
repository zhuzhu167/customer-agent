from __future__ import annotations

import json

from backend.app.core.config import Settings
from backend.app.worker.production_release_gate import build_release_gate_report


def test_release_gate_reports_missing_manual_acceptance(tmp_path) -> None:
    _write_required_files(tmp_path)

    report = build_release_gate_report(
        settings=Settings(app_env="local"),
        root=tmp_path,
    )

    assert report["ok"] is False
    manual_gate = _gate_by_name(report, "manual_microphone_acceptance")
    assert manual_gate["ok"] is False
    assert manual_gate["detail"]["records_found"] == 0


def test_release_gate_passes_with_required_evidence(tmp_path) -> None:
    _write_required_files(tmp_path)
    output_dir = tmp_path / "output/manual-mic-acceptance"
    output_dir.mkdir(parents=True)
    (output_dir / "manual-mic-acceptance-2026-06-21T00-00-00.json").write_text(
        json.dumps({"ok": True, "kind": "manual_microphone_acceptance"}),
        encoding="utf-8",
    )

    report = build_release_gate_report(
        settings=Settings(app_env="local"),
        root=tmp_path,
    )

    assert report["ok"] is True
    assert _gate_by_name(report, "mock_provider_exposure")["ok"] is True
    assert _gate_by_name(report, "real_provider_smoke")["ok"] is True
    assert _gate_by_name(report, "browser_real_chain_smoke")["ok"] is True
    assert _gate_by_name(report, "manual_microphone_acceptance")["ok"] is True


def test_release_gate_uses_live_provider_smoke_result(tmp_path) -> None:
    _write_required_files(tmp_path, write_provider_evidence=False)
    output_dir = tmp_path / "output/manual-mic-acceptance"
    output_dir.mkdir(parents=True)
    (output_dir / "manual-mic-acceptance-2026-06-21T00-00-00.json").write_text(
        json.dumps({"ok": True, "kind": "manual_microphone_acceptance"}),
        encoding="utf-8",
    )

    report = build_release_gate_report(
        settings=Settings(app_env="local"),
        root=tmp_path,
        provider_smoke_result={
            "llm": {"provider": "alibaba_cloud", "model": "qwen-plus"},
            "tts": {"provider": "tencent_cloud", "content_type": "audio/wav"},
            "asr": {"provider": "tencent_cloud", "transcript": "这是一轮云服务语音接入冒烟测试。"},
            "realtime_asr": {
                "provider": "tencent_cloud",
                "final_text": "这是一轮云服务语音接入冒烟测试。",
            },
        },
    )

    assert report["ok"] is True
    assert _gate_by_name(report, "real_provider_smoke")["detail"]["source"] == "live"


def _write_required_files(tmp_path, *, write_provider_evidence: bool = True) -> None:
    changes_dir = tmp_path / "docs/engineering/changes"
    changes_dir.mkdir(parents=True)
    if write_provider_evidence:
        (changes_dir / "TECH-20260621-225800-real-provider-current-smoke.md").write_text(
            "\n".join(
                [
                    "alibaba_cloud",
                    "qwen-plus",
                    "tencent_cloud",
                    "audio/wav",
                    "这是一轮云服务语音接入冒烟测试。",
                    "realtime_asr",
                ]
            ),
            encoding="utf-8",
        )
    (changes_dir / "TECH-20260621-230420-browser-real-chain-current-smoke.md").write_text(
        "\n".join(
            [
                "transcript.final",
                "agent.reply.preparing",
                "agent.reply.answered",
                "这是一轮云服务语音接入冒烟测试",
                "console.error_count",
                "0",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.production.yml").write_text(
        "command: python -m backend.app.worker.runtime_config_check\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.production.example").write_text(
        'TENCENT_CLOUD_SECRET_FILE=""\nALIBABA_CLOUD_SECRET_FILE=""\n',
        encoding="utf-8",
    )


def _gate_by_name(report, name: str):
    return next(gate for gate in report["gates"] if gate["name"] == name)
