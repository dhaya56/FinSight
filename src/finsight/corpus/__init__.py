"""The development corpus and its governance.

PROJECT_BLUEPRINT.md §32 defines what documents FinSight develops against, and
§34 defines how they are partitioned. This package makes those rules executable
rather than aspirational: the manifest is parsed and validated, checksums are
verified, and held-out documents are protected from being read by accident.

Acquisition itself is deliberately not automated. The authoritative Indian
repositories refuse programmatic access, so a person downloads each document and
records the URL used alongside the checksum of the bytes received. What lives
here is the record and the guard, not a downloader.
"""
