#!/usr/bin/env python3.14
"""
QUESTION      Comment convertir ces données entre formats binaires/textuels ?
MESURE        Encodage/décodage base64, hex, UUID v7 avec optimisations tierces si disponibles
HYPOTHESES    Les données en entrée sont valides pour le format demandé
LIMITES       UUID v7 natif en Python 3.14 est lent, pas de UUID v8 natif
CONTRE-EXEMPLES UUID v7 généré avec random() peut avoir des collisions
INVOCATION
    {outil} encoder --base64 --donnees test --json
DOMAINE       Conversion de données binaires/textuelles pour stockage ou transmission
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import secrets
import struct
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

__all__ = [
    "encoder_base64",
    "decoder_base64",
    "encoder_hex",
    "decoder_hex",
    "generer_uuid7",
    "generer_uuid4",
    "main",
]

RACINE = Path(__file__).resolve().parent

# Configuration de l'encodage des flux standard
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Tentative d'import des modules tierces
try:
    import pybase64
    PYBASE64_DISPONIBLE = True
except ImportError:
    PYBASE64_DISPONIBLE = False
    print("pybase64 non disponible, utilisation de base64 standard", file=sys.stderr)

try:
    import fastuuid
    FASTUUID_DISPONIBLE = True
except ImportError:
    FASTUUID_DISPONIBLE = False
    print("fastuuid non disponible, utilisation de uuid standard", file=sys.stderr)

def encoder_base64(donnees: bytes, url_safe: bool = False, optimise: bool = False) -> bytes:
    """Encode des données binaires en base64.

    Args:
        donnees: Données binaires à encoder
        url_safe: Utilise l'alphabet URL-safe
        optimise: Utilise pybase64 si disponible

    Returns:
        Données encodées en base64
    """
    if optimise and PYBASE64_DISPONIBLE:
        if url_safe:
            return pybase64.urlsafe_b64encode(donnees)
        return pybase64.standard_b64encode(donnees)
    else:
        if url_safe:
            return base64.urlsafe_b64encode(donnees)
        return base64.b64encode(donnees)

def decoder_base64(donnees: Union[bytes, str], url_safe: bool = False, optimise: bool = False) -> bytes:
    """Décode des données base64 en binaire.

    Args:
        donnees: Données base64 à décoder
        url_safe: Utilise l'alphabet URL-safe
        optimise: Utilise pybase64 si disponible

    Returns:
        Données décodées en binaire
    """
    if isinstance(donnees, str):
        donnees = donnees.encode("ascii")

    if optimise and PYBASE64_DISPONIBLE:
        if url_safe:
            return pybase64.urlsafe_b64decode(donnees)
        return pybase64.standard_b64decode(donnees)
    else:
        if url_safe:
            return base64.urlsafe_b64decode(donnees)
        return base64.b64decode(donnees)

def encoder_hex(donnees: bytes) -> str:
    """Encode des données binaires en hexadécimal.

    Args:
        donnees: Données binaires à encoder

    Returns:
        Chaîne hexadécimale
    """
    return binascii.hexlify(donnees).decode("ascii")

def decoder_hex(donnees: Union[bytes, str]) -> bytes:
    """Décode des données hexadécimales en binaire.

    Args:
        donnees: Chaîne hexadécimale à décoder

    Returns:
        Données binaires
    """
    if isinstance(donnees, str):
        donnees = donnees.encode("ascii")
    return binascii.unhexlify(donnees)

def generer_uuid7() -> str:
    """Génère un UUID v7 optimisé si possible.

    Returns:
        UUID v7 sous forme de chaîne
    """
    if FASTUUID_DISPONIBLE:
        return str(fastuuid.uuid7())
    else:
        # Implémentation manuelle de UUID v7 (timestamp + random)
        timestamp = int((uuid.uuid1().time - 0x01b21dd213814000) * 100 // 1e9)
        random_part = secrets.token_bytes(10)
        time_high = (timestamp >> 16) & 0xFFFFFFFFFFFF
        time_low = timestamp & 0xFFFF
        uuid_int = (time_high << 80) | (0x7 << 76) | (time_low << 64) | int.from_bytes(random_part, "big")
        return str(uuid.UUID(int=uuid_int))

def generer_uuid4() -> str:
    """Génère un UUID v4.

    Returns:
        UUID v4 sous forme de chaîne
    """
    return str(uuid.uuid4())

def _construire_sortie_json(
    resultat: Any = None,
    denominateur: int = 0,
    examines: List[Dict[str, Any]] = None,
    erreur: str = None
) -> Dict[str, Any]:
    """Construit la sortie JSON standardisée avec dénominateur toujours présent."""
    base = {
        "denominateur": denominateur,
        "examines": examines or [],
        "contrat": {
            "QUESTION": "Comment convertir ces données entre formats binaires/textuels ?",
            "MESURE": "Encodage/décodage base64, hex, UUID v7 avec optimisations tierces si disponibles",
            "HYPOTHESES": "Les données en entrée sont valides pour le format demandé",
            "LIMITES": "UUID v7 natif en Python 3.14 est lent, pas de UUID v8 natif",
            "CONTRE-EXEMPLES": "UUID v7 généré avec random() peut avoir des collisions",
            "DOMAINE": "Conversion de données binaires/textuelles pour stockage ou transmission"
        }
    }

    if erreur:
        base["erreur"] = erreur
    else:
        base["resultat"] = resultat

    return base

def _refus_legitime(message: str) -> None:
    """Gère un refus légitime avec dénominateur=0."""
    print(f"Denominateur nul : {message}", file=sys.stderr)
    json.dump(
        _construire_sortie_json(denominateur=0, erreur=message),
        sys.stdout,
        ensure_ascii=False
    )
    raise SystemExit(3)

def _analyser_args() -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Outil de conversion entre formats binaires et textuels",
        epilog="Exemple: encoder_decoder.py encoder --base64 --donnees 'test' --optimise"
    )
    parser.add_argument(
        "--racine",
        type=str,
        help="Chemin racine à utiliser (surcharge la racine par défaut)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Sortie au format JSON"
    )

    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    # Sous-commande encodeur
    encodeur = sous_parsers.add_parser("encoder", help="Encoder des données")
    encodeur.add_argument(
        "--base64", action="store_true", help="Encoder en base64"
    )
    encodeur.add_argument(
        "--hex", action="store_true", help="Encoder en hexadécimal"
    )
    encodeur.add_argument(
        "--url-safe", action="store_true", help="Utiliser l'alphabet URL-safe pour base64"
    )
    encodeur.add_argument(
        "--optimise", action="store_true", help="Utiliser pybase64 si disponible"
    )
    encodeur.add_argument(
        "--donnees", type=str, required=True, help="Données à encoder"
    )

    # Sous-commande decodeur
    decodeur = sous_parsers.add_parser("decoder", help="Décoder des données")
    decodeur.add_argument(
        "--base64", action="store_true", help="Décoder depuis base64"
    )
    decodeur.add_argument(
        "--hex", action="store_true", help="Décoder depuis hexadécimal"
    )
    decodeur.add_argument(
        "--url-safe", action="store_true", help="Utiliser l'alphabet URL-safe pour base64"
    )
    decodeur.add_argument(
        "--optimise", action="store_true", help="Utiliser pybase64 si disponible"
    )
    decodeur.add_argument(
        "--donnees", type=str, required=True, help="Données à décoder"
    )

    # Sous-commande uuid
    uuid_parser = sous_parsers.add_parser("uuid", help="Générer un UUID")
    uuid_parser.add_argument(
        "--v7", action="store_true", help="Générer un UUID v7"
    )
    uuid_parser.add_argument(
        "--v4", action="store_true", help="Générer un UUID v4"
    )

    return parser.parse_args()

def main() -> int:
    """Point d'entrée principal."""
    args = _analyser_args()
    if args.racine:
        global RACINE
        RACINE = Path(args.racine).resolve()
        sys.path.insert(0, str(RACINE))

    try:
        if args.commande == "encoder":
            if not (args.base64 or args.hex):
                if args.json:
                    _refus_legitime("Aucun format d'encodage spécifié")
                else:
                    print("Aucun format d'encodage spécifié", file=sys.stderr)
                    return 1

            donnees = args.donnees.encode("utf-8")
            if args.base64:
                resultat = encoder_base64(donnees, args.url_safe, args.optimise)
                sortie = {
                    "format": "base64",
                    "url_safe": args.url_safe,
                    "optimise": args.optimise and PYBASE64_DISPONIBLE,
                    "donnees": resultat.decode("ascii")
                }
            elif args.hex:
                resultat = encoder_hex(donnees)
                sortie = {
                    "format": "hex",
                    "donnees": resultat
                }

            if args.json:
                json.dump(
                    _construire_sortie_json(
                        resultat=sortie,
                        denominateur=1,
                        examines=[{"nom": "encodage", "valeur": args.donnees}]
                    ),
                    sys.stdout,
                    ensure_ascii=False
                )
            else:
                print(sortie["donnees"])

        elif args.commande == "decoder":
            if not (args.base64 or args.hex):
                if args.json:
                    _refus_legitime("Aucun format de décodage spécifié")
                else:
                    print("Aucun format de décodage spécifié", file=sys.stderr)
                    return 1

            if args.base64:
                try:
                    resultat = decoder_base64(args.donnees, args.url_safe, args.optimise)
                    sortie = {
                        "format": "base64",
                        "url_safe": args.url_safe,
                        "optimise": args.optimise and PYBASE64_DISPONIBLE,
                        "donnees": resultat.decode("utf-8", errors="replace")
                    }
                except (binascii.Error, ValueError) as e:
                    if args.json:
                        _refus_legitime(f"Erreur de décodage base64: {e}")
                    else:
                        print(f"Erreur de décodage base64: {e}", file=sys.stderr)
                        return 2
            elif args.hex:
                try:
                    resultat = decoder_hex(args.donnees)
                    sortie = {
                        "format": "hex",
                        "donnees": resultat.decode("utf-8", errors="replace")
                    }
                except (binascii.Error, ValueError) as e:
                    if args.json:
                        _refus_legitime(f"Erreur de décodage hex: {e}")
                    else:
                        print(f"Erreur de décodage hex: {e}", file=sys.stderr)
                        return 2

            if args.json:
                json.dump(
                    _construire_sortie_json(
                        resultat=sortie,
                        denominateur=1,
                        examines=[{"nom": "decodage", "valeur": args.donnees}]
                    ),
                    sys.stdout,
                    ensure_ascii=False
                )
            else:
                print(sortie["donnees"])

        elif args.commande == "uuid":
            if not (args.v7 or args.v4):
                if args.json:
                    _refus_legitime("Aucun type d'UUID spécifié")
                else:
                    print("Aucun type d'UUID spécifié", file=sys.stderr)
                    return 1

            if args.v7:
                resultat = generer_uuid7()
            elif args.v4:
                resultat = generer_uuid4()

            sortie = {"uuid": resultat, "version": 7 if args.v7 else 4}

            if args.json:
                json.dump(
                    _construire_sortie_json(
                        resultat=sortie,
                        denominateur=1,
                        examines=[{"nom": "generation_uuid", "version": sortie["version"]}]
                    ),
                    sys.stdout,
                    ensure_ascii=False
                )
            else:
                print(resultat)

    except Exception as e:
        if args.json:
            _refus_legitime(f"Erreur inattendue: {str(e)}")
        else:
            print(f"Erreur inattendue: {str(e)}", file=sys.stderr)
            return 3

    return 0

if __name__ == "__main__":
    raise SystemExit(main())