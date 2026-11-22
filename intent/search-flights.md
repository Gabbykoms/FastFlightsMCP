# FastFlightsMCP: Flight search

Status: Approved — interview complete.
Named team approvers: Nadia and Patrick
Approval status: Reviewed and approved by Nadia and Patrick, November 22, 2026.

## Purpose

Help users find flights matching their request and identify the best overall option.

## User experience

Interpret a stated budget as per person for the entire itinerary, including
both legs of a round trip. Label displayed prices and budget overages as per
person, and apply the 20% allowance on that same basis.

Before searching, the host assistant asks for any missing departure location,
destination, or travel dates. It does not invent those details. A round-trip
request needs a return date as well as a departure date; a one-way request does
not need a return date.

If cabin preference is not specified, ask the user to choose economy, premium
economy, business, or first before searching. Do not silently default to economy.
Use the user's stated cabin preference when it is already provided.

Default to one adult, with no children or infants, when passenger details are
not specified. Use explicit passenger counts and types when provided; do not
ask for passenger details merely to confirm the default.

When the user names a city instead of an airport, resolve its relevant airports
through `search_airports` and search across all of them without asking the user
to choose one. Apply this to both the origin and destination. Combine the
results into one ranked list under the same five-flight and budget rules, and
identify the actual departure and arrival airports for each option. A city name
counts as a provided location, not missing airport information. Preserve any
specific airport the user explicitly requests.

Honor explicit hard filters across all results and recommendations, including
over-budget alternatives. For example, "nonstop only" excludes every flight
with a stop, even if it is cheaper. Do not relax a hard filter automatically.
Without a hard filter, retain the best-overall selection approach and the
agreed budget rules; a soft preference such as "nonstop if possible" does not
exclude flights with stops. The agreed 20% budget allowance remains in effect
for an ordinary stated budget; an explicitly strict price ceiling overrides
that allowance.

The user receives a list of matching flights, with one flight identified as the
best overall recommendation. The recommendation includes an explanation of why
that flight was selected.

Choose the best overall flights by prioritizing fewer stops and shorter total
travel time. Use lower price to choose between otherwise similar flights.
Apply this ranking within the agreed hard filters and budget-selection rules;
it does not override them. Flights with the same number of stops and total
travel times differing by at most one hour are considered similar; prefer the
lower price between those options. When fewer stops conflicts with shorter
total travel time, prefer the shorter trip. For example, at similar prices,
rank a two-stop flight taking 8 hours above a one-stop flight taking 10 hours,
provided both satisfy the user's hard filters.

For each flight, show the airline, price, departure and arrival times, total
duration, stops, and a Google Flights link.

When no budget is specified, proceed without asking for one. Show the five best
overall available flights that meet the user's other criteria, or fewer if fewer
are available, and explain one best overall recommendation.

When a budget is specified, show up to five flights total: the three best
within-budget flights and two over-budget alternatives within the 20% ceiling.
Clearly distinguish the two groups within that total. If fewer than two
worthwhile over-budget alternatives are available, fill their unused slots
with the next-best within-budget flights, up to five flights total.
If fewer than three within-budget flights are available, fill unused slots with
additional worthwhile over-budget alternatives within the same 20% ceiling.
For example, show two within-budget flights and three over-budget alternatives
when those options are available. Show fewer than five if there are not enough
eligible options across both groups.

For example, when both options fit the user's budget, prefer a $550 nonstop
flight taking 7 hours over a $450 flight with one stop taking 12 hours. In this
case, saving five hours and avoiding a stop is worth the additional $100. This
example guides the recommendation; it does not establish a universal price
threshold or a rule that nonstop flights always win.

