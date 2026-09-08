"""QUESTION: Quels processus tournent, et comment les superviser ?
MESURE: Liste des processus fournis, état d'exécution, tentative de redémarrage si absent.
HYPOTHESES: Les processus sont identifiés par leur nom d'exécutable.
LIMITES: Redémarrage limité à la commande fournie via --cmd, sinon aucun redémarrage.
CONTRE-EXEMPLES: Processus système protégés ne peuvent être redémarrés.
DOMAINE: Supervision légère de processus sur systèmes Unix/Windows."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

# Réglage de l'encodage de la console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Import optionnel de tierces bibliothèques
try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except ImportError:  # pragma: no cover
    _HAS_PSUTIL = False

__all__ = [
    "verifier_processus",
    "demarrer_processus",
    "generer_sortie",
    "analyser_arguments",
]

def verifier_processus(noms: List[str]) -> List[Dict[str, Any]]:
    """Retourne la liste des processus avec leur état d'exécution.

    Chaque dictionnaire contient :
        - "nom" : nom du processus recherché
        - "en_cours" : booléen indiquant s'il est présent
    """
    resultats: List[Dict[str, Any]] = []

    if _HAS_PSUTIL:
        processus_en_cours = {p.name() for p in psutil.process_iter(attrs=["name"])}
        for nom in noms:
            resultats.append({"nom": nom, "en_cours": nom in processus_en_cours})
    else:
        # Fallback avec la commande `ps` (Unix) ou `tasklist` (Windows)
        try:
            if os.name == "nt":
                cmd = ["tasklist", "/FO", "CSV", "/NH"]
                output = subprocess.check_output(cmd, text=True, encoding="utf-8")
                lignes = [line.split('","')[0].strip('"') for line in output.splitlines()]
                processus_en_cours = set(lignes)
            else:
                cmd = ["ps", "-e", "-o", "comm="]
                output = subprocess.check_output(cmd, text=True, encoding="utf-8")
                processus_en_cours = {line.strip() for line in output.splitlines()}
        except Exception as exc:  # pragma: no cover
            print(f"Erreur lors de la récupération des processus : {exc}", file=sys.stderr)
            processus_en_cours = set()

        for nom in noms:
            resultats.append({"nom": nom, "en_cours": nom in processus_en_cours})

    return resultats

def demarrer_processus(commande: str) -> bool:
    """Tente de démarrer le processus indiqué par `commande`.

    Retourne True si le lancement a réussi, False sinon.
    """
    try:
        subprocess.Popen(commande, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as exc:  # pragma: no cover
        print(f"Impossible de démarrer '{commande}' : {exc}", file=sys.stderr)
        return False

def generer_sortie(
    analyses: List[Dict[str, Any]],
    json_mode: bool,
    contrat: Dict[str, str],
) -> str:
    """Construit la sortie finale selon le mode demandé.

    En mode JSON, renvoie un objet JSON contenant le contrat, le dénominateur,
    la liste des éléments examinés (troncature à 200) et un indicateur de troncature.
    En mode texte, renvoie une représentation lisible.
    """
    denominateur = len(analyses)

    if json_mode:
        examines = analyses[:200]
        sortie = {
            "contrat": contrat,
            "denominateur": denominateur,
            "examines": examines,
            "examines_tronques": len(analyses) > 200,
        }
        return json.dumps(sortie, ensure_ascii=False, indent=2)
    else:
        lignes = [
            f"Processus : {a['nom']} -> {'en cours' if a['en_cours'] else 'absent'}"
            for a in analyses
        ]
        return "\n".join(lignes)

def analyser_arguments(argv: List[str] | None = None) -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(
        prog="surveiller_processus.py",
        description=(
            "Supervise les processus indiqués et, le cas échéant, les redémarre.\n"
            "Exemple d'appel réel :\n"
            "  python surveiller_processus.py nginx mysqld --cmd '/usr/sbin/nginx' --json"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "processus",
        nargs="*",
        help="Noms des processus à surveiller (ex. nginx, python).",
    )
    parser.add_argument(
        "--cmd",
        metavar="COMMANDE",
        help="Commande à exécuter pour redémarrer les processus absents.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produit la sortie au format JSON unique sur stdout.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine à insérer en tête de sys.path (défaut : répertoire du script).",
    )
    return parser.parse_args(argv)

def main(argv: List[str] | None = None) -> int:
    args = analyser_arguments(argv)

    # Gestion du chemin racine
    racine = args.racine.resolve()
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))

    if not args.processus:
        print("Aucun processus fourni à analyser.", file=sys.stderr)
        return 3  # dénominateur nul

    analyses = verifier_processus(args.processus)

    # Tentative de redémarrage si besoin et si une commande est fournie
    if args.cmd:
        for a in analyses:
            if not a["en_cours"]:
                a["redemarre"] = demarrer_processus(args.cmd)
                a["en_cours"] = a["redemarre"]
            else:
                a["redemarre"] = False

    # Construction du contrat à partir du docstring
    contrat: Dict[str, str] = {}
    for ligne in __doc__.splitlines():
        if ":" in ligne:
            cle, valeur = ligne.split(":", 1)
            contrat[cle.strip().lower()] = valeur.strip()

    sortie = generer_sortie(analyses, args.json, contrat)

    # Écriture du résultat
    print(sortie, file=sys.stdout)

    # Code de sortie : 0 si tous les processus sont en cours, sinon 1
    tous_en_cours = all(a["en_cours"] for a in analyses)
    return 0 if tous_en_cours else 1

if __name__ == "__main__":
    raise SystemExit(main())