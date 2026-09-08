"""ouvrir_partout.py
Outil générique d’accès à des sources de fichiers via *fsspec*.

Ce fichier implémente le **contrat de mesure** suivant :

QUESTION
    Puis‑je lire ce dépôt sans le poser sur le disque ?
MESURE
    fsspec.filesystem + glob + open, comparé à l’extraction.
HYPOTHÈSES
    Le protocole est installé ; l’accès distant est autorisé.
LIMITES
    Un chemin fsspec n’est pas un pathlib.Path ; les protocoles distants
    ajoutent une latence réseau NON mesurée ici ; certains protocoles ne
    fournissent ni taille ni date.
CONTRE-EXEMPLES
    `available_protocols()` liste 55 noms mais un protocole listé dont le
    paquet manque échoue à l’ouverture — « listé » ≠ « réel ».
DOMAINE
    Sources de fichiers accessibles par un protocole fsspec installé.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 2. Encodage en tête
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 1. Bibliothèque standard uniquement – fsspec en option
try:
    import fsspec  # type: ignore
except ImportError:  # pragma: no cover
    fsspec = None  # noqa: N816

RACINE_DEFAUT = Path(__file__).resolve().parent

_COMPRESSION_RATIO_MIN = 0.1


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if "--json" in sys.argv:
            json.dump({"erreur": message, "denominateur": 0}, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            sys.stderr.write(f"{self.prog}: erreur: {message}\n")
            self.print_usage(sys.stderr)
        sys.exit(2)


class Source:
    """Adaptateur minimal pour accéder à des fichiers via *fsspec*."""

    def __init__(self, uri: str, racine: Path = RACINE_DEFAUT, **fs_kwargs: Any) -> None:
        if not fsspec:
            raise RuntimeError("fsspec n'est pas disponible dans l'environnement.")
        if "://" not in uri:
            raise ValueError("L'URI doit contenir un protocole, ex. 'file://path'.")
        self._uri = uri
        self._proto, self._path = uri.split("://", 1)
        if self._proto == "file" and not posixpath.isabs(self._path):
            self._path = posixpath.join(str(racine), self._path)
        self._fs = fsspec.filesystem(self._proto, **fs_kwargs)

    @property
    def protocole(self) -> str:
        return self._proto

    def fichiers(self, motif: str = "*") -> List[str]:
        base_dir = posixpath.dirname(self._path)
        pattern = posixpath.join(base_dir, motif) if base_dir else motif
        try:
            return self._fs.glob(pattern)
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError, OSError) as exc:
            raise RuntimeError(f"Cible inexistante ou de mauvais type : {exc}")

    def lire(self, chemin: str) -> str:
        try:
            with self._fs.open(chemin, mode="r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"Fichier illisible ou binaire : {chemin} : {exc}")

    def infos(self, chemin: str) -> Dict[str, Any]:
        try:
            return self._fs.info(chemin)  # type: ignore[arg-type]
        except Exception:  # pragma: no cover
            return {}


def lister_protocoles() -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not fsspec:
        return result
    for proto in fsspec.available_protocols():
        try:
            fs = fsspec.filesystem(proto)
            fs.ls(".")
            try:
                fichiers = fs.ls(".")
                if fichiers:
                    premier = fichiers[0]
                    fs.cat(premier)
            except Exception as exc:
                raise RuntimeError(f"fs.cat échoue pour {proto}: {exc}")
            try:
                fsspec.open_files(f"{proto}://*")
            except Exception as exc:
                raise RuntimeError(f"fsspec.open_files échoue pour {proto}: {exc}")
            result[proto] = "REEL"
        except Exception:
            result[proto] = "CREUX"
    return result


def copier(source: Source, destination: Path) -> Tuple[Path, int, int]:
    destination.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    fichiers_copies = 0
    for chemin in source.fichiers("*"):
        if posixpath.isabs(chemin) or ".." in posixpath.normpath(chemin).split("/"):
            raise RuntimeError(f"Chemin dangereux détecté : {chemin}")
        if os.path.islink(chemin):
            raise RuntimeError(f"Lien symbolique détecté : {chemin}")

        info = source.infos(chemin)
        taille_non_comp = info.get("size") or len(source.lire(chemin).encode('utf-8'))
        taille_comp = info.get("compressed_size")
        if taille_comp is not None and taille_non_comp:
            ratio = taille_comp / taille_non_comp
            if ratio < _COMPRESSION_RATIO_MIN:
                raise RuntimeError(
                    f"Ratio de compression trop élevé pour {chemin} "
                    f"(compressé : {taille_comp}, non compressé : {taille_non_comp})"
                )

        data = source.lire(chemin)
        base_dir = posixpath.dirname(source._path)
        rel_path = posixpath.relpath(chemin, start=base_dir)  # type: ignore[attr-defined]
        if ".." in rel_path.split("/"):
            raise RuntimeError(f"Chemin relatif contenant '..' détecté : {rel_path}")
        target_path = destination / Path(*rel_path.split("/"))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("w", encoding="utf-8", errors="replace") as out_f:
            out_f.write(data)
        total_bytes += len(data.encode('utf-8'))
        fichiers_copies += 1
    return destination, total_bytes, fichiers_copies


def _construire_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        description="Accès universel aux fichiers via fsspec.",
        epilog="Exemple : python outils/ouvrir_partout.py fichiers 'file://.' --json",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE_DEFAUT,
        help="Chemin racine à utiliser à la place du répertoire du script.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique.",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    p_proto = sub.add_parser("protocoles", help="Lister les protocoles disponibles et leur état.")
    p_proto.add_argument("--json", action="store_true", help="Émettre la sortie au format JSON unique.")

    parser_fichiers = sub.add_parser("fichiers", help="Lister les fichiers d’une source.")
    parser_fichiers.add_argument("uri", help="URI source, ex. 'zip://pkg/*.py'.")
    parser_fichiers.add_argument("--motif", default="*.py", help="Motif de recherche (glob).")
    parser_fichiers.add_argument("--json", action="store_true", help="Émettre la sortie au format JSON unique.")

    parser_copier = sub.add_parser("copier", help="Copier les fichiers d’une source.")
    parser_copier.add_argument("uri", help="URI source.")
    parser_copier.add_argument("dest", type=Path, help="Répertoire destination (créé si absent).")
    parser_copier.add_argument("--json", action="store_true", help="Émettre la sortie au format JSON unique.")
    return parser


def _action_protocoles(args: argparse.Namespace) -> Tuple[int, Dict[str, Any]]:
    if not fsspec:
        print("fsspec n'est pas disponible dans l'environnement.", file=sys.stderr)
        return 1, {}
    data = {"protocoles": lister_protocoles()}
    return 0, data


def _action_fichiers(args: argparse.Namespace) -> Tuple[int, Dict[str, Any]]:
    try:
        src = Source(args.uri, racine=args.racine)
    except Exception as exc:
        print(f"Erreur lors de la création de la source : {exc}", file=sys.stderr)
        return 1, {}
    try:
        fichiers = src.fichiers(args.motif)
    except Exception as exc:
        print(f"Erreur lors de la recherche des fichiers : {exc}", file=sys.stderr)
        return 1, {}
    return 0, {"fichiers": fichiers, "protocole": src.protocole}


def _action_copier(args: argparse.Namespace) -> Tuple[int, Dict[str, Any]]:
    try:
        src = Source(args.uri, racine=args.racine)
    except Exception as exc:
        print(f"Erreur lors de la création de la source : {exc}", file=sys.stderr)
        return 1, {}
    try:
        dest_path, octets, nb_fichiers = copier(src, args.dest)
    except Exception as exc:
        print(f"Erreur pendant la copie : {exc}", file=sys.stderr)
        return 1, {}
    return 0, {
        "destination": str(dest_path),
        "octets_ecrits": octets,
        "fichiers_copies": nb_fichiers,
    }


def _calculer_denominateur(action: str, payload: Dict[str, Any]) -> int:
    if action == "protocoles":
        return len(payload.get("protocoles", {}))
    if action == "fichiers":
        return len(payload.get("fichiers", []))
    if action == "copier":
        return payload.get("fichiers_copies", 0)
    return 0


def main() -> int:
    parser = _construire_parser()
    args = parser.parse_args()

    if args.action == "protocoles":
        code, payload = _action_protocoles(args)
    elif args.action == "fichiers":
        code, payload = _action_fichiers(args)
    elif args.action == "copier":
        code, payload = _action_copier(args)
    else:  # pragma: no cover
        parser.error("Action inconnue.")

    denominateur = _calculer_denominateur(args.action, payload)

    if denominateur == 0:
        print("Dénominateur nul : aucun élément réellement examiné.", file=sys.stderr)
        if args.json:
            json.dump({"erreur": "Dénominateur nul", "denominateur": 0}, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        return 3

    sortie = {
        "resultat": payload,
        "contrat": {
            "QUESTION": "Puis‑je lire ce dépôt sans le poser sur le disque ?",
            "MESURE": "fsspec.filesystem + glob + open, comparé à l’extraction.",
            "HYPOTHÈSES": "Le protocole est installé ; l’accès distant est autorisé.",
            "LIMITES": "Un chemin fsspec n’est pas un pathlib.Path ; les protocoles distants "
                       "ajoutent une latence réseau NON mesurée ici ; certains protocoles "
                       "ne fournissent ni taille ni date.",
            "CONTRE-EXEMPLES": "available_protocols() liste 55 noms mais un protocole listé "
                               "dont le paquet manque échoue à l’ouverture — « listé » ≠ « réel ».",
            "DOMAINE": "Sources de fichiers accessibles par un protocole fsspec installé.",
        },
        "denominateur": denominateur,
    }

    if args.json:
        json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        if args.action == "protocoles":
            for proto, statut in sortie["resultat"]["protocoles"].items():
                print(f"{proto}: {statut}")
        elif args.action == "fichiers":
            print("Fichiers trouvés :")
            for f in sortie["resultat"]["fichiers"]:
                print(f" - {f}")
            print(f"Protocole : {sortie['resultat'].get('protocole')}")
        elif args.action == "copier":
            print(f"Copie terminée dans : {sortie['resultat']['destination']}")
            print(f"Octets écrits : {sortie['resultat']['octets_ecrits']}")
            print(f"Fichiers copiés : {sortie['resultat']['fichiers_copies']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())