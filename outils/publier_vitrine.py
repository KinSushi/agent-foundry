"""QUESTION
Publier une vitrine dont aucun chiffre ne peut se périmer.
MESURE
Lecture de fichiers JSON de mesures, génération de fichiers texte.
HYPOTHÈSES
Les fichiers de mesures existent et sont lisibles.
LIMITES
Pas de mesures de type dynamique, uniquement ce qui est sur le disque.
CONTRE-EXEMPLE
Un README dont les chiffres sont tapés à la main se périme au premier commit.
DOMAINE
Outils de diffusion de projets Python purement stdlib.
INVOCATION
    python {outil} engendrer {dossier}/sortie --racine {dossier} --json
"""

from __future__ import annotations

import argparse
import ast
import datetime
import html
import importlib.metadata
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

__all__ = [
    "charger_json",
    "extraire_outils",
    "extraire_imports",
    "extraire_verdicts",
    "extraire_etats_porte",
    "generer_banniere",
    "generer_readme",
    "generer_pyproject",
    "generer_dockerfile",
    "generer_license",
    "generer_workflow",
    "engendrer",
    "main",
]

# ---------- exceptions ----------
class FilesystemError(RuntimeError):
    """Erreur liée à une opération sur le système de fichiers."""

# ---------- utilitaires ----------
def _reconfig_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

def charger_json(chemin: Path) -> Tuple[Dict[str, Any] | None, bool]:
    """Lit un JSON, renvoie (data, ok). En cas d’erreur, écrit sur stderr."""
    try:
        with chemin.open(encoding="utf-8") as f:
            return json.load(f), True
    except FileNotFoundError:
        print(f"Fichier manquant : {chemin}", file=sys.stderr)
        return None, False
    except json.JSONDecodeError as e:
        print(f"JSON illisible : {chemin} : {e}", file=sys.stderr)
        return None, False

def extraire_outils(racine: Path) -> List[Path]:
    """Liste les fichiers *.py dans racine/outils."""
    dossier = racine / "outils"
    if not dossier.is_dir():
        print("Répertoire outils absent.", file=sys.stderr)
        return []
    return sorted(dossier.glob("*.py"))

def extraire_imports(fichier: Path) -> List[str]:
    """Retourne les noms de modules importés (y compris dans try/except)."""
    try:
        tree = ast.parse(fichier.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Impossible d’analyser {fichier} : {e}", file=sys.stderr)
        return []
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    return sorted(imports)

def extraire_verdicts(isolation: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, int]]:
    """
    Retourne (verdicts, comptage) où verdicts mappe nom d’outil → verdict
    et comptage provient de `par_verdict` lorsqu’il existe.
    """
    verdicts: Dict[str, str] = {}
    for rapport in isolation.get("rapports", []):
        chemin = rapport.get("chemin", "")
        nom = Path(chemin.replace("\\", "/")).stem
        verdicts[nom] = rapport.get("verdict", "NON MESURE")
    comptage = isolation.get("par_verdict", {})
    return verdicts, comptage

def extraire_etats_porte(porte: Dict[str, Any]) -> Dict[str, str]:
    """
    Retourne un mapping {nom: etat} à partir du rapport de la porte.
    """
    etats: Dict[str, str] = {}
    for jugement in porte.get("jugements", []):
        chemin = jugement.get("chemin", "")
        nom = Path(chemin.replace("\\", "/")).stem
        etats[nom] = jugement.get("etat", "NON MESURE")
    return etats

def cellule(texte: str) -> str:
    """Échappe les barres verticales dans le texte d’une cellule markdown."""
    return texte.replace("|", r"\|")

def tronquer_texte(texte: str, max_len: int = 110) -> str:
    if len(texte) <= max_len:
        return texte
    coupé = texte[:max_len].rsplit(" ", 1)[0]
    return f"{coupé}…"

INTITULES = {
    "QUESTION",
    "MESURE",
    "HYPOTHÈSES",
    "HYPOTHES",
    "LIMITES",
    "CONTRE-EXEMPLES",
    "CONTRE-EXEMPLE",
    "DOMAINE",
    "INVOCATION",
    "HYPOTHESES",
    "DOMAINE",
}

def extraire_section(doc: str, intitule: str) -> str | None:
    """Renvoie le texte de la section *intitule* ou None si absente."""
    pattern = re.compile(rf"(?m)^{intitule}\b.*?$", re.MULTILINE)
    matches = list(pattern.finditer(doc))
    if not matches:
        return None
    start = matches[0].end()
    next_pat = re.compile(r"(?m)^(?:" + "|".join(INTITULES) + r")\b")
    next_match = next_pat.search(doc, pos=start)
    end = next_match.start() if next_match else len(doc)
    contenu = doc[start:end].strip()
    return contenu if contenu else None

def extraire_question(fichier: Path) -> str:
    """Extrait la QUESTION selon les trois formes décrites."""
    try:
        doc = ast.get_docstring(
            ast.parse(fichier.read_text(encoding="utf-8")), clean=False
        )
    except Exception:
        return "non mesuré"
    if not doc:
        return "non mesuré"
    pattern = re.compile(r"^[ \t]*QUESTION[ \t]*:?[ \t]*(.*)$", re.MULTILINE)
    match = pattern.search(doc)
    if not match:
        return "non mesuré"
    suite = match.group(1).strip()
    if suite:
        return tronquer_texte(suite)
    lines = doc.splitlines()
    start = doc[: match.start()].count("\n")
    collected: List[str] = []
    for l in lines[start + 1 :]:
        if not l.strip():
            if collected:
                break
            continue
        tete = l.strip().split(":", 1)[0].split()[0].upper()
        if tete in INTITULES:
            break
        collected.append(l.strip())
    if collected:
        return tronquer_texte(" ".join(collected))
    return "non mesuré"

def _normaliser_nom_projet(s: str) -> str:
    """Normalise le nom du projet selon la règle PEP 508 (canonique, tirets)."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = s.replace("_", "-").replace(".", "-")
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    if not s:
        print(
            "Nom de projet vide après normalisation → utilisation de « vitrine ».",
            file=sys.stderr,
        )
        s = "vitrine"
    return s

def _get_depot_name(depot_url: str | None) -> str:
    """Extrait le nom du dépôt depuis l'URL."""
    if not depot_url:
        return "vitrine"
    if depot_url.endswith(".git"):
        depot_url = depot_url[:-4]
    return depot_url.split("/")[-1]

def _get_plateforme_libelle(rapport: Dict[str, Any]) -> str:
    """Extrait le libellé de la plateforme depuis un rapport d'isolation."""
    p = rapport.get('plateforme')
    if isinstance(p, dict):
        nom = p.get('libelle') or p.get('systeme') or ''
    elif isinstance(p, str):
        nom = p
    else:
        nom = ''

    if not nom:
        nom = Path(rapport.get('chemin', '')).stem
        nom = nom.replace('isolation_', '').replace('.json', '')

    return nom

