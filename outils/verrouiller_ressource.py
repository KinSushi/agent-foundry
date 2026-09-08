"""\
QUESTION      Vérifie si une ressource (fichier) est déjà utilisée par un autre processus.
MESURE        Tentative d'acquisition d'un verrou exclusif non bloquant sur le premier octet.
HYPOTHESES    Le verrouillage est consultatif et partagé par tous les processus ; le fichier est accessible en lecture/écriture.
LIMITES       Ne détecte que les verrous sur le premier octet ; ne fonctionne pas sur systèmes de fichiers sans verrouillage consultatif.
CONTRE-EXEMPLES   Cas où le processus ouvre le fichier en lecture seule sans verrouillage ou utilise un verrou partiel.
INVOCATION
    {outil} {fichier} --json
DOMAINE      Systèmes de fichiers Unix avec fcntl et Windows avec msvcrt.
"""

from __future__ import annotations

import sys
import os
import errno
import json
from pathlib import Path
import argparse
from typing import NoReturn

# Racine portable — l'outil ne connaît aucun projet
RACINE = Path(__file__).resolve().parent

def verifier_fichier_illisible(ressource: Path) -> bool:
    """Vérifie si le fichier est illisible ou binaire."""
    try:
        with open(ressource, 'rb') as f:
            content = f.read(1024)
            try:
                content.decode('utf-8')
            except UnicodeDecodeError:
                return True
    except OSError:
        return True
    return False

def verrouiller_ressource(ressource: Path) -> bool:
    """
    Retourne True si la ressource est verrouillée (utilisée par un autre processus), False sinon.
    Lève une OSError en cas d'erreur d'ouverture ou de verrouillage.
    """
    if not ressource.is_file():
        raise OSError(f"La ressource '{ressource}' n'existe pas ou n'est pas un fichier.")

    if ressource.suffix == '.py' and verifier_fichier_illisible(ressource):
        raise OSError(f"Le fichier '{ressource}' est illisible ou binaire.")

    try:
        fd = os.open(ressource, os.O_RDWR)
    except OSError as e:
        raise OSError(
            f"Impossible d'ouvrir la ressource '{ressource}' en lecture/écriture : {e}"
        ) from e

    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if sys.platform.startswith("win"):
            import msvcrt

            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as e:
                if e.errno in (errno.EACCES, errno.EAGAIN):
                    return True
                raise
        else:
            import fcntl

            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0, 0)
            except OSError as e:
                if e.errno in (errno.EACCES, errno.EAGAIN):
                    return True
                raise
        return False
    finally:
        os.close(fd)

def main() -> int:
    # Encodage UTF‑8 pour la console Windows
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Vérifie si une ressource (fichier) est déjà utilisée par un autre processus.",
        epilog="Exemple : python verrouiller_ressource.py mon_fichier.txt",
    )
    parser.add_argument(
        "ressource",
        help="Chemin vers la ressource (fichier) à vérifier.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Override la racine du projet (utilisée pour ajouter au sys.path si l'outil importe une cible).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON au lieu du texte lisible par un humain.",
    )

    args = parser.parse_args()

    # Gestion de l'option --racine
    if args.racine:
        global RACINE
        RACINE = args.racine.resolve()
        sys.path.insert(0, str(RACINE))

    ressource_path = Path(args.ressource).resolve()

    try:
        if ressource_path.suffix == '.py':
            try:
                with open(ressource_path, 'r', encoding='utf-8', errors='replace') as f:
                    f.read()
            except (OSError, UnicodeDecodeError) as e:
                print(f"Erreur : Le fichier '{ressource_path}' est illisible ou binaire : {e}", file=sys.stderr)
                if args.json:
                    result = {
                        "contrat": {
                            "QUESTION": "Cette ressource est-elle déjà utilisée ?",
                            "MESURE": "Vérification de lisibilité du fichier.",
                            "HYPOTHESES": [],
                            "LIMITES": [],
                            "CONTRE-EXEMPLES": [],
                            "DOMAINE": ""
                        },
                        "denominateur": 0,
                        "examines": [str(ressource_path)],
                        "examines_tronques": False,
                        "resultat": None,
                        "erreur": str(e)
                    }
                    print(json.dumps(result, ensure_ascii=False))
                return 3
        est_verrouillee = verrouiller_ressource(ressource_path)
    except OSError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        if args.json:
            result = {
                "contrat": {
                    "QUESTION": "Cette ressource est-elle déjà utilisée ?",
                    "MESURE": "Essai d'acquisition d'un verrou exclusif non-bloquant.",
                    "HYPOTHESES": [],
                    "LIMITES": [],
                    "CONTRE-EXEMPLES": [],
                    "DOMAINE": ""
                },
                "denominateur": 0,
                "examines": [str(ressource_path)],
                "examines_tronques": False,
                "resultat": None,
                "erreur": str(e)
            }
            print(json.dumps(result, ensure_ascii=False))
        return 2

    if args.json:
        contrat = {
            "QUESTION": "Cette ressource est-elle déjà utilisée ?",
            "MESURE": "Essai d'acquisition d'un verrou exclusif non-bloquant sur le premier octet de la ressource.",
            "HYPOTHESES": [
                "Le verrouillage est consultatif et tous les processus utilisant la ressource utilisent le même mécanisme de verrouillage.",
                "La ressource est un fichier régulier accessible en lecture/écriture.",
            ],
            "LIMITES": [
                "Le verrouillage ne porte que sur le premier octet.",
                "Ne détecte pas les verrouillages partiels.",
                "Sur les systèmes de fichiers où le verrouillage consultatif n'est pas supporté, le résultat peut être faux.",
            ],
            "CONTRE-EXEMPLES": [
                "Un processus qui ouvre la ressource en lecture seule sans verrouillage : faux négatif.",
                "Un processus qui utilise un verrouillage partiel sur un autre octet : faux négatif.",
            ],
            "DOMAINE": "Tout système de fichiers supportant le verrouillage consultatif via fcntl (Unix) ou msvcrt (Windows).",
        }
        result = {
            "contrat": contrat,
            "denominateur": 1,
            "examines": [str(ressource_path)],
            "examines_tronques": False,
            "resultat": est_verrouillee,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0 if not est_verrouillee else 1
    else:
        if est_verrouillee:
            print("La ressource est déjà utilisée.")
            return 1
        print("La ressource n'est pas utilisée.")
        return 0

if __name__ == "__main__":
    raise SystemExit(main())