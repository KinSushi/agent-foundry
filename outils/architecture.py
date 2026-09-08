"""architecture.py
Outil d’analyse d’un dépôt Python.

Ce fichier suit strictement le socle et le plan fournis. Il ne dépend que de la
bibliothèque standard ; `networkx` est importé de façon optionnelle.

CONTRAT DE MESURE
QUESTION       par quoi commencer pour comprendre ce dépôt, et qu'est‑ce qui
               casse le plus de choses ?
MESURE        graphe des imports (ast), centralité et cycles (networkx),
               ordre (graphlib)
HYPOTHÈSES    les imports sont statiques et absolus
LIMITES       ne voit pas les imports dynamiques ; les imports relatifs
               (level > 0) sont résolus de façon simple, ce qui peut manquer
               d’arêtes internes à un paquet
CONTRE‑EXEMPLES la mesure initiale ne prenait que level == 0 et sous‑comptait
               les arêtes — le défaut était dans l’instrument, pas dans
               networkx
INVOCATION
    {outil} carte --racine {dossier}/a --json
DOMAINE       arbre de fichiers *.py, imports statiques
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import time
from graphlib import TopologicalSorter, CycleError
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Set, Tuple, Any

# Export public API (core class) only
__all__ = ["Carte"]

# 2. Encodage en tête
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 1. stdlib pure – import optionnel de networkx
try:
    import networkx as nx
except ImportError:  # pragma: no cover
    nx = None  # type: ignore[assignment]

# --------------------------------------------------------------------------- #
#                       CŒUR – aucune I/O ni argparse                        #
# --------------------------------------------------------------------------- #

def _nom_module(dep: Path, racine: Path) -> str:
    """Convertit un chemin de fichier *.py* en nom de module relatif au racine."""
    rel = dep.relative_to(racine).with_suffix("")
    return ".".join(rel.parts)

def _resolver_import_relatif(
    module_courant: str, level: int, module: str | None
) -> str | None:
    """
    Résout un import relatif (level > 0) en nom absolu.
    Retourne ``None`` si la résolution échoue.
    """
    parts = module_courant.split(".")
    if len(parts) < level:
        return None
    base = parts[: -level]  # on remonte `level` niveaux
    if module:
        return ".".join(base + [module])
    return ".".join(base)

def _construire_graphe_stdlib(racine: Path) -> Tuple[Dict[str, List[str]], float]:
    """Construit le graphe d'imports avec la stdlib uniquement."""
    debut = time.perf_counter()
    graphe: Dict[str, List[str]] = {}

    for dirpath, dirnames, filenames in os.walk(racine):
        if "__pycache__" in dirnames:
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            chemin = Path(dirpath) / fn
            try:
                source = chemin.read_text(encoding="utf-8")
                compile(source, str(chemin), "exec")
            except Exception as exc:
                print(f"Impossible de compiler {chemin} : {exc}", file=sys.stderr)
                continue
            module = _nom_module(chemin, racine)
            graphe[module] = []
            try:
                arbre = ast.parse(source, filename=str(chemin), mode="exec")
            except Exception:
                continue
            for node in ast.walk(arbre):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        cible = alias.name
                        graphe[module].append(cible)
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0:
                        base_mod = node.module or ""
                        for alias in node.names:
                            if alias.name == "*":
                                cible = base_mod
                            else:
                                cible = f"{base_mod}.{alias.name}" if base_mod else alias.name
                            if cible:
                                graphe[module].append(cible)
                    else:
                        base_res = _resolver_import_relatif(module, node.level, node.module)
                        if base_res:
                            for alias in node.names:
                                if alias.name == "*":
                                    cible = base_res
                                else:
                                    cible = f"{base_res}.{alias.name}"
                                graphe[module].append(cible)
    fin = time.perf_counter()
    return graphe, fin - debut

def construire_graphe(racine: Path) -> Tuple[Any, float]:
    """
    Construit le graphe d'imports. Retourne un graphe networkx si disponible,
    sinon un dictionnaire {noeud: [voisins]} et le temps de construction.
    """
    if nx is not None:
        return _construire_graphe_networkx(racine)
    else:
        graphe, temps = _construire_graphe_stdlib(racine)
        return graphe, temps

