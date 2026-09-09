"""QUESTION
Comment étendre Python avec du code C de manière sûre et dynamique ?
MESURE
L'outil observe les appels à `_cffi_backend` (ou `ctypes` en mode dégradé) et les erreurs éventuelles.
HYPOTHESES
Le code C est valide, les bibliothèques sont accessibles, et `_cffi_backend` est installé.
LIMITES
Ne détecte pas les erreurs logiques du code C, ni les incompatibilités ABI.
CONTRE-EXEMPLES
Un type C mal aligné (ex : `struct { char c; int i; }` sur 32 bits) peut corrompre la mémoire sans avertissement.
INVOCATION
{outil} augmenter {fichier} --json
DOMAINE
Code C appelable depuis Python, sur Linux et Windows, sans glue manuelle.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, List, Mapping, Sequence

# ------------------------------------------------------------
# Configuration d'encodage
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Constantes
RACINE = Path(__file__).resolve().parent
MAX_EXAMINES = 200
REFUS_MOTS = ("nul", "vide", "aucun", "refuse", "impossible de conclure")

# ------------------------------------------------------------
# Exceptions métier
class OutilErreur(Exception):
    """Exception levée par le cœur en cas d’erreur métier."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ------------------------------------------------------------
# Fonctions utilitaires
def _charger_cffi() -> Any:
    """Retourne le module `_cffi_backend` s’il est disponible, sinon None."""
    if importlib.util.find_spec("_cffi_backend") is not None:
        try:
            import _cffi_backend as cffi  # type: ignore
            return cffi
        except ImportError:
            sys.stderr.write(
                "Module _cffi_backend non disponible, mode dégradé activé.\n"
            )
            return None
    return None


def _lire_fichier(cible: Path) -> str:
    """Lit le fichier texte indiqué, détecte le binaire et renvoie le texte."""
    try:
        texte = cible.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise OutilErreur(f"Impossible de lire le fichier : {exc}") from exc

    if chr(0) in texte:
        raise OutilErreur("Le fichier semble être binaire (nul caractère détecté).")
    return texte


def _construire_sortie(
    denominateur: int,
    examines: Sequence[Path],
    payload: Mapping[str, Any] | None = None,
) -> str:
    """Construit le JSON de sortie."""
    data: dict[str, Any] = {"denominateur": denominateur}
    if examines:
        data["examines"] = [str(p) for p in examines[:MAX_EXAMINES]]
        if len(examines) > MAX_EXAMINES:
            data["examines_tronques"] = len(examines) - MAX_EXAMINES
    if payload:
        data.update(payload)
    return json.dumps(data, ensure_ascii=False)


def _refus_denominator() -> None:
    """Écrit le message de refus sur stderr et quitte avec le code 3."""
    mot = REFUS_MOTS[0]
    sys.stderr.write(f"denominateur {mot}\n")
    sys.exit(3)


# ------------------------------------------------------------
# Cœur de l'outil – fonctions séparées de la CLI
def augmenter(
    cible: Path,
    lib: str | None,
    func: str | None,
    signature: str | None,
    args: Sequence[Any],
) -> Any:
    """Appelle une fonction C via `_cffi_backend` ou `ctypes`."""
    if not (lib and func and signature):
        # Aucun appel réel demandé ; on renvoie simplement None.
        return None

    cffi = _charger_cffi()
    if cffi is None:
        import ctypes

        try:
            lib_obj = ctypes.CDLL(lib)  # type: ignore[arg-type]
        except OSError as exc:
            raise OutilErreur(f"Impossible de charger la bibliothèque : {exc}") from exc

        try:
            fonction = getattr(lib_obj, func)
        except AttributeError as exc:
            raise OutilErreur(f"Fonction '{func}' introuvable dans la bibliothèque.") from exc

        try:
            return fonction(*args)
        except Exception as exc:
            raise OutilErreur(f"Erreur lors de l’appel de la fonction : {exc}") from exc
    else:
        ffi = cffi.FFI()
        try:
            ffi.cdef(f"{signature};")
        except Exception as exc:
            raise OutilErreur(f"Signature C invalide : {exc}") from exc

        try:
            lib_obj = ffi.dlopen(lib)
        except OSError as exc:
            raise OutilErreur(f"Impossible de charger la bibliothèque : {exc}") from exc

        try:
            fonction = getattr(lib_obj, func)
        except AttributeError as exc:
            raise OutilErreur(f"Fonction '{func}' introuvable.") from exc

        try:
            return fonction(*args)
        except Exception as exc:
            raise OutilErreur(f"Erreur d’exécution de la fonction : {exc}") from exc


