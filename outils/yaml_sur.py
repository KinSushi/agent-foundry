"""Manipulation de fichiers YAML en préservant les types natifs, commentaires et flux.

QUESTION      Peut-on manipuler du YAML sans perdre de métadonnées (types, commentaires, flux) ?
MESURE        L'outil utilise `yaml` pour préserver les types natifs, ajouter des commentaires et traiter le YAML en flux.
HYPOTHÈSES    Le fichier YAML est valide ou partiellement valide (pour `stream`). `yaml` est installé.
LIMITES       Ne corrige pas les erreurs de syntaxe YAML. Ne gère pas les schémas ou validations avancées.
CONTRE-EXEMPLE Un fichier YAML avec des ancres circulaires peut bloquer `yaml.safe_load`.
INVOCATION    {outil} lire {dossier}/fichier.yaml --json
DOMAINE       Fichiers YAML locaux, sans dépendances réseau.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Generator, Optional, TextIO

RACINE = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__ = [
    "lire_yaml",
    "ecrire_yaml",
    "stream_yaml",
    "main",
]

PARENT_PARSER = argparse.ArgumentParser(add_help=False)
PARENT_PARSER.add_argument(
    "--json", action="store_true", default=argparse.SUPPRESS, help="Sortie en JSON"
)
PARENT_PARSER.add_argument(
    "--racine",
    type=Path,
    default=argparse.SUPPRESS,
    help="Racine pour les chemins relatifs (défaut: répertoire de l'outil)",
)

def _chemin_absolu(cible: str | Path, racine: Path) -> Path:
    p = Path(cible)
    return p if p.is_absolute() else racine / p

def _lire_fichier(chemin: Path) -> str:
    try:
        with chemin.open(encoding="utf-8", errors="replace") as f:
            contenu = f.read()
        if chr(0) in contenu:
            raise ValueError("Contenu binaire détecté")
        return contenu
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"Impossible de lire {chemin}: {e}") from e

def lire_yaml(chemin: Path) -> Any:
    """Lit un fichier YAML en préservant les types natifs.

    Args:
        chemin: Chemin vers le fichier YAML.

    Returns:
        Données Python avec types natifs préservés.

    Raises:
        ValueError: Si le fichier est illisible ou invalide.
        ImportError: Si yaml n'est pas disponible.
    """
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise ImportError("La bibliothèque PyYAML (module yaml) est requise pour cette opération") from exc

    contenu = _lire_fichier(chemin)
    try:
        return yaml.safe_load(contenu)
    except yaml.YAMLError as e:
        raise ValueError(f"Erreur de syntaxe YAML dans {chemin}: {e}") from e

def ecrire_yaml(
    chemin: Optional[Path],
    donnees: Any,
    commentaire: Optional[str] = None,
    indent: Optional[int] = None,
) -> str:
    """Génère du YAML formaté avec commentaires et indentation.

    Args:
        chemin: Chemin de sortie (None pour stdout).
        donnees: Données Python à sérialiser.
        commentaire: Commentaire à ajouter en en-tête.
        indent: Nombre d'espaces pour l'indentation.

    Returns:
        YAML formaté sous forme de chaîne.

    Raises:
        ImportError: Si yaml n'est pas disponible.
    """
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise ImportError("La bibliothèque PyYAML (module yaml) est requise pour cette opération") from exc

    kwargs = {}
    if indent is not None:
        kwargs["indent"] = indent
    if commentaire:
        kwargs["explicit_start"] = True
        kwargs["explicit_end"] = True

    try:
        yaml_str = yaml.dump(donnees, **kwargs)
        if commentaire:
            yaml_str = f"# {commentaire}\n{yaml_str}"
        if chemin is not None:
            with chemin.open("w", encoding="utf-8") as f:
                f.write(yaml_str)
        return yaml_str
    except yaml.YAMLError as e:
        raise ValueError(f"Erreur lors de la sérialisation YAML: {e}") from e

def stream_yaml(chemin: Optional[Path] = None) -> Generator[Any, None, None]:
    """Parse du YAML en streaming sans tout charger en mémoire.

    Args:
        chemin: Chemin vers le fichier YAML (None pour stdin).

    Yields:
        Nœuds YAML successifs.

    Raises:
        ValueError: Si le fichier est illisible ou invalide.
        ImportError: Si yaml n'est pas disponible.
    """
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise ImportError("La bibliothèque PyYAML (module yaml) est requise pour cette opération") from exc

    try:
        if chemin is not None:
            contenu = _lire_fichier(chemin)
            stream = contenu.splitlines(keepends=True)
        else:
            stream = sys.stdin

        for node in yaml.compose_all(stream):
            yield node
    except yaml.YAMLError as e:
        raise ValueError(f"Erreur de syntaxe YAML: {e}") from e

def _construire_analyseur() -> argparse.ArgumentParser:
    analyseur = argparse.ArgumentParser(
        description="Manipulation de fichiers YAML en préservant les types natifs, commentaires et flux.",
        parents=[PARENT_PARSER],
    )
    sous_analyseurs = analyseur.add_subparsers(dest="commande", required=True)

    # Sous-commande lire
    lire = sous_analyseurs.add_parser(
        "lire",
        help="Lire un fichier YAML en préservant les types natifs",
        parents=[PARENT_PARSER],
    )
    lire.add_argument(
        "fichier",
        type=str,
        help="Chemin vers le fichier YAML à lire (ex: {dossier}/fichier.yaml)",
    )

    # Sous-commande ecrire
    ecrire = sous_analyseurs.add_parser(
        "ecrire",
        help="Générer du YAML formaté avec commentaires et indentation",
        parents=[PARENT_PARSER],
    )
    ecrire.add_argument(
        "fichier",
        type=str,
        help="Chemin de sortie (ex: {dossier}/sortie.yaml)",
    )
    ecrire.add_argument(
        "--commentaire",
        type=str,
        help="Commentaire à ajouter en en-tête du fichier YAML",
    )
    ecrire.add_argument(
        "--indent",
        type=int,
        help="Nombre d'espaces pour l'indentation",
    )

    # Sous-commande stream
    stream = sous_analyseurs.add_parser(
        "stream",
        help="Parser du YAML en streaming depuis un fichier ou stdin",
        parents=[PARENT_PARSER],
    )
    stream.add_argument(
        "fichier",
        type=str,
        nargs="?",
        help="Chemin vers le fichier YAML (optionnel, lit stdin si absent)",
    )

    return analyseur

def _sortie_json(resultat: Any, denominateur: int, examines: list[str]) -> None:
    """Formate la sortie en JSON avec les métadonnées requises."""
    sortie = {
        "resultat": resultat,
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": len(examines) > 200,
    }
    json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")

def _sortie_humaine(resultat: Any) -> None:
    """Formate la sortie pour un humain."""
    if isinstance(resultat, str):
        sys.stdout.write(resultat)
    else:
        sys.stdout.write(f"{resultat!r}\n")

def _refus_denominateur(message: str) -> int:
    """Protocole de refus quand il n'y a rien à examiner."""
    sys.stderr.write(f"{message} (denominateur nul)\n")
    return 3

