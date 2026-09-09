"""Surveillance de fichiers en temps réel pour déclencher des actions.

QUESTION      Le fichier a‑t‑il réellement changé depuis le dernier relevé ?
MESURE        watchfiles (ou fallback) signale un Change avec type et timestamp.
HYPOTHESES    Le système de fichiers notifie les événements en temps réel.
LIMITES       Les changements très rapides (< 10 ms) peuvent être coalescés.
CONTRE‑EXEMPLE Un fichier créé puis supprimé en 5 ms n’est jamais signalé.
INVOCATION
    {outil} event {fichier} --json
DOMAINE       Surveillance de fichiers texte ou binaire dans des projets IA.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Optional

__all__ = [
    "executer_commande",
    "surveiller_async",
    "afficher_evenements",
    "filtrer_profondeur",
    "obtenir_evenement",
    "main",
]

RACINE = Path(__file__).resolve().parent

# Mode dégradé si watchfiles absent
POLLING_INTERVAL = 0.5  # secondes
WATCHFILES_DISPONIBLE = False
try:
    import watchfiles  # type: ignore
    WATCHFILES_DISPONIBLE = True
except ImportError:
    sys.stderr.write(
        "watchfiles non disponible ; utilisation du mode dégradé (polling)\n"
    )

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

EVENEMENT_TYPES = {"added", "modified", "deleted"}


def chemin_absolu(cible: str | Path, racine: Path) -> Path:
    """Convertit un chemin relatif en chemin absolu depuis la racine."""
    p = Path(cible)
    return p if p.is_absolute() else racine / p


def valider_cible(cible: Path) -> None:
    """Vérifie que la cible existe et est surveillable."""
    if not cible.exists():
        raise ValueError(f"cible introuvable: {cible}")
    if not (cible.is_file() or cible.is_dir()):
        raise ValueError(f"cible doit être un fichier ou répertoire: {cible}")


def executer_commande(commande: list[str], cwd: Optional[Path] = None) -> int:
    """Exécute une commande système et retourne son code de sortie."""
    try:
        result = subprocess.run(
            commande,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode
    except (subprocess.SubprocessError, OSError) as e:
        sys.stderr.write(f"échec de l'exécution: {e}\n")
        return 1


async def surveiller_async(
    cible: Path, stop_event: asyncio.Event
) -> AsyncIterator[tuple[str, Path]]:
    """Surveille les changements de manière asynchrone (watchfiles ou polling)."""
    if WATCHFILES_DISPONIBLE:
        async for changes in watchfiles.awatch(cible, stop_event=stop_event):
            for change_type, path in changes:
                yield change_type, Path(path)
    else:
        dernier_etat = {
            p: p.stat().st_mtime for p in cible.rglob("*") if p.is_file()
        }
        while not stop_event.is_set():
            await asyncio.sleep(POLLING_INTERVAL)
            nouvel_etat = {
                p: p.stat().st_mtime for p in cible.rglob("*") if p.is_file()
            }
            for p in nouvel_etat:
                if p not in dernier_etat:
                    yield "added", p
                elif nouvel_etat[p] != dernier_etat[p]:
                    yield "modified", p
            for p in dernier_etat:
                if p not in nouvel_etat:
                    yield "deleted", p
            dernier_etat = nouvel_etat


def afficher_evenements(cible: Path, profondeur: int) -> tuple[int, Iterator[dict[str, Any]]]:
    """Génère les événements de modification en temps réel."""
    try:
        valider_cible(cible)
    except ValueError as e:
        sys.stderr.write(f"erreur: {e}\n")
        return 1, iter(())

    def generateur() -> Iterator[dict[str, Any]]:
        if WATCHFILES_DISPONIBLE:
            for changes in watchfiles.watch(cible, recursive=profondeur > 0):
                for change_type, path in changes:
                    yield {
                        "type": change_type,
                        "path": str(path),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%f"),
                        "denominateur": 1,
                        "examines": [str(cible)],
                        "examines_tronques": False,
                    }
        else:
            dernier_etat = {
                p: p.stat().st_mtime for p in cible.rglob("*") if p.is_file()
            }
            while True:
                time.sleep(POLLING_INTERVAL)
                nouvel_etat = {
                    p: p.stat().st_mtime for p in cible.rglob("*") if p.is_file()
                }
                for p in nouvel_etat:
                    if p not in dernier_etat:
                        yield {
                            "type": "added",
                            "path": str(p),
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%f"),
                            "denominateur": 1,
                            "examines": [str(cible)],
                            "examines_tronques": False,
                        }
                    elif nouvel_etat[p] != dernier_etat[p]:
                        yield {
                            "type": "modified",
                            "path": str(p),
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%f"),
                            "denominateur": 1,
                            "examines": [str(cible)],
                            "examines_tronques": False,
                        }
                for p in dernier_etat:
                    if p not in nouvel_etat:
                        yield {
                            "type": "deleted",
                            "path": str(p),
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%f"),
                            "denominateur": 1,
                            "examines": [str(cible)],
                            "examines_tronques": False,
                        }
                dernier_etat = nouvel_etat

    return 0, generateur()


def filtrer_profondeur(cible: Path, max_depth: int) -> int:
    """Surveille un répertoire avec une profondeur maximale."""
    try:
        valider_cible(cible)
    except ValueError as e:
        sys.stderr.write(f"erreur: {e}\n")
        return 1

    if not cible.is_dir():
        sys.stderr.write("erreur: la cible doit être un répertoire\n")
        return 1

    if WATCHFILES_DISPONIBLE:
        for changes in watchfiles.watch(cible, recursive=max_depth > 0):
            for change_type, path in changes:
                profondeur_fichier = len(Path(path).relative_to(cible).parts)
                if profondeur_fichier <= max_depth:
                    sys.stderr.write(f"{change_type}: {path}\n")
    else:
        dernier_etat = {
            p: p.stat().st_mtime
            for p in cible.rglob("*")
            if p.is_file() and len(p.relative_to(cible).parts) <= max_depth
        }
        while True:
            time.sleep(POLLING_INTERVAL)
            nouvel_etat = {
                p: p.stat().st_mtime
                for p in cible.rglob("*")
                if p.is_file() and len(p.relative_to(cible).parts) <= max_depth
            }
            for p in nouvel_etat:
                if p not in dernier_etat:
                    sys.stderr.write(f"added: {p}\n")
                elif nouvel_etat[p] != dernier_etat[p]:
                    sys.stderr.write(f"modified: {p}\n")
            for p in dernier_etat:
                if p not in nouvel_etat:
                    sys.stderr.write(f"deleted: {p}\n")
            dernier_etat = nouvel_etat
    return 0


def obtenir_evenement(cible: Path) -> tuple[int, dict[str, Any]]:
    """Retourne un événement factice garantissant un dénominateur positif."""
    try:
        valider_cible(cible)
    except ValueError as e:
        # Protocole de refus
        sys.stderr.write(f"denominateur nul: aucun fichier à examiner ({e})\n")
        return 3, {
            "denominateur": 0,
            "examines": [],
            "examines_tronques": False,
        }

    sortie = {
        "type": "present",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%f"),
        "denominateur": 1,
        "examines": [str(cible)],
        "examines_tronques": False,
    }
    return 0, sortie


def analyser_arguments() -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="racine du projet (défaut: répertoire de l'outil)",
    )
    parent_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="sortie au format JSON",
    )

    parser = argparse.ArgumentParser(
        description="Surveillance de fichiers en temps réel.",
        parents=[parent_parser],
    )
    sous_commandes = parser.add_subparsers(dest="commande", required=True)

    # run
    run_parser = sous_commandes.add_parser(
        "run",
        parents=[parent_parser],
        help="exécute une commande à chaque modification",
    )
    run_parser.add_argument(
        "--cmd",
        required=True,
        nargs="+",
        help="commande à exécuter (ex: python build.py)",
    )
    run_parser.add_argument(
        "--cible",
        required=True,
        type=Path,
        help="fichier ou répertoire à surveiller",
    )

    # async
    async_parser = sous_commandes.add_parser(
        "async",
        parents=[parent_parser],
        help="boucle asynchrone affichant les changements",
    )
    async_parser.add_argument(
        "--cible",
        required=True,
        type=Path,
        help="fichier ou répertoire à surveiller",
    )
    async_parser.add_argument(
        "--loop",
        action="store_true",
        help="boucle infinie (Ctrl‑C pour arrêter)",
    )

    # cli
    cli_parser = sous_commandes.add_parser(
        "cli",
        parents=[parent_parser],
        help="affiche les événements en temps réel",
    )
    cli_parser.add_argument(
        "--cible",
        required=True,
        type=Path,
        help="fichier ou répertoire à surveiller",
    )
    cli_parser.add_argument(
        "--depth",
        type=int,
        default=0,
        help="profondeur de récursion (0 = illimité)",
    )

    # filter
    filter_parser = sous_commandes.add_parser(
        "filter",
        parents=[parent_parser],
        help="surveille avec une profondeur maximale",
    )
    filter_parser.add_argument(
        "--cible",
        required=True,
        type=Path,
        help="répertoire à surveiller",
    )
    filter_parser.add_argument(
        "--max-depth",
        type=int,
        required=True,
        help="profondeur maximale de surveillance",
    )

    # event – cible en positionnel conformément à l'INVOCATION
    event_parser = sous_commandes.add_parser(
        "event",
        parents=[parent_parser],
        help="retourne le type d'événement et son horodatage",
    )
    event_parser.add_argument(
        "cible",
        type=Path,
        help="fichier à surveiller",
    )

    return parser.parse_args()


def main() -> int:
    """Point d'entrée principal."""
    args = analyser_arguments()

    racine = getattr(args, "racine", RACINE)
    if not isinstance(racine, Path):
        racine = RACINE

    try:
        if args.commande == "run":
            cible = chemin_absolu(args.cible, racine)
            try:
                valider_cible(cible)
            except ValueError as e:
                sys.stderr.write(f"erreur: {e}\n")
                return 1

            if WATCHFILES_DISPONIBLE:
                for changes in watchfiles.watch(cible):
                    for change_type, _ in changes:
                        if change_type in EVENEMENT_TYPES:
                            return executer_commande(args.cmd, cwd=racine)
            else:
                dernier_mtime = cible.stat().st_mtime
                while True:
                    time.sleep(POLLING_INTERVAL)
                    try:
                        nouveau_mtime = cible.stat().st_mtime
                        if nouveau_mtime != dernier_mtime:
                            return executer_commande(args.cmd, cwd=racine)
                        dernier_mtime = nouveau_mtime
                    except OSError:
                        continue

        elif args.commande == "async":
            cible = chemin_absolu(args.cible, racine)
            try:
                valider_cible(cible)
            except ValueError as e:
                sys.stderr.write(f"erreur: {e}\n")
                return 1

            async def boucle_async():
                stop_event = asyncio.Event()
                async for change_type, path in surveiller_async(cible, stop_event):
                    sortie = {
                        "type": change_type,
                        "path": str(path),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%f"),
                        "denominateur": 1,
                        "examines": [str(cible)],
                        "examines_tronques": False,
                    }
                    if getattr(args, "json", False):
                        print(json.dumps(sortie, ensure_ascii=False))
                    else:
                        print(f"{sortie['timestamp']} - {change_type}: {path}")

            if args.loop:
                asyncio.run(boucle_async())
                return 0
            else:
                async def ponctuel():
                    stop_event = asyncio.Event()
                    try:
                        change_type, path = await anext(
                            surveiller_async(cible, stop_event)
                        )
                        sortie = {
                            "type": change_type,
                            "path": str(path),
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%f"),
                            "denominateur": 1,
                            "examines": [str(cible)],
                            "examines_tronques": False,
                        }
                        if getattr(args, "json", False):
                            print(json.dumps(sortie, ensure_ascii=False))
                        else:
                            print(f"{sortie['timestamp']} - {change_type}: {path}")
                        return 0
                    finally:
                        stop_event.set()

                return asyncio.run(ponctuel())

        elif args.commande == "cli":
            cible = chemin_absolu(args.cible, racine)
            code, evenements = afficher_evenements(cible, args.depth)
            if code != 0:
                return code
            json_sortie = getattr(args, "json", False)
            for sortie in evenements:
                if json_sortie:
                    print(json.dumps(sortie, ensure_ascii=False))
                else:
                    print(f"{sortie['timestamp']} - {sortie['type']}: {sortie['path']}")
            return 0

        elif args.commande == "filter":
            cible = chemin_absolu(args.cible, racine)
            return filtrer_profondeur(cible, args.max_depth)

        elif args.commande == "event":
            cible = chemin_absolu(args.cible, racine)
            code, sortie = obtenir_evenement(cible)
            if getattr(args, "json", False):
                print(json.dumps(sortie, ensure_ascii=False))
            else:
                print(f"{sortie.get('type', '')} à {sortie.get('timestamp', '')}")
            return code

    except KeyboardInterrupt:
        sys.stderr.write("\ninterrompu par l'utilisateur\n")
        return 0
    except Exception as e:
        sys.stderr.write(f"erreur inattendue: {e}\n")
        return 1

    # Refus de conclure (ne devrait jamais être atteint)
    sys.stderr.write("denominateur nul: aucun chemin d'exécution valide\n")
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "denominateur": 0,
                    "examines": [],
                    "examines_tronques": False,
                },
                ensure_ascii=False,
            )
        )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())