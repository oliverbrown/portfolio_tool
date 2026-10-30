# Contributing

Thanks for your interest in Portfolio Tool! Bug reports, ideas, new
brokerage parsers, and fixes are all welcome.

## Keep personal data out

This tool handles real brokerage data, so the most important rule is:
**never put real financial information in an issue, pull request, test,
or commit.** That includes account numbers, balances, holdings exports,
and your own `retirement_scenario*.yaml` / `retirement_profile.yaml`.

- Reproduce problems with the synthetic household in
  [`test_data/`](test_data/) (see [`QUICKSTART.md`](QUICKSTART.md)), or
  with a made-up file in the same format.
- If you must share part of a real export to show a parsing problem,
  replace account numbers, names, and amounts with fake values first.
- Your real data belongs in `~/.portfolio_tool/`, outside the repo;
  `.gitignore` also blocks common personal filenames as a safety net.

## Development setup

Requires Python 3.9+.

```bash
git clone https://github.com/oliverbrown/portfolio_tool.git
cd portfolio_tool
python3 -m pip install -r requirements.txt
# Optional: market-data fetching and PNG charts
python3 -m pip install -r requirements-optional.txt
```

The core tool needs only `openpyxl` and `PyYAML`. Please don't add a new
required dependency without discussing it in an issue first.

## Running the tests

```bash
python3 -m unittest discover -s tests -t . -v
```

The suite runs entirely on `test_data/` in throwaway databases and never
touches `~/.portfolio_tool`. See [`tests/README.md`](tests/README.md) for
running a single file or test.

## Making a change

1. For anything bigger than a small fix, open an issue first so we can
   agree on the approach.
2. Create a branch from `main`.
3. Match the style of the surrounding code: plain standard-library Python,
   descriptive names, and comments that explain *why*.
4. Add or update tests in `tests/` for any behavior you change.
5. Update `README.md` (and `QUICKSTART.md` or a package README if it's
   affected) so the docs describe the new behavior.
6. Make sure the full test suite passes.
7. Open a pull request and fill in the template.

## Reporting bugs and requesting features

Use the [issue templates](../../issues/new/choose). For a bug, include the
command you ran, what you expected, what happened, and your Python
version and OS -- with any data anonymized as described above.

## Security issues

Please don't report security problems in a public issue; see
[`SECURITY.md`](SECURITY.md).

## Code of conduct

Everyone taking part is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
