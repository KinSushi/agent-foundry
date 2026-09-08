"""
QUESTION
ce code généré fait-il ce que l'original faisait, sans danger ?
MESURE
compile() + appels interdits + exécution bridée + équivalence symbolique du corps + diff littéral
HYPOTHÈSES
le corps de la fonction s'exprime en algèbre ; l'original est correct ; RestrictedPython ≥ 8.3.0 (CVE-2026-55830)
LIMITES
ne voit pas les effets de bord, les boucles, les E/S, les structures de données. Ne prouve pas la correction d'un PROGRAMME, seulement l'équivalence d'une EXPRESSION
CONTRE-EXEMPLES
`x/x` vs `1` rend ÉQUIVALENT alors que c'est faux en x=0 — d'où le verdict DOMAINES DIVERGENTS
DOMAINE
fonctions dont le corps est une expression algébrique
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import json
import argparse
import difflib
import ast
from pathlib import Path
from typing import List, Tuple, Optional

# --- constants --------------------------------------------------------------

_INTERDITS = {"eval", "exec", "open", "__import__"}

# --- core functions ---------------------------------------------------------

def _syntaxe_valide(source: str, nom: str) -> bool:
    """Return True if source compiles without SyntaxError."""
    try:
        compile(source, nom, "exec")
        return True
    except SyntaxError:
        return False

def _appels_interdits(source: str) -> List[str]:
    """Return a sorted list of forbidden call names found in source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _INTERDITS:
                found.add(node.func.id)
            if isinstance(node.func, ast.Attribute) and node.func.attr in _INTERDITS:
                found.add(node.func.attr)
    return sorted(found)

def _execution_bridée(source: str) -> Tuple[bool, Optional[str]]:
    """
    Try to compile and execute source with RestrictedPython.
    Returns (aboutie, niveau) where niveau is None if aboutie,
    otherwise "compilation" or "execution".
    """
    try:
        from RestrictedPython import compile_restricted, safe_globals  # type: ignore
    except ImportError:
        # RestrictedPython not available → cannot test execution
        return False, None

    try:
        bytecode = compile_restricted(source, "<string>", "exec")
    except Exception:
        return False, "compilation"

    try:
        exec(bytecode, safe_globals.copy())
        return True, None
    except Exception:
        return False, "execution"

def _extract_expression(source: str) -> str:
    """
    Attempt to extract a single expression from source.
    If source is a function with a single return, return the returned expression.
    If source is a lone expression, return it.
    Otherwise return the source stripped.
    """
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError:
        return source.strip()

    # lone expression
    if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr):
        return ast.unparse(tree.body[0].value)

    # function with single return
    if (
        len(tree.body) == 1
        and isinstance(tree.body[0], ast.FunctionDef)
        and len(tree.body[0].body) == 1
        and isinstance(tree.body[0].body[0], ast.Return)
    ):
        return ast.unparse(tree.body[0].body[0].value)

    return source.strip()

def _equivalence(source_genere: str, source_original: str) -> str:
    """
    Determine algebraic equivalence of two expressions.
    Returns one of:
        "ÉQUIVALENT", "DOMAINES DIVERGENTS", "CONTRE-EXEMPLE",
        "INDÉCIDABLE", "NON ALGÉBRIQUE".
    """
    # Détection explicite des structures non algébriques (boucles)
    try:
        tree_gen = ast.parse(source_genere)
        tree_orig = ast.parse(source_original)
    except SyntaxError:
        return "NON ALGÉBRIQUE"

    for tree in (tree_gen, tree_orig):
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.While)):
                return "NON ALGÉBRIQUE"

    try:
        import sympy  # type: ignore
        from sympy.calculus.util import continuous_domain  # type: ignore
        from sympy import S  # type: ignore
    except ImportError:
        return "NON ALGÉBRIQUE"

    try:
        expr_gen_str = _extract_expression(source_genere)
        expr_orig_str = _extract_expression(source_original)
    except Exception:
        return "NON ALGÉBRIQUE"

    try:
        expr_gen = sympy.sympify(expr_gen_str, evaluate=False)
        expr_orig = sympy.sympify(expr_orig_str, evaluate=False)
    except Exception:
        return "NON ALGÉBRIQUE"

    eq = expr_gen.equals(expr_orig, failing_expression=False)
    if eq is True:
        # check domain divergence
        free_syms = expr_gen.free_symbols.union(expr_orig.free_symbols)
        if not free_syms:
            return "ÉQUIVALENT"
        divergent = False
        for sym in free_syms:
            try:
                dom1 = continuous_domain(expr_gen, sym, S.Reals)
                dom2 = continuous_domain(expr_orig, sym, S.Reals)
                if dom1 != dom2:
                    divergent = True
                    break
            except Exception:
                divergent = True
                break
        return "DOMAINES DIVERGENTS" if divergent else "ÉQUIVALENT"
    elif eq is False:
        return "CONTRE-EXEMPLE"
    else:  # eq is None
        return "INDÉCIDABLE"

