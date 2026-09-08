"""QUESTION
Quels fichiers sont modifiés en temps réel ?
MESURE
L'outil parcourt le(s) fichier(s) cible(s) et détecte tout changement de
horodatage de modification (st_mtime) pendant une période d'observation.
HYPOTHÈSES
Le système de fichiers met à jour st_mtime dès qu'un processus écrit dans le
fichier. Aucun autre mécanisme de notification n'est utilisé.
LIMITES
Seules les modifications détectées par variation de st_mtime sont prises en
compte ; les changements de contenu qui n'affectent pas l'horodatage, les
modifications de métadonnées (ex. attributs Windows) ou les fichiers
ouvertes en mode exclusif peuvent être manqués.
CONTRE-EXEMPLES
Un fichier dont le contenu est modifié mais dont le système ne met pas à jour
st_mtime (ex. caches réseau) ne sera pas signalé.
DOMAINE
Sur tout système supporté par le module standard `os.stat`, notamment Windows
et POSIX, où l'accès en lecture aux métadonnées est autorisé.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Set, Tuple

# Réglage de l'encodage de la console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__ = ["auditer_fichiers", "contrat"]

contrat = {
    "QUESTION": "Quels fichiers sont modifiés en temps réel ?",
    "MESURE": "Détection de variations de st_mtime pendant l'observation.",
    "HYPOTHÈSES": "st_mtime reflète chaque écriture sur le fichier.",
    "LIMITES": "Ignoré les changements non reflétés dans st_mtime, "
               "et les métadonnées autres que la date de modification.",
    "CONTRE_EXEMPLES": "Modifications de contenu sans mise à jour de st_mtime.",
    "DOMAINE": "Systèmes où os.stat() fournit un st_mtime fiable.",
}


class BinaryFileError(RuntimeError):
    """Exception levée lorsqu'un fichier .py est détecté comme binaire."""


def _lire_fichier_texte(chemin: Path) -> str:
    """Lit un fichier texte en UTF‑8 ; signale les erreurs ou la présence du caractère nul."""
    try:
        texte = chemin.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RuntimeError(f"Fichier illisible {chemin} : {exc}") from exc

    if chr(0) in texte:
        raise BinaryFileError(f"Fichier binaire détecté {chemin}")

    return texte


def _collecter_fichiers(cible: Path) -> List[Path]:
    """Renvoie la liste des fichiers réguliers sous *cible* (récursif)."""
    if not cible.exists():
        raise FileNotFoundError(f"cible inexistante : {cible}")
    if cible.is_file():
        return [cible]
    fichiers: List[Path] = []
    for dirpath, _, filenames in os.walk(cible):
        for name in filenames:
            p = Path(dirpath) / name
            if p.is_file():
                fichiers.append(p)
    return fichiers


def _snapshot(fichiers: List[Path]) -> dict[Path, float]:
    """Capture les st_mtime de chaque fichier, en vérifiant la lisibilité des *.py*."""
    snap: dict[Path, float] = {}
    for f in fichiers:
        if f.suffix == ".py":
            # Vérifie que le fichier .py est lisible comme texte UTF‑8.
            _ = _lire_fichier_texte(f)  # le contenu n’est pas utilisé.
        try:
            snap[f] = os.stat(f).st_mtime
        except OSError as e:
            print(f"avertissement : impossible d'accéder à {f} ({e})", file=sys.stderr)
    return snap


def auditer_fichiers(
    cible: Path,
    duree: float = 5.0,
    intervalle: float = 0.5,
) -> Tuple[int, List[str]]:
    """
    Observe *cible* pendant *duree* secondes, en interrogeant toutes les
    *intervalle* secondes. Retourne (nombre_examines, liste_chemins_modifies).
    """
    try:
        fichiers = _collecter_fichiers(cible)
    except Exception as e:
        raise RuntimeError(str(e))

    if not fichiers:
        return 0, []

    snapshot_initial = _snapshot(fichiers)
    modifiés: Set[Path] = set()
    fin = time.time() + duree
    while time.time() < fin:
        time.sleep(intervalle)
        snapshot_courant = _snapshot(fichiers)
        for f, mtime in snapshot_courant.items():
            if f not in snapshot_initial:
                continue
            if mtime != snapshot_initial[f]:
                modifiés.add(f)
        snapshot_initial = snapshot_courant

    return len(fichiers), [str(p) for p in sorted(modifiés)]


def _afficher_humain(denominateur: int, examines: List[str]) -> None:
    if denominateur == 0:
        print("Aucun fichier n'a pu être examiné.", file=sys.stderr)
        return
    if not examines:
        print("Aucun fichier modifié en temps réel n'a été détecté.")
    else:
        print("Fichiers modifiés en temps réel :")
        for p in examines:
            print(p)


def _produire_json(
    denominateur: int,
    examines: List[str],
    contract: dict,
) -> str:
    limite = 200
    tronque = len(examines) > limite
    payload = {
        "contrat": contract,
        "denominateur": denominateur,
        "examines": examines[:limite],
        "examines_tronques": tronque,
    }
    return json.dumps(payload, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Détecte les fichiers modifiés en temps réel.",
        epilog=(
            "Exemple : python auditeur_fichiers.py --duree 10 --intervalle 1 "
            "--racine C:\\projet mon_dossier"
        ),
    )
    parser.add_argument(
        "cible",
        nargs="?",
        default=None,
        help="Chemin du fichier ou du répertoire à observer.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=None,
        help="Chemin racine à préfixer dans sys.path (remplace le répertoire du script).",
    )
    parser.add_argument(
        "--duree",
        type=float,
        default=5.0,
        help="Durée d'observation en secondes (défaut : 5).",
    )
    parser.add_argument(
        "--intervalle",
        type=float,
        default=0.5,
        help="Intervalle entre deux sondages en secondes (défaut : 0.5).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique sur stdout.",
    )

    args = parser.parse_args()

    # Gestion du chemin racine
    racine_defaut = Path(__file__).resolve().parent
    racine = args.racine or racine_defaut
    if racine not in map(Path, sys.path):
        sys.path.insert(0, str(racine))

    # Détermination de la cible
    cible_path = Path(args.cible) if args.cible else racine_defaut
    if not cible_path.exists():
        print(f"Erreur : cible inexistante : {cible_path}", file=sys.stderr)
        return 2

    try:
        denominateur, examines = auditer_fichiers(
            cible=cible_path,
            duree=args.duree,
            intervalle=args.intervalle,
        )
    except BinaryFileError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        # Production d'un JSON avec dénominateur = 0 même si --json n'est pas demandé
        if args.json:
            json_out = _produire_json(0, [], contrat)
            print(json_out)
        return 2
    except RuntimeError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Erreur inattendue : {e}", file=sys.stderr)
        return 2

    if args.json:
        json_out = _produire_json(denominateur, examines, contrat)
        print(json_out)
    else:
        _afficher_humain(denominateur, examines)

    # Code de sortie
    if denominateur == 0:
        return 3  # refus de conclure
    return 0 if not examines else 1


if __name__ == "__main__":
    raise SystemExit(main())