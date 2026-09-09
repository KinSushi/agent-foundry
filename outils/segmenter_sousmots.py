"""Segmentation de modèles de tokenisation SentencePiece.

QUESTION      Peut-on exploiter un modèle de tokenisation pré‑entraîné sans connaître son format interne ?
MESURE        Extraction des métadonnées binaires via sentencepiece_model_pb2 et validation du vocabulaire.
HYPOTHÈSES    Le fichier .model est valide et non corrompu ; sentencepiece est installé.
LIMITES       Ne vérifie pas la cohérence des tokens (ex : doublons) ; ignore les modèles non‑SentencePiece.
CONTRE‑EXEMPLE Un fichier .model avec un vocabulaire vide (taille=0) est accepté, mais inutilisable en pratique.
INVOCATION    {outil} exemple {fichier} --json
DOMAINE       Modèles SentencePiece en format .model ou .vocab, versions compatibles avec sentencepiece>=0.1.90.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = [
    "charger_modele",
    "extraire_metadonnees",
    "exemple_examen",
    "main",
]

class ErreurModele(Exception):
    """Erreur liée au modèle SentencePiece."""

class ErreurBibliotheque(Exception):
    """Erreur liée à l'absence de sentencepiece."""


def _verifier_fichier(cible: Path) -> None:
    """Vérifie que le fichier existe, est un fichier et n’est pas vide."""
    if not cible.exists():
        raise FileNotFoundError(f"Fichier introuvable : {cible}")
    if not cible.is_file():
        raise ValueError(f"Chemin non fichier : {cible}")
    if not cible.stat().st_size:
        raise ValueError(f"Fichier vide : {cible}")


def _detecter_format(cible: Path) -> str:
    """Détecte le format du fichier (.model ou .vocab)."""
    suffixe = cible.suffix.lower()
    if suffixe == ".model":
        return "model"
    if suffixe == ".vocab":
        return "vocab"
    raise ValueError("Format non reconnu (attendu : .model ou .vocab)")


def charger_modele(cible: Union[str, Path], racine: Path = RACINE) -> Dict[str, Any]:
    """Charge un modèle SentencePiece et retourne ses métadonnées basiques."""
    chemin = Path(cible)
    if not chemin.is_absolute():
        chemin = racine / chemin

    _verifier_fichier(chemin)
    _detecter_format(chemin)

    try:
        import sentencepiece
    except ImportError as exc:
        raise ErreurBibliotheque("Bibliothèque sentencepiece manquante") from exc

    try:
        sp = sentencepiece.SentencePieceProcessor()
        sp.load(str(chemin))
        taille_vocab = sp.GetPieceSize()
    except Exception as exc:
        raise ErreurModele(f"Modèle invalide : {exc}") from exc

    return {
        "statut": "chargé",
        "taille_vocabulaire": taille_vocab,
        "format": "model",
    }


def extraire_metadonnees(cible: Union[str, Path], racine: Path = RACINE) -> Dict[str, Any]:
    """Extrait les métadonnées détaillées d’un modèle SentencePiece."""
    chemin = Path(cible)
    if not chemin.is_absolute():
        chemin = racine / chemin

    _verifier_fichier(chemin)
    if _detecter_format(chemin) != "model":
        raise ValueError("Format invalide pour métadonnées (attendu : .model)")

    try:
        import sentencepiece
        from google.protobuf import text_format  # noqa: F401
    except ImportError as exc:
        raise ErreurBibliotheque("Bibliothèque sentencepiece ou protobuf manquante") from exc

    try:
        sp = sentencepiece.SentencePieceProcessor()
        sp.load(str(chemin))

        try:
            from sentencepiece import sentencepiece_model_pb2
            model_proto = sentencepiece_model_pb2.ModelProto()
            with open(chemin, "rb") as f:
                model_proto.ParseFromString(f.read())
            regles = {
                "pretok": bool(model_proto.trainer_spec.pretokenization_delimiter),
                "byte_fallback": bool(model_proto.trainer_spec.byte_fallback),
            }
        except Exception:
            regles = {"pretok": False, "byte_fallback": False}

        return {
            "taille_vocabulaire": sp.GetPieceSize(),
            "regles": regles,
            "premier_token": sp.GetPiece(0) if sp.GetPieceSize() > 0 else "",
        }
    except Exception as exc:
        raise ErreurModele(f"Modèle invalide : {exc}") from exc


def exemple_examen(cible: Union[str, Path], racine: Path = RACINE) -> Dict[str, Any]:
    """Exemple d’examen : compte le nombre de lignes du fichier fourni."""
    chemin = Path(cible)
    if not chemin.is_absolute():
        chemin = racine / chemin
    _verifier_fichier(chemin)
    try:
        with chemin.open(encoding="utf-8") as f:
            lignes = f.readlines()
    except Exception as exc:
        raise ValueError(f"Impossible de lire le fichier : {exc}") from exc
    return {"lignes": len(lignes)}


