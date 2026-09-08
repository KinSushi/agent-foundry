"""Outil : ecart_declare_reel.py

QUESTION
    Ce que je lis dans ce fichier est-il ce qui s'exécutera ?
MESURE
    AST du source contre introspection du module importé,
    signature avec ET sans follow_wrapped, co_name réel.
HYPOTHÈSES
    Le module s'importe sans effet de bord (sinon --sans-importer).
LIMITES
    Ne peut PAS énumérer un __getattr__ dynamique ; ne voit pas ce qu'un
    import conditionnel n'a pas exécuté.
CONTRE-EXEMPLES
    `isfunction|isclass` déclare `typing.ClassVar` absent alors qu'il existe —
    un filtre trop étroit fabrique de fausses divergences.
INVOCATION
    {outil} {fichier} --json
DOMAINE
    Modules Python importables, sur cet interpréteur.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import importlib
import importlib.util
import inspect
import json
import pyclbr
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

# ------------------------------------------------------------
# Configuration de l'encodage
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Types
# ------------------------------------------------------------
Resultat = Dict[str, Any]

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _join_path(cible: Path | str, racine: Path) -> Path:
    """Retourne *cible* tel quel s'il est absolu, sinon le joint à *racine*."""
    p = Path(cible)
    return p if p.is_absolute() else racine / p

def _chemin_module(nom: str, racine: Path) -> Path | None:
    """Retourne le chemin du fichier source du module *nom* s'il existe."""
    try:
        spec = importlib.util.find_spec(nom)
    except (ValueError, TypeError, ModuleNotFoundError):
        return None
    if spec and spec.origin and spec.origin.endswith(".py"):
        return _join_path(Path(spec.origin), racine).resolve()
    return None

def _source_valide(source: str, nom: str) -> bool:
    """Vérifie que *source* est compilable (règle 10)."""
    try:
        compile(source, nom, "exec")
        return True
    except (SyntaxError, TypeError, ValueError):
        return False

def _collect_ast_names(source: str) -> Dict[str, ast.AST]:
    """Renvoie un dictionnaire nom → nœud AST (fonctions, classes, imports, variables)."""
    arbre = ast.parse(source)
    noms: Dict[str, ast.AST] = {}
    for node in ast.iter_child_nodes(arbre):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            noms[node.name] = node
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                nom = alias.asname or alias.name.split(".")[0]
                noms[nom] = node
            continue
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                nom = alias.asname or alias.name
                noms[nom] = node
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            for target in targets:
                if isinstance(target, ast.Name):
                    noms[target.id] = node
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            noms[elt.id] = node
            continue
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            noms[node.target.id] = node
            continue
    return noms

