"""garde_execution.py
Outil de garde d'exécution restreinte.

Ce script vérifie, avant d'exécuter du code Python fourni, qu'il ne viole pas les
principes de confinement de *RestrictedPython*. Il compile le code avec
``compile_restricted`` et, selon le résultat, le signale comme refusé à la
compilation ou à l'exécution. Il expose également la surface des noms autorisés
dans les espaces de noms fournis par la bibliothèque.

Le script respecte le socle imposé : aucune dépendance externe autre que
*RestrictedPython*, encodage UTF-8, sortie JSON optionnelle, code de sortie
signifiant, etc.

CONTRAT DE MESURE
-----------------
QUESTION
    puis-je exécuter ce code sans conséquence accidentelle ?
MESURE
    compile_restricted refuse à la COMPILATION ; exécution dans un espace de
    noms composé et déclaré nom par nom.
HYPOTHÈSES
    RestrictedPython >= 8.3.0 (CVE-2026-56030 corrigé) ; le code est d'origine
    connue.
LIMITES
    ne borne NI le temps NI la mémoire ; n'isole pas le système de fichiers.
CONTRE-EXEMPLES
    safe_globals seul fait échouer ``sum(range(10))`` : 12 builtins inoffensifs
    manquent.
INVOCATION
    {outil} {fichier} --json
DOMAINE
    code Python bref, origine connue, essai rapide.
"""

from __future__ import annotations

import argparse
import builtins as _builtins
import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ------------------------------------------------------------
# Encodage UTF-8 pour stdout (conformité SOCLE)
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Racine portable
# ------------------------------------------------------------
RACINE = Path(__file__).resolve().parent

# ------------------------------------------------------------
# Import de RestrictedPython avec gestion d'absence
# ------------------------------------------------------------
try:
    from RestrictedPython import (
        compile_restricted,
        safe_globals,
        limited_builtins,
        utility_builtins,
    )
    from RestrictedPython.PrintCollector import PrintCollector
    import RestrictedPython
except ImportError:  # pragma: no cover
    compile_restricted = None  # type: ignore[assignment]
    safe_globals = None  # type: ignore[assignment]
    limited_builtins = None  # type: ignore[assignment]
    utility_builtins = None  # type: ignore[assignment]
    PrintCollector = None  # type: ignore[assignment]
    RestrictedPython = None  # type: ignore[assignment]

# ------------------------------------------------------------
# Constantes
# ------------------------------------------------------------
EXIT_OK = 0
EXIT_DEFECT = 1
EXIT_MISSING_RP = 2
EXIT_ZERO_DENOM = 3
EXIT_OLD_VERSION = 4

# Builtins manquants à ajouter (voir plan)
BUILTINS_MANQUANTS = [
    "sum",
    "min",
    "max",
    "any",
    "all",
    "enumerate",
    "map",
    "filter",
    "print",
    "list",
    "dict",
    "set",
]

# Builtins essentiels à vérifier après composition
BUILTINS_ESSENTIELS = [
    "list",
    "range",
    "sum",
    "min",
    "max",
    "any",
    "all",
    "enumerate",
    "map",
    "filter",
    "print",
    "dict",
    "set",
]

# ------------------------------------------------------------
# Fonctions du cœur (sans argparse, sans I/O)
# ------------------------------------------------------------
def verifier_version() -> bool:
    """Vérifie que RestrictedPython >= 8.3.0."""
    if RestrictedPython is None:
        return False
    ver_str = getattr(RestrictedPython, "__version__", "0.0.0")
    major, minor, *_ = (int(p) for p in ver_str.split("."))
    return (major, minor) >= (8, 3)

def charger_source(fichier: Path | None, source: str | None) -> str:
    """Renvoie le code source à analyser."""
    if source is not None:
        return source
    if fichier is None:
        raise ValueError("Aucun source fourni.")
    return fichier.read_text(encoding="utf-8")

def composer_globals(niveau: str) -> Dict[str, Any]:
    """Construit l'espace de noms à partir de safe_globals et du niveau choisi."""
    if safe_globals is None:
        raise RuntimeError("RestrictedPython non disponible.")
    env: Dict[str, Any] = safe_globals.copy()
    # Ajout des builtins manquants (12 noms inoffensifs)
    for name in BUILTINS_MANQUANTS:
        if name not in env:
            env[name] = getattr(_builtins, name)
    # Ajout des builtins complémentaires selon le niveau
    if niveau == "limited":
        if limited_builtins is None:
            raise RuntimeError("limited_builtins non disponible.")
        env.update(limited_builtins)
    elif niveau == "utility":
        if limited_builtins is None or utility_builtins is None:
            raise RuntimeError("limited_builtins ou utility_builtins non disponible.")
        env.update(limited_builtins)
        env.update(utility_builtins)
    # Capture des prints
    env["_print_"] = PrintCollector
    # Vérification que les builtins essentiels sont présents
    manquants = [b for b in BUILTINS_ESSENTIELS if b not in env]
    if manquants:
        raise RuntimeError(
            f"Builtins essentiels manquants dans l'espace de noms : {', '.join(manquants)}"
        )
    return env

