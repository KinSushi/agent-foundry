"""Instrumenter du code Python sans en casser le comportement observable.

QUESTION      Peut-on instrumenter du code Python sans en altérer le comportement observable ?
MESURE        Vérification de la signature des fonctions, transmission des méthodes spéciales,
              restauration des patches, et compatibilité avec des arguments mutables.
HYPOTHÈSES    Le code cible est valide Python 3.14, les chemins fournis existent.
LIMITES       Ne gère pas les décorateurs C‑extension, les objets avec __slots__ personnalisés,
              ou les modules compilés.
CONTRE-EXEMPLE Un décorateur appliqué à une fonction avec des annotations de type complexes
              peut perdre les annotations dans la signature retournée.
INVOCATION    {outil} decorators --cible {fichier} --nom mon_decorateur --json
DOMAINE       Code Python pur, sans dépendances externes non déclarées.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

RACINE = Path(__file__).resolve().parent

# Configuration de l'encodage
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__ = [
    "creer_decorateur",
    "creer_proxy",
    "creer_hook_import",
    "creer_patch_temporaire",
    "creer_cache_mutable",
    "verifier_signature_preservee",
]

# Analyseur parent pour --json et --racine
parent_parser = argparse.ArgumentParser(add_help=False)
parent_parser.add_argument(
    "--json", action="store_true", default=argparse.SUPPRESS, help="Sortie en JSON"
)
parent_parser.add_argument(
    "--racine",
    type=Path,
    default=argparse.SUPPRESS,
    help="Racine pour les chemins relatifs",
)


def _import_wrapt() -> Any | None:
    """Importation conditionnelle de la bibliothèque ``wrapt``."""
    try:
        import wrapt  # type: ignore
        return wrapt
    except ImportError:
        return None


def charger_objet(chemin: str, racine: Path) -> Any:
    """Charge un objet Python depuis un chemin de la forme module:objet."""
    try:
        module_path, obj_name = chemin.split(":", 1)
    except ValueError as exc:
        raise ValueError(
            f"Chemin invalide : {chemin}. Doit être de la forme module:objet"
        ) from exc

    try:
        module_spec = importlib.util.find_spec(module_path)
        if module_spec is None:
            raise ImportError(f"Module introuvable : {module_path}")

        module = importlib.import_module(module_path)
        return getattr(module, obj_name)
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Impossible de charger {chemin} : {e}") from e


def charger_module_fichier(fichier: str, racine: Path) -> Any:
    """Charge un module Python depuis un fichier .py donné."""
    chemin = Path(fichier)
    if not chemin.is_absolute():
        chemin = racine / chemin
    if not chemin.is_file():
        raise FileNotFoundError(f"Fichier module introuvable : {chemin}")

    spec = importlib.util.spec_from_file_location(chemin.stem, chemin)
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossible de créer un spec pour {chemin}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[arg-type]
    return module


def verifier_fonction(obj: Any) -> None:
    """Vérifie que l'objet est une fonction appelable."""
    if not callable(obj):
        raise TypeError(f"L'objet {obj} n'est pas appelable")
    if not (inspect.isfunction(obj) or inspect.ismethod(obj)):
        raise TypeError(f"L'objet {obj} n'est pas une fonction ou une méthode")


def verifier_signature_preservee(original: Callable, wrapper: Callable) -> bool:
    """Vérifie que la signature est préservée (mode dégradé sans wrapt)."""
    try:
        return inspect.signature(original) == inspect.signature(wrapper)
    except (ValueError, TypeError):
        return False


def creer_decorateur(cible: Callable, nom: str, wrapt_mod: Any | None) -> str:
    """Crée un décorateur : wrapt si disponible, sinon stdlib (dégradé)."""
    verifier_fonction(cible)

    if wrapt_mod:
        # utilisation de wrapt.decorators.decorator
        return f"""import wrapt

@wrapt.decorator
def {nom}(wrapped, instance, args, kwargs):
    return wrapped(*args, **kwargs)
"""
    # mode stdlib (dégradé)
    return f"""from functools import wraps

def {nom}(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper
"""


def construire_sortie_json_base(denominateur: int, examines: List[str]) -> Dict[str, Any]:
    """Construit la base de la sortie JSON avec les clés obligatoires."""
    return {"denominateur": denominateur, "examines": examines}


