"""
QUESTION       qui appelle quoi dans ce depot, sans rien executer ?
MESURE         ast.parse puis ast.walk sur chaque fichier ; les Call sont rattaches a la fonction englobante
HYPOTHÈSES     les appels sont ecrits litteralement ; le depot est du Python syntaxiquement valide
LIMITES        ne voit NI getattr(objet, nom)(), NI les appels par dispatch dynamique, NI ce qu'un decorateur insere ; ne resout pas la surcharge : deux methodes de meme nom sont confondues
CONTRE-EXEMPLES une classe a __getattr__ expose des appels qu'aucun AST ne peut enumerer -- l'outil doit les COMPTER comme indecidables, pas les taire
DOMAINE        un arbre de fichiers .py, appels statiques
"""

from __future__ import annotations

import sys
import os
import ast
import graphlib
import json
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CONTRAT = {
    "QUESTION": "qui appelle quoi dans ce depot, sans rien executer ?",
    "MESURE": "ast.parse puis ast.walk sur chaque fichier ; les Call sont rattaches a la fonction englobante",
    "HYPOTHÈSES": "les appels sont ecrits litteralement ; le depot est du Python syntaxiquement valide",
    "LIMITES": "ne voit NI getattr(objet, nom)(), NI les appels par dispatch dynamique, NI ce qu'un decorateur insere ; ne resout pas la surcharge : deux methodes de meme nom sont confondues",
    "CONTRE-EXEMPLE": "une classe a __getattr__ expose des appels qu'aucun AST ne peut enumerer -- l'outil doit les COMPTER comme indecidables, pas les taire",
    "DOMAINE": "un arbre de fichiers .py, appels statiques"
}

class VisiteurAppels(ast.NodeVisitor):
    def __init__(self) -> None:
        self.pile_def: list[str] = []
        self.appels: list[dict] = []
        self.definitions: list[str] = []

    def visit_FunctionDef(self, noeud: ast.FunctionDef) -> None:
        self.definitions.append(noeud.name)
        self.pile_def.append(noeud.name)
        self.generic_visit(noeud)
        self.pile_def.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, noeud: ast.Call) -> None:
        niveau = self.classifier_appel(noeud)
        cible = self.extraire_cible(noeud)
        englobante = self.pile_def[-1] if self.pile_def else "<module>"

        if isinstance(noeud.func, ast.Call):
            self.appels.append({
                "englobante": englobante,
                "cible": self.extraire_cible(noeud.func),
                "niveau": "INDÉCIDABLE"
            })
            self.appels.append({
                "englobante": englobante,
                "cible": cible,
                "niveau": "INDÉCIDABLE"
            })
        else:
            self.appels.append({
                "englobante": englobante,
                "cible": cible,
                "niveau": niveau
            })
        self.generic_visit(noeud)

    def classifier_appel(self, noeud: ast.Call) -> str:
        func = noeud.func
        if isinstance(func, ast.Name):
            return "CERTAIN"
        elif isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                return "PROBABLE"
            return "INDÉCIDABLE"
        elif isinstance(func, ast.Subscript):
            return "INDÉCIDABLE"
        elif isinstance(func, ast.Lambda):
            return "INDÉCIDABLE"
        return "INDÉCIDABLE"

    def extraire_cible(self, noeud: ast.Call) -> str:
        func = noeud.func
        if isinstance(func, ast.Name):
            return func.id
        elif isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                return f"{func.value.id}.{func.attr}"
            return func.attr
        elif isinstance(func, ast.Call):
            return self.extraire_cible(func)
        return "indécidable"

def trouver_fichiers_python(racine: Path) -> list[Path]:
    fichiers: list[Path] = []
    for dossier, sous_dossiers, noms in os.walk(racine):
        sous_dossiers[:] = [d for d in sous_dossiers if d not in (".git", "__pycache__", ".venv", "venv")]
        for nom in noms:
            if nom.endswith(".py"):
                fichiers.append(Path(dossier) / nom)
    return sorted(fichiers)

def analyser_fichier(chemin: Path) -> dict:
    try:
        source = chemin.read_text(encoding="utf-8")
    except Exception as e:
        return {"erreur": str(e), "chemin": str(chemin)}

    try:
        compile(source, str(chemin), "exec")
    except SyntaxError as e:
        return {"erreur": str(e), "chemin": str(chemin)}

    try:
        arbre = ast.parse(source, filename=str(chemin))
    except SyntaxError as e:
        return {"erreur": str(e), "chemin": str(chemin)}

    visiteur = VisiteurAppels()
    visiteur.visit(arbre)

    for appel in visiteur.appels:
        appel["chemin"] = str(chemin)

    return {
        "chemin": str(chemin),
        "appels": visiteur.appels,
        "definitions": visiteur.definitions
    }

