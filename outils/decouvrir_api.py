"""decouvrir_api : exploration dynamique d'une API Python inconnue.

CONTRAT
=======

QUESTION
    comment se sert‑on de cette API que je ne connais pas ?
MESURE
    signature + docstring via ``inspect``, puis appel réel (si demandé) et
    introspection de l'objet rendu, récursivement.
HYPOTHÈSES
    l'appel est sans effet de bord ; les arguments d'essai sont fournis par
    l'appelant, jamais devinés.
LIMITES
    appeler, c'est exécuter ; le mode passif (sans ``--essai``) ne fait aucun
    appel. ``inspect.getmembers`` peut déclencher des propriétés ; un objet
    à ``__getattr__`` dynamique n'est pas énumérable.
CONTRE‑EXEMPLE
    ``interegular.parse_pattern`` n'a aucune docstring : la signature seule
    ne révèle pas que le ``Pattern`` rendu possède ``.to_fsm()`` ni que le
    ``FSM`` possède ``empty()``.
DOMAINE
    modules Python importables, appels sans effet de bord déclaré.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import importlib
import inspect
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Tuple

# ------------------------------------------------------------
# Encodage UTF‑8 pour stdout et stderr (règle 2)
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Contrat (utilisé pour JSON et sortie texte)
# ------------------------------------------------------------
_CONTRAT = {
    "QUESTION": "comment se sert‑on de cette API que je ne connais pas ?",
    "MESURE": "signature + docstring via inspect, puis appel réel et introspection de l'objet rendu, récursivement",
    "HYPOTHÈSES": "l'appel est sans effet de bord ; les arguments d'essai sont fournis par l'appelant, jamais devinés",
    "LIMITES": "appeler, c'est exécuter — le mode passif est le défaut ; getmembers déclenche les propriétés ; un objet à __getattr__ dynamique n'est pas énumérable",
    "CONTRE‑EXEMPLE": "interegular.parse_pattern n'a AUCUNE docstring : la signature seule ne dit pas que le Pattern rendu porte .to_fsm(), ni que le FSM porte empty()",
    "DOMAINE": "modules Python importables, appels sans effet de bord",
}

# ------------------------------------------------------------
# Types utilitaires
# ------------------------------------------------------------
Signature = str
DocString = str
MethodList = List[str]
OperatorList = List[str]
ResultDict = Dict[str, Any]

# ------------------------------------------------------------
# Fonctions du cœur (sans argparse, sans print)
# ------------------------------------------------------------


def _import_symbol(symbol_path: str) -> Tuple[ModuleType, Any]:
    """Importe ``module`` et renvoie le tuple (module, objet) pour ``module.sym``."""
    if "." not in symbol_path:
        raise ValueError(f"Chemin invalide : « {symbol_path} » doit contenir au moins un point.")
    module_name, attr_name = symbol_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    try:
        obj = getattr(module, attr_name)
    except AttributeError as exc:
        raise AttributeError(
            f"Objet « {attr_name} » introuvable dans le module « {module_name} »."
        ) from exc
    return module, obj


def _signature(obj: Any) -> Signature:
    """Retourne la signature sous forme de chaîne, ou « (?) » en cas d'erreur."""
    try:
        return str(inspect.signature(obj))
    except (ValueError, TypeError):
        return "(?)"


def _docstring(obj: Any) -> DocString:
    """Retourne la première ligne de docstring ou « AUCUNE »."""
    doc = inspect.getdoc(obj)
    return doc.splitlines()[0] if doc else "AUCUNE"


def _methods(obj: Any) -> MethodList:
    """Liste les méthodes publiques (sans le préfixe «_») de l'objet."""
    return sorted(name for name in dir(obj) if not name.startswith("_") and callable(getattr(obj, name, None)))


def _operators(obj: Any) -> OperatorList:
    """Détecte les opérateurs spéciaux implémentés par l'objet (instance ou classe)."""
    specials = {
        "__and__": "&",
        "__or__": "|",
        "__sub__": "-",
        "__add__": "+",
        "__xor__": "^",
        "__invert__": "~",
    }
    present = [name for name in dir(type(obj)) if name in specials]
    return sorted(specials[name] for name in present)


