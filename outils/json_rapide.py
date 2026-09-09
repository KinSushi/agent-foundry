"""QUESTION
    Quelle est la performance et la compatibilité de `json_rapide` pour manipuler du JSON en production ?
MESURE
    Temps d'exécution, consommation mémoire, et support des types natifs (datetime, UUID, numpy.ndarray).
HYPOTHESES
    - `orjson` est installé et accessible.
    - Les entrées sont valides (JSON bien formé, types supportés).
LIMITES
    - Ne gère pas les flux binaires non JSON (ex: Protobuf).
    - En mode dégradé, les types natifs lèvent `TypeError`.
CONTRE-EXEMPLES
    Un objet contenant un `set` Python : `orjson` lève `TypeError` (non sérialisable).
INVOCATION
    {outil} serialiser '{"exemple": 1}' --json
DOMAINE
    Scripts Python 3.14+ sous Linux/Windows, avec ou sans `orjson`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence, Union

# --------------------------------------------------------------------------- #
# Import conditionnel de orjson
# --------------------------------------------------------------------------- #

try:
    import orjson  # type: ignore
    _orjson_module = orjson
except ImportError:  # pragma: no cover
    _orjson_module = None

def _orjson_disponible() -> bool:
    return _orjson_module is not None

def _charger_orjson() -> Any:
    """Retourne le module orjson s'il est disponible, sinon lève OutilErreur."""
    if _orjson_module is None:
        raise OutilErreur(
            "Mode dégradé : performances réduites et types natifs non supportés.",
            code=0,
            denominateur=0,
        )
    return _orjson_module

# --------------------------------------------------------------------------- #
# Exceptions et constantes
# --------------------------------------------------------------------------- #

