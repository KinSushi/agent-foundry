"""
Outil de diagnostic mémoire pour nommer les retenteurs d'objets, inspecter les cycles et mesurer le coût.

QUESTION       pourquoi cet objet est-il encore en mémoire, et qui le retient ?
MESURE         weakref pour la survie, gc.get_referrers pour le retenteur, gc.get_referents pour ce que l'objet retient, tracemalloc pour le coût — en passes séparées
HYPOTHÈSES     l'objet est atteignable depuis l'expression donnée ; l'interpréteur est CPython (get_referrers n'existe pas ailleurs)
LIMITES        ne voit pas les références détenues par du C ; ne voit pas ce qu'un autre thread retient au même instant ; tracemalloc fausse le chronométrage
CONTRE-EXEMPLE l'instrument non corrigé retient sa cible et rend TOUJOURS « encore vivant » — mesuré
DOMAINE        CPython, un processus, des objets Python

Avertissement de la documentation officielle :
« Care must be taken when using objects returned by get_referrers() because some of them could still be under construction… Avoid using get_referrers for any purpose other than debugging. »
Cet outil est strictement réservé au débogage.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import argparse
import gc
import json
import time
import tracemalloc
import weakref
from pathlib import Path
from typing import Any

CONTRAT: dict[str, str] = {
    "QUESTION": "pourquoi cet objet est-il encore en mémoire, et qui le retient ?",
    "MESURE": "weakref pour la survie, gc.get_referrers pour le retenteur, gc.get_referents pour ce que l'objet retient, tracemalloc pour le coût — en passes séparées",
    "HYPOTHÈSES": "l'objet est atteignable depuis l'expression donnée ; l'interpréteur est CPython (get_referrers n'existe pas ailleurs)",
    "LIMITES": "ne voit pas les références détenues par du C ; ne voit pas ce qu'un autre thread retient au même instant ; tracemalloc fausse le chronométrage",
    "CONTRE-EXEMPLE": "l'instrument non corrigé retient sa cible et rend TOUJOURS « encore vivant » — mesuré",
    "DOMAINE": "CPython, un processus, des objets Python",
    "AVERTISSEMENT": "Care must be taken when using objects returned by get_referrers() because some of them could still be under construction… Avoid using get_referrers for any purpose other than debugging."
}


def retenteurs(expression: str, racine: Path) -> dict[str, Any]:
    """Évalue l'expression, prend un weakref, tente la libération et nomme les retenteurs si l'objet survit."""
    code = compile(expression, "<expression>", "eval")
    ns: dict[str, Any] = {"__builtins__": __builtins__, "racine": racine}
    obj = eval(code, ns)

    try:
        ref = weakref.ref(obj)
    except TypeError:
        return {"erreur": "TypeError", "message": "list, dict, tuple et int ne supportent pas les références faibles"}

    del obj
    gc.collect()

    o = ref()
    if o is None:
        return {"vivant": False, "denominateur": 0, "retenteurs": [], "referents": []}

    # gc.get_referents(o) : ce que o retient — inspecter puis jeter
    referents = gc.get_referents(o)
    referents_types = [type(r).__name__ for r in referents]
    del referents

    referrers = gc.get_referrers(o)
    denominateur = len(referrers)
    retenteurs_list: list[dict[str, Any]] = []

    for r in referrers:
        if r is ref:
            continue
        type_nom = type(r).__name__
        cle: str | None = None
        if isinstance(r, dict):
            for k, v in r.items():
                if v is o:
                    cle = repr(k)
                    break
        retenteurs_list.append({"type": type_nom, "cle": cle})

    del o
    del referrers
    gc.collect()

    if ref() is None:
        return {"vivant": False, "denominateur": denominateur, "retenteurs": retenteurs_list, "referents": referents_types}
    else:
        conclusion = "NON DÉTERMINÉ" if denominateur == 0 else "propriétaire légitime"
        return {"vivant": True, "denominateur": denominateur, "retenteurs": retenteurs_list, "referents": referents_types, "conclusion": conclusion}


def cycles() -> dict[str, Any]:
    """Active DEBUG_SAVEALL, collecte les cycles, inspecte gc.garbage puis remet à zéro."""
    gc.set_debug(gc.DEBUG_SAVEALL)
    liberes = gc.collect()
    garbage = [str(g) for g in gc.garbage]
    denominateur = len(garbage)
    gc.garbage.clear()
    gc.set_debug(0)
    return {"liberes": liberes, "garbage": garbage, "denominateur": denominateur}


def couter(scenario: str, racine: Path) -> dict[str, Any]:
    """Exécute le scénario en deux passes : tracemalloc pour la mémoire, chronomètre pour le temps."""
    code = compile(scenario, "<scenario>", "exec")
    ns: dict[str, Any] = {"__builtins__": __builtins__, "racine": racine}

    # Passe mémoire : snapshot avant/après, compare_to pour size_diff et ligne source
    tracemalloc.start()
    snap_avant = tracemalloc.take_snapshot()
    exec(code, ns)
    snap_apres = tracemalloc.take_snapshot()
    tracemalloc.stop()

    # Passe temps : chronomètre, tracemalloc arrêté
    debut = time.perf_counter()
    exec(code, ns)
    fin = time.perf_counter()

    stats = snap_apres.compare_to(snap_avant, "lineno")
    total = sum(s.size for s in stats)
    top = [{"fichier": str(s.filename), "ligne": s.lineno, "size_diff": s.size_diff, "taille": s.size} for s in stats[:3]]
    denominateur = len(stats)

    return {"temps": fin - debut, "memoire_total": total, "top": top, "denominateur": denominateur}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Outil de diagnostic mémoire : nomme les retenteurs d'objets, inspecte les cycles et mesure le coût.",
        epilog="Exemple: python outils/retenir_memoire.py retenteurs \"[i for i in range(10)]\""
    )
    parser.add_argument("--racine", type=Path, default=Path(__file__).resolve().parent, help="Surcharge la racine du projet")
    parser.add_argument("--json", action="store_true", help="Renvoie un objet JSON sur stdout")

    subparsers = parser.add_subparsers(dest="commande", required=True)

    p_ret = subparsers.add_parser("retenteurs", help="Nomme les retenteurs d'un objet évalué par l'expression")
    p_ret.add_argument("expression", type=str, help="Expression Python évaluée pour obtenir l'objet")

    p_cyc = subparsers.add_parser("cycles", help="Inspecte les cycles collectés par gc")

    p_cout = subparsers.add_parser("couter", help="Mesure le coût mémoire et temporel d'un scénario")
    p_cout.add_argument("scenario", type=str, help="Code Python exécuté pour la mesure")

    args = parser.parse_args()

    if not hasattr(gc, 'get_referrers'):
        msg = "Erreur: CPython requis (gc.get_referrers indisponible)"
        if args.json:
            print(json.dumps({"erreur": msg, "contrat": CONTRAT, "denominateur": 0}, ensure_ascii=False, indent=2))
        else:
            print(msg, file=sys.stderr)
        return 3

    resultat: dict[str, Any] = {}
    code_sortie = 0

    if args.commande == "retenteurs":
        resultat = retenteurs(args.expression, args.racine)
        if resultat.get("vivant", False):
            code_sortie = 1
            if resultat.get("denominateur", 1) == 0:
                code_sortie = 2
    elif args.commande == "cycles":
        resultat = cycles()
        if resultat.get("liberes", 0) > 0 or len(resultat.get("garbage", [])) > 0:
            code_sortie = 1
    elif args.commande == "couter":
        resultat = couter(args.scenario, args.racine)
        if resultat.get("memoire_total", 0) > 0:
            code_sortie = 1

    # Contrôle F4 : dénominateur nul → refus de conclure
    if "erreur" not in resultat:
        denominateur = resultat.get("denominateur", 0)
        if denominateur == 0:
            print("Refus de conclure: dénominateur nul (aucun élément réellement examiné).", file=sys.stderr)
            code_sortie = 3

    if args.json:
        sortie = {"contrat": CONTRAT, **resultat}
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    else:
        print("Care must be taken when using objects returned by get_referrers() because some of them could still be under construction… Avoid using get_referrers for any purpose other than debugging.")
        if args.commande == "retenteurs":
            if "erreur" in resultat:
                print(f"Erreur: {resultat['message']}")
            else:
                print(f"Vivant: {resultat['vivant']}")
                print(f"Dénominateur: {resultat['denominateur']}")
                if resultat['denominateur'] == 0:
                    print("Refus de conclure: dénominateur nul (NON DÉTERMINÉ).")
                for r in resultat['retenteurs']:
                    if r['cle']:
                        print(f"Retenteur: {r['type']}, clé {r['cle']}")
                    else:
                        print(f"Retenteur: {r['type']}")
                if resultat.get('referents'):
                    print(f"Référents (ce que l'objet retient): {resultat['referents']}")
        elif args.commande == "cycles":
            print(f"Libérés par gc: {resultat['liberes']}")
            print(f"Garbage: {resultat['garbage']}")
            print(f"Dénominateur: {resultat['denominateur']}")
        elif args.commande == "couter":
            print(f"Temps: {resultat['temps']:.6f} s")
            print(f"Mémoire totale: {resultat['memoire_total']} o")
            print(f"Dénominateur: {resultat['denominateur']}")
            for t in resultat['top']:
                print(f"  {t['fichier']}:{t['ligne']} -> size_diff={t['size_diff']}, taille={t['taille']} o")

    return code_sortie


if __name__ == "__main__":
    raise SystemExit(main())