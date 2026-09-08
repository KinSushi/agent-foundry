#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Envoyer une alerte Sentry si nécessaire.

QUESTION
    Cette erreur doit-elle être rapportée ?
MESURE
    On examine le type de l'exception en cours (via sys.exc_info()) et on décide
    de la rapporter si elle n'est pas une interruption clavier ni une sortie système.
HYPOTHÈSES
    - Une exception en cours décrit une erreur réellement survenue.
    - Les interruptions clavier (KeyboardInterrupt) et les sorties système
      (SystemExit) ne doivent pas déclencher d'alerte.
    - Mode par défaut hors ligne ; aucun accès réseau sans --envoyer.
LIMITES
    - Aucun contexte supplémentaire (pile, variables locales, etc.) n'est pris en compte.
    - La décision repose uniquement sur le type d'exception.
CONTRE-EXEMPLES
    - Une KeyboardInterrupt lors d'un Ctrl+C ne doit pas être rapportée.
    - Une SystemExit levée délibérément ne doit pas être rapportée.
INVOCATION
    {outil} --json
DOMAINE
    Toute application Python où l'on souhaite décider d'envoyer une alerte Sentry
    à partir de l'exception courante.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

__all__ = [
    "analyser_erreur",
    "formatter_resultat_humain",
    "envoyer_alerte_sentry",
]

class ArgumentParserJSON(argparse.ArgumentParser):
    """ArgumentParser qui sort un JSON exploitable sur stdout en cas d'erreur."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._json_mode: bool = False
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        if self._json_mode:
            payload = {
                "reponse": "Refus de conclure",
                "denominateur": 0,
                "erreur": f"Erreur d'arguments : {message}",
                "examines": [],
                "examines_tronques": False,
            }
            json.dump(payload, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            raise SystemExit(3)
        super().error(message)


def analyser_erreur(
    exc_tuple: tuple[
        type[BaseException],
        BaseException,
        traceback.TracebackException | None,
    ]
) -> dict:
    """Analyse l'exception courante et retourne les éléments de décision."""
    exc_type, exc_value, exc_tb = exc_tuple
    denominateur = 1
    examines = [exc_type.__name__]
    examines_tronques = False

    devrait_rapporter = not (
        exc_type is KeyboardInterrupt or exc_type is SystemExit
    )

    return {
        "devrait_rapporter": devrait_rapporter,
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": examines_tronques,
    }


def formatter_resultat_humain(analyse: dict) -> str:
    """Produit la sortie lisible par un humain à partir de l'analyse."""
    return "Oui" if analyse["devrait_rapporter"] else "Non"


def envoyer_alerte_sentry(analyse: dict) -> None:
    """Envoie l'alerte à Sentry si disponible et demandé (non implémenté ici)."""
    try:
        import sentry_sdk  # type: ignore
    except ImportError:
        print(
            "Module sentry-sdk non disponible, envoi impossible.",
            file=sys.stderr,
        )
        return

    # Cette fonction serait appelée uniquement si --envoyer est passé.
    # Pour l'instant, on ne fait rien (mode hors ligne par défaut).
    print(
        "Mode hors ligne : alerte préparée mais non envoyée. "
        "Utilisez --envoyer pour un envoi réel.",
        file=sys.stderr,
    )
    print(
        f"Enveloppe Sentry simulée : {analyse['examines']} (taille estimée)",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    json_mode = "--json" in argv

    parser = ArgumentParserJSON(
        description="Détermine si une erreur doit être rapportée à Sentry.",
        prog="envoyer_alerte_sentry.py",
        epilog="Exemple d'appel : python -m envoyer_alerte_sentry --json",
    )
    parser._json_mode = json_mode
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON machine-lisible sur stdout.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Répertoire racine du projet (override du répertoire du script).",
    )
    parser.add_argument(
        "--envoyer",
        action="store_true",
        help="Envoie réel de l'alerte à Sentry (hors ligne par défaut).",
    )
    args = parser.parse_args(argv)

    racine = Path(__file__).resolve().parent
    if args.racine is not None:
        racine_arg = args.racine.resolve()
        if not racine_arg.is_dir():
            print(
                f"Le répertoire racine indiqué n'existe pas ou n'est pas un répertoire : {racine_arg}",
                file=sys.stderr,
            )
            if args.json:
                payload = {
                    "reponse": "Refus de conclure",
                    "denominateur": 0,
                    "erreur": f"Le répertoire racine indiqué n'existe pas ou n'est pas un répertoire : {racine_arg}",
                    "examines": [],
                    "examines_tronques": False,
                }
                json.dump(payload, sys.stdout, ensure_ascii=False)
                sys.stdout.write("\n")
            return 3
        racine = racine_arg
        if str(racine) not in sys.path:
            sys.path.insert(0, str(racine))

    exc_info = sys.exc_info()
    if exc_info[0] is None:
        analyse = {
            "devrait_rapporter": False,
            "denominateur": 0,
            "examines": [],
            "examines_tronques": False,
        }
    else:
        analyse = analyser_erreur(exc_info)

    if analyse["denominateur"] == 0:
        print(
            "Denominateur nul : rien à examiner, refus de conclure.",
            file=sys.stderr,
        )
        if args.json:
            payload = {
                "reponse": "Refus de conclure",
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
            }
            json.dump(payload, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        return 3

    if args.envoyer:
        envoyer_alerte_sentry(analyse)

    if args.json:
        contrat = {
            "QUESTION": "Cette erreur doit-elle être rapportée ?",
            "MESURE": "On examine le type de l'exception en cours (via sys.exc_info()) et on décide de la rapporter si elle n'est pas une interruption clavier ni une sortie système.",
            "HYPOTHÈSES": "- Une exception en cours décrit une erreur réellement survenue.\n- Les interruptions clavier (KeyboardInterrupt) et les sorties système (SystemExit) ne doivent pas déclencher d'alerte.\n- Mode par défaut hors ligne ; aucun accès réseau sans --envoyer.",
            "LIMITES": "- Aucun contexte supplémentaire (pile, variables locales, etc.) n'est pris en compte.\n- La décision repose uniquement sur le type d'exception.",
            "CONTRE-EXEMPLES": "- Une KeyboardInterrupt lors d'un Ctrl+C ne doit pas être rapportée.\n- Une SystemExit levée délibérément ne doit pas être rapportée.",
            "DOMAINE": "Toute application Python où l'on souhaite décider d'envoyer une alerte Sentry à partir de l'exception courante.",
        }
        payload = {
            "reponse": "Oui" if analyse["devrait_rapporter"] else "Non",
            "contrat": contrat,
            "denominateur": analyse["denominateur"],
            "examines": analyse["examines"],
            "examines_tronques": analyse["examines_tronques"],
            "enveloppe": {
                "champs": analyse["examines"],
                "taille_estimee": len(str(analyse["examines"])),
                "dsn": "masqué_en_mode_hors_ligne",
            },
        }
        json.dump(payload, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        result = formatter_resultat_humain(analyse)
        print(result)

    return 0 if not analyse["devrait_rapporter"] else 1


if __name__ == "__main__":
    raise SystemExit(main())