When a user specifies a budget, show a better over-budget option separately as
an alternative, clearly stating its price and how much it exceeds the budget.
Keep the main recommendation within budget when matching flights are available.
For example, with a $500 budget, show the $550 nonstop as an alternative that is
$50 over budget while recommending an eligible flight from the matching list.
Over-budget alternatives may cost at most 20% above the stated budget, inclusive.
For a $500 budget, alternatives may cost up to $600; options above $600 are excluded.
When within-budget flights exist, an over-budget alternative is worthwhile only
if it ranks better than the best within-budget flight under the agreed ranking
rules. If none do, fill the available slots with within-budget flights instead.
When no within-budget flights exist, rank eligible alternatives against each
other; there is no within-budget flight to beat.
If no flights fit the stated budget, explicitly say so and recommend the best
overall available alternative within the 20% allowance. Clearly state that the
recommendation exceeds the budget and by how much. This exception applies only
when no within-budget flights are available.
If no flights qualify even within the 20% allowance, explain that no qualifying
flights were found and ask whether the user wants to change their budget, dates,
or airports. Do not change those preferences automatically. The host assistant
asks this follow-up; the server returns a normal empty result rather than trying
to ask the user itself.

## Success criteria

- Ask for cabin preference before searching when it is missing; do not ask again
  when the user has already specified it.
- With no passenger details, search for one adult and no children or infants.
  Honor explicitly supplied passenger counts and types instead of the defaults.
- Treat a $500 round-trip budget as $500 per person for the complete round trip,
  not $500 per leg or $500 for the whole traveling party. Its 20% allowance is
  capped at $600 per person for that complete itinerary.
- If the departure location, destination, or required travel dates are missing,
  ask for the missing information before calling `search_flights`.
- For a city with multiple relevant airports, search all resolved airports and
  present a combined list of up to five flights, not five flights per airport.
  Show each option's actual airports and honor explicit airport selections.
- With "nonstop only," show no flights with stops in either the main list or
  alternatives. Apply other explicit hard filters equally strictly; if none
  are specified, retain the existing ranking and budget behavior.
- If the user explicitly makes their price ceiling a hard filter, show no
  over-budget alternatives. An ordinary stated budget retains the 20% allowance.
- Present a list of matching flights rather than only a single recommendation.
- Include the airline, price, departure and arrival times, total duration,
  stops, and a Google Flights link for each flight.
- Without a stated budget, show up to five best overall flights meeting the
  other criteria and one explained recommendation; do not require a budget first.
- When both groups have enough eligible options, show exactly five flights:
  the three best within budget and two over budget, each no more than 20% above
  budget. Over-budget alternatives count toward the five-flight total.
- If no worthwhile over-budget alternatives exist and at least five within-budget
  flights are available, show the five best within-budget flights. If only one
  worthwhile alternative exists, show four within budget and that alternative
  when enough within-budget flights are available.
- When only two within-budget flights exist and at least three worthwhile
  over-budget alternatives meet the 20% ceiling, show those five flights with
  their budget status clearly identified.
- Identify one best overall flight from that list and explain the choice when
  matching flights are available.
- Prioritize fewer stops and shorter total travel time; prefer the lower price
  when flights are otherwise similar, while respecting hard filters and budget
  rules.
- For flights with the same number of stops, treat a duration difference of
  60 minutes or less as similar and prefer the cheaper option. For example,
  prefer a $450 seven-hour flight over a $500 six-hour flight when both meet
  the other requirements.
- At similar prices, prefer a two-stop eight-hour flight over a one-stop
  ten-hour flight when both satisfy the user's hard filters: shorter total
  travel time takes priority over fewer stops.
- In the example above, recommend the $550 nonstop and explain the time savings
  and avoided stop alongside the additional cost.
- With a $500 budget, distinguish the $550 nonstop as an over-budget alternative,
  not a matching result or the main recommendation.
- Enforce the 20% ceiling for over-budget alternatives: a $600 option is eligible
  for consideration with a $500 budget, but a $601 option is not.
- When within-budget flights exist, show an over-budget alternative only if it
  ranks better than the best within-budget flight. Otherwise, use the available
  within-budget flights to fill the list, up to five total.
- If no within-budget flights exist but eligible alternatives do, recommend the
  best overall alternative and explain both its advantages and its budget overage.
- When no flights qualify within the 20% allowance, report no qualifying results
  and ask whether to adjust budget, dates, or airports. Preserve the existing
  preferences until the user chooses a change.

## Open decisions

- None. Reviewed and approved by Nadia and Patrick.
