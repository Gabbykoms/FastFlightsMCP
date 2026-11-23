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

## What these do and do not prove

They show the provider's real response shape for these requests only. Times are
local `date` plus `HH:MM` with no UTC offset. Nine of the 14 options have layovers.
Not covered: empty results (what SearchApi returns when no flights exist is unknown),
errors other than 401, multiple airports, infants, and round-trip pricing for more than
one adult. Do not describe other behavior as provider-verified.