def _construire_graphe_networkx(racine: Path) -> Tuple[nx.DiGraph, float]:
    """Construit le graphe d'imports avec networkx."""
    debut = time.perf_counter()
    graphe = nx.DiGraph()
    for dirpath, dirnames, filenames in os.walk(racine):
        if "__pycache__" in dirnames:
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            chemin = Path(dirpath) / fn
            try:
                source = chemin.read_text(encoding="utf-8")
                compile(source, str(chemin), "exec")
            except Exception as exc:
                print(f"Impossible de compiler {chemin} : {exc}", file=sys.stderr)
                continue
            module = _nom_module(chemin, racine)
            graphe.add_node(module)
            try:
                arbre = ast.parse(source, filename=str(chemin), mode="exec")
            except Exception:
                continue
            for node in ast.walk(arbre):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        cible = alias.name
                        graphe.add_edge(module, cible)
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0:
                        base_mod = node.module or ""
                        for alias in node.names:
                            if alias.name == "*":
                                cible = base_mod
                            else:
                                cible = f"{base_mod}.{alias.name}" if base_mod else alias.name
                            if cible:
                                graphe.add_edge(module, cible)
                    else:
                        base_res = _resolver_import_relatif(module, node.level, node.module)
                        if base_res:
                            for alias in node.names:
                                if alias.name == "*":
                                    cible = base_res
                                else:
                                    cible = f"{base_res}.{alias.name}"
                                graphe.add_edge(module, cible)
    fin = time.perf_counter()
    return graphe, fin - debut

def _critiques_stdlib(graphe: Dict[str, List[str]]) -> List[Tuple[str, int]]:
    """Calcule les modules critiques avec la stdlib (nombre de dépendants seulement)."""
    dependants = {n: 0 for n in graphe}
    for noeud, voisins in graphe.items():
        for v in voisins:
            if v in dependants:
                dependants[v] += 1
    result = [(n, dependants[n]) for n in graphe]
    result.sort(key=lambda t: (-t[1], t[0]))
    return result

def critiques(graphe: Any) -> List[Tuple[str, int, float]]:
    """
    Retourne la liste des modules triés par centralité décroissante.
    En mode dégradé, retourne (module, nombre_de_dependants, 0.0).
    """
    if nx is not None and isinstance(graphe, nx.DiGraph):
        dependants = {n: graphe.in_degree(n) for n in graphe.nodes}
        centralite = nx.in_degree_centrality(graphe) if graphe.number_of_nodes() else {}
        result = [(n, dependants[n], centralite.get(n, 0.0)) for n in graphe.nodes]
        result.sort(key=lambda t: (-t[2], -t[1], t[0]))
        return result
    else:
        result = _critiques_stdlib(graphe)
        return [(n, d, 0.0) for n, d in result]

def _cycles_graphlib(graphe: Dict[str, List[str]]) -> Tuple[bool, List[str] | None]:
    """Détecte les cycles avec graphlib."""
    try:
        ts = TopologicalSorter(graphe)
        list(ts.static_order())
        return True, None
    except CycleError as exc:
        return False, list(exc.args[1])  # type: ignore[arg-type]

def cycles_networkx(graphe: nx.DiGraph) -> List[List[str]]:
    """Détecte les cycles avec networkx."""
    if nx is None:
        return []
    return [list(c) for c in nx.simple_cycles(graphe)]

def cycles(graphe: Any) -> Tuple[bool, List[str] | None, List[List[str]]]:
    """
    Retourne (ordonnable, premier_cycle, cycles_nx).
    En mode dégradé, cycles_nx est toujours une liste vide.
    """
    if nx is not None and isinstance(graphe, nx.DiGraph):
        ok, premier = _cycles_graphlib({n: list(graphe.successors(n)) for n in graphe.nodes})
        cycles_nx = cycles_networkx(graphe)
        return ok, premier, cycles_nx
    else:
        ok, premier = _cycles_graphlib(graphe)
        return ok, premier, []

def _lire_ancetres_stdlib(graphe: Dict[str, List[str]], cible: str) -> Tuple[List[str], List[str] | None]:
    """Calcule l'ordre de lecture avec la stdlib."""
    if cible not in graphe:
        raise ValueError(f"Le module « {cible} » n’est pas présent dans le graphe.")

    # Calcul des ancêtres
    ancetres = set()
    a_traiter = [cible]
    while a_traiter:
        courant = a_traiter.pop()
        if courant not in ancetres:
            ancetres.add(courant)
            for predecesseur in graphe:
                if courant in graphe[predecesseur]:
                    a_traiter.append(predecesseur)

    sous_graphe = {n: graphe[n] for n in ancetres}
    try:
        ts = TopologicalSorter(sous_graphe)
        ordre = list(ts.static_order())
        return ordre, None
    except CycleError as exc:
        return [], list(exc.args[1])  # type: ignore[arg-type]