def construire_graphe(fichiers: list[Path]) -> dict:
    graphe = {
        "appels": [],
        "definitions": set(),
        "erreurs": []
    }
    for f in fichiers:
        res = analyser_fichier(f)
        if "erreur" in res:
            graphe["erreurs"].append(res)
        else:
            graphe["appels"].extend(res["appels"])
            graphe["definitions"].update(res["definitions"])
    return graphe

def commande_analyser(graphe: dict) -> dict:
    total = len(graphe["appels"])
    certain = sum(1 for a in graphe["appels"] if a["niveau"] == "CERTAIN")
    probable = sum(1 for a in graphe["appels"] if a["niveau"] == "PROBABLE")
    indecidable = sum(1 for a in graphe["appels"] if a["niveau"] == "INDÉCIDABLE")
    taux_indecidable = (indecidable / total) if total > 0 else None
    pourcent_indecidable = (indecidable / total * 100) if total > 0 else None
    return {
        "pourcent_indecidable": pourcent_indecidable,
        "taux_indecidable": taux_indecidable,
        "total": total,
        "certain": certain,
        "probable": probable,
        "indecidable": indecidable,
    }

def commande_orphelins(graphe: dict) -> dict:
    definitions = set(graphe["definitions"])
    erreurs = {e["chemin"] for e in graphe["erreurs"]}
    definitions_valides = {d for d in definitions if not any(d in e["chemin"] for e in graphe["erreurs"])}

    atteints = set()
    for a in graphe["appels"]:
        if a["niveau"] in ("CERTAIN", "PROBABLE"):
            atteints.add(a["cible"])

    candidats = sorted(list(definitions_valides - atteints))
    indecidables = sum(1 for a in graphe["appels"]
                      if a["niveau"] == "INDÉCIDABLE"
                      and a["chemin"] not in erreurs
                      and a["cible"] in candidats)
    total_definitions = len(definitions_valides)
    atteints_count = len(atteints & definitions_valides)
    candidats_count = len(candidats)
    total_appels = len([a for a in graphe["appels"] if a["chemin"] not in erreurs])

    return {
        "candidats": candidats,
        "indecidables_potentiels": indecidables,
        "total_definitions": total_definitions,
        "atteints_count": atteints_count,
        "candidats_count": candidats_count,
        "total_appels": total_appels,
    }

