"""Demo data generation + streaming for L1 SignalFabric.

generator.py — maritime crew world -> raw Slack/Email/ERP events
seed.py      — split at anchor -> data/ (backlog + future runway + entities + meta)
stream.py    — replay the dataset through the real connectors (backlog / live)
email_normalize.py — demo email->SignalEvent stand-in (Gmail connector lands Day 3)
"""
