#!/usr/bin/env python3
"""tools/etat.py - releve l'etat du depot et ecrit design/ETAT.md.

Adapte de smart-led-clock/tools/etat.py (brief 0004 partie C, ADR-0009) : la
structure — un rapport genere, chaque ligne citant sa source de lecture,
verifie par tools/coherence.py — est la meme ; ce qui est mesure est propre
a ce depot (brief 0004, tableau de la partie C).

Ne prend aucune saisie. Bibliotheque standard uniquement.

Determinisme obligatoire : deux executions consecutives sans modification du
depot doivent produire un fichier identique a l'octet pres. Aucun horodatage
d'execution : le rapport ne change que quand une valeur mesuree change
reellement. Sa fraicheur se lit avec `git log -1 design/ETAT.md`, jamais
inscrite ici.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ETAT_PATH = REPO_ROOT / "design" / "ETAT.md"


# ==========================================
# TESTS (def test_ dans tests/*.py)
# ==========================================

_TEST_DEF = re.compile(r"^\s+def (test_\w+)\(", re.MULTILINE)


def count_tests(root: Path) -> tuple[int, dict[str, int]]:
    per_file: dict[str, int] = {}
    for f in sorted((root / "tests").glob("test_*.py")):
        text = f.read_text(encoding="utf-8")
        per_file[f.name] = len(_TEST_DEF.findall(text))
    return sum(per_file.values()), per_file


# ==========================================
# BRIEFS (index de README.md)
# ==========================================

_BRIEF_ROW = re.compile(r"^\|\s*\[(\d{4})\]\([^)]+\)\s*\|[^|]*\|\s*(.+?)\s*\|\s*$")


def count_briefs_executed(root: Path) -> tuple[int, int]:
    readme = (root / "README.md").read_text(encoding="utf-8")
    total = 0
    executed = 0
    for line in readme.splitlines():
        m = _BRIEF_ROW.match(line)
        if not m:
            continue
        total += 1
        if "✅ Exécuté" in m.group(2):
            executed += 1
    return executed, total


# ==========================================
# MODULES DU COLLECTEUR (collector/*.py)
# ==========================================

def list_collector_modules(root: Path) -> list[str]:
    return sorted(p.name for p in (root / "collector").glob("*.py"))


# ==========================================
# SCENARIOS DU SIMULATEUR (la liste de --scenario)
# ==========================================

def list_simulator_scenarios(root: Path) -> list[str]:
    scenarios_path = root / "simulator" / "scenarios.py"
    spec = importlib.util.spec_from_file_location("scenarios", scenarios_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"impossible de charger {scenarios_path}")
    module = importlib.util.module_from_spec(spec)
    # scenarios.py declares a @dataclass; dataclasses resolves annotations
    # via sys.modules[cls.__module__], so the module must be registered
    # there before exec_module runs, or that lookup finds nothing.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return list(module.NAMES)


# ==========================================
# ASSEMBLAGE DU RAPPORT
# ==========================================

def generate_report(root: Path = REPO_ROOT) -> str:
    total_tests, per_file = count_tests(root)
    briefs_executed, briefs_total = count_briefs_executed(root)
    modules = list_collector_modules(root)
    scenario_names = list_simulator_scenarios(root)

    lines: list[str] = []
    lines.append("# État du dépôt")
    lines.append("")
    lines.append("> Généré par `tools/etat.py` (brief 0004 partie C, ADR-0009).")
    lines.append("> Ne pas éditer à la main : `tools/coherence.py` échoue si ce fichier")
    lines.append("> diverge d'une régénération. Chaque ligne cite sa source de lecture")
    lines.append("> dans le brief - ce fichier ne fait que les relever. Sa fraîcheur")
    lines.append("> se lit avec `git log -1 design/ETAT.md`, jamais inscrite ici.")
    lines.append("")
    lines.append(f"Tests                 : {total_tests} ({len(per_file)} fichiers)")
    for name in sorted(per_file):
        lines.append(f"  {name:<24} : {per_file[name]}")
    lines.append("")
    lines.append(f"Briefs exécutés       : {briefs_executed} / {briefs_total}")
    lines.append("")
    lines.append(f"Modules du collecteur : {len(modules)}")
    for name in modules:
        lines.append(f"  {name}")
    lines.append("")
    lines.append(f"Scénarios du simulateur : {len(scenario_names)}")
    for name in scenario_names:
        lines.append(f"  {name}")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    report = generate_report(REPO_ROOT)
    ETAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ETAT_PATH.write_text(report, encoding="utf-8")
    print(f"écrit : {ETAT_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
