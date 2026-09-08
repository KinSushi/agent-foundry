#!/usr/bin/env python3
# indexeur_fichiers.py
# -*- coding: utf-8 -*-

"""indexeur_fichiers.py

QUESTION      Où sont stockés ces motifs dans des fichiers binaires ?
MESURE        Recherche d'octets signatures (PDF, PNG, JPEG, GIF, ZIP) dans les fichiers
              binaires situés sous une racine donnée, enregistrement du chemin et de l'offset
              de chaque occurrence trouvée.
HYPOTHÈSES    Les fichiers sont lisibles, les signatures recherchées sont suffisantes pour
              identifier le type de fichier, et aucun fichier n'est modifié pendant l'analyse.
LIMITES       Seules les signatures prédéfinies sont détectées ; aucune analyse de structure
              interne n'est effectuée. L'outil fonctionne en mode dégradé si les bibliothèques
              tierces sont absentes.
CONTRE-EXEMPLES Un fichier PDF dont la signature %PDF- apparaît uniquement à l'intérieur
              d'un flux compressé sera néanmoins signalé comme une occurrence.
INVOCATION
    {outil} --racine {dossier} --json
DOMAINE       Tout système de fichiers où l'utilisateur possède le droit de lecture sur les
              fichiers binaires visés.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Iterator, List, Tuple, Dict, Any

# ----------------------------------------------------------------------
# Configuration du socle : encodage de la console (cp1252 -> utf-8)
# ----------------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ----------------------------------------------------------------------
# API publique
# ----------------------------------------------------------------------
__all__ = [
    "construire_argparse",
    "analyser_racine",
    "lister_fichiers_racine",
    "examiner_fichiers",
    "formatter_resultats_humain",
    "formatter_resultats_json",
]

# ----------------------------------------------------------------------
# Construction du parseur d'arguments
# ----------------------------------------------------------------------
def construire_argparse() -> argparse.ArgumentParser:
    """Construit et retourne l'argument parser avec aide en français."""
    parser = argparse.ArgumentParser(
        description="Indexe les fichiers binaires à la recherche de motifs connus.",
        epilog="Exemple d'appel : python indexeur_fichiers.py --racine ./data --json",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Répertoire ou fichier à examiner. Par défaut, le répertoire du script.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON unique sur stdout (résultat machine‑lisible).",
    )
    return parser

# ----------------------------------------------------------------------
# Détermination de la racine effective
# ----------------------------------------------------------------------
def analyser_racine(args: argparse.Namespace) -> Path:
    """Retourne le chemin de racine à examiner, en appliquant --racine si fourni."""
    racine: Path = getattr(args, 'racine', Path(__file__).resolve().parent)
    if not racine.exists():
        raise NotADirectoryError(f"La racine spécifiée n'existe pas : {racine}")
    if not racine.is_dir() and not racine.is_file():
        raise NotADirectoryError(f"La racine spécifiée n'est ni un fichier ni un répertoire : {racine}")
    return racine

# ----------------------------------------------------------------------
# Listage des fichiers à examiner (récursif si répertoire)
# ----------------------------------------------------------------------
def lister_fichiers_racine(racine: Path) -> Iterator[Path]:
    """Yield chaque fichier à examiner sous racine (récursif si répertoire)."""
    if racine.is_file():
        yield racine
    else:  # répertoire
        for racine_courante, _, fichiers in os.walk(racine):
            for nom in fichiers:
                yield Path(racine_courante) / nom

# ----------------------------------------------------------------------
# Cœur de l'outil : recherche des motifs dans les fichiers
# ----------------------------------------------------------------------
def examiner_fichiers(
    chemins: Iterable[Path],
    motifs: Dict[str, bytes],
) -> Tuple[int, List[str], bool, List[Dict[str, Any]]]:
    """
    Examine chaque fichier fourni et retourne :
        - denominateur : nombre de fichiers effectivement examinés
        - examines : liste des chemins examinés (tronquée à 200)
        - examines_tronques : True si la liste a été tronquée
        - resultats : liste de dicts {fichier, motif, offset}
    Aucune impression ni lecture de sys.argv n'est effectuée ici.
    """
    examines: List[str] = []
    resultats: List[Dict[str, Any]] = []
    denominateur = 0

    for chemin in chemins:
        try:
            data = chemin.read_bytes()
        except OSError as e:
            # Diagnostic sur stderr, mais on continue avec les autres fichiers
            print(f"Impossible de lire le fichier {chemin} : {e}", file=sys.stderr)
            continue

        denominateur += 1
        # On garde la trace des fichiers examinés (limite 200)
        if len(examines) < 200:
            examines.append(str(chemin))

        # Recherche de chaque motif
        for nom_motif, pattern in motifs.items():
            start = 0
            while True:
                idx = data.find(pattern, start)
                if idx == -1:
                    break
                resultats.append(
                    {"fichier": str(chemin), "motif": nom_motif, "offset": idx}
                )
                start = idx + 1  # permettre des occurrences chevauchantes

    examines_tronques = len(examines) >= 200 and denominateur > 200
    return denominateur, examines, examines_tronques, resultats

