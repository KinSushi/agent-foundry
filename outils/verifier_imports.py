"""
QUESTION       ces imports existent-ils, et si non, que faut-il en faire ?
MESURE         ast pour les extraire sans exécuter ; find_spec pour la présence
               locale ; API JSON de PyPI pour l'existence au monde ; difflib
               pour le nom proche ; modulefinder pour croiser avec pyproject.toml
HYPOTHÈSES     le fichier est du Python valide ; PyPI est joignable en mode
               connecté ; pyproject.toml est valide si présent
LIMITES        un 200 sur PyPI ne prouve PAS l'innocuité — slopsquatting ;
               le nom d'import diffère parfois du nom de distribution ;
               un import dans un try/except ImportError est légitime ;
               les imports dynamiques (importlib.import_module(nom)) échappent ;
               modulefinder rate les imports dynamiques et rapporte les imports
               conditionnels même non exécutés
CONTRE-EXEMPLE `fastapi_utils` classé ABSENT par la mesure locale existe en
               0.8.0 sur PyPI — accuser un import légitime est la faute que cet
               outil doit éviter
DOMAINE        fichiers Python, imports statiques, pyproject.toml valide
"""

from __future__ import annotations

import ast
import difflib
import importlib.metadata
import importlib.util
import json
import sys
import urllib.request
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

# Reconfiguration UTF‑8 pour stdout et stderr
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# tomllib disponible à partir de Python 3.11
try:
    import tomllib  # type: ignore
except ImportError:  # pragma: no cover
    tomllib = None

RACINE = Path(__file__).resolve().parent

# Constantes
NOMBRE_PROCHES = 3
SEUIL_DISTANCE = 2
TIMEOUT_PYPI = 2.0
URL_PYPI = "https://pypi.org/pypi/{}/json"

# Cache pour éviter de refaire les requêtes PyPI
_cache_pypi: Dict[str, Union[Dict, None, bool]] = {}


class BinaryFileError(Exception):
    """Exception levée lorsqu'un fichier source contient des octets nuls."""


class SourceIllisible(Exception):
    """Exception levée lorsqu'un fichier source ne peut être compilé ou parsé."""

    def __init__(self, chemin: Path, message: str) -> None:
        super().__init__(f"{chemin}: {message}")
        self.chemin = chemin
        self.message = message


class _JsonArgumentParser(ArgumentParser):
    """ArgumentParser qui sort un JSON d'erreur si --json est présent."""

    def error(self, message: str) -> None:
        if "--json" in sys.argv:
            json.dump(
                {"erreur": message, "denominateur": 0},
                sys.stdout,
                ensure_ascii=False,
                indent=2,
            )
            sys.stdout.write("\n")
            sys.exit(2)
        super().error(message)


def _nom_distribution(nom_import: str) -> Optional[str]:
    """Retourne le nom de distribution correspondant à un nom d'import."""
    try:
        for dist in importlib.metadata.distributions():
            for fichier in dist.files or []:
                if fichier.name.endswith(".dist-info/top_level.txt"):
                    with (dist.locate_file(fichier)).open(encoding="utf-8") as f:
                        top_levels = f.read().splitlines()
                        if nom_import in top_levels:
                            return dist.metadata["Name"]
    except Exception:
        pass
    return None


def _interroger_pypi(nom: str) -> Union[Dict, None, bool]:
    """Interroge PyPI pour un nom donné.
    Retourne :
        - dict en cas de succès (200)
        - None en cas de HTTP 404
        - False en cas d'erreur réseau ou autre HTTP error
    """
    if nom in _cache_pypi:
        return _cache_pypi[nom]

    url = URL_PYPI.format(nom)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_PYPI) as response:
            if response.getcode() == 200:
                data = json.loads(response.read().decode("utf-8"))
                _cache_pypi[nom] = data
                return data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _cache_pypi[nom] = None
            return None
        sys.stderr.write(f"Erreur HTTP {e.code} pour {nom}\n")
        _cache_pypi[nom] = False
        return False
    except Exception as e:
        sys.stderr.write(f"Erreur réseau pour {nom}: {e}\n")
        _cache_pypi[nom] = False
        return False


