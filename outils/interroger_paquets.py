"""interroger_paquets.py - Interroge les métadonnées des paquets Python installés.

QUESTION
    Quelle version, quels points d'entrée, quelle distribution ou quelles dépendances
    pour un paquet Python installé dans l'environnement courant ?

MESURE
    Utilise importlib_metadata (backport) ou importlib.metadata (stdlib) pour lire les
    métadonnées des distributions Python. En mode dégradé (stdlib seule), seules les
    fonctions compatibles sont disponibles.

HYPOTHESES
    - L'environnement Python est correctement installé et accessible en lecture.
    - Les métadonnées des paquets sont au format standard (PEP 566).
    - Pour les modules importables, le nom du module correspond à un fichier .py ou
      à un dossier __init__.py dans site-packages.

LIMITES
    - Ne voit pas les paquets installés en mode "editable" (pip install -e) dont
      les métadonnées ne sont pas dans site-packages.
    - Ne résout pas les dépendances optionnelles non installées.
    - En mode dégradé, seules les fonctions compatibles avec importlib.metadata sont disponibles.

CONTRE-EXEMPLES
    - Un paquet installé via un lien symbolique hors de site-packages ne sera pas vu.
    - Un paquet avec des métadonnées corrompues lèvera une exception.
    - Un module importé depuis un zip ne sera pas associé à sa distribution.

INVOCATION
    {outil} version-requests --json
    {outil} entrypoints-pytest --json
    {outil} distribution-de-module --module yaml --json
    {outil} dependencies-pandas --json
    {outil} dependencies-pandas --optionnel --json

DOMAINE
    Tout environnement Python 3.8+ où les métadonnées des paquets sont accessibles.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Configuration de l'encodage des flux standard
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

# Déclaration des exceptions spécifiques
class PaquetNonTrouveError(Exception):
    """Le paquet ou module demandé n'existe pas dans l'environnement."""

class MetadonneesInaccessiblesError(Exception):
    """Les métadonnées du paquet sont corrompues ou illisibles."""

class ModuleNonAssocieError(Exception):
    """Le module demandé n'est pas associé à une distribution connue."""

class ModeDegradeError(Exception):
    """Fonctionnalité non disponible en mode dégradé."""

__all__ = [
    "obtenir_version_requests",
    "obtenir_entrypoints_pytest",
    "obtenir_distribution_du_module",
    "obtenir_dependances_pandas",
]

# ======================
# COEUR LOGIQUE
# ======================

def _detecter_moteur() -> tuple[Any, str]:
    """Détecte le moteur disponible et retourne son nom."""
    try:
        import importlib_metadata
        return importlib_metadata, "importlib_metadata"
    except ImportError:
        try:
            import importlib.metadata as importlib_metadata
            return importlib_metadata, "stdlib"
        except ImportError:
            raise ModeDegradeError("Aucun moteur de métadonnées disponible.")

def obtenir_version_requests() -> str:
    """Retourne la version installée de requests."""
    importlib_metadata, moteur = _detecter_moteur()
    try:
        return importlib_metadata.version("requests")
    except importlib_metadata.PackageNotFoundError:
        raise PaquetNonTrouveError("Le paquet 'requests' n'est pas installé.")
    except Exception as e:
        raise MetadonneesInaccessiblesError(
            f"Impossible de lire la version de 'requests' : {str(e)}"
        )

def obtenir_entrypoints_pytest() -> List[str]:
    """Retourne les points d'entrée console_scripts de pytest."""
    importlib_metadata, moteur = _detecter_moteur()

    try:
        entry_points = importlib_metadata.entry_points()
        if hasattr(entry_points, "select"):
            console_scripts = entry_points.select(group="console_scripts", name=None)
        else:
            console_scripts = entry_points.get("console_scripts", [])
        pytest_scripts = [
            ep.name for ep in console_scripts if ep.value.startswith("pytest:")
        ]
        return pytest_scripts
    except Exception as e:
        raise MetadonneesInaccessiblesError(
            f"Impossible de lire les points d'entrée de pytest : {str(e)}"
        )

