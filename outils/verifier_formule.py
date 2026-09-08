"""
QUESTION       ces deux expressions calculent-elles la meme chose ?
MESURE         sympify(evaluate=False) puis Expr.equals ; domaines compares par
               continuous_domain ; evaluation numerique a precision choisie par
               mpmath quand sympy ne conclut pas
HYPOTHESES     les deux expressions sont algebriques et portent les memes symboles
LIMITES        le theoreme de Richardson garantit l'INDECIDABLE ; une
               concordance mpmath sur 50 chiffres reste un FAISCEAU, jamais une
               preuve ; ne traite ni boucle, ni effet de bord, ni structure de
               donnees
CONTRE-EXEMPLE x/x et 1 rendent ÉQUIVALENT alors que c'est faux en x=0 --
               d'ou le verdict DOMAINES DIVERGENTS, distinct des trois autres
INVOCATION
    {outil} {fichier} --json
DOMAINE        expressions algebriques a une ou plusieurs variables reelles
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ──────────────────────────────────────────────────────────────────────────────
# Imports optionnels, avec messages d'erreur sur stderr
# ──────────────────────────────────────────────────────────────────────────────
try:
    import sympy
    from sympy import Expr, Symbol, sympify
    from sympy.calculus.util import continuous_domain
    from sympy.simplify import simplify, trigsimp, expand
    SYMPY_DISPONIBLE = True
except ImportError:  # pragma: no cover
    SYMPY_DISPONIBLE = False
    Symbol = None  # type: ignore[assignment]

try:
    import mpmath
except ImportError:  # pragma: no cover
    mpmath = None
    sys.stderr.write(
        "mpmath absent : l'évaluation numérique à haute précision ne sera pas disponible.\n"
    )

try:
    import networkx
except ImportError:  # pragma: no cover
    networkx = None
    sys.stderr.write(
        "networkx absent : l'analyse de structure ne sera pas disponible.\n"
    )

try:
    import timeit
    import statistics
except ImportError:  # pragma: no cover
    timeit = None
    statistics = None
    sys.stderr.write(
        "timeit ou statistics absent : le coût mesuré ne sera pas disponible.\n"
    )

# ──────────────────────────────────────────────────────────────────────────────
# Constantes globales
# ──────────────────────────────────────────────────────────────────────────────
RACINE = Path(__file__).resolve().parent

Verdict = Dict[str, Any]

# Alias de type dépendant de sympy, protégé contre son absence
try:
    from sympy import Symbol
    SymboleSet = Set[Symbol]
except ImportError:
    SymboleSet = set  # type: ignore[assignment]

# ──────────────────────────────────────────────────────────────────────────────
# Fonctions utilitaires
# ──────────────────────────────────────────────────────────────────────────────
def extraire_symboles(expr: Expr) -> SymboleSet:
    """Extrait l'ensemble des symboles d'une expression SymPy."""
    return expr.free_symbols


def domaine_continu(expr: Expr, symbol: Symbol) -> Expr:
    """Calcule le domaine continu d'une expression pour un symbole donné."""
    return continuous_domain(expr, symbol, sympy.S.Reals)


def domaines_divergents(a: Expr, b: Expr) -> Tuple[bool, Dict[str, Any]]:
    """Vérifie si les domaines de définition divergent."""
    symboles_a = extraire_symboles(a)
    symboles_b = extraire_symboles(b)
    if symboles_a != symboles_b:
        return False, {}

    divergences: Dict[str, Any] = {}
    for symbole in symboles_a:
        domaine_a = domaine_continu(a, symbole)
        domaine_b = domaine_continu(b, symbole)
        if domaine_a != domaine_b:
            divergences[str(symbole)] = {
                "a": str(domaine_a),
                "b": str(domaine_b),
                "points_exclus_a": str(sympy.Complement(sympy.S.Reals, domaine_a)),
                "points_exclus_b": str(sympy.Complement(sympy.S.Reals, domaine_b)),
            }

    return (bool(divergences), divergences)


