"""A measurement harness for ``check_citations`` (step 1 of the citation test regime).

It measures; it changes no checker behaviour. The pieces:

- :mod:`.facttable` runs every capability on a small synthetic site (neutral names) and turns
  each fact into claim tuples (the oracle);
- :mod:`.realise` writes a true clause for a claim; :mod:`.forms` wraps one or two clauses in a
  sentence form (plain, contrasts, status lists, tables, comments, ...);
- :mod:`.mutate` changes one field of a claim (a swapped unit, a perturbed figure, a flipped
  verdict, ...) and drops a mutant that another true claim makes true;
- :mod:`.run` judges the generated cases (in this interpreter or another venv's) and reports
  E = P(a false sentence kept with ``grounded: true``) and S = P(a true sentence removed) per
  cell form x operator x capability;
- :mod:`.import_uat` turns the acceptance-test repros into regression-corpus entries.
"""
