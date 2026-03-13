from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app_logging import (
    LOG_CATEGORY_BACKEND_LOGIC,
    LOG_CATEGORY_FRONTEND_BACKEND,
    get_log_config,
    is_log_enabled,
    log_info,
    reset_log_config,
    set_log_config,
)
from app_services import build_app_services
from main import create_app


class AppLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_log_config()

    def tearDown(self) -> None:
        reset_log_config()

    def test_log_endpoint_returns_default_category_switches_and_updates_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
            with TestClient(app) as client:
                self.assertEqual(
                    client.get("/log").json(),
                    {
                        "ok": True,
                        "backend_logic": True,
                        "frontend_backend": False,
                    },
                )
                self.assertTrue(is_log_enabled(LOG_CATEGORY_BACKEND_LOGIC))
                self.assertFalse(is_log_enabled(LOG_CATEGORY_FRONTEND_BACKEND))

                enabled_response = client.post("/log", json={"frontend_backend": True})
                self.assertEqual(enabled_response.status_code, 200)
                self.assertEqual(
                    enabled_response.json(),
                    {
                        "ok": True,
                        "backend_logic": True,
                        "frontend_backend": True,
                    },
                )
                self.assertEqual(
                    get_log_config(),
                    {
                        "backend_logic": True,
                        "frontend_backend": True,
                    },
                )

                disabled_response = client.post("/log", json={"backend_logic": False})
                self.assertEqual(disabled_response.status_code, 200)
                self.assertEqual(
                    disabled_response.json(),
                    {
                        "ok": True,
                        "backend_logic": False,
                        "frontend_backend": True,
                    },
                )

    def test_frontend_backend_logs_are_filtered_by_default(self) -> None:
        with self.assertNoLogs("tauri_agent_next.backend", level="INFO"):
            log_info(
                "test.log.frontend_backend.suppressed",
                category=LOG_CATEGORY_FRONTEND_BACKEND,
                session_id="session-1",
            )

        with self.assertLogs("tauri_agent_next.backend", level="INFO") as captured:
            log_info(
                "test.log.backend_logic.enabled",
                category=LOG_CATEGORY_BACKEND_LOGIC,
                session_id="session-1",
                run_id="run-1",
            )

        self.assertTrue(
            any(
                "[backend_logic] test.log.backend_logic.enabled | session_id=session-1 run_id=run-1"
                in line
                for line in captured.output
            )
        )

        set_log_config(frontend_backend=True)
        with self.assertLogs("tauri_agent_next.backend", level="INFO") as captured:
            log_info(
                "test.log.frontend_backend.enabled",
                category=LOG_CATEGORY_FRONTEND_BACKEND,
                session_id="session-1",
            )

        self.assertTrue(
            any(
                "[frontend_backend] test.log.frontend_backend.enabled | session_id=session-1"
                in line
                for line in captured.output
            )
        )