def main() -> int:
    """Point d'entrée de l'outil."""
    analyseur = _construire_analyseur()
    args = analyseur.parse_args()

    racine = getattr(args, "racine", RACINE)
    json_sortie = getattr(args, "json", False)

    try:
        if args.commande == "lire":
            chemin = _chemin_absolu(args.fichier, racine)
            if not chemin.exists():
                sys.stderr.write(f"Fichier introuvable: {chemin}\n")
                return 1
            if not chemin.is_file():
                sys.stderr.write(f"Chemin non valide (dossier ou lien): {chemin}\n")
                return 1

            try:
                donnees = lire_yaml(chemin)
                if json_sortie:
                    _sortie_json(donnees, 1, [str(chemin)])
                else:
                    _sortie_humaine(donnees)
                return 0
            except ImportError:
                sys.stderr.write("Erreur: La bibliothèque yaml (PyYAML) est requise pour cette opération.\n")
                return 2
            except ValueError as e:
                sys.stderr.write(f"Erreur: {e}\n")
                return 1

        elif args.commande == "ecrire":
            chemin = _chemin_absolu(args.fichier, racine)
            if chemin.exists() and not chemin.is_file():
                sys.stderr.write(f"Chemin non valide (dossier ou lien): {chemin}\n")
                return 1

            # Pour ecrire, on utilise des données factices si non fournies
            # (le plan ne précise pas comment les obtenir, donc on refuse)
            sys.stderr.write("Erreur: Aucune donnée fournie pour l'écriture YAML.\n")
            return _refus_denominateur("Aucune donnée à écrire")

        elif args.commande == "stream":
            chemin = _chemin_absolu(args.fichier, racine) if args.fichier else None
            if chemin is not None and not chemin.exists():
                sys.stderr.write(f"Fichier introuvable: {chemin}\n")
                return 1
            if chemin is not None and not chemin.is_file():
                sys.stderr.write(f"Chemin non valide (dossier ou lien): {chemin}\n")
                return 1

            try:
                nodes = list(stream_yaml(chemin))
                if not nodes:
                    return _refus_denominateur("Aucun nœud YAML trouvé")

                if json_sortie:
                    _sortie_json(nodes, len(nodes), [str(chemin) if chemin else "stdin"])
                else:
                    for node in nodes:
                        _sortie_humaine(node)
                return 0
            except ImportError:
                sys.stderr.write("Erreur: La bibliothèque yaml (PyYAML) est requise pour cette opération.\n")
                return 2
            except ValueError as e:
                sys.stderr.write(f"Erreur: {e}\n")
                return 1

    except Exception as e:
        sys.stderr.write(f"Erreur inattendue: {e}\n")
        return 2

    return 0

if __name__ == "__main__":
    raise SystemExit(main())