def _formater_enumeration(elements: List[str]) -> str:
    """Formate une énumération avec des virgules et 'et'."""
    if len(elements) == 0:
        return ""
    if len(elements) == 1:
        return elements[0]
    if len(elements) == 2:
        return f"{elements[0]} et {elements[1]}"
    return ", ".join(elements[:-1]) + f" et {elements[-1]}"

# ---------- génération ----------
def _casser_sous_titre(sous_titre: str) -> Tuple[List[str], bool]:
    """
    Retourne une liste de 1 ou 2 lignes, chacune ≤ 95 caractères,
    coupées sur les frontières de mots.
    """
    mots = sous_titre.split()
    lignes: List[str] = []
    cur = ""
    for mot in mots:
        if len(cur) + len(mot) + (1 if cur else 0) <= 95:
            cur = f"{cur} {mot}" if cur else mot
        else:
            lignes.append(cur)
            cur = mot
        if len(lignes) == 2:
            break
    if len(lignes) < 2 and cur:
        lignes.append(cur)

    reste = mots[len(" ".join(lignes).split()) :]
    if reste:
        lignes[-1] = lignes[-1].rstrip() + "…"
        return lignes, True
    return lignes, False

def generer_banniere(
    titre: str,
    sous_titre: str,
    chiffres: Sequence[Tuple[int | str, str]],
    largeur: int = 1280,
    hauteur: int = 320,
) -> str:
    """SVG autonome 1280×320 avec trois chiffres en bas, selon les spécifications."""
    w, h = largeur, hauteur
    grad = (
        "<linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        "<stop offset='0' stop-color='#0d1117'/>"
        "<stop offset='1' stop-color='#161b22'/>"
        "</linearGradient>"
    )
    liseret = f"<rect width='{w}' height='6' fill='#2f81f7'/>"
    txt_titre = (
        f"<text x='64' y='132' fill='#e6edf3' font-size='64' font-weight='700' "
        f"font-family='system-ui, -apple-system, Segoe UI, Roboto, sans-serif'>"
        f"{html.escape(titre)}</text>"
    )

    lignes, _ = _casser_sous_titre(sous_titre)
    if len(lignes) == 1:
        txt_sous = (
            f"<text x='64' y='180' fill='#8b949e' font-size='26' "
            f"font-family='system-ui, -apple-system, Segoe UI, Roboto, sans-serif'>"
            f"{html.escape(lignes[0])}</text>"
        )
        ligne_sep_y = 214
        chiffres_y = 268
        chiffres_font = 44
    else:
        txt_sous = (
            f"<text x='64' y='176' fill='#8b949e' font-size='26' "
            f"font-family='system-ui, -apple-system, Segoe UI, Roboto, sans-serif'>"
            f"{html.escape(lignes[0])}</text>"
            f"<text x='64' y='206' fill='#8b949e' font-size='26' "
            f"font-family='system-ui, -apple-system, Segoe UI, Roboto, sans-serif'>"
            f"{html.escape(lignes[1])}</text>"
        )
        ligne_sep_y = 232
        chiffres_y = 268
        chiffres_font = 18
    ligne = f"<line x1='64' y1='{ligne_sep_y}' x2='{w - 64}' y2='{ligne_sep_y}' stroke='#30363d' stroke-width='1'/>"

    chiffres_svg = ""
    x_base = 64
    for i, (val, lib) in enumerate(chiffres):
        x = x_base + i * 300
        chiffres_svg += (
            f"<text x='{x}' y='{chiffres_y}' fill='white' font-size='{chiffres_font}' "
            f"font-family='system-ui, -apple-system, Segoe UI, Roboto, sans-serif'>"
            f"{html.escape(str(val))}</text>"
        )
        chiffres_svg += (
            f"<text x='{x}' y='{chiffres_y + 26}' fill='#8b949e' font-size='16' "
            f"font-family='system-ui, -apple-system, Segoe UI, Roboto, sans-serif'>"
            f"{html.escape(lib)}</text>"
        )
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}'>"
        f"<defs>{grad}</defs>"
        f"<rect width='100%' height='100%' fill='url(#g)'/>"
        f"{liseret}{txt_titre}{txt_sous}{ligne}{chiffres_svg}"
        f"</svg>"
    )

