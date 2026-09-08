"""
QUESTION       que fera ce pickle si je le charge ?
MESURE         pickletools.genops : opcodes et noms, sans exécution
HYPOTHÈSES     le flux est un pickle Python de protocole connu
LIMITES        un nom peut être construit dynamiquement et échapper à la liste ;
               ne dit pas ce que le code appelé FAIT ; ne couvre pas les protocoles futurs
CONTRE-EXEMPLES cloudpickle emploie REDUCE et STACK_GLOBAL comme un pickle piégé —
               un verdict fondé sur les seuls opcodes refuse tout transport légitime
DOMAINE        flux pickle, protocoles 0 à 5
"""

from __future__ import annotations

import ast
import json
import pickletools
import sys
from pathlib import Path
from typing import Literal, Set, Tuple

# Reconfiguration UTF‑8 pour stdout et stderr si possible
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ----------------------------------------------------------------------
# Contrôle AST : interdire toute utilisation de pickle.loads dans ce module
# ----------------------------------------------------------------------
_source = Path(__file__).read_text(encoding="utf-8")
_tree = compile(_source, __file__, "exec", ast.PyCF_ONLY_AST)
for node in ast.walk(_tree):
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == "pickle"
                and func.attr == "loads"
            ):
                raise RuntimeError("Usage interdit de pickle.loads détecté dans le code.")
# ----------------------------------------------------------------------

RACINE = Path(__file__).resolve().parent

# Liste noire des noms dangereux
NOMS_DANGEREUX = {
    ("builtins", "eval"),
    ("builtins", "exec"),
    ("builtins", "compile"),
    ("builtins", "__import__"),
    ("os", "system"),
    ("os", "popen"),
    ("posix", "system"),
    ("posix", "popen"),
    ("nt", "system"),
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
}

# Opcodes qui peuvent appeler du code
OPCodes_APPEL = {
    "REDUCE",
    "STACK_GLOBAL",
    "GLOBAL",
    "INST",
    "OBJ",
    "NEWOBJ",
    "BUILD",
    "EXT1",
    "EXT2",
    "EXT4",
}

Verdict = Literal[
    "INERTE",
    "APPELANT",
    "DANGEREUX",
    "ILLISIBLE",
    "NON DÉCIDABLE",
    "NON ANALYSABLE",
    "REFUSÉ (liste blanche)",
]

INERTE: Verdict = "INERTE"
APPELANT: Verdict = "APPELANT"
DANGEREUX: Verdict = "DANGEREUX"
ILLISIBLE: Verdict = "ILLISIBLE"
NON_DECIDABLE: Verdict = "NON DÉCIDABLE"
NON_ANALYSABLE: Verdict = "NON ANALYSABLE"
REFUSE_LB: Verdict = "REFUSÉ (liste blanche)"


def lire_fichier(chemin: Path) -> bytes:
    """Lit un fichier en bytes."""
    try:
        return chemin.read_bytes()
    except OSError as e:
        print(f"Erreur de lecture du fichier {chemin}: {e}", file=sys.stderr)
        raise SystemExit(2)


def est_marshal(donnees: bytes) -> bool:
    """Vérifie si le flux commence par un octet de type marshal."""
    if not donnees:
        return False
    return donnees[0] in {
        0x63,  # 'c' code object
        0x64,  # 'd' decimal integer
        0x66,  # 'f' float
        0x67,  # 'g' binary float
        0x69,  # 'i' signed int
        0x6C,  # 'l' long integer
        0x73,  # 's' string
        0x74,  # 't' interned string
        0x78,  # 'x' short ascii string
        0x79,  # 'y' short unicode string
    }


def extraire_protocole(donnees: bytes) -> int:
    """Renvoie le protocole du pickle en lisant le premier opcode PROTO."""
    try:
        for op, arg, _ in pickletools.genops(donnees):
            if op.name == "PROTO":
                return int(arg)  # arg est déjà un int
            break
    except Exception:
        pass
    return 0


