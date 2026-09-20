"""Turning stored document bytes into the citable source representation.

This package owns the producer boundary and the format-specific adapters behind
it. Nothing here writes to the database; persistence belongs to the repository,
and orchestration to the extraction service.
"""
