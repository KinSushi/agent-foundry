"""
QUESTION      Puis-je partager un cache entre processus ?
MESURE        Analyse statique de fichiers Python pour detecter l usage de dbm/pickle avec multiprocessing sans verrou.
HYPOTHESES    Un cache disque partage entre processus necessite un verrou (multiprocessing.Lock) pour etre distribue-safe.
LIMITES       L analyse est statique et basee sur l AST, elle ne couvre pas les alias complexes ou le code dynamique.
CONTRE-EXEMPLES Un fichier utilisant dbm seul (sans multiprocessing) n est pas un defaut.
INVOCATION
    {outil} --racine {fichier} --json
DOMAINE       Fichiers source Python (.py) dans une arborescence donnee.
"""
from __future__ import annotations

import sys
import ast
import argparse
import json
from pathlib import Path
from typing import Any, NoReturn

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = [
    "compiler_source",
    "analyser_arbre",
    "examiner_fichier",
    "examiner_racine",
    "main",
]

_CONTRAT = {
    "QUESTION": "Puis-je partager un cache entre processus ?",
    "MESURE": "Analyse statique de fichiers Python pour detecter l usage de dbm/pickle avec multiprocessing sans verrou.",
    "HYPOTHESES": "Un cache disque partage entre processus necessite un verrou (multiprocessing.Lock) pour etre distribue-safe.",
    "LIMITES": "L analyse est statique et basee sur l AST, elle ne couvre pas les alias complexes ou le code dynamique.",
    "CONTRE-EXEMPLES": "Un fichier utilisant dbm seul (sans multiprocessing) n est pas un defaut.",
    "INVOCATION": "{outil} --racine {fichier} --json",
    "DOMAINE": "Fichiers source Python (.py) dans une arborescence donnee.",
}


