#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QUESTION
    Cette regex est-elle valide ?
MESURE
    On tente de compiler la pattern avec re.compile et on attrape toute
    re.error (alias re.PatternError). Si aucune exception n'est levée,
    la regex est considérée comme valide.
HYPOTHÈSES
    - La pattern est fournie sous forme de chaîne UTF-8.
    - L'interpréteur est Python 3.14.7 avec le module re standard.
    - Aucune modification du module re n'est effectuée à l'exécution.
LIMITES
    - Ne vérifie pas la compatibilité avec d'autres moteurs de regex.
    - Ne teste pas l'exécution effective de la regex (correspondance,
      substitution, etc.).
    - Ne détecte pas les erreurs qui ne surviennent qu'à l'exécution
      (ex. lookbehind de longueur variable).
CONTRE-EXEMPLES
    La pattern "(?<=a*)b" compile sans erreur mais lève une
    re.error lors de la recherche d'une correspondance à cause d'un
    lookbehind de longueur variable. Cet outil la déclarera valide.
DOMAINE
    Toute pattern regex destinée à être utilisée avec le module
    re de Python 3.14.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Tuple, List, Dict, Any

__all__: list[str] = ["est_regex_valide", "main"]

RACINE: Path = Path(__file__).resolve().parent


def est_regex_valide(pattern: str) -> Tuple[bool, str | None]:
    """
    Détermine si une pattern de regex est valide pour le module re.

    Retourne un tuple (valide, message_erreur) où :
        - valide est True si la compilation réussit,
        - message_erreur contient l'exception re.error sous forme de chaîne
          si la compilation échoue, sinon None.
    """
    try:
        re.compile(pattern)
        return True, None
    except re.error as exc:  # pragma: no cover - simple interception
        return False, str(exc)


def _build_result(
    pattern: str,
    valide: bool,
    message: str | None,
) -> Dict[str, Any]:
    """
    Construit le dictionnaire résultat destiné à la sortie JSON ou à l'affichage.
    """
    examines: List[str] = [pattern]
    examines_tronques: bool = False
    if len(examines) > 200:
        examines = examines[:200]
        examines_tronques = True

    return {
        "contrat": {
            "QUESTION": "Cette regex est-elle valide ?",
            "MESURE": "On tente de compiler la pattern avec re.compile et on attrape toute re.error.",
            "HYPOTHÈSES": (
                "La pattern est fournie sous forme de chaîne UTF-8 ; "
                "l'environnement utilise la version de re de l'interpréteur Python 3.14.7."
            ),
            "LIMITES": (
                "Ne vérifie pas la compatibilité avec d'autres moteurs de regex ; "
                "ne teste pas l'exécution effective de la regex."
            ),
            "CONTRE-EXEMPLES": (
                "Une pattern qui compile mais provoque une erreur d'exécution lors de la "
                "correspondance (ex. lookbehind de longueur variable) sera considérée "
                "comme valide par cet outil bien qu'elle lève une exception à l'utilisation."
            ),
            "DOMAINE": (
                "Toute pattern regex destinée à être utilisée avec le module "
                "re de Python 3.14."
            ),
        },
        "denominateur": 1,
        "examines": examines,
        "examines_tronques": examines_tronques,
        "resultat": {
            "valide": valide,
            "message": message if not valide else None,
        },
    }


def main() -> int:
    """
    Point d'entrée de l'outil.
    Retourne un code de sortie conforme aux spécifications.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Vérifie la validité d'une expression régulière Python.",
        epilog="Exemple : python validateur_regex.py '(?P<year>\\d{4})-(?P<month>\\d{2})-(?P<day>\\d{2})'",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON unique sur stdout (au lieu du texte lisible).",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        metavar="CHEMIN",
        help="Surcharge la racine du projet et l'ajoute à sys.path si nécessaire.",
    )
    parser.add_argument(
        "pattern",
        nargs="?",
        help="La pattern de regex à valider.",
    )
    args = parser.parse_args()

    if args.racine is not None:
        global RACINE
        RACINE = args.racine.resolve()
        # Si nous devions importer une cible, nous insérerions RACINE en tête de sys.path.
        # Aucun import de cible n'est effectué dans cet outil.

    if args.pattern is None:
        sys.stderr.write("Aucun motif de regex fourni.\n")
        return 2  # code d'erreur d'utilisation

    denominateur = 1
    if denominateur == 0:
        sys.stderr.write("Aucun élément à examiner.\n")
        return 3

    valide, message = est_regex_valide(args.pattern)
    result = _build_result(args.pattern, valide, message)

    if args.json:
        # Une seule objet JSON sur stdout, rien d'autre.
        json.dump(result, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        # Sortie lisible par un humain : uniquement le résultat.
        if valide:
            sys.stdout.write("Valide\n")
        else:
            # On inclut le message d'erreur pour que l'utilisateur sache pourquoi.
            sys.stdout.write(f"Invalide : {message}\n")

    # Code de sortie : 0 = rien à signaler (valide), non nul = défaut trouvé (invalide).
    return 0 if valide else 1


if __name__ == "__main__":
    raise SystemExit(main())