#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
QUESTION
Quels fichiers ont des permissions dangereuses ?

MESURE
Nombre de fichiers examinés et liste de ceux présentant des permissions pouvant représenter un risque de sécurité (écriture par autrui, bits SUID/SGID sur fichiers exécutables réguliers).

HYPOTHÈSES
Les permissions sont définies selon le modèle Unix traditionnel ; les systèmes de fichiers locaux sont accessibles en lecture ; les liens symboliques ne sont pas suivis pour éviter les boucles.

LIMITES
L'outil ne vérifie pas les ACL étendues, les capacités, ni les restrictions spécifiques à SELinux/AppArmor ; il ne examine pas les systèmes de fichiers réseau pouvant masquer les vrais propriétaires.

CONTRE-EXEMPLES
Un fichier possédant le bit SUID mais appartenant à un compte système légitime (comme /usr/bin/passwd) est considéré dangereux par l'outil bien que son usage soit attendu.

INVOCATION
    {outil} --racine {dossier} --json

DOMAINE
Tout système de fichiers POSIX accessible via os.walk à partir de la racine spécifiée.
"""

from __future__ import annotations

import sys
import os
import stat
import json
from pathlib import Path
import argparse
from typing import List, Tuple

try:
    import grp  # noqa: F401
except ImportError:  # pragma: no cover
    grp = None  # type: ignore

__all__ = ["analyser_permissions"]


def analyser_permissions(racine: Path) -> Tuple[int, List[Path], List[Path], bool]:
    """
    Analyse les permissions des fichiers et répertoires sous ``racine``.

    Retourne un tuple ``(denominateur, examines, dangereux, tronque)`` où :
    - ``denominateur`` est le nombre d'éléments pour lesquels un ``lstat`` a réussi.
    - ``examines`` est la liste (limitée à 200) des chemins examinés.
    - ``dangereux`` est la liste des chemins présentant des permissions dangereuses.
    - ``tronque`` indique si la liste ``examines`` a été coupée à 200 éléments.
    """
    denom = 0
    examined: List[Path] = []
    dangerous: List[Path] = []

    try:
        for dirpath, dirnames, filenames in os.walk(
            racine, topdown=True, onerror=None, followlinks=False
        ):
            entry_path = Path(dirpath)
            try:
                mode = os.lstat(entry_path).st_mode
            except OSError:
                pass
            else:
                _process_entry(mode, entry_path, examined, dangerous)
                denom += 1

            for name in filenames:
                entry_path = Path(dirpath) / name
                try:
                    mode = os.lstat(entry_path).st_mode
                except OSError:
                    continue
                _process_entry(mode, entry_path, examined, dangerous)
                denom += 1
    except Exception:  # pragma: no cover
        pass

    truncated = denom > len(examined)
    return denom, examined, dangerous, truncated


def _process_entry(mode: int, entry_path: Path, examined: List[Path], dangerous: List[Path]) -> None:
    """Met à jour les listes ``examines`` et ``dangereux`` selon le mode."""
    if len(examined) < 200:
        examined.append(entry_path)

    if mode & stat.S_IWOTH:
        dangerous.append(entry_path)
    elif stat.S_ISREG(mode) and (mode & (stat.S_ISUID | stat.S_ISGID)):
        dangerous.append(entry_path)


def _build_contrat() -> dict[str, str]:
    return {
        "QUESTION": "Quels fichiers ont des permissions dangereuses ?",
        "MESURE": (
            "Nombre de fichiers examinés et liste de ceux présentant des permissions pouvant représenter "
            "un risque de sécurité (écriture par autrui, bits SUID/SGID sur fichiers exécutables réguliers)."
        ),
        "HYPOTHÈSES": (
            "Les permissions sont définies selon le modèle Unix traditionnel ; les systèmes de fichiers locaux "
            "sont accessibles en lecture ; les liens symboliques ne sont pas suivis pour éviter les boucles."
        ),
        "LIMITES": (
            "L'outil ne vérifie pas les ACL étendues, les capacités, ni les restrictions spécifiques à SELinux/AppArmor ; "
            "il ne examine pas les systèmes de fichiers réseau pouvant masquer les vrais propriétaires."
        ),
        "CONTRE-EXEMPLES": (
            "Un fichier possédant le bit SUID mais appartenant à un compte système légitime (comme /usr/bin/passwd) "
            "est considéré dangereux par l'outil bien que son usage soit attendu."
        ),
        "DOMAINE": (
            "Tout système de fichiers POSIX accessible via os.walk à partir de la racine spécifiée."
        ),
    }


class _JsonArgParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if "--json" in sys.argv:
            payload = {
                "contrat": _build_contrat(),
                "denominateur": 0,
                "erreur": f"Erreur d'argument: {message}",
            }
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            raise SystemExit(2)
        super().error(message)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = _JsonArgParser(
        description=(
            "Liste les fichiers dont les permissions sont considérées dangereuses.\n"
            "Exemple d'appel : python auditeur_permissions.py --racine /home/utilisateur"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--racine",
        type=str,
        help="Répertoire racine à analyser (par défaut : répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON unique sur stdout.",
    )
    args = parser.parse_args()

    racine_path = Path(args.racine).resolve() if args.racine else Path(__file__).resolve().parent

    if not racine_path.exists():
        msg = f"Erreur : le chemin racine '{racine_path}' n'existe pas."
        print(msg, file=sys.stderr)
        if args.json:
            payload = {"contrat": _build_contrat(), "denominateur": 0, "erreur": msg}
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 1
    if not racine_path.is_dir():
        msg = f"Erreur : le chemin racine '{racine_path}' n'est pas un répertoire."
        print(msg, file=sys.stderr)
        if args.json:
            payload = {"contrat": _build_contrat(), "denominateur": 0, "erreur": msg}
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 1

    sys.path.insert(0, str(racine_path))

    denom, examined, dangerous, truncated = analyser_permissions(racine_path)

    if denom == 0:
        msg = "Aucun élément examiné ; impossible de conclure."
        print(msg, file=sys.stderr)
        if args.json:
            payload = {"contrat": _build_contrat(), "denominateur": 0, "erreur": msg}
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 3

    if args.json:
        payload = {
            "contrat": _build_contrat(),
            "denominateur": denom,
            "examines": [str(p) for p in examined],
            "examines_tronques": truncated,
            "dangereux": [str(p) for p in dangerous],
            "total_dangereux": len(dangerous),
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0 if not dangerous else 1
    else:
        if not dangerous:
            print("Aucun fichier dangereux détecté.")
        else:
            for p in dangerous:
                print(p)
        return 0 if not dangerous else 1


if __name__ == "__main__":
    raise SystemExit(main())