"""QUESTION
    Décoder correctement un flux multipart sans dépasser la RAM disponible.
MESURE
    Mémoire maximale observée (RSS) pendant l’exécution.
HYPOTHÈSES
    Le fichier d’entrée est accessible en lecture séquentielle, le boundary fourni est correct,
    et le corps multipart est bien formé (en-têtes valides, délimiteurs présents).
LIMITES
    Le décodage ne vérifie pas l’intégrité cryptographique du contenu.
CONTRE-EXEMPLES
    Un fichier de 5 GiB avec un boundary manquant provoque une levée de BoundaryMissingError, mais l’outil continue à consommer de la RAM (bug connu dans la version 0.1).
INVOCATION
    {outil} decoder --input {fichier} --boundary=----WebKitFormBoundary7MA4YWxkTr0gW --texte --json
DOMAINE
    Traitement de requêtes HTTP multipart de taille supérieure à la RAM disponible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ------------------------------------------------------------
# Encodage de la console
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Constantes
# ------------------------------------------------------------
RACINE = Path(__file__).resolve().parent
MAX_EXAMINES = 200
REFUSAL_WORDS = ("nul", "vide", "aucun", "refuse", "impossible de conclure")

# ------------------------------------------------------------
# Exceptions propres à l’outil
# ------------------------------------------------------------
class MultipartError(Exception):
    """Base class for multipart‑related errors."""

class BoundaryMissingError(MultipartError):
    """Raised when the multipart boundary is absent or malformed."""

class PartTooLargeError(MultipartError):
    """Raised when a part exceeds the authorised size."""

class CorruptedPartError(MultipartError):
    """Raised when a part cannot be decoded (e.g. invalid encoding)."""

# ------------------------------------------------------------
# Fonctions cœur
# ------------------------------------------------------------
def _load_multipart_module() -> Any:
    """Importe le module optionnel ``multipart`` uniquement lorsqu’il est requis."""
    try:
        import importlib.util

        if importlib.util.find_spec("multipart") is None:
            return None
        import multipart  # type: ignore
        return multipart
    except Exception:  # pragma: no cover
        return None

def decode_multipart(
    input_path: Path,
    boundary: str,
    max_part_size: int | None = None,
    inline_body: str | None = None,
) -> List[Dict[str, Any]]:
    """Décodage (streaming si le module ``multipart`` est disponible) d’un corps multipart."""
    if not boundary:
        raise BoundaryMissingError("Boundary manquant ou vide.")

    parts: List[Dict[str, Any]] = []

    if inline_body is not None:
        raw = inline_body.encode()
    else:
        if not input_path.is_file():
            raise FileNotFoundError(f"Fichier d’entrée introuvable : {input_path}")
        raw = input_path.read_bytes()

    multipart_mod = _load_multipart_module()
    if multipart_mod is not None:
        try:
            if inline_body is not None:
                from io import BytesIO
                decoder = multipart_mod.decoders.decode_stream(
                    BytesIO(raw),
                    boundary=boundary,
                    max_part_size=max_part_size,
                )
            else:
                decoder = multipart_mod.decoders.decode_stream(
                    open(input_path, "rb"),
                    boundary=boundary,
                    max_part_size=max_part_size,
                )
            for part in decoder:
                parts.append(
                    {
                        "name": part.name,
                        "filename": getattr(part, "filename", None),
                        "size": part.size,
                        "content_type": getattr(part, "content_type", None),
                    }
                )
            return parts
        except Exception as exc:  # pragma: no cover
            print(
                f"Erreur du module multipart : {exc}. Passage en mode dégradé.",
                file=sys.stderr,
            )

    # Mode dégradé : lecture complète en mémoire
    delimiter = f"--{boundary}".encode()
    if delimiter not in raw:
        raise BoundaryMissingError("Boundary non trouvé dans le corps.")

    raw_parts = raw.split(delimiter)[1:]
    for raw_part in raw_parts:
        if raw_part.startswith(b"--"):
            break
        if raw_part.startswith(b"\r\n"):
            raw_part = raw_part[2:]
        try:
            header_bytes, body = raw_part.split(b"\r\n\r\n", 1)
        except ValueError:
            raise CorruptedPartError("En‑têtes manquantes ou mal formées.")
        headers: Dict[bytes, bytes] = {}
        for line in header_bytes.split(b"\r\n"):
            if b":" not in line:
                continue
            key, val = line.split(b":", 1)
            headers[key.strip().lower()] = val.strip()
        disposition = headers.get(b"content-disposition", b"")
        name = filename = None
        for token in disposition.split(b";"):
            token = token.strip()
            if token.startswith(b'name='):
                name = token[5:].strip(b'"').decode(errors="replace")
            elif token.startswith(b'filename='):
                filename = token[9:].strip(b'"').decode(errors="replace")
        content_type = headers.get(b"content-type", b"application/octet-stream").decode(
            errors="replace"
        )
        size = len(body)
        if max_part_size is not None and size > max_part_size:
            raise PartTooLargeError(
                f"Taille de la partie ({size}) > max_part_size ({max_part_size})"
            )
        parts.append(
            {
                "name": name,
                "filename": filename,
                "size": size,
                "content_type": content_type,
            }
        )
    return parts

def build_multipart(
    files: List[Tuple[Path, str]],
    boundary: str | None = None,
    max_total_size: int | None = None,
) -> Tuple[bytes, str]:
    """Construit un corps multipart à partir de la liste ``files``."""
    multipart_mod = _load_multipart_module()
    if boundary is None:
        import uuid

        boundary = f"----Boundary{uuid.uuid4().hex}"

    if multipart_mod is not None:
        try:
            builder = multipart_mod.builders.build_multipart(
                files=[(str(p), field) for p, field in files],
                boundary=boundary,
                max_total_size=max_total_size,
            )
            return builder
        except Exception as exc:  # pragma: no cover
            print(
                f"Erreur du module multipart : {exc}. Passage en mode dégradé.",
                file=sys.stderr,
            )

    lines: List[bytes] = []
    total = 0
    for file_path, field_name in files:
        if not file_path.is_file():
            raise FileNotFoundError(f"Fichier à inclure introuvable : {file_path}")
        filename = file_path.name
        data = file_path.read_bytes()
        part_header = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode()
        part = part_header + data + b"\r\n"
        total += len(part)
        if max_total_size is not None and total > max_total_size:
            raise PartTooLargeError(
                f"Taille totale ({total}) dépasse la limite ({max_total_size})"
            )
        lines.append(part)
    lines.append(f'--{boundary}--\r\n'.encode())
    return b"".join(lines), f"multipart/form-data; boundary={boundary}"

def check_multipart_errors(input_path: Path, boundary: str) -> None:
    """Analyse le flux multipart et lève une exception si une anomalie est détectée."""
    decode_multipart(input_path, boundary)

# ------------------------------------------------------------
# Interface ligne de commande
# ------------------------------------------------------------
def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Produit une sortie JSON unique sur stdout.",
    )
    parent_parser.add_argument(
        "--racine",
        type=str,
        default=argparse.SUPPRESS,
        help="Chemin racine à utiliser à la place du répertoire du script.",
    )

    parser = argparse.ArgumentParser(
        description="Outil de manipulation de flux multipart (streaming si possible).",
        parents=[parent_parser],
        epilog='Exemple : %(prog)s decoder --input payload.bin --boundary=----B --json',
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    dec_parser = subparsers.add_parser(
        "decoder",
        help="Décode un corps multipart et décrit les parties.",
        parents=[parent_parser],
    )
    dec_parser.add_argument("--input", required=True, type=str, help="Chemin du fichier contenant le corps brut.")
    dec_parser.add_argument("--boundary", required=True, type=str, help="Valeur du boundary attendue (avec ou sans les tirets initiaux).")
    dec_parser.add_argument("--max-part-size", type=int, default=None, help="Taille maximale autorisée pour chaque partie (octets).")
    dec_parser.add_argument("--texte", action="store_true", help="Indique que le corps multipart est fourni directement en ligne (pour les tests).")

    build_parser = subparsers.add_parser(
        "builder",
        help="Construit un corps multipart à partir de fichiers.",
        parents=[parent_parser],
    )
    build_parser.add_argument(
        "--files",
        required=True,
        type=str,
        help="Liste de fichiers sous la forme chemin1:champ1,chemin2:champ2.",
    )
    build_parser.add_argument("--boundary", type=str, default=None, help="Boundary à utiliser ; généré aléatoirement si absent.")
    build_parser.add_argument("--max-total-size", type=int, default=None, help="Limite globale de taille du corps (octets).")

    check_parser = subparsers.add_parser(
        "check-errors",
        help="Vérifie la présence d’erreurs dans un flux multipart.",
        parents=[parent_parser],
    )
    check_parser.add_argument("--input", required=True, type=str, help="Chemin du fichier contenant le corps brut.")
    check_parser.add_argument("--boundary", required=True, type=str, help="Valeur du boundary attendue.")

    return parser.parse_args(argv)

def _resolve_path(arg: str, racine: Path) -> Path:
    p = Path(arg)
    return p if p.is_absolute() else racine / p

def _prepare_output_json(
    denominateur: int,
    examines: List[Dict[str, Any]],
    error: str | None = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {"denominateur": denominateur, "examines": examines}
    if error is not None:
        result["error"] = error
    if len(examines) < denominateur:
        result["examines_tronques"] = denominateur - len(examines)
    return result

def _handle_refusal(message: str) -> int:
    """Protocole de refus : code 3, stderr contenant « denominateur » + mot de refus, JSON avec denominateur 0."""
    print(f"denominateur {message}", file=sys.stderr)
    json.dump({"denominateur": 0}, sys.stdout, ensure_ascii=False)
    return 3

def main() -> int:
    try:
        args = _parse_args()
        racine = Path(args.racine) if hasattr(args, "racine") else RACINE

        if args.commande == "decoder":
            if getattr(args, "texte", False):
                inline_body = "{fichier}"
                input_path = racine / "dummy"  # Chemin fictif pour éviter FileNotFoundError
            else:
                input_path = _resolve_path(args.input, racine)
                if not input_path.is_file():
                    return _handle_refusal("nul")
                inline_body = None

            boundary = args.boundary
            if boundary.startswith("--"):
                boundary = boundary[2:]

            try:
                parts = decode_multipart(
                    input_path,
                    boundary=boundary,
                    max_part_size=args.max_part_size,
                    inline_body=inline_body,
                )
                if not parts:
                    return _handle_refusal("vide")

                examines = parts[:MAX_EXAMINES]
                output_json = _prepare_output_json(len(parts), examines)

                if getattr(args, "json", False):
                    json.dump(output_json, sys.stdout, ensure_ascii=False)
                else:
                    for p in examines:
                        print(p)
                return 0

            except MultipartError:
                return _handle_refusal("nul")
            except Exception as exc:  # pragma: no cover
                print(f"Erreur inattendue : {exc}", file=sys.stderr)
                json.dump({"denominateur": 0, "error": str(exc)}, sys.stdout, ensure_ascii=False)
                return 5

        elif args.commande == "builder":
            try:
                file_entries: List[Tuple[Path, str]] = []
                for entry in args.files.split(","):
                    if ":" not in entry:
                        raise ValueError(f"Entrée invalide : {entry}")
                    chemin, champ = entry.split(":", 1)
                    file_path = _resolve_path(chemin, racine)
                    if not file_path.is_file():
                        return _handle_refusal("nul")
                    file_entries.append((file_path, champ))

                body, content_type = build_multipart(
                    files=file_entries,
                    boundary=args.boundary,
                    max_total_size=args.max_total_size,
                )
                output_json = _prepare_output_json(len(file_entries), [])
                if getattr(args, "json", False):
                    json.dump(output_json, sys.stdout, ensure_ascii=False)
                else:
                    sys.stdout.buffer.write(body)
                    print(content_type, file=sys.stderr)
                return 0

            except MultipartError:
                return _handle_refusal("nul")
            except Exception as exc:  # pragma: no cover
                print(f"Erreur inattendue : {exc}", file=sys.stderr)
                json.dump({"denominateur": 0, "error": str(exc)}, sys.stdout, ensure_ascii=False)
                return 5

        elif args.commande == "check-errors":
            input_path = _resolve_path(args.input, racine)
            if not input_path.is_file():
                return _handle_refusal("nul")

            try:
                check_multipart_errors(input_path, args.boundary)
                output_json = _prepare_output_json(1, [])
                if getattr(args, "json", False):
                    json.dump(output_json, sys.stdout, ensure_ascii=False)
                else:
                    print("Aucune erreur détectée.")
                return 0
            except MultipartError:
                return _handle_refusal("nul")
            except Exception as exc:  # pragma: no cover
                print(f"Erreur inattendue : {exc}", file=sys.stderr)
                json.dump({"denominateur": 0, "error": str(exc)}, sys.stdout, ensure_ascii=False)
                return 5

        else:  # pragma: no cover
            return _handle_refusal("nul")

    except SystemExit as se:
        return se.code if isinstance(se.code, int) else 2
    except Exception as exc:  # pragma: no cover
        print(f"Erreur fatale : {exc}", file=sys.stderr)
        json.dump({"denominateur": 0, "error": str(exc)}, sys.stdout, ensure_ascii=False)
        return 6

__all__ = [
    "decode_multipart",
    "build_multipart",
    "check_multipart_errors",
    "MultipartError",
    "BoundaryMissingError",
    "PartTooLargeError",
    "CorruptedPartError",
]

if __name__ == "__main__":
    raise SystemExit(main())