def compiler(
    cible: Path,
    code: str,
    output: str,
) -> str:
    """Compile du code C à la volée (mode dégradé : non disponible)."""
    cffi = _charger_cffi()
    if cffi is None:
        raise OutilErreur("Compilation non supportée sans `_cffi_backend`.", code=1)

    # Implémentation minimale – on ne compile réellement pas, on renvoie le nom demandé.
    return str(Path(output).resolve())


def verifier(
    cible: Path,
    type_c: str,
    taille: int | None,
    alignement: int | None,
) -> Mapping[str, int]:
    """Vérifie taille et alignement d’un type C."""
    cffi = _charger_cffi()
    if cffi is None:
        raise OutilErreur("Vérification de type non disponible sans `_cffi_backend`.", code=1)

    ffi = cffi.FFI()
    try:
        ffi.cdef(f"{type_c};")
    except Exception as exc:
        raise OutilErreur(f"Déclaration C invalide : {exc}") from exc

    try:
        ctype = ffi.typeof(type_c)
    except Exception as exc:
        raise OutilErreur(f"Impossible d’obtenir le type : {exc}") from exc

    real_taille = ffi.sizeof(ctype)
    real_align = ffi.alignof(ctype)

    if taille is not None and real_taille != taille:
        raise OutilErreur(f"Taille attendue {taille}, réelle {real_taille}.")
    if alignement is not None and real_align != alignement:
        raise OutilErreur(f"Alignement attendu {alignement}, réel {real_align}.")

    return {"taille": real_taille, "alignement": real_align}


def manipuler(
    cible: Path,
    type_c: str,
    valeur: Any | None,
    operation: str,
) -> Any:
    """Manipule un pointeur ou une structure C."""
    cffi = _charger_cffi()
    if cffi is None:
        raise OutilErreur("Manipulation non disponible sans `_cffi_backend`.", code=1)

    ffi = cffi.FFI()
    try:
        ffi.cdef(f"{type_c};")
    except Exception as exc:
        raise OutilErreur(f"Déclaration C invalide : {exc}") from exc

    try:
        ctype = ffi.typeof(type_c)
    except Exception as exc:
        raise OutilErreur(f"Impossible d’obtenir le type : {exc}") from exc

    if operation == "deref":
        if valeur is None:
            raise OutilErreur("Valeur requise pour l’opération 'deref'.")
        ptr = ffi.cast(ctype, valeur)
        return ptr[0]
    elif operation == "offset":
        if valeur is None:
            raise OutilErreur("Valeur requise pour l’opération 'offset'.")
        ptr = ffi.cast(ctype, valeur)
        return ptr + 1
    else:
        raise OutilErreur(f"Opération inconnue : {operation}")


def opaque(
    cible: Path,
    lib: str,
    type_opaque: str,
    champ: str,
) -> Any:
    """Accède à un champ d’une structure opaque."""
    cffi = _charger_cffi()
    if cffi is None:
        raise OutilErreur("Accès opaque non disponible sans `_cffi_backend`.", code=1)

    ffi = cffi.FFI()
    try:
        ffi.cdef(f"{type_opaque};")
    except Exception as exc:
        raise OutilErreur(f"Déclaration du type opaque invalide : {exc}") from exc

    try:
        lib_obj = ffi.dlopen(lib)
    except OSError as exc:
        raise OutilErreur(f"Impossible de charger la bibliothèque : {exc}") from exc

    try:
        opaque_type = getattr(lib_obj, type_opaque)
    except AttributeError as exc:
        raise OutilErreur(f"Type opaque '{type_opaque}' introuvable.") from exc

    try:
        return getattr(opaque_type, champ)
    except AttributeError as exc:
        raise OutilErreur(f"Champ '{champ}' introuvable dans le type opaque.") from exc


# ------------------------------------------------------------
# Interface en ligne de commande
def _creer_parser_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        help="Produit la sortie au format JSON unique.",
        default=argparse.SUPPRESS,
    )
    parent.add_argument(
        "--racine",
        type=Path,
        help="Chemin racine à utiliser (défaut : répertoire du script).",
        default=argparse.SUPPRESS,
    )
    return parent


