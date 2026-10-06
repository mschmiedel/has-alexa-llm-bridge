import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

from category_handler.advice_handler import AdviceHandler
from evcc_service.main import EvccUnavailableError, build_plan
from evcc_service.models import EvoptResponse

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "evopt_sample.json")


def load_plan():
    with open(FIXTURE) as f:
        return build_plan(EvoptResponse.model_validate(json.load(f)))


class TestAdviceHandler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.evcc = AsyncMock()
        self.evcc.get_plan.return_value = load_plan()
        self.client = MagicMock()
        self.client.models.generate_content.return_value.text = "LLM Antwort"
        patcher = patch("category_handler.advice_handler.get_client", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.handler = AdviceHandler(self.evcc)

    def prompt(self) -> str:
        return self.client.models.generate_content.call_args.kwargs["contents"]

    async def test_appliance_passes_computed_advice_to_llm(self):
        result = await self.handler.execute(["Spülmaschine"])
        self.assertEqual(result.text, "LLM Antwort")
        self.assertIn("SPÄTER", self.prompt())
        self.assertIn("morgen 09:15 Uhr", self.prompt())

    async def test_vehicle(self):
        await self.handler.execute(["Auto"])
        self.assertIn("morgen 11:15 Uhr bis morgen 13:00 Uhr", self.prompt())

    async def test_no_ha_calls(self):
        ha = AsyncMock()
        await self.handler.execute(["Trockner"], ha)
        ha.get_smart_home_context.assert_not_called()

    async def test_llm_failure_falls_back_to_template(self):
        self.client.models.generate_content.side_effect = RuntimeError("boom")
        result = await self.handler.execute(["Waschmaschine"])
        self.assertIn("Besser ab morgen 09:00 Uhr", result.text)

    async def test_evcc_unavailable(self):
        self.evcc.get_plan.side_effect = EvccUnavailableError("down")
        result = await self.handler.execute(["Waschmaschine"])
        self.assertIn("nicht an die Energiedaten", result.text)
        self.client.models.generate_content.assert_not_called()

    async def test_missing_device(self):
        result = await self.handler.execute([])
        self.assertIn("welches Gerät", result.text)
        self.evcc.get_plan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
