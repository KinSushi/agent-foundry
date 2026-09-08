"""
QUESTION      Comment enregistrer les événements de manière exploitable ?
MESURE        Présence d'appels au module logging ou loguru, absence d'usage brut de print pour journaliser, et usage de structures JSON ou d'arguments extra.
HYPOTHESES    Le code source Python est accessible et syntaxiquement valide.
LIMITES       L'outil ne voit que le code source, pas le comportement à l'exécution ni la configuration dynamique des handlers.
CONTRE-EXEMPLES Un script qui écrit dans un fichier binaire ou qui utilise sys.stdout.write directement sans passer par logging.
INVOCATION
    {outil} {fichier} --json
DOMAINE       Scripts et modules Python 3.14.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from pathlib import Path
import argparse
import json
import ast

RACINE = Path(__file__).resolve().parent

__all__ = ["analyser_cible", "analyser_fichier", "CONTRAT"]

CONTRAT = {
    "QUESTION": "Comment enregistrer les événements de manière exploitable ?",
    "MESURE": "Présence d'appels au module logging ou loguru, absence d'usage brut de print pour journaliser, et usage de structures JSON ou d'arguments extra.",
    "HYPOTHESES": "Le code source Python est accessible et syntaxiquement valide.",
    "LIMITES": "L'outil ne voit que le code source, pas le comportement à l'exécution ni la configuration dynamique des handlers.",
    "CONTRE-EXEMPLES": "Un script qui écrit dans un fichier binaire ou qui utilise sys.stdout.write directement sans passer par logging.",
    "DOMAINE": "Scripts et modules Python 3.14."
}


def analyser_fichier(chemin: Path) -> dict:
    """Analyse un fichier Python pour déterminer sa capacité à journaliser de façon exploitable."""
    try:
        source = chemin.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as e:
        return {
            "nom": str(chemin),
            "valide": False,
            "erreur": str(e),
            "defaut": True,
        }

    try:
        compile(source, str(chemin), "exec")
    except SyntaxError as e:
        return {
            "nom": str(chemin),
            "valide": False,
            "erreur": f"SyntaxError: {e}",
            "defaut": True,
        }
    except Exception as e:
        return {
            "nom": str(chemin),
            "valide": False,
            "erreur": f"Erreur de compilation: {e}",
            "defaut": True,
        }

    try:
        arbre = ast.parse(source)
    except SyntaxError:
        return {
            "nom": str(chemin),
            "valide": False,
            "erreur": "AST parse error",
            "defaut": True,
        }
    except Exception as e:
        return {
            "nom": str(chemin),
            "valide": False,
            "erreur": f"Erreur AST: {e}",
            "defaut": True,
        }

    utilise_logging = False
    utilise_print = False
    utilise_structure = False
    utilise_loguru = False

    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            for alias in noeud.names:
                if alias.name == "loguru":
                    utilise_loguru = True
        elif isinstance(noeud, ast.ImportFrom):
            if noeud.module == "loguru":
                utilise_loguru = True
        elif isinstance(noeud, ast.Call):
            f = noeud.func
            if isinstance(f, ast.Attribute):
                nom_attr = f.attr
                if nom_attr in (
                    "info",
                    "debug",
                    "warning",
                    "error",
                    "critical",
                    "exception",
                    "log",
                ):
                    if isinstance(f.value, ast.Name) and f.value.id in (
                        "logging",
                        "logger",
                        "log",
                    ):
                        utilise_logging = True
                        if noeud.keywords:
                            for kw in noeud.keywords:
                                if kw.arg == "extra":
                                    utilise_structure = True
                elif nom_attr == "dumps" and isinstance(
                    f.value, ast.Name
                ) and f.value.id == "json":
                    utilise_structure = True
            elif isinstance(f, ast.Name) and f.id == "print":
                utilise_print = True

    defaut = False
    if utilise_print and not (utilise_logging or utilise_loguru):
        defaut = True
    if utilise_logging and not (utilise_structure or utilise_loguru):
        defaut = True

    return {
        "nom": str(chemin),
        "valide": True,
        "utilise_logging": utilise_logging,
        "utilise_print": utilise_print,
        "utilise_structure": utilise_structure,
        "utilise_loguru": utilise_loguru,
        "defaut": defaut,
    }


def analyser_cible(cible: Path) -> tuple[int, list[dict], bool]:
    """Analyse une cible (fichier ou répertoire) et retourne le nombre d'éléments, les résultats et la présence de défauts."""
    fichiers: list[Path] = []
    if cible.is_file():
        if cible.suffix == ".py":
            fichiers.append(cible)
        else:
            # Mauvais type : fichier qui n'est pas un .py
            print(
                f"Erreur: le fichier {cible} n'est pas un fichier Python (.py).",
                file=sys.stderr,
            )
            return 0, [], True
    elif cible.is_dir():
        fichiers = list(cible.rglob("*.py"))
    else:
        # Cas impossible grâce aux vérifications précédentes
        return 0, [], True

    examines: list[dict] = []
    defaut_trouve = False
    for f in fichiers:
        res = analyser_fichier(f)
        examines.append(res)
        if res.get("defaut"):
            defaut_trouve = True

    return len(examines), examines, defaut_trouve


def main() -> int:
    parseur = argparse.ArgumentParser(
        description="Analyse comment le code enregistre les événements de manière exploitable.",
        epilog="Exemple: python journaliser_structure.py ./mon_script.py --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument(
        "cible", type=Path, help="Fichier ou répertoire à analyser"
    )
    parseur.add_argument(
        "--racine",
        type=Path,
        help="Surcharge la racine et l'insère dans sys.path",
    )
    parseur.add_argument(
        "--json", action="store_true", help="Sortie JSON structurée"
    )

    args = parseur.parse_args()

    if args.racine:
        racine = args.racine.resolve()
        sys.path.insert(0, str(racine))
    else:
        racine = RACINE

    cible = args.cible
    if not cible.exists():
        print(f"Erreur: la cible {cible} n'existe pas.", file=sys.stderr)
        return 1

    if not cible.is_file() and not cible.is_dir():
        print(
            f"Erreur: la cible {cible} n'est ni un fichier ni un répertoire.",
            file=sys.stderr,
        )
        return 1

    denominateur, examines, defaut_trouve = analyser_cible(cible)

    if denominateur == 0:
        print(
            "Aucun fichier Python à examiner. Refus de conclure.",
            file=sys.stderr,
        )
        return 3

    examines_tronques = False
    if len(examines) > 200:
        examines = examines[:200]
        examines_tronques = True

    if args.json:
        sortie = {
            "contrat": CONTRAT,
            "denominateur": denominateur,
            "examines": examines,
            "examines_tronques": examines_tronques,
            "defaut_trouve": defaut_trouve,
        }
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    else:
        print(f"Fichiers examinés: {denominateur}")
        for ex in examines:
            statut = "DÉFAUT" if ex.get("defaut") else "OK"
            print(f"  [{statut}] {ex['nom']}")
            if ex.get("defaut"):
                print(
                    f"    -> logging: {ex.get('utilise_logging')}, "
                    f"print: {ex.get('utilise_print')}, "
                    f"structure: {ex.get('utilise_structure')}, "
                    f"loguru: {ex.get('utilise_loguru')}"
                )

    return 1 if defaut_trouve else 0


if __name__ == "__main__":
    raise SystemExit(main())