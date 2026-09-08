"""
QUESTION : Comment éviter de recalculer ce résultat coûteux ?
MESURE : Analyse statique d'un source Python pour détecter l'absence de mécanismes de mise en cache (décorateurs ou modules).
HYPOTHESES : Une fonction sans décorateur de cache ni utilisation de module de cache est susceptible de recalculer un résultat coûteux.
LIMITES : Ne détecte pas les caches personnalisés non standard (ex: dictionnaire global). N'évalue pas le coût réel de la fonction.
CONTRE-EXEMPLES : Une fonction simple sans arguments ou avec effets de bord ne nécessite pas de cache, mais sera signalée.
INVOCATION
    {outil} {fichier} --json
DOMAINE : Fichiers source Python 3.14.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

# ------------------------------------------------------------
# Encodage UTF‑8 pour les consoles Windows (règle 2 du socle)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
# ------------------------------------------------------------

DECORATEURS_CACHE = {
    "lru_cache",
    "cache",
    "cached_property",
    "cached",
    "cachedmethod",
    "fifo_cache",
    "lfu_cache",
    "rr_cache",
    "ttl_cache",
    "tlru_cache",
}

MODULES_CACHE = {
    "functools",
    "shelve",
    "dbm",
    "pickle",
    "cachetools",
    "redis",
    "filelock",
    "dill",
}


def extraire_contrat() -> dict[str, str]:
    """Retourne le contrat de l'outil tel que déclaré dans la docstring."""
    return {
        "QUESTION": "Comment éviter de recalculer ce résultat coûteux ?",
        "MESURE": "Analyse statique d'un source Python pour détecter l'absence de mécanismes de mise en cache (décorateurs ou modules).",
        "HYPOTHESES": "Une fonction sans décorateur de cache ni utilisation de module de cache est susceptible de recalculer un résultat coûteux.",
        "LIMITES": "Ne détecte pas les caches personnalisés non standard (ex: dictionnaire global). N'évalue pas le coût réel de la fonction.",
        "CONTRE-EXEMPLES": "Une fonction simple sans arguments ou avec effets de bord ne nécessite pas de cache, mais sera signalée.",
        "DOMAINE": "Fichiers source Python 3.14.",
    }


def verifier_validite_source(chemin: Path) -> tuple[bool, str]:
    """Vérifie l'existence, la lisibilité et la validité syntaxique d'un source Python."""
    try:
        # Lecture tolérante : remplace les caractères illisibles.
        source = chemin.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False, f"Fichier introuvable : {chemin}"
    except PermissionError:
        return False, f"Fichier illisible (permission) : {chemin}"
    except IsADirectoryError:
        return False, f"La cible est un répertoire, pas un fichier : {chemin}"
    except OSError as e:
        return False, f"Erreur d'accès au fichier : {e}"
    except UnicodeDecodeError as e:
        return False, f"Erreur de décodage UTF‑8 : {e}"

    try:
        compile(source, str(chemin), "exec")
    except SyntaxError as e:
        return False, f"Erreur de syntaxe ligne {e.lineno} : {e.msg}"
    except Exception as e:
        return False, f"Erreur de compilation : {type(e).__name__}: {e}"

    return True, source


def _obtenir_nom_decorateur(deco: ast.expr) -> str:
    """Extrait le nom d'un décorateur, gérant les appels et les attributs."""
    if isinstance(deco, ast.Name):
        return deco.id
    if isinstance(deco, ast.Attribute):
        return deco.attr
    if isinstance(deco, ast.Call):
        return _obtenir_nom_decorateur(deco.func)
    return ""


def _a_import_cache(arbre: ast.AST) -> bool:
    """Détecte les imports de modules de cache dans l'arbre AST."""
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            if any(alias.name in MODULES_CACHE for alias in noeud.names):
                return True
        elif isinstance(noeud, ast.ImportFrom):
            if noeud.module in MODULES_CACHE:
                return True
    return False