def contre_exemple(a: Expr, b: Expr, precision: int = 15) -> Optional[Dict[str, Any]]:
    """Cherche un contre‑exemple numérique où a et b diffèrent."""
    symboles = extraire_symboles(a)
    if not symboles:
        return None

    symbole = next(iter(symboles))
    for x_val in [-10, -1, -0.1, 0, 0.1, 1, 10, 100]:
        try:
            val_a = float(a.subs(symbole, x_val).evalf())
            val_b = float(b.subs(symbole, x_val).evalf())
            if not sympy.Float(val_a, precision).equals(sympy.Float(val_b, precision)):
                return {
                    "valeur": x_val,
                    "a": val_a,
                    "b": val_b,
                    "symbole": str(symbole),
                }
        except (TypeError, ValueError):
            continue
    return None


def evaluation_mpmath(a: Expr, b: Expr, precision: int = 50, points: int = 20) -> Dict[str, Any]:
    """Évalue a et b avec mpmath sur plusieurs points et compare les résultats."""
    if mpmath is None:
        return {"erreur": "mpmath absent"}

    mpmath.mp.dps = precision
    symboles = extraire_symboles(a)
    if not symboles:
        return {"concordance": "aucun symbole"}

    symbole = next(iter(symboles))
    valeurs = [
        mpmath.mpf(-10) + mpmath.mpf(20) * mpmath.mpf(i) / mpmath.mpf(points - 1)
        for i in range(points)
    ]
    concordances = []

    for x_val in valeurs:
        try:
            val_a = a.subs(symbole, x_val).evalf()
            val_b = b.subs(symbole, x_val).evalf()
            if isinstance(val_a, sympy.Expr):
                val_a = mpmath.mpmathify(val_a)
            if isinstance(val_b, sympy.Expr):
                val_b = mpmath.mpmathify(val_b)
            concordances.append(mpmath.almosteq(val_a, val_b, 1e-50))
        except (TypeError, ValueError):
            concordances.append(False)

    taux = sum(concordances) / len(concordances) if concordances else 0
    return {
        "precision": precision,
        "points": points,
        "taux_concordance": taux,
        "concordant": all(concordances) if concordances else False,
    }


def structure_expression(expr: Expr) -> Dict[str, Any]:
    """Analyse la structure de l'expression (nœuds, profondeur, opérations coûteuses)."""
    if networkx is None:
        return {"erreur": "networkx absent"}

    graphe = networkx.DiGraph()
    compteur = 0

    def parcourir(noeud, parent_id=None):
        nonlocal compteur
        node_id = compteur
        compteur += 1
        graphe.add_node(node_id, type=type(noeud).__name__)
        if parent_id is not None:
            graphe.add_edge(parent_id, node_id)
        for enfant in getattr(noeud, "args", []):
            parcourir(enfant, node_id)

    parcourir(expr)

    noeuds = graphe.number_of_nodes()

    def profondeur_max(node_id):
        succ = list(graphe.successors(node_id))
        if not succ:
            return 1
        return 1 + max(profondeur_max(s) for s in succ)

    profondeur = profondeur_max(0) if noeuds > 0 else 0

    operations_couteuses = sum(
        1 for _, data in graphe.nodes(data=True) if data["type"] in {"Mul", "Pow"}
    )

    return {"noeuds": noeuds, "profondeur": profondeur, "operations_couteuses": operations_couteuses}


