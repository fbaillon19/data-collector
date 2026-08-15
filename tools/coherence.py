#!/usr/bin/env python3
"""tools/coherence.py - la barriere de coherence documentaire.

Copie de smart-led-clock/tools/coherence.py. La version de reference vit
la-bas ; toute modification s'y fait d'abord, puis se reporte ici
(brief 0004 partie B, data-collector).

Brief 0010 partie B du depot smart-led-clock (ADR-0009). Deux regimes :
  - bloquant  : code de retour 1 si un seul controle echoue (B1..B6)
  - avertissement : affiche et compte, ne change jamais le code de retour

Bibliotheque standard uniquement - aucune dependance, aucun pip install.

--depot permet de faire tourner cet outil contre un autre depot : ce qui
differe entre les deux est la configuration ci-dessous, pas le code qui
suit.
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ==========================================
# CONFIGURATION (specifique au depot - c'est cette section qui change
# d'un depot a l'autre, pas le code qui suit)
# ==========================================

DOC_SCAN_DIRS = ["design", "docs"]
ROOT_DOC_GLOB = "*.md"

# B1 : seuls les blocs de code etiquetes ```tree sont verifies - un chemin
# cite en prose pose une question de mode grammatical (present affirme ou
# passe raconte) qu'aucun controle structurel ne peut trancher sans lire le
# sens de la phrase. Un bloc tree n'a qu'un mode : il decrit le present.
#
# Vide ici : aucun bloc ```tree n'existe encore dans ce depot (le seul bloc
# d'arborescence, dans README.md, n'est pas etiquete - donc invisible a B1
# par construction, pas une exception a declarer).
TREE_PATH_EXCEPTIONS: dict[str, str] = {}

# D5 : les archives - un document qu'on s'interdit de corriger ne produit
# jamais un signalement actionnable. design/briefs/ en est deja exclu par la
# non-recursion d'iter_doc_files. Ce depot n'a pas d'equivalent de
# DECISIONS.md (journal chronologique en ajout seul) a la racine.
ARCHIVAL_ROOT_FILES: set[str] = set()

# B5 - valeurs interdites, quelle que soit leur plage : un filtre restreint
# aux plages privees RFC 1918 a trouve zero occurrence sur vingt occurrences
# reelles lors du prototypage (dans smart-led-clock). 127.0.0.1 est la
# boucle locale legitime des tests de data-collector ; 192.168.x.x est le
# placeholder du gabarit de configuration (le "x" litteral ne correspond de
# toute facon jamais au motif d'une IPv4, l'exception documente l'intention).
FORBIDDEN_IP_EXCEPTIONS = {"192.168.x.x", "127.0.0.1"}
ALLOWED_EMAIL_DOMAIN_PREFIX = "example."

# Regime avertissement - seuil retenu pour "document design/ stagnant",
# faute d'un critere plus precis : nombre de commits derriere HEAD au-dela
# duquel un document design/*.md est signale. Purement indicatif - ce
# controle ne bloque jamais.
STALE_DESIGN_DOC_COMMITS = 60

# W3 lit l'index de design/README.md, qui n'existe pas dans ce depot (l'index
# des briefs vit dans le README.md racine). Le controle reste tel quel -
# copier, ne pas reimplementer - et s'auto-desactive faute de fichier a lire,
# au meme titre que B4 (pas de BACKLOG.md) et B6 (pas de REPRISE.md).
BRIEF_CITATION_CONVENTION_SINCE = "2026-08-15"


# ==========================================
# STRUCTURE COMMUNE
# ==========================================

@dataclass
class Finding:
    check: str
    path: Path
    line: int | None
    message: str

    def render(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line is not None else str(self.path)
        return f"[{self.check}] {loc} — {self.message}"


def iter_doc_files(repo: Path):
    """design/ et docs/ eux-mêmes, non récursif : design/adr/, design/briefs/
    et design/gabarits/ sont des archives historiques (un ADR ou un brief
    déjà exécuté décrit un état passé par construction - un brief qui a
    supprimé `datalog.cpp` le cite forcément partout dans son propre texte)
    et des gabarits (chemins littéralement placeholder). Aucun des exemples
    vérifiés du brief 0010 ne vient de ces sous-répertoires."""
    seen: set[Path] = set()
    for d in DOC_SCAN_DIRS:
        base = repo / d
        if base.is_dir():
            for f in sorted(base.glob("*.md")):
                if f not in seen:
                    seen.add(f)
                    yield f
    for f in sorted(repo.glob(ROOT_DOC_GLOB)):
        if f.name in ARCHIVAL_ROOT_FILES:
            continue
        if f not in seen:
            seen.add(f)
            yield f


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


# ==========================================
# B1 - ARBORESCENCE DECLAREE (```tree), UNE ENTREE QUI NE RESOUT PAS
# ==========================================
#
# Remplace l'ancienne version, qui cherchait un chemin de dépôt entre
# accents graves n'importe où en prose. Verdict de l'étalonnage du
# 2026-08-15 : 43 signalements, zéro défaut - "le harnais est dans tools/"
# et "datalog.h a disparu" ont la même forme syntaxique et un mode
# grammatical opposé (affirmation présente / récit du passé), qu'aucun
# outil structurel ne peut trancher sans lire le sens de la phrase. Un
# bloc étiqueté ```tree décrit toujours le présent, sans ambiguïté de
# mode - c'est ce qui le rend vérifiable.

TREE_BLOCK = re.compile(r"```tree\r?\n(.*?)```", re.DOTALL)
TREE_MARKER = re.compile(r"(├──|└──)\s*")


def _parse_tree_block(text: str) -> list[str]:
    """Reconstruit les chemins d'un bloc ```tree. La toute première ligne
    nomme la racine du bloc (ex. "design/" ou "smart-led-clock/") : elle
    n'est pas un chemin à vérifier elle-même, mais tout ce qui suit en
    descend, donc elle amorce la pile."""
    entries: list[str] = []
    stack: list[str] = []
    root_seen = False
    for line in text.splitlines():
        if not line.strip():
            continue
        m = TREE_MARKER.search(line)
        if m is None:
            if not root_seen:
                root_seen = True
                root_m = re.match(r"(\S+)", line.strip())
                if root_m:
                    stack = [root_m.group(1).rstrip("/")]
            continue  # ligne racine deja traitee, ou ligne "│" isolee
        depth = len(line[: m.start()]) // 4
        rest = line[m.end() :]
        name_m = re.match(r"(\S+)", rest)
        if not name_m:
            continue
        name = name_m.group(1)
        if name.startswith("<"):
            continue  # placeholder documentaire ("<one header per src module>")
        stack = stack[: depth + 1] + [name.rstrip("/")]
        entries.append("/".join(stack))
    return entries


def check_b1_tree(repo: Path) -> list[Finding]:
    findings = []
    for md_file in iter_doc_files(repo):
        rel = md_file.relative_to(repo)
        text = md_file.read_text(encoding="utf-8", errors="replace")
        for block_match in TREE_BLOCK.finditer(text):
            block_start_line = text.count("\n", 0, block_match.start()) + 1
            for entry in _parse_tree_block(block_match.group(1)):
                # Une racine de bloc qui nomme le dépôt lui-même ("smart-led-clock/")
                # ne doit pas être ajoutée au chemin : le dépôt courant EST déjà
                # cette racine sur le disque.
                parts = entry.split("/")
                if parts and parts[0] == repo.name:
                    entry = "/".join(parts[1:])
                if not entry:
                    continue
                if entry in TREE_PATH_EXCEPTIONS:
                    continue
                if not (repo / entry).exists():
                    findings.append(
                        Finding("B1", rel, block_start_line, f"arborescence déclarée, entrée introuvable : {entry}")
                    )
    return findings


# ==========================================
# B2 - LIEN OU ANCRE QUI NE RESOUT PAS
# ==========================================

MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
HEADING_LINE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")


def _github_slug(heading_text: str) -> str:
    """Reproduit l'algorithme de github.com pour les ancres de titre
    (table des matieres implicite) : minuscules, ponctuation retiree,
    espaces en tirets. Necessaire pour docs/CONFIGURATION.md et consorts,
    qui n'utilisent pas <a id="..."> mais un sommaire genere par GitHub."""
    text = heading_text.strip().lower()
    text = re.sub(r"[`*_]", "", text)  # emphase/inline code markdown
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text)
    return text