def commande_lire(graphe: dict, nom: str, profondeur: int) -> dict:
    edges: dict[str, set[str]] = {}
    for a in graphe["appels"]:
        if a["niveau"] in ("CERTAIN", "PROBABLE"):
            edges.setdefault(a["englobante"], set()).add(a["cible"])

    reverse_edges: dict[str, set[str]] = {}
    for src, dests in edges.items():
        for d in dests:
            reverse_edges.setdefault(d, set()).add(src)

    visited_callees: set[str] = set()
    current_level = {nom}
    for _ in range(profondeur):
        next_level = set()
        for node in current_level:
            for callee in edges.get(node, set()):
                if callee not in visited_callees:
                    visited_callees.add(callee)
                    next_level.add(callee)
        current_level = next_level

    visited_callers: set[str] = set()
    current_level = {nom}
    for _ in range(profondeur):
        next_level = set()
        for node in current_level:
            for caller in reverse_edges.get(node, set()):
                if caller not in visited_callers:
                    visited_callers.add(caller)
                    next_level.add(caller)
        current_level = next_level

    subgraph_nodes = visited_callers | visited_callees | {nom}
    ts = graphlib.TopologicalSorter()
    for src, dests in edges.items():
        if src in subgraph_nodes:
            for d in dests:
                if d in subgraph_nodes:
                    ts.add(src, d)

    cycle_error = False
    try:
        order = list(ts.static_order())
    except graphlib.CycleError:
        cycle_error = True
        order = sorted(list(subgraph_nodes))

    aretes = []
    for a in graphe["appels"]:
        if a["englobante"] in subgraph_nodes and a["cible"] in subgraph_nodes:
            aretes.append({
                "source": a["englobante"],
                "cible": a["cible"],
                "niveau": a["niveau"]
            })

    return {
        "appelants": sorted(list(visited_callers)),
        "appeles": sorted(list(visited_callees)),
        "ordre": order,
        "aretes": aretes,
        "cycle": cycle_error,
        "subgraph_size": len(subgraph_nodes),
    }

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Cartographie statique des appels Python.",
        epilog="Exemple: python carte_appels.py analyser --racine /chemin/depot"
    )
    parser.add_argument("--racine", type=Path, default=Path(__file__).resolve().parent, help="Racine du projet à analyser.")
    parser.add_argument("--json", action="store_true", help="Sortie JSON sur stdout.")

    subparsers = parser.add_subparsers(dest="commande", required=True)

    p_analyser = subparsers.add_parser("analyser", help="Analyse le dépôt et compte les appels par niveau.")
    p_lire = subparsers.add_parser("lire", help="Remonte les appelants et appelés d'une fonction.")
    p_lire.add_argument("nom", type=str, help="Nom de la fonction à inspecter.")
    p_lire.add_argument("--profondeur", type=int, default=1, help="Nombre de niveaux de remontée.")
    p_orphelins = subparsers.add_parser("orphelins", help="Trouve les fonctions non appelées.")

    args = parser.parse_args()

    racine = args.racine.resolve()
    if not racine.is_dir():
        print(f"Erreur: la racine {racine} n'existe pas ou n'est pas un dossier.", file=sys.stderr)
        return 1

    fichiers = trouver_fichiers_python(racine)
    graphe = construire_graphe(fichiers)

    if args.commande == "analyser":
        res = commande_analyser(graphe)
        if res["total"] == 0:
            if args.json:
                json.dump({"contrat": CONTRAT, "erreur": "Aucun appel trouvé", "denominateur": 0}, sys.stdout, ensure_ascii=False, indent=2)
            else:
                print("Dénominateur: 0. Refus de conclure.", file=sys.stderr)
            return 2
        if args.json:
            json.dump({"contrat": CONTRAT, **res}, sys.stdout, ensure_ascii=False, indent=2)
        else:
            print(f"Indécidables: {res['indecidable']} ({res['pourcent_indecidable']:.1f}%)")
            print(f"Total d'appels: {res['total']}")
            print(f"CERTAIN: {res['certain']}")
            print(f"PROBABLE: {res['probable']}")
        return 0

    elif args.commande == "lire":
        if not any(a["cible"] == args.nom or a["englobante"] == args.nom for a in graphe["appels"]) and args.nom not in graphe["definitions"]:
            if args.json:
                json.dump({"contrat": CONTRAT, "erreur": "Fonction introuvable", "denominateur": 0}, sys.stdout, ensure_ascii=False, indent=2)
            else:
                print("Dénominateur: 0. Refus de conclure.", file=sys.stderr)
            return 2
        res = commande_lire(graphe, args.nom, args.profondeur)
        if args.json:
            json.dump({"contrat": CONTRAT, **res}, sys.stdout, ensure_ascii=False, indent=2)
        else:
            print(f"Appelants de {args.nom}: {', '.join(res['appelants']) or 'Aucun'}")
            print(f"Appelés par {args.nom}: {', '.join(res['appeles']) or 'Aucun'}")
            if res["cycle"]:
                print("Cycle détecté dans le sous-graphe, ordre de lecture approximatif.")
            print("Ordre de lecture:")
            for n in res["ordre"]:
                print(f"  - {n}")
            print(f"Taille du sous-graphe: {res['subgraph_size']}")
            print("Arêtes:")
            for a in res["aretes"]:
                print(f"  {a['source']} -> {a['cible']} [{a['niveau']}]")
        return 0

    elif args.commande == "orphelins":
        if not graphe["definitions"]:
            if args.json:
                json.dump({"contrat": CONTRAT, "erreur": "Aucune définition trouvée", "denominateur": 0}, sys.stdout, ensure_ascii=False, indent=2)
            else:
                print("Dénominateur: 0. Refus de conclure.", file=sys.stderr)
            return 2
        res = commande_orphelins(graphe)
        if args.json:
            json.dump({"contrat": CONTRAT, **res}, sys.stdout, ensure_ascii=False, indent=2)
        else:
            print(f"Candidats orphelins ({len(res['candidats'])}):")
            for c in res['candidats']:
                print(f"  - {c}")
            print(f"Définitions totales: {res['total_definitions']}")
            print(f"Atteints: {res['atteints_count']}")
            print(f"Candidats: {res['candidats_count']}")
            print(f"Appels totaux: {res['total_appels']}")
            print(f"Appels indécidables pouvant les atteindre: {res['indecidables_potentiels']}")
        return 1 if res["candidats"] else 0

    return 0

if __name__ == "__main__":
    raise SystemExit(main())