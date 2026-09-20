"""Operator commands.

A thin shell over the services: it parses arguments, prints identifiers and
counts, and translates a failure into an exit code. No orchestration lives here,
so anything the CLI can do is equally reachable from the API or a worker.
"""