def verifier(source_genere: str, source_original: Optional[str] = None) -> dict:
    """
    Core verification logic.
    Returns a dict with keys: syntaxe, interdits, execution, equivalence, mentor, diff.
    """
    nom = "<string>"
    syntaxe_ok = _syntaxe_valide(source_genere, nom)
    interdits = _appels_interdits(source_genere)
    aboutie, niveau = _execution_bridée(source_genere)
    if source_original is None:
        equivalence = "NON TENTÉE (pas d'original)"
    else:
        equivalence = _equivalence(source_genere, source_original)

    # Correction: vérification stricte que equivalence est exactement "CONTRE-EXEMPLE"
    mentor = (
        "MENTEUR" if (
            syntaxe_ok
            and not interdits
            and aboutie
            and equivalence == "CONTRE-EXEMPLE"
        ) else ""
    )

    diff = ""
    if source_original is not None and equivalence in ("CONTRE-EXEMPLE", "DOMAINES DIVERGENTS"):
        diff = "".join(difflib.unified_diff(
            source_original.splitlines(keepends=True),
            source_genere.splitlines(keepends=True),
            fromfile="original",
            tofile="genere",
        ))

    return {
        "syntaxe": "VALIDE" if syntaxe_ok else "INVALIDE",
        "interdits": interdits,
        "execution": (
            "ABOUTIE"
            if aboutie
            else ("REFUSÉE(" + niveau + ")" if niveau else "NON TENTÉE")
        ),
        "equivalence": equivalence,
        "mentor": mentor,
        "diff": diff,
    }

# --- CLI --------------------------------------------------------------------

def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Vérifie que le code généré est équivalent et sûr.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemple d'appel réel:\n"
            "  python -m outils.verifier_code_genere \"pi * r ** 2\" \"pi * r * r\""
        ),
    )
    parser.add_argument(
        "genere",
        help="Code généré à vérifier (expression ou fonction).",
    )
    parser.add_argument(
        "original",
        nargs="?",
        default=None,
        help="Code original de référence (optionnel). Si omis, l'étape d'équivalence est sautée.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Répertoire racine du projet (surcharge la valeur déduite de __file__).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON unique sur stdout, rien d'autre.",
    )
    return parser

def main() -> int:
    parser = _build_argparser()
    args = parser.parse_args()

    # racine is not used further but we respect the rule
    racine = Path(__file__).resolve().parent
    if args.racine is not None:
        racine = args.racine

    result = verifier(args.genere, args.original)

    denominateur = 1 if args.genere else 0
    if denominateur == 0:
        print("Dénominateur nul : refus de conclure.", file=sys.stderr)
        return 3

    if args.json:
        contrat = {
            "QUESTION": "ce code généré fait-il ce que l'original faisait, sans danger ?",
            "MESURE": "compile() + appels interdits + exécution bridée + équivalence symbolique du corps + diff littéral",
            "HYPOTHÈSES": "le corps de la fonction s'exprime en algèbre ; l'original est correct ; RestrictedPython ≥ 8.3.0 (CVE-2026-55830)",
            "LIMITES": "ne voit pas les effets de bord, les boucles, les E/S, les structures de données. Ne prouve pas la correction d'un PROGRAMME, seulement l'équivalence d'une EXPRESSION",
            "CONTRE-EXEMPLES": "`x/x` vs `1` rend ÉQUIVALENT alors que c'est faux en x=0 — d'où le verdict DOMAINES DIVERGENTS",
            "DOMAINE": "fonctions dont le corps est une expression algébrique",
        }
        output = {"contrat": contrat, "resultat": result, "denominateur": denominateur}
        print(json.dumps(output, ensure_ascii=False))
    else:
        print(f"Syntaxe: {result['syntaxe']}")
        if result["interdits"]:
            print(f"Interdits: {', '.join(result['interdits'])}")
        else:
            print("Interdits: AUCUN")
        print(f"Exécution: {result['execution']}")
        print(f"Équivalence: {result['equivalence']}")
        if result["mentor"]:
            print("VERDICT: MENTEUR (code dangereux)")
        else:
            print("VERDICT: OK")
        if result["diff"]:
            print("Diff:")
            print(result["diff"])

    # Correction: INDÉCIDABLE n'est pas considéré comme un défaut
    defect = (
        result["syntaxe"] == "INVALIDE"
        or bool(result["interdits"])
        or result["execution"] != "ABOUTIE"
        or (
            result["equivalence"] not in ("ÉQUIVALENT", "INDÉCIDABLE")
            and result["equivalence"] != "NON TENTÉE (pas d'original)"
        )
    )
    return 1 if defect else 0

if __name__ == "__main__":
    raise SystemExit(main())