# FastFlightsMCP: Multi-city search

Status: Ready for team review — interview complete.
Named team approvers: Nadia and Patrick
Approval status: Pending review.

## Purpose

Help users find and compare flight options for a trip with 2 to 6 requested
legs, such as New York → London, London → Paris, and Paris → New York.

Let users compare complete itineraries and choose flights independently for each
leg. This tool searches flights; it does not book or purchase tickets.

## User experience

The user describes the legs of their trip in travel order. Each requested leg
has a departure location, destination, and departure date. A requested leg is
distinct from a connecting flight within that leg.

For example: "New York to London on March 10, 2027, London to Paris on March 14,
and Paris to New York on March 20."

Before searching, the host assistant asks for any missing origin, destination,
or departure date for each requested leg. If the supplied dates conflict with
the requested travel order, explain the conflict and ask the user to correct
it before searching. Do not invent missing details, reorder legs, or change
dates or locations automatically. Use details already supplied without asking
the user to repeat them; a city name is sufficient location information.

Support both complete-itinerary comparisons with a total price and selection
of individual flights for each requested leg. Users can evaluate the whole trip
or choose their preferred flight for each leg.

Show complete itineraries with total prices first. Then let the user customize
individual legs. After a leg changes, recheck availability and refresh the
complete trip's total price before presenting the revised option. Clearly
identify when the revised option requires separate tickets.

For each complete itinerary, show the total price per person and total travel
duration. For every leg, show the airline, departure and arrival airports,
departure and arrival times, duration, stops, and a Google Flights link.
Use the same leg details when presenting customization options, alongside
their updated whole-trip prices.

Interpret a stated budget as per person for the entire itinerary, covering all
requested legs. Apply the same basis to complete-itinerary results and revised
trips after per-leg customization. Label prices and budget overages as per person.
Over-budget alternatives may cost at most 20% above an ordinary stated budget,
inclusive. An explicitly strict price ceiling overrides this allowance: do not
show options exceeding that ceiling.

If cabin preference is missing, the host assistant asks the user to choose
economy, premium economy, business, or first before searching. Use an already
stated preference without asking again; do not silently default to economy.
Apply the chosen cabin across all legs.

Default to one adult, with no children or infants, when passenger details are
missing. Honor explicit passenger counts and types without asking merely to
confirm the defaults. Apply the same passenger party across all legs.

When the user names a city, resolve its relevant airports through
`search_airports` and search across all of them without asking the user to
choose an airport. Apply this to the origin and destination of every leg.
A city name counts as a provided location. Preserve any airport explicitly
requested for a leg, and show the actual departure and arrival airports for
each leg of every presented itinerary.

Require city continuity between successive legs: each later leg must depart
from the previous leg's destination city. Do not support gaps that require the
user to arrange travel between different cities. If a request includes such a
gap, explain the constraint and ask the user to revise the route before searching;
do not silently add a flight or change a location. Preserve this constraint
during per-leg customization.

Within the same city, require the next leg to depart from the previous leg's
arrival airport unless the layover is at least 24 elapsed hours between arrival
and onward departure. For a qualifying layover,
an airport change within that city is allowed; clearly identify both airports
and the transfer the user must arrange. Apply this rule to complete itineraries
and per-leg customization. Crossing midnight alone does not qualify.

When customizing a leg, show up to five eligible flight options for that leg.
For each option, show the updated whole-trip price per person with the other
selected legs held fixed. Evaluate eligibility against the complete trip's
budget, applicable hard filters, city continuity, and airport-change rules.
Clearly label any permitted budget overage. Show fewer than five options when
fewer qualify; do not fill the list by relaxing the user's constraints.

For itinerary ranking, count time in flight and time spent in transit,
including waits for connecting flights. Sum the travel duration of all requested
legs; do not count only airborne time. Exclude time spent visiting cities
between requested legs from this travel-duration total.

Rank complete itineraries using the same approach as regular flight search:
prioritize shorter total travel time and fewer connecting stops across all
legs. Requested city visits do not count as connecting stops. When fewer stops
conflicts with shorter travel time, prefer the shorter trip. For itineraries
with the same number of connecting stops and total travel durations differing
by at most one hour, prefer the lower price. Apply ranking within the user's
hard filters and agreed budget-selection rules; ranking does not override them.

Without a stated budget, show up to five best overall complete itineraries and
explain one best overall recommendation. Do not ask for a budget before searching.
Combine airport combinations into that single list; the limit is not per airport.

With a budget, show up to five complete itineraries total: the three best within
budget and two worthwhile over-budget alternatives within the inclusive 20%
allowance. Clearly distinguish the groups. When within-budget itineraries exist,
an over-budget alternative is worthwhile only if it ranks better than the best
within-budget itinerary. Keep the main recommendation within budget and explain
its selection; label each alternative's price and budget overage.

Fill unused alternative slots with the next-best within-budget itineraries.
If fewer than three within-budget itineraries exist, fill their unused slots
with additional worthwhile alternatives within the allowance. Show fewer than
five when there are not enough eligible options. With a strict price ceiling,
fill the list only with itineraries at or below that ceiling.

