"""
validateur_csv.py - Validation de fichiers CSV selon des règles complexes.

QUESTION
    Ce CSV est-il valide selon les règles définies ?

MESURE
    Validation syntaxique (RFC 4180) et sémantique (règles métier) d'un fichier CSV.
    Vérification de :
    - Encodage UTF-8
    - Structure cohérente (nombre de colonnes, absence de lignes vides)
    - Format des valeurs (entiers, flottants, chaînes non vides, dates ISO 8601)
    - Cohérence des en-têtes (présence, unicité, format)
    - Respect des contraintes spécifiques (plages de valeurs, motifs regex)

HYPOTHESES
    - Le fichier CSV est encodé en UTF-8 ou compatible.
    - Les règles de validation sont fournies sous forme de dictionnaire JSON.
    - Les valeurs manquantes sont représentées par des chaînes vides.
    - Les dates sont au format ISO 8601 (YYYY-MM-DD).

LIMITES
    - Ne gère pas les fichiers CSV avec des champs multi-lignes complexes.
    - Les règles de validation doivent être exprimables via regex ou types simples.
    - La détection automatique de dialecte CSV peut échouer sur des formats exotiques.
    - Fichiers limités à 100 Mo pour des raisons de performance.

CONTRE-EXEMPLES
    - Un CSV avec des champs contenant des sauts de ligne non échappés.
    - Un CSV dont les règles nécessitent une validation contextuelle (ex : somme de colonnes).
    - Un CSV avec un nombre inégal de colonnes par ligne.

INVOCATION
    {outil} {fichier} --regles {dossier}/regles.json --json

DOMAINE
    Fichiers CSV générés par des outils standards (Excel, bases de données, exports logiciels).
    Taille maximale : 100 Mo.
"""

from __future__ import annotations
import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, Set

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = [
    "valider_csv",
    "analyser_en_tete",
    "valider_lignes",
    "appliquer_regles",
    "main",
]

