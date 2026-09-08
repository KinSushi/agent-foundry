"""QUESTION
Quel est le chemin d’exécution de cette requête ?
MESURE
Enregistrement séquentiel des appels de fonctions (nom, fichier, ligne) lors de l'exécution du script cible.
HYPOTHESES
Le script cible est exécutable en Python 3.14, ne dépend pas de modules natifs non‑installés et ne modifie pas le processus d’interprétation.
LIMITES
Les appels exécutés dans du code C (extensions, modules intégrés) ne sont pas visibles ; seules les fonctions Python sont tracées.
CONTRE-EXEMPLES
Un script qui utilise uniquement du code C (ex. : module `math`) ne produira aucun élément dans le chemin d’exécution.
DOMAINE
Analyse de scripts Python purs, utile pour le débogage ou la génération de traces d’exécution à des fins d’audit.
"""

from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path
from types import FrameType
from typing import List, Dict, Any, Tuple

# 2. Encodage en tête
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 6. Racine portable
RACINE: Path = Path(__file__).resolve().parent

__all__ = [
    "tracer_execution",
    "format_result_humain",
    "format_result_json",
    "main",
]

def _charger_script(source_path: Path) -> str:
    """Lit le fichier source et renvoie son texte."""
    try:
        return source_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise OSError(f"Impossible de lire le fichier cible : {exc}") from exc

def _verifier_compilable(source: str, nom: str) -> None:
    """Vérifie que le code source peut être compilé."""
    try:
        compile(source, nom, "exec")
    except Exception as exc:
        raise SyntaxError(f"Le code source n’est pas compilable : {exc}") from exc

def tracer_execution(
    cible: Path,
    racine: Path | None = None,
) -> Tuple[int, List[str], bool, Dict[str, Any]]:
    """
    Exécute le script *cible* sous trace et renvoie :

    - denominator : nombre total d'évènements (appels) enregistrés.
    - examines    : liste des identifiants « module.fonction » (max 200).
    - tronque      : booléen indiquant si la liste a été tronquée.
    - meta         : dictionnaire contenant le contrat et les métadonnées.
    """
    if racine is None:
        racine = RACINE
    sys.path.insert(0, str(racine))

    source = _charger_script(cible)
    _verifier_compilable(source, str(cible))

    calls: List[str] = []

    def _trace(frame: FrameType, event: str, arg: Any) -> None:
        if event == "call":
            code = frame.f_code
            mod = frame.f_globals.get("__name__", "")
            func = code.co_name
            identifier = f"{mod}.{func}"
            calls.append(identifier)

    sys.settrace(_trace)
    try:
        exec(compile(source, str(cible), "exec"), {"__name__": "__main__"})
    finally:
        sys.settrace(None)

    denominator = len(calls)
    tronque = denominator > 200
    examines = calls[:200] if tronque else calls

    contrat = {
        "QUESTION": "Quel est le chemin d’exécution de cette requête ?",
        "MESURE": "Enregistrement séquentiel des appels de fonctions Python.",
        "HYPOTHESES": "Le script s’exécute sans code natif non‑installé.",
        "LIMITES": "Les appels dans du code C ne sont pas visibles.",
        "CONTRE-EXEMPLES": "Un script ne contenant que du code C ne produit aucune trace.",
        "DOMAINE": "Débogage et audit de scripts Python purs.",
    }

    meta = {
        "denominateur": denominator,
        "contrat": contrat,
        "examines": examines,
        "examines_tronques": tronque,
    }

    return denominator, examines, tronque, meta

def format_result_humain(denom: int, examines: List[str], tronque: bool) -> str:
    """Formate le résultat pour l’affichage humain."""
    if denom == 0:
        return "Aucun appel enregistré – impossible de conclure."
    lignes = [f"Nombre d’appels enregistrés : {denom}"]
    lignes.append("Chemin d’exécution (premiers appels) :")
    for idx, ident in enumerate(examines, 1):
        lignes.append(f"  {idx:3d}. {ident}")
    if tronque:
        lignes.append("… (liste tronquée à 200 éléments)")
    return "\n".join(lignes)

