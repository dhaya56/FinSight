# Corpus

The documents FinSight develops and measures against, governed by
[PROJECT_BLUEPRINT.md](../../PROJECT_BLUEPRINT.md) §32 and §34.

## What is committed, and what is not

| Path | Committed |
|---|---|
| `manifest.toml` | Yes — the record of what was acquired |
| `README.md` | Yes |
| `development/` | **No** |
| `held_out_pdf_core/` | **No** |
| `held_out_format_supplement/` | **No** |

**No source document is ever committed, whatever its licence.** Public
availability is not redistribution permission (§32.8), so the repository holds
the manifest and the checksums while the bytes stay local. The three split
directories are already listed in `.gitignore`; the manifest is what makes the
corpus reproducible, not the files.

## Acquisition is manual, by necessity

The authoritative Indian repositories refuse programmatic access — issuer
investor-relations sites return 403 to automated clients, SEBI's filing list is
rendered client-side, and exchange sites are protected similarly. SEC EDGAR is
the exception and permits automated access with a declared User-Agent at no more
than 10 requests per second.

So a person downloads each document in a browser, then records the URL they used
and the checksum of the bytes they received. There is no downloader in this
repository, and the checksum is the only identity that matters (§32.7).

## Adding a document

1. **Get approval first.** Downloading corpus documents, and choosing dataset
   sources and split assignments, both require it (CLAUDE.md §4).
2. Download from an official issuer, exchange, regulator or offer-document
   repository only (§32.3). Never a mirror, aggregator or third-party copy.
3. Save it into the directory for its split — `development/`,
   `held_out_pdf_core/` or `held_out_format_supplement/`.
4. Print a manifest block:

   ```cmd
   python -m finsight.cli.main corpus checksum data\corpus\<split>\<file>
   ```

5. Paste it into `manifest.toml` and complete the descriptive fields. Fill in
   `expected_challenges` **before** running extraction — recorded beforehand it
   is a prediction, recorded afterwards it is a rationalisation.
6. Assign the split now, not later. No question may be authored against a
   document whose split is undecided (§32.9, §34.9).
7. Validate:

   ```cmd
   python -m finsight.cli.main corpus validate
   python -m finsight.cli.main corpus verify
   ```

## Held-out documents stay unread

Entries in `held_out_pdf_core/` and `held_out_format_supplement/` carry a
`frozen_at` date and are refused by any command that reads their content.
Checksum verification is still allowed: hashing bytes discloses nothing, and
§34.6 forbids tuning against held-out data, not checking that it is intact.

Held-out documents are acquired at the same time as development ones on purpose.
Chosen later, they would be chosen — even unconsciously — to suit what has
already been built, and §34.10's freeze only means something if it precedes
development.

## Splits

| Directory | Blueprint | Purpose |
|---|---|---|
| `development/` | §34.5 | Configuration and threshold selection |
| `held_out_pdf_core/` | §34.6 | Final primary-format performance; never used for tuning |
| `held_out_format_supplement/` | §34.7 | Non-PDF formats, reported separately |

Synthetic and negative fixtures are not corpus entries. They have no
acquisition, no licence and no external identity, and §33.11 requires their
results to be reported separately — so they live as generators in `tests/`
rather than as files here.
