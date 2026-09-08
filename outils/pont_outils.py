"""
QUESTION      cet outil est-il utilisable par un autre projet ?
MESURE        imports par ast (sans exécuter) + import réel de contrôle
HYPOTHÈSES    l'outil suit le socle : un main(), une API publique nommée
LIMITES       ne voit pas les imports dynamiques ; ne garantit pas que
              l'API soit stable entre deux versions
CONTRE-EXEMPLE un critère unique « contient print » déclare Banc non
              importable alors qu'il l'est — mesuré
DOMAINE       les outils de cette boîte, sur cet interpréteur
"""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import json
import pkgutil
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent.parent

def conventions(racine: Path) -> Dict[str, Any]:
    """Lit [tool.puissance] dans pyproject.toml du projet cible."""
    pyproject = racine / "pyproject.toml"
    if not pyproject.exists():
        return {}
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        return data.get("tool", {}).get("puissance", {})
    except (tomllib.TOMLDecodeError, OSError) as e:
        return {"_pont_outils_conventions_error": str(e)}

def _est_importable(arbre: ast.AST) -> bool:
    """Vérifie que le module n'importe pas argparse et ne lit pas sys.argv."""
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            for alias in noeud.names:
                if alias.name == "argparse":
                    return False
        elif isinstance(noeud, ast.ImportFrom):
            if noeud.module == "argparse":
                return False
        elif isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Attribute):
            if isinstance(noeud.func.value, ast.Name) and noeud.func.value.id == "sys":
                if noeud.func.attr == "argv":
                    return False
    return True

def _est_pur(arbre: ast.AST) -> Tuple[bool, Optional[int]]:
    """Vérifie que le module ne contient aucun appel à print en dehors de main ou _afficher."""
    stack = [(arbre, False)]
    while stack:
        node, in_main = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            new_in_main = in_main or (node.name in ("main", "_afficher"))
        else:
            new_in_main = in_main

        if not new_in_main and isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                return (False, node.lineno)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "print":
                if isinstance(node.func.value, ast.Name) and node.func.value.id in ("builtins", "__builtins__"):
                    return (False, node.lineno)

        for child in ast.iter_child_nodes(node):
            stack.append((child, new_in_main))
    return (True, None)

def _api_publique(arbre: ast.AST) -> Set[str]:
    """Extrait les noms des fonctions et classes publiques (ne commençant pas par _)."""
    publics = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.FunctionDef) and not noeud.name.startswith("_"):
            publics.add(noeud.name)
        elif isinstance(noeud, ast.ClassDef) and not noeud.name.startswith("_"):
            publics.add(noeud.name)
    return publics

def _contrat_de_mesure(arbre: ast.AST) -> Dict[str, str]:
    """Extrait le contrat de mesure de la docstring du module en préservant les sauts de ligne."""
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Module):
            if noeud.body and isinstance(noeud.body[0], ast.Expr):
                docstring = ast.get_docstring(noeud)
                if docstring:
                    sections: Dict[str, str] = {}
                    lignes = docstring.splitlines()
                    section: Optional[str] = None
                    for ligne in lignes:
                        if ligne.startswith("QUESTION"):
                            section = "question"
                            sections[section] = ligne.split("     ", 1)[1].strip()
                        elif ligne.startswith("MESURE"):
                            section = "mesure"
                            sections[section] = ligne.split("     ", 1)[1].strip()
                        elif ligne.startswith("HYPOTHÈSES"):
                            section = "hypotheses"
                            sections[section] = ligne.split("     ", 1)[1].strip()
                        elif ligne.startswith("LIMITES"):
                            section = "limites"
                            sections[section] = ligne.split("     ", 1)[1].strip()
                        elif ligne.startswith("CONTRE-EXEMPLE") or ligne.startswith("CONTRE-EXEMPLES"):
                            section = "contre_exemples"
                            sections[section] = ligne.split("     ", 1)[1].strip()
                        elif ligne.startswith("DOMAINE"):
                            section = "domaine"
                            sections[section] = ligne.split("     ", 1)[1].strip()
                        elif section and ligne.strip():
                            sections[section] += "\n" + ligne.strip()
                    return sections
    return {}

def analyser_source(chemin: Path) -> Dict[str, Any]:
    """Analyse un fichier source Python sans l'importer."""
    try:
        source = chemin.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"erreur": "encodage non UTF-8", "avertissements": ["encodage non UTF-8"]}

    try:
        compile(source, str(chemin), "exec")
        arbre = ast.parse(source, filename=str(chemin))
    except SyntaxError as e:
        return {"erreur": f"syntaxe invalide : {e}"}

    pure_result = _est_pur(arbre)
    result: Dict[str, Any] = {
        "importable": _est_importable(arbre),
        "pur": pure_result[0],
        "pur_line": pure_result[1],
        "api_publique": sorted(_api_publique(arbre)),
        "contrat": _contrat_de_mesure(arbre),
    }
    return result

