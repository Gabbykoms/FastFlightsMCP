"""Tests-first contract for the same-city rule, `fastflights_mcp.places.same_city`.

Multi-city trips need "the next leg departs from the previous leg's destination city".
The rule uses trusted location data (the pinned `airportsdata` table), never IATA string
similarity. Two airports are the same city when they are in the same country and either
  * `airportsdata` gives them the same city name and the same subdivision, or
  * they are at most 100 km apart (great-circle, from the table's latitude and longitude).
The name rule alone is not enough: PDX and PWM are both "Portland" in the US. The
distance rule is needed because the table calls Newark's airport "Newark" and Rome's
second airport "Roma".

Interface the implementation must provide
-----------------------------------------
fastflights_mcp.places
  CITY_RADIUS_KM = 100
  same_city(a: str, b: str) -> bool     IATA codes; an unknown code is never the same city
                                        as anything, itself included
"""
import importlib

import pytest


@pytest.fixture
def places():
    # Imported inside a fixture so every test is collected before the module exists.
    return importlib.import_module("fastflights_mcp.places")


def test_radius_is_one_hundred_kilometres(places):
    assert places.CITY_RADIUS_KM == 100


@pytest.mark.parametrize("a,b", [
    ("JFK", "LGA"),   # same table city, "New York"
    ("LHR", "LGW"),   # same table city, "London"
    ("CDG", "ORY"),   # same table city, "Paris"
    ("JFK", "EWR"),   # the table says "Newark"; 33 km apart
    ("FCO", "CIA"),   # the table says "Rome" and "Roma"; 29 km apart
    ("LHR", "STN"),   # 67 km apart
    ("SFO", "SJC"),   # "San Francisco" and "San Jose", 49 km apart
    ("LGA", "LGA"),   # an airport is its own city
])
def test_airports_of_one_metro_area_are_the_same_city(places, a, b):
    assert places.same_city(a, b) is True
    assert places.same_city(b, a) is True


@pytest.mark.parametrize("a,b", [
    ("JFK", "BOS"),   # 300 km
    ("JFK", "PHL"),   # 151 km
    ("EWR", "PHL"),   # 129 km
    ("LHR", "CDG"),   # 347 km
    ("LHR", "LIS"),
    ("PDX", "PWM"),   # both "Portland" in the US, 4000 km apart
])
def test_distant_airports_are_different_cities(places, a, b):
    assert places.same_city(a, b) is False
    assert places.same_city(b, a) is False


def test_a_nearby_airport_in_another_country_is_a_different_city(places):
    # Detroit (US) and Windsor (Canada) are 33 km apart, across a border.
    assert places.same_city("DTW", "YQG") is False


@pytest.mark.parametrize("a,b", [("ZZZ", "JFK"), ("JFK", "ZZZ"), ("ZZZ", "ZZZ"), ("", "JFK")])
def test_unknown_airports_are_never_the_same_city(places, a, b):
    assert places.same_city(a, b) is False


def test_similar_codes_do_not_make_a_city(places):
    # IATA string similarity must not decide anything: LGA (New York) and LGB (Long Beach,
    # California) share two letters and are 3,900 km apart.
    assert places.same_city("LGA", "LGB") is False