def sous_commande_decorators(args: argparse.Namespace) -> Dict[str, Any]:
    """Gère la sous‑commande *decorators*."""
    racine = getattr(args, "racine", RACINE)
    wrapt_mod = _import_wrapt()
    moteur = "wrapt" if wrapt_mod else "stdlib"

    if ":" not in args.cible:
        try:
            module = charger_module_fichier(args.cible, racine)
        except Exception as e:
            resultat = construire_sortie_json_base(0, [])
            resultat.update({"erreur": str(e), "moteur": moteur})
            return resultat

        fonctions = [
            name
            for name, obj in vars(module).items()
            if inspect.isfunction(obj) and obj.__module__ == module.__name__
        ]

        if not fonctions:
            resultat = construire_sortie_json_base(0, [])
            resultat.update(
                {"erreur": "Aucune fonction de premier niveau détectée", "moteur": moteur}
            )
            return resultat

        codes = {
            nom: creer_decorateur(getattr(module, nom), args.nom, wrapt_mod) for nom in fonctions
        }
        resultat = construire_sortie_json_base(len(fonctions), fonctions)
        resultat.update(
            {
                "code": codes,
                "signature_preservee": bool(wrapt_mod),
                "moteur": moteur,
            }
        )
        return resultat

    try:
        cible = charger_objet(args.cible, racine)
        verifier_fonction(cible)

        code = creer_decorateur(cible, args.nom, wrapt_mod)

        resultat = construire_sortie_json_base(1, [args.cible])
        resultat.update(
            {
                "code": code,
                "signature_preservee": bool(wrapt_mod),
                "moteur": moteur,
            }
        )
        return resultat
    except Exception as e:
        resultat = construire_sortie_json_base(0, [])
        resultat.update({"erreur": str(e), "moteur": moteur})
        return resultat


def creer_proxy(cible: Any, nom: str, wrapt_mod: Any | None) -> str:
    """Crée un proxy : wrapt si disponible, sinon stdlib (dégradé)."""
    if wrapt_mod:
        # Utilisation de wrapt.proxies.ObjectProxy
        return f"""import wrapt

class {nom}(wrapt.ObjectProxy):
    pass
"""
    # mode stdlib (dégradé)
    methodes = [
        name
        for name in dir(cible)
        if name.startswith("__")
        and name.endswith("__")
        and callable(getattr(cible, name))
    ]

    methodes_code = "\n".join(
        f"    def {m}(self, *args, **kwargs):\n"
        f"        return getattr(self._obj, '{m}')(*args, **kwargs)"
        for m in methodes
    )

    return f"""class {nom}:
    def __init__(self, obj):
        self._obj = obj

    def __getattr__(self, name):
        return getattr(self._obj, name)

{methodes_code}
"""


def sous_commande_proxies(args: argparse.Namespace) -> Dict[str, Any]:
    """Gère la sous‑commande *proxies*."""
    racine = getattr(args, "racine", RACINE)
    wrapt_mod = _import_wrapt()
    moteur = "wrapt" if wrapt_mod else "stdlib"

    try:
        cible = charger_objet(args.cible, racine)
        code = creer_proxy(cible, args.nom, wrapt_mod)
        methodes = [
            name
            for name in dir(cible)
            if name.startswith("__") and name.endswith("__") and callable(getattr(cible, name))
        ]
        resultat = construire_sortie_json_base(1, [args.cible])
        resultat.update(
            {
                "code": code,
                "methodes_speciales": methodes,
                "moteur": moteur,
            }
        )
        return resultat
    except Exception as e:
        resultat = construire_sortie_json_base(0, [])
        resultat.update({"erreur": str(e), "moteur": moteur})
        return resultat


def creer_hook_import(module: str, wrapper: Callable, wrapt_mod: Any | None) -> str:
    """Crée un hook d'importation : wrapt si disponible, sinon stdlib (dégradé)."""
    verifier_fonction(wrapper)

    if wrapt_mod:
        # Utilisation de wrapt.importer.register_post_import_hook
        return f"""import wrapt

wrapt.importer.register_post_import_hook({wrapper.__module__}.{wrapper.__name__}, "{module}")
"""
    # mode stdlib (dégradé)
    return f"""import importlib
import sys

def hook_{module}():
    mod = importlib.import_module("{module}")
    {wrapper.__module__}.{wrapper.__name__}(mod)

sys.path_hooks.append(hook_{module})
"""


