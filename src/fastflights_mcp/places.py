"""Same-city rule for multi-city trips, decided from trusted location data.

Two airports are the same city when they are in the same country and either
  * `airportsdata` gives them the same city name and the same subdivision, or
  * they are at most CITY_RADIUS_KM apart (great-circle, from the table's coordinates).
The name rule alone would join PDX and PWM (both "Portland" in the US); the distance rule
is needed because the table calls Newark's airport "Newark" and Rome's second airport
"Roma". IATA string similarity is never used, and an unknown code is never a match.
"""
import functools
import math
from typing import Any

import airportsdata

CITY_RADIUS_KM = 100
_EARTH_DIAMETER_KM = 12742


@functools.lru_cache(maxsize=1)
def _airports() -> dict[str, dict[str, Any]]:
    return airportsdata.load("IATA")


def _distance_km(a: dict[str, Any], b: dict[str, Any]) -> float:
    to_rad = math.pi / 180
    half_lat = math.sin((b["lat"] - a["lat"]) * to_rad / 2) ** 2
    half_lon = math.sin((b["lon"] - a["lon"]) * to_rad / 2) ** 2
    haversine = half_lat + math.cos(a["lat"] * to_rad) * math.cos(b["lat"] * to_rad) * half_lon
    return _EARTH_DIAMETER_KM * math.asin(math.sqrt(haversine))


def same_city(a: str, b: str) -> bool:
    first, second = _airports().get(a), _airports().get(b)
    if first is None or second is None or first["country"] != second["country"]:
        return False
    if first["city"] == second["city"] and first["subd"] == second["subd"]:
        return True
    return _distance_km(first, second) <= CITY_RADIUS_KM
