"""
QUESTION      Ce code contient-il des patterns dangereux ?
MESURE        Analyse statique du code source Python 3.14 en utilisant les modules standard ast et tokenize
              pour détecter des constructions potentiellement dangereuses. Vérification optionnelle avec
              RestrictedPython en mode dégradé si disponible.
HYPOTHÈSES    Le code source est syntaxiquement valide et peut être parsé par ast.parse(). Les patterns
              dangereux incluent les appels dynamiques, les imports non contrôlés, les manipulations de
              fichiers, et les appels système.
LIMITES       Ne détecte pas les vulnérabilités dynamiques (injections SQL, XSS, etc.). Ne vérifie pas
              les dépendances tierces. Peut produire des faux positifs/négatifs.
CONTRE-EXEMPLES Un appel à __import__('module') peut être marqué comme dangereux même s'il est sécurisé
              dans un contexte spécifique. Les paramètres inexistants comme all_threads=True dans
              faulthandler.dump_traceback_later() sont détectés.
DOMAINE       Code source Python 3.14 analysé statiquement sans exécution.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tokenize
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = [
    "analyser_fichier",
    "detecter_patterns_dangereux",
    "generer_rapport_humain",
    "generer_rapport_json",
    "main",
]


def _lire_fichier(chemin: Path) -> str | None:
    """Lit un fichier en UTF‑8 avec remplacement des erreurs.
    Retourne None et écrit sur stderr en cas d’échec."""
    try:
        with chemin.open(encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"Erreur de lecture du fichier {chemin} : {e}", file=sys.stderr)
        return None


class AnalyseurAST(ast.NodeVisitor):
    def __init__(self) -> None:
        self.patterns: Dict[str, List[Tuple[int, int, str]]] = defaultdict(list)
        self.imports: Set[str] = set()
        self.denominateur = 0
        self.noms_examines: List[str] = []

    def visiter_et_compter(self, node: ast.AST) -> None:
        self.denominateur += 1
        self.generic_visit(node)

    def ajouter_pattern(self, node: ast.AST, pattern: str) -> None:
        source_segment = ast.get_source_segment("", node) or ""
        self.patterns[pattern].append((node.lineno, node.col_offset, source_segment))

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in {"exec", "eval", "compile", "__import__"}:
                self.ajouter_pattern(node, f"Appel dangereux: {node.func.id}")
            elif node.func.id == "open":
                self.ajouter_pattern(node, "Ouverture de fichier non contrôlée")
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr in {"exec", "eval", "compile"}:
                self.ajouter_pattern(node, f"Appel dangereux via attribut: {node.func.attr}")
            elif node.func.attr == "format" and isinstance(node.func.value, ast.Str):
                self.ajouter_pattern(node, "Utilisation dangereuse de str.format()")
            elif isinstance(node.func.value, ast.Name) and node.func.value.id == "faulthandler":
                if node.func.attr == "dump_traceback_later":
                    for kw in node.keywords:
                        if kw.arg == "all_threads":
                            self.ajouter_pattern(node, "Paramètre all_threads inexistant dans faulthandler.dump_traceback_later")
            elif isinstance(node.func.value, ast.Name) and node.func.value.id == "Interpreter":
                if node.func.attr == "exec":
                    for kw in node.keywords:
                        if kw.arg == "dedent":
                            self.ajouter_pattern(node, "Paramètre dedent refusé par Interpreter.exec")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.add(alias.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.add(node.module.split(".")[0])
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name) and node.value.id == "os":
            if node.attr in {"system", "popen", "execv", "execve", "spawn"}:
                self.ajouter_pattern(node, f"Appel système dangereux: os.{node.attr}")
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if isinstance(item.context_expr, ast.Call) and isinstance(item.context_expr.func, ast.Name):
                if item.context_expr.func.id == "open":
                    self.ajouter_pattern(node, "Ouverture de fichier dans un contexte 'with'")
        self.generic_visit(node)

    def _enregistrer_noms(self, noms: List[ast.AST]) -> None:
        for n in noms:
            if isinstance(n, ast.Name):
                self.noms_examines.append(n.id)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._enregistrer_noms(node.targets)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._enregistrer_noms([node.target])
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._enregistrer_noms([node.target])
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._enregistrer_noms([node.target])
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._enregistrer_noms([node.elt])
        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._enregistrer_noms([node.key, node.value])
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._enregistrer_noms([node.elt])
        self.generic_visit(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._enregistrer_noms([node.elt])
        self.generic_visit(node)


def analyser_fichier(chemin: Path, racine: Path) -> Tuple[Dict[str, List[Tuple[int, int, str]]], Set[str], int, List[str], bool]:
    source = _lire_fichier(chemin)
    if source is None:
        return {}, set(), 0, [], True

    try:
        arbre = ast.parse(source, filename=str(chemin))
    except SyntaxError as e:
        print(f"Erreur de syntaxe dans {chemin}: {e}", file=sys.stderr)
        return {}, set(), 0, [], True

    analyseur = AnalyseurAST()
    analyseur.visiter_et_compter(arbre)

    return analyseur.patterns, analyseur.imports, analyseur.denominateur, analyseur.noms_examines, False


def detecter_patterns_dangereux(chemin: Path, racine: Path) -> Dict[str, Any]:
    patterns, imports, denominateur, noms_examines, lecture_echouee = analyser_fichier(chemin, racine)

    if not lecture_echouee:
        try:
            import RestrictedPython  # noqa: F401
            from RestrictedPython import compile_restricted  # noqa: F401

            source = _lire_fichier(chemin)
            if source is not None:
                try:
                    compile_restricted(source, filename=str(chemin))
                except SyntaxError as e:
                    patterns["RestrictedPython"] = [(e.lineno or 0, e.offset or 0, str(e))]
                except Exception as e:
                    patterns["RestrictedPython"] = [(0, 0, f"Erreur RestrictedPython: {e}")]
        except ImportError:
            print("RestrictedPython non disponible, analyse en mode dégradé", file=sys.stderr)
        except Exception as e:
            print(f"Erreur lors de l'analyse avec RestrictedPython: {e}", file=sys.stderr)

    return {
        "patterns": patterns,
        "imports": sorted(imports),
        "denominateur": denominateur,
        "examines": noms_examines[:200],
        "examines_tronques": len(noms_examines) > 200,
        "lecture_echouee": lecture_echouee,
    }


def generer_rapport_humain(resultat: Dict[str, Any], chemin: Path) -> str:
    rapport = []
    if resultat["lecture_echouee"]:
        rapport.append(f"Impossible d’analyser {chemin} à cause d’une erreur de lecture.")
    elif not resultat["patterns"]:
        rapport.append(f"Aucun pattern dangereux détecté dans {chemin}.")
    else:
        rapport.append(f"Patterns dangereux détectés dans {chemin} :")
        for pattern, occurrences in resultat["patterns"].items():
            rapport.append(f"  {pattern} :")
            for ligne, col, extrait in occurrences:
                rapport.append(f"    Ligne {ligne}, colonne {col} : {extrait or 'extrait non disponible'}")

    if resultat["examines_tronques"]:
        rapport.append(f"  ... {len(resultat['examines']) - 200} noms supplémentaires examinés (tronqués).")
    return "\n".join(rapport)


def generer_rapport_json(resultat: Dict[str, Any], chemin: Path) -> Dict[str, Any]:
    return {
        "fichier": str(chemin),
        "denominateur": resultat["denominateur"],
        "examines": resultat["examines"],
        "examines_tronques": resultat["examines_tronques"],
        "patterns": {
            pattern: [{"ligne": l, "colonne": c, "extrait": e} for l, c, e in occ]
            for pattern, occ in resultat["patterns"].items()
        },
        "imports": resultat["imports"],
        "contrat": {
            "QUESTION": "Ce code contient-il des patterns dangereux ?",
            "MESURE": "Analyse statique du code source Python en utilisant les modules ast et tokenize pour détecter "
                      "des constructions potentiellement dangereuses (ex: appels dynamiques, imports non contrôlés, "
                      "manipulations de fichiers, etc.). Utilisation optionnelle de RestrictedPython pour valider "
                      "la conformité à un sous‑ensemble sécurisé de Python.",
            "HYPOTHÈSES": "Le code source est valide et peut être parsé par ast.parse(). Les patterns dangereux sont "
                          "ceux qui permettent l'exécution de code arbitraire, l'accès non contrôlé aux ressources "
                          "système, ou la modification de l'environnement d'exécution.",
            "LIMITES": "Ne détecte pas les vulnérabilités dynamiques (ex: injections SQL, XSS). Ne vérifie pas les "
                       "dépendances tierces. Ne couvre pas les vulnérabilités spécifiques aux frameworks ou aux "
                       "bibliothèques externes.",
            "CONTRE-EXEMPLES": "Un code utilisant des imports dynamiques avec des noms calculés (ex: __import__(var)) "
                               "peut être marqué comme dangereux même s'il est sécurisé dans un contexte spécifique.",
            "DOMAINE": "Code source Python 3.14, analysé statiquement sans exécution."
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Détecte des patterns dangereux dans du code Python.",
        epilog="Exemple : python scanner_vulnerabilites.py mon_fichier.py --racine /mon/projet"
    )
    parser.add_argument("cible", type=str, help="Chemin vers le fichier Python à analyser.")
    parser.add_argument(
        "--racine",
        type=str,
        default=str(RACINE),
        help="Répertoire racine pour les imports relatifs. Par défaut : répertoire du script."
    )
    parser.add_argument("--json", action="store_true", help="Produit une sortie JSON au lieu d'un rapport humain.")
    args = parser.parse_args()

    racine = Path(args.racine).resolve()
    if racine not in sys.path:
        sys.path.insert(0, str(racine))

    chemin = Path(args.cible).resolve()
    if not chemin.exists() or not chemin.is_file():
        print(f"Erreur : {chemin} n'existe pas ou n'est pas un fichier.", file=sys.stderr)
        return 2

    resultat = detecter_patterns_dangereux(chemin, racine)

    if args.json:
        print(json.dumps(generer_rapport_json(resultat, chemin), indent=2, ensure_ascii=False))
    else:
        print(generer_rapport_humain(resultat, chemin), file=sys.stdout)

    if resultat["lecture_echouee"]:
        return 2
    return 1 if resultat["patterns"] else 0


if __name__ == "__main__":
    raise SystemExit(main())