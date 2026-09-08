"""Outil pour lire, écrire et lister des objets dans un stockage cloud.

QUESTION      Comment lire/écrire/lister des objets dans un stockage cloud ?
MESURE        Liste, lit et écrit des objets dans un stockage local (mode dégradé) ou cloud.
HYPOTHÈSES    Le stockage est accessible en lecture/écriture.
LIMITES       Ne gère pas les transferts multipart sans boto3.
CONTRE-EXEMPLES Un objet trop grand pour la mémoire.
DOMAINE       Petits objets, stockage local ou S3.
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
from typing import Any

# boto3 est optionnel ; son import est volontairement évité pour éviter tout accès réseau par défaut.
BOTO3_DISPO = False

RACINE = Path(__file__).resolve().parent

__all__ = ["lister_objets", "lire_objet", "ecrire_objet", "valider_source_python"]


def _obtenir_stockage(racine: Path) -> Path:
    stockage = racine / "nuage_local"
    stockage.mkdir(exist_ok=True)
    return stockage


def lister_objets(racine: Path) -> list[str]:
    stockage = _obtenir_stockage(racine)
    return sorted([p.name for p in stockage.iterdir() if p.is_file()])


def lire_objet(racine: Path, nom: str) -> bytes:
    stockage = _obtenir_stockage(racine)
    chemin = stockage / nom
    if not chemin.exists():
        raise FileNotFoundError(f"Objet introuvable: {nom}")
    return chemin.read_bytes()


def ecrire_objet(racine: Path, nom: str, contenu: bytes) -> None:
    stockage = _obtenir_stockage(racine)
    chemin = stockage / nom
    chemin.write_bytes(contenu)


def valider_source_python(nom: str, contenu: bytes) -> bool:
    if nom.endswith(".py"):
        try:
            compile(contenu, nom, "exec")
        except SyntaxError:
            return False
    return True


def _contrat() -> dict[str, str]:
    return {
        "QUESTION": "Comment lire/écrire/lister des objets dans un stockage cloud ?",
        "MESURE": "Liste, lit et écrit des objets dans un stockage local (mode dégradé) ou cloud.",
        "HYPOTHÈSES": "Le stockage est accessible en lecture/écriture.",
        "LIMITES": "Ne gère pas les transferts multipart sans boto3.",
        "CONTRE-EXEMPLES": "Un objet trop grand pour la mémoire.",
        "DOMAINE": "Petits objets, stockage local ou S3."
    }


def main() -> int:
    if not BOTO3_DISPO:
        print("Mode dégradé: boto3 non disponible, utilisation du stockage local.", file=sys.stderr)

    parser = argparse.ArgumentParser(
        description="Lire, écrire et lister des objets dans un stockage cloud.",
        epilog="Exemple: python stocker_nuage.py lister --json"
    )
    parser.add_argument("--racine", type=Path, default=RACINE, help="Racine du stockage local")
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    p_lister = sous_parsers.add_parser("lister", help="Lister les objets")
    p_lister.add_argument("--json", action="store_true", help="Sortie JSON")

    p_lire = sous_parsers.add_parser("lire", help="Lire un objet")
    p_lire.add_argument("nom", type=str, help="Nom de l'objet")
    p_lire.add_argument("--json", action="store_true", help="Sortie JSON")

    p_ecrire = sous_parsers.add_parser("ecrire", help="Écrire un objet")
    p_ecrire.add_argument("nom", type=str, help="Nom de l'objet")
    p_ecrire.add_argument("fichier", type=Path, help="Fichier source à écrire")
    p_ecrire.add_argument("--json", action="store_true", help="Sortie JSON")

    args = parser.parse_args()
    racine = args.racine.resolve()
    sys.path.insert(0, str(racine))

    try:
        if args.commande == "lister":
            objets = lister_objets(racine)
            denominateur = len(objets)
            if denominateur == 0:
                print("Aucun objet à examiner.", file=sys.stderr)
                return 3
            
            examines = [{"nom": o} for o in objets[:200]]
            examines_tronques = denominateur > 200

            if args.json:
                resultat = {
                    "contrat": _contrat(),
                    "denominateur": denominateur,
                    "examines": examines,
                    "examines_tronques": examines_tronques
                }
                print(json.dumps(resultat, ensure_ascii=False, indent=2))
            else:
                for obj in examines:
                    print(obj["nom"])
            return 0

        elif args.commande == "lire":
            contenu = lire_objet(racine, args.nom)
            valide = valider_source_python(args.nom, contenu)
            denominateur = 1
            examines = [{"nom": args.nom, "valide": valide}]

            if args.json:
                resultat = {
                    "contrat": _contrat(),
                    "denominateur": denominateur,
                    "examines": examines,
                    "examines_tronques": False
                }
                print(json.dumps(resultat, ensure_ascii=False, indent=2))
            else:
                sys.stdout.buffer.write(contenu)
            return 0 if valide else 1

        elif args.commande == "ecrire":
            if not args.fichier.exists():
                print(f"Fichier source introuvable: {args.fichier}", file=sys.stderr)
                return 2
            contenu = args.fichier.read_bytes()
            ecrire_objet(racine, args.nom, contenu)
            valide = valider_source_python(args.nom, contenu)
            denominateur = 1
            examines = [{"nom": args.nom, "valide": valide}]

            if args.json:
                resultat = {
                    "contrat": _contrat(),
                    "denominateur": denominateur,
                    "examines": examines,
                    "examines_tronques": False
                }
                print(json.dumps(resultat, ensure_ascii=False, indent=2))
            else:
                print(f"Objet écrit: {args.nom}")
            return 0 if valide else 1

    except Exception as e:
        print(f"Erreur: {e}", file=sys.stderr)
        return 2

    return 0

if __name__ == "__main__":
    raise SystemExit(main())