def format_result_json(meta: Dict[str, Any]) -> str:
    """Sérialise le dictionnaire *meta* en JSON compact."""
    return json.dumps(meta, ensure_ascii=False, separators=(",", ":"))

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tracer l’exécution d’un script Python et restituer le chemin d’appel.",
        epilog="Exemple : python tracer_execution.py mon_script.py --json",
    )
    parser.add_argument(
        "cible",
        type=Path,
        help="Chemin du script Python à analyser.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Répertoire à préfixer dans sys.path (défaut : répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre le résultat sous forme d’un unique objet JSON sur stdout.",
    )
    args = parser.parse_args()

    if not args.cible.exists():
        if args.json:
            print(json.dumps({
                "denominateur": 0,
                "erreur": f"Le fichier cible « {args.cible} » n’existe pas.",
                "contrat": {
                    "QUESTION": "Quel est le chemin d’exécution de cette requête ?",
                    "MESURE": "Enregistrement séquentiel des appels de fonctions Python.",
                    "HYPOTHESES": "Le script s’exécute sans code natif non‑installé.",
                    "LIMITES": "Les appels dans du code C ne sont pas visibles.",
                    "CONTRE-EXEMPLES": "Un script ne contenant que du code C ne produit aucune trace.",
                    "DOMAINE": "Débogage et audit de scripts Python purs.",
                }
            }, ensure_ascii=False, separators=(",", ":")))
        else:
            print(f"Erreur : le fichier cible « {args.cible} » n’existe pas.", file=sys.stderr)
        return 1

    if not args.cible.is_file():
        if args.json:
            print(json.dumps({
                "denominateur": 0,
                "erreur": f"Le chemin « {args.cible} » n’est pas un fichier.",
                "contrat": {
                    "QUESTION": "Quel est le chemin d’exécution de cette requête ?",
                    "MESURE": "Enregistrement séquentiel des appels de fonctions Python.",
                    "HYPOTHESES": "Le script s’exécute sans code natif non‑installé.",
                    "LIMITES": "Les appels dans du code C ne sont pas visibles.",
                    "CONTRE-EXEMPLES": "Un script ne contenant que du code C ne produit aucune trace.",
                    "DOMAINE": "Débogage et audit de scripts Python purs.",
                }
            }, ensure_ascii=False, separators=(",", ":")))
        else:
            print(f"Erreur : le chemin « {args.cible} » n’est pas un fichier.", file=sys.stderr)
        return 2

    try:
        denom, examines, tronque, meta = tracer_execution(
            cible=args.cible,
            racine=args.racine,
        )
    except Exception as exc:
        if args.json:
            print(json.dumps({
                "denominateur": 0,
                "erreur": f"Erreur lors du traitement : {exc}",
                "contrat": {
                    "QUESTION": "Quel est le chemin d’exécution de cette requête ?",
                    "MESURE": "Enregistrement séquentiel des appels de fonctions Python.",
                    "HYPOTHESES": "Le script s’exécute sans code natif non‑installé.",
                    "LIMITES": "Les appels dans du code C ne sont pas visibles.",
                    "CONTRE-EXEMPLES": "Un script ne contenant que du code C ne produit aucune trace.",
                    "DOMAINE": "Débogage et audit de scripts Python purs.",
                }
            }, ensure_ascii=False, separators=(",", ":")))
        else:
            print(f"Erreur lors du traitement : {exc}", file=sys.stderr)
        return 4

    if denom == 0:
        if args.json:
            print(json.dumps({
                "denominateur": 0,
                "erreur": "Impossible de déterminer le chemin d’exécution (aucun appel détecté).",
                "contrat": meta["contrat"]
            }, ensure_ascii=False, separators=(",", ":")))
        else:
            print("Impossible de déterminer le chemin d’exécution (aucun appel détecté).", file=sys.stderr)
        return 3

    if args.json:
        print(format_result_json(meta))
    else:
        print(format_result_humain(denom, examines, tronque))

    return 0

if __name__ == "__main__":
    raise SystemExit(main())