_heading_slug_cache: dict[Path, set[str]] = {}


def _heading_slugs(path: Path) -> set[str]:
    if path not in _heading_slug_cache:
        slugs: set[str] = set()
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                m = HEADING_LINE.match(line)
                if m:
                    slugs.add(_github_slug(m.group(1)))
        except OSError:
            pass
        _heading_slug_cache[path] = slugs
    return _heading_slug_cache[path]


def _anchor_resolves(target_file: Path, anchor: str) -> bool:
    if anchor in _heading_slugs(target_file):
        return True
    try:
        target_text = target_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return f'id="{anchor}"' in target_text


def check_b2_links(repo: Path) -> list[Finding]:
    findings = []
    for md_file in iter_doc_files(repo):
        rel = md_file.relative_to(repo)
        for lineno, line in enumerate(read_lines(md_file), start=1):
            for m in MD_LINK.finditer(line):
                target = m.group(2).strip()
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                path_part, _, anchor = target.partition("#")
                if path_part:
                    target_file = (md_file.parent / path_part).resolve()
                    if not target_file.exists():
                        findings.append(Finding("B2", rel, lineno, f"lien vers un fichier inexistant : {target}"))
                        continue
                else:
                    target_file = md_file
                if anchor and not _anchor_resolves(target_file, anchor):
                    label = path_part or rel.name
                    findings.append(Finding("B2", rel, lineno, f"ancre introuvable : #{anchor} dans {label}"))
    return findings


