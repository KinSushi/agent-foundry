#!/usr/bin/env python3.14
"""
QUESTION      : Quelles métadonnées ce fichier contient‑il ?
MESURE        : Extraction des métadonnées système, EXIF, PDF et plist avec
                normalisation des dates et des codes pays.
HYPOTHÈSES   : Le fichier existe, est lisible et possède des métadonnées
                standardisées.
LIMITES       : Ne traite pas les métadonnées audio/vidéo ni les formats
                propriétaires non documentés.
CONTRE‑EXEMPLES: PDF sans XMP, image sans EXIF.
INVOCATION    : {outil} {fichier} --json
DOMAINE       : Fichiers locaux accessibles en lecture (images, PDF, plist).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import plistlib
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

# Encodage UTF‑8 pour les consoles Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent
__all__ = ["extraire_metadonnees", "main"]


def _charger_module(nom: str) -> Optional[Any]:
    """Importation conditionnelle d’un module, renvoie None en cas d’échec."""
    try:
        return __import__(nom)
    except ImportError:
        return None


# Modules optionnels – importés uniquement s’ils sont disponibles
cv2 = _charger_module("cv2")
fitz = _charger_module("fitz")          # PyMuPDF
pycountry = _charger_module("pycountry")
isodate = _charger_module("isodate")


def _normaliser_date(date_str: Optional[str]) -> Optional[str]:
    """Normalise une chaîne de date en ISO‑8601."""
    if not date_str:
        return None
    if isodate:
        try:
            dt = isodate.parse_datetime(date_str)
            return dt.isoformat()
        except Exception:
            pass
    try:
        dt = datetime.fromisoformat(date_str)
        return dt.isoformat()
    except Exception:
        return date_str


def _normaliser_pays(code: Optional[str]) -> Optional[str]:
    """Convertit un code pays ISO en nom complet."""
    if not code or not pycountry:
        return code
    try:
        return pycountry.countries.lookup(code).name
    except Exception:
        return code


def _extraire_metadonnees_systeme(chemin: Path) -> Dict[str, Any]:
    """Métadonnées système du fichier."""
    st = chemin.stat()
    return {
        "taille_octets": st.st_size,
        "date_modification": _normaliser_date(datetime.fromtimestamp(st.st_mtime).isoformat()),
        "date_acces": _normaliser_date(datetime.fromtimestamp(st.st_atime).isoformat()),
        "date_creation": _normaliser_date(datetime.fromtimestamp(st.st_ctime).isoformat()),
        "permissions": stat.filemode(st.st_mode),
        "proprietaire": st.st_uid,
        "groupe": st.st_gid,
        "est_fichier": stat.S_ISREG(st.st_mode),
        "est_dossier": stat.S_ISDIR(st.st_mode),
        "est_lien": stat.S_ISLNK(st.st_mode),
    }


def _extraire_type_mime(chemin: Path) -> Dict[str, Optional[str]]:
    """Détermination du type MIME."""
    mime, _ = mimetypes.guess_type(chemin)
    return {"type_mime": mime}


def _extraire_metadonnees_exif(chemin: Path) -> Dict[str, Any]:
    """Extraction EXIF via cv2 lorsqu’il est disponible."""
    if not cv2:
        return {"erreur": "Module cv2 non disponible"}

    try:
        img = cv2.imread(str(chemin))
        if img is None:
            return {"erreur": "Impossible de lire l’image"}

        exif_data = {}
        if hasattr(cv2, "imagecodecs"):
            metadata = cv2.imagecodecs.imread(str(chemin), cv2.IMREAD_METADATA)
            if metadata:
                exif_data = {
                    "date_prise": _normaliser_date(metadata.get("DateTimeOriginal")),
                    "fabricant": metadata.get("Make"),
                    "modele": metadata.get("Model"),
                    "orientation": metadata.get("Orientation"),
                    "resolution": metadata.get("XResolution"),
                    "geolocalisation": {
                        "latitude": metadata.get("GPSLatitude"),
                        "longitude": metadata.get("GPSLongitude"),
                        "altitude": metadata.get("GPSAltitude"),
                        "pays": _normaliser_pays(metadata.get("GPSCountry")),
                    } if metadata.get("GPSLatitude") else None,
                }
        return exif_data
    except Exception as e:
        return {"erreur": f"Erreur EXIF : {e}"}


def _extraire_metadonnees_pdf(chemin: Path) -> Dict[str, Any]:
    """Extraction des métadonnées PDF via PyMuPDF."""
    if not fitz:
        return {"erreur": "Module PyMuPDF non disponible"}

    try:
        doc = fitz.open(chemin)
        md = doc.metadata
        result = {
            "titre": md.get("title"),
            "auteur": md.get("author"),
            "sujet": md.get("subject"),
            "mots_cles": md.get("keywords"),
            "createur": md.get("creator"),
            "producteur": md.get("producer"),
            "date_creation": _normaliser_date(md.get("creationDate")),
            "date_modification": _normaliser_date(md.get("modDate")),
            "nombre_pages": len(doc),
        }
        doc.close()
        return result
    except Exception as e:
        return {"erreur": f"Erreur PDF : {e}"}


def _extraire_metadonnees_plist(chemin: Path) -> Dict[str, Any]:
    """Extraction d’un fichier plist."""
    try:
        with chemin.open("rb") as f:
            return {"plist": plistlib.load(f)}
    except Exception as e:
        return {"erreur": f"Erreur plist : {e}"}


def _verifier_texte(chemin: Path) -> None:
    """
    Vérifie que le fichier est lisible comme texte et qu’il ne contient pas
    de caractère nul. En cas d’erreur, écrit un message clair sur stderr et
    lève une exception appropriée.
    """
    try:
        texte = chemin.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        sys.stderr.write(f"Erreur de lecture du fichier {chemin}: {exc}\n")
        raise

    if chr(0) in texte:
        sys.stderr.write(f"Fichier {chemin} est binaire, refusé.\n")
        raise ValueError(f"Fichier {chemin} illisible ou binaire")


def extraire_metadonnees(chemin: Union[str, Path]) -> Dict[str, Any]:
    """Extrait toutes les métadonnées disponibles pour le fichier indiqué."""
    chemin = Path(chemin).resolve()
    if not chemin.exists():
        raise FileNotFoundError(f"Fichier {chemin} introuvable")
    if not chemin.is_file():
        raise ValueError(f"{chemin} n’est pas un fichier")

    # Détection binaire/illisible – dernier mot, avant toute autre logique
    _verifier_texte(chemin)

    meta = {
        "systeme": _extraire_metadonnees_systeme(chemin),
        "type_mime": _extraire_type_mime(chemin),
    }

    mime = meta["type_mime"]["type_mime"]
    if mime and mime.startswith("image/"):
        meta["exif"] = _extraire_metadonnees_exif(chemin)
    elif mime == "application/pdf":
        meta["pdf"] = _extraire_metadonnees_pdf(chemin)
    elif mime == "application/x-plist":
        meta["plist"] = _extraire_metadonnees_plist(chemin)

    return meta


class _ArgumentParserJSON(argparse.ArgumentParser):
    """ArgumentParser qui renvoie toujours du JSON en cas d’erreur."""

    def error(self, message: str) -> None:  # pragma: no cover
        erreur = {"erreur": message, "denominateur": 0}
        json.dump(erreur, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        self.exit(2)


def analyser_arguments() -> argparse.Namespace:
    """Configuration de l’analyseur d’arguments CLI."""
    parser = _ArgumentParserJSON(
        description="Extrait les métadonnées d’un fichier.",
        epilog="Exemple : %(prog)s --json mon_fichier.pdf",
    )
    parser.add_argument(
        "fichier",
        type=Path,
        help="Chemin vers le fichier à analyser",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Répertoire racine pour les imports relatifs",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON (objet unique)",
    )
    return parser.parse_args()


def _construire_resultat(
    denominateur: int,
    examines: list[str],
    metadonnees: Optional[Dict[str, Any]] = None,
    erreur: Optional[str] = None,
) -> Dict[str, Any]:
    """Construit le dictionnaire de sortie unique."""
    base = {
        "denominateur": denominateur,
        "examines": examines,
        "contrat": {
            "QUESTION": "Quelles métadonnées ce fichier contient‑il ?",
            "MESURE": "Extraction des métadonnées système, EXIF, PDF et plist avec normalisation.",
            "HYPOTHÈSES": "Le fichier existe et contient des métadonnées standardisées.",
            "LIMITES": "Ne traite pas les métadonnées audio/vidéo ou formats propriétaires.",
            "CONTRE‑EXEMPLES": "Fichiers PDF sans XMP, images sans EXIF.",
            "DOMAINE": "Fichiers locaux accessibles en lecture.",
        },
    }
    if erreur is not None:
        base["erreur"] = erreur
    if metadonnees is not None:
        base["metadonnees"] = metadonnees
    return base


def main() -> int:
    """Point d’entrée principal."""
    args = analyser_arguments()
    if args.racine:
        sys.path.insert(0, str(args.racine.resolve()))

    examines: list[str] = []
    denominateur = 0
    metadonnees: Optional[Dict[str, Any]] = None
    erreur: Optional[str] = None

    try:
        metadonnees = extraire_metadonnees(args.fichier)
        examines = [args.fichier.name]
        denominateur = len(examines)
    except (FileNotFoundError, ValueError, OSError) as e:
        erreur = str(e)
        denominateur = 0
    except Exception as e:  # pragma: no cover
        erreur = f"Erreur inattendue : {e}"
        denominateur = 0

    # Cas de refus légitime (défaut d’éléments à examiner)
    if denominateur == 0:
        # Message d’erreur déjà éventuellement écrit par _verifier_texte ;
        # on ajoute le message générique requis par le socle.
        sys.stderr.write("Denominateur nul : rien à examiner, refus de conclure.\n")
        resultat = _construire_resultat(0, [], metadonnees=None, erreur=erreur)
        json.dump(resultat, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 3

    # Construction du résultat normal / avec éventuelle erreur
    resultat = _construire_resultat(denominateur, examines, metadonnees, erreur)

    if args.json:
        json.dump(resultat, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        # Sortie lisible par un humain (hors mode JSON)
        print(json.dumps(metadonnees, indent=2, ensure_ascii=False), file=sys.stdout)

    # Code de sortie : 0 si aucune erreur, 1 sinon
    return 0 if erreur is None else 1


if __name__ == "__main__":
    raise SystemExit(main())