def _noms_proches(nom: str, tous_noms: Set[str]) -> List[Tuple[str, int]]:
    """Retourne les noms proches avec leur distance (ratio)."""
    proches = difflib.get_close_matches(nom, tous_noms, n=NOMBRE_PROCHES, cutoff=0.6)
    return [(p, difflib.SequenceMatcher(None, nom, p).ratio()) for p in proches]


def _est_stdlib(nom: str) -> bool:
    """Vérifie si le nom est dans la stdlib."""
    return nom in sys.stdlib_module_names


def _est_present_localement(nom: str) -> bool:
    """Vérifie si le module est présent localement sans l'importer."""
    try:
        return importlib.util.find_spec(nom) is not None
    except (ValueError, ImportError):
        return False


def _est_module_local_projet(nom: str, racine: Path) -> bool:
    """Vérifie si le module correspond à un fichier local au projet."""
    try:
        spec = importlib.util.find_spec(nom)
        if spec is None:
            return False
        origine: Optional[Path] = None
        if spec.origin:
            origine = Path(spec.origin).resolve()
        elif spec.submodule_search_locations:
            origine = Path(spec.submodule_search_locations[0]).resolve()
        if origine is None:
            return False
        racine_resolue = racine.resolve()
        try:
            origine.relative_to(racine_resolue)
            return True
        except ValueError:
            return False
    except (ValueError, ImportError):
        return False


def _extraire_imports(chemin: Path) -> Dict[str, bool]:
    """
    Extrait les noms d'imports statiques d'un fichier Python.
    Retourne un dictionnaire {nom_import: optionnel}.
    """
    try:
        texte = chemin.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise SourceIllisible(chemin, f"Erreur de lecture : {e}")

    if chr(0) in texte:
        raise BinaryFileError(f"Fichier binaire détecté : {chemin}")

    # Validation syntaxique via compile()
    try:
        compile(texte, str(chemin), "exec")
    except Exception as e:
        raise SourceIllisible(chemin, f"Erreur de compilation : {e}")

    try:
        arbre = ast.parse(texte, filename=str(chemin))
    except SyntaxError as e:
        raise SourceIllisible(chemin, f"Erreur de syntaxe : {e}")

    imports: Dict[str, bool] = {}

    def _visiter(node: ast.AST, dans_try_importerror: bool = False) -> None:
        if isinstance(node, ast.Try):
            catches_importerror = any(
                isinstance(h.type, ast.Name) and h.type.id == "ImportError"
                for h in node.handlers
            )
            for child in ast.iter_child_nodes(node):
                _visiter(child, dans_try_importerror or catches_importerror)
            return

        if isinstance(node, ast.Import):
            for alias in node.names:
                nom = alias.name.split(".")[0]
                imports[nom] = dans_try_importerror
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                nom = node.module.split(".")[0]
                imports[nom] = dans_try_importerror

        for child in ast.iter_child_nodes(node):
            _visiter(child, dans_try_importerror)

    _visiter(arbre)
    return imports


def _lire_pyproject(racine: Path) -> Set[str]:
    """Lit les dépendances déclarées dans pyproject.toml."""
    pyproject = racine / "pyproject.toml"
    if not pyproject.exists() or tomllib is None:
        return set()

    try:
        with pyproject.open(encoding="utf-8") as f:
            data = tomllib.loads(f.read())
        deps = set()
        for section in ["dependencies", "optional-dependencies"]:
            if section in data.get("project", {}):
                deps.update(data["project"][section])
        return deps
    except Exception as e:
        sys.stderr.write(f"Erreur lors de la lecture de pyproject.toml: {e}\n")
        return set()


def _noms_distributions() -> Set[str]:
    """Retourne tous les noms de distributions disponibles."""
    try:
        return {dist.metadata["Name"] for dist in importlib.metadata.distributions()}
    except Exception:
        return set()