def analyser_source(source: str, nom_fichier: str) -> list[dict[str, Any]]:
    """Analyse les fonctions d'un source pour détecter la présence de cache."""
    arbre = ast.parse(source, nom_fichier)
    fichier_a_import_cache = _a_import_cache(arbre)

    fonctions: list[dict[str, Any]] = []
    for noeud in ast.walk(arbre):
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a_cache = False
            for dec in noeud.decorator_list:
                nom_dec = _obtenir_nom_decorateur(dec)
                if nom_dec in DECORATEURS_CACHE:
                    a_cache = True
                    break

            if not a_cache and fichier_a_import_cache:
                a_cache = True

            fonctions.append(
                {
                    "nom": noeud.name,
                    "ligne": noeud.lineno,
                    "a_cache": a_cache,
                }
            )
    return fonctions


def _module_est_disponible(nom: str) -> bool:
    """Vérifie, sans importer, si un module est présent dans l'environnement."""
    return importlib.util.find_spec(nom) is not None


def main() -> int:
    """Point d'entrée principal de l'outil."""
    parseur = argparse.ArgumentParser(
        prog="mettre_en_cache.py",
        description="Analyse un source Python pour détecter l'absence de mise en cache des résultats de fonctions.",
        epilog="Exemple : python mettre_en_cache.py mon_script.py --json",
    )
    parseur.add_argument("cible", type=str, help="Chemin du fichier source Python à analyser")
    parseur.add_argument("--racine", type=str, help="Surcharge la racine et l'insère en tête de sys.path")
    parseur.add_argument("--json", action="store_true", help="Rend un seul objet JSON sur stdout")

    args = parseur.parse_args()

    racine = Path(__file__).resolve().parent
    if args.racine:
        racine = Path(args.racine).resolve()
        if racine.is_dir():
            sys.path.insert(0, str(racine))

    cible = Path(args.cible)
    if not cible.is_absolute():
        cible = racine / cible

    valide, source_ou_msg = verifier_validite_source(cible)

    if not valide:
        print(f"Erreur : {source_ou_msg}", file=sys.stderr)
        return 2  # conformité R1/R2/R3 : code non nul, message clair

    source = source_ou_msg
    fonctions = analyser_source(source, str(cible))

    denominateur = len(fonctions)
    if denominateur == 0:
        print("Dénominateur nul : aucune fonction examinée, refus de conclure.", file=sys.stderr)
        if args.json:
            sortie = {
                "contrat": extraire_contrat(),
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
            }
            print(json.dumps(sortie, ensure_ascii=False, indent=2))
        return 3

    examines = [
        {"nom": f["nom"], "ligne": f["ligne"], "defaut": not f["a_cache"]} for f in fonctions
    ]
    examines_tronques = False
    if len(examines) > 200:
        examines = examines[:200]
        examines_tronques = True

    defect_trouve = any(e["defaut"] for e in examines)
    code_sortie = 1 if defect_trouve else 0

    modules_manquants = [m for m in ("cachetools", "redis", "filelock", "dill") if not _module_est_disponible(m)]
    mode_degrade = "Aucun" if not modules_manquants else f"Mode dégradé : modules manquants {', '.join(modules_manquants)}"

    if args.json:
        sortie = {
            "contrat": extraire_contrat(),
            "denominateur": denominateur,
            "examines": examines,
            "examines_tronques": examines_tronques,
            "mode": mode_degrade,
        }
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    else:
        print(mode_degrade)
        print(f"Fonctions examinées : {denominateur}")
        for ex in examines:
            statut = "DÉFAUT" if ex["defaut"] else "OK"
            print(f"  [{statut}] {ex['nom']} (ligne {ex['ligne']})")
        if defect_trouve:
            print("Défaut trouvé : au moins une fonction n'est pas mise en cache.")
        else:
            print("Rien à signaler : toutes les fonctions examinées sont mises en cache.")

    return code_sortie


if __name__ == "__main__":
    raise SystemExit(main())