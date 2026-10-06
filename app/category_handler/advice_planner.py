from datetime import datetime
from enum import Enum

from category_handler.device_profiles import DeviceProfile
from evcc_service.models import Plan, PlanSlot
from pydantic import BaseModel

# Unterschied unter dieser Schwelle gilt als "egal"
MIN_SAVING_EUR = 0.10


class Verdict(str, Enum):
    NOW = "JETZT"
    LATER = "SPÄTER"
    EQUAL = "EGAL"


class Window(BaseModel):
    start: datetime
    end: datetime
    cost_eur: float
    solar_share: float  # 0..1, Anteil der Geräteenergie aus PV-Überschuss


class ApplianceAdvice(BaseModel):
    device: str
    verdict: Verdict
    best: Window
    now: Window
    price_grid_now: float  # EUR/kWh
    price_feedin_now: float  # EUR/kWh
    # nur bei temperaturabhängigen Geräten gesetzt
    temperature_now: float | None = None
    temperature_best: float | None = None


class ChargeWindow(BaseModel):
    start: datetime
    end: datetime
    energy_kwh: float


class VehicleAdvice(BaseModel):
    title: str
    windows: list[ChargeWindow]
    total_kwh: float


def _surplus_wh(slot: PlanSlot) -> float:
    return max(0.0, slot.solar_wh - slot.load_wh)


def _window_from(slots: list[PlanSlot], first: int, profile: DeviceProfile) -> Window | None:
    """Kosten eines Laufs ab Slot `first`; None, wenn der Plan nicht lang genug ist."""
    remaining_s = profile.duration_min * 60
    energy_wh = 0.0
    covered_wh = 0.0
    cost = 0.0
    end = slots[first].start

    for slot in slots[first:]:
        if remaining_s <= 0:
            break
        used_s = min(remaining_s, (slot.end - slot.start).total_seconds())
        remaining_s -= used_s

        slot_energy = profile.power_at(slot.temperature) * used_s / 3600
        free = _surplus_wh(slot) * used_s / (slot.end - slot.start).total_seconds()
        solar_part = min(slot_energy, free)

        cost += (solar_part * slot.price_feedin + (slot_energy - solar_part) * slot.price_grid) / 1000
        energy_wh += slot_energy
        covered_wh += solar_part
        end = slot.start + (slot.end - slot.start) * (used_s / (slot.end - slot.start).total_seconds())

    if remaining_s > 0:
        return None

    return Window(
        start=slots[first].start,
        end=end,
        cost_eur=cost,
        solar_share=covered_wh / energy_wh if energy_wh else 0.0,
    )


def advise_appliance(plan: Plan, profile: DeviceProfile) -> ApplianceAdvice | None:
    windows = [
        w for i in range(len(plan.slots)) if (w := _window_from(plan.slots, i, profile))
    ]
    if not windows:
        return None

    now = windows[0]
    best = min(windows, key=lambda w: (round(w.cost_eur, 4), w.start))

    if best.start == now.start:
        verdict = Verdict.NOW
    elif now.cost_eur - best.cost_eur < MIN_SAVING_EUR:
        verdict = Verdict.EQUAL
    else:
        verdict = Verdict.LATER

    temperature_dependent = bool(profile.power_curve)
    best_slot = next(s for s in plan.slots if s.start == best.start)

    return ApplianceAdvice(
        device=profile.name,
        verdict=verdict,
        best=best,
        now=now,
        price_grid_now=plan.slots[0].price_grid,
        price_feedin_now=plan.slots[0].price_feedin,
        temperature_now=plan.slots[0].temperature if temperature_dependent else None,
        temperature_best=best_slot.temperature if temperature_dependent else None,
    )


def advise_vehicle(plan: Plan) -> VehicleAdvice | None:
    vehicle = next((d for d in plan.devices if d.type == "vehicle"), None)
    if not vehicle:
        return None

    windows: list[ChargeWindow] = []
    current: ChargeWindow | None = None
    for slot in plan.slots:
        wh = slot.charge_wh.get(vehicle.key, 0.0)
        if wh <= 0:
            current = None
            continue
        if current and current.end == slot.start:
            current.end = slot.end
            current.energy_kwh += wh / 1000
        else:
            current = ChargeWindow(start=slot.start, end=slot.end, energy_kwh=wh / 1000)
            windows.append(current)

    return VehicleAdvice(
        title=vehicle.title or vehicle.key,
        windows=windows,
        total_kwh=sum(w.energy_kwh for w in windows),
    )
