# pdf-renderer

Lays out an invoice for print, entirely fictional. The PDF engine takes the text this produces.

- `renderer/layout.py`: the page, as fixed-width text columns. `render(invoice)` is its entry point.
- `renderer/vat_breakdown.py`: net and VAT per rate for the foot of the page. VAT rounds per line, as in the
  exporter, so the breakdown adds up to its totals.

The exporter calls `render(invoice)` for every PDF it sends: keep that signature, and tell the exporter's
owners before it changes. Run the tests with `python3 -m unittest`. Owner: `CODEOWNERS`.
