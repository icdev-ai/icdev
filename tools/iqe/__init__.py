"""IQE — ICDEV Query Engine.

Declarative DSL for querying canvas DBs and the awareness graph.
Syntax: foreach <var> in <collection> [where <predicate>]* select <projection>, ...

Example:
    from tools.iqe import parse, execute
    from tools.iqe.adapters.ndc import NDCAdapter
    q = parse('foreach c in network.circuits where c.monthly_cost_usd > 5000 select c.circuit_id, c.carrier')
    rows = execute(q, NDCAdapter())
"""
from tools.iqe.parser import parse
from tools.iqe.interpreter import execute

__all__ = ["parse", "execute"]