If no within-budget itineraries exist, explicitly say so and recommend the best
eligible alternative within the allowance, explaining its price and overage.
Rank these alternatives against each other. If no itineraries qualify, the
server returns a normal empty result; the host assistant explains that none
were found and asks whether to change the budget, dates, or airports. Do not
change those preferences automatically.

Honor explicit hard filters across every requested leg unless the user scopes
a filter to a particular leg. Apply them equally to complete itineraries,
over-budget alternatives, and per-leg customization. For example, "nonstop
only" excludes connecting flights on every leg; "nonstop only from London to
Paris" restricts that leg. Do not relax hard filters automatically. A soft
preference such as "nonstop if possible" does not exclude connecting flights.

The intended search tool is `search_multi_city`.

Show only options that form a complete eligible itinerary covering every
requested leg. Do not display partial itineraries or isolated available legs
when they cannot be combined into a complete trip. During customization, show
a replacement flight only if it forms a complete eligible itinerary with the
other selected legs. If any requested leg has no qualifying flight and no
complete itinerary can be formed, report that no complete itinerary was found
and ask whether the user wants to adjust their preferences; do not show the
remaining legs as partial results or change the route automatically.

## Success criteria

- Every displayed complete itinerary includes its total price per person and
  total travel duration. Each leg includes its airline, airports, departure
  and arrival times, duration, stops, and Google Flights link. Customization
  options include these leg details and the revised whole-trip price.
- If flights exist for only two of three requested legs, show no partial
  itinerary or standalone flight options. Explain that no complete itinerary
  was found and ask about adjusting preferences.
- Exclude a per-leg customization option if it cannot form a complete eligible
  trip with the other selected legs, even when that flight is available alone.
- Before calling `search_multi_city`, ask for missing origins, destinations,
  or departure dates and resolve conflicting dates with the user. Preserve
  supplied details and travel order rather than inventing or changing them.
- During per-leg customization, show at most five eligible options, each with
  the revised whole-trip price per person. Keep other selected legs fixed and
  enforce the trip's budget rules, filters, and route-continuity requirements.
- With "nonstop only," exclude any itinerary containing a connecting flight
  on any requested leg, including alternatives and customized trips. If the
  user limits that filter to one leg, enforce it on that leg without extending
  it to the others. Treat "nonstop if possible" as a soft preference.
- Without a budget, show up to five complete itineraries and one explained
  recommendation without requiring a budget first.
- When both groups have enough eligible options, show three within-budget
  itineraries and two worthwhile alternatives, with five total across all
  airport combinations. Every alternative must respect the 20% ceiling.
- If no alternative ranks better than the best within-budget itinerary, show
  up to five within-budget itineraries. If only two within-budget itineraries
  exist and three worthwhile alternatives qualify, show those five options.
- Keep the recommendation within budget whenever an eligible within-budget
  itinerary exists. Otherwise, recommend the best eligible alternative and
  explicitly explain its budget overage.
- If no itineraries qualify, return a normal empty result and have the host
  assistant ask about changing preferences without changing them automatically.
- With the same total number of connecting stops, treat a travel-duration
  difference of 60 minutes or less as similar and prefer the cheaper itinerary,
  subject to hard filters and budget-selection rules.
- At similar prices, rank an itinerary with two connecting stops and eight
  total travel hours above one with one connecting stop and ten travel hours
  when both satisfy the user's hard filters. Do not count requested city visits
  as connecting stops.
- Include connecting-flight waits in ranked travel duration. A leg with two
  hours of flying, a three-hour connection, and another two hours of flying
  contributes seven hours, not four. Time spent visiting a city between
  requested legs does not add to the travel-duration total.
- Require the same arrival and onward departure airport for layovers shorter
  than 24 elapsed hours, including overnight stops. At exactly 24 hours or
  longer, permit a change between airports in the same city and clearly
  disclose the transfer.
- For New York → London followed by Paris → Rome, ask the user to revise the
  route before searching because London and Paris do not match. Do not invent
  a London → Paris leg. Enforce city continuity after customization too.
- Support a request with 2 to 6 legs, preserving the user's requested travel
  order, locations, and dates.
- Treat New York → London → Paris → New York as three requested legs,
  regardless of any connections within those legs.
- Search and compare flight options without purchasing tickets.
- Offer complete itineraries with total prices as well as independently
  selectable flight options for each requested leg.
- Present complete itineraries first and allow subsequent per-leg customization.
- After customization, recheck availability, update the total trip price, and
  identify any separate-ticket requirement.
- Treat a $1,000 budget as $1,000 per person across all requested legs, not per
  leg or for the entire traveling party. Alternatives may cost up to $1,200
  per person inclusive; a $1,201 itinerary is excluded. Apply this rule after
  customization as well.
- With an explicitly strict $1,000 ceiling, exclude every itinerary above
  $1,000 per person, including alternatives and customized trips.
- Ask for cabin preference before searching when missing, and use an existing
  preference without asking again. Apply the chosen cabin to every leg.
- With no passenger details, search every leg for one adult and no children or
  infants. When passenger details are supplied, use that party for every leg.
- Resolve city names through `search_airports` for every leg and search all
  relevant airports without requiring the user to select one. Honor explicitly
  requested airports and display each leg's actual departure and arrival airports.

## Open decisions

- Final review and approval by Nadia and Patrick are pending.
