#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""QUESTION: Quel est le résumé de ce texte ?
MESURE: Retourner un résumé sous forme de chaîne de caractères.
HYPOTHESES: Un modèle de résumé peut être disponible via la bibliothèque `transformers`. Sinon, un algorithme naïf basé sur les premières phrases sera utilisé.
LIMITES: Aucun modèle externe n’est garanti d’être installé. Le résumé naïf ne garantit pas la pertinence sémantique.
CONTRE-EXEMPLES: Texte très long sans ponctuation claire ou texte non‑textuel.
DOMAINE: Traitement de texte en français."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

__all__ = [
    "resumer_texte",
    "analyse_texte",
    "contrat",
]

# ----------------------------------------------------------------------
# Configuration de l'encodage de la console (règle 2)
# ----------------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ----------------------------------------------------------------------
# Contrat (docstring reprise dans la sortie JSON) (règle 9)
# ----------------------------------------------------------------------
contrat = {
    "QUESTION": "Quel est le résumé de ce texte ?",
    "MESURE": "Retourner un résumé sous forme de chaîne de caractères.",
    "HYPOTHESES": (
        "Un modèle de résumé peut être disponible via la bibliothèque `transformers`. "
        "Sinon, un algorithme naïf basé sur les premières phrases sera utilisé."
    ),
    "LIMITES": (
        "Aucun modèle externe n’est garanti d’être installé. Le résumé naïf ne garantit "
        "pas la pertinence sémantique."
    ),
    "CONTRE-EXEMPLES": (
        "Texte très long sans ponctuation claire ou texte non‑textuel."
    ),
    "DOMAINE": "Traitement de texte en français."
}

# ----------------------------------------------------------------------
# Fonction cœur : résumer le texte (règle 8)
# ----------------------------------------------------------------------
def resumer_texte(texte: str, max_phrases: int = 3) -> str:
    """
    Retourne un résumé du texte fourni.

    Si la bibliothèque `transformers` est disponible, on utilise le pipeline
    de summarisation. Sinon, on renvoie les `max_phrases` premières phrases
    du texte (heuristique naïve).
    """
    try:
        from transformers import pipeline  # type: ignore
    except ImportError:
        # Mode dégradé : aucune bibliothèque de résumé disponible.
        pass
    else:
        try:
            summarizer = pipeline("summarization", model="facebook/bart-large-cnn")
            résumé = summarizer(texte, max_length=130, min_length=30, do_sample=False)
            return résumé[0]["summary_text"].strip()
        except Exception:
            # Si le pipeline échoue (ex. modèle absent), on retombe sur le fallback.
            pass

    # Fallback naïf : extraire les premières phrases.
    phrases = re.split(r'(?<=[.!?])\s+', texte.strip())
    return " ".join(phrases[:max_phrases]).strip()

def analyse_texte(texte: str) -> Dict[str, Any]:
    """
    Analyse le texte et renvoie les métadonnées requises pour le JSON.

    - `denominateur` : nombre de phrases réellement examinées.
    - `examines` : liste des phrases (max 200) avec indication de troncature.
    - `examines_tronques` : booléen indiquant si la liste a été tronquée.
    - `resume` : le résumé produit par `resumer_texte`.
    """
    phrases = re.split(r'(?<=[.!?])\s+', texte.strip())
    denominateur = len(phrases)

    limite = 200
    examines = phrases[:limite]
    examines_tronques = denominateur > limite

    resume = resumer_texte(texte)

    return {
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": examines_tronques,
        "resume": resume,
    }

# ----------------------------------------------------------------------
# CLI (règle 7)
# ----------------------------------------------------------------------
def construire_parser() -> argparse.ArgumentParser:
    description = (
        "Outil de résumé de texte. Lit un fichier texte et renvoie son résumé.\n"
        "Exemple d’appel réel :\n"
        "  python resumer_texte.py mon_fichier.txt --json"
    )
    parser = argparse.ArgumentParser(
        prog="resumer_texte.py",
        description=description,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "cible",
        type=Path,
        help="Chemin vers le fichier texte à résumer."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique sur stdout."
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=None,
        help="Chemin racine à préfixer à sys.path (remplace la valeur par défaut)."
    )
    return parser

def main() -> int:
    parser = construire_parser()
    args = parser.parse_args()

    # Gestion du paramètre --racine (règle 6)
    racine = args.racine or Path(__file__).resolve().parent
    if racine.is_dir():
        sys.path.insert(0, str(racine))

    # Lecture du fichier cible
    try:
        if not args.cible.is_file():
            if args.json:
                json.dump({
                    "contrat": contrat,
                    "denominateur": 0,
                    "erreur": f"Le fichier cible « {args.cible} » n’existe pas."
                }, sys.stdout, ensure_ascii=False)
                sys.stdout.write("\n")
                return 1
            else:
                print(f"Erreur de lecture du fichier cible : {args.cible} n’existe pas.", file=sys.stderr)
                return 1
        source = args.cible.read_text(encoding="utf-8")
    except Exception as e:
        if args.json:
            json.dump({
                "contrat": contrat,
                "denominateur": 0,
                "erreur": f"Erreur de lecture du fichier cible : {e}"
            }, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return 1
        else:
            print(f"Erreur de lecture du fichier cible : {e}", file=sys.stderr)
            return 1

    resultat = analyse_texte(source)

    # Gestion du cas où le dénominateur est nul (règle 10)
    if resultat["denominateur"] == 0:
        if args.json:
            json.dump({
                "contrat": contrat,
                "denominateur": 0,
                "erreur": "Aucun élément à examiner – le texte est vide."
            }, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            print("Aucun élément à examiner – le texte est vide.", file=sys.stderr)
        return 3

    # Construction de la sortie
    sortie = {
        "contrat": contrat,
        "denominateur": resultat["denominateur"],
        "examines": resultat["examines"],
        "examines_tronques": resultat["examines_tronques"],
        "resume": resultat["resume"],
    }

    if args.json:
        json.dump(sortie, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print("=== Résumé ===")
        print(resultat["resume"])
        print("\n=== Analyse ===")
        print(f"Nombre d'éléments examinés : {resultat['denominateur']}")
        if resultat["examines_tronques"]:
            print("Liste tronquée à 200 éléments.")
        else:
            print("Liste complète des éléments examinés :")
            for i, phrase in enumerate(resultat["examines"], 1):
                print(f"{i}: {phrase}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())