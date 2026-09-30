# Privacy

holiplan is built to know as little about your family as it can and to keep nothing.

This note covers the server as you install and run it yourself (stdio, for example in Claude
Desktop). There is no hosted holiplan endpoint yet; this note will be updated before one exists.

## What holiplan stores

Nothing. The server has no database and writes no files. Your plan lives in a ledger file that
you keep. Each tool receives the ledger from your assistant, works on it in memory, and hands
the result back. When the server stops, nothing about you remains in it.

## What the ledger contains

Only what you or your assistant put in it. The format is designed to need very little:

- **Children** are identified by an alias you choose ("older", "A"), and optionally a birth year
  and interests. Never a name, photo or school.
- **Where you live** is a region code (such as `DE-BY`) and optionally a home town, used to work
  out which local holidays apply to you.
- **Your plan**: trip dates, destinations, booking status and deadlines, and your leave
  allowance.

## Who sees what

| Recipient | What it receives |
| --- | --- |
| Your AI assistant (e.g. Claude) | The whole ledger, because it passes the ledger to the tools. Its provider's privacy terms apply to your conversations. |
| [OpenHolidays](https://openholidaysapi.org) | Only a country code, a region code, date ranges and a language, to fetch holiday dates. Never your ledger, home town, children or trips. |
| Anyone else | Nothing. holiplan calls no other service. |

Your home town is matched against OpenHolidays' public list of towns on your own machine; the
town itself is never sent anywhere.

## Logs

The server writes logs to its standard error, which your assistant app may save (Claude Desktop
keeps them in its logs folder). They contain:

- the OpenHolidays requests made (country, region, dates), and
- when a tool call fails, the error message, which can name a trip id or quote a value that was
  invalid, such as a malformed date.

They never contain the ledger itself. They stay on your machine.

## Booking and payments

holiplan never books anything and never sees payment details or provider accounts. It only
tracks what you tell it needs booking and by when.

## Holiday data

Holiday data comes from OpenHolidays under CC BY 4.0. It is fetched as needed and cached in
memory for up to a week.
