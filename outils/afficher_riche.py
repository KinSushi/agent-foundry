"""afficher_riche.py

QUESTION:
Comment présenter ce résultat de manière lisible ?

MESURE:
Nombre de lignes d'entrée examinées et celles dépassant une largeur de 80 caractères.

HYPOTHESES:
L'entrée est un texte lisible constitué de lignes séparées par des sauts de ligne.

LIMITES:
Le largeur de référence est fixée à 80 caractères ; les listes d'examen sont limitées à 200 éléments.

CONTRE-EXEMPLES:
Une entrée contenant uniquement des lignes de moins de 80 caractères ne déclenche aucun défaut.

DOMAINE:
Traitement de flux texte provenant de fichiers ou de l'entrée standard.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import textwrap

try:
    from rich import print as rprint  # type: ignore
except Exception:  # pragma: no cover
    rprint = None  # rich non disponible

__all__ = (
    "lire_entree",
    "analyser_lignes",
    "formater_lignes",
    "main",
)

_LARGEUR_REF = 80
_EXAMENS_MAX = 200
_RACINE_DEFAUT = Path(__file__).resolve().parent


def lire_entree(chemin: Path | None) -> list[str]:
    """
    Lit les lignes d'entrée depuis ``chemin`` (fichier) ou depuis stdin si ``chemin`` est None.
    Retourne la liste des lignes sans les caractères de nouvelle ligne.
    En cas d'erreur d'accès ou de fichier .py illisible, écrit un message sur stderr
    et lève SystemExit(2).
    """
    if chemin is None:
        data = sys.stdin.read()
    else:
        try:
            data = chemin.read_text(encoding="utf-8", errors="replace")
            # Détecter un fichier .py contenant des octets non décodables
            if chemin.suffix == ".py" and "\ufffd" in data:
                raise UnicodeDecodeError(
                    "utf-8", b"", 0, 1, "octet illisible dans un fichier .py"
                )
        except (OSError, UnicodeDecodeError) as exc:  # pragma: no cover
            print(
                f"Impossible de lire le fichier '{chemin}': {exc}",
                file=sys.stderr,
            )
            raise SystemExit(2) from None
    return data.splitlines()


def analyser_lignes(lignes: list[str]) -> tuple[int, list[int], bool]:
    """
    Analyse les lignes et retourne :
    - denominateur : nombre total de lignes examinées
    - examines : liste des numéros de ligne (1-indexés) où la longueur dépasse _LARGEUR_REF
    - examines_tronques : True si la liste a été tronquée à _EXAMENS_MAX éléments
    """
    total = len(lignes)
    longues: list[int] = []
    for idx, ligne in enumerate(lignes, start=1):
        if len(ligne) > _LARGEUR_REF:
            longues.append(idx)
            if len(longues) >= _EXAMENS_MAX:
                break
    tronques = len(longues) == _EXAMENS_MAX and any(
        len(l) > _LARGEUR_REF for l in lignes[_EXAMENS_MAX:]
    )
    return total, longues, tronques


def formater_lignes(lignes: list[str]) -> str:
    """
    Retourne un texte où chaque paragraphe est rempli à une largeur de _LARGEUR_REF
    en utilisant textwrap.fill. Cette fonction ne modifie pas le contenu sémantique,
    elle améliore seulement la lisibilité visuelle.
    """
    texte = "\n".join(lignes)
    return textwrap.fill(texte, width=_LARGEUR_REF)


def _build_contrat() -> dict[str, str]:
    """Extrait les six sections du docstring du module."""
    lignes = __doc__.splitlines() if __doc__ else []
    contrat: dict[str, str] = {}
    courant: str | None = None
    buffer: list[str] = []
    for ligne in lignes:
        stripped = ligne.strip()
        if stripped in {
            "QUESTION",
            "MESURE",
            "HYPOTHESES",
            "LIMITES",
            "CONTRE-EXEMPLES",
            "DOMAINE",
        }:
            if courant is not None:
                contrat[courant] = "\n".join(buffer).strip()
                buffer = []
            courant = stripped
        elif courant is not None:
            buffer.append(ligne)
    if courant is not None:
        contrat[courant] = "\n".join(buffer).strip()
    return contrat


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Affiche un texte de manière lisible, avec enrichissement éventuel via 'rich'.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="Exemple d'appel :\n"
        "  afficher_riche.py mon_fichier.txt\n"
        "  cat mon_fichier.txt | afficher_riche.py --json",
    )
    parser.add_argument(
        "entree",
        nargs="?",
        type=Path,
        default=None,
        help="Fichier à lire (stdin si omis)",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Répertoire racine à ajouter en tête de sys.path",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie unique en JSON sur stdout",
    )
    args = parser.parse_args()

    racine = args.racine if args.racine is not None else _RACINE_DEFAUT
    if racine is not None:
        if racine.is_dir():
            sys.path.insert(0, str(racine))
        else:
            print(
                f"Erreur : la racine spécifiée '{racine}' n'est pas un répertoire valide.",
                file=sys.stderr,
            )
            return 2

    try:
        lignes = lire_entree(args.entree)
    except SystemExit:
        return 2

    denominateur, examines_liste, examines_tronques = analyser_lignes(lignes)

    if denominateur == 0:
        print("Aucun élément examiné.", file=sys.stderr)
        return 3

    texte_formate = formater_lignes(lignes)

    if args.json:
        contrat = _build_contrat()
        payload = {
            "contrat": contrat,
            "denominateur": denominateur,
            "examines": [
                {"ligne": num, "longueur": len(lignes[num - 1])} for num in examines_liste
            ],
            "examines_tronques": examines_tronques,
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 1 if examines_liste else 0
    else:
        if rprint is None:
            print(
                "Module riche non disponible, affichage en mode dégradé.",
                file=sys.stderr,
            )
            print(texte_formate)
        else:
            rprint(texte_formate)
        return 1 if examines_liste else 0


if __name__ == "__main__":
    raise SystemExit(main())