def compiler(source: str) -> Tuple[bool, Any]:
    """Compile le code avec compile_restricted.\n
    Retourne (True, code_obj) ou (False, exception)."""
    try:
        code_obj = compile_restricted(source, filename="<source>", mode="exec")
        return True, code_obj
    except Exception as exc:  # pragma: no cover
        return False, exc

def executer(code_obj: Any, env: Dict[str, Any]) -> Tuple[bool, Any]:
    """Exécute le code compilé dans l'environnement fourni.\n
    Retourne (True, None) si succès, sinon (False, exception)."""
    try:
        exec(code_obj, env)  # noqa: S102
        return True, None
    except Exception as exc:  # pragma: no cover
        return False, exc

def analyser(source: str, niveau: str) -> Dict[str, Any]:
    """Analyse le code fourni.\n
    Retourne un dictionnaire décrivant le verdict."""
    ok_compile, compile_res = compiler(source)
    if not ok_compile:
        return {
            "verdict": "refusé_compilation",
            "niveau": "compilation",
            "refusal_type": "compilation",
            "error": str(compile_res),
        }

    env = composer_globals(niveau)

    # Calcul des builtins ajoutés, à inclure dans tous les rapports d'exécution
    added_builtins = [
        n
        for n in env.get("__builtins__", {})
        if n not in safe_globals.get("__builtins__", {})
    ]

    ok_exec, exec_res = executer(compile_res, env)

    # Récupération de la sortie imprimée
    sortie = ""
    collector = env.get("_print_")
    if collector is not None:
        sortie = "\n".join(collector.output)  # type: ignore[attr-defined]

    if not ok_exec:
        return {
            "verdict": "refusé_execution",
            "niveau": "execution",
            "refusal_type": "execution",
            "error": str(exec_res),
            "added_builtins": sorted(added_builtins),
        }

    # Succès
    return {
        "verdict": "passe",
        "niveau": "execution",
        "refusal_type": None,
        "output": sortie,
        "globals_used": sorted(env.get("__builtins__", {})),
        "added_builtins": sorted(added_builtins),
        "namespace": niveau,
    }

def surface(niveau: str) -> List[str]:
    """Retourne la liste des noms de l'environnement construit pour le niveau donné.

    Pour le niveau « safe », la surface doit correspondre exactement aux 81 noms
    fournis par ``safe_globals['__builtins__']`` sans les ajouts supplémentaires.
    """
    if safe_globals is None:
        raise RuntimeError("RestrictedPython non disponible.")
    if niveau == "safe":
        # Surface brute de safe_globals
        return sorted(safe_globals.get("__builtins__", {}).keys())
    env = composer_globals(niveau)
    return sorted(env.get("__builtins__", {}).keys())

# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------
def construire_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Garde d'exécution restreinte – détecte les accidents avant exécution.",
        epilog=textwrap.dedent(
            """\
            Exemple d'appel :
                python -m outils.garde_execution --source "x = 1 + 1"
                python -m outils.garde_execution mon_script.py --niveau utility --json
            """
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "fichier",
        nargs="?",
        type=Path,
        help="Fichier contenant le code à analyser.",
    )
    source_group.add_argument(
        "--source",
        type=str,
        help="Code source fourni directement en ligne de commande.",
    )
    parser.add_argument(
        "--niveau",
        choices=["safe", "limited", "utility"],
        default="safe",
        help="Niveau d'espace de noms à utiliser (défaut : safe).",
    )
    parser.add_argument(
        "--surface",
        action="store_true",
        help="Affiche la surface des noms autorisés pour le niveau choisi et quitte.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produit la sortie au format JSON unique.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Chemin racine à utiliser à la place de celui dérivé du script.",
    )
    return parser

