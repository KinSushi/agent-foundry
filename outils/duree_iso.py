"""QUESTION
    La chaîne fournie est‑elle une date/heure/durée ISO 8601 valide ?
MESURE
    Analyse syntaxique via `isodate` (si disponible) ou parsing manuel.
HYPOTHESES
    La chaîne ne contient pas de caractères non‑ASCII et respecte le jeu de caractères ISO 8601.
LIMITES
    Le fallback ne détecte pas les fuseaux horaires « Z » avec précision et ne supporte pas les intervalles complexes.
CONTRE-EXEMPLES
    Chaîne « 2023-13-01T00:00:00Z » (mois 13) : `isodate` signale « Invalid month », le fallback accepte ! → erreur.
INVOCATION
    {outil} isoerror {fichier} --json
DOMAINE
    Validation de dates, heures, durées simples conformes à ISO 8601 (YYYY‑MM‑DD[T]hh:mm:ss[.sss][±hh:mm|Z]).
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "main",
    "parse_args",
    "valider_iso",
    "formater_iso",
]

# --------------------------------------------------------------------------- #
# Exceptions du cœur
# --------------------------------------------------------------------------- #
class ValidationError(Exception):
    """Erreur levée quand la chaîne ISO n’est pas valide."""


class FormattingError(Exception):
    """Erreur levée quand le formatage ISO échoue."""


class DenominatorError(Exception):
    """Erreur levée quand il n’y a rien à examiner (déni de service)."""


# --------------------------------------------------------------------------- #
# Implémentations de secours (fallback) – aucune dépendance externe
# --------------------------------------------------------------------------- #
_ISO_REGEX = re.compile(
    r"""
    ^
    (?P<date>\d{4}-\d{2}-\d{2})
    T
    (?P<time>\d{2}:\d{2}:\d{2})
    (?:\.(?P<ms>\d+))?
    (?P<tz>Z|[+-]\d{2}:\d{2})?
    $
    """,
    re.VERBOSE,
)


def _fallback_valider_iso(chaine: str) -> Dict[str, Any]:
    """Validation minimale d’une chaîne ISO 8601."""
    if not chaine:
        raise DenominatorError("déni de chaîne vide")
    if "\x00" in chaine:
        raise ValidationError("entrée non‑textuelle")
    m = _ISO_REGEX.match(chaine)
    if not m:
        return {"ok": False, "erreur": "format ISO invalide"}
    # vérifications simples de mois et jour
    year, month, day = map(int, m.group("date").split("-"))
    hour, minute, second = map(int, m.group("time").split(":"))
    try:
        datetime.datetime(year, month, day, hour, minute, second)
    except ValueError as exc:
        return {"ok": False, "erreur": str(exc)}
    # fuseau horaire basique
    tz = m.group("tz")
    if tz and tz != "Z":
        # vérifier que le format ±hh:mm est correct
        if not re.fullmatch(r"[+-]\d{2}:\d{2}", tz):
            return {"ok": False, "erreur": "fuseau horaire hors limites"}
    return {"ok": True, "erreur": None}


def _fallback_formater_iso(
    dt: datetime.datetime, *, séparateur: str = "T", millisecondes: int = 0, tz: Optional[str] = None
) -> str:
    """Formate un datetime en ISO 8601 sans dépendance externe."""
    if not isinstance(dt, datetime.datetime):
        raise FormattingError("objet fourni n’est pas un datetime")
    if millisecondes < 0 or millisecondes > 6:
        raise FormattingError("le nombre de décimales ms doit être entre 0 et 6")
    base = dt.strftime(f"%Y-%m-%d{séparateur}%H:%M:%S")
    if millisecondes:
        ms = f"{dt.microsecond // 1000:03d}"[:millisecondes]
        base += f".{ms}"
    if tz is None:
        if dt.tzinfo is None:
            return base
        offset = dt.utcoffset()
        if offset is None:
            return base
        total_seconds = int(offset.total_seconds())
        sign = "+" if total_seconds >= 0 else "-"
        hh = abs(total_seconds) // 3600
        mm = (abs(total_seconds) % 3600) // 60
        tz = f"{sign}{hh:02d}:{mm:02d}"
    elif tz.upper() == "Z":
        tz = "Z"
    base += tz
    return base


# --------------------------------------------------------------------------- #
# API publique du cœur
# --------------------------------------------------------------------------- #
def valider_iso(chaine: str) -> Dict[str, Any]:
    """
    Valide une chaîne ISO 8601.

    Retourne un dictionnaire ``{'ok': bool, 'erreur': str|None}``.
    Lève : ValidationError, DenominatorError.
    """
    # tentative d’utiliser la bibliothèque tierce si disponible
    spec = importlib.util.find_spec("isodate")
    if spec is not None:
        try:
            import isodate  # type: ignore

            try:
                isodate.parse_datetime(chaine)
                return {"ok": True, "erreur": None}
            except Exception as exc:  # pragma: no cover
                return {"ok": False, "erreur": str(exc)}
        except Exception:  # pragma: no cover
            # si l’import échoue ou que la fonction lève, on retombe sur le fallback
            pass
    # fallback pure stdlib
    return _fallback_valider_iso(chaine)


def formater_iso(
    dt: datetime.datetime,
    *,
    séparateur: str = "T",
    millisecondes: int = 0,
    tz: Optional[str] = None,
) -> str:
    """
    Formate un objet :class:`datetime.datetime` en chaîne ISO 8601.

    Lève : FormattingError.
    """
    spec = importlib.util.find_spec("isodate")
    if spec is not None:
        try:
            import isodate  # type: ignore

            try:
                # isodate.format_datetime ne supporte pas le séparateur personnalisé,
                # on utilise le fallback dans ce cas.
                if séparateur != "T" or millisecondes != 0 or tz is not None:
                    raise NotImplementedError
                return isodate.format_datetime(dt)
            except Exception:
                # on retombe sur le fallback
                pass
        except Exception:  # pragma: no cover
            pass
    return _fallback_formater_iso(dt, séparateur=séparateur, millisecondes=millisecondes, tz=tz)


# --------------------------------------------------------------------------- #
# Analyseur d’arguments
# --------------------------------------------------------------------------- #
def _parent_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="produire la sortie au format JSON",
    )
    parent.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="chemin racine à utiliser (défaut: répertoire du script)",
    )
    return parent


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parent = _parent_parser()

    parser = argparse.ArgumentParser(
        description="Outil de validation et de formatage ISO 8601.",
        epilog="Exemple : python duree_iso.py isoerror 2023-07-15T13:45:30Z --json",
        parents=[parent],
    )
    parser.add_argument("--version", action="version", version="duree_iso 1.0")
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # isoerror
    sp_isoerror = subparsers.add_parser(
        "isoerror",
        help="valider une chaîne ISO 8601",
        parents=[parent],
        description="Valide la chaîne fournie selon la norme ISO 8601.",
    )
    sp_isoerror.add_argument("chaine", type=str, help="chaîne à tester")
    sp_isoerror.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="afficher les détails de l’erreur sur stderr",
    )

    # isostrf
    sp_isostrf = subparsers.add_parser(
        "isostrf",
        help="formater un datetime en ISO 8601",
        parents=[parent],
        description="Formate la date/heure fournie au format ISO 8601.",
    )
    sp_isostrf.add_argument(
        "datetime",
        type=str,
        help="date/heure au format ISO 8601 ou timestamp (secondes depuis epoch)",
    )
    sp_isostrf.add_argument(
        "--sep",
        dest="séparateur",
        default=argparse.SUPPRESS,
        help="séparateur entre date et heure (défaut: T)",
    )
    sp_isostrf.add_argument(
        "--ms",
        dest="millisecondes",
        type=int,
        default=argparse.SUPPRESS,
        help="nombre de décimales de millisecondes (0‑6)",
    )
    sp_isostrf.add_argument(
        "--tz",
        dest="tz",
        type=str,
        default=argparse.SUPPRESS,
        help="fuseau horaire à appliquer (ex: +02:00 ou Z)",
    )

    return parser.parse_args(argv)


# --------------------------------------------------------------------------- #
# Gestion de la sortie JSON et du code de retour
# --------------------------------------------------------------------------- #
def _sortie_json(data: dict[str, Any]) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def _imprimer_erreur(msg: str) -> None:
    sys.stderr.write(msg + "\n")


# --------------------------------------------------------------------------- #
# Entrée principale
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    # 2. Forcer l’encodage UTF‑8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    try:
        args = parse_args(argv)

        # 6. Gestion du paramètre --racine
        racine = Path(__file__).resolve().parent
        if hasattr(args, "racine"):
            p = Path(args.racine)
            racine = p if p.is_absolute() else racine / p

        # Dispatcher selon la sous‑commande
        if args.commande == "isoerror":
            return _cli_isoerror(args, racine)
        elif args.commande == "isostrf":
            return _cli_isostrf(args, racine)
        else:
            _imprimer_erreur("Commande inconnue")
            return 2
    except SystemExit as exc:
        # argparse utilise SystemExit avec le code d’erreur
        return exc.code  # type: ignore
    except Exception as exc:  # pragma: no cover
        _imprimer_erreur(f"Erreur inattendue : {exc}")
        return 1


# --------------------------------------------------------------------------- #
# Implémentations CLI des sous‑commandes
# --------------------------------------------------------------------------- #
def _cli_isoerror(args: argparse.Namespace, racine: Path) -> int:
    json_mode = getattr(args, "json", False)
    verbose = getattr(args, "verbose", False)

    try:
        # 11. Gestion des entrées hostiles
        chaine = args.chaine
        if not isinstance(chaine, str):
            raise ValidationError("la chaîne doit être du texte")
        if "\x00" in chaine:
            raise ValidationError("entrée non‑textuelle")
        if chaine == "":
            raise DenominatorError("déni de chaîne vide")

        resultat = valider_iso(chaine)

        if json_mode:
            sortie = {
                "ok": resultat["ok"],
                "erreur": resultat["erreur"],
                "denominateur": 1,
                "examines_tronques": [],
            }
            _sortie_json(sortie)
        else:
            if resultat["ok"]:
                sys.stdout.write("OK\n")
            else:
                _imprimer_erreur(f"Erreur : {resultat['erreur']}")
        return 0 if resultat["ok"] else 1

    except DenominatorError as exc:
        msg = f"denominateur nul : {exc}"
        _imprimer_erreur(msg)
        if json_mode:
            _sortie_json({"denominateur": 0, "examines_tronques": []})
        return 3
    except ValidationError as exc:
        if json_mode:
            _sortie_json(
                {
                    "ok": False,
                    "erreur": str(exc),
                    "denominateur": 1,
                    "examines_tronques": [],
                }
            )
        else:
            _imprimer_erreur(f"Erreur : {exc}")
        return 1
    except Exception as exc:  # pragma: no cover
        _imprimer_erreur(f"Erreur interne : {exc}")
        return 1


def _cli_isostrf(args: argparse.Namespace, racine: Path) -> int:
    json_mode = getattr(args, "json", False)

    try:
        texte = args.datetime
        if not isinstance(texte, str):
            raise FormattingError("l’entrée doit être une chaîne")
        if "\x00" in texte:
            raise FormattingError("entrée non‑textuelle")
        # Détermination du datetime
        try:
            # Essai d’interpréter comme timestamp
            ts = float(texte)
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        except ValueError:
            # Remplacement du Z par +00:00 pour fromisoformat
            iso_str = texte.replace("Z", "+00:00")
            try:
                dt = datetime.datetime.fromisoformat(iso_str)
            except ValueError as exc:
                raise FormattingError(str(exc))

        # Options
        séparateur = getattr(args, "séparateur", "T")
        millisecondes = getattr(args, "millisecondes", 0)
        tz = getattr(args, "tz", None)

        iso = formater_iso(
            dt,
            séparateur=séparateur,
            millisecondes=millisecondes,
            tz=tz,
        )

        if json_mode:
            _sortie_json({"iso": iso, "denominateur": 1, "examines_tronques": []})
        else:
            sys.stdout.write(iso + "\n")
        return 0

    except FormattingError as exc:
        if json_mode:
            _sortie_json(
                {"iso": None, "erreur": str(exc), "denominateur": 1, "examines_tronques": []}
            )
        else:
            _imprimer_erreur(f"Erreur : {exc}")
        return 1
    except Exception as exc:  # pragma: no cover
        _imprimer_erreur(f"Erreur interne : {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())