class OutilErreur(Exception):
    """Exception levée par le cœur en cas d'erreur.

    Attributes
    ----------
    code: int
        Code de sortie souhaité.
    message: str
        Message d'erreur à afficher sur stderr.
    denominateur: int
        Valeur du champ `denominateur` à inclure dans la sortie JSON.
    """
    def __init__(self, message: str, code: int = 1, denominateur: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.denominateur = denominateur

# --------------------------------------------------------------------------- #
# Cœur de l'outil (ne produit aucune sortie)
# --------------------------------------------------------------------------- #

def _parse_json_texte(texte: str) -> Any:
    try:
        return json.loads(texte)
    except json.JSONDecodeError as exc:
        raise OutilErreur(f"JSON invalide : {exc}", code=1)


def _serialiser_core(objet: str, options: Sequence[str]) -> Union[bytes, str]:
    """Sérialise un objet JSON en bytes (orjson) ou str (json)."""
    data = _parse_json_texte(objet)

    if _orjson_disponible():
        orjson = _charger_orjson()
        opt = 0
        for opt_name in options:
            if opt_name == "numpy":
                opt |= orjson.OPT_SERIALIZE_NUMPY
            elif opt_name == "omit_microseconds":
                opt |= orjson.OPT_OMIT_MICROSECONDS
            elif opt_name == "sort_keys":
                opt |= orjson.OPT_SORT_KEYS
        try:
            return orjson.dumps(data, option=opt)
        except Exception as exc:  # pragma: no cover
            raise OutilErreur(f"Erreur de sérialisation orjson : {exc}", code=1)
    else:
        sort = "sort_keys" in options
        try:
            return json.dumps(
                data,
                ensure_ascii=False,
                sort_keys=sort,
                indent=None,
            )
        except TypeError as exc:
            raise OutilErreur(f"Type non supporté en mode dégradé : {exc}", code=1)


def _deserialiser_core(cible: str, flux: bool) -> Any:
    """Déserialise un fichier JSON ou stdin."""
    if cible == "-":
        texte = sys.stdin.read()
    else:
        p = Path(cible)
        try:
            texte = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise OutilErreur(f"Impossible de lire le fichier : {exc}", code=1)

    if chr(0) in texte:
        raise OutilErreur("Contenu binaire détecté dans le texte.", code=1)

    if flux:
        if not _orjson_disponible():
            raise OutilErreur(
                "Le mode flux n'est pas supporté en mode dégradé.", code=1
            )
        orjson = _charger_orjson()
        try:
            return orjson.loads(texte.encode("utf-8"))
        except Exception as exc:  # pragma: no cover
            raise OutilErreur(f"Erreur de désérialisation orjson : {exc}", code=1)

    try:
        return json.loads(texte)
    except json.JSONDecodeError as exc:
        raise OutilErreur(f"JSON invalide : {exc}", code=1)


def _encoder_types_core(objet: str) -> Union[bytes, str]:
    """Encode les types natifs supportés (datetime, UUID, ndarray)."""
    return _serialiser_core(objet, [])


def _reseau_core(objet: str) -> Union[bytes, str]:
    """Prépare les données pour un envoi réseau (identique à serialiser)."""
    return _serialiser_core(objet, [])


def _compact_core(objet: str, options: Sequence[str]) -> Union[bytes, str]:
    """Compacte le JSON avec les options demandées."""
    return _serialiser_core(objet, options)

# --------------------------------------------------------------------------- #
# Interface CLI
# --------------------------------------------------------------------------- #

def _creer_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Retourner la sortie sous forme d'un unique objet JSON.",
    )
    parent.add_argument(
        "--racine",
        type=str,
        default=argparse.SUPPRESS,
        help="Chemin racine à utiliser à la place du répertoire du script.",
    )

    parser = argparse.ArgumentParser(
        description="Outil rapide de manipulation JSON.",
        epilog="Exemple : json_rapide serialiser '{\"a\":1}' --json",
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    sp = subparsers.add_parser(
        "serialiser",
        parents=[parent],
        help="Sérialiser un objet JSON.",
    )
    sp.add_argument("OBJET", help="Objet JSON à sérialiser (chaine).")
    sp.add_argument(
        "--option",
        action="append",
        choices=["numpy", "omit_microseconds", "sort_keys"],
        default=[],
        help="Options de sérialisation (peut être répété).",
    )

    sp = subparsers.add_parser(
        "deserialiser",
        parents=[parent],
        help="Désérialiser un fichier JSON ou stdin.",
    )
    sp.add_argument("FICHIER", help="Chemin du fichier JSON ou '-' pour stdin.")
    sp.add_argument(
        "--flux",
        action="store_true",
        default=False,
        help="Utiliser le mode streaming (orjson uniquement).",
    )

    sp = subparsers.add_parser(
        "encoder_types",
        parents=[parent],
        help="Encoder les types natifs supportés.",
    )
    sp.add_argument("OBJET", help="Objet JSON contenant des types natifs.")

    sp = subparsers.add_parser(
        "reseau",
        parents=[parent],
        help="Préparer le JSON pour envoi réseau.",
    )
    sp.add_argument("OBJET", help="Objet JSON à préparer.")

    sp = subparsers.add_parser(
        "compact",
        parents=[parent],
        help="Compacte le JSON avec options.",
    )
    sp.add_argument("OBJET", help="Objet JSON à compacter.")
    sp.add_argument(
        "--option",
        action="append",
        choices=["omit_microseconds", "sort_keys"],
        default=[],
        help="Options de compactage (peut être répété).",
    )

    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = _creer_parser()
    args = parser.parse_args()

    racine = Path(__file__).resolve().parent
    if hasattr(args, "racine"):
        p = Path(args.racine)
        racine = p if p.is_absolute() else racine / p

    mode_json = getattr(args, "json", False)

    if not _orjson_disponible():
        print(
            "Mode dégradé : performances réduites et types natifs non supportés.",
            file=sys.stderr,
        )

    try:
        if args.commande == "serialiser":
            resultat = _serialiser_core(args.OBJET, args.option)
        elif args.commande == "deserialiser":
            resultat = _deserialiser_core(args.FICHIER, args.flux)
        elif args.commande == "encoder_types":
            resultat = _encoder_types_core(args.OBJET)
        elif args.commande == "reseau":
            resultat = _reseau_core(args.OBJET)
        elif args.commande == "compact":
            resultat = _compact_core(args.OBJET, args.option)
        else:  # pragma: no cover
            raise OutilErreur("Commande inconnue.", code=2)

        if mode_json:
            if isinstance(resultat, (bytes, bytearray)):
                try:
                    texte = resultat.decode("utf-8")
                except UnicodeDecodeError:
                    texte = resultat.hex()
                payload = texte
            else:
                payload = resultat
            sortie = {"denominateur": 1, "resultat": payload}
            print(json.dumps(sortie, ensure_ascii=False))
            return 0
        else:
            if isinstance(resultat, (bytes, bytearray)):
                sys.stdout.buffer.write(resultat)
                if not resultat.endswith(b"\n"):
                    sys.stdout.buffer.write(b"\n")
            else:
                print(resultat)
            return 0

    except OutilErreur as err:
        if err.denominateur == 0:
            print(f"denominateur refusé : {err.message}", file=sys.stderr)
            if mode_json:
                print(json.dumps({"denominateur": 0, "erreur": err.message}, ensure_ascii=False))
            return 3
        else:
            print(err.message, file=sys.stderr)
            if mode_json:
                print(json.dumps({"denominateur": err.denominateur, "erreur": err.message}, ensure_ascii=False))
            return err.code

    except Exception as exc:  # pragma: no cover
        print(f"Erreur interne : {exc}", file=sys.stderr)
        if mode_json:
            print(json.dumps({"denominateur": 0, "erreur": str(exc)}, ensure_ascii=False))
        return 2


__all__ = [
    "_serialiser_core",
    "_deserialiser_core",
    "_encoder_types_core",
    "_reseau_core",
    "_compact_core",
    "OutilErreur",
]

if __name__ == "__main__":
    raise SystemExit(main())