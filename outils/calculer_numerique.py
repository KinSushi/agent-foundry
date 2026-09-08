"""
QUESTION      Quel est le résultat numérique de cette expression ?
MESURE        L'outil évalue l'expression via le moteur disponible (stdlib, numpy, mpmath, sympy) et retourne sa valeur numérique.
HYPOTHESES    L'expression est une chaîne Python valide et mathématiquement définie.
LIMITES       Sans bibliothèque tierce, seuls les calculs standards en virgule flottante ou décimaux simples sont possibles.
CONTRE-EXEMPLES Une expression syntaxiquement valide mais mathématiquement indéfinie (ex: 1/0) lèvera une erreur.
DOMAINE       Expressions mathématiques évaluables par Python et ses bibliothèques scientifiques.
"""
from __future__ import annotations

import sys
import math
import cmath
import decimal
import fractions
import numbers
import random
import statistics
import json
import argparse
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__ = ["evaluer_expression"]


def _construire_espace_noms() -> dict[str, Any]:
    """Construit le dictionnaire des modules standard et tierces pour l'évaluation."""
    espace: dict[str, Any] = {
        "math": math,
        "cmath": cmath,
        "decimal": decimal,
        "fractions": fractions,
        "numbers": numbers,
        "random": random,
        "statistics": statistics,
        "Decimal": decimal.Decimal,
        "Fraction": fractions.Fraction,
    }
    try:
        import numpy
        espace["numpy"] = numpy
        espace["np"] = numpy
    except ImportError:
        pass
    try:
        import mpmath
        espace["mpmath"] = mpmath
    except ImportError:
        pass
    try:
        import sympy
        espace["sympy"] = sympy
    except ImportError:
        pass
    return espace


def evaluer_expression(expression: str) -> dict[str, Any]:
    """Évalue une expression mathématique et retourne le résultat sous forme de dictionnaire."""
    if not expression or not expression.strip():
        return {"denominateur": 0, "examines": [], "examines_tronques": False}

    try:
        compile(expression, "<expression>", "exec")
    except SyntaxError as e:
        return {
            "denominateur": 1,
            "examines": [{"nom": expression, "erreur": str(e)}],
            "examines_tronques": False,
            "erreur": str(e)
        }

    espace = _construire_espace_noms()
    degrade = not all(k in espace for k in ("numpy", "mpmath", "sympy"))

    try:
        res = eval(expression, {"__builtins__": {}}, espace)
        return {
            "denominateur": 1,
            "examines": [{"nom": expression, "resultat": str(res)}],
            "examines_tronques": False,
            "resultat": str(res),
            "degrade": degrade
        }
    except Exception as e:
        return {
            "denominateur": 1,
            "examines": [{"nom": expression, "erreur": str(e)}],
            "examines_tronques": False,
            "erreur": str(e)
        }


def main() -> int:
    """Point d'entrée principal pour la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Calcule le résultat numérique d'une expression.",
        epilog="Exemple d'appel réel : python calculer_numerique.py '2+2'"
    )
    parser.add_argument("expression", type=str, help="L'expression à évaluer")
    parser.add_argument("--json", action="store_true", help="Rend un seul objet JSON sur stdout")
    parser.add_argument("--racine", type=str, help="Surcharge la racine et l'insère en tête de sys.path")

    args = parser.parse_args()

    if args.racine:
        racine = Path(args.racine).resolve()
        sys.path.insert(0, str(racine))
    else:
        racine = Path(__file__).resolve().parent
        sys.path.insert(0, str(racine))

    resultat = evaluer_expression(args.expression)

    if resultat["denominateur"] == 0:
        print("Denominateur nul : l'outil refuse de conclure.", file=sys.stderr)
        return 3

    if "erreur" in resultat:
        if args.json:
            print(json.dumps(resultat, ensure_ascii=False))
        else:
            print(f"Erreur: {resultat['erreur']}", file=sys.stderr)
        return 2

    if args.json:
        sortie = {
            "contrat": {
                "QUESTION": "Quel est le résultat numérique de cette expression ?",
                "MESURE": "Évaluation de l'expression via le moteur disponible (stdlib, numpy, mpmath, sympy).",
                "HYPOTHESES": "L'expression est syntaxiquement valide et mathématiquement définie.",
                "LIMITES": "Sans bibliothèque tierce, seuls les calculs standards sont possibles.",
                "CONTRE-EXEMPLES": "Une expression syntaxiquement invalide ou mathématiquement indéfinie lèvera une erreur.",
                "DOMAINE": "Expressions mathématiques évaluables par Python."
            },
            "denominateur": resultat["denominateur"],
            "examines": resultat["examines"],
            "examines_tronques": resultat["examines_tronques"],
            "resultat": resultat["resultat"],
            "degrade": resultat["degrade"]
        }
        print(json.dumps(sortie, ensure_ascii=False))
    else:
        print(resultat["resultat"])
        if resultat["degrade"]:
            print("Mode dégradé : bibliothèques tierces absentes.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())