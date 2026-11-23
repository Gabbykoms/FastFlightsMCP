# SearchApi Google Flights recordings (sanitized)

Real responses from `https://www.searchapi.io/api/v1/search?engine=google_flights`,
recorded once on 2026-11-22 (about 04:53 UTC on 2026-11-23). Three successful live
requests; no tool code or model was involved.

| File | Request | Notes |
|---|---|---|
| `one_way.json` | JFK to LIS, `flight_type=one_way`, outbound 2027-01-21, economy, 1 adult, USD | Options carry a `booking_token` and a one-way `price`. |
| `round_trip_outbound.json` | Same route, `flight_type=round_trip`, outbound 2027-01-21, return 2027-01-28 | Outbound legs only. Each option has a `departure_token`; `price` is the round-trip price. |
| `round_trip_return.json` | Same, plus the first outbound option's `departure_token` | Return legs for that one outbound choice, with a `booking_token`. |

## Sanitization

- The key was sent in an `Authorization` header, never in the URL. Verified by search
  that the key text, `api_key`, `bearer` and `authorization` appear in no file.
- `search_metadata.id`, `html_url` and `json_url` (SearchApi search IDs) are replaced
  with `search_REDACTED`. Nothing else was edited.
- Opaque `departure_token` and `booking_token` values and the Google Flights
  `request_url` are kept as returned; they are not credentials.

## What these do and do not prove

They show the provider's real response shape for these three requests only. Times are
local `date` plus `HH:MM` with no UTC offset. One recording per case does not cover
empty results, errors, multiple airports, children or infants, or mixed-passenger
pricing. Do not describe other behavior as provider-verified.