def lire_ancetres(graphe: Any, cible: str) -> Tuple[List[str], List[str] | None]:
    """Retourne l'ordre de lecture des ancêtres de la cible."""
    if nx is not None and isinstance(graphe, nx.DiGraph):
        if cible not in graphe:
            raise ValueError(f"Le module « {cible} » n’est pas présent dans le graphe.")
        anc = nx.ancestors(graphe, cible)
        sous = graphe.subgraph(anc | {cible}).copy()
        ok, cycle = _cycles_graphlib({n: list(sous.successors(n)) for n in sous.nodes})
        if ok:
            ts = TopologicalSorter({n: list(sous.successors(n)) for n in sous.nodes})
            ordre = list(ts.static_order())
            return ordre, None
        else:
            return [], cycle
    else:
        return _lire_ancetres_stdlib(graphe, cible)

def _verifier_apis() -> Dict[str, bool]:
    """Vérifie la disponibilité des APIs."""
    return {
        "ast.parse": True,
        "ast.walk": True,
        "os.walk": True,
        "networkx.DiGraph": nx is not None,
        "networkx.in_degree_centrality": nx is not None,
        "networkx.simple_cycles": nx is not None,
        "graphlib.TopologicalSorter": True,
    }

class Carte:
    """Classe centrale pour l'analyse du graphe d'imports."""

    def __init__(self, racine: str | Path):
        self.racine = Path(racine).resolve()
        self.graphe, self.temps = construire_graphe(self.racine)
        self.mode_degrade = nx is None

    def critiques(self) -> List[Tuple[str, int, float]]:
        return critiques(self.graphe)

    def cycles(self) -> Tuple[bool, List[str] | None, List[List[str]]]:
        return cycles(self.graphe)

    def lire(self, module: str) -> Tuple[List[str], List[str] | None]:
        return lire_ancetres(self.graphe, module)

# --------------------------------------------------------------------------- #
#                               CLI – I/O                                     #
# --------------------------------------------------------------------------- #

def _afficher_humain(args: argparse.Namespace, carte: Carte) -> int:
    """Affiche la sortie lisible par un humain."""
    if carte.mode_degrade:
        print("Mode dégradé : networkx non disponible", file=sys.stderr)

    if args.command == "carte":
        noeuds = len(carte.graphe) if isinstance(carte.graphe, dict) else carte.graphe.number_of_nodes()
        aretes = sum(len(v) for v in carte.graphe.values()) if isinstance(carte.graphe, dict) else carte.graphe.number_of_edges()
        print(f"Construction du graphe : {noeuds} nœuds, {aretes} arêtes, en {carte.temps:.2f}s.")
    elif args.command == "critiques":
        crit = carte.critiques()
        print("Modules les plus critiques (centralité décroissante) :")
        for mod, dep, cen in crit[:10]:
            print(f"  {mod:<30} dépendants : {dep:>3}  centralité : {cen:.4f}")
    elif args.command == "cycles":
        ok, premier, cycles_nx = carte.cycles()
        if ok:
            print("graphlib : le graphe est ordonnable (pas de cycle détecté).")
        else:
            print("graphlib : cycle détecté → " + " → ".join(premier))
        if not carte.mode_degrade:
            print(f"networkx : {len(cycles_nx)} cycles au total.")
            for c in cycles_nx[:5]:
                print("  - " + " → ".join(c))
            if len(cycles_nx) > 5:
                print(f"  … ({len(cycles_nx) - 5} autres cycles)")
        else:
            print("networkx : non disponible en mode dégradé")
    elif args.command == "lire":
        try:
            ordre, cycle = carte.lire(args.module)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 3
        if cycle:
            print("Cycle détecté lors du calcul de l’ordre : " + " → ".join(cycle))
        print("Ordre de lecture recommandé :")
        for m in ordre:
            print(f"  {m}")
    elif args.command == "all":
        noeuds = len(carte.graphe) if isinstance(carte.graphe, dict) else carte.graphe.number_of_nodes()
        aretes = sum(len(v) for v in carte.graphe.values()) if isinstance(carte.graphe, dict) else carte.graphe.number_of_edges()
        print(f"Construction du graphe : {noeuds} nœuds, {aretes} arêtes, en {carte.temps:.2f}s.")

        crit = carte.critiques()
        print("\nModules les plus critiques (centralité décroissante) :")
        for mod, dep, cen in crit[:10]:
            print(f"  {mod:<30} dépendants : {dep:>3}  centralité : {cen:.4f}")

        ok, premier, cycles_nx = carte.cycles()
        if ok:
            print("\ngraphlib : le graphe est ordonnable (pas de cycle détecté).")
        else:
            print("\ngraphlib : cycle détecté → " + " → ".join(premier))
        if not carte.mode_degrade:
            print(f"networkx : {len(cycles_nx)} cycles au total.")
        else:
            print("networkx : non disponible en mode dégradé")

    return 0 if not (args.command == "cycles" and not carte.cycles()[0]) else 1

