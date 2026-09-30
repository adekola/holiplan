# Example conversations

Four invented families, one feature each, planning 2027 with Claude. Every date and figure is
what the holiplan tools returned for them, using OpenHolidays data fetched on 30 September 2026.
The assistant's wording is illustrative. The tool calls it makes are shown in brackets.

## 1. Vienna: what does the year offer?

*A family in Vienna, 25 days of leave. The profile gives the region as `AT-9`, its ISO code;
holiplan maps it to OpenHolidays' `AT-WI`.*

> **You:** Here's our ledger. What do next year's school holidays look like, and where are the
> cheap days off?

*[get_holiday_windows AT, 2027, AT-9, home "Wien"; find_bridge_days with the ledger]*

> **Claude:** Counting the weekends and holidays either side, Vienna's 2027 school holidays give
> you:
>
> | Holiday | Days off |
> | --- | --- |
> | Semester | 30 Jan – 7 Feb, 9 days (costs 5 days of leave) |
> | Easter | 20–29 Mar, 10 days |
> | Summer | 3 Jul – 5 Sep, 65 days |
> | Autumn | 26 Oct – 1 Nov, 7 days |
> | Christmas | 24 Dec – 6 Jan 2028, 14 days |
>
> Pentecost (15–17 May), All Souls' Day (2 Nov) and St Leopold's Day (15 Nov) are single school
> closures.
>
> Three leave days each buy a four-day weekend:
> - **Fri 7 May**, after Ascension.
> - **Fri 28 May**, after Corpus Christi.
> - **Mon 25 Oct**, before National Day. The autumn holidays start the next day, so that one
>   runs straight into them.

## 2. Cologne: carryover about to expire

*A family in Cologne (`DE-NW`). One parent works Monday to Thursday, with 24 days for 2027 plus
4 carried over that expire on 31 March. So far the only plan is two weeks on the Baltic coast
in July.*

> **You:** Does our year add up?

*[check_ledger; get_year_budget 2027]*

> **Claude:** The summer trip costs 8 days, since Fridays are already off for you. That leaves 20
> of 28. One warning: **your 4 carried-over days expire on 31 March, and nothing planned uses
> them.**

> **You:** What would the week before Easter cost?

*[calculate_leave_cost 2027-03-20 to 2027-03-28 with the ledger]*

> **Claude:** Saturday 20 to Sunday 28 March costs **4 days**: Monday to Thursday. It ends
> before 31 March, so it would use up exactly the days that are about to expire.

> **You:** Add it as an idea.

*[upsert_trip; get_year_budget 2027]*

> **Claude:** Done. The warning has gone: 12 days are planned, all 4 carried-over days are used,
> and 16 are left. Here's your updated ledger to save: *(the full JSON)*

## 3. Zürich: local and half-day holidays

*A family in the city of Zürich (`CH-ZH`) with children at primary school.*

> **You:** My parents are visiting the week of 19 April. How much leave is Monday to Friday?

*[calculate_leave_cost 2027-04-19 to 2027-04-23 with the ledger]*

> **Claude:** **4.5 days.** Monday is Sechseläuten, when the city of Zürich has the afternoon
> off. For a family living in Winterthur the same week would cost 5.

> **You:** Is the Friday after Ascension off school?

*[get_holiday_windows CH, 2027, CH-ZH, home "Zürich"]*

> **Claude:** Not for primary schools. The Ascension bridge (7 May) applies only to secondary
> and vocational schools. It hardly matters this year, though: the spring holidays run 24 April
> to 9 May, so Ascension falls inside them anyway.

## 4. Hamburg: what's next, and a brief for a partner

*A family in Hamburg (`DE-HH`) with 30 days of leave and two children: "older" (11, sailing and
football) and "younger" (7, dinosaurs). The plan so far:*
- *a booked week in the Harz mountains over the spring holidays,*
- *a sailing camp in July for the older one,*
- *an idea for two weeks on the Danish coast,*
- *an autumn week at home.*

> **You:** It's 1 February. What needs doing?

*[list_deadlines with today 2027-02-01; check_ledger]*

> **Claude:** Three things, soonest first:
> 1. **13 February:** free cancellation ends for the Harz holiday flat.
> 2. **By 15 March:** book the sailing camp place.
> 3. **Before 17 July:** the Danish coast has nothing booked yet.
>
> No problems in the plan.

> **You:** Make a brief for my partner.

*[summarise_plan with today 2027-02-01]*

> **Claude:** Here's where our 2027 holidays stand:
>
> - **Booked:** Harz mountains, 27 Feb – 6 Mar (5 days' leave).
> - **Decided:** sailing camp for the older one, 5–9 July (no leave for us), and a week at home
>   in the autumn holidays. The camp place needs booking by 15 March.
> - **Idea:** the Danish coast, 17–31 July (10 days).
> - **Still open:** the Pentecost holidays (6–17 May) and Christmas (18 Dec – 2 Jan).
> - **Leave:** 15 of 30 days planned, so 15 are left.
>
> Can you look at the Danish coast this week? If it's a yes, I'll book it.

## Getting started from nothing

> **You:** Set up my family profile.

*[the "Set up my family profile" prompt, starting from resource holiplan://ledger/starter]*

The assistant asks one question at a time, covering:
- country and region,
- home town,
- each child's alias, birth year and interests (never names),
- work pattern and half days,
- leave allowance and carryover,
- the longest drive you'll do in one go.

It then hands you a ledger to save. From then on, share that file at the start of a
conversation and save the updated version whenever the assistant gives you one.
