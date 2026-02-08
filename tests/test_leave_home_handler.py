import sys
import os
import unittest
import logging
from unittest.mock import AsyncMock, MagicMock, patch

<<<<<<< HEAD
# 1. Pfad Setup
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

# 2. Pre-Import Mocking
mock_const_module = MagicMock()
mock_const_module.tools_schema = [{"name": "dummy_tool"}]
class MockCategory:
    LEAVE_HOME = MagicMock()
    LEAVE_HOME.value = "LEAVE_HOME"
mock_const_module.Category = MockCategory

=======
# 1. Pfad Setup: 'app' Ordner verfügbar machen
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

# 2. Pre-Import Mocking
# Wir mocken kritische Abhängigkeiten in sys.modules, BEVOR wir den Handler importieren.
mock_const_module = MagicMock()
mock_const_module.tools_schema = [{"name": "dummy_tool"}] 
>>>>>>> origin/main
sys.modules["const"] = mock_const_module

mock_genai_pkg = MagicMock()
sys.modules["genai_client"] = mock_genai_pkg
mock_genai_client_module = MagicMock()
sys.modules["genai_client.client"] = mock_genai_client_module

<<<<<<< HEAD
# Import Handler
=======
# Jetzt importieren wir den Handler und die Ergebnis-Klasse
>>>>>>> origin/main
try:
    from category_handler.leave_home_handler import LeaveHomeHandler
    from category_handler.base import HandlerResult
except ImportError as e:
    logging.error(f"Kritischer Import Fehler im Test: {e}")
    raise

class TestLeaveHomeHandler(unittest.IsolatedAsyncioTestCase):
<<<<<<< HEAD
    
    def setUp(self):
        self.mock_ha_service = AsyncMock()
        self.mock_client_instance = MagicMock()
        self.mock_client_instance.models.generate_content.return_value.text = "Mock AI Antwort: Alles okay."
        
        self.client_patcher = patch("category_handler.leave_home_handler.get_client", return_value=self.mock_client_instance)
        self.client_patcher.start()

    def tearDown(self):
        self.client_patcher.stop()

    async def test_initial_request_lights_on(self):
        """Fachlicher Fall: Lichter an -> Rückfrage."""
        self.mock_ha_service.get_smart_home_context.return_value = {
            "controllable_devices": [
                {"eid": "light.wohnzimmer", "area": "Wohnzimmer", "state": "on", "device_class": "light"}
            ],
            "sensors": []
        }

        handler = LeaveHomeHandler()
        # Optional: Info Logs unterdrücken oder prüfen, hier lassen wir sie zu Debug-Zwecken
        result = await handler.execute([], self.mock_ha_service)

        call_args = self.mock_client_instance.models.generate_content.call_args
        _, kwargs = call_args
        prompt = kwargs.get("contents", "")
        
        self.assertIn("light.wohnzimmer", prompt)
        self.assertIn("Soll ich die Lichter ausschalten?", prompt)
        self.assertFalse(result.should_end_session)
        self.assertEqual(result.session_attributes["state"], "AWAITING_LIGHTS_CONFIRMATION")

    async def test_initial_request_all_safe(self):
        """Fachlicher Fall: Alles sicher -> Ende."""
        self.mock_ha_service.get_smart_home_context.return_value = {
            "controllable_devices": [],
            "sensors": []
        }

        handler = LeaveHomeHandler()
        result = await handler.execute([], self.mock_ha_service)

        self.assertTrue(result.should_end_session)

    async def test_open_windows(self):
        """Fachlicher Fall: Offenes Fenster."""
        self.mock_ha_service.get_smart_home_context.return_value = {
            "controllable_devices": [],
            "sensors": [
                 {"eid": "binary_sensor.fenster_gast", "area": "Gast", "state": "open", "device_class": "window"}
            ]
        }

        handler = LeaveHomeHandler()
        await handler.execute([], self.mock_ha_service)
        
        call_args = self.mock_client_instance.models.generate_content.call_args
        _, kwargs = call_args
        prompt = kwargs.get("contents", "")
        
        self.assertIn("binary_sensor.fenster_gast", prompt)

    async def test_high_power_consumption(self):
        """Fachlicher Fall: Hoher Verbrauch."""
        self.mock_ha_service.get_smart_home_context.return_value = {
            "controllable_devices": [],
            "sensors": [
                 {"eid": "sensor.waschmaschine", "area": "Keller", "state": "1200", "device_class": "power"}
            ]
        }

        handler = LeaveHomeHandler()
        await handler.execute([], self.mock_ha_service)
        
        call_args = self.mock_client_instance.models.generate_content.call_args
        _, kwargs = call_args
        prompt = kwargs.get("contents", "")
        self.assertIn("1200", prompt)

    async def test_followup_yes_turn_off_lights(self):
        """Fachlicher Fall: Ja -> Ausschalten."""
        session_attributes = {
            "state": "AWAITING_LIGHTS_CONFIRMATION",
            "lights_to_turn_off": ["light.wohnzimmer"]
        }
        self.mock_ha_service.execute_ha_service.return_value = True
        
        handler = LeaveHomeHandler()
        result = await handler.execute([], self.mock_ha_service, session_attributes, intent_name="AMAZON.YesIntent")
        
        self.mock_ha_service.execute_ha_service.assert_called_with("light", "turn_off", "light.wohnzimmer")
        self.assertTrue(result.should_end_session)

    async def test_followup_no_keep_lights(self):
        """Fachlicher Fall: Nein -> Nichts tun."""
        session_attributes = {
            "state": "AWAITING_LIGHTS_CONFIRMATION",
            "lights_to_turn_off": ["light.wohnzimmer"]
        }
        
        handler = LeaveHomeHandler()
        result = await handler.execute([], self.mock_ha_service, session_attributes, intent_name="AMAZON.NoIntent")
        
        self.mock_ha_service.execute_ha_service.assert_not_called()
        self.assertTrue(result.should_end_session)

    async def test_ha_service_error(self):
        """
        Technischer Fall: Home Assistant antwortet nicht.
        Wir nutzen assertLogs, um sicherzustellen, dass der Fehler geloggt wird,
        und um die Konsolenausgabe sauber zu halten.
        """
        self.mock_ha_service.get_smart_home_context.side_effect = Exception("Verbindung verloren")
        
        handler = LeaveHomeHandler()
        
        # Fängt Logs auf Level ERROR (oder höher) im Logger 'category_handler.leave_home_handler'
        with self.assertLogs('category_handler.leave_home_handler', level='ERROR') as cm:
            result = await handler.execute([], self.mock_ha_service)
        
        # Sicherstellen, dass die Fehlermeldung auch wirklich geloggt wurde
        self.assertTrue(any("Verbindung verloren" in o for o in cm.output))
        
        self.assertTrue(result.should_end_session)
        self.assertIn("Fehler", result.text)

if __name__ == "__main__":
    unittest.main()
=======
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
>>>>>>> origin/main
