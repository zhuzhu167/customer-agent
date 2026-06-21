from __future__ import annotations

import json

from backend.app.core.config import get_settings
from backend.app.core.runtime_guard import collect_runtime_configuration_violations


def main() -> int:
    settings = get_settings()
    violations = collect_runtime_configuration_violations(settings)
    payload = {
        "app_env": settings.app_env,
        "ok": not violations,
        "violations": violations,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