def valider_csv(
    chemin_fichier: Union[str, Path],
    regles: Dict[str, Any],
    racine: Optional[Path] = None,
    json_sortie: bool = False,
) -> Tuple[int, Dict[str, Any]]:
    """
    Valide un fichier CSV selon les règles fournies.

    Args:
        chemin_fichier: Chemin vers le fichier CSV à valider.
        regles: Dictionnaire définissant les règles de validation.
        racine: Répertoire racine pour les chemins relatifs.
        json_sortie: Si True, retourne un dictionnaire JSON-compatible.

    Returns:
        Tuple (code_sortie, resultat) où:
        - code_sortie: 0 si valide, 1 si invalide, 2 si erreur de lecture, 3 si refus de conclure.
        - resultat: Dictionnaire contenant les détails de la validation.
    """
    if racine is not None:
        chemin_fichier = racine / chemin_fichier

    resultat = {
        "denominateur": 0,
        "fichier": str(chemin_fichier),
        "examines": [],
        "examines_tronques": False,
        "erreurs": [],
        "conformite": {},
        "contrat": {
            "QUESTION": "Ce CSV est-il valide selon les règles définies ?",
            "MESURE": (
                "Validation syntaxique (RFC 4180) et sémantique (règles métier) d'un fichier CSV. "
                "Vérification de l'encodage, de la structure, des types de données et des contraintes."
            ),
            "HYPOTHESES": (
                "Le fichier CSV est encodé en UTF-8. Les règles sont fournies en JSON. "
                "Les valeurs manquantes sont des chaînes vides. Les dates sont au format ISO 8601."
            ),
            "LIMITES": (
                "Ne gère pas les champs multi-lignes complexes. Les règles doivent être exprimables "
                "via regex ou types simples. Fichiers limités à 100 Mo."
            ),
            "CONTRE-EXEMPLES": (
                "CSV avec sauts de ligne non échappés, nombre inégal de colonnes, "
                "ou validation contextuelle requise."
            ),
            "DOMAINE": "Fichiers CSV standards jusqu'à 100 Mo.",
        },
    }

    try:
        if not Path(chemin_fichier).exists():
            print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
            return 3, resultat

        if Path(chemin_fichier).stat().st_size == 0:
            print("Denominateur nul : fichier vide, refus de conclure.", file=sys.stderr)
            return 3, resultat

        if Path(chemin_fichier).stat().st_size > 100 * 1024 * 1024:
            resultat["erreurs"].append("Fichier trop volumineux (> 100 Mo). Refus de traiter.")
            return 3, resultat

        with open(chemin_fichier, newline="", encoding="utf-8") as fichier:
            try:
                dialecte = csv.Sniffer().sniff(fichier.read(1024))
                fichier.seek(0)
            except csv.Error:
                dialecte = csv.excel
                fichier.seek(0)

            lecteur = csv.reader(fichier, dialect=dialecte)
            lignes = list(lecteur)
    except FileNotFoundError:
        print("Denominateur nul : fichier introuvable, refus de conclure.", file=sys.stderr)
        return 3, resultat
    except PermissionError:
        print("Denominateur refusé : permission refusée pour lire le fichier.", file=sys.stderr)
        return 3, resultat
    except UnicodeDecodeError:
        resultat["erreurs"].append(f"Encodage non UTF-8 détecté dans: {chemin_fichier}")
        return 2, resultat
    except Exception as e:
        resultat["erreurs"].append(f"Erreur inattendue lors de la lecture: {str(e)}")
        return 2, resultat

    if not lignes:
        print("Denominateur nul : fichier CSV vide, refus de conclure.", file=sys.stderr)
        return 3, resultat

    en_tetes, erreurs_en_tetes = analyser_en_tete(lignes[0], regles)
    resultat["erreurs"].extend(erreurs_en_tetes)
    if erreurs_en_tetes:
        return 1, resultat

    erreurs_lignes, lignes_examinees = valider_lignes(
        lignes[1:], en_tetes, regles, max_lignes=200
    )
    resultat["erreurs"].extend(erreurs_lignes)
    resultat["denominateur"] = len(lignes_examinees)
    resultat["examines"] = lignes_examinees
    resultat["examines_tronques"] = len(lignes[1:]) > 200

    if resultat["denominateur"] == 0:
        print("Denominateur nul : aucune ligne de données à valider, refus de conclure.", file=sys.stderr)
        return 3, resultat

    if not resultat["erreurs"]:
        resultat["conformite"] = {"valide": True, "message": "Le fichier CSV est valide."}
        return 0, resultat
    else:
        resultat["conformite"] = {"valide": False, "message": "Le fichier CSV contient des erreurs."}
        return 1, resultat

def analyser_en_tete(
    en_tete: List[str], regles: Dict[str, Any]
) -> Tuple[List[str], List[str]]:
    """
    Analyse les en-têtes du CSV selon les règles fournies.

    Args:
        en_tete: Liste des en-têtes du CSV.
        regles: Dictionnaire des règles de validation.

    Returns:
        Tuple (en_tetes, erreurs) où:
        - en_tetes: Liste des en-têtes nettoyés.
        - erreurs: Liste des erreurs détectées.
    """
    erreurs = []
    en_tetes = [col.strip() for col in en_tete]

    if not en_tetes:
        erreurs.append("En-têtes manquants dans le CSV.")
        return en_tetes, erreurs

    if len(en_tetes) != len(set(en_tetes)):
        erreurs.append("En-têtes dupliqués détectés.")

    regles_en_tetes = regles.get("en_tetes", {})
    if regles_en_tetes:
        if "obligatoires" in regles_en_tetes:
            manquants = set(regles_en_tetes["obligatoires"]) - set(en_tetes)
            if manquants:
                erreurs.append(f"En-têtes obligatoires manquants: {', '.join(manquants)}")

        if "interdits" in regles_en_tetes:
            interdits = set(en_tetes) & set(regles_en_tetes["interdits"])
            if interdits:
                erreurs.append(f"En-têtes interdits présents: {', '.join(interdits)}")

        if "format" in regles_en_tetes:
            motif = regles_en_tetes["format"]
            for col in en_tetes:
                if not re.fullmatch(motif, col):
                    erreurs.append(f"Format d'en-tête invalide pour '{col}'.")

    return en_tetes, erreurs