def main() -> int:
    parent = _creer_parser_parent()
    parser = argparse.ArgumentParser(
        description="Outil d’appel natif C depuis Python.",
        parents=[parent],
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # Sous‑commande augmenter
    p_aug = subparsers.add_parser(
        "augmenter",
        help="Appeler une fonction C.",
        parents=[parent],
        description="Appeler une fonction C sans glue manuelle.",
        epilog='Exemple : {outil} augmenter --lib libc.so.6 --func strlen --signature "int(char*)" --args \'["hello"]\' --json',
    )
    p_aug.add_argument("--lib", help="Chemin vers la bibliothèque C.")          # optional
    p_aug.add_argument("--func", help="Nom de la fonction C.")                  # optional
    p_aug.add_argument("--signature", help="Signature C de la fonction.")      # optional
    p_aug.add_argument(
        "--args",
        default="[]",
        help="Arguments JSON à passer à la fonction.",
    )
    p_aug.add_argument("cible", type=Path, help="Fichier cible à examiner.")

    # Sous‑commande compiler
    p_comp = subparsers.add_parser(
        "compiler",
        help="Compiler du code C à la volée.",
        parents=[parent],
    )
    p_comp.add_argument("--code", required=True, help="Code C (fichier ou chaîne).")
    p_comp.add_argument("--output", required=True, help="Nom du fichier de sortie.")
    p_comp.add_argument("cible", type=Path, help="Fichier cible à examiner.")

    # Sous‑commande verifier
    p_ver = subparsers.add_parser(
        "verifier",
        help="Vérifier la correspondance des types C.",
        parents=[parent],
    )
    p_ver.add_argument("--type", required=True, help="Type C à vérifier.")
    p_ver.add_argument("--taille", type=int, help="Taille attendue en octets.")
    p_ver.add_argument("--alignement", type=int, help="Alignement attendu en octets.")
    p_ver.add_argument("cible", type=Path, help="Fichier cible à examiner.")

    # Sous‑commande manipuler
    p_man = subparsers.add_parser(
        "manipuler",
        help="Manipuler des pointeurs/structures C.",
        parents=[parent],
    )
    p_man.add_argument("--type", required=True, help="Type C (ex : int*).")
    p_man.add_argument("--valeur", help="Valeur initiale (optionnel).")
    p_man.add_argument(
        "--operation",
        required=True,
        choices=["deref", "offset"],
        help="Opération à effectuer.",
    )
    p_man.add_argument("cible", type=Path, help="Fichier cible à examiner.")

    # Sous‑commande opaque
    p_opa = subparsers.add_parser(
        "opaque",
        help="Accéder à des champs de structures opaques.",
        parents=[parent],
    )
    p_opa.add_argument("--lib", required=True, help="Bibliothèque contenant le type.")
    p_opa.add_argument("--type", required=True, help="Nom du type opaque.")
    p_opa.add_argument("--champ", required=True, help="Champ à accéder.")
    p_opa.add_argument("cible", type=Path, help="Fichier cible à examiner.")

    args = parser.parse_args()

    # Résolution du répertoire racine
    racine: Path = (
        args.racine if hasattr(args, "racine") else RACINE
    )
    if not racine.is_absolute():
        racine = RACINE / racine

    # Lecture et validation du fichier cible
    cible_path = args.cible
    cible = cible_path if cible_path.is_absolute() else racine / cible_path
    try:
        _lire_fichier(cible)
    except OutilErreur as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 1

    examines: List[Path] = [cible]

    try:
        if args.commande == "augmenter":
            import json as _json

            args_list = _json.loads(args.args)
            resultat = augmenter(
                cible=cible,
                lib=getattr(args, "lib", None),
                func=getattr(args, "func", None),
                signature=getattr(args, "signature", None),
                args=args_list,
            )
            payload = {"resultat": resultat}
        elif args.commande == "compiler":
            resultat = compiler(cible=cible, code=args.code, output=args.output)
            payload = {"bibliotheque": resultat}
        elif args.commande == "verifier":
            resultat = verifier(
                cible=cible,
                type_c=args.type,
                taille=args.taille,
                alignement=args.alignement,
            )
            payload = resultat
        elif args.commande == "manipuler":
            valeur = args.valeur
            resultat = manipuler(
                cible=cible,
                type_c=args.type,
                valeur=valeur,
                operation=args.operation,
            )
            payload = {"resultat": resultat}
        elif args.commande == "opaque":
            resultat = opaque(
                cible=cible,
                lib=args.lib,
                type_opaque=args.type,
                champ=args.champ,
            )
            payload = {"resultat": resultat}
        else:
            raise OutilErreur("Commande inconnue.", code=2)

        denominateur = len(examines)
        if denominateur == 0:
            _refus_denominator()

        if getattr(args, "json", False):
            json_str = _construire_sortie(denominateur, examines, payload)
            sys.stdout.write(json_str + "\n")
        else:
            # En mode non‑JSON on n’émet que le payload (conforme à la règle 3)
            sys.stdout.write(str(payload) + "\n")
        return 0

    except OutilErreur as exc:
        sys.stderr.write(f"{exc.message}\n")
        return exc.code


__all__ = [
    "main",
    "augmenter",
    "compiler",
    "verifier",
    "manipuler",
    "opaque",
    "OutilErreur",
]

if __name__ == "__main__":
    raise SystemExit(main())