def _explorer_objet(obj: Any, profondeur: int, args: Tuple[Any, ...] | None = None) -> Dict[str, Any]:
    """Explore un objet : type, méthodes, opérateurs, et récursivement les méthodes appelables sans argument."""
    result: Dict[str, Any] = {
        "type_retour": type(obj).__qualname__,
        "methods": _methods(obj),
        "operators": _operators(obj),
        "args": args,
    }
    if profondeur <= 0:
        return result

    explorations = []
    for name in result["methods"]:
        method = getattr(obj, name)
        if not callable(method):
            continue
        entry = {
            "nom": name,
            "signature": _signature(method),
            "docstring": _docstring(method),
        }
        try:
            sig = inspect.signature(method)
            params = list(sig.parameters.values())
            obligatoires = [
                p
                for p in params
                if p.default is inspect.Parameter.empty
                and p.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
            ]
        except (ValueError, TypeError):
            obligatoires = []
        if obligatoires:
            entry["requiert_arguments"] = True
            entry["erreur"] = "méthode requiert des arguments"
        else:
            try:
                retour = method()
                entry["appel"] = _explorer_objet(retour, profondeur - 1)
            except Exception as exc:  # noqa: BLE001
                entry["erreur"] = f"{type(exc).__name__}: {exc}"
        explorations.append(entry)
    result["explorations"] = explorations
    return result


def decouvrir(
    symbol_path: str,
    essai: Tuple[Any, ...] | None = None,
    racine: Path | None = None,
    profondeur_max: int = 2,
) -> ResultDict:
    """Explore une API : signature, docstring et, si ``essai`` est fourni, appel réel."""
    _, obj = _import_symbol(symbol_path)

    result: ResultDict = {
        "chemin": symbol_path,
        "signature": _signature(obj),
        "docstring": _docstring(obj),
    }

    if essai is not None:
        try:
            appel = obj(*essai)
            result["appel"] = _explorer_objet(appel, profondeur_max, args=essai)
        except Exception as exc:  # noqa: BLE001
            result["appel"] = {"args": essai, "erreur": f"{type(exc).__name__}: {exc}"}
    return result


def voisins(symbol_path: str, racine: Path | None = None) -> ResultDict:
    """Propose les noms les plus proches d'un attribut introuvable."""
    try:
        _import_symbol(symbol_path)
        return {"chemin": symbol_path, "voisins": []}
    except Exception:
        if "." not in symbol_path:
            return {"chemin": symbol_path, "voisins": []}
        module_name = symbol_path.rsplit(".", 1)[0]
        try:
            module = importlib.import_module(module_name)
        except Exception:
            return {"chemin": symbol_path, "voisins": []}
        candidates = dir(module)
        target = symbol_path.rsplit(".", 1)[1]
        proches = difflib.get_close_matches(target, candidates, n=5, cutoff=0.6)
        return {"chemin": symbol_path, "voisins": proches}


def carte(
    module_name: str,
    borne: int = 100,
    racine: Path | None = None,
) -> ResultDict:
    """Retourne la surface publique d'un module, bornée à ``borne`` symboles."""
    module = importlib.import_module(module_name)
    publics = [name for name in dir(module) if not name.startswith("_")]
    total = len(publics)

    if total == 0:
        return {
            "module": module_name,
            "total_symboles": 0,
            "coupure": False,
            "symboles": [],
        }

    coupure = total > borne
    selection = publics[:borne] if coupure else publics

    entries = []
    for name in selection:
        obj = getattr(module, name)
        entries.append(
            {
                "nom": name,
                "type": type(obj).__qualname__,
                "signature": _signature(obj) if callable(obj) else None,
                "docstring": _docstring(obj) if callable(obj) else None,
            }
        )
    return {
        "module": module_name,
        "total_symboles": total,
        "coupure": coupure,
        "symboles": entries,
    }


# ------------------------------------------------------------
# Helpers pour le JSON
# ------------------------------------------------------------


def _denominateur(data: ResultDict, commande: str) -> int:
    """Calcule le dénominateur (nombre d'éléments réellement examinés)."""
    if commande == "carte":
        return data.get("total_symboles", 0)
    # pour les autres commandes, on considère qu'un seul symbole a été examiné
    return 1


# ------------------------------------------------------------
# Interface en ligne de commande
# ------------------------------------------------------------


def _parse_essai(val: str) -> Tuple[Any, ...]:
    """Convertit la chaîne fournie en tuple d'arguments via ast.literal_eval."""
    try:
        parsed = ast.literal_eval(val)
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"Essai invalide : {exc}") from exc
    if isinstance(parsed, tuple):
        return parsed
    return (parsed,)