def _produire_json(args: argparse.Namespace, carte: Carte) -> Tuple[dict, int]:
    """Construit l'objet JSON complet."""
    data: dict = {
        "denominateur": len(carte.graphe) if isinstance(carte.graphe, dict) else carte.graphe.number_of_nodes(),
        "mode_degrade": carte.mode_degrade,
        "contrat": {
            "QUESTION": "par quoi commencer pour comprendre ce dépôt, et qu'est‑ce qui casse le plus de choses ?",
            "MESURE": "graphe des imports (ast), centralité et cycles (networkx), ordre (graphlib)",
            "HYPOTHÈSES": "les imports sont statiques et absolus",
            "LIMITES": "ne voit pas les imports dynamiques ; les imports relatifs (level>0) sont résolus de façon simple",
            "CONTRE‑EXEMPLES": "la mesure initiale ne prenait que level==0 et sous‑comptait les arêtes",
            "DOMAINE": "arbre de fichiers .py, imports statiques",
        },
        "carte": {
            "noeuds": len(carte.graphe) if isinstance(carte.graphe, dict) else carte.graphe.number_of_nodes(),
            "aretes": sum(len(v) for v in carte.graphe.values()) if isinstance(carte.graphe, dict) else carte.graphe.number_of_edges(),
            "temps_s": carte.temps,
        },
        "api_verification": _verifier_apis(),
    }

    if args.command in ("critiques", "all"):
        data["critiques"] = [
            {"module": m, "dependants": d, "centralite": c}
            for m, d, c in carte.critiques()
        ]

    if args.command in ("cycles", "all"):
        ok, premier, cycles_nx = carte.cycles()
        data["cycles"] = {
            "graphlib": {"ordonnable": ok, "premier_cycle": premier},
            "networkx": {"disponible": not carte.mode_degrade, "nombre": len(cycles_nx), "cycles": cycles_nx} if not carte.mode_degrade else {"disponible": False}
        }

    if args.command == "lire":
        try:
            ordre, cycle = carte.lire(args.module)
        except ValueError as exc:
            data["erreur"] = str(exc)
            return data, 3
        data["lire"] = {
            "module": args.module,
            "ordre": ordre,
            "cycle": cycle,
        }

    code_retour = 0
    if args.command == "cycles" and not carte.cycles()[0]:
        code_retour = 1
    return data, code_retour

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyse d’un dépôt Python : graphe d’imports, centralité, cycles, ordre de lecture.",
        epilog="Exemple : python -m outils.architecture carte --racine ./mon_projet",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine du dépôt à analyser (défaut : répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique sur stdout.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("carte", help="Construire le graphe d’imports et afficher ses métriques.")
    sub.add_parser("critiques", help="Lister les modules les plus critiques (centralité).")
    sub.add_parser("cycles", help="Détecter les cycles avec graphlib et networkx.")
    sub.add_parser("all", help="Toutes les informations (critiques, cycles, …).")
    lire = sub.add_parser("lire", help="Proposer l’ordre de lecture d’un module.")
    lire.add_argument("module", help="Nom du module cible (exemple : package.module).")

    args = parser.parse_args()

    racine = args.racine.resolve()
    if not racine.is_dir():
        print(f"Erreur : le répertoire racine « {racine} » n’existe pas.", file=sys.stderr)
        return 2

    try:
        carte = Carte(racine)
    except Exception as exc:
        print(f"Erreur lors de la construction du graphe : {exc}", file=sys.stderr)
        return 5

    if carte.mode_degrade:
        print("Mode dégradé : networkx non disponible - certaines fonctionnalités sont limitées", file=sys.stderr)

    denominateur = len(carte.graphe) if isinstance(carte.graphe, dict) else carte.graphe.number_of_nodes()
    if denominateur == 0:
        print("Aucun fichier *.py* trouvé dans le répertoire indiqué.", file=sys.stderr)
        return 3

    if args.json:
        obj, code = _produire_json(args, carte)
        json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return code
    else:
        return _afficher_humain(args, carte)

if __name__ == "__main__":
    raise SystemExit(main())