from itertools import pairwise

from pydantic import BaseModel


class DeviceProfile(BaseModel):
    name: str
    power_w: float  # mittlere Leistung während des Laufs
    duration_min: int
    # (Außentemperatur °C, Leistung W), aufsteigend nach Temperatur, dazwischen linear
    power_curve: list[tuple[float, float]] | None = None

    def power_at(self, temperature: float | None) -> float:
        if not self.power_curve or temperature is None:
            return self.power_w
        points = self.power_curve
        if temperature <= points[0][0]:
            return points[0][1]
        for (t0, p0), (t1, p1) in pairwise(points):
            if temperature <= t1:
                return p0 + (p1 - p0) * (temperature - t0) / (t1 - t0)
        return points[-1][1]


DEFAULT_PROFILE = DeviceProfile(name="Gerät", power_w=100, duration_min=120)

_PROFILES = {
    "spülmaschine": DeviceProfile(name="Spülmaschine", power_w=1000, duration_min=120),
    "waschmaschine": DeviceProfile(name="Waschmaschine", power_w=700, duration_min=120),
    "trockner": DeviceProfile(name="Trockner", power_w=600, duration_min=150),
    "laptop": DeviceProfile(name="Laptop", power_w=60, duration_min=180),
    "handy": DeviceProfile(name="Handy", power_w=10, duration_min=120),
    "e-bike": DeviceProfile(name="E-Bike", power_w=200, duration_min=180),
    "klimaanlage": DeviceProfile(
        name="Klimaanlage",
        power_w=300,
        duration_min=180,
        power_curve=[(-10, 1000), (10, 200), (25, 200), (38, 1000)],
    ),
}

_ALIASES = {
    "geschirrspüler": "spülmaschine",
    "geschirrspülmaschine": "spülmaschine",
    "wäschetrockner": "trockner",
    "notebook": "laptop",
    "smartphone": "handy",
    "ebike": "e-bike",
    "fahrrad": "e-bike",
    "klima": "klimaanlage",
}

_VEHICLE_WORDS = ("auto", "fahrzeug", "wallbox")


# Alexa liefert den Slot teils mit Verb ("Auto laden"), daher Teilstring statt Gleichheit
def is_vehicle(device: str) -> bool:
    text = device.lower()
    return any(word in text for word in _VEHICLE_WORDS)


def profile_for(device: str) -> DeviceProfile:
    text = device.strip().lower()
    for word, key in {**{k: k for k in _PROFILES}, **_ALIASES}.items():
        if word in text:
            return _PROFILES[key]
    return DEFAULT_PROFILE.model_copy(update={"name": device.strip() or "Gerät"})