def main() -> int:
    # Avertissement permanent (stderr)
    warning = (
        "Ce garde protège de l'ACCIDENT, pas d'un ADVERSAIRE. RestrictedPython "
        "n'est pas un bac à sable — son propre projet le déclare, et CVE-2026-56030 "
        "a montré ses gardes contournables. Pour du code hostile : conteneur."
    )
    print(warning, file=sys.stderr)

    parser = construire_parser()
    args = parser.parse_args()

    # Gestion des cas de dénominateur nul (protocole de refus)
    if args.fichier is None and args.source is None and not args.surface:
        print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        if args.json:
            json.dump({
                "contrat": _contrat(),
                "denominateur": 0,
                "erreur": "Aucun source fourni."
            }, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return EXIT_ZERO_DENOM

    # Gestion de l'absence de RestrictedPython
    if compile_restricted is None:
        print("Denominateur nul : RestrictedPython non disponible, refus de conclure.", file=sys.stderr)
        if args.json:
            json.dump({"contrat": _contrat(), "denominateur": 0, "erreur": "RestrictedPython n'est pas installé."}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return EXIT_ZERO_DENOM

    # Vérification de version
    if not verifier_version():
        print("Denominateur nul : version de RestrictedPython trop ancienne (< 8.3.0), refus de conclure.", file=sys.stderr)
        if args.json:
            json.dump({"contrat": _contrat(), "denominateur": 0, "erreur": "version de RestrictedPython trop ancienne (< 8.3.0)."}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return EXIT_ZERO_DENOM

    # Option surface
    if args.surface:
        try:
            noms = surface(args.niveau)
        except Exception as exc:
            print(f"Denominateur nul : erreur lors du calcul de la surface, refus de conclure : {exc}", file=sys.stderr)
            if args.json:
                json.dump({"contrat": _contrat(), "denominateur": 0, "erreur": str(exc)}, sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
            return EXIT_ZERO_DENOM
        if args.json:
            payload = {"contrat": _contrat(), "denominateur": 1, "surface": noms}
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            for n in noms:
                print(n)
        return EXIT_OK

    # Chargement du code source
    try:
        source = charger_source(args.fichier, args.source)
    except Exception as exc:  # pragma: no cover
        print(f"Denominateur nul : erreur de lecture du source, refus de conclure : {exc}", file=sys.stderr)
        if args.json:
            json.dump({"contrat": _contrat(), "denominateur": 0, "erreur": str(exc)}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return EXIT_ZERO_DENOM

    try:
        resultat = analyser(source, args.niveau)
    except Exception as exc:
        print(f"Denominateur nul : erreur lors de l'analyse, refus de conclure : {exc}", file=sys.stderr)
        if args.json:
            json.dump({"contrat": _contrat(), "denominateur": 0, "erreur": str(exc)}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return EXIT_ZERO_DENOM

    # Construction de la sortie
    if args.json:
        payload = {"contrat": _contrat(), "denominateur": 1, "resultat": resultat}
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        _afficher_humain(resultat)

    # Code de sortie selon le verdict
    return EXIT_OK if resultat["verdict"] == "passe" else EXIT_DEFECT

def _contrat() -> Dict[str, str]:
    """Renvoie le contrat de mesure sous forme de dictionnaire."""
    return {
        "QUESTION": "puis-je exécuter ce code sans conséquence accidentelle ?",
        "MESURE": "compile_restricted refuse à la COMPILATION ; exécution dans un espace de noms composé et déclaré nom par nom.",
        "HYPOTHÈSES": "RestrictedPython >= 8.3.0 (CVE-2026-56030 corrigé) ; le code est d'origine connue.",
        "LIMITES": "ne borne NI le temps NI la mémoire ; n'isole pas le système de fichiers.",
        "CONTRE_EXEMPLES": "safe_globals seul fait échouer sum(range(10)) : 12 builtins inoffensifs manquent.",
        "DOMAINE": "code Python bref, origine connue, essai rapide",
    }

def _afficher_humain(res: Dict[str, Any]) -> None:
    """Affiche le résultat de façon lisible pour un humain."""
    verdict = res.get("verdict")
    print(f"Verdict : {verdict}")
    if verdict.startswith("refusé"):
        print(f"Niveau : {res.get('niveau')}")
        print(f"Type de refus : {res.get('refusal_type')}")
        print(f"Erreur : {res.get('error')}")
        added = res.get("added_builtins", [])
        if added:
            print("Builtins ajoutés :")
            for name in added:
                print(f"  - {name}")
    elif verdict == "passe":
        output = res.get("output", "")
        if output:
            print("Sortie capturée :")
            print(output)
        else:
            print("Aucune sortie capturée.")
        print(f"Espace de noms utilisé : {res.get('namespace')}")
        print("Builtins disponibles :")
        for name in res.get("globals_used", []):
            print(f"  - {name}")
        added = res.get("added_builtins", [])
        if added:
            print("Builtins ajoutés :")
            for name in added:
                print(f"  - {name}")
    else:
        print("Résultat inattendu :", res)

if __name__ == "__main__":
    raise SystemExit(main())