def verifier(
    chemin_fichier: Union[str, Path],
    racine: Union[str, Path] = RACINE,
    hors_ligne: bool = False,
    proches: bool = False,
) -> List[Tuple[str, str, Dict]]:
    """
    Vérifie les imports d'un fichier Python.

    Args:
        chemin_fichier: Chemin vers le fichier Python à vérifier.
        racine: Racine du projet (pour pyproject.toml).
        hors_ligne: Si True, n'interroge pas PyPI.
        proches: Si True, cherche les noms proches.

    Returns:
        Liste de tuples (nom_import, état, détails).
    """
    chemin = Path(chemin_fichier)
    racine = Path(racine)
    imports_opt = _extraire_imports(chemin)
    if not imports_opt:
        return []

    tous_noms_dist = _noms_distributions()
    deps_declarees = _lire_pyproject(racine)

    # Préparer les résultats de modulefinder une fois par fichier
    badmodules: Set[str] = set()
    modules: Set[str] = set()
    try:
        import modulefinder

        finder = modulefinder.ModuleFinder()
        finder.run_script(str(chemin))
        badmodules = set(finder.badmodules)
        modules = set(finder.modules)
    except ImportError:
        pass
    except Exception as e:
        sys.stderr.write(f"Erreur modulefinder pour {chemin}: {e}\n")

    resultats: List[Tuple[str, str, Dict]] = []

    for nom, optionnel in sorted(imports_opt.items()):
        if _est_stdlib(nom):
            resultats.append((nom, "STDLIB", {}))
            continue

        present_local = _est_present_localement(nom)
        if badmodules or modules:
            if nom in badmodules:
                present_local = False
            elif nom in modules:
                present_local = True

        nom_dist = _nom_distribution(nom)
        details: Dict = {}
        if optionnel:
            details["optionnel"] = True

        if present_local:
            if nom_dist:
                if nom_dist.lower() != nom.lower():
                    etat = f"PRÉSENT sous le nom de distribution « {nom_dist} »"
                    details["nom_distribution"] = nom_dist
                else:
                    etat = "PRÉSENT"
                    details["nom_distribution"] = nom_dist
            else:
                if _est_module_local_projet(nom, racine):
                    etat = "LOCAL, non résolu"
                else:
                    etat = "LOCAL"
        else:
            if hors_ligne:
                etat = "ABSENT ICI"
                details["hors_ligne"] = True
            else:
                info_pypi = _interroger_pypi(nom)
                if info_pypi is False:
                    etat = "INDÉTERMINÉ"
                    details["indetermine"] = True
                elif info_pypi is None:
                    etat = "HALLUCINÉ"
                else:
                    version = info_pypi["info"]["version"]
                    etat = f"ABSENT ICI — existe sur PyPI ({version}), à installer"
                    details["version"] = version
                    details["nom_distribution"] = info_pypi["info"]["name"]

        if nom in badmodules:
            details["modulefinder"] = "manquant"
        elif nom in modules:
            details["modulefinder"] = "trouvé"

        if nom_dist and nom_dist in deps_declarees:
            details["déclaré"] = True
        elif nom in deps_declarees:
            details["déclaré"] = True
        else:
            details["déclaré"] = False

        if proches and not present_local and not _est_stdlib(nom):
            proches_list = _noms_proches(nom, tous_noms_dist)
            if proches_list:
                details["proches"] = [
                    {"nom": p, "distance": 1 - ratio}
                    for p, ratio in proches_list
                    if 1 - ratio <= SEUIL_DISTANCE
                ]

        resultats.append((nom, etat, details))

    return resultats


def _afficher_resultats(resultats: List[Tuple[str, str, Dict]]) -> int:
    """Affiche les résultats de manière lisible et retourne le code de sortie."""
    code_sortie = 0
    for nom, etat, details in resultats:
        if etat == "HALLUCINÉ":
            code_sortie = 2
            sys.stderr.write(f"*** {nom} : {etat} ***\n")
        elif etat == "INDÉTERMINÉ":
            code_sortie = 3
            sys.stderr.write(f"*** {nom} : {etat} (PyPI injoignable) ***\n")
        elif etat == "ABSENT ICI" and details.get("hors_ligne"):
            sys.stderr.write(f"{nom} : {etat} (mode hors‑ligne)\n")
        else:
            sys.stdout.write(f"{nom} : {etat}\n")

        if "version" in details:
            sys.stdout.write(f"    version disponible : {details['version']}\n")
        if "nom_distribution" in details and details["nom_distribution"] != nom:
            sys.stdout.write(f"    nom de distribution : {details['nom_distribution']}\n")
        if "proches" in details:
            for proche in details["proches"]:
                sys.stdout.write(
                    f"    proche de : {proche['nom']} (distance {proche['distance']:.0f})\n"
                )
                if proche["distance"] <= SEUIL_DISTANCE:
                    sys.stderr.write("    *** TYPOSQUAT PROBABLE ***\n")
        if "modulefinder" in details:
            sys.stdout.write(f"    modulefinder : {details['modulefinder']}\n")
        if "déclaré" in details:
            statut = "déclaré" if details["déclaré"] else "non déclaré"
            sys.stdout.write(f"    dépendance : {statut}\n")
        if details.get("optionnel"):
            sys.stdout.write("    import optionnel (try/except ImportError)\n")
        if details.get("indetermine"):
            sys.stdout.write("    état indéterminé (erreur réseau)\n")
    return code_sortie


