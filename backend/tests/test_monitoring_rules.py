from __future__ import annotations

from pathlib import Path

import yaml


ALERTS_PATH = Path("docs/engineering/ops/prometheus-alerts.yml")


def test_prometheus_alert_rules_cover_production_runtime_risks() -> None:
    document = yaml.safe_load(ALERTS_PATH.read_text(encoding="utf-8"))
    groups = document["groups"]
    assert groups

    rules = [rule for group in groups for rule in group["rules"]]
    alerts = {rule["alert"]: rule for rule in rules}

    expected_alerts = {
        "CustomerAgentRuntimeConfigurationInvalid",
        "CustomerAgentMockProviderActive",
        "CustomerAgentVoiceWorkerLeaseExpired",
        "CustomerAgentVoiceWorkerHeartbeatStale",
        "CustomerAgentVoiceWorkerJobFailed",
        "CustomerAgentRecentVoiceErrors",
        "CustomerAgentNoActiveSessions",
    }
    assert expected_alerts.issubset(alerts)

    assert "customer_agent_runtime_configuration_violations_total" in alerts[
        "CustomerAgentRuntimeConfigurationInvalid"
    ]["expr"]
    assert "customer_agent_provider_mock_active" in alerts["CustomerAgentMockProviderActive"]["expr"]
    assert "customer_agent_voice_worker_expired_running_jobs" in alerts[
        "CustomerAgentVoiceWorkerLeaseExpired"
    ]["expr"]
    assert "customer_agent_voice_events_errors_recent_15m" in alerts["CustomerAgentRecentVoiceErrors"]["expr"]

    for alert in expected_alerts:
        rule = alerts[alert]
        assert rule["for"]
        assert rule["labels"]["severity"] in {"critical", "warning", "info"}
        assert rule["annotations"]["summary"]
        assert rule["annotations"]["description"]