# ==========================================
# B3 - LE BLOC D'ETAT PERIME
# ==========================================

def check_b3_etat(repo: Path) -> list[Finding]:
    etat_script = repo / "tools" / "etat.py"
    etat_doc = repo / "design" / "ETAT.md"
    if not etat_script.exists() or not etat_doc.exists():
        return []

    import importlib.util

    spec = importlib.util.spec_from_file_location("etat_module", etat_script)
    if spec is None or spec.loader is None:
        return [Finding("B3", Path("tools/etat.py"), None, "impossible de charger le module")]
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fresh = module.generate_report(repo).rstrip("\n")
    current = etat_doc.read_text(encoding="utf-8").rstrip("\n")
    if fresh == current:
        return []

    diff = "\n".join(
        difflib.unified_diff(
            current.splitlines(),
            fresh.splitlines(),
            fromfile="design/ETAT.md (commité)",
            tofile="design/ETAT.md (régénéré)",
            lineterm="",
        )
    )
    return [Finding("B3", Path("design/ETAT.md"), None, "bloc d'état périmé\n" + diff)]


# ==========================================
# B4 - UN IDENTIFIANT PORTANT DEUX STATUTS
# ==========================================

INDEX_ROW = re.compile(r"^\|\s*(BUG-\d+)\s*\|[^|]*\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$")
STORY_TITLE = re.compile(r"^###\s+(US-\d+)\s+—")
MOSCOW_WORDS = ("Won't", "Wont", "Must", "Should", "Could")


def _normalize_priority(text: str) -> tuple[str | None, bool]:
    """Retourne (mot, sans_objet). sans_objet couvre le barre ~~**X**~~, un
    statut ferme (Clos/Sans objet) et une story livree - aucun de ces cas
    n'est une priorite comparable, donc jamais une divergence."""
    t = text.strip()
    if t.startswith("~~"):
        return (None, True)
    if "Sans objet" in t or "✅ Clos" in t or "✅ **Livré**" in t:
        return (None, True)
    stripped = t.lstrip("*").strip()
    for word in MOSCOW_WORDS:
        if stripped.startswith(word):
            return ("Won't" if word == "Wont" else word, False)
    return (None, False)


