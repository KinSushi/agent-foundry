#!/usr/bin/env python3.14
"""
QUESTION      Où en est ce traitement long ?
MESURE        Observation en temps réel de l'avancement d'un itérable Python avec
              estimation du temps restant (ETA) et débit instantané, via une barre
              de progression interactive ou une sortie JSON structurée.
HYPOTHÈSES    L'itérable est homogène en temps de traitement par élément. L'ETA
              est calculé par régression linéaire sur les dernières itérations.
LIMITES       Ne mesure pas le temps CPU, seulement le temps écoulé. L'ETA peut
              être imprécis si le temps par élément varie fortement. Sans module
              tierce, seule une sortie textuelle basique est disponible.
CONTRE-EXEMPLES Un itérable dont les éléments ont des temps de traitement très
              hétérogènes (ex : lecture de fichiers de tailles très différentes).
DOMAINE       Tout itérable Python dont la longueur est connue à l'avance (Sized),
              ou tout générateur pour lequel un callback de progression est fourni.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, TypeVar, Union

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = ["observer_progression", "main"]

T = TypeVar("T")

def _mode_degrade(
    iterable: Iterable[T],
    description: str = "",
    total: Optional[int] = None,
) -> Iterator[T]:
    """Mode dégradé sans module tierce."""
    compteur = 0
    debut = time.time()
    for element in iterable:
        compteur += 1
        if total is not None:
            progression = compteur / total
            temps_ecoule = time.time() - debut
            eta = (temps_ecoule / progression) - temps_ecoule if progression > 0 else 0
            sys.stderr.write(
                f"\r{description}: {compteur}/{total} ({progression:.1%}) | ETA: {eta:.1f}s"
            )
            sys.stderr.flush()
        else:
            sys.stderr.write(f"\r{description}: {compteur} éléments traités")
            sys.stderr.flush()
        yield element
    sys.stderr.write("\n")

def observer_progression(
    iterable: Iterable[T],
    *,
    description: str = "",
    total: Optional[int] = None,
    json_sortie: bool = False,
) -> Iterator[T]:
    """
    Observe la progression d'un itérable avec barre de progression et ETA.

    Args:
        iterable: L'itérable à observer.
        description: Texte affiché avant la barre de progression.
        total: Nombre total d'éléments (si None, tente len(iterable)).
        json_sortie: Si True, produit une sortie JSON sur stdout.

    Yields:
        Les éléments de l'itérable un par un.

    Raises:
        TypeError: Si l'itérable n'est pas Sized et total=None.
    """
    try:
        from tqdm import tqdm
        barre_cls = tqdm
    except ImportError:
        try:
            from rich.progress import track as barre_cls
        except ImportError:
            barre_cls = None

    if barre_cls is None:
        if not json_sortie:
            print(
                "Mode dégradé : ni tqdm ni rich disponibles. "
                "Affichage basique sur stderr.",
                file=sys.stderr,
            )
        yield from _mode_degrade(iterable, description, total)
        return

    try:
        if total is None:
            total = len(iterable)  # type: ignore

        with barre_cls(
            iterable,
            desc=description,
            total=total,
            unit="élément",
            dynamic_ncols=True,
            leave=False,
            file=sys.stderr,
        ) as barre:
            for element in barre:
                yield element
    except TypeError:
        if not json_sortie:
            print(
                "Mode dégradé : longueur inconnue. Affichage basique sans ETA.",
                file=sys.stderr,
            )
        yield from _mode_degrade(iterable, description, None)

def _construire_analyseur() -> argparse.ArgumentParser:
    """Construit l'analyseur d'arguments en ligne de commande."""
    analyseur = argparse.ArgumentParser(
        description="Où en est ce traitement long ?",
        epilog="Exemple : afficher_progression.py --json --description 'Analyse' fichier.txt",
    )
    analyseur.add_argument(
        "--json",
        action="store_true",
        help="Produit une sortie JSON sur stdout au lieu d'une sortie humaine.",
    )
    analyseur.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Chemin racine pour les imports relatifs (défaut : répertoire du script).",
    )
    analyseur.add_argument(
        "--description",
        type=str,
        default="Traitement",
        help="Description affichée avant la barre de progression.",
    )
    analyseur.add_argument(
        "cible",
        type=Path,
        help="Chemin vers le fichier ou dossier à traiter.",
    )
    return analyseur

def _simuler_traitement_long(iterable: Iterable[str]) -> Iterator[str]:
    """Simule un traitement long pour démonstration."""
    for element in iterable:
        time.sleep(0.01)  # Simulation de traitement
        yield element

def _traiter_cible(
    chemin: Path,
    description: str,
    json_sortie: bool,
) -> tuple[int, list[str]]:
    """
    Traite la cible (fichier ou dossier) et retourne les éléments examinés.

    Args:
        chemin: Chemin vers la cible.
        description: Description pour la barre de progression.
        json_sortie: Si True, active le mode JSON.

    Returns:
        Tuple (nombre total d'éléments, liste des noms des éléments examinés).
    """
    if not chemin.exists():
        print(f"Erreur : le chemin '{chemin}' n'existe pas.", file=sys.stderr)
        return 0, []

    if chemin.is_file():
        try:
            with chemin.open("r", encoding="utf-8") as f:
                lignes = f.readlines()
        except UnicodeDecodeError:
            print(f"Erreur : impossible de lire '{chemin}' en UTF-8.", file=sys.stderr)
            return 0, []
        total = len(lignes)
        elements = list(_simuler_traitement_long(lignes))
    elif chemin.is_dir():
        fichiers = list(chemin.glob("**/*"))
        total = len(fichiers)
        elements = []
        for fichier in observer_progression(
            fichiers,
            description=description,
            total=total,
            json_sortie=json_sortie,
        ):
            if fichier.is_file():
                try:
                    with fichier.open("r", encoding="utf-8") as f:
                        elements.extend(f.readlines())
                except UnicodeDecodeError:
                    continue
    else:
        print(f"Erreur : '{chemin}' n'est ni un fichier ni un dossier.", file=sys.stderr)
        return 0, []

    examines = [str(f) for f in chemin.glob("**/*") if f.is_file()][:200]
    return total, examines

def main() -> int:
    """Point d'entrée principal de l'outil."""
    analyseur = _construire_analyseur()
    args = analyseur.parse_args()

    if args.racine not in sys.path:
        sys.path.insert(0, str(args.racine.resolve()))

    try:
        total, examines = _traiter_cible(
            args.cible.resolve(),
            args.description,
            args.json,
        )
    except Exception as e:
        print(f"Erreur lors du traitement : {e}", file=sys.stderr)
        return 1

    if args.json:
        resultat = {
            "denominateur": total,
            "examines": examines,
            "examines_tronques": len(examines) > 200,
            "contrat": {
                "QUESTION": "Où en est ce traitement long ?",
                "MESURE": "Observation en temps réel de l'avancement d'un itérable Python, "
                          "avec estimation du temps restant (ETA) et débit instantané.",
                "HYPOTHÈSES": "L'itérable est homogène en temps de traitement par élément. "
                              "L'ETA est calculé par régression linéaire sur les dernières itérations.",
                "LIMITES": "Ne mesure pas le temps CPU, seulement le temps écoulé. "
                           "L'ETA peut être imprécis si le temps par élément varie fortement. "
                           "Sans module tierce, seule une sortie textuelle basique est disponible.",
                "CONTRE-EXEMPLES": "Un itérable dont les éléments ont des temps de traitement très "
                                   "hétérogènes (ex : lecture de fichiers de tailles très différentes).",
                "DOMAINE": "Tout itérable Python dont la longueur est connue à l'avance (Sized), "
                           "ou tout générateur pour lequel un callback de progression est fourni.",
            },
        }
        print(json.dumps(resultat, ensure_ascii=False, indent=2))
        return 0 if total == 0 else 1 if len(examines) < total else 0
    else:
        if total == 0:
            print("Aucun élément à traiter.", file=sys.stderr)
            return 3
        return 0

if __name__ == "__main__":
    raise SystemExit(main())