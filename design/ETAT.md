# État du dépôt

> Généré par `tools/etat.py` (brief 0004 partie C, ADR-0009).
> Ne pas éditer à la main : `tools/coherence.py` échoue si ce fichier
> diverge d'une régénération. Chaque ligne cite sa source de lecture
> dans le brief - ce fichier ne fait que les relever. Sa fraîcheur
> se lit avec `git log -1 design/ETAT.md`, jamais inscrite ici.

Tests                 : 79 (8 fichiers)
  test_archive.py          : 24
  test_config.py           : 5
  test_fetch.py            : 13
  test_main.py             : 8
  test_main_reports.py     : 5
  test_notify.py           : 4
  test_report.py           : 16
  test_state.py            : 4

Briefs exécutés       : 2 / 4

Modules du collecteur : 9
  __init__.py
  __main__.py
  archive.py
  config.py
  fetch.py
  main.py
  notify.py
  report.py
  state.py

Scénarios du simulateur : 12
  nominal
  gap
  dead_sensor
  overwritten
  device_restart
  time_untrusted
  unreachable
  schema_extension
  schema_violation
  pagination
  malformed
  empty