def _cli_error(message: str) -> NoReturn:
    """Gestion des erreurs d'argumentaire, conforme à la règle du socle."""
    if "--json" in sys.argv:
        print(
            json.dumps(
                {
                    "erreur": f"arguments invalides : {message}",
                    "denominateur": 0,
                    "reponse": "REFUS",
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)
    # argparse.error écrit déjà sur stderr ; on reproduit le même comportement.
    sys.stderr.write(f"{message}\n")
    raise SystemExit(2)


def compiler_source(source: str, nom: str) -> tuple[bool, str | None, ast.AST | None]:
    """Compile le source pour valider sa syntaxe, puis génère l'AST."""
    try:
        compile(source, nom, "exec")
    except Exception as exc:
        return False, f"compilation impossible : {exc}", None
    try:
        arbre = compile(source, nom, "exec", flags=ast.PyCF_ONLY_AST)
    except Exception as exc:
        return False, f"erreur AST : {exc}", None
    return True, None, arbre


def analyser_arbre(arbre: ast.AST) -> dict[str, Any]:
    """Analyse l'AST pour détecter les motifs de cache partagé non protégé."""
    modules: set[str] = set()
    aliases: dict[str, str] = {}
    appels: list[dict[str, Any]] = []

    for node in ast.walk(arbre):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                if alias.asname:
                    aliases[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
                for alias in node.names:
                    if node.module == "multiprocessing" and alias.name in ("Lock", "RLock"):
                        aliases[alias.asname or alias.name] = f"multiprocessing.{alias.name}"
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                nom_module = None
                if isinstance(func.value, ast.Name):
                    nom_module = aliases.get(func.value.id, func.value.id)
                if nom_module == "dbm" and func.attr == "open":
                    appels.append({"type": "dbm.open", "ligne": node.lineno})
                elif nom_module == "multiprocessing" and func.attr in ("Lock", "RLock"):
                    appels.append({"type": "verrou", "ligne": node.lineno})
                elif nom_module == "pickle" and func.attr == "dump":
                    appels.append({"type": "pickle.dump", "ligne": node.lineno})
            elif isinstance(func, ast.Name):
                nom_resolu = aliases.get(func.id)
                if nom_resolu in ("multiprocessing.Lock", "multiprocessing.RLock"):
                    appels.append({"type": "verrou", "ligne": node.lineno})

    a_mp = "multiprocessing" in modules
    a_cache = any(a["type"] in ("dbm.open", "pickle.dump") for a in appels)
    a_verrou = any(a["type"] == "verrou" for a in appels)

    defauts: list[str] = []
    if a_cache and a_mp and not a_verrou:
        defauts.append(
            "Cache disque (dbm/pickle) partagé avec multiprocessing sans verrou (Lock/RLock)."
        )

    return {
        "a_defaut": len(defauts) > 0,
        "defauts": defauts,
        "details": {
            "multiprocessing": a_mp,
            "cache_disque": a_cache,
            "verrou": a_verrou,
        },
    }


def examiner_fichier(chemin: Path) -> dict[str, Any]:
    """Examine un fichier Python et retourne le résultat de l'analyse."""
    try:
        source = chemin.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "chemin": str(chemin),
            "valide": False,
            "erreur": f"lecture impossible : {exc}",
            "analyse": None,
        }
    valide, erreur, arbre = compiler_source(source, str(chemin))
    if not valide or arbre is None:
        return {
            "chemin": str(chemin),
            "valide": False,
            "erreur": erreur,
            "analyse": None,
        }
    return {
        "chemin": str(chemin),
        "valide": True,
        "erreur": None,
        "analyse": analyser_arbre(arbre),
    }


def examiner_racine(racine: Path) -> tuple[list[dict[str, Any]], int]:
    """Examine tous les fichiers .py sous la racine donnée."""
    if racine.is_file():
        fichiers = [racine] if racine.suffix == ".py" else []
    elif racine.is_dir():
        fichiers = sorted(racine.rglob("*.py"))
    else:
        return [], 0

    resultats = [examiner_fichier(f) for f in fichiers]
    return resultats, len(resultats)


def main() -> int:
    """Point d'entrée de l'outil en ligne de commande."""
    parser = argparse.ArgumentParser(
        prog="cache_distribue.py",
        description="Analyse le partage de cache entre processus dans du code Python.",
        epilog="Exemple : python cache_distribue.py --racine ./src",
    )
    # Remplacement de la méthode error par la fonction CLI conforme.
    parser.error = _cli_error  # type: ignore[attr-defined]

    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Racine du projet à analyser (défaut : répertoire de l'outil).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON au lieu de texte humain.",
    )
    args = parser.parse_args()

    racine = args.racine.resolve()
    if not racine.exists():
        msg = f"Erreur : la cible {racine} n'existe pas."
        if args.json:
            print(json.dumps({"erreur": msg, "denominateur": 0, "reponse": "REFUS"}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 2
    if not (racine.is_dir() or racine.suffix == ".py"):
        msg = f"Erreur : la cible {racine} n'est ni un dossier ni un fichier Python."
        if args.json:
            print(json.dumps({"erreur": msg, "denominateur": 0, "reponse": "REFUS"}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 2

    racine_path = racine if racine.is_dir() else racine.parent
    racine_str = str(racine_path)
    if racine_str not in sys.path:
        sys.path.insert(0, racine_str)

    resultats, denominateur = examiner_racine(racine)

    if denominateur == 0:
        print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        if args.json:
            obj = {
                "contrat": _CONTRAT,
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
                "reponse": "REFUS",
            }
            print(json.dumps(obj, ensure_ascii=False, indent=2))
        return 3

    defauts_trouves = any(
        (not r["valide"])
        or (r["valide"] and r["analyse"] is not None and r["analyse"]["a_defaut"])
        for r in resultats
    )

    if args.json:
        examines = [
            {
                "nom": Path(r["chemin"]).name,
                "chemin": r["chemin"],
                "valide": r["valide"],
                "erreur": r["erreur"],
                "analyse": r["analyse"],
            }
            for r in resultats
        ]
        examines_tronques = len(examines) > 200
        if examines_tronques:
            examines = examines[:200]

        obj = {
            "contrat": _CONTRAT,
            "denominateur": denominateur,
            "examines": examines,
            "examines_tronques": examines_tronques,
            "reponse": "DEFAUT" if defauts_trouves else "OK",
        }
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        if defauts_trouves:
            print("Défauts trouvés :")
            for r in resultats:
                if not r["valide"]:
                    print(f"  {r['chemin']}: {r['erreur']}")
                elif r["analyse"] and r["analyse"]["a_defaut"]:
                    print(f"  {r['chemin']}:")
                    for d in r["analyse"]["defauts"]:
                        print(f"    - {d}")
        else:
            print("Rien à signaler : aucun défaut de cache distribué trouvé.")

    return 1 if defauts_trouves else 0


if __name__ == "__main__":
    raise SystemExit(main())