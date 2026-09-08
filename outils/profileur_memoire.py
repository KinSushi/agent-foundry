#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
profileur_memoire.py

QUESTION
    Quels objets consomment le plus de mémoire ?
MESURE
    Taille approximative en octets de chaque objet suivi par le garbage collector,
    obtenue via sys.getsizeof.
HYPOTHÈSES
    - Les objets suivis par le gc représentent une partie significative de la mémoire
      utilisée par le programme.
    - sys.getsizeof fournit une estimation raisonnable de la consommation mémoire
      d'un objet (incluant son overhead immédiat).
LIMITES
    - Ne compte pas la mémoire référencée indirectement (référents).
    - Ne distingue pas les objets partagés ou les structures internes du gc.
    - L'estimation peut sous‑évaluer la taille réelle pour certains types d'extension.
CONTRE-EXEMPLES
    - Un objet très petit qui référence un grand tableau (ex. bytearray) apparaîtra
      comme peu consommateur alors que la mémoire réelle est dominée par le référent.
DOMAINE
    Analyse de la mémoire vive d'un processus Python CPython 3.14 utilisant uniquement
    la bibliothèque standard.
"""

from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import argparse
import gc
import json
from pathlib import Path
from typing import List, Tuple, Dict, Any

__all__ = (
    "obtenir_objets_suivis",
    "taille_objet",
    "analyser_memoire",
    "main",
)

# Racine portable du script (répertoire contenant ce fichier)
RACINE = Path(__file__).resolve().parent


def obtenir_objets_suivis() -> List[Any]:
    """Renvoie la liste des objets actuellement suivis par le garbage collector."""
    return gc.get_objects()


def taille_objet(obj: Any) -> int:
    """Renvoie la taille en octets de l'objet selon sys.getsizeof."""
    return sys.getsizeof(obj)


def analyser_memoire(objets: List[Any]) -> Dict[str, Any]:
    """
    Analyse la mémoire des objets fournis.

    Retourne un dictionnaire avec :
        - denominateur : nombre d'objets examinés
        - examines : liste des objets les plus volumineux (max 200), chaque élément
          possède les clés « nom », « taille_octets », « type »
        - examines_tronques : True si la liste a été tronquée à 200 éléments
    """
    if not objets:
        return {"denominateur": 0, "examines": [], "examines_tronques": False}

    # Construire une liste de tuples (taille, nom, type, objet)
    sized: List[Tuple[int, str, str, Any]] = []
    for obj in objets:
        try:
            taille = taille_objet(obj)
            nom = f"{type(obj).__name__}@{id(obj)}"
            sized.append((taille, nom, type(obj).__name__, obj))
        except Exception:
            # Ignorer les objets qui provoquent une exception lors de la mesure
            continue

    # Trier par taille décroissante
    sized.sort(key=lambda x: x[0], reverse=True)

    denominateur = len(sized)
    limite = 200
    examines_tronques = denominateur > limite
    top = sized[:limite]

    examines: List[Dict[str, Any]] = []
    for taille, nom, typ, _ in top:
        examines.append(
            {"nom": nom, "taille_octets": taille, "type": typ}
        )

    return {
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": examines_tronques,
    }


def _afficher_humain(resultat: Dict[str, Any]) -> None:
    """Affichage lisible destiné à un humain sur stdout."""
    denom = resultat["denominateur"]
    if denom == 0:
        # Ce cas est traité avant l'appel, mais on garde la garde.
        print("Aucun objet examiné.", file=sys.stderr)
        return

    examines = resultat["examines"]
    tronque = resultat["examines_tronques"]
    print(f"Nombre d'objets examinés : {denom}")
    print(f"Top {len(examines)} objets les plus volumineux :")
    for item in examines:
        print(
            f"- {item['nom']} : {item['taille_octets']} octets (type : {item['type']})"
        )
    if tronque:
        print(
            f"Note : la liste a été tronquée à {len(examines)} éléments "
            f"sur {denom} objets examinés.",
            file=sys.stderr,
        )


def main() -> int:
    """Point d'entrée de l'outil."""
    # Récupération des objets et analyse
    objets = obtenir_objets_suivis()
    resultat = analyser_memoire(objets)

    if resultat["denominateur"] == 0:
        print(
            "Erreur : aucun objet n'a pu être examiné (liste gc.get_objects() vide).",
            file=sys.stderr,
        )
        return 3

    parser = argparse.ArgumentParser(
        description="Profileur mémoire : identifie les objets consommant le plus de mémoire.",
        epilog="Exemple d'appel : python profileur_memoire.py --racine . --json",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Répertoire racine à ajouter en tête de sys.path (override du répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON unique sur stdout (sinon affichage lisible).",
    )
    args = parser.parse_args()

    # Déterminer la racine à utiliser (option --racine override la racine par défaut)
    racine_path = args.racine if args.racine is not None else RACINE

    # Gestion de --racine : validation seulement si l'utilisateur a fourni une valeur
    if args.racine:
        racine = args.racine.resolve()
        if not racine.is_dir():
            print(
                f"Erreur : le répertoire spécifié --racine '{racine}' n'existe pas ou n'est pas un dossier.",
                file=sys.stderr,
            )
            return 2
        # On utilise le chemin validé
        racine_path = racine

    # Insérer en tête de sys.path
    sys.path.insert(0, str(racine_path))

    if args.json:
        # Sortie JSON unique sur stdout
        json.dump(resultat, sys.stdout, ensure_ascii=False)
    else:
        _afficher_humain(resultat)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())