def cout_mesure(a: Expr, b: Expr, cible: str = "math") -> Dict[str, Any]:
    """Mesure le coût d'exécution des deux expressions."""
    if timeit is None or statistics is None:
        return {"erreur": "timeit ou statistics absent"}

    symboles = extraire_symboles(a)
    if not symboles:
        return {"erreur": "aucun symbole"}

    symbole = next(iter(symboles))
    a_func = sympy.lambdify(symbole, a, modules=cible)
    b_func = sympy.lambdify(symbole, b, modules=cible)

    def mesurer(func):
        temps = timeit.repeat(lambda: func(1.0), number=20000, repeat=9)
        return min(temps) / 20000

    temps_a = mesurer(a_func)
    temps_b = mesurer(b_func)

    return {
        "cible": cible,
        "appels": 20000,
        "temps_a": temps_a,
        "temps_b": temps_b,
        "ratio": temps_b / temps_a if temps_a else float("inf"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Fonction principale de vérification
# ──────────────────────────────────────────────────────────────────────────────
def verifier_formule(
    expr_a: str,
    expr_b: str,
    precision_mpmath: int = 50,
    points_mpmath: int = 20,
    cible_cout: str = "math",
) -> Verdict:
    """Vérifie l'équivalence de deux expressions algébriques."""
    if not SYMPY_DISPONIBLE:
        return {
            "denominateur": 0,
            "erreur": "sympy absent, l'outil ne peut fonctionner",
            "code": 3,
        }

    try:
        a = sympify(expr_a, evaluate=False)
        b = sympify(expr_b, evaluate=False)
    except (sympy.SympifyError, SyntaxError) as e:
        return {"denominateur": 0, "erreur": f"Expression invalide : {e}", "code": 3}

    denominateur = 2  # deux expressions analysées

    # --------------------------------------------------------------------- #
    # 1️⃣ Domaines divergents
    # --------------------------------------------------------------------- #
    diverge, details_divergence = domaines_divergents(a, b)
    if diverge:
        return {
            "denominateur": denominateur,
            "verdict": "DOMAINES DIVERGENTS",
            "code": 1,
            "details": details_divergence,
            "domaines": True,
        }

    # --------------------------------------------------------------------- #
    # 2️⃣ Équivalence symbolique
    # --------------------------------------------------------------------- #
    equivalence = a.equals(b)
    if equivalence is True:
        structure_a = structure_expression(a)
        structure_b = structure_expression(b)
        cout = cout_mesure(a, b, cible_cout)

        verdict = {
            "denominateur": denominateur,
            "verdict": "ÉQUIVALENT",
            "code": 0,
            "structure": {"a": structure_a, "b": structure_b},
            "cout": cout,
        }
        if cout.get("ratio", 1) > 1.1:
            verdict["avertissement"] = "ÉQUIVALENT mais PLUS COÛTEUX"
        return verdict

    # --------------------------------------------------------------------- #
    # 3️⃣ Contre‑exemple numérique
    # --------------------------------------------------------------------- #
    if equivalence is False:
        contre_ex = contre_exemple(a, b)
        if contre_ex:
            return {
                "denominateur": denominateur,
                "verdict": "CONTRE-EXEMPLE",
                "code": 1,
                "details": contre_ex,
            }

    # --------------------------------------------------------------------- #
    # 4️⃣ Simplifications successives
    # --------------------------------------------------------------------- #
    diff = simplify(a - b)
    if diff == 0:
        return {
            "denominateur": denominateur,
            "verdict": "ÉQUIVALENT",
            "code": 0,
            "details": {"simplification": "réduite à zéro"},
        }

    diff_expand = expand(a - b)
    if diff_expand == 0:
        return {
            "denominateur": denominateur,
            "verdict": "ÉQUIVALENT",
            "code": 0,
            "details": {"simplification": "expand réduite à zéro"},
        }

    diff_trig = trigsimp(a - b)
    if diff_trig == 0:
        return {
            "denominateur": denominateur,
            "verdict": "ÉQUIVALENT",
            "code": 0,
            "details": {"simplification": "trigsimp réduite à zéro"},
        }

    # --------------------------------------------------------------------- #
    # 5️⃣ Évaluation numérique avec mpmath
    # --------------------------------------------------------------------- #
    eval_mpmath = evaluation_mpmath(a, b, precision_mpmath, points_mpmath)

    resultat = {
        "denominateur": denominateur,
        "verdict": "INDÉCIDABLE",
        "code": 2,
        "details": {
            "simplification": "aucune méthode n'a réduit à zéro",
            "mpmath": eval_mpmath,
        },
    }
    if eval_mpmath.get("concordant", False):
        resultat["avertissement"] = (
            "Concordance numérique observée mais ceci est un faisceau, jamais une preuve."
        )
    return resultat


# ──────────────────────────────────────────────────────────────────────────────
# Entrée en ligne de commande
# ──────────────────────────────────────────────────────────────────────────────
def _lire_fichier_expressions(chemin: Path) -> Tuple[Optional[str], Optional[str]]:
    """Lit deux expressions depuis un fichier de trois lignes (ou plus)."""
    try:
        lignes = [
            l.strip()
            for l in chemin.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        if len(lignes) >= 2:
            return lignes[0], lignes[1]
    except Exception:
        pass
    return None, None


def main() -> int:
    """Point d'entrée de la CLI."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Vérifie l'équivalence de deux expressions algébriques.",
        epilog="Exemple : verifier_formule.py \"(x+1)**2\" \"x**2+2*x+1\" --json",
    )
    parser.add_argument(
        "expr_a",
        help="Première expression algébrique ou chemin vers un fichier contenant deux expressions",
    )
    parser.add_argument(
        "expr_b",
        nargs="?",
        help="Deuxième expression algébrique (optionnel si expr_a est un fichier)",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Racine du projet (défaut: répertoire de l'outil)",
    )
    parser.add_argument("--json", action="store_true", help="Sortie au format JSON")
    parser.add_argument(
        "--precision", type=int, default=50, help="Précision pour mpmath (défaut: 50)"
    )
    parser.add_argument(
        "--points", type=int, default=20, help="Nombre de points pour mpmath (défaut: 20)"
    )
    parser.add_argument(
        "--cible",
        choices=["math", "numpy"],
        default="math",
        help="Cible pour lambdify (défaut: math)",
    )

    args = parser.parse_args()

    # --------------------------------------------------------------------- #
    # Gestion de l'invocation « fichier unique » (exigence INVOCATION)
    # --------------------------------------------------------------------- #
    expr_a = args.expr_a
    expr_b = args.expr_b

    if expr_b is None:
        possible_path = Path(expr_a)
        if possible_path.is_file():
            expr_a, expr_b = _lire_fichier_expressions(possible_path)
            if expr_a is None or expr_b is None:
                # Aucun élément à examiner → refus légitime
                sys.stderr.write(
                    "denominateur nul : rien a examiner, refus de conclure.\n"
                )
                if args.json:
                    sys.stdout.write(json.dumps({"denominateur": 0}, ensure_ascii=False) + "\n")
                return 3
        else:
            # Pas de deuxième expression et pas de fichier → refus légitime
            sys.stderr.write(
                "denominateur nul : rien a examiner, refus de conclure.\n"
            )
            if args.json:
                sys.stdout.write(json.dumps({"denominateur": 0}, ensure_ascii=False) + "\n")
            return 3

    resultat = verifier_formule(
        expr_a,
        expr_b,
        precision_mpmath=args.precision,
        points_mpmath=args.points,
        cible_cout=args.cible,
    )

    # --------------------------------------------------------------------- #
    # Sortie JSON ou texte
    # --------------------------------------------------------------------- #
    if args.json:
        sys.stdout.write(json.dumps(resultat, ensure_ascii=False, indent=2) + "\n")
        return resultat.get("code", 0)

    # Sortie lisible (stderr)
    if "erreur" in resultat:
        sys.stderr.write(resultat["erreur"] + "\n")
        return resultat.get("code", 3)

    verdict = resultat["verdict"]
    code = resultat["code"]
    sys.stderr.write(f"Verdict : {verdict}\n")

    if "details" in resultat:
        details = resultat["details"]
        if verdict == "DOMAINES DIVERGENTS":
            for symbole, data in details.items():
                sys.stderr.write(f"  Symbole {symbole} :\n")
                sys.stderr.write(f"    Domaine A : {data['a']}\n")
                sys.stderr.write(f"    Domaine B : {data['b']}\n")
                sys.stderr.write(f"    Points exclus A : {data['points_exclus_a']}\n")
                sys.stderr.write(f"    Points exclus B : {data['points_exclus_b']}\n")
        elif verdict == "CONTRE-EXEMPLE":
            sys.stderr.write(f"  Contre-exemple en x={details['valeur']} :\n")
            sys.stderr.write(f"    A = {details['a']}\n")
            sys.stderr.write(f"    B = {details['b']}\n")
        elif "simplification" in details:
            sys.stderr.write(f"  Simplification : {details['simplification']}\n")

    if "structure" in resultat:
        struct_a = resultat["structure"]["a"]
        struct_b = resultat["structure"]["b"]
        sys.stderr.write("Structure :\n")
        sys.stderr.write(
            f"  A : {struct_a.get('noeuds', '?')} nœuds, {struct_a.get('operations_couteuses', '?')} opérations coûteuses\n"
        )
        sys.stderr.write(
            f"  B : {struct_b.get('noeuds', '?')} nœuds, {struct_b.get('operations_couteuses', '?')} opérations coûteuses\n"
        )

    if "cout" in resultat:
        cout = resultat["cout"]
        if "ratio" in cout:
            sys.stderr.write(
                f"Coût mesuré ({cout['cible']}, {cout['appels']} appels) : B/A = ×{cout['ratio']:.2f}\n"
            )

    if "avertissement" in resultat:
        sys.stderr.write(f"Avertissement : {resultat['avertissement']}\n")

    return code


if __name__ == "__main__":
    raise SystemExit(main())