def obtenir_distribution_du_module(nom_module: str) -> str:
    """Retourne le nom de la distribution qui a installé le module donné."""
    if not isinstance(nom_module, str) or not nom_module.isidentifier():
        raise ValueError(f"Nom de module invalide : {nom_module!r}")

    importlib_metadata, moteur = _detecter_moteur()

    try:
        # Vérifier d'abord si le module est importable
        try:
            module = __import__(nom_module)
        except ImportError:
            raise ModuleNonAssocieError(
                f"Le module '{nom_module}' n'est pas importable."
            )

        if hasattr(importlib_metadata, "packages_distributions"):
            pkgs_dist = importlib_metadata.packages_distributions()
            for pkg, dists in pkgs_dist.items():
                if nom_module == pkg:
                    return dists[0]
        else:
            # Fallback pour stdlib (moins précis)
            for dist in importlib_metadata.distributions():
                try:
                    if nom_module in dist.read_text("top_level.txt") or any(
                        nom_module == f.split(".")[0]
                        for f in dist.files or []
                        if f.suffix == ".py"
                    ):
                        return dist.metadata["Name"]
                except (AttributeError, TypeError, FileNotFoundError):
                    continue

        raise ModuleNonAssocieError(
            f"Aucune distribution trouvée pour le module '{nom_module}'."
        )
    except Exception as e:
        if isinstance(e, (ModuleNonAssocieError, ModeDegradeError)):
            raise
        raise MetadonneesInaccessiblesError(
            f"Impossible de déterminer la distribution pour '{nom_module}' : {str(e)}"
        )

def obtenir_dependances_pandas(optionnel: bool = False) -> Dict[str, Any]:
    """Retourne les dépendances de pandas."""
    importlib_metadata, moteur = _detecter_moteur()

    try:
        requires = importlib_metadata.requires("pandas") or []

        # Filtrer les dépendances requises et optionnelles
        required_deps = []
        optional_deps = {}

        for req in requires:
            if "extra ==" in req:
                # Dépendance optionnelle
                extra = req.split("extra ==")[1].split("]")[0].strip('"').strip("'")
                dep = req.split(";")[0].strip()
                if extra not in optional_deps:
                    optional_deps[extra] = []
                optional_deps[extra].append(dep)
            else:
                # Dépendance requise
                required_deps.append(req.split(";")[0].strip())

        result = {"required": required_deps}
        if optionnel:
            result["optional"] = optional_deps
        return result
    except importlib_metadata.PackageNotFoundError:
        raise PaquetNonTrouveError("Le paquet 'pandas' n'est pas installé.")
    except Exception as e:
        raise MetadonneesInaccessiblesError(
            f"Impossible de lire les dépendances de pandas : {str(e)}"
        )

# ======================
# COUCHE CLI
# ======================