def _capturer_noms(arg, op_name: str, noms_references: Set[Tuple[str, str]]) -> None:
    """Capture les noms potentiels depuis un argument d'opcode."""
    if isinstance(arg, str):
        noms_references.add(("builtins", arg))
    elif isinstance(arg, (tuple, list)):
        if len(arg) == 2 and all(isinstance(elt, str) for elt in arg):
            module, name = arg  # type: ignore[assignment]
            noms_references.add((module, name))
        else:
            for elt in arg:
                _capturer_noms(elt, op_name, noms_references)


def compter_opcodes(donnees: bytes) -> int:
    """Compte le nombre d'opcodes du flux pickle."""
    try:
        return sum(1 for _ in pickletools.genops(donnees))
    except Exception:
        return 0


def inspecter(
    donnees: bytes,
) -> Tuple[Verdict, int, Set[str], Set[Tuple[str, str]], bool]:
    """
    Inspecte un flux pickle et rend un verdict.

    Retourne:
        verdict,
        nombre d'opcodes,
        noms des opcodes appelants,
        ensemble des (module, attribut) référencés,
        présence d'opcodes dynamiques (BUILD/NEWOBJ) → avertissement
    """
    if est_marshal(donnees):
        return NON_ANALYSABLE, 0, set(), set(), False

    vu_stop = False
    opcodes = 0
    noms_appelants: Set[str] = set()
    noms_references: Set[Tuple[str, str]] = set()
    dynamique = False

    try:
        for op, arg, _ in pickletools.genops(donnees):
            opcodes += 1
            if op.name == "STOP":
                vu_stop = True
            if op.name in OPCodes_APPEL:
                noms_appelants.add(op.name)
            if op.name in {"BUILD", "NEWOBJ"}:
                dynamique = True
            if op.name == "GLOBAL":
                module, name = arg.split(" ", 1)
                noms_references.add((module, name))
            elif op.name in OPCodes_APPEL:
                _capturer_noms(arg, op.name, noms_references)
            elif op.name in {"SHORT_BINUNICODE", "BINUNICODE", "UNICODE"} and noms_appelants:
                if isinstance(arg, str):
                    noms_references.add(("builtins", arg))
    except ValueError:
        return ILLISIBLE, opcodes, noms_appelants, noms_references, dynamique
    except KeyError:
        return NON_DECIDABLE, opcodes, noms_appelants, noms_references, dynamique

    if not vu_stop:
        return ILLISIBLE, opcodes, noms_appelants, noms_references, dynamique

    if not noms_appelants:
        return INERTE, opcodes, noms_appelants, noms_references, dynamique

    dangereux_trouves = noms_references & NOMS_DANGEREUX
    if dangereux_trouves:
        return DANGEREUX, opcodes, noms_appelants, noms_references, dynamique

    return APPELANT, opcodes, noms_appelants, noms_references, dynamique


def noms(
    donnees: bytes, appelants: Set[str] | None = None
) -> Set[Tuple[str, str]]:
    """Rend l'ensemble des (module, attribut) référencés par le pickle."""
    if est_marshal(donnees):
        return set()

    appelants_local: Set[str] = appelants if appelants is not None else set()
    if appelants is None:
        try:
            for op, _, _ in pickletools.genops(donnees):
                if op.name in OPCodes_APPEL:
                    appelants_local.add(op.name)
        except (ValueError, KeyError):
            return set()

    noms_references: Set[Tuple[str, str]] = set()
    try:
        for op, arg, _ in pickletools.genops(donnees):
            if op.name == "GLOBAL":
                module, name = arg.split(" ", 1)
                noms_references.add((module, name))
            elif op.name in OPCodes_APPEL:
                _capturer_noms(arg, op.name, noms_references)
            elif op.name in {"SHORT_BINUNICODE", "BINUNICODE", "UNICODE"} and appelants_local:
                if isinstance(arg, str):
                    noms_references.add(("builtins", arg))
    except (ValueError, KeyError):
        return set()
    return noms_references


def verifier_liste_blanche(
    donnees: bytes, liste_blanche: Set[Tuple[str, str]]
) -> Tuple[Verdict, Set[Tuple[str, str]]]:
    """Vérifie si tous les noms référencés sont dans la liste blanche."""
    verdict, _, _, noms_references, _ = inspecter(donnees)
    if verdict in {NON_ANALYSABLE, ILLISIBLE}:
        return verdict, set()
    intrus = noms_references - liste_blanche
    if intrus:
        return REFUSE_LB, intrus
    return APPELANT, set()


