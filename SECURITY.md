# Security Policy

## Supported versions

Only the latest version on the `main` branch gets fixes.

## Reporting a vulnerability

Please **don't** open a public issue for a security problem.

Report it privately through the repository's
[**Report a vulnerability**](../../security/advisories/new) form (Security
tab → Advisories). Include what you found, how to reproduce it, and what
impact you think it has. Please don't include real financial data.

You can expect an acknowledgement within a week. Once the issue is
confirmed, a fix will be prepared and released, and you'll be credited
unless you'd rather not be.

## Data handling

Portfolio Tool runs entirely on your machine. Your holdings live in a
local SQLite database (by default under `~/.portfolio_tool/`), and the
core tool makes no network calls. The one exception is the optional
`scripts/update_market_data.py`, which fetches public fund data (sector
weights, expense ratios) from Yahoo Finance for the tickers you hold.

Reports that are shared with others should be generated with `--public`
(the default), which leaves out account numbers and other identifying
details.