def _creer_analyseur_parent() -> argparse.ArgumentParser:
    """Crée l'analyseur parent pour les options communes."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Produit une sortie JSON sur stdout (par défaut : sortie lisible).",
    )
    parent.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Racine du projet (par défaut : répertoire du script).",
    )
    return parent

def _creer_analyseur_principal() -> argparse.ArgumentParser:
    """Crée l'analyseur principal avec les sous-commandes."""
    parent = _creer_analyseur_parent()
    parser = argparse.ArgumentParser(
        description="Interroge les métadonnées des paquets Python installés.",
        parents=[parent],
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    # version-requests
    version_parser = sous_parsers.add_parser(
        "version-requests",
        help="Affiche la version installée de requests.",
        parents=[parent],
    )

    # entrypoints-pytest
    entrypoints_parser = sous_parsers.add_parser(
        "entrypoints-pytest",
        help="Affiche les points d'entrée console_scripts de pytest.",
        parents=[parent],
    )

    # distribution-de-module
    dist_parser = sous_parsers.add_parser(
        "distribution-de-module",
        help="Affiche la distribution qui a installé un module donné.",
        parents=[parent],
    )
    dist_parser.add_argument(
        "--module",
        type=str,
        required=True,
        help="Nom du module à interroger (ex: yaml).",
    )

    # dependencies-pandas
    deps_parser = sous_parsers.add_parser(
        "dependencies-pandas",
        help="Affiche les dépendances de pandas.",
        parents=[parent],
    )
    deps_parser.add_argument(
        "--optionnel",
        action="store_true",
        help="Inclut les dépendances optionnelles.",
    )

    return parser

def _formater_sortie(
    resultat: Union[str, List[str], Dict[str, Any]],
    json_mode: bool,
    denominateur: int,
    moteur: str,
    examines: Optional[List[str]] = None,
) -> str:
    """Formate le résultat selon le mode de sortie."""
    examines = examines or []
    examines_tronques = len(examines) > 200
    examines_liste = examines[:200] if examines_tronques else examines

    if json_mode:
        sortie = {
            "resultat": resultat,
            "denominateur": denominateur,
            "moteur": moteur,
            "examines": examines_liste,
        }
        if examines_tronques:
            sortie["examines_tronques"] = True
        return json.dumps(sortie, ensure_ascii=False, indent=2)
    else:
        if isinstance(resultat, str):
            return resultat
        elif isinstance(resultat, list):
            return "\n".join(resultat)
        elif isinstance(resultat, dict):
            return "\n".join(f"{k}: {v}" for k, v in resultat.items())
        else:
            return str(resultat)

def _gerer_erreur(
    erreur: Exception,
    json_mode: bool,
    denominateur: int = 0,
    message_refus: Optional[str] = None,
    moteur: str = "stdlib",
) -> tuple[str, int]:
    """Gère les erreurs et produit une sortie adaptée."""
    message = str(erreur)
    if message_refus:
        message = message_refus

    if json_mode:
        sortie = {
            "erreur": message,
            "denominateur": denominateur,
            "moteur": moteur,
            "examines": [],
        }
        return json.dumps(sortie, ensure_ascii=False), 3 if denominateur == 0 else 1
    else:
        return message, 3 if denominateur == 0 else 1

def main() -> int:
    """Point d'entrée principal de l'outil."""
    parser = _creer_analyseur_principal()
    args = parser.parse_args()

    # Déterminer la racine
    racine = getattr(args, "racine", RACINE)
    if not isinstance(racine, Path):
        racine = RACINE

    # Déterminer le mode JSON
    json_mode = getattr(args, "json", False)

    # Détecter le moteur disponible
    try:
        importlib_metadata, moteur = _detecter_moteur()
    except ModeDegradeError as e:
        if args.commande != "version-requests":
            print("mode degrade : importlib_metadata absent", file=sys.stderr)
        moteur = "stdlib"

    try:
        if args.commande == "version-requests":
            resultat = obtenir_version_requests()
            sortie = _formater_sortie(
                resultat, json_mode, denominateur=1, moteur=moteur, examines=["requests"]
            )
            print(sortie)
            return 0

        elif args.commande == "entrypoints-pytest":
            resultat = obtenir_entrypoints_pytest()
            sortie = _formater_sortie(
                resultat,
                json_mode,
                denominateur=1,
                moteur=moteur,
                examines=["pytest"],
            )
            print(sortie)
            return 0

        elif args.commande == "distribution-de-module":
            resultat = obtenir_distribution_du_module(args.module)
            sortie = _formater_sortie(
                resultat,
                json_mode,
                denominateur=1,
                moteur=moteur,
                examines=[args.module],
            )
            print(sortie)
            return 0

        elif args.commande == "dependencies-pandas":
            resultat = obtenir_dependances_pandas(args.optionnel)
            sortie = _formater_sortie(
                resultat,
                json_mode,
                denominateur=1,
                moteur=moteur,
                examines=["pandas"],
            )
            print(sortie)
            return 0

    except PaquetNonTrouveError as e:
        sortie, code = _gerer_erreur(e, json_mode, denominateur=0, moteur=moteur)
        print(sortie, file=sys.stderr)
        return code
    except (MetadonneesInaccessiblesError, ModuleNonAssocieError, ValueError) as e:
        sortie, code = _gerer_erreur(e, json_mode, denominateur=0, moteur=moteur)
        print(sortie, file=sys.stderr)
        return code
    except Exception as e:
        sortie, code = _gerer_erreur(e, json_mode, denominateur=0, moteur=moteur)
        print(sortie, file=sys.stderr)
        return code

if __name__ == "__main__":
    raise SystemExit(main())