def check_b4_status(repo: Path) -> list[Finding]:
    backlog_path = repo / "design" / "BACKLOG.md"
    if not backlog_path.exists():
        return []
    rel = backlog_path.relative_to(repo)
    lines = read_lines(backlog_path)

    story_priority: dict[str, str] = {}
    for line in lines:
        m = STORY_TITLE.match(line)
        if not m:
            continue
        parts = line.split(" · ")
        story_priority[m.group(1)] = parts[1] if len(parts) > 1 else ""

    findings = []
    in_index = False
    for lineno, line in enumerate(lines, start=1):
        if line.strip() == "## Index des anomalies":
            in_index = True
            continue
        if in_index and line.startswith("## "):
            break
        if not in_index:
            continue
        m = INDEX_ROW.match(line)
        if not m or m.group(1) == "ID":
            continue
        bug_id, stories_raw, status = m.groups()
        idx_word, idx_moot = _normalize_priority(status)
        if idx_moot or idx_word is None:
            continue
        for story_id in re.findall(r"US-\d+", stories_raw):
            story_text = story_priority.get(story_id)
            if story_text is None:
                continue
            story_word, story_moot = _normalize_priority(story_text)
            if story_moot or story_word is None:
                continue
            if idx_word != story_word:
                findings.append(
                    Finding(
                        "B4",
                        rel,
                        lineno,
                        f"{bug_id}/{story_id} : l'index dit {idx_word}, la fiche {story_id} dit {story_word}",
                    )
                )
    return findings


# ==========================================
# B5 - VALEUR INTERDITE DANS UN FICHIER VERSIONNE
# ==========================================

IPV4_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
GDOCS_RE = re.compile(r"\bdocs\.google\.com/\S*?/d/[\w-]+")
DOTLOCAL_RE = re.compile(r"\b[\w-]+\.local\b")


def check_b5_forbidden(repo: Path) -> list[Finding]:
    # ⚠️ Ne jamais inclure la valeur trouvée dans le message (brief 0010,
    # B5) : fichier, ligne, nature - jamais le contenu.
    findings = []
    for md_file in iter_doc_files(repo):
        rel = md_file.relative_to(repo)
        for lineno, line in enumerate(read_lines(md_file), start=1):
            for m in IPV4_RE.finditer(line):
                if m.group(0) in FORBIDDEN_IP_EXCEPTIONS:
                    continue
                findings.append(Finding("B5", rel, lineno, "adresse IP littérale"))
            for m in EMAIL_RE.finditer(line):
                domain = m.group(0).split("@", 1)[1]
                if domain.startswith(ALLOWED_EMAIL_DOMAIN_PREFIX):
                    continue
                findings.append(Finding("B5", rel, lineno, "adresse électronique"))
            for _ in GDOCS_RE.finditer(line):
                findings.append(Finding("B5", rel, lineno, "identifiant de document Google"))
            for _ in DOTLOCAL_RE.finditer(line):
                findings.append(Finding("B5", rel, lineno, "nom d'hôte .local"))
    return findings


# ==========================================
# B6 - LE POINT DE REPRISE NON RELU
# ==========================================

def check_b6_reprise(repo: Path) -> list[Finding]:
    reprise = repo / "design" / "REPRISE.md"
    if not reprise.exists():
        return []
    rel = reprise.relative_to(repo)

    reprise_date_result = git(repo, "log", "-1", "--format=%ad", "--date=short", "--", str(rel))
    if reprise_date_result.returncode != 0 or not reprise_date_result.stdout.strip():
        return [Finding("B6", rel, None, "aucun commit ne touche design/REPRISE.md")]
    reprise_date = reprise_date_result.stdout.strip()

    work_date_result = git(
        repo, "log", "-1", "--format=%ad", "--date=short", "--", "src", "include", "design"
    )
    if work_date_result.returncode != 0 or not work_date_result.stdout.strip():
        return []
    work_date = work_date_result.stdout.strip()

    if work_date > reprise_date:
        return [
            Finding(
                "B6",
                rel,
                None,
                f"point de reprise non relu — dernier commit sur src/, include/ ou design/ "
                f"le {work_date}, postérieur au dernier commit sur {rel} ({reprise_date})",
            )
        ]
    return []


