"""
QUESTION
Ces deux formules mathématiques sont-elles équivalentes ?

MESURE
L'outil évalue l'équivalence en utilisant :
1. Évaluation numérique avec fractions.Fraction pour une précision exacte
2. Évaluation numérique avec decimal.Decimal pour une précision configurable
3. Simplification symbolique avec sympy (si disponible) pour une preuve formelle

HYPOTHESES
- Les formules sont des expressions mathématiques valides en Python
- Les variables utilisées sont déclarées dans l'environnement fourni
- Les formules ne contiennent pas d'effets de bord

LIMITES
- Ne gère pas les expressions avec variables non déclarées
- La précision numérique peut être insuffisante pour certaines expressions
- Sans sympy, certaines équivalences algébriques ne sont pas détectées

CONTRE-EXEMPLES
- Formules équivalentes mais non détectées sans sympy (ex: x+1 et 1+x)
- Formules avec singularités (division par zéro) non gérées

INVOCATION
    {outil} '1+1' '2' --json

DOMAINE
Expressions mathématiques sans effets de bord avec variables déclarées
"""

from __future__ import annotations
import sys
import json
import argparse
import decimal
import fractions
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

RACINE = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__ = ["evaluer_equivalence", "analyser_formules", "main"]


def evaluer_numerique(formule1: str, formule2: str, variables: Dict[str, Any]) -> Tuple[bool, bool]:
    """Évalue l'équivalence numérique avec fractions et decimal."""
    try:
        f1 = eval(formule1, {"fractions": fractions, "__builtins__": {}}, variables)
        f2 = eval(formule2, {"fractions": fractions, "__builtins__": {}}, variables)
        fractions_eq = f1 == f2
    except Exception:
        fractions_eq = False

    try:
        ctx = decimal.Context(prec=28)
        variables_decimal = {
            k: decimal.Decimal(str(v)) if isinstance(v, (int, float)) else v
            for k, v in variables.items()
        }
        with decimal.localcontext(ctx):
            d1 = eval(formule1, {"decimal": decimal, "__builtins__": {}}, variables_decimal)
            d2 = eval(formule2, {"decimal": decimal, "__builtins__": {}}, variables_decimal)
            decimal_eq = d1 == d2
    except Exception:
        decimal_eq = False

    return fractions_eq, decimal_eq


def evaluer_symbolique(formule1: str, formule2: str, variables: Dict[str, Any]) -> Optional[bool]:
    """Évalue l'équivalence symbolique avec sympy si disponible."""
    try:
        import sympy
    except ImportError:
        return None

    try:
        symbols = {k: sympy.Symbol(k) for k in variables.keys()}
        expr1 = eval(formule1, {"sympy": sympy, "__builtins__": {}}, symbols)
        expr2 = eval(formule2, {"sympy": sympy, "__builtins__": {}}, symbols)
        return sympy.simplify(expr1 - expr2) == 0
    except Exception:
        return None


def evaluer_equivalence(
    formule1: str,
    formule2: str,
    variables: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    """Évalue si deux formules sont équivalentes."""
    fractions_eq, decimal_eq = evaluer_numerique(formule1, formule2, variables)
    sympy_eq = evaluer_symbolique(formule1, formule2, variables)

    resultats = {
        "fractions": fractions_eq,
        "decimal": decimal_eq,
        "sympy": sympy_eq,
    }

    if sympy_eq is not None:
        equivalence = sympy_eq
    else:
        equivalence = fractions_eq and decimal_eq

    return equivalence, resultats


def analyser_formules(
    formule1: str,
    formule2: str,
    variables: Dict[str, Any],
    json_sortie: bool = False,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """
    Analyse deux formules et détermine si elles sont équivalentes.

    Retourne un tuple (code_retour, payload) où *payload* contient les données
    nécessaires à l'affichage (ou ``None`` en cas d'erreur).
    Aucun texte n'est écrit sur *stdout* ; les diagnostics restent sur *stderr*.
    """
    try:
        compile(formule1, "<formule1>", "eval")
        compile(formule2, "<formule2>", "eval")
    except SyntaxError as e:
        print(f"Erreur de syntaxe dans les formules : {e}", file=sys.stderr)
        return 2, None

    try:
        equivalence, resultats = evaluer_equivalence(formule1, formule2, variables)
    except Exception as e:
        print(f"Erreur lors de l'évaluation : {e}", file=sys.stderr)
        return 2, None

    denominateur = sum(1 for v in resultats.values() if v is not None)

    # Gestion du refus légitime lorsque le dénominateur est nul
    if denominateur == 0:
        print("Denominateur nul : rien a examiner, refus de conclure.", file=sys.stderr)
        payload = {"denominateur": 0}
        return 3, payload

    examines = [
        {"methode": "fractions", "resultat": resultats["fractions"]},
        {"methode": "decimal", "resultat": resultats["decimal"]},
    ]
    if resultats["sympy"] is not None:
        examines.append({"methode": "sympy", "resultat": resultats["sympy"]})

    payload = {
        "equivalence": equivalence,
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": False,
        "contrat": {
            "QUESTION": "Ces deux formules mathématiques sont-elles équivalentes ?",
            "MESURE": "L'outil évalue l'équivalence en utilisant fractions, decimal et sympy (si disponible)",
            "HYPOTHESES": "Formules valides avec variables déclarées et sans effets de bord",
            "LIMITES": "Ne gère pas les variables non déclarées ou les singularités",
            "CONTRE-EXEMPLES": "Formules équivalentes non détectées sans sympy ou avec singularités",
            "DOMAINE": "Expressions mathématiques sans effets de bord avec variables déclarées",
        },
    }

    code = 0 if equivalence else 1
    return code, payload


def main() -> int:
    """Point d'entrée principal de l'outil."""
    parser = argparse.ArgumentParser(
        description="Vérifie si deux formules mathématiques sont équivalentes.",
        epilog="Exemple : validateur_formule.py 'x + y' 'y + x' --variables '{\"x\": 1, \"y\": 2}'",
    )
    parser.add_argument("formule1", help="Première formule mathématique.")
    parser.add_argument("formule2", help="Deuxième formule mathématique.")
    parser.add_argument(
        "--variables",
        type=json.loads,
        default="{}",
        help="Variables au format JSON (ex: '{\"x\": 1, \"y\": 2}').",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Répertoire racine pour les imports (défaut: répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON.",
    )

    args = parser.parse_args()

    if args.racine:
        sys.path.insert(0, str(args.racine))

    code, payload = analyser_formules(
        formule1=args.formule1,
        formule2=args.formule2,
        variables=args.variables,
        json_sortie=args.json,
    )

    if payload is None:
        return code

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        if payload.get("equivalence"):
            print("Les formules sont équivalentes.")
        else:
            print("Les formules ne sont pas équivalentes.")
        print("Résultats détaillés :", file=sys.stderr)
        for examen in payload.get("examines", []):
            print(
                f"- {examen['methode']}: {'équivalentes' if examen['resultat'] else 'non équivalentes'}",
                file=sys.stderr,
            )

    return code


if __name__ == "__main__":
    raise SystemExit(main())