def _afficher_exploration(exploration: Dict[str, Any], indent: int = 0) -> None:
    """Affiche récursivement une exploration d'objet."""
    prefix = "  " * indent
    print(f"{prefix}Type retour   : {exploration['type_retour']}")
    print(f"{prefix}Méthodes      : {', '.join(exploration['methods']) or '‑'}")
    print(f"{prefix}Opérateurs    : {', '.join(exploration['operators']) or '‑'}")
    for sub in exploration.get("explorations", []):
        print(f"{prefix}  .{sub['nom']}()")
        print(f"{prefix}    signature : {sub['signature']}")
        print(f"{prefix}    docstring : {sub['docstring']}")
        if "appel" in sub:
            _afficher_exploration(sub["appel"], indent + 2)
        elif "erreur" in sub:
            print(f"{prefix}    APPEL ÉCHOUÉ ({sub['erreur']})")


def _afficher_contrat() -> None:
    """Affiche le contrat de mesure en sortie texte."""
    print("\nCONTRAT DE MESURE")
    for key, val in _CONTRAT.items():
        print(f"{key}: {val}")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="decouvrir_api",
        description="Explore dynamiquement une API Python inconnue.",
        epilog="Exemple : python -m outils.decouvrir_api decouvrir interegular.parse_pattern --essai \"('a+',)\"",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine à utiliser (défaut : répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique.",
    )

    subparsers = parser.add_subparsers(dest="commande", required=True)

    sp_dec = subparsers.add_parser("decouvrir", help="Découvre une API, éventuellement en l'appelant.")
    sp_dec.add_argument("symbol", help="Chemin complet du symbole, ex. : interegular.parse_pattern")
    sp_dec.add_argument(
        "--essai",
        type=_parse_essai,
        help="Tuple d'arguments à passer à l'appel (exemple : \"('a+',)\").",
    )

    sp_vois = subparsers.add_parser("voisins", help="Propose des noms proches lorsqu'un attribut est introuvable.")
    sp_vois.add_argument("symbol", help="Chemin complet du symbole recherché.")

    sp_carte = subparsers.add_parser("carte", help="Affiche la surface publique d'un module.")
    sp_carte.add_argument("module", help="Nom du module à analyser.")
    sp_carte.add_argument(
        "--borne",
        type=int,
        default=100,
        help="Nombre maximal de symboles affichés (défaut : 100).",
    )

    args = parser.parse_args()

    try:
        if args.commande == "decouvrir":
            data = decouvrir(args.symbol, essai=args.essai, racine=args.racine)
        elif args.commande == "voisins":
            data = voisins(args.symbol, racine=args.racine)
        elif args.commande == "carte":
            data = carte(args.module, borne=args.borne, racine=args.racine)
        else:
            raise RuntimeError("Commande inconnue.")
    except Exception as exc:  # noqa: BLE001
        print(f"Erreur inattendue : {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        denom = _denominateur(data, args.commande)
        if denom == 0:
            print("Denominateur nul – aucun élément réellement examiné.", file=sys.stderr)
            return 3
        sortie = {
            "resultat": data,
            "contrat": _CONTRAT,
            "denominateur": denom,
        }
        json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
        # code de sortie : 0 si aucune erreur détectée dans le résultat
        return 0 if not data.get("erreur") else 1
    else:
        if args.commande == "decouvrir":
            print(f"Chemin          : {data['chemin']}")
            print(f"Signature       : {data['signature']}")
            print(f"Docstring       : {data['docstring']}")
            if "appel" in data:
                appel = data["appel"]
                if "erreur" in appel:
                    print(f"Appel           : ÉCHOUÉ – {appel['erreur']}")
                else:
                    print(f"Appel           : réussi avec args {appel.get('args', '?')}")
                    _afficher_exploration(appel)
        elif args.commande == "voisins":
            print(f"Chemin recherché : {data['chemin']}")
            if data["voisins"]:
                print("Proches :", ", ".join(data["voisins"]))
            else:
                print("Aucun voisin trouvé.")
        elif args.commande == "carte":
            print(f"Module          : {data['module']}")
            print(f"Total symboles  : {data['total_symboles']}")
            if data["coupure"]:
                print(f"Affichage limité aux {len(data['symboles'])} premiers symboles (borne={args.borne}).")
            for entry in data["symboles"]:
                print(f"- {entry['nom']} ({entry['type']})")
                if entry["signature"]:
                    print(f"    signature : {entry['signature']}")
                if entry["docstring"]:
                    print(f"    docstring : {entry['docstring']}")
        _afficher_contrat()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())