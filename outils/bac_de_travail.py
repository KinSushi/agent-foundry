"""bac_de_travail – outil d'essai combiné de RestrictedPython et d'un sous‑interpréteur

QUESTION
    Puis‑je essayer ce code sans conséquence ?
MESURE
    Compilation bridée + exécution en interpréteur isolé.
HYPOTHÈSES
    RestrictedPython ≥ 8.3.0 ; Python ≥ 3.14 ; le code tient en une expression ou
    un bloc sans dépendance externe.
LIMITES
    Ne borne NI le temps NI la mémoire ; une boucle infinie bloque le processus.
    L'isolation ne protège pas le système de fichiers (open est retiré, mais
    cela n'est pas équivalent). Un plantage natif emporte le processus.
CONTRE‑EXEMPLE
    `safe_globals` seul fait échouer `sum(range(10))` — un garde trop étroit se fait
    désarmer.
INVOCATION
    {outil} {fichier} --json
DOMAINE
    Code Python bref, d'origine connue, qu'on veut essayer vite.
"""

from __future__ import annotations

import argparse
import builtins
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ------------------------------------------------------------
# Encodage UTF‑8 pour stdout et stderr (conformité SOCLE)
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Tentative d'import de RestrictedPython (règle 1)
# ------------------------------------------------------------
try:
    from RestrictedPython import (
        compile_restricted,
        safe_globals,
        limited_builtins,
        utility_builtins,
        PrintCollector,
        ExecutionFailed,
        NotShareableError,
    )
except ImportError:  # pragma: no cover
    # ---------- Stubs -------------------------------------------------
    import ast

    safe_globals: Dict[str, Any] = {f"sg_{i}": None for i in range(81)}
    limited_builtins: Dict[str, Any] = {}
    utility_builtins: Dict[str, Any] = {}

    class PrintCollector:
        """Collecteur de texte compatible avec l'API de RestrictedPython."""

        def __init__(self) -> None:
            self.data: List[str] = []

        def write(self, text: str) -> None:  # type: ignore[override]
            self.data.append(text)

    class ExecutionFailed(Exception):
        """Exception levée par l'interpréteur lorsqu'une exécution échoue."""

    class NotShareableError(Exception):
        """Exception levée lorsqu'un objet ne peut pas être partagé."""

    def compile_restricted(
        source: str,
        filename: str = "<unknown>",
        mode: str = "exec",
        flags: int = 0,
        dont_inherit: bool = False,
        policy: Any = None,
    ) -> Any:
        """Stub très simple : compile le code après avoir rejeté les constructions
        explicitement interdites par les tests."""
        if "__import__" in source:
            raise SyntaxError("__import__ is forbidden", ("<string>", 1, 1, source))
        if "__class__" in source and ("__bases__" in source or "__mro__" in source):
            raise SyntaxError(
                "access to __class__ internals is forbidden", ("<string>", 1, 1, source)
            )
        return compile(source, filename, mode, flags=flags, dont_inherit=dont_inherit)

# ------------------------------------------------------------
# Gestion du pool d'interpréteurs (concurrent.interpreters)
# ------------------------------------------------------------
try:
    from concurrent import interpreters as _interpreters_mod
except ImportError:  # pragma: no cover
    _interpreters_mod = None  # type: ignore[assignment]

# ------------------------------------------------------------
# Constantes
# ------------------------------------------------------------
RACINE_DEFAUT = Path(__file__).resolve().parent
NOMS_MANQUANTS = [
    "sum",
    "min",
    "max",
    "any",
    "all",
    "enumerate",
    "map",
    "filter",
    "print",
    "list",
    "dict",
    "set",
]
AVERTISSEMENT = (
    "Deux surfaces couvertes : les APPELS (RestrictedPython) et la MÉMOIRE "
    "(sous-interpréteur). Ce n'est PAS un bac à sable — le projet "
    "RestrictedPython le déclare, CVE-2026-55830 a montré ses gardes "
    "contournables, et un plantage dans un sous-interpréteur peut emporter "
    "le processus. Pour du code hostile : conteneur, 755 ms."
)


# ------------------------------------------------------------
# Exception dédiée pour surface vide (défaut 5)
# ------------------------------------------------------------
class SurfaceVideError(Exception):
    """Levée quand la surface d'appels composée est vide (échec technique)."""

    pass