def main() -> int:
    """Point d'entrée CLI."""
    parser = _JsonArgumentParser(
        description=__doc__.split("QUESTION")[0].strip(),
        formatter_class=RawDescriptionHelpFormatter,
        epilog="Exemple : verifier_imports.py mon_fichier.py --racine . --proches",
    )
    parser.add_argument("fichier", help="Fichier Python à vérifier")
    parser.add_argument(
        "--racine",
        help="Racine du projet (défaut: répertoire de l'outil)",
        default=RACINE,
    )
    parser.add_argument(
        "--hors-ligne",
        action="store_true",
        help="Ne pas interroger PyPI",
    )
    parser.add_argument(
        "--proches",
        action="store_true",
        help="Afficher les noms proches",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON",
    )

    args = parser.parse_args()

    chemin = Path(args.fichier)
    if not chemin.exists():
        msg = f"La cible n'existe pas : {chemin}"
        sys.stderr.write(msg + "\n")
        if args.json:
            json.dump({"erreur": msg, "denominateur": 0}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 1
    if not chemin.is_file():
        msg = f"La cible n'est pas un fichier : {chemin}"
        sys.stderr.write(msg + "\n")
        if args.json:
            json.dump({"erreur": msg, "denominateur": 0}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 1

    try:
        resultats = verifier(
            chemin_fichier=args.fichier,
            racine=args.racine,
            hors_ligne=args.hors_ligne,
            proches=args.proches,
        )
    except BinaryFileError as be:
        msg = str(be)
        sys.stderr.write(msg + "\n")
        if args.json:
            json.dump({"erreur": msg, "denominateur": 0}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 2
    except SourceIllisible as si:
        msg = str(si)
        sys.stderr.write(msg + "\n")
        if args.json:
            json.dump({"erreur": msg, "denominateur": 0}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 1
    except Exception as e:
        msg = f"Erreur inattendue lors de l'analyse : {e}"
        sys.stderr.write(msg + "\n")
        if args.json:
            json.dump({"erreur": msg, "denominateur": 0}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 1

    if args.json:
        contrat = {
            "QUESTION": "ces imports existent-ils, et si non, que faut-il en faire ?",
            "MESURE": "ast pour les extraire sans exécuter ; find_spec pour la présence locale ; API JSON de PyPI pour l'existence au monde ; difflib pour le nom proche ; modulefinder pour croiser avec pyproject.toml",
            "HYPOTHÈSES": "le fichier est du Python valide ; PyPI est joignable en mode connecté ; pyproject.toml est valide si présent",
            "LIMITES": "un 200 sur PyPI ne prouve PAS l'innocuité — slopsquatting ; le nom d'import diffère parfois du nom de distribution ; un import dans un try/except ImportError est légitime ; les imports dynamiques échappent ; modulefinder rate les imports dynamiques et rapporte les imports conditionnels même non exécutés",
            "CONTRE-EXEMPLE": "fastapi_utils classé ABSENT par la mesure locale existe en 0.8.0 sur PyPI — accuser un import légitime est la faute que cet outil doit éviter",
            "DOMAINE": "fichiers Python, imports statiques, pyproject.toml valide",
        }
        denominateur = len(resultats)
        if denominateur == 0:
            sys.stderr.write("Aucun import à examiner (dénominateur nul).\n")
            json.dump(
                {"contrat": contrat, "denominateur": 0, "resultats": []},
                sys.stdout,
                ensure_ascii=False,
                indent=2,
            )
            sys.stdout.write("\n")
            return 3

        json.dump(
            {
                "contrat": contrat,
                "denominateur": denominateur,
                "resultats": [
                    {"nom": nom, "état": etat, "détails": details}
                    for nom, etat, details in resultats
                ],
            },
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0
    else:
        return _afficher_resultats(resultats)


if __name__ == "__main__":
    raise SystemExit(main())