"""QUESTION: Quelles entités (noms, dates) sont présentes ?
MESURE: Extraction par expressions régulières de séquences capitalisées et de motifs de dates, validation par datetime.
HYPOTHESES: Le texte est en français ou en anglais. Les noms sont capitalisés. Les dates suivent des formats standards.
LIMITES: Python pur ne fait pas de véritable NER. La détection des noms est basique. pycountry améliore la détection des pays.
CONTRE-EXEMPLES: Paris peut être un nom commun. Avril peut être un prénom.
DOMAINE: Fichiers texte et sources Python.
"""
from __future__ import annotations

import sys
import re
import datetime
import argparse
import json
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import pycountry
    PYCOUNTRY_DISPONIBLE = True
except ImportError:
    pycountry = None
    PYCOUNTRY_DISPONIBLE = False

__all__ = ["extraire_entites", "analyser_fichier"]


def valider_date(chaine: str) -> bool:
    formats = ["%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%Y/%m/%d"]
    for fmt in formats:
        try:
            datetime.datetime.strptime(chaine, fmt)
            return True
        except ValueError:
            continue
    return False


def extraire_entites(texte: str) -> dict[str, list[str]]:
    dates: set[str] = set()
    noms: set[str] = set()
    
    motifs_dates = [
        r'\b\d{4}-\d{2}-\d{2}\b',
        r'\b\d{2}/\d{2}/\d{4}\b',
        r'\b\d{2}/\d{2}/\d{2}\b',
        r'\b\d{4}/\d{2}/\d{2}\b'
    ]
    for motif in motifs_dates:
        for match in re.finditer(motif, texte):
            if valider_date(match.group(0)):
                dates.add(match.group(0))
            
    for match in re.finditer(r'\b[A-Z][a-zà-ÿ]+(?:\s+[A-Z][a-zà-ÿ]+)*\b', texte):
        noms.add(match.group(0))
        
    pays: set[str] = set()
    if PYCOUNTRY_DISPONIBLE:
        for pays_obj in pycountry.countries:
            if pays_obj.name in texte:
                pays.add(pays_obj.name)
                
    return {
        "dates": sorted(list(dates)),
        "noms": sorted(list(noms)),
        "pays": sorted(list(pays))
    }


def analyser_fichier(chemin: Path) -> dict:
    if not chemin.exists():
        raise FileNotFoundError(f"Le fichier {chemin} n'existe pas.")
    if not chemin.is_file():
        raise ValueError(f"{chemin} n'est pas un fichier.")

    with open(chemin, "r", encoding="utf-8", errors="replace") as f:
        contenu = f.read()

    lignes = contenu.splitlines()
    denominateur = len(lignes)
    examines = [{"ligne": i + 1, "contenu": l} for i, l in enumerate(lignes)]
    examines_tronques = False
    if len(examines) > 200:
        examines = examines[:200]
        examines_tronques = True

    est_valide = True
    if chemin.suffix == ".py":
        try:
            compile(contenu, str(chemin), "exec")
        except Exception:
            est_valide = False

    entites = extraire_entites(contenu)

    return {
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": examines_tronques,
        "noms": entites["noms"],
        "dates": entites["dates"],
        "pays": entites["pays"],
        "est_valide": est_valide,
        "pycountry_disponible": PYCOUNTRY_DISPONIBLE
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrait les entités (noms, dates) d'un fichier.",
        epilog="Exemple d'appel réel: python extracteur_entites.py --json mon_fichier.txt",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("cible", type=str, help="Chemin du fichier à analyser")
    parser.add_argument("--json", action="store_true", help="Rend un seul objet JSON sur stdout")
    parser.add_argument("--racine", type=str, help="Surcharge la racine et l'insère en tête de sys.path")

    args = parser.parse_args()

    if args.racine:
        racine = Path(args.racine).resolve()
        sys.path.insert(0, str(racine))
    else:
        racine = Path(__file__).resolve().parent

    cible = Path(args.cible)
    if not cible.is_absolute():
        cible = racine / cible

    try:
        resultat = analyser_fichier(cible)
    except Exception as e:
        print(f"Erreur: {e}", file=sys.stderr)
        return 1

    if resultat["denominateur"] == 0:
        print("Denominateur nul: l'outil refuse de conclure.", file=sys.stderr)
        return 3

    code_sortie = 0
    if resultat["noms"] or resultat["dates"] or resultat["pays"]:
        code_sortie = 1
    if not resultat["est_valide"]:
        code_sortie = 2

    if args.json:
        sortie = {
            "contrat": {
                "QUESTION": "Quelles entités (noms, dates) sont présentes ?",
                "MESURE": "Extraction par expressions régulières de séquences capitalisées et de motifs de dates, validation par datetime.",
                "HYPOTHESES": "Le texte est en français ou en anglais. Les noms sont capitalisés. Les dates suivent des formats standards.",
                "LIMITES": "Python pur ne fait pas de véritable NER. La détection des noms est basique. pycountry améliore la détection des pays.",
                "CONTRE-EXEMPLES": "Paris peut être un nom commun. Avril peut être un prénom.",
                "DOMAINE": "Fichiers texte et sources Python."
            },
            "denominateur": resultat["denominateur"],
            "examines": resultat["examines"],
            "examines_tronques": resultat["examines_tronques"],
            "entites": {
                "noms": resultat["noms"],
                "dates": resultat["dates"],
                "pays": resultat["pays"]
            },
            "est_valide": resultat["est_valide"],
            "pycountry_disponible": resultat["pycountry_disponible"]
        }
        if not resultat["pycountry_disponible"]:
            sortie["mode_degrade"] = "pycountry non installé, extraction des pays limitée."
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    else:
        if not resultat["pycountry_disponible"]:
            print("Mode dégradé: pycountry non installé.", file=sys.stderr)
        print(f"Entités trouvées dans {cible}:")
        print(f"Noms: {', '.join(resultat['noms']) if resultat['noms'] else 'Aucun'}")
        print(f"Dates: {', '.join(resultat['dates']) if resultat['dates'] else 'Aucune'}")
        print(f"Pays: {', '.join(resultat['pays']) if resultat['pays'] else 'Aucun'}")
        if not resultat["est_valide"]:
            print("Attention: le fichier Python n'est pas valide.", file=sys.stderr)

    return code_sortie


if __name__ == "__main__":
    raise SystemExit(main())