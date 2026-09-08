"""analyser_portees.py
Outil d’analyse statique des portées Python.

QUESTION
    À quelle portée appartient chaque nom, et quelles fermetures capturent
    variable qui évolue ?
MESURE
    Utilise ``symtable.symtable`` : arbre des portées, classification de chaque
    symbole.
HYPOTHÈSES
    Le source est du Python analysable ; les règles de portée sont celles de
    l’interpréteur exécutant cet outil.
LIMITES
    Ne voit pas les noms créés à l’exécution (``setattr``, ``exec``,
    ``globals()[...]``) ; une capture libre n’est pas toujours un bug ;
    les portées « annotation » de Python 3.14 ne sont pas comptées comme fonctions.
CONTRE‑EXEMPLE
    ``lambda: i`` et ``lambda i=i: i`` ont le même arbre AST mais des portées
    différentes ; l'AST ne peut pas les distinguer.
DOMAINE
    Source Python, portées statiques.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Iterable, List, Tuple, Dict, Any, Optional, Set

# ------------------------------------------------------------
# Encodage UTF‑8 pour stdout (règle 2)
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Imports de la stdlib uniquement (règle 1)
# ------------------------------------------------------------
try:
    import symtable
    import ast
except ImportError as exc:
    print(f"Erreur d’importation de symtable ou ast : {exc}", file=sys.stderr)
    sys.exit(1)

__all__ = [
    "charger_source",
    "analyser_symtable",
    "extraire_portees",
    "extraire_captures",
    "extraire_morts",
    "valider_source",
    "calculer_metrics",
]

# ----------------------------------------------------------------------
# Fonctions utilitaires
# ----------------------------------------------------------------------
def charger_source(chemin: Path) -> str:
    """Lit le fichier source et renvoie son texte."""
    try:
        return chemin.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Impossible de lire {chemin}: {exc}") from exc


def analyser_symtable(source: str, nom: str) -> symtable.SymbolTable:
    """Construit la table des symboles du source."""
    return symtable.symtable(source, nom, "exec")


def extraire_portees(st: symtable.SymbolTable) -> List[dict]:
    """Retourne une représentation récursive des portées."""
    def rec(table: symtable.SymbolTable) -> dict:
        return {
            "type": table.get_type(),
            "name": table.get_name(),
            "lineno": table.get_lineno(),
            "symbols": [s.get_name() for s in table.get_symbols()],
            "children": [
                rec(c) for c in table.get_children()
                if c.get_type() != "annotation"
            ],
        }

    return [rec(st)]


def _get_assignment_lineno(symbol_name: str, ancestors: List[symtable.SymbolTable]) -> Optional[int]:
    """Retourne le numéro de ligne de la première affectation du symbole dans les ancêtres."""
    for ancestor in reversed(ancestors):
        for sym in ancestor.get_symbols():
            if sym.get_name() == symbol_name and sym.is_assigned():
                return _find_assignment_lineno_in_ast(symbol_name, ancestor)
    return None


def _find_assignment_lineno_in_ast(symbol_name: str, table: symtable.SymbolTable) -> Optional[int]:
    """Trouve le numéro de ligne de la première affectation du symbole dans la portée donnée."""
    try:
        source = charger_source(Path(table.get_name()))
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return None

    class AssignmentVisitor(ast.NodeVisitor):
        def __init__(self):
            self.lineno = None

        def _check_target(self, node):
            if isinstance(node, ast.Name) and node.id == symbol_name:
                self.lineno = node.lineno
                return True
            return False

        def visit_Assign(self, node):
            for target in node.targets:
                if self._check_target(target):
                    return
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            if self._check_target(node.target):
                return
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            if self._check_target(node.target):
                return
            self.generic_visit(node)

        def visit_For(self, node):
            if self._check_target(node.target):
                return
            self.generic_visit(node)

        def visit_With(self, node):
            for item in node.items:
                if item.optional_vars and self._check_target(item.optional_vars):
                    return
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            if node.name == symbol_name:
                self.lineno = node.lineno
                return
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node):
            if node.name == symbol_name:
                self.lineno = node.lineno
                return
            self.generic_visit(node)

        def visit_ClassDef(self, node):
            if node.name == symbol_name:
                self.lineno = node.lineno
                return
            self.generic_visit(node)

    visitor = AssignmentVisitor()
    visitor.visit(tree)
    return visitor.lineno


def is_assigned_after_lambda(symbol_name: str, lambda_lineno: int, ancestors: List[symtable.SymbolTable]) -> bool:
    """Vérifie si le symbole est affecté après la lambda dans une portée englobante."""
    assignment_lineno = _get_assignment_lineno(symbol_name, ancestors)
    if assignment_lineno is None:
        return False
    return assignment_lineno > lambda_lineno


def extraire_captures(st: symtable.SymbolTable) -> List[Tuple[int, int, str, List[str]]]:
    """
    Retourne les captures libres des lambdas qui sont affectées dans la portée englobante.

    Chaque élément est (lineno, order, nom_scope, [symboles_libres]).
    """
    captures: List[Tuple[int, int, str, List[str]]] = []

    def rec(table: symtable.SymbolTable, ancestors: List[symtable.SymbolTable]) -> None:
        if table.get_type() == "lambda":
            lambda_lineno = table.get_lineno()
            libres = [
                s.get_name()
                for s in table.get_symbols()
                if s.is_free() and is_assigned_after_lambda(s.get_name(), lambda_lineno, ancestors)
            ]
            if libres:
                parent = ancestors[-1] if ancestors else None
                if parent:
                    siblings = [
                        c
                        for c in parent.get_children()
                        if c.get_type() == "lambda" and c.get_lineno() == lambda_lineno
                    ]
                else:
                    siblings = []
                try:
                    order = siblings.index(table) + 1
                except ValueError:
                    order = 1
                captures.append((lambda_lineno, order, table.get_name(), libres))
        for child in table.get_children():
            rec(child, ancestors + [table])

    rec(st, [])
    return captures


def extraire_morts(st: symtable.SymbolTable) -> List[Tuple[str, str]]:
    """
    Retourne les symboles locaux assignés mais jamais référencés dans une portée locale.

    Chaque élément est (nom_portée, nom_symbole).
    """
    morts: List[Tuple[str, str]] = []

    def rec(table: symtable.SymbolTable) -> None:
        if table.get_type() in ("function", "lambda"):
            for s in table.get_symbols():
                if (
                    s.is_assigned()
                    and not s.is_referenced()
                    and not s.is_parameter()
                    and not s.is_global()
                    and not s.is_nonlocal()
                ):
                    morts.append((table.get_name(), s.get_name()))
        for child in table.get_children():
            rec(child)

    rec(st)
    return morts


def valider_source(source: str, nom: str) -> Dict[str, Any]:
    """
    Vérifie la validité du source en utilisant symtable.symtable.
    Retourne un dictionnaire avec le résultat de la validation et les temps de mesure.
    """
    start_symtable = time.perf_counter()
    try:
        symtable.symtable(source, nom, "exec")
        symtable_valid = True
    except SyntaxError:
        symtable_valid = False
    end_symtable = time.perf_counter()
    symtable_time = end_symtable - start_symtable

    start_compile = time.perf_counter()
    try:
        compile(source, nom, "exec")
        compile_valid = True
    except SyntaxError:
        compile_valid = False
    end_compile = time.perf_counter()
    compile_time = end_compile - start_compile

    return {
        "valide": symtable_valid,
        "symtable_time": symtable_time,
        "compile_time": compile_time,
        "gain": compile_time / symtable_time if symtable_time > 0 else float("inf"),
    }


def calculer_metrics(st: symtable.SymbolTable) -> Tuple[int, List[str]]:
    """
    Calcule le nombre total de symboles examinés (dénominateur) et la liste
    des noms de ces symboles (examines). La liste est triée de façon stable.
    """
    noms: Set[str] = set()

    def rec(table: symtable.SymbolTable) -> None:
        for s in table.get_symbols():
            noms.add(s.get_name())
        for child in table.get_children():
            rec(child)

    rec(st)
    liste = sorted(noms)
    return len(liste), liste


# ------------------------------------------------------------
# Affichage
# ------------------------------------------------------------
def _afficher_humain(captures: List[Tuple[int, int, str, List[str]]], denom: int) -> None:
    if not captures:
        print("Aucune capture libre détectée.")
    else:
        for lineno, order, nom, libres in captures:
            print(f"Ligne {lineno} – lambda #{order} « {nom} » capture libre : {libres}")
            print("    *** la fermeture verra la DERNIÈRE valeur ***")
    print(f"Dénominateur (éléments réellement examinés) : {denom}")


def _afficher_json(
    commande: str,
    resultat: dict,
    contrat: dict,
    denom: int,
    examines: List[str],
    examines_tronques: int,
) -> None:
    payload = {
        "commande": commande,
        "resultat": resultat,
        "contrat": contrat,
        "denominateur": denom,
        "examines": examines,
        "examines_tronques": examines_tronques,
    }
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)


# ------------------------------------------------------------
# CLI – uniquement dans le bloc __main__
# ------------------------------------------------------------
def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="analyser_portees.py",
        description="Analyse les portées Python et détecte les captures libres, les erreurs de portée et les variables mortes.",
        epilog="Exemple : python -m outils.analyser_portees captures mon_module.py --racine .",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine du projet (défaut : répertoire contenant cet outil).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique.",
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    sp = subparsers.add_parser("portees", help="Affiche l’arbre des portées.")
    sp.add_argument("fichier", type=Path, help="Fichier source Python à analyser.")

    sc = subparsers.add_parser("captures", help="Détecte les captures libres des lambdas.")
    sc.add_argument("fichier", type=Path, help="Fichier source Python à analyser.")

    sv = subparsers.add_parser("valider", help="Valide les règles de portée (équivalent compile).")
    sv.add_argument("fichier", type=Path, help="Fichier source Python à analyser.")

    sm = subparsers.add_parser("morts", help="Liste les variables locales assignées mais jamais lues.")
    sm.add_argument("fichier", type=Path, help="Fichier source Python à analyser.")

    args = parser.parse_args()

    # --------------------------------------------------------
    # Chargement du source
    # --------------------------------------------------------
    try:
        source = charger_source(args.fichier)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # --------------------------------------------------------
    # Construction de la symtable et métriques communes
    # --------------------------------------------------------
    st = analyser_symtable(source, str(args.fichier))
    denom, examines_full = calculer_metrics(st)

    # Ajout du chemin du fichier aux éléments examinés
    examines_full = [str(args.fichier)] + examines_full
    denom = len(examines_full)

    # Gestion du zéro silencieux
    if denom == 0:
        print("Zéro élément examiné", file=sys.stderr)
        return 3

    # Limitation de la liste à 200 entrées
    examines = examines_full[:200]
    examines_tronques = max(0, len(examines_full) - 200)

    # --------------------------------------------------------
    # Contrat (pour la sortie JSON)
    # --------------------------------------------------------
    contrat = {
        "QUESTION": "à quelle portée appartient chaque nom, et quelles fermetures capturent une variable qui bouge ?",
        "MESURE": "symtable : arbre des portées, classification de chaque symbole",
        "HYPOTHÈSES": "le source est du Python analysable ; les règles de portée du langage sont celles de cet interpréteur",
        "LIMITES": "ne voit pas les noms créés à l'exécution (setattr, exec, globals()[...]) ; une capture libre n'est pas toujours un bug ; les portées « annotation » de 3.14 ne sont pas des fonctions",
        "CONTRE‑EXEMPLES": "`lambda: i` et `lambda i=i: i` ont le MÊME arbre AST et des portées DIFFÉRENTES — l'AST ne peut pas les distinguer",
        "DOMAINE": "source Python, portées statiques",
    }

    # --------------------------------------------------------
    # Exécution selon la sous‑commande
    # --------------------------------------------------------
    if args.commande == "portees":
        resultat = {"portees": extraire_portees(st)}
        if args.json:
            _afficher_json("portees", resultat, contrat, denom, examines, examines_tronques)
        else:
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
            print(f"Dénominateur (éléments réellement examinés) : {denom}")
        return 0

    if args.commande == "captures":
        captures = extraire_captures(st)
        resultat = {
            "captures": [
                {"lineno": l, "order": o, "name": n, "libres": lib}
                for l, o, n, lib in captures
            ]
        }
        if args.json:
            _afficher_json("captures", resultat, contrat, denom, examines, examines_tronques)
        else:
            _afficher_humain(captures, denom)
        return 0 if not captures else 1

    if args.commande == "valider":
        validation = valider_source(source, str(args.fichier))
        resultat = {
            "valide": validation["valide"],
            "symtable_time": validation["symtable_time"],
            "compile_time": validation["compile_time"],
            "gain": validation["gain"],
        }
        if args.json:
            _afficher_json("valider", resultat, contrat, denom, examines, examines_tronques)
        else:
            print("Le fichier est valide." if validation["valide"] else "Le fichier contient des erreurs de portée.")
            print(f"Temps symtable: {validation['symtable_time']:.6f} secondes")
            print(f"Temps compile: {validation['compile_time']:.6f} secondes")
            if validation["gain"] != float("inf"):
                print(f"Gain: {validation['gain']:.2f}x")
            else:
                print("Gain: infini (symtable a pris 0 seconde)")
            print(f"Dénominateur (éléments réellement examinés) : {denom}")
        return 0 if validation["valide"] else 2

    if args.commande == "morts":
        morts = extraire_morts(st)
        resultat = {"morts": [{"scope": s, "symbol": sym} for s, sym in morts]}
        if args.json:
            _afficher_json("morts", resultat, contrat, denom, examines, examines_tronques)
        else:
            if not morts:
                print("Aucune variable locale morte détectée.")
            else:
                for scope, sym in morts:
                    print(f"Variable morte – portée « {scope} », symbole « {sym} ».")
            print(f"Dénominateur (éléments réellement examinés) : {denom}")
        return 0 if not morts else 3

    # Si on arrive ici, c’est une situation inattendue
    print("Commande inconnue.", file=sys.stderr)
    return 255


if __name__ == "__main__":
    raise SystemExit(main())