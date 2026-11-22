# FastFlightsMCP: Price insight

Status: Draft — under review.
Named team approver:

## Purpose

Tell the traveler whether the price they are looking at now is low, typical, or high compared with what the same trip has cost when searched at other times, so they can decide whether to book now or keep watching.

## User experience

The user asks something like "Is $520 a good price for JFK to Lisbon on March 10?" or "Was this cheaper two weeks ago?" The assistant looks up the trip (route, dates, passengers, cabin) and answers with:

- the current lowest price for the trip,
- Google's price level for it (low, typical, or high) and the typical price range,
- how today's price compares with what the same search returned earlier, for example "$520 today; $610 two weeks ago; $480 a month ago," from the trip's price history.

The answer names the route, dates, passengers, cabin, and currency it is based on, and says that prices change and must be confirmed before booking.

The comparison looks backward only. If the user asks what the price will be if they search again in a month, the assistant explains that no future price is known, and offers the current price level and the past trend instead of a prediction. If the history does not reach back as far as the user asks, or Google has no insight for that trip, the assistant says so plainly rather than guessing.

## Success criteria

- The current price, price level, typical range, and every past price shown are exactly what the flight data service returned. The assistant does not compute its own "low/high" judgment or invent a past price.
- Past comparison points (for example, two weeks ago and a month ago) come from the trip's price history. If the history has no point near the requested date, the result says so.
- No future price is presented as known. A question about future prices gets an explanation, not a number.
- When no price insight is available for the trip, the result says so and is not an error.
- The route, dates, passengers, cabin, and currency are shown with the insight.
- Every answer carries a reminder that prices change and must be confirmed on the booking site.
- A failure of the flight data service produces a clear message, not a crash or an invented answer.
- Tests run with no key and no network, using saved or faked responses.

## Open decisions

- **Data source (decided: SearchApi).** The `google_flights` engine returns a `price_insights` object: `lowest_price`, `price_level`, `typical_price_range {low_price, high_price}`, `price_history [{price, iso_date}]`, and `cheapest_to_book.estimated_savings`. There is no separate insight endpoint, so one insight costs one billed flight search.
- **What "the same flight" means.** Google's price history appears to be for the trip (route, dates, and search options), not one flight number. Confirm with a real call, and word results as "this trip," not "this flight," if so.
- **How far back the history goes.** The docs do not say. Check with real calls before promising "a month ago."
- **Which comparison points to return.** Fixed points (two weeks ago, a month ago), whatever the user asks for, or a summary (lowest, highest, trend over the history)?
- **Values of `price_level`.** The docs only show `"typical"`. Confirm the other values (`low`, `high`?) with real calls before writing the schema.
- **`cheapest_to_book.estimated_savings`.** Show it? It is Google's estimate, not a known future price, and must be labelled that way if used.
- **Separate tool or part of `search_flights`?** `search_flights` gets `price_insights` in the same response. A separate tool is clearer for the model but bills a second search for the same trip. Decide with whoever owns `search_flights`.
- **Currency and cost.** Echo the currency requested, count each call against the agent loop's cost cap, and keep `SEARCHAPI_API_KEY` in the server's environment only.
- **Not advice.** Should the assistant only report the signal, or may it suggest booking or waiting?