BLOCKING_CHECKS = [
    ("B1", check_b1_tree),
    ("B2", check_b2_links),
    ("B3", check_b3_etat),
    ("B4", check_b4_status),
    ("B5", check_b5_forbidden),
    ("B6", check_b6_reprise),
]


# ==========================================
# REGIME AVERTISSEMENT (jamais bloquant)
# ==========================================

def check_w2_stale_design_docs(repo: Path) -> list[str]:
    """Non recursif, comme iter_doc_files() : design/adr/, design/briefs/ et
    design/gabarits/ sont des archives (D5) - un brief clos dort par
    construction, un avertissement permanent dessus n'apprend rien qu'à
    ignorer la sortie."""
    design_dir = repo / "design"
    if not design_dir.is_dir():
        return []
    head = git(repo, "rev-list", "--count", "HEAD")
    if head.returncode != 0:
        return []
    head_count = int(head.stdout.strip())
    warnings = []
    for f in sorted(design_dir.glob("*.md")):
        rel = f.relative_to(repo)
        last = git(repo, "log", "-1", "--format=%H", "--", str(rel))
        if last.returncode != 0 or not last.stdout.strip():
            continue
        count = git(repo, "rev-list", "--count", last.stdout.strip() + "..HEAD")
        if count.returncode != 0:
            continue
        behind = int(count.stdout.strip())
        if behind >= STALE_DESIGN_DOC_COMMITS:
            warnings.append(f"{rel} n'a pas été touché depuis {behind} commits (seuil {STALE_DESIGN_DOC_COMMITS})")
    return warnings


BRIEF_EXECUTED_LINE = re.compile(
    r"\[(\d{4})\][^|]*\|[^|]*\|\s*✅ Exécuté\s*—\s*(\d{4}-\d{2}-\d{2})"
)


def check_w3_brief_date_uncited(repo: Path) -> list[str]:
    readme = repo / "design" / "README.md"
    if not readme.exists():
        return []
    warnings = []
    for m in BRIEF_EXECUTED_LINE.finditer(readme.read_text(encoding="utf-8", errors="replace")):
        brief_no, closed_date = m.groups()
        if closed_date < BRIEF_CITATION_CONVENTION_SINCE:
            continue  # clos avant la convention qui impose la citation - D5
        result = git(repo, "log", "--all", "--oneline", f"--grep=brief {brief_no}", "-i")
        if result.returncode != 0 or not result.stdout.strip():
            warnings.append(f"aucun commit ne cite « brief {brief_no} » dans son message")
    return warnings


WARNING_CHECKS = [
    ("W2", check_w2_stale_design_docs),
    ("W3", check_w3_brief_date_uncited),
]


# ==========================================
# PROGRAMME PRINCIPAL
# ==========================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Barrière de cohérence documentaire (ADR-0009)")
    parser.add_argument("--depot", default=".", help="racine du dépôt à vérifier (défaut : .)")
    args = parser.parse_args(argv)
    repo = Path(args.depot).resolve()

    total_blocking = 0
    for name, fn in BLOCKING_CHECKS:
        findings = fn(repo)
        if findings:
            print(f"\n=== {name} ({len(findings)}) ===")
            for f in findings:
                print(f.render())
        total_blocking += len(findings)

    total_warnings = 0
    for name, fn in WARNING_CHECKS:
        messages = fn(repo)
        if messages:
            print(f"\n--- {name}, avertissement ({len(messages)}) ---")
            for msg in messages:
                print(f"[{name}] {msg}")
        total_warnings += len(messages)

    print(f"\n{total_blocking} contrôle(s) bloquant(s) en échec, {total_warnings} avertissement(s).")
    return 1 if total_blocking > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
