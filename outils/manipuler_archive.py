"""QUESTION
Que contient cette archive, et comment l’extraire ?

MESURE
Liste les membres de l’archive (fichiers et répertoires) et indique le
nombre d’éléments réellement examinés.  En mode « --json », rend un objet
JSON contenant le contrat et le résultat de l’examen.

HYPOTHÈSES
* L’archive est au format tar (y compris tar.gz, tar.bz2, tar.xz, tar.zst)
  ou zip.
* Les modules standard tarfile, zipfile, gzip, bz2, lzma, zlib sont
  disponibles.
* Le filtre d’extraction par défaut de tarfile (``data``) assure une
  extraction sécurisée.

LIMITES
* Les archives compressées avec des algorithmes non supportés par la
  bibliothèque standard (ex. zstd sans le module ``compression``) ne
  seront pas reconnues.
* Les archives vides ne produisent aucun résultat exploitable.

CONTRE-EXEMPLES
Une archive contenant un lien symbolique pointant hors du répertoire de
destination sera rejetée par le filtre ``data`` et ne sera pas
extrait.

INVOCATION
    {outil} {fichier} --json

DOMAINE
Analyse et extraction sécurisée d’archives tar et zip sur tout système
supporté par Python 3.14.
"""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import List, Mapping

__all__ = [
    "analyser_archive",
    "extraire_archive",
    "contrat",
]

# ----------------------------------------------------------------------
# Fonctions utilitaires (aucun état global)
# ----------------------------------------------------------------------


