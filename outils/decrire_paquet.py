"""Analyse et force l'usage de setuptools comme moteur d'empaquetage Python.

QUESTION      Le processus d'importation utilise-t-il réellement setuptools ?
              Quelle est la configuration complète du paquet (pyproject.toml/setup.cfg) ?
MESURE        Présence de _distutils_hack dans sys.modules et absence de distutils.
              Configuration développée depuis pyproject.toml/setup.cfg via setuptools.
HYPOTHESES    Aucun autre module n'a modifié sys.modules entre le démarrage et l'appel.
              Les fichiers de configuration sont valides et accessibles en lecture.
LIMITES       L'outil ne peut pas détecter des imports faits dans des sous-processus.
              Le mode dégradé (stdlib) ne développe pas les valeurs dynamiques.
CONTRE-EXEMPLE Un script qui importe distutils puis setuptools dans un thread secondaire.
               Un pyproject.toml avec des valeurs dynamiques non résolues en mode stdlib.
INVOCATION
    {outil} prevenir_conflit --json
    {outil} forcer_setuptools --json
    {outil} decrire_configuration --json --racine {dossier}
DOMAINE       Environnements Python 3.8+ où setuptools est installé ou non.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tomllib
from configparser import ConfigParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "main",
    "parse_args",
    "prevenir_conflit",
    "forcer_setuptools",
    "decrire_configuration",
    "_charger_hack",
    "_lire_configuration_stdlib",
    "_lire_configuration_setuptools",
]

RACINE = Path(__file__).resolve().parent

# Configuration des flux de sortie
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Analyseur parent pour les options globales
parent_parser = argparse.ArgumentParser(add_help=False)
parent_parser.add_argument(
    "--json", action="store_true", default=argparse.SUPPRESS, help="Sortie en JSON"
)
parent_parser.add_argument(
    "--racine",
    type=Path,
    default=argparse.SUPPRESS,
    help="Racine du projet (défaut: répertoire du script)",
)

def _charger_hack() -> Optional[Any]:
    """Charge le module _distutils_hack si disponible.

    Retourne:
        Le module si trouvé, None sinon.
    """
    try:
        return importlib.import_module("_distutils_hack")
    except ImportError:
        return None

def _lire_configuration_stdlib(racine: Path) -> Dict[str, Any]:
    """Lit la configuration avec la bibliothèque standard (mode dégradé).

    Args:
        racine: Chemin racine du projet.

    Retourne:
        Dictionnaire avec la configuration brute et le dénominateur.
    """
    config = {
        "pyproject": {},
        "setup_cfg": {},
        "denominateur": 0,
        "moteur": "stdlib"
    }

    # Lecture de pyproject.toml
    pyproject_path = racine / "pyproject.toml"
    if pyproject_path.exists():
        try:
            with open(pyproject_path, "rb") as f:
                config["pyproject"] = tomllib.load(f)
            config["denominateur"] += 1
        except (tomllib.TOMLDecodeError, OSError) as e:
            print(f"Erreur lecture pyproject.toml: {e}", file=sys.stderr)

    # Lecture de setup.cfg
    setup_cfg_path = racine / "setup.cfg"
    if setup_cfg_path.exists():
        try:
            parser = ConfigParser()
            parser.read(setup_cfg_path)
            config["setup_cfg"] = {section: dict(parser[section])
                                 for section in parser.sections()}
            config["denominateur"] += 1
        except (OSError, Exception) as e:
            print(f"Erreur lecture setup.cfg: {e}", file=sys.stderr)

    return config

def _lire_configuration_setuptools(racine: Path) -> Dict[str, Any]:
    """Lit la configuration avec setuptools (mode réel).

    Args:
        racine: Chemin racine du projet.

    Retourne:
        Dictionnaire avec la configuration développée et le dénominateur.
    """
    try:
        import setuptools.config.pyprojecttoml as pyprojecttoml
        import setuptools.config.setupcfg as setupcfg
    except ImportError:
        print("mode dégradé : setuptools absent", file=sys.stderr)
        return _lire_configuration_stdlib(racine)

    config = {
        "pyproject": {},
        "setup_cfg": {},
        "denominateur": 0,
        "moteur": "setuptools"
    }

    # Lecture de pyproject.toml
    pyproject_path = racine / "pyproject.toml"
    if pyproject_path.exists():
        try:
            config["pyproject"] = pyprojecttoml.read_configuration(
                pyproject_path,
                expand=True,
                ignore_option_errors=False
            )
            config["denominateur"] += 1
        except Exception as e:
            print(f"Erreur lecture pyproject.toml avec setuptools: {e}", file=sys.stderr)

    # Lecture de setup.cfg
    setup_cfg_path = racine / "setup.cfg"
    if setup_cfg_path.exists():
        try:
            config["setup_cfg"] = setupcfg.read_configuration(
                setup_cfg_path,
                find_others=False,
                ignore_option_errors=False
            )
            config["denominateur"] += 1
        except Exception as e:
            print(f"Erreur lecture setup.cfg avec setuptools: {e}", file=sys.stderr)

    return config

def decrire_configuration(racine: Optional[Path] = None) -> Dict[str, Any]:
    """Décrit la configuration complète du paquet.

    Args:
        racine: Chemin racine optionnel du projet.

    Retourne:
        Dictionnaire avec la configuration et le dénominateur.
    """
    if racine is None:
        racine = RACINE

    # Vérification de l'existence des fichiers
    pyproject_exists = (racine / "pyproject.toml").exists()
    setup_cfg_exists = (racine / "setup.cfg").exists()

    if not pyproject_exists and not setup_cfg_exists:
        print("Aucun fichier de configuration trouvé (pyproject.toml/setup.cfg)", file=sys.stderr)
        return {
            "question": "Quelle est la configuration complète du paquet ?",
            "mesure": "Aucun fichier de configuration trouvé",
            "hypotheses": "Les fichiers de configuration sont valides et accessibles en lecture.",
            "limites": "Le mode dégradé (stdlib) ne développe pas les valeurs dynamiques.",
            "contre_exemple": "Un pyproject.toml avec des valeurs dynamiques non résolues en mode stdlib.",
            "domaine": "Environnements Python 3.8+ où setuptools est installé ou non.",
            "denominateur": 0,
            "moteur": "stdlib",
            "examines": ["pyproject.toml", "setup.cfg"],
            "examines_tronques": False,
        }

    # Détection de setuptools pour choisir le mode
    try:
        import setuptools
        return _lire_configuration_setuptools(racine)
    except ImportError:
        print("mode dégradé : setuptools absent", file=sys.stderr)
        return _lire_configuration_stdlib(racine)

def prevenir_conflit(racine: Optional[Path] = None) -> Dict[str, Any]:
    """Vérifie que setuptools a déjà injecté _distutils_hack.

    Args:
        racine: Chemin racine optionnel (non utilisé ici mais requis par l'API).

    Retourne:
        Dictionnaire avec les résultats et le dénominateur.

    Lève:
        SystemExit: Avec code 3 si conflit détecté.
    """
    distutils_present = "distutils" in sys.modules
    hack = _charger_hack()

    if distutils_present and hack is None:
        if getattr(sys, "_called_from_decrire_paquet", False):
            # Évite la récursion infinie lors du forçage
            return {
                "question": "Le processus d'importation utilise-t-il réellement setuptools ?",
                "mesure": "Conflit détecté: distutils déjà chargé sans _distutils_hack",
                "hypotheses": "Aucun autre module n'a modifié sys.modules entre le démarrage et l'appel.",
                "limites": "L'outil ne peut pas détecter des imports faits dans des sous-processus.",
                "contre_exemple": "Un script qui importe distutils puis setuptools dans un thread secondaire.",
                "domaine": "Environnements Python 3.8+ où setuptools est installé.",
                "denominateur": 0,
                "moteur": "stdlib",
                "examines": ["sys.modules"],
                "examines_tronques": False,
            }

        print("Conflit détecté: distutils déjà chargé", file=sys.stderr)
        sys.exit(3)

    return {
        "question": "Le processus d'importation utilise-t-il réellement setuptools ?",
        "mesure": "setuptools actif via _distutils_hack" if hack else "Aucun conflit détecté",
        "hypotheses": "Aucun autre module n'a modifié sys.modules entre le démarrage et l'appel.",
        "limites": "L'outil ne peut pas détecter des imports faits dans des sous-processus.",
        "contre_exemple": "Un script qui importe distutils puis setuptools dans un thread secondaire.",
        "domaine": "Environnements Python 3.8+ où setuptools est installé.",
        "denominateur": 1,
        "moteur": "setuptools" if hack else "stdlib",
        "examines": ["sys.modules"],
        "examines_tronques": False,
    }

def forcer_setuptools(racine: Optional[Path] = None) -> Dict[str, Any]:
    """Force l'usage de setuptools en retirant distutils de sys.modules.

    Args:
        racine: Chemin racine optionnel (non utilisé ici mais requis par l'API).

    Retourne:
        Dictionnaire avec les résultats et le dénominateur.

    Lève:
        SystemExit: Avec code 2 si échec.
    """
    hack = _charger_hack()
    if hack is None:
        print("setuptools introuvable", file=sys.stderr)
        return {
            "question": "Le processus d'importation utilise-t-il réellement setuptools ?",
            "mesure": "setuptools introuvable",
            "hypotheses": "Aucun autre module n'a modifié sys.modules entre le démarrage et l'appel.",
            "limites": "L'outil ne peut pas détecter des imports faits dans des sous-processus.",
            "contre_exemple": "Un script qui importe distutils puis setuptools dans un thread secondaire.",
            "domaine": "Environnements Python 3.8+ où setuptools est installé.",
            "denominateur": 0,
            "moteur": "stdlib",
            "examines": ["sys.modules"],
            "examines_tronques": False,
        }

    # Sauvegarde l'état actuel pour restauration en cas d'échec
    modules_sauve = sys.modules.copy()
    distutils_present = "distutils" in sys.modules

    try:
        if distutils_present:
            # Marque que nous sommes dans le processus de forçage
            sys._called_from_decrire_paquet = True
            del sys.modules["distutils"]

        # Réimporte setuptools pour réinstaller le hack
        importlib.import_module("setuptools")

        print("setuptools forcé, conflit résolu", file=sys.stderr)
        return {
            "question": "Le processus d'importation utilise-t-il réellement setuptools ?",
            "mesure": "setuptools forcé avec succès",
            "hypotheses": "Aucun autre module n'a modifié sys.modules entre le démarrage et l'appel.",
            "limites": "L'outil ne peut pas détecter des imports faits dans des sous-processus.",
            "contre_exemple": "Un script qui importe distutils puis setuptools dans un thread secondaire.",
            "domaine": "Environnements Python 3.8+ où setuptools est installé.",
            "denominateur": 1,
            "moteur": "setuptools",
            "examines": ["sys.modules"],
            "examines_tronques": False,
        }
    except ImportError as e:
        # Restaure l'état précédent
        sys.modules.clear()
        sys.modules.update(modules_sauve)
        print(f"Échec du forçage: {e}", file=sys.stderr)
        return {
            "question": "Le processus d'importation utilise-t-il réellement setuptools ?",
            "mesure": f"Échec du forçage: {e}",
            "hypotheses": "Aucun autre module n'a modifié sys.modules entre le démarrage et l'appel.",
            "limites": "L'outil ne peut pas détecter des imports faits dans des sous-processus.",
            "contre_exemple": "Un script qui importe distutils puis setuptools dans un thread secondaire.",
            "domaine": "Environnements Python 3.8+ où setuptools est installé.",
            "denominateur": 0,
            "moteur": "stdlib",
            "examines": ["sys.modules"],
            "examines_tronques": False,
        }
    finally:
        if hasattr(sys, "_called_from_decrire_paquet"):
            del sys._called_from_decrire_paquet

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Analyse et force l'usage de setuptools comme moteur d'empaquetage.",
        parents=[parent_parser],
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # Sous-commande prevenir_conflit
    prevenir_parser = subparsers.add_parser(
        "prevenir_conflit",
        help="Vérifie que setuptools a déjà injecté _distutils_hack",
        parents=[parent_parser],
    )

    # Sous-commande forcer_setuptools
    forcer_parser = subparsers.add_parser(
        "forcer_setuptools",
        help="Force l'usage de setuptools en retirant distutils de sys.modules",
        parents=[parent_parser],
    )

    # Sous-commande decrire_configuration
    decrire_parser = subparsers.add_parser(
        "decrire_configuration",
        help="Décrit la configuration complète du paquet (pyproject.toml/setup.cfg)",
        parents=[parent_parser],
    )

    return parser.parse_args(argv)

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Point d'entrée principal."""
    try:
        args = parse_args(argv)
        racine = getattr(args, "racine", None)
        if racine is not None:
            if not racine.exists():
                print(f"Chemin racine introuvable: {racine}", file=sys.stderr)
                return 1
            if not racine.is_dir():
                print(f"Argument --racine doit être un dossier: {racine}", file=sys.stderr)
                return 1

        if args.commande == "prevenir_conflit":
            resultat = prevenir_conflit(racine)
        elif args.commande == "forcer_setuptools":
            resultat = forcer_setuptools(racine)
        elif args.commande == "decrire_configuration":
            resultat = decrire_configuration(racine)
        else:
            print(f"Commande inconnue: {args.commande}", file=sys.stderr)
            return 2

        if getattr(args, "json", False):
            json.dump(resultat, sys.stdout, ensure_ascii=False, indent=2)
            print()  # Nouvelle ligne après le JSON
        return 0
    except SystemExit as e:
        return e.code
    except Exception as e:
        print(f"Erreur inattendue: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())