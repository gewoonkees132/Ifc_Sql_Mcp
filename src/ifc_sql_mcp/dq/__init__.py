"""Data quality: a rule catalogue, its copy in ``ifc.dq_rule``, and runs that evaluate it.

* ``catalogue.py`` loads ``rules/dq_rules.yaml`` and refuses anything malformed.
* ``sync.py`` makes ``ifc.dq_rule`` match the catalogue.
* ``run.py`` evaluates every rule against one model, as one audited ``dq_run``.

ADR 5 explains the design, and ``docs/data-quality.md`` the rules and their findings.
"""