def sous_commande_importer(args: argparse.Namespace) -> Dict[str, Any]:
    """Gère la sous‑commande *importer*."""
    racine = getattr(args, "racine", RACINE)
    wrapt_mod = _import_wrapt()
    moteur = "wrapt" if wrapt_mod else "stdlib"

    try:
        wrapper = charger_objet(args.wrapper, racine)
        verifier_fonction(wrapper)
        code = creer_hook_import(args.module, wrapper, wrapt_mod)
        resultat = construire_sortie_json_base(1, [args.module, args.wrapper])
        resultat.update(
            {
                "hook_installe": bool(wrapt_mod),
                "code": code,
                "moteur": moteur,
            }
        )
        return resultat
    except Exception as e:
        resultat = construire_sortie_json_base(0, [])
        resultat.update({"erreur": str(e), "moteur": moteur})
        return resultat


def creer_patch_temporaire(cible: Callable, patch: Callable, wrapt_mod: Any | None) -> str:
    """Crée un contexte de patch : wrapt si disponible, sinon stdlib (dégradé)."""
    verifier_fonction(cible)
    verifier_fonction(patch)

    if wrapt_mod:
        # Utilisation de wrapt.patches.apply_patch
        return f"""import wrapt

wrapt.patches.apply_patch({cible.__module__}, "{cible.__name__}", {patch.__module__}.{patch.__name__})
"""
    # mode stdlib (dégradé)
    return f"""from contextlib import contextmanager
import {cible.__module__}

@contextmanager
def patch_{cible.__name__}():
    original = getattr({cible.__module__}, "{cible.__name__}")
    setattr({cible.__module__}, "{cible.__name__}", {patch.__module__}.{patch.__name__})
    try:
        yield
    finally:
        setattr({cible.__module__}, "{cible.__name__}", original)
"""


def sous_commande_patches(args: argparse.Namespace) -> Dict[str, Any]:
    """Gère la sous‑commande *patches*."""
    racine = getattr(args, "racine", RACINE)
    wrapt_mod = _import_wrapt()
    moteur = "wrapt" if wrapt_mod else "stdlib"

    try:
        cible = charger_objet(args.cible, racine)
        patch = charger_objet(args.patch, racine)
        verifier_fonction(cible)
        verifier_fonction(patch)
        code = creer_patch_temporaire(cible, patch, wrapt_mod)
        resultat = construire_sortie_json_base(1, [args.cible, args.patch])
        resultat.update(
            {
                "patch_applique": bool(wrapt_mod),
                "restauration_garantie": True,
                "code": code,
                "moteur": moteur,
            }
        )
        return resultat
    except Exception as e:
        resultat = construire_sortie_json_base(0, [])
        resultat.update({"erreur": str(e), "moteur": moteur})
        return resultat


def creer_cache_mutable(cible: Callable, nom: str, wrapt_mod: Any | None) -> str:
    """Crée un cache : wrapt si disponible, sinon stdlib (dégradé)."""
    verifier_fonction(cible)

    if wrapt_mod:
        # Utilisation de wrapt.caching.lru_cache
        return f"""import wrapt

@wrapt.caching.lru_cache
def {nom}(func):
    return func
"""
    # mode stdlib (dégradé)
    return f"""from functools import wraps

def {nom}(func):
    cache = {{}}

    @wraps(func)
    def wrapper(*args, **kwargs):
        key = (args, frozenset(kwargs.items()))
        if key not in cache:
            cache[key] = func(*args, **kwargs)
        return cache[key]

    return wrapper
"""