def _creer_analyseur_parent() -> argparse.ArgumentParser:
    """Analyseur partagé entre le parser principal et les sous‑parsers."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Sortie JSON sur stdout (par défaut : sortie lisible)",
    )
    parent.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Répertoire racine pour les chemins relatifs (défaut : répertoire du script)",
    )
    return parent


def _creer_analyseur() -> argparse.ArgumentParser:
    """Construit l’analyseur d’arguments principal."""
    parent = _creer_analyseur_parent()
    analyseur = argparse.ArgumentParser(
        description="Segmentation de modèles de tokenisation SentencePiece.",
        parents=[parent],
    )
    sous = analyseur.add_subparsers(dest="commande", required=True)

    # sous‑commande charger
    cmd_charger = sous.add_parser(
        "charger",
        parents=[parent],
        help="Charge un modèle SentencePiece et affiche sa taille.",
    )
    cmd_charger.add_argument(
        "modele",
        type=str,
        help="Chemin vers le fichier .model ou .vocab",
    )

    # sous‑commande metadonnees
    cmd_meta = sous.add_parser(
        "metadonnees",
        parents=[parent],
        help="Extrait les métadonnées d’un modèle SentencePiece.",
    )
    cmd_meta.add_argument(
        "modele",
        type=str,
        help="Chemin vers le fichier .model",
    )

    # sous‑commande exemple (dégradé, ne dépend d’aucune bibliothèque externe)
    cmd_exemple = sous.add_parser(
        "exemple",
        parents=[parent],
        help="Exemple d’examen : compte les lignes d’un fichier texte.",
    )
    cmd_exemple.add_argument(
        "fichier",
        type=str,
        help="Chemin vers le fichier à examiner",
    )

    return analyseur


def _formater_sortie_humaine(resultat: Dict[str, Any], commande: str) -> str:
    """Formate la sortie lisible pour l’utilisateur."""
    if commande == "charger":
        return (
            f"Modèle chargé. Taille du vocabulaire : {resultat['taille_vocabulaire']}\n"
            f"Format : {resultat['format']}"
        )
    if commande == "metadonnees":
        regles = resultat["regles"]
        return (
            f"Taille du vocabulaire : {resultat['taille_vocabulaire']}\n"
            f"Règles de tokenisation :\n"
            f"  Pré‑tokenisation : {'oui' if regles['pretok'] else 'non'}\n"
            f"  Byte fallback : {'oui' if regles['byte_fallback'] else 'non'}\n"
            f"Premier token : {resultat['premier_token']}"
        )
    if commande == "exemple":
        return f"Nombre de lignes : {resultat['lignes']}"
    raise ValueError(f"Commande inconnue : {commande}")


def _formater_sortie_json(
    resultat: Dict[str, Any],
    commande: str,
    erreur: Optional[str] = None,
    denominateur: int = 1,
) -> Dict[str, Any]:
    """Construit l’objet JSON de sortie."""
    sortie = {
        "denominateur": denominateur,
        "examines": [str(resultat.get("modele", resultat.get("fichier", "")))],
        "examines_tronques": False,
    }
    if erreur:
        sortie["erreur"] = erreur
    else:
        if commande == "charger":
            sortie.update(
                {
                    "statut": resultat["statut"],
                    "taille_vocabulaire": resultat["taille_vocabulaire"],
                    "format": resultat["format"],
                }
            )
        elif commande == "metadonnees":
            sortie.update(
                {
                    "taille_vocabulaire": resultat["taille_vocabulaire"],
                    "regles": resultat["regles"],
                    "premier_token": resultat["premier_token"],
                }
            )
        elif commande == "exemple":
            sortie.update({"lignes": resultat["lignes"]})
    return sortie


def main() -> int:
    """Entrée principale de l’outil."""
    analyseur = _creer_analyseur()
    args = analyseur.parse_args()

    racine = getattr(args, "racine", RACINE)
    if not isinstance(racine, Path):
        racine = RACINE

    try:
        if args.commande == "charger":
            resultat = charger_modele(args.modele, racine)
        elif args.commande == "metadonnees":
            resultat = extraire_metadonnees(args.modele, racine)
        elif args.commande == "exemple":
            resultat = exemple_examen(args.fichier, racine)
        else:
            raise ValueError(f"Commande inconnue : {args.commande}")

        json_mode = getattr(args, "json", False)
        if json_mode:
            # denominateur strictement positif pour un succès
            sortie = _formater_sortie_json(resultat, args.commande, denominateur=1)
            json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print(_formater_sortie_humaine(resultat, args.commande), file=sys.stdout)

        return 0

    # ---- Gestion des refus (déni de données) ---------------------------------
    except (FileNotFoundError, ValueError, ErreurModele) as e:
        msg = f"denominateur nul : {e}"
        print(msg, file=sys.stderr)
        if getattr(args, "json", False):
            sortie = _formater_sortie_json(
                {}, args.commande, erreur=msg, denominateur=0
            )
            json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 3

    # ---- Bibliothèque manquante ------------------------------------------------
    except ErreurBibliotheque as e:
        msg = f"denominateur nul : {e}"
        print(msg, file=sys.stderr)
        if getattr(args, "json", False):
            sortie = _formater_sortie_json(
                {}, args.commande, erreur=msg, denominateur=0
            )
            json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 3

    # ---- Erreur inattendue ----------------------------------------------------
    except Exception as e:
        msg = f"Erreur inattendue : {e}"
        print(msg, file=sys.stderr)
        if getattr(args, "json", False):
            sortie = _formater_sortie_json(
                {}, args.commande, erreur=msg, denominateur=0
            )
            json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())