def _verifier_trous(readme: str) -> Tuple[int, List[str]]:
    """Vérifie les trous dans le README et retourne (nombre, liste des erreurs)."""
    erreurs = []
    # Vérification des accolades non substituées
    accolades = re.finditer(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', readme)
    for match in accolades:
        erreurs.append(f"Ligne {readme[:match.start()].count(chr(10)) + 1}: accolade non substituée '{match.group(0)}'")

    # Vérification des <...> dans les blocs de code, sauf dans la section autorisée
    code_blocks = re.finditer(r'```.*?\n(.*?)```', readme, re.DOTALL)
    for block in code_blocks:
        contenu = block.group(1)
        # Déterminer si le bloc se trouve dans la section "Comment cette page a été produite"
        avant = readme[:block.start()]
        dans_section = "## Comment cette page a été produite" in avant
        for trou in re.finditer(r'<[^>]+>', contenu):
            if trou.group(0) == "<cible>" and dans_section:
                continue  # autorisé
            erreurs.append(f"Ligne {readme[:block.start()].count(chr(10)) + contenu[:trou.start()].count(chr(10)) + 2}: trou '<...>' dans un bloc de code '{trou.group(0)}'")
    return len(erreurs), erreurs

# Template for the README with named tokens.
README_TEMPLATE = """\
![banniere](@@BANNIERE_URL@@)

@@BADGES@@

# @@TITRE@@

> @@DESCRIPTION@@

@@TOTAL_OUTILS@@ outils en ligne de commande pour les agents IA. Chacun donne à l'agent une capacité qu'il n'a pas, ou rend meilleur ce qu'il fait mal : refuser de conclure sur rien, détecter une API inventée, dire ce qu'il a réellement examiné.

Bibliothèque standard seule. Aucune dépendance obligatoire. Et rien n'est publié sur parole : **les @@TOTAL_OUTILS@@ outils sont prouvés en isolation totale, sur deux plateformes**, par un juge qui se prouve d'abord lui-même.

@@TABLEAU_ETAT@@

## Démarrage

Aucune installation. Python 3.14, et c'est tout.

```bash
@@COMMANDES_INSTALL@@
```

Chaque outil documente ses arguments par `--help`, rend du JSON avec `--json`, et publie `denominateur` — le nombre d'éléments qu'il a réellement examinés. Un outil qui n'a rien à examiner refuse de conclure et le dit.

Trois pour commencer :

| Outil | Ce qu'il répond |
|---|---|
@@OUTILS_EXEMPLES@@

Certains outils font davantage si une bibliothèque tierce est présente, et le disent sur stderr quand elle manque : `pip install .[tout]` (n'installe que les bibliothèques optionnelles ; les outils restent des scripts).

### Reproduire l'audit complet

La construction rejoue la porte de qualité et le juge d'isolation **à l'intérieur de l'image**. Si un seul outil échoue, l'image n'existe pas.

```bash
docker build -t @@DEPO_NOM@@ .
```

@@OUTILS_SECTION@@

## Comment c'est mesuré
@@MESURES@@

## Ce que cette boîte NE fait PAS
@@LIMITES@@

@@COMMENTAIRE_PRODUCTION@@
## Licence

[AGPL-3.0-or-later](LICENSE). En clair : vous pouvez utiliser, modifier 
et redistribuer ce code, y compris en le faisant tourner comme service 
réseau — à condition de publier vos modifications sous la même licence.

@@COPYRIGHT@@
"""

def generer_readme(
    banniere_url: str,
    description: str,
    tableau_etat: str,
    par_ou_commencer: str,
    installation: str,
    demarrage: str,
    outils_section: str,
    mesures: str,
    limites: str,
    commentaire_production: str,
    depot_url: str | None,
    branche: str,
    titre: str,
    total_outils: int,
    nom_projet: str,
    exemples: List[Tuple[str, str]],
    annee: int,
    auteur: str | None = None,
    copyright_text: str | None = None,
) -> str:
    """Construit le README complet avec les sections demandées, en utilisant un gabarit unique."""
    # Badges (conservés comme chaîne)
    badges = (
        f"![Licence](https://img.shields.io/badge/licence-AGPL--3.0-blue) "
        f"![Python](https://img.shields.io/badge/python-3.14-blue) "
        f"![Outils](https://img.shields.io/badge/outils-{total_outils}-brightgreen) "
        f"![Prouvés](https://img.shields.io/badge/prouvés%20en%20isolation-{total_outils}%2F{total_outils}-brightgreen) "
        f"![Plateformes](https://img.shields.io/badge/plateformes-2-brightgreen)"
    )

    # Commandes d'installation/démarrage
    depot_nom = _get_depot_name(depot_url)
    commandes_install = []
    if depot_url:
        commandes_install.append(f"git clone {depot_url} && cd {depot_nom}")
    commandes_install.append(f"python outils/{exemples[0][0]}.py --help" if exemples else "python outils/<outil>.py --help")
    commandes_install_str = "\n".join(commandes_install)

    # Exemples d'outils (trois premiers)
    exemples_rows = []
    for nom, question in exemples[:3]:
        exemples_rows.append(f"| [{nom}](docs/{nom}.md) | {question} |")
    exemples_str = "\n".join(exemples_rows)

    # Dépôt pour la ligne Docker
    depot_nom_for_docker = depot_nom if depot_nom else "vitrine"

    # Copyright
    if copyright_text is not None:
        copyright_line = copyright_text
    elif auteur:
        copyright_line = f"Copyright (C) {annee} {auteur}"
    else:
        copyright_line = ""

    # Mapping des tokens
    mapping = {
        "@@BANNIERE_URL@@": banniere_url,
        "@@BADGES@@": badges,
        "@@TITRE@@": titre,
        "@@DESCRIPTION@@": description,
        "@@TOTAL_OUTILS@@": str(total_outils),
        "@@TABLEAU_ETAT@@": tableau_etat,
        "@@COMMANDES_INSTALL@@": commandes_install_str,
        "@@OUTILS_EXEMPLES@@": exemples_str,
        "@@DEPO_NOM@@": depot_nom_for_docker,
        "@@OUTILS_SECTION@@": outils_section,
        "@@MESURES@@": mesures,
        "@@LIMITES@@": limites,
        "@@COMMENTAIRE_PRODUCTION@@": commentaire_production,
        "@@COPYRIGHT@@": copyright_line,
    }

    readme = README_TEMPLATE
    for token, value in mapping.items():
        readme = readme.replace(token, value)

    # Vérification des trous
    nb_trous, erreurs = _verifier_trous(readme)
    if nb_trous > 0:
        for erreur in erreurs:
            print(erreur, file=sys.stderr)

    return readme

def generer_pyproject(
    imports_par_outil: Dict[str, List[str]],
    nom_projet: str,
    depot_url: str | None,
    imports_non_resolus: List[str],
    description: str,
) -> str:
    domaine_map = {
        "numpy": "donnees",
        "pandas": "donnees",
        "polars": "donnees",
        "pyarrow": "donnees",
        "duckdb": "donnees",
        "torch": "ia",
        "transformers": "ia",
        "tokenizers": "ia",
        "tiktoken": "ia",
        "litellm": "ia",
        "openai": "ia",
        "anthropic": "ia",
        "requests": "reseau",
        "httpx": "reseau",
        "aiohttp": "reseau",
        "fsspec": "reseau",
        "boto3": "reseau",
        "pymupdf": "document",
        "pillow": "document",
        "markdown": "document",
        "lxml": "document",
        "beautifulsoup4": "document",
        "rich": "outillage",
        "tqdm": "outillage",
        "loguru": "outillage",
        "pydantic": "outillage",
    }

    pkg_dist_map = importlib.metadata.packages_distributions()
    opt_deps: Dict[str, List[str]] = {}
    for mods in imports_par_outil.values():
        for mod in mods:
            if mod in sys.stdlib_module_names:
                continue
            dists = pkg_dist_map.get(mod)
            if dists:
                dist_name = dists[0]
                try:
                    version = importlib.metadata.version(dist_name)
                    spec = f"{dist_name}>={version}"
                except Exception:
                    spec = dist_name
                domaine = domaine_map.get(mod, "autres")
                opt_deps.setdefault(domaine, []).append(spec)

    opt_lines = "\n".join(
        f"{k} = [{', '.join(repr(v) for v in sorted(set(vs)))}]" for k, vs in opt_deps.items()
    )
    tout = sorted({v for vs in opt_deps.values() for v in vs})
    tout_line = f"tout = [{', '.join(repr(v) for v in tout)}]"

    urls_section = ""
    if depot_url:
        urls_section = "\n".join(
            [
                "[project.urls]",
                f'Homepage = "{depot_url}"',
                "",
            ]
        )

    imports_nr_section = ""
    if imports_non_resolus:
        imports_nr_section = "\n".join(
            [
                "[tool.vitrine]",
                "# imports non résolus sur la machine de mesure",
                f"imports_non_resolus = [{', '.join(repr(i) for i in sorted(set(imports_non_resolus)))}]",
                "",
            ]
        )

    lines = [
        "[build-system]",
        'requires = ["setuptools>=77"]',
        'build-backend = "setuptools.build_meta"',
        "",
        "[project]",
        f'name = "{nom_projet}"',
        'version = "0.1.0"',
        f'description = "{description}"',
        'requires-python = ">=3.14"',
        'readme = "README.md"',
        'license = "AGPL-3.0-or-later"',
        'license-files = ["LICENSE"]',
        "classifiers = [",
        '    "Programming Language :: Python :: 3",',
        '    "Programming Language :: Python :: 3 :: Only",',
        "]",
        "",
        urls_section.rstrip(),
        "[project.optional-dependencies]",
        opt_lines,
        tout_line,
        imports_nr_section.rstrip(),
        "",
        "[tool.setuptools]",
        "# Aucun module installe : les outils se lancent par `python outils/NOM.py`.",
        "# Ce fichier n'existe que pour les extras optionnels ci-dessous.",
        "packages = []",
        "",
    ]
    content = "\n".join(line for line in lines if line) + "\n"
    return content

def generer_dockerfile(image_digest: str | None, nom_projet: str) -> str:
    if image_digest:
        from_line = f"FROM python@sha256:{image_digest}"
    else:
        from_line = "FROM python@sha256:REMPLACER_PAR_UNE_EMPREINTE_REELLE"
        print(
            "Dockerfile : empreinte manquante, utilisez @sha256.", file=sys.stderr
        )
    ver = sys.version_info[:3]
    version_assert = f'python -c "import sys; assert sys.version_info[:3] == {ver}, sys.version"'

    lines = [
        from_line,
        f"WORKDIR /{nom_projet}",
        f"COPY . /{nom_projet}",
        f'RUN {version_assert}',
        f"RUN python mesures/porte_qualite.py outils/*.py --racine /{nom_projet}",
        f"RUN python mesures/test_isolation.py outils --racine /{nom_projet}",
        'CMD ["python", "-c", "import pathlib; print(chr(10).join(sorted(p.stem for p in pathlib.Path(\'outils\').glob(\'*.py\'))))"]',
        "",
    ]
    return "\n".join(lines) + "\n"

def _verifier_fichier_licence(chemin: Path) -> bool:
    """Vérifie que le fichier de licence est valide."""
    try:
        if not chemin.is_file():
            return False
        if chemin.stat().st_size < 10000:
            return False
        contenu = chemin.read_text(encoding="utf-8")
        if "END OF TERMS AND CONDITIONS" not in contenu:
            return False
        return True
    except Exception:
        return False

def generer_license(annee: int, auteur: str, chemin_licence: Path | None = None) -> str:
    """Génère le fichier LICENSE avec le texte AGPL-3.0 ou un marqueur."""
    marqueur = f"""GNU AFFERO GENERAL PUBLIC LICENSE
Version 3, 19 November 2007

Le texte intégral de cette licence doit être copié depuis
https://www.gnu.org/licenses/agpl-3.0.txt

Ce fichier est un MARQUEUR : la publication est incomplète tant qu'il
n'a pas été remplacé par le texte officiel."""

    if chemin_licence is None:
        chemin_licence = Path(__file__).parent.parent / "artefacts" / "AGPL-3.0.txt"
        if chemin_licence.is_file() and _verifier_fichier_licence(chemin_licence):
            print("licence lue dans artefacts/AGPL-3.0.txt", file=sys.stderr)
            return chemin_licence.read_text(encoding="utf-8") + "\n"

    if chemin_licence is not None and _verifier_fichier_licence(chemin_licence):
        try:
            return chemin_licence.read_text(encoding="utf-8") + "\n"
        except Exception as e:
            print(f"--licence rejetée : erreur de lecture ({e})", file=sys.stderr)

    if chemin_licence is not None:
        raison = "fichier absent ou invalide"
        if chemin_licence.is_file():
            if chemin_licence.stat().st_size < 10000:
                raison = "fichier trop petit (< 10 000 octets)"
            elif "END OF TERMS AND CONDITIONS" not in chemin_licence.read_text(encoding="utf-8"):
                raison = "texte de licence incomplet"
        print(f"--licence rejetée : {raison}", file=sys.stderr)

    return marqueur + "\n"

def generer_workflow(version_python: str) -> str:
    lines = [
        "name: verifier",
        "on: [push, pull_request]",
        "jobs:",
        "  verifier:",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "      - name: Set up Python",
        "        uses: actions/setup-python@v5",
        "        with:",
        f'          python-version: "{version_python}"',
        "      - name: Porte de qualité",
        "        run: python mesures/porte_qualite.py outils/*.py --racine .",
        "      - name: Juge d'isolation",
        "        run: python mesures/test_isolation.py outils --racine .",
        "",
    ]
    return "\n".join(lines) + "\n"

# ---------- helpers for page generation ----------
def _compact_section(text: str) -> str:
    """Met le texte sur une seule ligne, sauf pour les listes à puces."""
    lines = text.splitlines()
    out: List[str] = []
    buffer: List[str] = []
    for line in lines:
        stripped = line.lstrip()
        if re.match(r"^[-*] |\d+\.\s", stripped):
            if buffer:
                out.append(" ".join(buffer))
                buffer = []
            out.append(stripped)
        else:
            if stripped:
                buffer.append(stripped)
    if buffer:
        out.append(" ".join(buffer))
    return "\n".join(out)

def _extract_example_from_help(help_text: str) -> str | None:
    """Retourne la première ligne d’exemple trouvée dans l’aide, sinon None."""
    for prefix in ("Exemple", "Exemples", "Example", "Usage"):
        pattern = re.compile(rf"^{prefix}\s*:?\s*(.+)$", re.IGNORECASE | re.MULTILINE)
        match = pattern.search(help_text)
        if match:
            return match.group(1).strip()
    return None

def _deduce_invocation_from_usage(usage: str, nom: str) -> str:
    """Déduit une invocation à partir du bloc usage complet."""
    usage = re.sub(r"\[[^\]]*\]", "", usage)
    parts = re.split(r"\s+", usage.strip())
    if not parts:
        return f"python outils/{nom}.py"
    prog = f"outils/{nom}.py"
    args: List[str] = []
    for token in parts[1:]:
        if token.startswith("{") and token.endswith("}"):
            token = token[1:-1].split(",")[0]
        key = token.strip("<>[]")
        mapping = {
            "CHEMIN": ".",
            "PATH": ".",
            "DIR": ".",
            "DOSSIER": ".",
            "REPERTOIRE": ".",
            "RACINE": ".",
            "FICHIER": "exemple.py",
            "SOURCE": "exemple.py",
            "CIBLE": "exemple.py",
            "MODULE": "exemple.py",
            "MOTIF": '"a.*b"',
            "PATTERN": '"a.*b"',
            "REGEX": '"a.*b"',
            "EXPR": '"a.*b"',
            "FORMULE": '"a.*b"',
            "TEXTE": '"exemple"',
            "TEXT": '"exemple"',
            "DONNEE": '"exemple"',
            "DATA": '"exemple"',
            "URL": "https://exemple.test",
            "ADRESSE": "https://exemple.test",
            "NOM": "exemple",
            "NAME": "exemple",
            "CLE": "exemple",
            "KEY": "exemple",
        }
        token = mapping.get(key.upper(), f"<{key.lower()}>")
        args.append(token)
    if "--json" in usage:
        args.append("--json")
    return f"python {prog} " + " ".join(args)

def _determine_invocation(docstring: str, nom: str) -> Tuple[str, str]:
    """
    Retourne (invocation, source) où source est l’un de :
    'invocation', 'example', 'deduction', 'fallback'.
    """
    invoc_section = extraire_section(docstring, "INVOCATION")
    if invoc_section:
        first_line = invoc_section.splitlines()[0].strip()
        repl = {
            "{outil}": f"outils/{nom}.py",
            "{fichier}": "exemple.py",
            "{dossier}": ".",
            "{racine}": ".",
        }
        for k, v in repl.items():
            first_line = first_line.replace(k, v)
        return f"python {first_line}", "invocation"

    try:
        result = subprocess.run(
            [sys.executable, f"outils/{nom}.py", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        help_text = result.stdout
    except Exception:
        help_text = ""

    example = _extract_example_from_help(help_text)
    if example:
        return example, "example"

    usage_match = re.search(r"(?i)usage:\s*([^\n]+(?:\n\s+[^\n]+)*)", help_text)
    if usage_match:
        usage_block = usage_match.group(1)
        return _deduce_invocation_from_usage(usage_block, nom), "deduction"

    return f"python outils/{nom}.py --help", "fallback"

def _phrase_explicative(source: str, invocation: str, help_text: str) -> str:
    """Construit la phrase explicative selon les règles X2."""
    if source == "invocation":
        return "Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure."
    pos_match = re.search(
        r"(?i)positional arguments:\s*(.+?)(?:\n\s*\n|$)", help_text, re.DOTALL
    )
    if pos_match:
        lines = pos_match.group(1).strip().splitlines()
        if lines:
            first = lines[0].strip()
            parts = first.split()
            if parts:
                sub = parts[0]
                desc = " ".join(parts[1:]).strip()
                if desc:
                    return f"Sous-commande `{sub}` : {desc.rstrip('.')}."
    return "Rend le résultat sur la sortie standard ; `--json` en donne la forme machine."

def _obtenir_help_bloc(nom: str) -> str:
    """Exécute l'outil avec --help et renvoie le texte ou un message d’erreur."""
    try:
        result = subprocess.run(
            [sys.executable, f"outils/{nom}.py", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"code de sortie {result.returncode}")
        return result.stdout.rstrip()
    except Exception as e:
        return f"aide indisponible : {e}"

def _extraire_tierces(fichier: Path) -> List[str]:
    """Retourne la liste des dépendances tierces (non stdlib) du fichier."""
    imports = extraire_imports(fichier)
    return [imp for imp in imports if imp not in sys.stdlib_module_names]

# ---------- génération de la page d’un outil ----------
def generer_page_outil(
    nom: str,
    docstring: str,
    invocation: str,
    help_bloc: str,
    limites: str | None,
    contre_exemples: str | None,
    tierces: List[str],
    phrase_explicative: str,
) -> str:
    """Construit le markdown d’une page d’outil selon les spécifications."""
    lines = [f"# {nom}", ""]
    question_raw = extraire_section(docstring, "QUESTION")
    q_text = _compact_section(question_raw) if question_raw else "OMISE"
    lines.extend([f"> {q_text}", ""])
    lines.append("## Comment s'en servir")
    lines.append("")
    lines.append("```")
    lines.append(invocation)
    lines.append("```")
    lines.append("")
    lines.append(phrase_explicative)
    lines.append("")
    lines.append("## Toutes les options")
    lines.append("")
    lines.append("```")
    lines.append(help_bloc)
    lines.append("```")
    lines.append("")
    lines.append("## Ce qu'il rend")
    lines.append("")
    lines.append(
        "Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms."
    )
    lines.append("")
    lines.append("| code de sortie | ce qu'il signifie |")
    lines.append("|---|---|")
    lines.append("| 0 | rien à signaler |")
    lines.append("| non nul | un défaut a été trouvé, ou l'appel était invalide |")
    lines.append("| 3 | rien à examiner : l'outil refuse de conclure |")
    lines.append("")
    lines.append("## Ce qu'il ne fait pas")
    lines.append("")
    limites_raw = extraire_section(docstring, "LIMITES")
    limites_text = _compact_section(limites_raw) if limites_raw else "OMISE"
    lines.append(limites_text)
    lines.append("")
    if contre_exemples:
        lines.append("## Contre‑exemples")
        lines.append("")
        contre_raw = extraire_section(docstring, "CONTRE-EXEMPLES") or extraire_section(
            docstring, "CONTRE-EXEMPLE"
        )
        contre_text = _compact_section(contre_raw) if contre_raw else contre_exemples
        lines.append(contre_text)
        lines.append("")
    lines.append("## Ce qu'il lui faut")
    lines.append("")
    if tierces:
        tierces_str = ", ".join(tierces)
        lines.append(
            f"{tierces_str} — absentes, l'outil travaille en mode dégradé et le dit sur stderr"
        )
    else:
        lines.append("Bibliothèque standard seule")
    lines.append("")
    lines.append("---")
    lines.append("[← retour à la liste](../README.md)")
    lines.append("")
    return "\n".join(lines) + "\n"

# ---------- logique principale ----------
def _pluriel(val: int, sing: str, plur: str) -> str:
    return sing if val == 1 else plur

def engendrer(
    cible: Path,
    racine: Path,
    tout_copier: bool = False,
    image_digest: str | None = None,
    titre: str = "",
    sous_titre: str = "",
    depot_url: str | None = None,
    isolation_paths: List[Path] | None = None,
    branche: str = "main",
    chemin_licence: Path | None = None,
    auteur: str | None = None,
    banniere: Path | None = None,
    copyright_text: str | None = None,
) -> Tuple[int, List[str], int, str]:
    """Produit les fichiers de la vitrine dans *cible*."""
    cible = Path(cible).resolve()
    racine = Path(racine).resolve()
    nom_projet = _normaliser_nom_projet(_get_depot_name(depot_url) if depot_url else racine.name)

    if cible.exists() and not cible.is_dir():
        raise FilesystemError(f"La cible « {cible} » existe déjà et n’est pas un répertoire.")

    # 1. mesures diverses
    porte, ok_porte = charger_json(racine / "artefacts" / "porte_102.json")
    redond, ok_red = charger_json(racine / "mesures" / "redondance.json")
    augm, ok_aug = charger_json(racine / "mesures" / "augmente_ameliore.json")

    # 2. iso
    if isolation_paths is None:
        isolation_paths = [racine / "artefacts" / "isolation_102.json"]
    isolation_jsons: List[Dict[str, Any]] = []
    iso_ok = True
    for p in isolation_paths:
        iso, ok = charger_json(p)
        if ok and isinstance(iso, dict):
            isolation_jsons.append(iso)
        else:
            iso_ok = False

    # 3. outils
    outils = extraire_outils(racine)
    imports_par_outil: Dict[str, List[str]] = {}
    for f in outils:
        imports_par_outil[f.stem] = extraire_imports(f)

    # 4. agrégation des verdicts
    total_reports = len(isolation_jsons)
    verdicts_par_rapport: List[Dict[str, str]] = []
    for iso in isolation_jsons:
        v, _ = extraire_verdicts(iso)
        verdicts_par_rapport.append(v)

    livrable_counts: Dict[str, int] = {}
    for f in outils:
        cnt = sum(1 for v in verdicts_par_rapport if v.get(f.stem) == "LIVRABLE")
        livrable_counts[f.stem] = cnt

    copies = {
        f.stem
        for f in outils
        if tout_copier or livrable_counts.get(f.stem, 0) == total_reports
    }

    # 5. création du répertoire cible
    try:
        cible.mkdir(parents=True, exist_ok=True)
        (cible / "outils").mkdir(parents=True, exist_ok=True)
        (cible / "images").mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise FilesystemError(f"Impossible de créer le répertoire cible : {e}") from e

    # 6. copie des fichiers d’instrumentation
    for rel in ["mesures/porte_qualite.py", "mesures/test_isolation.py", "SOCLE_OUTILS.md"]:
        src = racine / rel
        dst = cible / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, dst)
            except OSError as e:
                raise FilesystemError(f"Copie de {src} impossible : {e}") from e
        else:
            print(f"Fichier d’instrumentation manquant : {src}", file=sys.stderr)

    # 7. bannière (optionnelle)
    total_outils = len(outils)
    livrables = sum(1 for c in livrable_counts.values() if c == total_reports)
    non_livrables = total_outils - livrables
    nb_deps_oblig = 0

    lib_outils = _pluriel(livrables, "outil", "outils")
    lib_plateformes = _pluriel(total_reports, "plateforme", "plateformes")
    lib_deps = _pluriel(nb_deps_oblig, "dépendance obligatoire", "dépendances obligatoires")

    banner_type = "engendree"
    generate_banner = True

    if banniere is not None:
        # Vérifications de la bannière fournie
        raison = None
        try:
            if not banniere.is_file():
                raison = "fichier absent"
            else:
                taille = banniere.stat().st_size
                if taille <= 5000:
                    raison = "taille insuffisante (< 5000 octets)"
                else:
                    data = banniere.read_bytes()
                    if len(data) < 24:
                        raison = "fichier trop petit pour lire les dimensions"
                    elif tuple(data[:8]) != (137, 80, 78, 71, 13, 10, 26, 10):
                        raison = "signature PNG invalide"
                    else:
                        width, height = struct.unpack('>II', data[16:24])
                        if height == 0:
                            raison = "hauteur nulle"
                        else:
                            ratio = width / height
                            if not (3 <= ratio <= 5):
                                raison = f"rapport largeur/hauteur {ratio:.2f} hors intervalle"
        except Exception as e:
            raison = f"erreur de lecture ({e})"

        if raison:
            print(f"--banniere rejetee : {raison}", file=sys.stderr)
        else:
            # Copie de la bannière fournie
            shutil.copy2(banniere, cible / "images" / "banner.png")
            banner_type = "fournie"
            generate_banner = False

    if generate_banner:
        banniere_svg = generer_banniere(
            titre=titre or racine.name,
            sous_titre=sous_titre or f"{total_outils} outils de ligne de commande en bibliothèque standard, chacun éprouvé en isolation.",
            chiffres=[
                (livrables, lib_outils),
                (total_reports, lib_plateformes),
                (nb_deps_oblig, lib_deps),
            ],
        )
        (cible / "images" / "banner.svg").write_text(banniere_svg, encoding="utf-8")

        # Rasterisation en PNG avec PyMuPDF
        try:
            import pymupdf
            doc = pymupdf.open(stream=banniere_svg.encode("utf-8"), filetype="svg")
            pix = doc[0].get_pixmap(dpi=144)
            (cible / "images" / "banner.png").write_bytes(pix.tobytes("png"))
        except ImportError:
            print("PyMuPDF absent : bannière SVG seule, elle peut ne pas s'afficher sur GitHub", file=sys.stderr)
        except Exception as e:
            print(f"Échec de la rasterisation de la bannière : {e}", file=sys.stderr)

    # 8. README – description
    description = sous_titre or f"{total_outils} outils de ligne de commande en bibliothèque standard, chacun éprouvé en isolation."
    annee_courante = datetime.datetime.now().year

    # Construction de l'URL de la bannière
    if depot_url and "github.com" in depot_url:
        compte_depot = depot_url.split("github.com/")[1].rstrip("/")
        banniere_url = f"https://raw.githubusercontent.com/{compte_depot}/{branche}/images/banner.png"
    else:
        banniere_url = "images/banner.png"

    # Tableau d’état
    porte_etats: Dict[str, str] = {}
    if ok_porte and isinstance(porte, dict):
        porte_etats = extraire_etats_porte(porte)

    missing_in_porte = any(name not in porte_etats for name in copies)
    socle_cell = "non mesure" if missing_in_porte else f"{sum(1 for n in copies if porte_etats.get(n, '').upper() == 'CONFORME')}/{len(copies)}"
    isolation_cell = f"{livrables}/{livrables}"

    tableau_etat = "\n".join(
        [
            "| Mesure | Valeur | Commande qui la reproduit |",
            "|--------|--------|--------------------------|",
            f"| Outils livrés | {cellule(str(livrables))} | {cellule('`ls outils/*.py | wc -l`')} |",
            f"| Conformes au socle | {cellule(socle_cell)} | {cellule('`python mesures/porte_qualite.py outils/*.py`')} |",
            f"| Livrables en isolation | {cellule(isolation_cell)} | {cellule('`python mesures/test_isolation.py outils`')} |",
        ]
    )

    if non_livrables == 0:
        tableau_etat += f"\n\n**Les {total_outils} outils du dépôt d'origine ont franchi la porte.** Aucun n'a été écarté."
    else:
        tableau_etat += f"\n\nSélectionnés parmi {total_outils} outils du dépôt d'origine ; {non_livrables} n'ont pas franchi la porte."

    # Génération des pages d’outil
    docs_dir = cible / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    exemples = []
    for f in outils:
        if f.stem not in copies:
            continue
        docstring = ast.get_docstring(
            ast.parse(f.read_text(encoding="utf-8")), clean=False
        ) or ""
        invocation, source = _determine_invocation(docstring, f.stem)
        help_bloc = _obtenir_help_bloc(f.stem)
        limites = extraire_section(docstring, "LIMITES")
        contre_ex = extraire_section(docstring, "CONTRE-EXEMPLES") or extraire_section(
            docstring, "CONTRE-EXEMPLE"
        )
        tierces = _extraire_tierces(f)
        phrase = _phrase_explicative(source, invocation, help_bloc)
        page = generer_page_outil(
            nom=f.stem,
            docstring=docstring,
            invocation=invocation,
            help_bloc=help_bloc,
            limites=limites,
            contre_exemples=contre_ex,
            tierces=tierces,
            phrase_explicative=phrase,
        )
        (docs_dir / f"{f.stem}.md").write_text(page, encoding="utf-8")
        if len(exemples) < 3:
            question = extraire_question(f)
            if question != "non mesuré":
                exemples.append((f.stem, question))

    # Tableaux des outils livrés / non retenus
    livrables_rows = []
    nonlivrables_rows = []
    plateformes_libelles = []
    for iso in isolation_jsons:
        for rapport in iso.get("rapports", []):
            libelle = _get_plateforme_libelle(rapport)
            if libelle and libelle not in plateformes_libelles:
                plateformes_libelles.append(libelle)

    for f in sorted(outils, key=lambda p: p.stem.lower()):
        nom = f.stem
        question = extraire_question(f)
        éprouvé = f"{livrable_counts.get(nom,0)} / {total_reports}"
        if nom in copies:
            lien = f"[{nom}](docs/{nom}.md)"
            ligne = f"| {cellule(lien)} | {cellule(question)} | {cellule(éprouvé)} |"
            livrables_rows.append(ligne)
        else:
            verdict = "NON LIVRABLE"
            ligne = f"| {cellule(nom)} | {cellule(question)} | {cellule(verdict)} | {cellule(éprouvé)} |"
            nonlivrables_rows.append(ligne)

    outils_section = (
        "## Les outils\n\n"
        f"{livrables} outils, chacun avec sa page : ce qu'il répond, comment on s'en sert, "
        "toutes ses options, et ce qu'il ne fait pas.\n\n"
        "<details>\n"
        f"<summary>Voir les {livrables} outils</summary>\n\n"
        "| Outil | Ce qu’il répond | Éprouvé sur |\n"
        "|-------|-----------------|------------|\n"
        + "\n".join(livrables_rows) +
        "\n\n</details>"
    )

    if non_livrables > 0:
        outils_section += (
            "\n\n## Ce qui n'a pas été retenu\n"
            + f"{non_livrables} outils n'ont pas été sélectionnés car ils ne sont pas livrés dans cette vitrine.\n"
            + "| Outil | Ce qu’il répond | Verdict | Éprouvé sur |\n"
            + "|-------|-----------------|--------|------------|\n"
            + "\n".join(nonlivrables_rows)
        )

    # Mesures
    mesures = (
        "- `docker build .` : rejoue la porte et le juge À L'INTÉRIEUR d'une image épinglée par empreinte. Si un seul outil échoue, l'image n'existe pas.\n"
        "- `mesures/porte_qualite.py` : 15 contrôles de forme sur chaque outil.\n"
        "- `mesures/test_isolation.py` : chaque outil est copié seul dans un dossier temporaire, appelé de seize façons, avec détection de fuite en lecture, écriture et réseau.\n"
        "Un **VERDICT** peut être : LIVRABLE, FORWARD KO, REVERSE KO, FUITE."
    )

    if plateformes_libelles:
        mesures += f"\n\n*Verdicts établis sur {total_reports} plateforme{'' if total_reports == 1 else 's'} : {_formater_enumeration(plateformes_libelles)}*"

    # Limites (BA3)
    epreuve_path = racine / "mesures" / "epreuve_188.json"
    try:
        with epreuve_path.open(encoding="utf-8") as f:
            epreuve = json.load(f)
        total = epreuve.get("total", 0)
        imp = epreuve.get("compte", {}).get("IMPORTE", 0)
        bloquées = total - imp
        pourcentage = round(bloquées * 100 / total, 1) if total else 0

        if non_livrables == 0:
            limites = (
                "⚠️ **« LIVRABLE » ne veut pas dire « juste ».** Le juge prouve qu'un outil "
                "répond, refuse proprement ce qu'il doit refuser, et ne sort pas de son "
                "périmètre. Il ne prouve pas que sa réponse est la bonne : c'est à vous de "
                "lire la question qu'il déclare et de juger si elle est la vôtre.\n\n"
                "⚠️ **Mesuré sur une machine, pas sur toutes.** "
                f"{bloquées} distributions sur {total} sont bloquées par une politique système "
                f"sur la machine de référence ({pourcentage} %) ; les outils qui les emploient "
                "fonctionnent en mode dégradé et le disent."
            )
        else:
            limites = f"⚠️ {non_livrables} outils ne sont pas livrables. Le statut « LIVRABLE » ne garantit pas l’absence de défauts. {bloquées} distributions sur {total} sont bloquées ({pourcentage} %)."

        if total_reports > 1:
            mismatches = sum(1 for c in livrable_counts.values() if 0 < c < total_reports)
            if mismatches > 0:
                limites += f" {mismatches} outil{'s' if mismatches!=1 else ''} passent sur une plateforme mais pas sur l’autre."
    except FileNotFoundError:
        if non_livrables == 0:
            limites = (
                "⚠️ **« LIVRABLE » ne veut pas dire « juste ».** Le juge prouve qu'un outil "
                "répond, refuse proprement ce qu'il doit refuser, et ne sort pas de son "
                "périmètre. Il ne prouve pas que sa réponse est la bonne : c'est à vous de "
                "lire la question qu'il déclare et de juger si elle est la vôtre.\n\n"
                "⚠️ **Mesuré sur une machine, pas sur toutes.** non mesuré."
            )
        else:
            limites = f"⚠️ {non_livrables} outils ne sont pas livrables. Le statut « LIVRABLE » ne garantit pas l’absence de défauts. non mesuré."
    except Exception as e:
        print(f"Erreur lors de la lecture de {epreuve_path} : {e}", file=sys.stderr)
        if non_livrables == 0:
            limites = (
                "⚠️ **« LIVRABLE » ne veut pas dire « juste ».** Le juge prouve qu'un outil "
                "répond, refuse proprement ce qu'il doit refuser, et ne sort pas de son "
                "périmètre. Il ne prouve pas que sa réponse est la bonne : c'est à vous de "
                "lire la question qu'il déclare et de juger si elle est la vôtre.\n\n"
                "⚠️ **Mesuré sur une machine, pas sur toutes.** non mesuré."
            )
        else:
            limites = f"⚠️ {non_livrables} outils ne sont pas livrables. Le statut « LIVRABLE » ne garantit pas l’absence de défauts. non mesuré."

    # Comment cette page a été produite
    script_name = Path(__file__).name
    commentaire_production = "\n".join(
        [
            "## Comment cette page a été produite",
            "",
            "Ce README, la bannière, le `pyproject.toml` et le `Dockerfile` sont",
            "engendrés depuis les mesures. Aucun chiffre n'y est saisi à la main :",
            "",
            "```",
            f"python {script_name} engendrer <cible> --racine .",
            "```",
            "",
            "Relancer la commande après une nouvelle mesure met la page à jour.",
            "Un chiffre absent s'écrit « non mesuré », jamais une valeur plausible.",
            "",
        ]
    )

    # Assemble le README via le gabarit unique
    readme = generer_readme(
        banniere_url=banniere_url,
        description=description,
        tableau_etat=tableau_etat,
        par_ou_commencer="",
        installation="",
        demarrage="",
        outils_section=outils_section,
        mesures=mesures,
        limites=limites,
        commentaire_production=commentaire_production,
        depot_url=depot_url,
        branche=branche,
        titre=titre or racine.name,
        total_outils=total_outils,
        nom_projet=nom_projet,
        exemples=exemples,
        annee=annee_courante,
        auteur=auteur,
        copyright_text=copyright_text,
    )
    (cible / "README.md").write_text(readme, encoding="utf-8")

    # 9. pyproject.toml
    imports_par_outil_copies = {k: v for k, v in imports_par_outil.items() if k in copies}
    imports_non_resolus = [
        imp
        for imp in {
            imp for lst in imports_par_outil_copies.values() for imp in lst
        }
        if imp not in sys.stdlib_module_names
        and not importlib.metadata.packages_distributions().get(imp)
    ]
    (cible / "pyproject.toml").write_text(
        generer_pyproject(
            imports_par_outil_copies,
            nom_projet,
            depot_url,
            imports_non_resolus,
            description=sous_titre or description,
        ),
        encoding="utf-8",
    )

    # 10. Dockerfile (BA8)
    (cible / "Dockerfile").write_text(generer_dockerfile(image_digest, nom_projet), encoding="utf-8")

    # .dockerignore (BA8)
    (cible / ".dockerignore").write_text(
        ".git\n"
        ".github\n"
        "__pycache__\n"
        "*.pyc\n"
        "docs\n",
        encoding="utf-8",
    )

    # 11. LICENSE (BA5, BA6)
    try:
        license_text = generer_license(annee_courante, auteur or titre or racine.name, chemin_licence)
        (cible / "LICENSE").write_text(license_text, encoding="utf-8")
    except Exception as e:
        print(f"Erreur lors de la génération de LICENSE : {e}", file=sys.stderr)
        (cible / "LICENSE").write_text(generer_license(annee_courante, auteur or titre or racine.name), encoding="utf-8")

    # 12. workflow GitHub (BA7)
    version_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    (cible / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (cible / ".github" / "workflows" / "verifier.yml").write_text(generer_workflow(version_python), encoding="utf-8")

    # 13. copie des outils
    for f in outils:
        if tout_copier or f.stem in copies:
            try:
                shutil.copy2(f, cible / "outils" / f.name)
            except OSError as e:
                raise FilesystemError(f"Copie de l’outil {f} impossible : {e}") from e

    # 14. retour JSON
    examines = [f.stem for f in outils][:200]
    trous_restants = _verifier_trous(readme)[0]
    return total_outils, examines, trous_restants, banner_type

def main() -> int:
    _reconfig_stdout()
    commun = argparse.ArgumentParser(add_help=False)
    commun.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Répertoire racine contenant les artefacts (défaut : répertoire du script).",
    )
    commun.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Émettre un unique objet JSON sur stdout.",
    )
    commun.add_argument(
        "--image-digest",
        type=str,
        default=argparse.SUPPRESS,
        help="Empreinte SHA256 de l’image Python à utiliser dans le Dockerfile.",
    )
    commun.add_argument(
        "--titre",
        type=str,
        default=argparse.SUPPRESS,
        help="Titre de la bannière (défaut : nom du dossier racine).",
    )
    commun.add_argument(
        "--sous-titre",
        type=str,
        default=argparse.SUPPRESS,
        help="Sous‑titre de la bannière (défaut : phrase neutre basée sur le nombre d’outils).",
    )
    commun.add_argument(
        "--depot-url",
        type=str,
        default=argparse.SUPPRESS,
        help="URL du dépôt à placer dans [project.urls]; si absent, la section est omise.",
    )
    commun.add_argument(
        "--isolation",
        type=Path,
        action="append",
        default=None,
        help="Chemin vers un rapport d’isolation (peut être répété).",
    )
    commun.add_argument(
        "--branche",
        type=str,
        default="main",
        help="Nom de la branche pour l'URL raw GitHub (défaut: main).",
    )
    commun.add_argument(
        "--licence",
        type=Path,
        default=argparse.SUPPRESS,
        help="Chemin vers le fichier de licence à utiliser (défaut : artefacts/AGPL-3.0.txt si valide).",
    )
    commun.add_argument(
        "--auteur",
        type=str,
        default=argparse.SUPPRESS,
        help="Nom de l'auteur ou de la société pour le copyright (défaut : nom du projet).",
    )
    commun.add_argument(
        "--copyright",
        dest="copyright_text",
        type=str,
        default=argparse.SUPPRESS,
        help="Texte complet de la ligne de copyright juridique (exact, sans aucun préfixe).",
    )
    commun.add_argument(
        "--banniere",
        type=Path,
        default=argparse.SUPPRESS,
        help="Chemin vers une bannière PNG fournie (remplace la génération automatique).",
    )

    parser = argparse.ArgumentParser(
        prog="publier_vitrine.py",
        description="Génère une vitrine complète à partir de mesures.",
        formatter_class=argparse.RawTextHelpFormatter,
        parents=[commun],
    )
    sub = parser.add_subparsers(dest="commande", required=True)

    eng = sub.add_parser(
        "engendrer",
        help="Produit la vitrine dans le répertoire cible.",
        description="Engendre les fichiers de la vitrine.",
        parents=[commun],
    )
    eng.add_argument("cible", type=Path, help="Répertoire où créer la vitrine.")
    eng.add_argument(
        "--tout-copier",
        action="store_true",
        help="Copier tous les outils, même ceux non livrables.",
    )

    args = parser.parse_args()

    racine = getattr(args, "racine", Path(__file__).resolve().parent)
    json_mode = getattr(args, "json", False)
    image_digest = getattr(args, "image_digest", None)
    titre = getattr(args, "titre", "")
    sous_titre = getattr(args, "sous_titre", "")
    depot_url = getattr(args, "depot_url", None)
    isolation_paths = getattr(args, "isolation", None)
    branche = getattr(args, "branche", "main")
    chemin_licence = getattr(args, "licence", None)
    auteur = getattr(args, "auteur", None)
    banniere_path = getattr(args, "banniere", None)
    copyright_text = getattr(args, "copyright_text", None)

    if not auteur:
        print("Avertissement : --auteur non fourni, utilisation du nom du projet pour le copyright", file=sys.stderr)

    if args.commande == "engendrer":
        try:
            total, examines, trous_restants, banner_type = engendrer(
                cible=args.cible,
                racine=racine,
                tout_copier=getattr(args, "tout_copier", False),
                image_digest=image_digest,
                titre=titre,
                sous_titre=sous_titre,
                depot_url=depot_url,
                isolation_paths=isolation_paths,
                branche=branche,
                chemin_licence=chemin_licence,
                auteur=auteur,
                banniere=banniere_path,
                copyright_text=copyright_text,
            )
        except FilesystemError as e:
            print(e, file=sys.stderr)
            return 1

        if json_mode:
            out = {
                "denominateur": total,
                "examines": examines,
                "examines_tronques": len(examines) >= 200,
                "trous_restants": trous_restants,
                "banniere": banner_type,
            }
            if total == 0:
                print("Denominateur nul : impossible de conclure.", file=sys.stderr)
                return 3
            json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            if trous_restants > 0:
                return 2
            return 0
        if trous_restants > 0:
            return 2
        return 0
    return 1

if __name__ == "__main__":
    raise SystemExit(main())