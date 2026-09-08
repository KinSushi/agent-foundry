"""tenir_annotations.py
QUESTION
    ce code tient‑il les promesses de ses annotations ?
MESURE
    get_type_hints pour l’annonce, isinstance sur l’origine pour le fait,
    traitement récursif des unions.
HYPOTHÈSES
    les annotations sont sincères ; get_type_hints peut importer.
LIMITES
    les paramètres de généricité ne sont pas vérifiés (list[int] → list) ;
    Any n’affirme rien ; appeler une fonction l’exécute.
    La fonction `conforme` ne vérifie pas les paramètres de fonction,
    seulement les retours.
CONTRE-EXEMPLES
    un vérificateur sans traitement des unions déclare VIOLÉE une fonction
    Optional[float] rendant None — faux positif mesuré.
INVOCATION
    {outil} {dossier}/a {dossier}/b --json
DOMAINE
    modules Python importables, annotations résolubles.
    Exemple d'appel depuis un autre projet :
        from tenir_annotations import conforme
        verdict = conforme(valeur, annotation)
"""

from __future__ import annotations

__all__ = ["conforme", "envelopper"]

import argparse
import functools
import importlib
import importlib.util
import inspect
import json
import signal
import sys
import threading
import types
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, get_args, get_origin, get_type_hints

# ------------------------------------------------------------
# Encodage UTF‑8 pour stdout / stderr
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Types de verdicts
# ------------------------------------------------------------
VERDICT_TENUE = "TENUE"
VERDICT_VIOLEE = "VIOLÉE"
VERDICT_INDECIDABLE = "INDÉCIDABLE"
VERDICT_NON_VERIFIABLE = "NON VÉRIFIABLE"
VERDICT_NON_APPELEE = "NON APPELÉE"
VERDICT_NON_RESOLUBLE = "NON RÉSOLUBLE"

def _est_any(annotation: Any) -> bool:
    """Retourne True si l’annotation correspond à typing.Any."""
    return annotation is Any

def _conforme(valeur: Any, annotation: Any) -> Optional[bool]:
    """
    Vérifie si *valeur* correspond à *annotation*.

    Retourne :
        True  – correspondance (TENUE)
        False – violation (VIOLÉE)
        None  – indécidable (INDÉCIDABLE)
    """
    if _est_any(annotation):
        return None

    origin = get_origin(annotation)
    args = get_args(annotation)

    if None in args and valeur is None:
        return True

    if origin is Union or isinstance(annotation, types.UnionType):
        results: List[Optional[bool]] = [_conforme(valeur, a) for a in args]
        if any(r is None for r in results):
            return None
        return any(r for r in results)

    if origin is not None:
        try:
            return isinstance(valeur, origin)
        except Exception:
            return False

    try:
        return isinstance(valeur, annotation)
    except Exception:
        return False

def conforme(valeur: Any, annotation: Any) -> Optional[bool]:
    """API publique : wrapper autour de _conforme."""
    return _conforme(valeur, annotation)

def _verdict_conforme(valeur: Any, annotation: Any) -> str:
    """Convertit le résultat de _conforme en verdict texte."""
    res = _conforme(valeur, annotation)
    if res is True:
        if get_origin(annotation) is not None:
            return f"{VERDICT_TENUE} (conteneur seul)"
        return VERDICT_TENUE
    if res is False:
        return VERDICT_VIOLEE
    return VERDICT_INDECIDABLE

def _deduire_valeur(annotation: Any) -> Any:
    """
    Déduit une valeur d’exemple à partir d’une annotation.
    Lève NotImplementedError si la déduction n’est pas possible.
    """
    if _est_any(annotation):
        return None
    if annotation is type(None):
        return None

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Union or isinstance(annotation, types.UnionType):
        for a in args:
            try:
                return _deduire_valeur(a)
            except NotImplementedError:
                continue
        raise NotImplementedError

    if origin in (list, set, frozenset):
        return origin()
    if origin is dict:
        return {}
    if origin is tuple:
        return ()

    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is str:
        return ""
    if annotation is bool:
        return False
    if annotation is bytes:
        return b""

    raise NotImplementedError