def charger_liste_blanche(chemin: Path) -> Set[Tuple[str, str]]:
    """Charge une liste blanche depuis un fichier texte."""
    try:
        lignes = chemin.read_text(encoding="utf-8").splitlines()
        return {
            tuple(ligne.strip().split(".", 1))
            for ligne in lignes
            if ligne.strip()
        }
    except OSError as e:
        print(f"Erreur de lecture de la liste blanche {chemin}: {e}", file=sys.stderr)
        raise SystemExit(2)


def afficher_verdict_humain(
    verdict: Verdict,
    opcodes: int,
    noms_appelants: Set[str],
    noms_references: Set[Tuple[str, str]],
    protocole: int = 0,
    dynamique: bool = False,
) -> None:
    """Affiche le verdict de manière lisible."""
    print(f"protocole    {protocole}", file=sys.stdout)
    print(f"opcodes      {opcodes}", file=sys.stdout)
    if noms_appelants:
        print(f"appels       {', '.join(sorted(noms_appelants))}", file=sys.stdout)
    if noms_references:
        print(
            "noms         " + ", ".join(f"{m}.{a}" for m, a in sorted(noms_references)),
            file=sys.stdout,
        )
    if dynamique:
        print(
            "avertissement   noms construits dynamiquement (BUILD/NEWOBJ) – vérification incomplète",
            file=sys.stdout,
        )
    print(f"verdict      {verdict}", file=sys.stdout)


