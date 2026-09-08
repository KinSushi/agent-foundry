"""QUESTION
Comment compresser/décompresser sans zlib ?

MESURE
Utilisation des modules standard bz2 et lzma, et du module optionnel pybase64
pour l'encodage base64 optimisé.

HYPOTHESES
Les algorithmes bz2 et lzma sont disponibles dans l'interpréteur Python 3.14.
pybase64, s'il est installé, fournit des fonctions d'encodage/décodage base64
sans dépendance binaire.

LIMITES
Pas de support zstd, pas de compression personnalisée au‑delà de bz2/lzma.
Si pybase64 est absent, l'option --base64 est ignorée.

CONTRE-EXEMPLES
Un fichier inexistant ou non lisible entraîne une erreur claire et un code
de sortie non nul, jamais d'exception non interceptée.

INVOCATION
    {outil} compress {fichier} {dossier}/resultat --json

DOMAINE
Compression rapide de petits fichiers ou flux en mémoire, avec option
d'encodage base64 pour transport texte.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

# 2. Encodage en tête
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 1. Bibliothèque standard seule, pybase64 optionnel
try:
    import pybase64  # type: ignore
    _has_pybase64 = True
except ImportError:
    _has_pybase64 = False

import bz2
import lzma

__all__ = [
    "compresser",
    "decompresser",
    "encoder_base64",
    "decoder_base64",
    "main",
]

# ----------------------------------------------------------------------
# COEUR : fonctions sans dépendance argparse / print
# ----------------------------------------------------------------------

def compresser(donnees: bytes, algo: str) -> bytes:
    """Compresse *donnees* avec l'algorithme indiqué."""
    if algo == "bz2":
        return bz2.compress(donnees)
    if algo == "lzma":
        return lzma.compress(donnees)
    raise ValueError(f"Algorithme inconnu : {algo!r}")

def decompresser(donnees: bytes, algo: str) -> bytes:
    """Décompresse *donnees* avec l'algorithme indiqué."""
    if algo == "bz2":
        return bz2.decompress(donnees)
    if algo == "lzma":
        return lzma.decompress(donnees)
    raise ValueError(f"Algorithme inconnu : {algo!r}")

def encoder_base64(donnees: bytes) -> bytes:
    """Encode *donnees* en base64 via pybase64 si disponible."""
    if not _has_pybase64:
        raise RuntimeError("pybase64 non disponible")
    return pybase64.standard_b64encode(donnees)

def decoder_base64(donnees: bytes) -> bytes:
    """Décode *donnees* en base64 via pybase64 si disponible."""
    if not _has_pybase64:
        raise RuntimeError("pybase64 non disponible")
    return pybase64.standard_b64decode(donnees)

def _lire_fichier(chemin: Path) -> bytes:
    try:
        return chemin.read_bytes()
    except Exception as exc:
        raise RuntimeError(f"Impossible de lire le fichier {chemin!s} : {exc}") from exc

def _ecrire_fichier(chemin: Path, donnees: bytes) -> None:
    try:
        chemin.write_bytes(donnees)
    except Exception as exc:
        raise RuntimeError(f"Impossible d'écrire le fichier {chemin!s} : {exc}") from exc

# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _construire_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compresseur_rapide.py",
        description="Compresse ou décompresse un fichier avec bz2 ou lzma, "
        "optionnellement en base64.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=None,
        help="Chemin racine à préfixer à sys.path (par défaut le répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produit une sortie JSON unique sur stdout.",
    )
    sub = parser.add_subparsers(dest="commande", required=True)

    # compress
    comp = sub.add_parser("compress", help="Compresser un fichier.")
    comp.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Produit une sortie JSON unique sur stdout.",
    )
    comp.add_argument("source", type=Path, help="Fichier source à compresser.")
    comp.add_argument("cible", type=Path, help="Fichier cible où écrire le résultat.")
    comp.add_argument(
        "--algo",
        choices=["bz2", "lzma"],
        default="bz2",
        help="Algorithme de compression (bz2 par défaut).",
    )
    comp.add_argument(
        "--base64",
        action="store_true",
        help="Encodage du résultat en base64 (requiert pybase64).",
    )

    # decompress
    decomp = sub.add_parser("decompress", help="Décompresser un fichier.")
    decomp.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Produit une sortie JSON unique sur stdout.",
    )
    decomp.add_argument("source", type=Path, help="Fichier source à décompresser.")
    decomp.add_argument("cible", type=Path, help="Fichier cible où écrire le résultat.")
    decomp.add_argument(
        "--algo",
        choices=["bz2", "lzma"],
        default="bz2",
        help="Algorithme de décompression (bz2 par défaut).",
    )
    decomp.add_argument(
        "--base64",
        action="store_true",
        help="Décodage base64 du fichier source avant décompression (requiert pybase64).",
    )
    return parser

