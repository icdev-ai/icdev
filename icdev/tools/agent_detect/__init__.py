# CUI // SP-CTI
"""ICDEV™ agent detection — declarative rules over the agent event stream (AGOV/DET).

A read-only normalizer over the activity ICDEV already records, a YAML rule pack
under ``args/agent_rules/``, and an append-only findings store. Monitor-only by
default: a rule blocks nothing unless an operator opts it in.
"""