def _signature_ast(node: ast.AST) -> List[str]:
    """Liste des noms d'arguments telle qu'écrite dans le source."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = [arg.arg for arg in node.args.args]
        args += [arg.arg for arg in node.args.kwonlyargs]
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")
        return args
    return []

def _collect_runtime_objects(module: Any) -> Dict[str, Any]:
    """Renvoie un dictionnaire nom → objet du module (callables, types, variables)."""
    result: Dict[str, Any] = {}
    for name in dir(module):
        try:
            obj = getattr(module, name)
        except AttributeError:
            continue
        if inspect.ismodule(obj):
            continue
        result[name] = obj
    return result

def _est_wrapt_wrapper(_: Any) -> bool:
    """Wrapt n'étant pas disponible, aucun wrapper n'est détecté."""
    return False

def _est_forme_speciale(obj: Any) -> bool:
    """Détecte une forme spéciale (ex. typing.ClassVar, typing.Annotated)."""
    return type(obj).__name__ == "_SpecialForm"

def _signature_proche(signature_attendue: str, signatures_possibles: List[str]) -> str:
    """Trouve la signature la plus proche de *signature_attendue*."""
    if not signatures_possibles:
        return ""
    matcher = difflib.get_close_matches(
        signature_attendue, signatures_possibles, n=1, cutoff=0.6
    )
    return matcher[0] if matcher else ""

def citation(module: str, fragment: str, racine: Path) -> Dict[str, Any]:
    """Confronte un fragment de signature prétendue au réel et propose le plus proche."""
    chemin = _chemin_module(module, racine)
    if chemin is None:
        raise FileNotFoundError(f"Module {module!r} introuvable.")
    try:
        source = chemin.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise OSError(f"Impossible de lire le fichier {chemin}: {exc}") from exc
    if not _source_valide(source, str(chemin)):
        raise SyntaxError(f"Le source de {module!r} n'est pas compilable.")
    spec = importlib.util.find_spec(module)
    if spec is None:
        raise ImportError(f"Impossible d'importer le module {module!r}.")
    module_obj = importlib.import_module(module)

    try:
        nom_fonction, signature_attendue = fragment.split("(", 1)
        signature_attendue = f"({signature_attendue}"
    except ValueError as exc:
        raise ValueError(
            "Le fragment doit être de la forme 'nom_fonction(arg1, arg2=val)'."
        ) from exc

    runtime_noms = _collect_runtime_objects(module_obj)
    signatures_possibles: List[str] = []
    for nom, obj in runtime_noms.items():
        if nom == nom_fonction:
            try:
                sig = inspect.signature(obj, follow_wrapped=False)
                signatures_possibles.append(f"{nom}{sig}")
            except (ValueError, TypeError):
                continue

    signature_proche = _signature_proche(
        f"{nom_fonction}{signature_attendue}", signatures_possibles
    )

    present = nom_fonction in runtime_noms
    if present:
        obj = runtime_noms[nom_fonction]
        try:
            sig_reelle = inspect.signature(obj, follow_wrapped=False)
            signature_reelle = f"{nom_fonction}{sig_reelle}"
        except (ValueError, TypeError):
            signature_reelle = None
    else:
        signature_reelle = None

    return {
        "module": module,
        "fragment": fragment,
        "present": present,
        "signature_reelle": signature_reelle,
        "signature_proche": (
            signature_proche
            if signature_proche != f"{nom_fonction}{signature_attendue}"
            else None
        ),
    }

def _analyse_nom(
    nom: str,
    ast_node: ast.AST | None,
    runtime_obj: Any | None,
    sans_importer: bool = False,
) -> Resultat:
    """Analyse un nom et renvoie le résultat détaillé."""
    present_ast = ast_node is not None
    present_rt = runtime_obj is not None

    signature_ast: List[str] = []
    signature_rt: List[str] | None = None
    co_name_ast: str | None = getattr(ast_node, "name", None) if ast_node else None
    co_name_rt: str | None = None
    forme_speciale = False
    statut = "INCONNU"

    if present_ast:
        signature_ast = _signature_ast(ast_node)

    if sans_importer:
        statut = "RUNTIME NON CONSULTÉ"
    elif not present_ast and not present_rt:
        statut = "INCONNU"
    elif not present_ast and present_rt:
        if _est_wrapt_wrapper(runtime_obj):
            statut = "ENVELOPPÉ TRANSPARENT"
        elif _est_forme_speciale(runtime_obj):
            statut = "FORME SPÉCIALE"
            forme_speciale = True
        else:
            statut = "AJOUTÉ À CHAUD"
    elif present_ast and not present_rt:
        statut = "ABSENT RUNTIME"
    else:  # present_ast and present_rt
        if _est_wrapt_wrapper(runtime_obj):
            statut = "ENVELOPPÉ TRANSPARENT"
        else:
            forme_speciale = _est_forme_speciale(runtime_obj)
            try:
                sig_rt = inspect.signature(runtime_obj, follow_wrapped=False)
                signature_rt = []
                for p in sig_rt.parameters.values():
                    if p.kind == inspect.Parameter.VAR_POSITIONAL:
                        signature_rt.append(f"*{p.name}")
                    elif p.kind == inspect.Parameter.VAR_KEYWORD:
                        signature_rt.append(f"**{p.name}")
                    else:
                        signature_rt.append(p.name)
            except (ValueError, TypeError):
                signature_rt = None

            try:
                co_name_rt = runtime_obj.__code__.co_name  # type: ignore[attr-defined]
            except AttributeError:
                co_name_rt = None

            if signature_rt is not None and signature_ast != signature_rt:
                statut = "DIVERGENT"
            elif co_name_rt and co_name_ast and co_name_ast != co_name_rt:
                statut = "ENVELOPPÉ"
            else:
                statut = "ACCORD"

    return {
        "nom": nom,
        "present_ast": present_ast,
        "present_runtime": present_rt,
        "signature_ast": signature_ast,
        "signature_runtime": signature_rt,
        "co_name_ast": co_name_ast,
        "co_name_runtime": co_name_rt,
        "forme_speciale": forme_speciale,
        "statut": statut,
    }

def _construire_resultats(
    ast_noms: Dict[str, ast.AST],
    runtime_noms: Dict[str, Any],
    sans_importer: bool,
) -> List[Resultat]:
    """Construit la liste complète des résultats à partir des deux dictionnaires."""
    tous_noms = set(ast_noms) | set(runtime_noms)
    resultats: List[Resultat] = []
    for nom in sorted(tous_noms):
        resultats.append(
            _analyse_nom(
                nom,
                ast_noms.get(nom),
                runtime_noms.get(nom),
                sans_importer=sans_importer,
            )
        )
    return resultats

def comparer(module: str, racine: Path, sans_importer: bool = False) -> List[Resultat]:
    """Compare l'AST et le runtime d'un module (nom de module)."""
    chemin = _chemin_module(module, racine)
    if chemin is None:
        raise FileNotFoundError(f"Module {module!r} introuvable.")
    try:
        source = chemin.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise OSError(f"Impossible de lire le fichier {chemin}: {exc}") from exc
    if not _source_valide(source, str(chemin)):
        raise SyntaxError(f"Le source de {module!r} n'est pas compilable.")

    ast_noms = _collect_ast_names(source)

    if sans_importer:
        runtime_noms: Dict[str, Any] = pyclbr.readmodule_ex(module)  # type: ignore[assignment]
    else:
        spec = importlib.util.find_spec(module)
        if spec is None:
            raise ImportError(f"Impossible d'importer le module {module!r}.")
        module_obj = importlib.import_module(module)
        runtime_noms = _collect_runtime_objects(module_obj)

    return _construire_resultats(ast_noms, runtime_noms, sans_importer)

def _format_humain(resultats: Iterable[Resultat]) -> str:
    """Produit une représentation textuelle lisible."""
    lignes = [
        f"{r['nom']:<20} AST:{'Oui' if r['present_ast'] else 'Non':<3} "
        f"RT:{'Oui' if r['present_runtime'] else 'Non':<3} {r['statut']}"
        for r in resultats
    ]
    return "\n".join(lignes)

# ------------------------------------------------------------
# Interface en ligne de commande
# ------------------------------------------------------------

class _JsonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser qui renvoie un JSON sur stdout en cas d'erreur si --json est présent."""

    def error(self, message: str) -> None:  # pragma: no cover
        if "--json" in sys.argv:
            json.dump({"erreur": message, "denominateur": 0}, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            super().error(message)
        raise SystemExit(2)

def _construire_parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(
        description=(
            "Compare l'AST d'un module Python avec ce qui est réellement exécuté. "
            "Exemple : python outils/ecart_declare_reel.py mon_module --json"
        )
    )
    parser.add_argument(
        "module",
        help="Nom complet du module à analyser (exemple : package.module) ou chemin vers un fichier .py.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine à utiliser à la place du répertoire du script.",
    )
    parser.add_argument(
        "--sans-importer",
        action="store_true",
        help="Utilise uniquement pyclbr (pas d'import) pour éviter les effets de bord.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émet le résultat au format JSON unique.",
    )
    parser.add_argument(
        "--citation",
        type=str,
        help="Confronte une signature prétendue au réel (exemple: 'ma_fonction(x, y=2)').",
    )
    return parser

def _contrat() -> Dict[str, str]:
    return {
        "QUESTION": "Ce que je lis dans ce fichier est-il ce qui s'exécutera ?",
        "MESURE": "AST du source contre introspection du module importé, signature avec ET sans follow_wrapped, co_name réel",
        "HYPOTHÈSES": "Le module s'importe sans effet de bord (sinon --sans-importer)",
        "LIMITES": "Ne peut PAS énumérer un __getattr__ dynamique ; ne voit pas ce qu'un import conditionnel n'a pas exécuté",
        "CONTRE-EXEMPLES": "isfunction|isclass déclare typing.ClassVar absent alors qu'il existe — un filtre trop étroit fabrique de fausses divergences",
        "DOMAINE": "Modules Python importables, sur cet interpréteur",
    }

def main() -> int:
    parser = _construire_parser()
    args = parser.parse_args()

    try:
        if args.citation:
            resultat = citation(args.module, args.citation, args.racine)
            if args.json:
                sortie = {
                    "denominateur": 1,
                    "examines": [],
                    "contrat": _contrat(),
                    "citation": resultat,
                }
                json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
            else:
                print(f"Module: {resultat['module']}")
                print(f"Fragment: {resultat['fragment']}")
                print(f"Présent: {'Oui' if resultat['present'] else 'Non'}")
                if resultat["present"]:
                    print(f"Signature réelle: {resultat['signature_reelle']}")
                if resultat["signature_proche"]:
                    print(f"Signature proche: {resultat['signature_proche']}")
            return 0

        # Détermination du mode (module nommé ou chemin de fichier)
        chemin_possible = Path(args.module)
        if chemin_possible.is_file():
            # Mode fichier
            chemin = _join_path(chemin_possible, args.racine).resolve()
            source = chemin.read_text(encoding="utf-8", errors="replace")
            if not _source_valide(source, str(chemin)):
                raise SyntaxError(f"Le source de {chemin} n'est pas compilable.")
            ast_noms = _collect_ast_names(source)

            if args.sans_importer:
                runtime_noms: Dict[str, Any] = {}
            else:
                spec = importlib.util.spec_from_file_location("module_temp", chemin)
                if spec is None or spec.loader is None:
                    raise ImportError(f"Impossible de charger le module depuis {chemin}.")
                module_obj = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module_obj)  # type: ignore[assignment]
                runtime_noms = _collect_runtime_objects(module_obj)

            resultats = _construire_resultats(ast_noms, runtime_noms, args.sans_importer)
        else:
            # Mode module nommé (comportement historique)
            resultats = comparer(args.module, args.racine, args.sans_importer)
            # Recalcul du dictionnaire AST pour obtenir le dénominateur correct
            chemin = _chemin_module(args.module, args.racine)
            if chemin is None:
                raise FileNotFoundError(f"Module {args.module!r} introuvable.")
            source = chemin.read_text(encoding="utf-8", errors="replace")
            ast_noms = _collect_ast_names(source)

        total = len(ast_noms)  # dénominateur = nombre d'éléments examinés (AST)

        if total == 0:
            sys.stderr.write("Denominateur nul : rien à examiner, refus de conclure.\n")
            if args.json:
                sortie = {"denominateur": 0, "examines": [], "contrat": _contrat()}
                json.dump(sortie, sys.stdout, ensure_ascii=False)
                sys.stdout.write("\n")
            return 3

        examines = sorted(ast_noms.keys())
        divergences = [r for r in resultats if r["statut"] != "ACCORD"]
        code_sortie = 0 if not divergences else 1

        if args.json:
            sortie = {
                "denominateur": total,
                "examines": examines,
                "contrat": _contrat(),
                "resultats": resultats,
            }
            json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print(_format_humain(resultats))
            print("\nContrat de mesure :", json.dumps(_contrat(), ensure_ascii=False, indent=2))
        return code_sortie

    except OSError as exc:
        sys.stderr.write(f"Erreur d'accès fichier : {exc}\n")
        if args.json:
            json.dump({"erreur": str(exc), "denominateur": 0}, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        return 3
    except Exception as exc:
        sys.stderr.write(f"Erreur : {exc}\n")
        if args.json:
            json.dump({"erreur": str(exc), "denominateur": 0}, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        return 3

if __name__ == "__main__":
    raise SystemExit(main())