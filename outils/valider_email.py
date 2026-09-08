"""QUESTION      Cette adresse email est-elle valide ?
MESURE       Syntaxe de base par regex, syntaxe avancee et MX via email-validator/dnspython si present.
HYPOTHESES   L'entree est une adresse email ou un fichier texte/Python contenant des adresses.
LIMITES      Sans modules tierces, seule la syntaxe de base est verifiee (mode degrade).
CONTRE-EXEMPLES  Une adresse syntaxiquement valide mais sans boite receptrice renvoie valide en mode degrade.
DOMAINE      Adresses email individuelles.
"""
from __future__ import annotations

import sys
import json
import re
import argparse
from pathlib import Path
from typing import Any

try:
    from email_validator import validate_email as _ev_validate, EmailNotValidError
    _EMAIL_VALIDATOR = True
except ImportError:
    _EMAIL_VALIDATOR = False
    EmailNotValidError = type("EmailNotValidError", (Exception,), {})

try:
    import dns.resolver
    _DNSPYTHON = True
except ImportError:
    _DNSPYTHON = False

__all__ = ["valider_une_adresse", "valider_des_adresses"]


def valider_une_adresse(adresse: str) -> dict[str, Any]:
    """Valide une seule adresse email et retourne un dictionnaire de resultats."""
    resultat: dict[str, Any] = {
        "adresse": adresse,
        "valide": False,
        "raison": "",
        "mode": "degrade"
    }
    
    regex = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
    
    if _EMAIL_VALIDATOR:
        resultat["mode"] = "email-validator"
        try:
            _ev_validate(adresse, check_deliverability=_DNSPYTHON)
            resultat["valide"] = True
            resultat["raison"] = "Valide"
            return resultat
        except EmailNotValidError as e:
            resultat["raison"] = str(e)
            return resultat
        except Exception as e:
            resultat["raison"] = f"Erreur tierce: {e}"
            return resultat

    if not regex.match(adresse):
        resultat["raison"] = "Syntaxe de base invalide"
        return resultat

    if _DNSPYTHON:
        resultat["mode"] = "dnspython"
        domaine = adresse.split("@")[1]
        try:
            dns.resolver.resolve(domaine, "MX")
            resultat["valide"] = True
            resultat["raison"] = "Valide (MX present)"
            return resultat
        except Exception:
            resultat["raison"] = "Invalide (MX introuvable)"
            return resultat

    resultat["valide"] = True
    resultat["raison"] = "Syntaxe de base valide"
    return resultat


def valider_des_adresses(adresses: list[str]) -> dict[str, Any]:
    """Valide une liste d'adresses email."""
    examines = [valider_une_adresse(addr) for addr in adresses]
    return {
        "denominateur": len(examines),
        "examines": examines[:200],
        "examines_tronques": len(examines) > 200,
    }


def _contrat() -> dict[str, str]:
    return {
        "QUESTION": "Cette adresse email est-elle valide ?",
        "MESURE": "Syntaxe de base par regex, syntaxe avancee et MX via email-validator/dnspython si present.",
        "HYPOTHESES": "L'entree est une adresse email ou un fichier texte/Python contenant des adresses.",
        "LIMITES": "Sans modules tierces, seule la syntaxe de base est verifiee (mode degrade).",
        "CONTRE-EXEMPLES": "Une adresse syntaxiquement valide mais sans boite receptrice renvoie valide en mode degrade.",
        "DOMAINE": "Adresses email individuelles.",
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Valide une adresse email ou un fichier d'adresses.",
        epilog="Exemple: python valider_email.py user@example.com"
    )
    parser.add_argument("cible", help="L'adresse email ou le chemin du fichier a valider.")
    parser.add_argument("--json", action="store_true", help="Rend un seul objet JSON sur stdout.")
    parser.add_argument("--racine", help="Surcharge la racine et l'insere en tete de sys.path.")
    
    args = parser.parse_args()

    racine = Path(__file__).resolve().parent
    if args.racine:
        racine = Path(args.racine).resolve()
        sys.path.insert(0, str(racine))

    cible = args.cible
    chemin = Path(cible)
    adresses: list[str] = []

    try:
        if chemin.is_file():
            if chemin.suffix == ".py":
                source = chemin.read_text(encoding="utf-8")
                compile(source, str(chemin), "exec")
            lignes = chemin.read_text(encoding="utf-8").splitlines()
            adresses = [ligne.strip() for ligne in lignes if ligne.strip()]
        else:
            adresses = [cible]
    except Exception as e:
        sys.stderr.write(f"Erreur de lecture de la cible: {e}\n")
        return 1

    if not adresses:
        sys.stderr.write("Aucun element a examiner. Denominateur nul, refus de conclure.\n")
        if args.json:
            resultat = {
                "contrat": _contrat(),
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False
            }
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
        return 3

    try:
        validation = valider_des_adresses(adresses)
    except Exception as e:
        sys.stderr.write(f"Erreur lors de la validation: {e}\n")
        return 1

    defect_trouve = any(not r["valide"] for r in validation["examines"])

    if args.json:
        sortie = {
            "contrat": _contrat(),
            "denominateur": validation["denominateur"],
            "examines": validation["examines"],
            "examines_tronques": validation["examines_tronques"]
        }
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    else:
        for r in validation["examines"]:
            statut = "VALIDE" if r["valide"] else "INVALIDE"
            print(f"{r['adresse']}: {statut} ({r['raison']}) [mode: {r['mode']}]")
        
        if not _EMAIL_VALIDATOR and not _DNSPYTHON:
            sys.stderr.write("Mode degrade: modules tierces absents, syntaxe de base uniquement.\n")

    return 1 if defect_trouve else 0


if __name__ == "__main__":
    raise SystemExit(main())