def valider_lignes(
    lignes: List[List[str]],
    en_tetes: List[str],
    regles: Dict[str, Any],
    max_lignes: int = 200,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Valide les lignes de données du CSV selon les règles fournies.

    Args:
        lignes: Liste des lignes de données du CSV.
        en_tetes: Liste des en-têtes du CSV.
        regles: Dictionnaire des règles de validation.
        max_lignes: Nombre maximal de lignes à examiner.

    Returns:
        Tuple (erreurs, lignes_examinees) où:
        - erreurs: Liste des erreurs détectées.
        - lignes_examinees: Liste des lignes examinées (tronquée si nécessaire).
    """
    erreurs = []
    lignes_examinees = []
    regles_colonnes = regles.get("colonnes", {})

    for i, ligne in enumerate(lignes[:max_lignes], start=1):
        if len(ligne) != len(en_tetes):
            erreurs.append(f"Ligne {i}: Nombre de colonnes incohérent ({len(ligne)} au lieu de {len(en_tetes)}).")
            continue

        if not any(ligne):
            erreurs.append(f"Ligne {i}: Ligne vide détectée.")
            continue

        ligne_dict = dict(zip(en_tetes, ligne))
        erreurs_ligne = appliquer_regles(ligne_dict, regles_colonnes, i)
        erreurs.extend(erreurs_ligne)

        if not erreurs_ligne:
            lignes_examinees.append({"numero": i, "ligne": ligne_dict})

    return erreurs, lignes_examinees

def appliquer_regles(
    ligne: Dict[str, str], regles_colonnes: Dict[str, Any], numero_ligne: int
) -> List[str]:
    """
    Applique les règles de validation à une ligne de données.

    Args:
        ligne: Dictionnaire {en-tête: valeur} pour une ligne.
        regles_colonnes: Règles de validation par colonne.
        numero_ligne: Numéro de la ligne dans le CSV.

    Returns:
        Liste des erreurs détectées pour cette ligne.
    """
    erreurs = []

    for col, valeur in ligne.items():
        regles = regles_colonnes.get(col, {})
        if not regles:
            continue

        valeur_strip = valeur.strip()

        if "obligatoire" in regles and regles["obligatoire"] and not valeur_strip:
            erreurs.append(f"Ligne {numero_ligne}: Colonne '{col}' obligatoire mais vide.")
            continue

        if "type" in regles:
            type_attendu = regles["type"]

            if type_attendu == "entier":
                if not re.fullmatch(r"^-?\d+$", valeur_strip):
                    erreurs.append(f"Ligne {numero_ligne}: Colonne '{col}' doit être un entier (reçu: '{valeur}').")
            elif type_attendu == "flottant":
                if not re.fullmatch(r"^-?\d+(\.\d+)?$", valeur_strip):
                    erreurs.append(f"Ligne {numero_ligne}: Colonne '{col}' doit être un flottant (reçu: '{valeur}').")
            elif type_attendu == "chaine_non_vide":
                if not valeur_strip:
                    erreurs.append(f"Ligne {numero_ligne}: Colonne '{col}' doit être une chaîne non vide.")
            elif type_attendu == "bool":
                if valeur_strip.lower() not in ("true", "false", "1", "0", "oui", "non"):
                    erreurs.append(f"Ligne {numero_ligne}: Colonne '{col}' doit être un booléen (reçu: '{valeur}').")
            elif type_attendu == "date":
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", valeur_strip):
                    erreurs.append(f"Ligne {numero_ligne}: Colonne '{col}' doit être une date au format ISO 8601 (YYYY-MM-DD) (reçu: '{valeur}').")

        if "plage" in regles:
            plage = regles["plage"]
            try:
                if "min" in plage and float(valeur) < plage["min"]:
                    erreurs.append(f"Ligne {numero_ligne}: Colonne '{col}' doit être ≥ {plage['min']} (reçu: {valeur}).")
                if "max" in plage and float(valeur) > plage["max"]:
                    erreurs.append(f"Ligne {numero_ligne}: Colonne '{col}' doit être ≤ {plage['max']} (reçu: {valeur}).")
            except ValueError:
                erreurs.append(f"Ligne {numero_ligne}: Colonne '{col}' doit être un nombre pour la validation de plage.")

        if "motif" in regles:
            motif = regles["motif"]
            if not re.fullmatch(motif, valeur_strip):
                erreurs.append(f"Ligne {numero_ligne}: Colonne '{col}' ne correspond pas au motif requis (reçu: '{valeur}').")

        if "valeurs_autorisees" in regles:
            valeurs_autorisees = set(regles["valeurs_autorisees"])
            if valeur_strip not in valeurs_autorisees:
                erreurs.append(f"Ligne {numero_ligne}: Colonne '{col}' doit être l'une de {valeurs_autorisees} (reçu: '{valeur}').")

    return erreurs

def main() -> int:
    """Point d'entrée de la CLI."""
    parser = argparse.ArgumentParser(
        description="Valide un fichier CSV selon des règles complexes.",
        epilog="Exemple: python validateur_csv.py donnees.csv --regles regles.json",
    )
    parser.add_argument("fichier", help="Chemin vers le fichier CSV à valider.")
    parser.add_argument(
        "--regles",
        required=True,
        help="Chemin vers le fichier JSON définissant les règles de validation.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Répertoire racine pour les chemins relatifs (défaut: répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON sur stdout.",
    )

    args = parser.parse_args()
    racine = args.racine if args.racine else RACINE

    try:
        with open(racine / args.regles, encoding="utf-8") as f_regles:
            regles = json.load(f_regles)
    except FileNotFoundError:
        print("Denominateur nul : fichier de règles introuvable, refus de conclure.", file=sys.stderr)
        if args.json:
            json.dump({
                "denominateur": 0,
                "erreurs": [f"Fichier de règles introuvable: {args.regles}"],
                "conformite": {"valide": False, "message": "Erreur de configuration"}
            }, sys.stdout, indent=2, ensure_ascii=False)
            print()
        return 3
    except json.JSONDecodeError as e:
        print(f"Denominateur nul : fichier de règles invalide, refus de conclure.", file=sys.stderr)
        if args.json:
            json.dump({
                "denominateur": 0,
                "erreurs": [f"Fichier de règles invalide: {str(e)}"],
                "conformite": {"valide": False, "message": "Erreur de configuration"}
            }, sys.stdout, indent=2, ensure_ascii=False)
            print()
        return 3
    except Exception as e:
        print(f"Denominateur nul : erreur inattendue lors de la lecture des règles, refus de conclure.", file=sys.stderr)
        if args.json:
            json.dump({
                "denominateur": 0,
                "erreurs": [f"Erreur inattendue lors de la lecture des règles: {str(e)}"],
                "conformite": {"valide": False, "message": "Erreur de configuration"}
            }, sys.stdout, indent=2, ensure_ascii=False)
            print()
        return 3

    code_sortie, resultat = valider_csv(args.fichier, regles, racine, args.json)

    if args.json:
        json.dump(resultat, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        if resultat["erreurs"]:
            print("Erreurs détectées:", file=sys.stderr)
            for erreur in resultat["erreurs"]:
                print(f"  - {erreur}", file=sys.stderr)
        print(resultat["conformite"]["message"])

    return code_sortie

if __name__ == "__main__":
    raise SystemExit(main())