from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.core.config import Settings, get_settings
from backend.app.core.runtime_guard import collect_runtime_configuration_violations
from backend.app.services.provider_config import default_provider_configs
from backend.app.worker.provider_smoke import run_provider_smoke


REQUIRED_PROVIDER_SMOKE_FRAGMENTS = [
    "alibaba_cloud",
    "qwen-plus",
    "tencent_cloud",
    "audio/wav",
    "这是一轮云服务语音接入冒烟测试。",
    "realtime_asr",
]
REQUIRED_BROWSER_SMOKE_FRAGMENTS = [
    "transcript.final",
    "agent.reply.preparing",
    "agent.reply.answered",
    "这是一轮云服务语音接入冒烟测试",
    "console.error_count",
    "0",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def build_release_gate_report(
    *,
    settings: Settings,
    root: Path | None = None,
    provider_smoke_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or repo_root()
    gates = [
        _runtime_configuration_gate(settings),
        _mock_provider_exposure_gate(settings),
        _production_compose_gate(root),
        _provider_smoke_evidence_gate(root, provider_smoke_result=provider_smoke_result),
        _browser_smoke_evidence_gate(root),
        _manual_microphone_acceptance_gate(root),
    ]
    return {
        "ok": all(gate["ok"] for gate in gates),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app_env": settings.app_env,
        "gates": gates,
    }


def _runtime_configuration_gate(settings: Settings) -> dict[str, Any]:
    violations = collect_runtime_configuration_violations(settings)
    return {
        "name": "runtime_configuration",
        "ok": not violations,
        "detail": {"violations": violations},
    }


def _mock_provider_exposure_gate(settings: Settings) -> dict[str, Any]:
    configs = default_provider_configs(settings)
    exposed_mock = [
        {
            "provider_type": config.provider_type,
            "provider_name": config.provider_name,
            "enabled": config.enabled,
        }
        for config in configs
        if config.provider_name == "mock"
    ]
    ok = settings.app_env == "test" or not exposed_mock
    return {
        "name": "mock_provider_exposure",
        "ok": ok,
        "detail": {
            "app_env": settings.app_env,
            "exposed_mock": exposed_mock,
        },
    }


def _production_compose_gate(root: Path) -> dict[str, Any]:
    compose_path = root / "docker-compose.production.yml"
    env_path = root / ".env.production.example"
    compose_text = compose_path.read_text(encoding="utf-8") if compose_path.exists() else ""
    env_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    problems: list[str] = []
    if not compose_path.exists():
        problems.append("missing docker-compose.production.yml")
    if not env_path.exists():
        problems.append("missing .env.production.example")
    if "docs/密钥" in compose_text:
        problems.append("production compose must not mount docs/密钥")
    if "runtime_config_check" not in compose_text:
        problems.append("production compose must run runtime_config_check before services")
    if 'TENCENT_CLOUD_SECRET_FILE=""' not in env_text:
        problems.append(".env.production.example must clear TENCENT_CLOUD_SECRET_FILE")
    if 'ALIBABA_CLOUD_SECRET_FILE=""' not in env_text:
        problems.append(".env.production.example must clear ALIBABA_CLOUD_SECRET_FILE")
    return {
        "name": "production_compose_template",
        "ok": not problems,
        "detail": {
            "compose_path": str(compose_path),
            "env_template_path": str(env_path),
            "problems": problems,
        },
    }


def _provider_smoke_evidence_gate(
    root: Path,
    *,
    provider_smoke_result: dict[str, Any] | None,
) -> dict[str, Any]:
    if provider_smoke_result is not None:
        serialized = json.dumps(provider_smoke_result, ensure_ascii=False)
        problems = [
            fragment for fragment in REQUIRED_PROVIDER_SMOKE_FRAGMENTS if fragment not in serialized
        ]
        return {
            "name": "real_provider_smoke",
            "ok": not problems,
            "detail": {
                "source": "live",
                "missing_fragments": problems,
                "summary": _provider_smoke_summary(provider_smoke_result),
            },
        }

    evidence_path = root / "docs/engineering/changes/TECH-20260621-225800-real-provider-current-smoke.md"
    text = evidence_path.read_text(encoding="utf-8") if evidence_path.exists() else ""
    missing = [fragment for fragment in REQUIRED_PROVIDER_SMOKE_FRAGMENTS if fragment not in text]
    return {
        "name": "real_provider_smoke",
        "ok": evidence_path.exists() and not missing,
        "detail": {
            "source": "evidence_file",
            "path": str(evidence_path),
            "missing_fragments": missing,
        },
    }


def _browser_smoke_evidence_gate(root: Path) -> dict[str, Any]:
    evidence_path = root / "docs/engineering/changes/TECH-20260621-230420-browser-real-chain-current-smoke.md"
    text = evidence_path.read_text(encoding="utf-8") if evidence_path.exists() else ""
    missing = [fragment for fragment in REQUIRED_BROWSER_SMOKE_FRAGMENTS if fragment not in text]
    return {
        "name": "browser_real_chain_smoke",
        "ok": evidence_path.exists() and not missing,
        "detail": {
            "path": str(evidence_path),
            "missing_fragments": missing,
        },
    }


def _manual_microphone_acceptance_gate(root: Path) -> dict[str, Any]:
    output_dir = root / "output/manual-mic-acceptance"
    records = sorted(output_dir.glob("manual-mic-acceptance-*.json")) if output_dir.exists() else []
    passing_records: list[str] = []
    invalid_records: list[dict[str, str]] = []
    for record in records:
        try:
            payload = json.loads(record.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            invalid_records.append({"path": str(record), "error": str(exc)})
            continue
        if payload.get("ok") is True and payload.get("kind") == "manual_microphone_acceptance":
            passing_records.append(str(record))

    return {
        "name": "manual_microphone_acceptance",
        "ok": bool(passing_records),
        "detail": {
            "output_dir": str(output_dir),
            "records_found": len(records),
            "passing_records": passing_records[-3:],
            "invalid_records": invalid_records[-3:],
        },
    }


def _provider_smoke_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "llm": result.get("llm", {}),
        "tts": result.get("tts", {}),
        "asr": result.get("asr", {}),
        "realtime_asr": result.get("realtime_asr", {}),
    }


async def _main_async() -> int:
    parser = argparse.ArgumentParser(description="Check Web Voice MVP production release gates.")
    parser.add_argument(
        "--run-provider-smoke",
        action="store_true",
        help="Run live Tencent/Alibaba provider smoke instead of using the recorded evidence file.",
    )
    parser.add_argument(
        "--include-realtime-asr",
        action="store_true",
        help="When --run-provider-smoke is set, include Tencent realtime ASR WebSocket smoke.",
    )
    parser.add_argument(
        "--output",
        help="Optional JSON output path for the release gate report.",
    )
    args = parser.parse_args()

    provider_smoke_result = None
    if args.run_provider_smoke:
        provider_smoke_result = await run_provider_smoke(include_realtime_asr=args.include_realtime_asr)

    report = build_release_gate_report(
        settings=get_settings(),
        provider_smoke_result=provider_smoke_result,
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(f"{serialized}\n", encoding="utf-8")
    print(serialized)
    return 0 if report["ok"] else 1


def main() -> int:
    return asyncio.run(_main_async())


if __name__ == "__main__":
    raise SystemExit(main())
