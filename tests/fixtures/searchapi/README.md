# SearchApi Google Flights recordings (sanitized)

Real responses from `https://www.searchapi.io/api/v1/search?engine=google_flights`,
recorded once on 2026-11-22 (about 04:53 UTC on 2026-11-23). Three successful live
requests; no tool code or model was involved.

| File | Request | Notes |
|---|---|---|
| `one_way.json` | JFK to LIS, `flight_type=one_way`, outbound 2027-01-21, economy, 1 adult, USD | Options carry a `booking_token` and a one-way `price`. |
| `round_trip_outbound.json` | Same route, `flight_type=round_trip`, outbound 2027-01-21, return 2027-01-28 | Outbound legs only. Each option has a `departure_token`; `price` is the round-trip price. |
| `round_trip_return.json` | Same, plus the first outbound option's `departure_token` | Return legs for that one outbound choice, with a `booking_token`. |
| `one_way_2_adults.json` | Same as `one_way.json` but `adults=2` (recorded 2026-11-23) | Same 5 options. See "Price basis". |
| `one_way_1_adult_1_child.json` | Same as `one_way.json` but `adults=1`, `children=1` (recorded 2026-11-23) | Same 5 options. See "Price basis". |
| `error_401_invalid_key.json` | HTTP 401 body returned for a rejected key (2026-11-22) | The raw file was overwritten by a later run; this is the body exactly as it was printed (33 bytes, no trailing newline). Not billed. |
| `location_search_new_york.json` | `engine=google_flights_location_search`, `q=New York` (recorded 2026-11-23) | 5 places, each with a nested `airports` list (`airport_code`, `title`, `city`, `distance`) and no `country`. The same request with `query=` instead of `q=` returned HTTP 400 "Missing required parameter q.". |
| `multi_city_leg1.json` | `flight_type=multi_city`, `multi_city_json` = JFK,EWR,LGA to LHR,LGW on 2027-01-05, then LHR,LGW to CDG,ORY on 2027-01-09, then CDG,ORY to JFK,EWR,LGA on 2027-01-14; economy, 1 adult, USD (recorded 2026-11-24) | Step 1 of the chain: 32 leg-1 options (EWR, JFK, LGA to LHR/LGW). Every option has a `departure_token`, none a `booking_token`. `type` is "Multi city". |
| `multi_city_leg2.json` | Same, plus the `departure_token` of the first leg-1 option (UA 122, EWR to LHR, 09:20, $701) | Step 2: 15 leg-2 options (LHR/LGW to CDG/ORY on 2027-01-09), each with a `departure_token`. |
| `multi_city_leg3.json` | Same, plus the `departure_token` of the cheapest leg-2 option that departs LHR (VY 8058, LHR to ORY, 15:15, $722) | Step 3, the last leg: 16 options (CDG/ORY to EWR/JFK/LGA on 2027-01-14), each with a `booking_token` and no `departure_token`. Cheapest is $722 (ORY to JFK, VY 8001 + LL 2627). |
| `multi_city_leg3_no_results.json` | Same as the leg-3 request but with the `departure_token` of the first leg-2 option (U2 8405, LGW to CDG, 12:00, $701) | **HTTP 200 with `error`: "Google Flights didn't return any results."** and no options. The cause is unknown. Billed, since the status was 200. |

## Sanitization

- The key was sent in an `Authorization` header, never in the URL. Verified by search
  that the key text, `api_key`, `bearer` and `authorization` appear in no file.
- `search_metadata.id`, `html_url` and `json_url` (SearchApi search IDs) are replaced
  with `search_REDACTED`. Nothing else was edited.
- Opaque `departure_token` and `booking_token` values and the Google Flights
  `request_url` are kept as returned; they are not credentials.

## Price basis

For a one-way search with 2 adults, all 5 options cost 2.00 times their 1-adult price
(404 to 807, 527 to 1053, 436 to 872, 521 to 1041, 535 to 1069), within $1 of integer
rounding. So `price` is the **party total**, not a per-person fare. This is verified
for adults only and for one-way only.

For 1 adult plus 1 child, 4 of the 5 options are also 2.00 times the 1-adult price (the
child is priced like the adult). The fifth, Royal Air Maroc AT 201 + AT 980, costs 993
instead of 1042 (1.91 times), so on that airline the child fare is lower. A party total
that includes children therefore **cannot be divided into per-person fares** by headcount
without asserting a fare the provider did not give. Infants and round-trip pricing for
larger parties are not recorded.

## Multi-city

Recorded for the `search_multi_city` adapter (4 billed requests, all HTTP 200, on 2026-11-24).
What the recording shows, for these requests only:

- One request returns the options for **one leg**, so a trip with N legs takes N chained
  requests. Each non-final option carries a `departure_token` that selects it; the next
  request repeats the same `multi_city_json` and adds that token.
- Several airports per leg work: `JFK,EWR,LGA` came back as EWR, JFK and LGA flights.
- Each response describes only its own leg. Neither the chosen earlier flights nor a trip
  total other than `price` appears in it, so the complete itinerary must be assembled from
  the flights chosen at each step.
- `price` appears to be a projected whole-trip total at every step: leg 1 starts at $661, leg 2
  at $701 (the price of the leg-1 option that was followed, not a leg-2 fare) and the leg-3
  options start at $722 (the price of the leg-2 option that was followed). It is **not
  verified** that the leg-3 price equals the sum of three separately priced legs, and
  multi-city pricing for more than one adult is not recorded.
- The final leg's options carry a `booking_token`; the earlier legs' carry a `departure_token`.
- A token can lead to no results: the leg-3 request for the first leg-2 option returned
  HTTP 200 with an `error` key instead of options. An adapter must therefore treat an
  `error` body on one branch as a dead branch, not necessarily as a failed search. Why that
  token failed is unknown (a time or fare incompatibility with leg 3 is possible but not
  checked).
- Times are local `date` plus `HH:MM` with no UTC offset, as in the other recordings.

## What these do and do not prove

They show the provider's real response shape for these requests only. Times are
local `date` plus `HH:MM` with no UTC offset. Nine of the 14 options have layovers.
Not covered: empty results for one-way and round-trip searches (what SearchApi returns when no flights exist is unknown),
errors other than 401, multiple airports outside multi-city, infants, and round-trip pricing for more than
one adult. Location Search is recorded for one query (a city name); airport-name queries are not recorded. Do not describe other behavior as provider-verified.
