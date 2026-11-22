# FastFlightsMCP: Cheapest dates

Status: Draft — under review.
Named team approver:

## Purpose

Help a flexible traveler find the cheapest days to fly a route, instead of searching one date at a time.

## User experience

The user names a route and a window of dates in plain language, for example "a week in Lisbon sometime in March, leaving from NYC." The assistant asks for whatever it cannot infer (the route, the window, one-way or round trip, and for round trips, how long the trip should be), then returns the cheapest departure dates, or departure and return pairs, with the price of each.

The answer shows the route, the window searched, the passengers and cabin, and the currency, so the user can check what was searched. It lists a short set of the cheapest options rather than the whole calendar, marks the lowest price, and says which dates had no flights. Each option can be taken into a normal flight search to see the actual flights.

If the window is too large to search at once, the assistant says so and asks the user to narrow it (or splits the window into smaller searches). If the flight data service fails, the user is told the search could not be completed. The assistant never estimates or invents prices.

## Success criteria

- One-way and round-trip searches both work. Round trips respect the requested trip length or return-date range.
- Every price comes from the flight data service. The assistant never fills in a price for a date that came back with no result.
- Dates with no flights are reported as such, not left out silently and not shown as errors.
- The cheapest option is clearly marked, and options are ordered by price.
- The route, dates searched, passengers, cabin, and currency are shown with the results.
- A window that is too large is rejected with a message the user can act on before any request is made.
- A failure of the flight data service (bad key, quota, timeout, unknown airport) produces a clear message, not a crash and not a made-up answer.
- Tests run with no key and no network, using saved or faked responses.

## Open decisions

- **Data source.** SearchApi's `google_flights_calendar` engine (`https://www.searchapi.io/api/v1/search?engine=google_flights_calendar`) returns a `calendar` list of `{departure, return, price, is_lowest_price, has_no_flights}`. This replaces `fast-flights` in the README; the team needs to agree and update `spec.md` and `README.md`.
- **Window limits.** SearchApi allows at most 200 outbound/return combinations for round trips and a 200-day range for one-way searches, and returns an *empty* response (not an error) past those limits. Reject or split in our code before calling?
- **Trip length.** Ask for a fixed number of nights, a min/max range, or explicit return dates? The API takes return-date ranges, so nights must be converted.
- **How many options to return** (top 5? top 10?) and whether `limit` is a tool argument.
- **Currency.** The calendar response has no currency field, so the tool must echo the currency it requested.
- **Cost.** Each call is a billed SearchApi search. Should the tool cache identical searches, and how does the agent loop's cost cap count them?
- **Credential.** `SEARCHAPI_API_KEY` lives in the server's environment only and never appears in a result, log, or commit.
- **Errors.** SearchApi's docs do not describe error responses, rate limits, or invalid-airport behavior. Check the OpenAPI spec or test one call before writing the error cases.
- **One-way with no return date.** The docs are inconsistent about whether `return_date` is required. Verify with one real call.