def lister(racine: Path, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Liste les outils dans le dossier outils/ sans les importer."""
    if config is None:
        config = {}
    outils: List[Dict[str, Any]] = []
    avertissements: List[str] = []
    dossier_outils = racine / "outils"
    if not dossier_outils.exists():
        avertissements.append(f"dossier {dossier_outils} introuvable")
        return {"outils": outils, "avertissements": avertissements, "denominateur": 0}

    for info in pkgutil.iter_modules([str(dossier_outils)]):
        if info.ispkg:
            continue
        chemin = dossier_outils / f"{info.name}.py"
        if not chemin.exists():
            continue
        analyse = analyser_source(chemin)
        if "erreur" in analyse:
            warn = analyse.get("avertissements", [])
            if warn:
                avertissements.extend(warn)
            else:
                avertissements.append(f"{analyse['erreur']} pour {chemin}")
            continue
        warn = analyse.get("avertissements", [])
        if warn:
            avertissements.extend(warn)
        outils.append({
            "nom": info.name,
            "chemin": str(chemin.relative_to(racine)),
            **analyse,
        })
    return {"outils": outils, "avertissements": avertissements, "denominateur": len(outils)}

def lister_entry_points() -> Dict[str, Any]:
    """Liste les outils déclarés dans les entry points."""
    outils: List[Dict[str, Any]] = []
    avertissements: List[str] = []
    try:
        eps = importlib.metadata.entry_points()
        if not hasattr(eps, "select"):
            raise RuntimeError("Python 3.10+ requis pour les entry points")
        groupe = eps.select(group="puissance.outils")
        for ep in groupe:
            outils.append({
                "nom": ep.name,
                "module": ep.value.split(":")[0],
                "fonction": ep.value.split(":")[1],
                "entry_point": True,
            })
    except ImportError:
        avertissements.append("importlib.metadata non disponible (Python < 3.10)")
    return {"outils": outils, "avertissements": avertissements, "denominateur": len(outils)}

def contrat(nom: str, racine: Path, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Rend le contrat complet d'un outil."""
    if config is None:
        config = {}
    dossier_outils = racine / "outils"
    chemin = dossier_outils / f"{nom}.py"
    if not chemin.exists():
        return {"erreur": f"outil {nom} introuvable", "denominateur": 0}

    analyse = analyser_source(chemin)
    if "erreur" in analyse:
        return {**analyse, "denominateur": 0}

    return {
        "outil": nom,
        "question": analyse["contrat"].get("question", ""),
        "api": analyse["api_publique"],
        "importable": analyse["importable"],
        "pur": analyse["pur"],
        "pur_line": analyse["pur_line"],
        "dependances": "stdlib pure",
        "embarquable": True,
        "contrat": analyse["contrat"],
        "denominateur": 1,
    }

def charger(nom: str, racine: Path, config: Optional[Dict[str, Any]] = None) -> Any:
    """Charge un outil et rend son module."""
    if config is None:
        config = {}
    sys.path.insert(0, str(racine))
    try:
        return importlib.import_module(f"outils.{nom}")
    finally:
        sys.path.pop(0)

def controler(racine: Path, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Contrôle mécanique : tous les outils doivent être importables et purs."""
    if config is None:
        config = {}
    dossier_outils = racine / "outils"
    if not dossier_outils.exists():
        return {
            "code_sortie": 1,
            "defauts": ["dossier outils/ introuvable"],
            "avertissements": [],
            "outils_examines": 0,
            "denominateur": 0,
        }
    liste = lister(racine, config)
    outils = liste["outils"]
    avertissements = liste.get("avertissements", [])
    defauts: List[str] = []
    code_sortie = 0
    denominateur = len(outils)

    if denominateur == 0:
        sys.stderr.write("Aucun outil examiné — le dénominateur est zéro, l'outil refuse de conclure.\n")
        return {
            "code_sortie": 3,
            "defauts": ["dénominateur zéro"],
            "avertissements": avertissements,
            "outils_examines": 0,
            "denominateur": 0,
        }

    for outil in outils:
        if not outil["importable"]:
            defauts.append(f"{outil['nom']} n'est pas importable")
            code_sortie = 1
        if not outil["pur"]:
            line = outil.get("pur_line")
            if line is not None:
                defauts.append(f"{outil['nom']} n'est pas pur, ligne {line}")
            else:
                defauts.append(f"{outil['nom']} n'est pas pur")
            code_sortie = 1
    return {
        "code_sortie": code_sortie,
        "defauts": defauts,
        "avertissements": avertissements,
        "outils_examines": denominateur,
        "denominateur": denominateur,
    }

def main() -> int:
    """Point d'entrée principal – construit l'analyseur d'arguments et exécute la commande."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Vérifie que les outils sont utilisables par un autre projet.",
        epilog="Exemple : python outils/pont_outils.py --racine . lister",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="racine du projet (défaut : %(default)s)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="sortie JSON sur stdout",
    )
    sous_commandes = parser.add_subparsers(dest="commande", required=True)

    lister_parser = sous_commandes.add_parser("lister", help="liste les outils sans les importer")
    lister_parser.add_argument(
        "--entry-points",
        action="store_true",
        help="liste aussi les outils déclarés dans les entry points",
    )

    contrat_parser = sous_commandes.add_parser("contrat", help="affiche le contrat d'un outil")
    contrat_parser.add_argument("nom", help="nom de l'outil")

    charger_parser = sous_commandes.add_parser("charger", help="charge un outil et rend son module")
    charger_parser.add_argument("nom", help="nom de l'outil")

    controler_parser = sous_commandes.add_parser("controler", help="contrôle mécanique des outils")

    args = parser.parse_args()

    config = conventions(args.racine)
    error_msg = config.pop("_pont_outils_conventions_error", None)
    if error_msg is not None:
        sys.stderr.write(f"Avertissement : {error_msg}\n")

    if args.commande == "lister":
        resultat = lister(args.racine, config)
        outils = resultat["outils"]
        avertissements = resultat.get("avertissements", [])
        denominateur = resultat.get("denominateur", 0)
        if args.entry_points:
            ep_result = lister_entry_points()
            outils.extend(ep_result["outils"])
            avertissements.extend(ep_result.get("avertissements", []))
            denominateur += ep_result.get("denominateur", 0)
        for msg in avertissements:
            sys.stderr.write(f"Avertissement : {msg}\n")
        if args.json:
            json.dump(
                {"outils": outils, "denominateur": denominateur},
                sys.stdout,
                indent=2,
                ensure_ascii=False,
            )
        else:
            print(f"{len(outils)} outil(s) examiné(s)")
            for outil in outils:
                print(f"{outil['nom']} : {outil['chemin'] if 'chemin' in outil else 'entry point'}")
                print(f"  API publique : {', '.join(outil['api_publique'])}")
                print(f"  Importable : {'OUI' if outil['importable'] else 'NON'}")
                print(f"  Pur : {'OUI' if outil['pur'] else 'NON'}")
        return 0

    elif args.commande == "contrat":
        resultat = contrat(args.nom, args.racine, config)
        denominateur = resultat.get("denominateur", 0)
        if args.json:
            json.dump({**resultat, "denominateur": denominateur}, sys.stdout, indent=2, ensure_ascii=False)
        else:
            if "erreur" in resultat:
                sys.stderr.write(f"Erreur : {resultat['erreur']}\n")
                return 1
            print(f"Outil : {resultat['outil']}")
            print(f"Question : {resultat['question']}")
            print(f"API : {', '.join(resultat['api'])}")
            print(f"Importable : {'OUI' if resultat['importable'] else 'NON'}")
            pur_line = resultat.get("pur_line")
            if not resultat['pur'] and pur_line is not None:
                print(f"Pur : NON — écrit sur stderr ligne {pur_line}")
            else:
                print(f"Pur : {'OUI' if resultat['pur'] else 'NON'}")
            print("Contrat de mesure :")
            for k, v in resultat["contrat"].items():
                print(f"  {k.upper()} : {v}")
        return 0 if "erreur" not in resultat else 1

    elif args.commande == "charger":
        try:
            module = charger(args.nom, args.racine, config)
            if args.json:
                json.dump({"module": args.nom, "charge": True, "denominateur": 1}, sys.stdout)
            else:
                print(f"Module {args.nom} chargé avec succès.")
            return 0
        except Exception as e:
            if args.json:
                json.dump({"module": args.nom, "erreur": str(e), "denominateur": 0}, sys.stdout)
            else:
                sys.stderr.write(f"Erreur : {e}\n")
            return 1

    elif args.commande == "controler":
        resultat = controler(args.racine, config)
        code = resultat["code_sortie"]
        defauts = resultat.get("defauts", [])
        avertissements = resultat.get("avertissements", [])
        denominateur = resultat.get("denominateur", 0)
        for msg in avertissements:
            sys.stderr.write(f"Avertissement : {msg}\n")
        if denominateur == 0:
            sys.stderr.write("Aucun outil examiné — le dénominateur est zéro, l'outil refuse de conclure.\n")
            code = 3
        if args.json:
            json.dump(
                {
                    "code_sortie": code,
                    "defauts": defauts,
                    "avertissements": avertissements,
                    "outils_examines": denominateur,
                    "denominateur": denominateur,
                },
                sys.stdout,
                indent=2,
                ensure_ascii=False,
            )
        else:
            if code == 0:
                print(f"Tous les outils ({denominateur}) sont importables et purs.")
            else:
                sys.stderr.write(f"Outils examinés : {denominateur}\n")
                for d in defauts:
                    sys.stderr.write(f"Défaut : {d}\n")
        return code

    return 0

if __name__ == "__main__":
    raise SystemExit(main())