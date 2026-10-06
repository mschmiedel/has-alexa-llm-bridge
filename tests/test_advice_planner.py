import os
import sys
import unittest
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

from category_handler.advice_planner import (
    Verdict,
    advise_appliance,
    advise_vehicle,
)
from category_handler.device_profiles import (
    DeviceProfile,
    is_vehicle,
    profile_for,
)
from evcc_service.models import Plan, PlanDevice, PlanSlot

START = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
GRID = 0.30
FEEDIN = 0.07
PROFILE = DeviceProfile(name="Test", power_w=1000, duration_min=60)


def make_plan(surplus_wh: list[float], vehicle_wh: list[float] | None = None,
              grid_price: list[float] | None = None) -> Plan:
    """15-Minuten-Slots; surplus_wh ist solar - load je Slot."""
    slots = []
    for i, surplus in enumerate(surplus_wh):
        start = START + timedelta(minutes=15 * i)
        slots.append(
            PlanSlot(
                start=start,
                end=start + timedelta(minutes=15),
                solar_wh=max(surplus, 0) + 100,
                load_wh=100 - min(surplus, 0),
                grid_export_wh=0,
                grid_import_wh=0,
                price_grid=grid_price[i] if grid_price else GRID,
                price_feedin=FEEDIN,
                charge_wh={"car": vehicle_wh[i] if vehicle_wh else 0.0},
            )
        )
    return Plan(
        updated=START,
        devices=[PlanDevice(type="vehicle", key="car", title="Mein Auto")],
        slots=slots,
    )


class TestAdviseAppliance(unittest.TestCase):
    def test_surplus_now(self):
        advice = advise_appliance(make_plan([300] * 8), PROFILE)
        self.assertEqual(advice.verdict, Verdict.NOW)
        self.assertEqual(advice.now.solar_share, 1.0)

    def test_surplus_later(self):
        advice = advise_appliance(make_plan([0] * 4 + [300] * 8), PROFILE)
        self.assertEqual(advice.verdict, Verdict.LATER)
        self.assertEqual(advice.best.start, START + timedelta(hours=1))
        self.assertGreater(advice.now.cost_eur - advice.best.cost_eur, 0.05)

    def test_no_surplus_picks_cheapest_grid_price(self):
        prices = [0.40] * 4 + [0.10] * 4 + [0.40] * 4
        advice = advise_appliance(make_plan([0] * 12, grid_price=prices), PROFILE)
        self.assertEqual(advice.verdict, Verdict.LATER)
        self.assertEqual(advice.best.start, START + timedelta(hours=1))
        self.assertEqual(advice.best.solar_share, 0.0)

    def test_negative_price_beats_surplus(self):
        prices = [0.30] * 4 + [-0.05] * 4 + [0.30] * 4
        surplus = [300] * 4 + [0] * 8
        advice = advise_appliance(make_plan(surplus, grid_price=prices), PROFILE)
        self.assertEqual(advice.best.start, START + timedelta(hours=1))

    def test_small_difference_is_equal(self):
        prices = [0.30] * 4 + [0.29] * 4 + [0.30] * 4
        advice = advise_appliance(make_plan([0] * 12, grid_price=prices), PROFILE)
        self.assertEqual(advice.verdict, Verdict.EQUAL)

    def test_partial_surplus_share(self):
        # 1 kW Gerät = 250 Wh je Slot, nur 125 Wh Überschuss
        advice = advise_appliance(make_plan([125] * 4), PROFILE)
        self.assertAlmostEqual(advice.now.solar_share, 0.5)

    def test_plan_too_short(self):
        self.assertIsNone(advise_appliance(make_plan([300] * 3), PROFILE))

    def test_short_first_slot(self):
        plan = make_plan([300] * 6)
        first = plan.slots[0]
        first.start = first.end - timedelta(minutes=5)
        advice = advise_appliance(plan, PROFILE)
        self.assertEqual(advice.verdict, Verdict.NOW)
        self.assertEqual(advice.now.end - advice.now.start, timedelta(minutes=60))


class TestTemperatureDependent(unittest.TestCase):
    def test_cold_slots_cost_more_energy(self):
        plan = make_plan([0] * 24, grid_price=[0.30] * 24)
        for i, slot in enumerate(plan.slots):
            slot.temperature = -10 if i < 4 else 15
        advice = advise_appliance(plan, profile_for("Klimaanlage"))
        self.assertEqual(advice.temperature_now, -10)
        self.assertEqual(advice.temperature_best, 15)
        self.assertGreater(advice.now.cost_eur, advice.best.cost_eur)

    def test_temperature_hidden_for_other_devices(self):
        plan = make_plan([0] * 8)
        for slot in plan.slots:
            slot.temperature = 5
        self.assertIsNone(advise_appliance(plan, PROFILE).temperature_now)


class TestAdviseVehicle(unittest.TestCase):
    def test_merges_contiguous_slots(self):
        plan = make_plan([0] * 8, vehicle_wh=[0, 500, 500, 0, 0, 800, 0, 0])
        advice = advise_vehicle(plan)
        self.assertEqual(advice.title, "Mein Auto")
        self.assertEqual(len(advice.windows), 2)
        self.assertEqual(advice.windows[0].start, START + timedelta(minutes=15))
        self.assertEqual(advice.windows[0].end, START + timedelta(minutes=45))
        self.assertAlmostEqual(advice.windows[0].energy_kwh, 1.0)
        self.assertAlmostEqual(advice.total_kwh, 1.8)

    def test_no_charging_planned(self):
        advice = advise_vehicle(make_plan([0] * 4))
        self.assertEqual(advice.windows, [])

    def test_no_vehicle_in_plan(self):
        plan = make_plan([0] * 4)
        plan.devices = []
        self.assertIsNone(advise_vehicle(plan))


class TestProfiles(unittest.TestCase):
    def test_alias_and_default(self):
        self.assertEqual(profile_for("Geschirrspüler").name, "Spülmaschine")
        self.assertEqual(profile_for("Föhn").name, "Föhn")
        self.assertEqual(profile_for("Waschmaschine starten").name, "Waschmaschine")
        self.assertEqual(profile_for("Laptop laden").name, "Laptop")
        self.assertEqual(profile_for("Notebook").name, "Laptop")
        self.assertEqual(profile_for("Klimaanlage zum Heizen").power_w, 300)

    def test_power_curve(self):
        ac = profile_for("Klimaanlage")
        self.assertEqual(ac.power_at(None), 300)
        self.assertEqual(ac.power_at(-20), 1000)
        self.assertEqual(ac.power_at(0), 600)
        self.assertEqual(ac.power_at(15), 200)
        self.assertEqual(ac.power_at(31.5), 600)
        self.assertEqual(ac.power_at(45), 1000)

    def test_small_devices_are_equal(self):
        plan = make_plan([0] * 4 + [300] * 20, grid_price=[0.50] * 24)
        for device in ("Laptop laden", "Handy", "Föhn"):
            advice = advise_appliance(plan, profile_for(device))
            self.assertEqual(advice.verdict, Verdict.EQUAL, device)

    def test_vehicle_detection(self):
        self.assertTrue(is_vehicle("Auto"))
        self.assertTrue(is_vehicle("Auto laden"))
        self.assertTrue(is_vehicle("Elektroauto"))
        self.assertFalse(is_vehicle("Waschmaschine"))


if __name__ == "__main__":
    unittest.main()