def afficher_noms_humain(noms_references: Set[Tuple[str, str]]) -> None:
    """Affiche les noms référencés de manière lisible."""
    for module, attr in sorted(noms_references):
        print(f"{module}.{attr}", file=sys.stdout)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Inspecte un fichier pickle sans l'exécuter.",
        epilog="Exemple: inspecter_pickle.py inspecter mon_fichier.pkl",
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    inspecter_parser = sous_parsers.add_parser("inspecter", help="Inspecte un fichier pickle.")
    inspecter_parser.add_argument("fichier", type=Path, help="Fichier pickle à inspecter")
    inspecter_parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Racine du projet (défaut: répertoire de l'outil)",
    )
    inspecter_parser.add_argument("--json", action="store_true", help="Sortie en JSON sur stdout")
    inspecter_parser.add_argument(
        "--liste-blanche",
        type=Path,
        help="Fichier texte listant les modules autorisés (un par ligne, format module.attribut)",
    )

    noms_parser = sous_parsers.add_parser("noms", help="Liste les noms référencés par le pickle.")
    noms_parser.add_argument("fichier", type=Path, help="Fichier pickle à analyser")
    noms_parser.add_argument("--json", action="store_true", help="Sortie en JSON sur stdout")

    args = parser.parse_args()

    donnees = lire_fichier(args.fichier)

    if args.commande == "inspecter":
        protocole = extraire_protocole(donnees)
        verdict, opcodes, noms_appelants, noms_references, dynamique = inspecter(donnees)

        # dénominateur = nombre d'éléments réellement examinés
        denominateur = opcodes
        if denominateur == 0:
            print(
                "Denominateur nul : aucun élément n’a été examiné.", file=sys.stderr
            )
            raise SystemExit(3)

        if args.liste_blanche:
            liste_blanche = charger_liste_blanche(args.liste_blanche)
            verdict_lb, intrus = verifier_liste_blanche(donnees, liste_blanche)
            if verdict_lb in {NON_ANALYSABLE, ILLISIBLE}:
                verdict = verdict_lb
            elif verdict_lb == REFUSE_LB:
                verdict = verdict_lb
                if args.json:
                    print(
                        json.dumps(
                            {
                                "verdict": verdict,
                                "opcodes": opcodes,
                                "denominateur": denominateur,
                                "appels": sorted(noms_appelants),
                                "noms": [f"{m}.{a}" for m, a in sorted(noms_references)],
                                "intrus": [f"{m}.{a}" for m, a in sorted(intrus)],
                                "avertissement": (
                                    "noms construits dynamiquement (BUILD/NEWOBJ) – vérification incomplète"
                                    if dynamique
                                    else None
                                ),
                                "contrat": {
                                    "QUESTION": "que fera ce pickle si je le charge ?",
                                    "MESURE": "pickletools.genops : opcodes et noms, sans exécution",
                                    "HYPOTHÈSES": "le flux est un pickle Python de protocole connu",
                                    "LIMITES": "un nom peut être construit dynamiquement et échapper à la liste ; "
                                    "ne dit pas ce que le code appelé FAIT ; ne couvre pas les protocoles futurs",
                                    "CONTRE-EXEMPLES": "cloudpickle emploie REDUCE et STACK_GLOBAL comme un pickle piégé — "
                                    "un verdict fondé sur les seuls opcodes refuse tout transport légitime",
                                    "DOMAINE": "flux pickle, protocoles 0 à 5",
                                },
                            }
                        )
                    )
                else:
                    afficher_verdict_humain(
                        verdict, opcodes, noms_appelants, noms_references, protocole, dynamique
                    )
                    print(
                        "intrus       " + ", ".join(f"{m}.{a}" for m, a in sorted(intrus)),
                        file=sys.stdout,
                    )
                return 1
            else:
                verdict = APPELANT

        if args.json:
            print(
                json.dumps(
                    {
                        "verdict": verdict,
                        "protocole": protocole,
                        "opcodes": opcodes,
                        "denominateur": denominateur,
                        "appels": sorted(noms_appelants),
                        "noms": [f"{m}.{a}" for m, a in sorted(noms_references)],
                        "avertissement": (
                            "noms construits dynamiquement (BUILD/NEWOBJ) – vérification incomplète"
                            if dynamique
                            else None
                        ),
                        "contrat": {
                            "QUESTION": "que fera ce pickle si je le charge ?",
                            "MESURE": "pickletools.genops : opcodes et noms, sans exécution",
                            "HYPOTHÈSES": "le flux est un pickle Python de protocole connu",
                            "LIMITES": "un nom peut être construit dynamiquement et échapper à la liste ; "
                            "ne dit pas ce que le code appelé FAIT ; ne couvre pas les protocoles futurs",
                            "CONTRE-EXEMPLES": "cloudpickle emploie REDUCE et STACK_GLOBAL comme un pickle piégé — "
                            "un verdict fondé sur les seuls opcodes refuse tout transport légitime",
                            "DOMAINE": "flux pickle, protocoles 0 à 5",
                        },
                    }
                )
            )
        else:
            afficher_verdict_humain(
                verdict, opcodes, noms_appelants, noms_references, protocole, dynamique
            )

        return 0 if verdict in {INERTE, APPELANT} else 1

    elif args.commande == "noms":
        noms_references = noms(donnees)
        denominateur = compter_opcodes(donnees)
        if denominateur == 0:
            print(
                "Denominateur nul : aucun élément n’a été examiné.", file=sys.stderr
            )
            raise SystemExit(3)

        if args.json:
            print(
                json.dumps(
                    {
                        "noms": [f"{m}.{a}" for m, a in sorted(noms_references)],
                        "denominateur": denominateur,
                        "contrat": {
                            "QUESTION": "que fera ce pickle si je le charge ?",
                            "MESURE": "pickletools.genops : opcodes et noms, sans exécution",
                            "HYPOTHÈSES": "le flux est un pickle Python de protocole connu",
                            "LIMITES": "un nom peut être construit dynamiquement et échapper à la liste ; "
                            "ne dit pas ce que le code appelé FAIT ; ne couvre pas les protocoles futurs",
                            "CONTRE-EXEMPLES": "cloudpickle emploie REDUCE et STACK_GLOBAL comme un pickle piégé — "
                            "un verdict fondé sur les seuls opcodes refuse tout transport légitime",
                            "DOMAINE": "flux pickle, protocoles 0 à 5",
                        },
                    }
                )
            )
        else:
            afficher_noms_humain(noms_references)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())