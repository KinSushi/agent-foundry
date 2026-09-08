#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verifier_signature_jwt.py

QUESTION      Ce token JWT est‑il valide ?
MESURE        Vérification de la signature HMAC‑SHA256 et de la claim « exp » (expiration).
HYPOTHÈSES   Le jeton utilise l’algorithme HS256, la clé secrète est fournie en UTF‑8,
              le claim « exp » est un timestamp UNIX entier ou flottant.
LIMITES       Ne vérifie que les algorithmes HMAC (HS256). Ne valide pas les autres
              claims (nbf, iat, aud, iss, …) ni les signatures RSA/ECDSA.
CONTRE-EXEMPLES Un token signé avec HS384 sera considéré invalide même si la signature
              est correcte car l’outil ne supporte que HS256.
INVOCATION
    {outil} {fichier} --secret secret --json
DOMAINE       Tout jeton JWT HS256 où l’on possède la clé secrète utilisée pour la signature.
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import sys
import time
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parent

__all__: list[str] = ["verifier_jwt"]


def verifier_jwt(token: str, secret: bytes) -> dict[str, Any]:
    """
    Vérifie la signature et l'expiration d'un JWT.

    Retourne un dictionnaire contenant :
        - denominateur : nombre d'éléments effectivement examinés
        - examines     : liste des noms des éléments examinés
        - examines_tronques : True si la liste a été tronquée à 200 éléments
        - valide       : True si signature et expiration sont correctes
        - erreurs      : liste de messages d'erreur éventuels
    """
    examines: list[str] = []
    denominateur: int = 0
    erreurs: list[str] = []
    valide: bool = True

    # --- Séparation des trois parties ---
    parts = token.split('.')
    if len(parts) != 3:
        erreurs.append("Format JWT invalide : attendu 3 parties séparées par '.'")
        return {
            "denominateur": 0,
            "examines": [],
            "examines_tronques": False,
            "valide": False,
            "erreurs": erreurs,
        }

    # --- Décodage base64url ---
    def _decode_b64url(s: str) -> bytes:
        padding = 4 - len(s) % 4
        if padding == 4:
            padding = 0
        return base64.urlsafe_b64decode(s + "=" * padding)

    try:
        header_b64, payload_b64, signature_b64 = parts
        header = _decode_b64url(header_b64)
        payload = _decode_b64url(payload_b64)
        signature = _decode_b64url(signature_b64)
    except Exception as exc:  # pragma: no cover - defensive
        erreurs.append(f"Échec de décodage base64url : {exc}")
        return {
            "denominateur": 0,
            "examines": [],
            "examines_tronques": False,
            "valide": False,
            "erreurs": erreurs,
        }

    # --- Vérification de la signature (HMAC‑SHA256) ---
    denominateur += 1
    examines.append("signature")
    try:
        mac = hmac.digest(secret, header + b"." + payload, "SHA256")
        if not hmac.compare_digest(mac, signature):
            erreurs.append("Signature HMAC‑SHA256 invalide")
            valide = False
    except Exception as exc:  # pragma: no cover
        erreurs.append(f"Erreur lors de la vérification de la signature : {exc}")
        valide = False

    # --- Vérification de l'expiration (claim 'exp') ---
    try:
        payload_obj = json.loads(payload.decode("utf-8"))
        exp = payload_obj.get("exp")
        if isinstance(exp, (int, float)):
            denominateur += 1
            examines.append("expiration")
            now = time.time()
            if exp < now:
                erreurs.append(f"Token expiré depuis {int(now - exp)} seconde(s)")
                valide = False
        else:
            denominateur += 1
            examines.append("expiration")
            erreurs.append("Claim 'exp' manquant ou non numérique")
            valide = False
    except Exception as exc:  # pragma: no cover
        denominateur += 1
        examines.append("expiration")
        erreurs.append(f"Impossible de décoder le payload JSON : {exc}")
        valide = False

    # --- Tronquage éventuel de la liste examines ---
    examines_tronques = len(examines) > 200
    if examines_tronques:
        examines = examines[:200]

    return {
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": examines_tronques,
        "valide": valide,
        "erreurs": erreurs,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Vérifie la signature et l'expiration d'un jeton JWT.",
        epilog=(
            "Exemple: %(prog)s \"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c\" --secret monsecret"
        ),
        add_help=False,
    )
    parser.add_argument(
        "token",
        help="Jeton JWT à vérifier (chaîne contenant trois parties séparées par '.')",
    )
    parser.add_argument(
        "--secret",
        required=True,
        help="Clé secrète utilisée pour la signature HMAC (texte UTF-8)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON unique sur stdout (résultat brut)",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Chemin racine à ajouter au sys.path avant toute importation",
    )
    return parser


def main() -> int:
    # Encodage de la console (Windows cp1252 → UTF-8)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = _build_arg_parser()

    # Gestion manuelle de l'option --help
    if "--help" in sys.argv or "-h" in sys.argv:
        parser.print_help(sys.stdout)
        return 0

    args = parser.parse_args()

    # Détermination de la racine à ajouter au sys.path
    root_path = args.racine.resolve() if args.racine is not None else RACINE
    sys.path.insert(0, str(root_path))

    secret_bytes = args.secret.encode("utf-8")
    result = verifier_jwt(args.token, secret_bytes)

    if args.json:
        # Sortie JSON unique sur stdout
        json.dump(result, sys.stdout, ensure_ascii=False)
        print()  # newline
        if result["denominateur"] == 0:
            # Refus légitime : aucun élément réellement examiné
            print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
            return 3
        return 0

    # Mode lisible par un humain (sans --json)
    if result["denominateur"] == 0:
        print("Aucun élément à examiner : jeton malformé.", file=sys.stderr)
        return 3

    if result["valide"]:
        print("Token valide.")
        return 0
    else:
        msg = "Token invalide : " + "; ".join(result["erreurs"])
        print(msg)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())