# ----------------------------------------------------------------------
# Formatage lisible par l'humain
# ----------------------------------------------------------------------
def formatter_resultats_humain(resultats: List[Dict[str, Any]]) -> str:
    """Retourne une chaîne lisible par l'humain contenant les résultats."""
    if not resultats:
        return ""
    lignes = []
    for r in resultats:
        lignes.append(f"{r['fichier']}:{r['offset']} ({r['motif']})")
    return "\n".join(lignes)

# ----------------------------------------------------------------------
# Formatage JSON (objet unique sur stdout)
# ----------------------------------------------------------------------
def formatter_resultats_json(
    denominateur: int,
    examines: List[str],
    examines_tronques: bool,
    resultats: List[Dict[str, Any]],
) -> str:
    """Retourne un JSON contenant le contrat et les résultats."""
    # Extraction du contrat depuis le docstring du module
    lignes = __doc__.strip().splitlines()
    contrat: Dict[str, str] = {}
    current_key: str | None = None
    buffer: List[str] = []
    for ligne in lignes:
        if ligne.startswith("QUESTION"):
            if current_key is not None:
                contrat[current_key] = "\n".join(buffer).strip()
            current_key = "QUESTION"
            buffer = [ligne.split(None, 1)[1]] if len(ligne.split()) > 1 else [""]
        elif ligne.startswith("MESURE"):
            if current_key is not None:
                contrat[current_key] = "\n".join(buffer).strip()
            current_key = "MESURE"
            buffer = [ligne.split(None, 1)[1]] if len(ligne.split()) > 1 else [""]
        elif ligne.startswith("HYPOTHÈSES"):
            if current_key is not None:
                contrat[current_key] = "\n".join(buffer).strip()
            current_key = "HYPOTHÈSES"
            buffer = [ligne.split(None, 1)[1]] if len(ligne.split()) > 1 else [""]
        elif ligne.startswith("LIMITES"):
            if current_key is not None:
                contrat[current_key] = "\n".join(buffer).strip()
            current_key = "LIMITES"
            buffer = [ligne.split(None, 1)[1]] if len(ligne.split()) > 1 else [""]
        elif ligne.startswith("CONTRE-EXEMPLES"):
            if current_key is not None:
                contrat[current_key] = "\n".join(buffer).strip()
            current_key = "CONTRE-EXEMPLES"
            buffer = [ligne.split(None, 1)[1]] if len(ligne.split()) > 1 else [""]
        elif ligne.startswith("DOMAINE"):
            if current_key is not None:
                contrat[current_key] = "\n".join(buffer).strip()
            current_key = "DOMAINE"
            buffer = [ligne.split(None, 1)[1]] if len(ligne.split()) > 1 else [""]
        else:
            if current_key is not None:
                buffer.append(ligne)
    if current_key is not None:
        contrat[current_key] = "\n".join(buffer).strip()

    payload: Dict[str, Any] = {
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": examines_tronques,
        "resultats": resultats,
        "contrat": contrat,
    }
    return json.dumps(payload, ensure_ascii=False, indent=None)

# ----------------------------------------------------------------------
# Point d'entrée principal
# ----------------------------------------------------------------------
def main() -> int:
    parser = construire_argparse()
    try:
        args = parser.parse_args()
    except Exception as e:  # pragma: no cover - argparse gère déjà les erreurs
        print(f"Erreur d'analyse des arguments : {e}", file=sys.stderr)
        return 2

    try:
        racine = analyser_racine(args)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2

    # Motifs à rechercher (signatures octet fréquentes)
    motifs: Dict[str, bytes] = {
        "PDF": b"%PDF-",
        "PNG": b"\x89PNG\r\n\x1a\n",
        "JPEG": b"\xff\xd8\xff",
        "GIF87a": b"GIF87a",
        "GIF89a": b"GIF89a",
        "ZIP": b"PK\x03\x04",
    }

    # Collecte des fichiers à examiner
    fichiers = list(lister_fichiers_racine(racine))
    denominateur, examines, examines_tronques, resultats = examiner_fichiers(fichiers, motifs)

    if denominateur == 0:
        error_msg = "denominateur: Aucun fichier examiné."
        print(error_msg, file=sys.stderr)
        if args.json:
            # Toujours produire du JSON exploitable même quand aucun fichier n'est examiné
            sortie = formatter_resultats_json(denominateur, examines, examines_tronques, resultats)
            print(sortie)
        return 3

    # Sortie selon le mode demandé
    if args.json:
        sortie = formatter_resultats_json(denominateur, examines, examines_tronques, resultats)
        print(sortie)
    else:
        sortie = formatter_resultats_humain(resultats)
        if sortie:
            print(sortie)

    # Code de sortie : 0 = rien à signaler, non‑null = défaut trouvé
    if resultats:
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())