def _appeler_avec_timeout(
    func: Callable, args: Tuple[Any, ...], kwargs: Dict[str, Any], timeout: float = 1.0
) -> Any:
    """
    Appelle func avec un timeout.
    Utilise signal.alarm si disponible, sinon un threading.Timer.
    Lève TimeoutError en cas de dépassement.
    """
    if hasattr(signal, "SIGALRM"):
        def _handler(signum, frame):
            raise TimeoutError

        old_handler = signal.signal(signal.SIGALRM, _handler)
        signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            return func(*args, **kwargs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        result = [None]
        exc = [None]

        def _target():
            try:
                result[0] = func(*args, **kwargs)
            except Exception as e:
                exc[0] = e

        thread = threading.Thread(target=_target)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError
        if exc[0] is not None:
            raise exc[0]
        return result[0]

def _charger_module(nom: str) -> Any:
    """Importe le module indiqué par son nom ou son chemin de fichier."""
    try:
        if Path(nom).suffix == ".py":
            spec = importlib.util.spec_from_file_location(
                "module_temp", Path(nom).resolve()
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
            raise ImportError(f"Impossible de charger le fichier {nom}")
        return importlib.import_module(nom)
    except Exception as exc:
        raise ImportError(f"Erreur lors du chargement du module : {exc}") from exc

def _extraire_fonctions(mod: Any) -> List[Callable]:
    """Renvoie la liste des fonctions (et méthodes) définies dans *mod*."""
    return [
        obj
        for _, obj in inspect.getmembers(mod, inspect.isroutine)
        if inspect.getmodule(obj) is mod
    ]

def couverture(module_nom: str) -> Tuple[int, int, List[Dict[str, Any]]]:
    """
    Analyse le module sans exécuter de code.

    Retourne (total, annotées, détails) où *détails* contient le nom de chaque
    fonction et son statut d’annotation.
    """
    mod = _charger_module(module_nom)
    fonctions = _extraire_fonctions(mod)

    total = len(fonctions)
    annotées = 0
    details: List[Dict[str, Any]] = []

    for fn in fonctions:
        try:
            hints = get_type_hints(fn)
        except Exception:
            verdict = VERDICT_NON_RESOLUBLE
        else:
            if "return" in hints:
                annotées += 1
                verdict = "ANNOTÉE"
            else:
                verdict = VERDICT_NON_VERIFIABLE
        details.append({"fonction": fn.__name__, "verdict": verdict})

    return total, annotées, details

def verifier(module_nom: str, appeler: bool = False) -> List[Dict[str, Any]]:
    """
    Vérifie les fonctions du module.

    Si *appeler* est True, les fonctions sont appelées avec des valeurs
    déduites depuis les annotations de leurs paramètres (si possible) et
    leur retour est confronté à l’annotation.
    Sinon, seul le statut d’annotation est indiqué.
    """
    mod = _charger_module(module_nom)
    fonctions = _extraire_fonctions(mod)

    resultats: List[Dict[str, Any]] = []

    for fn in fonctions:
        entry: Dict[str, Any] = {"fonction": fn.__name__}
        try:
            hints = get_type_hints(fn)
            entry["avertissement"] = (
                "get_type_hints peut importer des modules (effet de bord)"
            )
        except Exception as exc:
            entry.update(
                {
                    "annotation": None,
                    "verdict": VERDICT_NON_RESOLUBLE,
                    "erreur": str(exc),
                }
            )
            resultats.append(entry)
            continue

        annotation = hints.get("return")
        entry["annotation"] = (
            getattr(annotation, "__name__", str(annotation)) if annotation else None
        )

        if annotation is None:
            entry["verdict"] = VERDICT_NON_VERIFIABLE
            resultats.append(entry)
            continue

        if not appeler:
            entry["verdict"] = "ANNOTÉE"
            resultats.append(entry)
            continue

        sig = inspect.signature(fn)
        args_list: List[Any] = []
        kwonly_dict: Dict[str, Any] = {}
        can_call = True

        for pname, param in sig.parameters.items():
            if param.default is not inspect.Parameter.empty:
                continue
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            ann_param = hints.get(pname)
            if ann_param is None:
                can_call = False
                break
            try:
                val = _deduire_valeur(ann_param)
            except NotImplementedError:
                can_call = False
                break
            if param.kind == param.KEYWORD_ONLY:
                kwonly_dict[pname] = val
            else:
                args_list.append(val)

        if not can_call:
            entry["verdict"] = VERDICT_NON_APPELEE
            resultats.append(entry)
            continue

        try:
            valeur = _appeler_avec_timeout(fn, tuple(args_list), kwonly_dict, timeout=1.0)
        except TimeoutError:
            entry.update(
                {
                    "verdict": "ERREUR D’EXÉCUTION",
                    "erreur": "timeout",
                }
            )
            resultats.append(entry)
            continue
        except Exception as exc:
            entry.update(
                {
                    "verdict": "ERREUR D’EXÉCUTION",
                    "erreur": str(exc),
                }
            )
            resultats.append(entry)
            continue

        entry["type_retour"] = type(valeur).__name__
        entry["verdict"] = _verdict_conforme(valeur, annotation)
        resultats.append(entry)

    return resultats

def envelopper(func):
    """Décorateur qui vérifie à chaque appel réel que le retour respecte l'annotation."""
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}
    annotation = hints.get("return")

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if annotation is None:
            return func(*args, **kwargs)
        result = func(*args, **kwargs)
        res = conforme(result, annotation)
        if res is False:
            raise RuntimeError(f"Return value {result!r} violates annotation {annotation}")
        return result
    return wrapper

def _afficher_humain_couverture(
    total: int, annotées: int, details: List[Dict[str, Any]]
) -> None:
    print(f"Fonctions analysées : {total}", file=sys.stdout)
    print(f"Fonctions annotées : {annotées}", file=sys.stdout)
    for d in details:
        print(f"- {d['fonction']}: {d['verdict']}", file=sys.stdout)

def _afficher_humain_verifier(resultats: List[Dict[str, Any]]) -> None:
    for r in resultats:
        ligne = f"{r['fonction']:20} → "
        if "erreur" in r:
            ligne += f"ERREUR ({r['erreur']})"
        else:
            ligne += f"{r.get('verdict', '')}"
            if r.get("annotation") is not None:
                ligne += f" (annonce : {r['annotation']})"
            if "type_retour" in r:
                ligne += f" (retour : {r['type_retour']})"
            if "avertissement" in r:
                ligne += f" [{r['avertissement']}]"
        print(ligne, file=sys.stdout)

def main() -> int:
    @envelopper
    def _dummy() -> int:
        return 42
    _dummy()

    parser = argparse.ArgumentParser(
        description="Vérifie la conformité des retours de fonctions aux annotations.",
        epilog="Exemple : python -m outils.tenir_annotations verifier mon_module --appeler",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine du projet (défaut : répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique.",
    )

    subparsers = parser.add_subparsers(dest="commande", required=True)

    parser_couv = subparsers.add_parser("couverture", help="Analyse sans exécution.")
    parser_couv.add_argument("--json", action="store_true", help="Sortie JSON")
    parser_couv.add_argument("module", help="Nom ou chemin du module à analyser.")

    parser_verif = subparsers.add_parser("verifier", help="Analyse avec appel éventuel.")
    parser_verif.add_argument("--json", action="store_true", help="Sortie JSON")
    parser_verif.add_argument("module", help="Nom ou chemin du module à analyser.")
    parser_verif.add_argument(
        "--appeler",
        action="store_true",
        help="Appeler les fonctions sans paramètres obligatoires.",
    )

    args = parser.parse_args()

    _ = args.racine

    if args.commande == "couverture":
        try:
            total, annotées, details = couverture(args.module)
            if total == 0:
                print("denominateur nul : refus d'analyse (aucune fonction).", file=sys.stderr)
                return 3
            if args.json:
                sortie = {
                    "denominateur": total,
                    "contrat": {
                        "QUESTION": "ce code tient‑il les promesses de ses annotations ?",
                        "MESURE": "get_type_hints pour l’annonce, isinstance sur l’origine pour le fait, traitement récursif des unions",
                        "HYPOTHÈSES": "les annotations sont sincères ; get_type_hints peut importer",
                        "LIMITES": "les paramètres de généricité ne sont pas vérifiés ; Any n’affirme rien ; appeler une fonction l’exécute",
                        "CONTRE-EXEMPLES": "un vérificateur sans traitement des unions déclare VIOLÉE une fonction Optional[float] rendant None",
                        "DOMAINE": "modules Python importables, annotations résolubles",
                    },
                    "total": total,
                    "annotées": annotées,
                    "details": details,
                }
                json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
                return 0 if annotées == total else 1
            else:
                _afficher_humain_couverture(total, annotées, details)
                return 0 if annotées == total else 1
        except Exception as e:
            if args.json:
                json.dump(
                    {
                        "denominateur": 0,
                        "erreur": str(e),
                        "contrat": {
                            "QUESTION": "ce code tient‑il les promesses de ses annotations ?",
                            "MESURE": "get_type_hints pour l’annonce, isinstance sur l’origine pour le fait, traitement récursif des unions",
                            "HYPOTHÈSES": "les annotations sont sincères ; get_type_hints peut importer",
                            "LIMITES": "les paramètres de généricité ne sont pas vérifiés ; Any n’affirme rien ; appeler une fonction l’exécute",
                            "CONTRE-EXEMPLES": "un vérificateur sans traitement des unions déclare VIOLÉE une fonction Optional[float] rendant None",
                            "DOMAINE": "modules Python importables, annotations résolubles",
                        },
                    },
                    sys.stdout,
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                print(f"Erreur: {e}", file=sys.stderr)
            return 2

    elif args.commande == "verifier":
        try:
            resultats = verifier(args.module, appeler=args.appeler)
            mod = _charger_module(args.module)
            fonctions = _extraire_fonctions(mod)
            total = len(fonctions)
            if total == 0:
                print("denominateur nul : refus d'analyse (aucune fonction).", file=sys.stderr)
                return 3
            violations = sum(1 for r in resultats if r.get("verdict") == VERDICT_VIOLEE)
            if args.json:
                sortie = {
                    "denominateur": total,
                    "contrat": {
                        "QUESTION": "ce code tient‑il les promesses de ses annotations ?",
                        "MESURE": "get_type_hints pour l’annonce, isinstance sur l’origine pour le fait, traitement récursif des unions",
                        "HYPOTHÈSES": "les annotations sont sincères ; get_type_hints peut importer",
                        "LIMITES": "les paramètres de généricité ne sont pas vérifiés ; Any n’affirme rien ; appeler une fonction l’exécute",
                        "CONTRE-EXEMPLES": "un vérificateur sans traitement des unions déclare VIOLÉE une fonction Optional[float] rendant None",
                        "DOMAINE": "modules Python importables, annotations résolubles",
                    },
                    "resultats": resultats,
                }
                json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
                return 0 if violations == 0 else 1
            else:
                _afficher_humain_verifier(resultats)
                return 0 if violations == 0 else 1
        except Exception as e:
            if args.json:
                json.dump(
                    {
                        "denominateur": 0,
                        "erreur": str(e),
                        "contrat": {
                            "QUESTION": "ce code tient‑il les promesses de ses annotations ?",
                            "MESURE": "get_type_hints pour l’annonce, isinstance sur l’origine pour le fait, traitement récursif des unions",
                            "HYPOTHÈSES": "les annotations sont sincères ; get_type_hints peut importer",
                            "LIMITES": "les paramètres de généricité ne sont pas vérifiés ; Any n’affirme rien ; appeler une fonction l’exécute",
                            "CONTRE-EXEMPLES": "un vérificateur sans traitement des unions déclare VIOLÉE une fonction Optional[float] rendant None",
                            "DOMAINE": "modules Python importables, annotations résolubles",
                        },
                    },
                    sys.stdout,
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                print(f"Erreur: {e}", file=sys.stderr)
            return 2

    return 1

if __name__ == "__main__":
    raise SystemExit(main())