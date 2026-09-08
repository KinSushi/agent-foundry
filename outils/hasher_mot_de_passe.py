"""QUESTION
Puis-je stocker ce secret de manière sécurisée ?

MESURE
L'outil examine le fichier indiqué, vérifie la présence d'un hachage
issu de PBKDF2‑HMAC avec sel, nombre d'itérations et longueur du sel.

HYPOTHESES
- Le fichier contient soit du texte brut (secret en clair),
  soit la chaîne «sel:itérations:hachage» en hexadécimal.
- hashlib.pbkdf2_hmac est disponible (OpenSSL présent).

LIMITES
- Aucun algorithme de dérivation de mot de passe moderne (argon2, bcrypt)
  n'est utilisé, uniquement PBKDF2 fourni par la bibliothèque standard.
- Le format du fichier doit correspondre exactement au schéma attendu.
- La sécurité dépend de la robustesse du sel et du nombre d'itérations.

CONTRE-EXEMPLES
Un fichier contenant «password» en clair ou un hachage sans sel/itérations
sera jugé non sécurisé.

DOMAINE
Cette analyse s'applique aux secrets stockés sous forme de fichier texte
sur le système de fichiers local.
"""

from __future__ import annotations

import argparse
import json
import sys
import os
from pathlib import Path
from typing import List, Tuple, Dict, Any

# Encodage UTF‑8 même sur console cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = ["analyser_stockage"]


def _pbkdf2_disponible() -> bool:
    """Retourne True si hashlib.pbkdf2_hmac est présent."""
    return hasattr(__import__("hashlib"), "pbkdf2_hmac")


def _extraire_elements(contenu: bytes) -> Dict[str, Any]:
    """
    Analyse le contenu du fichier.

    Retourne un dictionnaire contenant :
        - "est_clair" (bool) : le texte semble être du texte brut.
        - "sel" (bytes|None)
        - "iterations" (int|None)
        - "hash" (bytes|None)
    Le format attendu est «sel_hex:iterations:hash_hex».
    """
    texte = contenu.decode(errors="ignore").strip()
    result = {"est_clair": False, "sel": None, "iterations": None, "hash": None}

    # Si le fichier ne contient qu'un seul mot sans deux deux‑points → clair
    if ":" not in texte:
        result["est_clair"] = True
        return result

    parts = texte.split(":")
    if len(parts) != 3:
        return result

    sel_hex, it_str, hash_hex = parts
    try:
        sel = bytes.fromhex(sel_hex)
        it = int(it_str)
        h = bytes.fromhex(hash_hex)
    except (ValueError, TypeError):
        return result

    result.update({"sel": sel, "iterations": it, "hash": h})
    return result


def analyser_stockage(chemin: Path) -> Tuple[bool, List[str], int]:
    """
    Analyse le fichier «chemin» et indique s'il est stocké de façon sécurisée.

    Retourne (secure, examines, denom) où :
        - secure (bool) : True si les critères de sécurité sont remplis.
        - examines (list[str]) : noms des éléments effectivement examinés.
        - denom (int) : nombre d'éléments réellement analysés (0 si lecture impossible).
    """
    examines: List[str] = []
    denom = 0

    if not chemin.is_file():
        sys.stderr.write(f"Erreur : le chemin {chemin} n’est pas un fichier lisible.\n")
        return False, examines, denom

    try:
        contenu = chemin.read_bytes()
    except OSError as e:
        sys.stderr.write(f"Erreur de lecture du fichier {chemin} : {e}\n")
        return False, examines, denom

    denom += 1
    elems = _extraire_elements(contenu)

    # 1. Vérification du format clair
    if elems["est_clair"]:
        examines.append("format_clair")
        return False, examines, denom

    # 2. Présence du sel
    if elems["sel"] is None:
        examines.append("sel_absent")
        return False, examines, denom
    examines.append("sel_present")
    if len(elems["sel"]) < 16:
        examines.append("sel_trop_court")
        return False, examines, denom
    examines.append("sel_longueur_ok")

    # 3. Nombre d'itérations
    if elems["iterations"] is None:
        examines.append("iterations_absent")
        return False, examines, denom
    examines.append("iterations_present")
    if elems["iterations"] < 100_000:
        examines.append("iterations_insuffisantes")
        return False, examines, denom
    examines.append("iterations_suffisantes")

    # 4. Disponibilité de PBKDF2
    if not _pbkdf2_disponible():
        examines.append("pbkdf2_indisponible")
        sys.stderr.write("Avertissement : hashlib.pbkdf2_hmac indisponible, aucune garantie de sécurité.\n")
        return False, examines, denom
    examines.append("pbkdf2_disponible")

    # 5. Présence du hash
    if elems["hash"] is None:
        examines.append("hash_absent")
        return False, examines, denom
    examines.append("hash_present")

    # Tous les critères remplis → sécurisé
    return True, examines, denom


def _contrat() -> Dict[str, str]:
    """Construit le contrat à insérer dans la sortie JSON."""
    sections = ["QUESTION", "MESURE", "HYPOTHESES", "LIMITES", "CONTRE-EXEMPLES", "DOMAINE"]
    doc = __doc__ or ""
    contract = {}
    for sec in sections:
        start = doc.find(sec)
        if start == -1:
            contract[sec] = ""
            continue
        end = doc.find("\n\n", start)
        block = doc[start + len(sec) :].strip()
        if end != -1:
            block = doc[start + len(sec) :end].strip()
        contract[sec] = block
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vérifie si un secret stocké dans un fichier est sécurisé.",
        epilog="Exemple : python hasher_mot_de_passe.py secret.txt --json",
    )
    parser.add_argument("cible", type=Path, help="Chemin du fichier contenant le secret.")
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Répertoire racine à préfixer dans sys.path (défaut : répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produit une sortie unique au format JSON sur stdout.",
    )
    args = parser.parse_args()

    # Gestion du chemin racine
    racine = args.racine.resolve()
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))

    secure, examines, denom = analyser_stockage(args.cible)

    # Construction du résultat JSON
    json_obj: Dict[str, Any] = {
        "contrat": _contrat(),
        "denominateur": denom,
        "examines": examines[:200],
        "examines_tronques": len(examines) > 200,
    }

    if args.json:
        json.dump(json_obj, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        # Sortie humaine
        if denom == 0:
            sys.stderr.write("Impossible d’analyser le fichier fourni.\n")
        else:
            if secure:
                print("Le secret est stocké de façon sécurisée.")
            else:
                print("Le secret n’est pas stocké de façon sécurisée.")
            # Diagnostics supplémentaires sur stderr
            if examines:
                sys.stderr.write(f"Éléments examinés : {', '.join(examines)}\n")

    # Code de sortie : 0 si sécurisé, 1 sinon, 3 si aucune analyse possible
    if denom == 0:
        return 3
    return 0 if secure else 1


if __name__ == "__main__":
    raise SystemExit(main())