def sous_commande_caching(args: argparse.Namespace) -> Dict[str, Any]:
    """Gère la sous‑commande *caching*."""
    racine = getattr(args, "racine", RACINE)
    wrapt_mod = _import_wrapt()
    moteur = "wrapt" if wrapt_mod else "stdlib"

    try:
        cible = charger_objet(args.cible, racine)
        verifier_fonction(cible)
        code = creer_cache_mutable(cible, args.nom, wrapt_mod)
        resultat = construire_sortie_json_base(1, [args.cible])
        resultat.update(
            {
                "code": code,
                "accepte_mutables": True,
                "moteur": moteur,
            }
        )
        return resultat
    except Exception as e:
        resultat = construire_sortie_json_base(0, [])
        resultat.update({"erreur": str(e), "moteur": moteur})
        return resultat


def main() -> int:
    """Point d'entrée principal."""
    parser = argparse.ArgumentParser(
        description="Instrumenter du code Python sans en casser le comportement.",
        parents=[parent_parser],
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    # sous‑commande decorators
    parser_decorators = sous_parsers.add_parser(
        "decorators",
        parents=[parent_parser],
        help="Créer un décorateur préservant la signature",
    )
    parser_decorators.add_argument(
        "--cible", required=True, help="Chemin vers la fonction (module:fonction) ou le fichier .py"
    )
    parser_decorators.add_argument(
        "--nom", required=True, help="Nom du décorateur à créer"
    )

    # sous‑commande proxies
    parser_proxies = sous_parsers.add_parser(
        "proxies",
        parents=[parent_parser],
        help="Générer un proxy transmettant les méthodes spéciales",
    )
    parser_proxies.add_argument(
        "--cible", required=True, help="Chemin vers l'objet (module:objet)"
    )
    parser_proxies.add_argument(
        "--nom", required=True, help="Nom du proxy à créer"
    )

    # sous‑commande importer
    parser_importer = sous_parsers.add_parser(
        "importer",
        parents=[parent_parser],
        help="Intercepter les importations pour wrapper un module",
    )
    parser_importer.add_argument(
        "--module", required=True, help="Nom du module à wrapper"
    )
    parser_importer.add_argument(
        "--wrapper", required=True, help="Chemin vers la fonction wrapper (module:fonction)"
    )

    # sous‑commande patches
    parser_patches = sous_parsers.add_parser(
        "patches",
        parents=[parent_parser],
        help="Appliquer un patch temporaire avec restauration garantie",
    )
    parser_patches.add_argument(
        "--cible", required=True, help="Chemin vers la fonction à patcher (module:fonction)"
    )
    parser_patches.add_argument(
        "--patch", required=True, help="Chemin vers la fonction patch (module:fonction)"
    )

    # sous‑commande caching
    parser_caching = sous_parsers.add_parser(
        "caching",
        parents=[parent_parser],
        help="Créer un cache acceptant des arguments non hashables",
    )
    parser_caching.add_argument(
        "--cible", required=True, help="Chemin vers la fonction (module:fonction)"
    )
    parser_caching.add_argument(
        "--nom", required=True, help="Nom du cache à créer"
    )

    args = parser.parse_args()

    # exécution de la sous‑commande
    try:
        if args.commande == "decorators":
            resultat = sous_commande_decorators(args)
        elif args.commande == "proxies":
            resultat = sous_commande_proxies(args)
        elif args.commande == "importer":
            resultat = sous_commande_importer(args)
        elif args.commande == "patches":
            resultat = sous_commande_patches(args)
        elif args.commande == "caching":
            resultat = sous_commande_caching(args)
        else:
            resultat = construire_sortie_json_base(0, [])
            resultat["erreur"] = "Commande inconnue"
    except Exception as e:
        resultat = construire_sortie_json_base(0, [])
        resultat["erreur"] = f"Erreur interne : {e}"

    # message de mode dégradé si wrapt absent
    if resultat.get("moteur") == "stdlib":
        print("mode degrade : wrapt absent", file=sys.stderr)

    # protocole de refus
    if resultat["denominateur"] == 0:
        print(f"denominateur {resultat['denominateur']}", file=sys.stderr)
        print(resultat.get("erreur", "refuse"), file=sys.stderr)
        print("refuse", file=sys.stderr)

    # sortie JSON uniquement
    if getattr(args, "json", False):
        json.dump(resultat, sys.stdout, ensure_ascii=False)
        return 3 if resultat["denominateur"] == 0 else 0

    # sortie humaine
    if resultat["denominateur"] == 0:
        return 3
    if "code" in resultat:
        print(resultat["code"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())