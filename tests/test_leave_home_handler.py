import sys
import os
import unittest
import logging
from unittest.mock import AsyncMock, MagicMock, patch

# 1. Pfad Setup: 'app' Ordner verfügbar machen
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

# 2. Pre-Import Mocking
# Wir mocken kritische Abhängigkeiten in sys.modules, BEVOR wir den Handler importieren.
mock_const_module = MagicMock()
mock_const_module.tools_schema = [{"name": "dummy_tool"}] 
sys.modules["const"] = mock_const_module

mock_genai_pkg = MagicMock()
sys.modules["genai_client"] = mock_genai_pkg
mock_genai_client_module = MagicMock()
sys.modules["genai_client.client"] = mock_genai_client_module

# Jetzt importieren wir den Handler und die Ergebnis-Klasse
try:
    from category_handler.leave_home_handler import LeaveHomeHandler
    from category_handler.base import HandlerResult
except ImportError as e:
    logging.error(f"Kritischer Import Fehler im Test: {e}")
    raise

class TestLeaveHomeHandler(unittest.IsolatedAsyncioTestCase):
    """
    Testet den LeaveHomeHandler isoliert mit unittest (ohne pytest Abhängigkeit).
    """

    async def test_leave_home_handler_execute(self):
        # 1. Setup Mocks
        
        # HA Service Mock
        mock_ha_service = AsyncMock()
        mock_context = {
            "controllable_devices": [
                {"eid": "light.wohnzimmer", "area": "Wohnzimmer", "state": "on", "device_class": "light"},
                {"eid": "light.kueche", "area": "Küche", "state": "off", "device_class": "light"},
            ],
            "sensors": [
                {"eid": "binary_sensor.fenster_bad", "area": "Bad", "state": "on", "device_class": "window"},
                {"eid": "sensor.waschmaschine_power", "area": "Waschküche", "state": "1200.5", "device_class": "power"},
            ]
        }
        mock_ha_service.get_smart_home_context.return_value = mock_context

        # GenAI Client Mock Konfiguration
        mock_client_instance = MagicMock()
        mock_genai_client_module.get_client.return_value = mock_client_instance
        
        mock_response = MagicMock()
        expected_text = "Alles sicher."
        mock_response.text = expected_text
        mock_client_instance.models.generate_content.return_value = mock_response

        # 2. Execution
        handler = LeaveHomeHandler()
        
        # Wir nutzen assertLogs um sicherzustellen, dass wir Logs fangen können
        # Der Logger Name im Handler ist __name__, also 'category_handler.leave_home_handler'
        with self.assertLogs('category_handler.leave_home_handler', level='DEBUG') as cm:
            with patch("category_handler.leave_home_handler.get_client", return_value=mock_client_instance):
                # Execute returns a HandlerResult now
                result = await handler.execute([], mock_ha_service)

        # Logs für Debugging zusammenbauen
        logs = "\n".join(cm.output)

        # 3. Assertions
        
        # Wurde HA Service gefragt?
        mock_ha_service.get_smart_home_context.assert_awaited_once()
        
        # Wurde AI gefragt?
        mock_client_instance.models.generate_content.assert_called_once()
        
        # Prompt Inhalt prüfen
        call_args = mock_client_instance.models.generate_content.call_args
        
        if hasattr(call_args, "kwargs"):
            kwargs = call_args.kwargs
        else:
            _, kwargs = call_args
            
        prompt = kwargs.get("contents", "")
        
        # Debugging Hilfe
        if "light.wohnzimmer" not in prompt:
            self.fail(f"Prompt unvollständig. Inhalt: {prompt}\nLogs: {logs}")

        self.assertIn("light.wohnzimmer", prompt)
        self.assertIn("binary_sensor.fenster_bad", prompt)
        self.assertIn("sensor.waschmaschine_power", prompt)
        
        # Negative Tests
        self.assertNotIn("light.kueche", prompt)
        
        # Ergebnis prüfen (HandlerResult)
        self.assertIsInstance(result, HandlerResult)
        self.assertEqual(result.text, expected_text)
        # Da im Mock Lichter an sind (light.wohnzimmer), erwartet die neue Logik
        # eigentlich eine Rückfrage, ABER: Der Prompt-Response vom Mock ist nur "Alles sicher."
        # Die Logik im Handler prüft "aktive_lichter".
        # light.wohnzimmer ist 'on', also > 0.
        # Der Handler fragt die AI. Die AI antwortet "Alles sicher.".
        # Dann prüft der Handler: if len(aktive_lichter) > 0: ...
        # -> Er sollte should_end_session=False setzen.
        
        self.assertFalse(result.should_end_session)
        self.assertEqual(result.session_attributes["state"], "AWAITING_LIGHTS_CONFIRMATION")
        self.assertIn("light.wohnzimmer", result.session_attributes["lights_to_turn_off"])