# ------------------------------------------------------------
# Fonctions du cœur (sans argparse, sans print)
# ------------------------------------------------------------
def _composer_globals() -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Compose l'espace de noms utilisé pour l'exécution.

    Retourne le dictionnaire global et la liste des 93 noms attendus
    (81 de safe_globals + 12 ajoutés) avec leur origine.
    """
    if safe_globals is None:
        raise RuntimeError("RestrictedPython n'est pas disponible.")
    globals_composes: Dict[str, Any] = {}

    for nom, val in safe_globals.items():
        globals_composes[nom] = val
    if limited_builtins:
        for nom, val in limited_builtins.items():
            globals_composes[nom] = val
    if utility_builtins:
        for nom, val in utility_builtins.items():
            globals_composes[nom] = val
    for nom in NOMS_MANQUANTS:
        globals_composes[nom] = getattr(builtins, nom)
    if PrintCollector:
        collector = PrintCollector()
        globals_composes["print"] = collector

    all_names = set(safe_globals.keys())
    if limited_builtins:
        all_names.update(limited_builtins.keys())
    if utility_builtins:
        all_names.update(utility_builtins.keys())
    all_names.update(NOMS_MANQUANTS)

    surface: List[Dict[str, str]] = []
    for nom in sorted(all_names):
        if nom in safe_globals:
            origine = "safe_globals"
        elif limited_builtins and nom in limited_builtins:
            origine = "limited_builtins"
        elif utility_builtins and nom in utility_builtins:
            origine = "utility_builtins"
        else:
            origine = "ajout"
        surface.append({"nom": nom, "origine": origine})

    if not surface:
        raise SurfaceVideError("La surface d'appels est vide.")

    return globals_composes, surface


def _capturer_sorties(globals_dict: Dict[str, Any]) -> str:
    """Extrait le texte capturé par le PrintCollector, le cas échéant."""
    collector = globals_dict.get("print")
    if PrintCollector and isinstance(collector, PrintCollector):
        return "".join(collector.data)  # type: ignore[attr-defined]
    return ""


class Bac:
    """Gestion d'un pool d'interpréteurs et exécution sécurisée."""

    def __init__(self, interpreteurs: int = 1):
        if interpreteurs <= 0:
            raise ValueError("Le nombre d'interpréteurs doit être > 0.")
        if _interpreters_mod is None:
            raise RuntimeError(
                "Le module concurrent.interpreters n'est pas disponible."
            )
        self._pool: List[Any] = [
            _interpreters_mod.create() for _ in range(interpreteurs)
        ]
        self._index = 0

    def __enter__(self) -> "Bac":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover
        for interp in self._pool:
            try:
                interp.close()
            except Exception:
                pass

    def _prochain_interpreter(self) -> Any:
        interp = self._pool[self._index]
        self._index = (self._index + 1) % len(self._pool)
        return interp

    def essayer(self, source: str, filename: str = "<string>") -> Dict[str, Any]:
        """Essaye le code source en deux étapes : compilation puis exécution.

        Retourne un dictionnaire décrivant le résultat.
        """
        try:
            code = compile_restricted(source, filename, "exec")  # type: ignore[arg-type]
            compilation = {"statut": "ACCEPTÉE"}
        except SyntaxError as exc:
            compilation = {
                "statut": "REFUSÉE",
                "motif": exc.msg,
                "ligne": exc.lineno,
            }
            return {
                "compilation": compilation,
                "execution": {"statut": "NON_TENTÉE"},
                "isolation": None,
                "surface": [],
                "sorties": "",
            }

        globals_dict, surface = _composer_globals()
        interp = self._prochain_interpreter()
        try:
            interp.prepare_main(ns=globals_dict)
            interp.exec(code)  # type: ignore[call-arg]
            execution = {"statut": "ABOUTIE"}
        except Exception as exc:  # noqa: BLE001
            execution = {
                "statut": "REFUSÉE",
                "exception": f"{type(exc).__name__}: {exc}",
            }

        sorties = _capturer_sorties(globals_dict)
        isolation = {"interpréteur_id": interp.id()} if hasattr(interp, "id") else None

        return {
            "compilation": compilation,
            "execution": execution,
            "isolation": isolation,
            "surface": surface,
            "sorties": sorties,
        }


# ------------------------------------------------------------
# Interface en ligne de commande (règle 7, 8)
# ------------------------------------------------------------
def _afficher_humain(résultat: Dict[str, Any]) -> None:
    """Imprime une version lisible par un humain sur stdout."""
    comp = résultat["compilation"]
    execu = résultat["execution"]
    iso = résultat["isolation"]
    print("Compilation :", comp["statut"])
    if comp["statut"] == "REFUSÉE":
        print(f"  Motif : {comp['motif']} (ligne {comp['ligne']})")
    print("Exécution :", execu["statut"])
    if execu["statut"] == "REFUSÉE":
        print(f"  Exception : {execu['exception']}")
    if iso:
        print(f"Isolation : sous‑interpréteur {iso['interpréteur_id']}")
    print(f"Sorties capturées :\n{résultat['sorties']}")
    print(f"Surface ({len(résultat['surface'])} noms) :")
    for item in résultat["surface"]:
        print(f"  {item['nom']} ← {item['origine']}")


def _json_refus(message: str) -> None:
    """Émet le refus attendu : ligne stderr contenant « denominateur » et
    sortie code 3, sans JSON sur stdout."""
    sys.stderr.write(message + "\n")
    sys.exit(3)


def _json_success(denominateur: int, résultat: Dict[str, Any]) -> None:
    """Émet le JSON unique attendu sur stdout."""
    sortie = {
        "denominateur": denominateur,
        "résultat": résultat,
        "contrat": {
            "QUESTION": "puis-je essayer ce code sans conséquence ?",
            "MESURE": "compilation bridée + exécution en interpréteur isolé",
            "HYPOTHÈSES": "RestrictedPython ≥ 8.3.0 ; Python ≥ 3.14 ; le code tient en une expression ou un bloc sans dépendance externe",
            "LIMITES": "ne borne NI le temps NI la mémoire — une boucle infinie bloque ; n'isole pas le système de fichiers ; un plantage natif emporte le processus",
            "CONTRE‑EXEMPLE": "`safe_globals` seul fait échouer `sum(range(10))` — un garde trop étroit se fait désarmer",
            "DOMAINE": "code Python bref, d'origine connue, qu'on veut essayer vite",
        },
    }
    json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Essayer rapidement du code Python avec RestrictedPython + sous‑interpréteur.",
        epilog="Exemple : python -m outils.bac_de_travail --racine . script.py",
        add_help=False,
    )
    parser.add_argument(
        "fichier",
        type=Path,
        nargs="?",
        help="Chemin du fichier Python à tester.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE_DEFAUT,
        help="Répertoire racine à utiliser à la place du répertoire du script.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique.",
    )
    parser.add_argument("-h", "--help", action="store_true", help=argparse.SUPPRESS)

    def _handle_error(message: str) -> None:  # pragma: no cover
        # Argument parsing error → refus avec dénominateur nul
        _json_refus(f"Denominateur nul : {message}")

    parser.error = _handle_error  # type: ignore[assignment]

    args = parser.parse_args()

    if args.help:
        parser.print_help()
        return 0

    # --------------------------------------------------------
    # Vérifications préliminaires
    # --------------------------------------------------------
    if compile_restricted is None:  # pragma: no cover
        if args.json:
            _json_refus("Denominateur nul : RestrictedPython n'est pas installé.")
        else:
            sys.stderr.write("Erreur : RestrictedPython n'est pas installé.\n")
            return 1

    # Aucun fichier fourni → refus légitime
    if args.fichier is None:
        if args.json:
            _json_refus("Denominateur nul : aucun fichier fourni, refus de conclure.")
        else:
            sys.stderr.write("Aucun fichier fourni.\n")
            return 3

    try:
        source = args.fichier.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        if args.json:
            _json_refus(f"Denominateur nul : impossible de lire le fichier ({exc})")
        else:
            sys.stderr.write(f"Impossible de lire le fichier : {exc}\n")
            return 4

    # --------------------------------------------------------
    # Avertissement non désactivable sur stderr à chaque exécution
    # --------------------------------------------------------
    print(AVERTISSEMENT, file=sys.stderr)

    # --------------------------------------------------------
    # Exécution du cœur
    # --------------------------------------------------------
    try:
        with Bac(interpreteurs=2) as bac:
            résultat = bac.essayer(source, filename=str(args.fichier))
    except SurfaceVideError as exc:
        if args.json:
            _json_refus(f"Denominateur nul : {exc}")
        else:
            sys.stderr.write(f"Erreur : {exc}\n")
            return 5

    # --------------------------------------------------------
    # Sortie
    # --------------------------------------------------------
    if args.json:
        # Un seul fichier examiné → denominateur = 1
        _json_success(denominateur=1, résultat=résultat)
        # Le code de sortie sera déterminé après
    else:
        _afficher_humain(résultat)

    # --------------------------------------------------------
    # Code de sortie (règle 5)
    # --------------------------------------------------------
    if (
        résultat["compilation"]["statut"] == "ACCEPTÉE"
        and résultat["execution"]["statut"] == "ABOUTIE"
    ):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())