def _preparer_racine(racine: Path | None) -> Path:
    racine_finale = racine if racine is not None else Path(__file__).resolve().parent
    if str(racine_finale) not in sys.path:
        sys.path.insert(0, str(racine_finale))
    return racine_finale

def _generer_contrat() -> dict:
    return {
        "QUESTION": "Comment compresser/décompresser sans zlib ?",
        "MESURE": "Utilisation des modules standard bz2 et lzma, et du module optionnel pybase64 pour l'encodage base64 optimisé.",
        "HYPOTHESES": "Les algorithmes bz2 et lzma sont disponibles dans l'interpréteur Python 3.14. pybase64, s'il est installé, fournit des fonctions d'encodage/décodage base64 sans dépendance binaire.",
        "LIMITES": "Pas de support zstd, pas de compression personnalisée au‑delà de bz2/lzma. Si pybase64 est absent, l'option --base64 est ignorée.",
        "CONTRE-EXEMPLES": "Un fichier inexistant ou non lisible entraîne une erreur claire et un code de sortie non nul, jamais d'exception non interceptée.",
        "DOMAINE": "Compression rapide de petits fichiers ou flux en mémoire, avec option d'encodage base64 pour transport texte.",
    }

def _preparer_sortie_json(
    denominateur: int,
    examines: List[str],
    contrat: dict,
    message: str | None = None,
    code: int = 0,
) -> str:
    payload = {
        "denominateur": denominateur,
        "examines": examines[:200],
        "examines_tronques": len(examines) > 200,
        "contrat": contrat,
    }
    if message is not None:
        payload["message"] = message
    if code != 0:
        payload["code"] = code
    return json.dumps(payload, ensure_ascii=False)

def main() -> int:
    parser = _construire_parser()
    args = parser.parse_args()

    # 6. Racine
    _preparer_racine(args.racine)

    contrat = _generer_contrat()
    examines: List[str] = []
    denominateur = 0

    try:
        source_path: Path = args.source
        cible_path: Path = args.cible

        if not source_path.is_file():
            raise RuntimeError(f"Le fichier source n'existe pas ou n'est pas lisible : {source_path}")

        examines.append(str(source_path))
        denominateur = 1

        donnees = _lire_fichier(source_path)

        # Décodage base64 avant décompression si demandé
        if args.commande == "decompress" and args.base64:
            if not _has_pybase64:
                raise RuntimeError("Option --base64 demandée mais pybase64 n'est pas installé.")
            donnees = decoder_base64(donnees)

        # Action principale
        if args.commande == "compress":
            resultat = compresser(donnees, args.algo)
            if args.base64:
                if not _has_pybase64:
                    raise RuntimeError("Option --base64 demandée mais pybase64 n'est pas installé.")
                resultat = encoder_base64(resultat)
        else:  # decompress
            resultat = decompresser(donnees, args.algo)

        _ecrire_fichier(cible_path, resultat)

        # Sortie humaine
        if not getattr(args, "json", False):
            action = "Compression" if args.commande == "compress" else "Décompression"
            sys.stdout.write(f"{action} réussie : {cible_path}\n")
            return 0

        # Sortie JSON
        json_out = _preparer_sortie_json(
            denominateur=denominateur,
            examines=examines,
            contrat=contrat,
        )
        sys.stdout.write(json_out + "\n")
        return 0

    except Exception as exc:
        # Refus légitime (denominateur = 0)
        if denominateur == 0:
            sys.stderr.write(f"Denominateur nul : {str(exc)}, refus de conclure.\n")
            if getattr(args, "json", False):
                json_out = _preparer_sortie_json(
                    denominateur=0,
                    examines=examines,
                    contrat=contrat,
                    message=str(exc),
                    code=3,
                )
                sys.stdout.write(json_out + "\n")
            return 3

        # Erreur normale
        sys.stderr.write(f"Erreur : {exc}\n")
        if getattr(args, "json", False):
            json_out = _preparer_sortie_json(
                denominateur=denominateur,
                examines=examines,
                contrat=contrat,
                message=str(exc),
                code=1,
            )
            sys.stdout.write(json_out + "\n")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())