def _reconfigurer_flux() -> None:
    """Force l’encodage UTF‑8 même si la console Windows utilise cp1252."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def _inserer_racine(racine: Path) -> None:
    """Place le répertoire racine en tête de ``sys.path``."""
    racine_str = str(racine)
    if racine_str not in sys.path:
        sys.path.insert(0, racine_str)


def _tronquer_liste(seq: List[str], limite: int = 200) -> Mapping[str, object]:
    """Retourne la liste (éventuellement tronquée) et indique le tronquage."""
    if len(seq) > limite:
        return {"examines": seq[:limite], "examines_tronques": True}
    return {"examines": seq, "examines_tronques": False}


def _extraire_contrat() -> Mapping[str, str]:
    """Construit la partie « contrat » du JSON à partir du docstring."""
    sections = [
        "QUESTION",
        "MESURE",
        "HYPOTHÈSES",
        "LIMITES",
        "CONTRE-EXEMPLES",
        "INVOCATION",
        "DOMAINE",
    ]
    doc = __doc__ or ""
    contrat: dict[str, str] = {}
    for sec in sections:
        start = doc.find(sec)
        if start == -1:
            continue
        end = len(doc)
        for nxt in sections:
            if nxt == sec:
                continue
            nxt_pos = doc.find(nxt, start + len(sec))
            if nxt_pos != -1 and nxt_pos < end:
                end = nxt_pos
        contenu = doc[start + len(sec) :].strip()
        contrat[sec.lower()] = contenu
    return contrat


def _est_binaire(fichier: Path) -> bool:
    """
    Détecte un contenu binaire en lisant le fichier en texte avec
    ``errors='replace'`` et en cherchant le caractère nul.
    Retourne True si le fichier est considéré comme binaire.
    """
    try:
        texte = fichier.read_text(errors="replace")
    except (OSError, UnicodeDecodeError, ValueError):
        return True
    return chr(0) in texte


# ----------------------------------------------------------------------
# Cœur de l’application (sans dépendance à argparse, sans impression)
# ----------------------------------------------------------------------


def analyser_archive(archive: Path) -> Mapping[str, object]:
    """
    Analyse le contenu d’une archive.

    Retourne un dictionnaire contenant :
        - ``type`` : « tar », « zip » ou « file ».
        - ``members`` : liste des chemins (str) des membres.
    """
    if not archive.exists():
        raise FileNotFoundError(f"Le fichier « {archive} » n’existe pas.")
    if not archive.is_file():
        raise ValueError(f"« {archive} » n’est pas un fichier exploitable.")

    if tarfile.is_tarfile(archive):
        typ = "tar"
        with tarfile.open(archive, mode="r:*") as tf:
            members = tf.getnames()
    elif zipfile.is_zipfile(archive):
        typ = "zip"
        with zipfile.ZipFile(archive, mode="r") as zf:
            members = zf.namelist()
    else:
        typ = "file"
        members = [str(archive)]

    return {"type": typ, "members": members}


def extraire_archive(
    archive: Path,
    destination: Path,
    *,
    safe: bool = True,
) -> Mapping[str, str]:
    """
    Extrait l’archive vers *destination*.

    Si *safe* est vrai et que l’archive est de type tar, le filtre ``data``
    de ``tarfile`` est utilisé pour une extraction sécurisée.
    """
    if not destination.exists():
        destination.mkdir(parents=True, exist_ok=True)

    if tarfile.is_tarfile(archive):
        with tarfile.open(archive, mode="r:*") as tf:
            tf.extractall(path=destination, filter="data" if safe else None)
    elif zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive, mode="r") as zf:
            zf.extractall(path=destination)
    else:
        raise ValueError(f"« {archive} » n’est pas une archive supportée.")

    return {"extraction": "réussie", "destination": str(destination)}


# ----------------------------------------------------------------------
# Interface en ligne de commande
# ----------------------------------------------------------------------


def _construire_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyse et extraction sécurisée d’archives tar ou zip.",
        epilog="Exemple : python manipuler_archive.py mon.tar.gz --dest ./out --json",
    )
    parser.add_argument(
        "archive",
        type=Path,
        nargs="?",
        default=None,
        help="Chemin vers l’archive à analyser.",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path.cwd(),
        help="Répertoire de destination pour l’extraction (défaut : répertoire courant).",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine à placer en tête de sys.path.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique sur stdout.",
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Effectuer l’extraction après l’analyse.",
    )
    return parser


def main() -> int:
    _reconfigurer_flux()
    parser = _construire_parser()
    args = parser.parse_args()

    _inserer_racine(args.racine)

    # Base du résultat JSON, toujours présent
    base_sortie: dict[str, object] = {"contrat": _extraire_contrat(), "denominateur": 0}

    # Absence d’argument obligatoire logique
    if args.archive is None:
        msg = "Argument « archive » manquant."
        if args.json:
            base_sortie["error"] = msg
            json.dump(base_sortie, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stderr.write(f"Erreur : {msg}\n")
        return 1

    # Vérifications préliminaires : existence, type fichier, contenu binaire
    try:
        if not args.archive.exists():
            raise FileNotFoundError(f"Le fichier « {args.archive} » n’existe pas.")
        if not args.archive.is_file():
            raise ValueError(f"« {args.archive} » n’est pas un fichier exploitable.")
        if _est_binaire(args.archive):
            raise ValueError("Contenu binaire détecté.")
        resultat = analyser_archive(args.archive)
    except Exception as exc:
        if args.json:
            base_sortie["error"] = str(exc)
            json.dump(base_sortie, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stderr.write(f"Erreur : {exc}\n")
        return 1

    membres: List[str] = resultat["members"]
    denominateur = len(membres)
    base_sortie["denominateur"] = denominateur

    # Cas d’une archive vide → refus légitime
    if denominateur == 0:
        sys.stderr.write(
            "Denominateur nul : rien à examiner, refus de conclure.\n"
        )
        if args.json:
            json.dump(base_sortie, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 3

    # Construction du résultat complet
    sortie: dict[str, object] = {
        **base_sortie,
        "type": resultat["type"],
        **_tronquer_liste(membres),
    }

    if args.extract:
        try:
            extraire_archive(args.archive, args.dest, safe=True)
            sortie["extraction"] = {"status": "ok", "destination": str(args.dest)}
        except Exception as exc:
            sys.stderr.write(f"Erreur d’extraction : {exc}\n")
            return 2

    if args.json:
        json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    else:
        sys.stdout.write(f"Type d’archive : {resultat['type']}\n")
        sys.stdout.write(f"Nombre d’éléments : {denominateur}\n")
        sys.stdout.write("Contenu :\n")
        for nom in membres:
            sys.stdout.write(f"  {nom}\n")
        if args.extract:
            sys.stdout.write(f"Extraction réalisée dans : {args.dest}\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())