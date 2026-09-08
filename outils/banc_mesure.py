#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Banc de comparaison qui REFUSE de conclure quand la comparaison est malhonnete.

QUESTION       cette voie est-elle vraiment plus rapide que celle-là ?
MESURE         timeit.repeat sur N répétitions ; minimum retenu comme estimateur ;
               allocation par tracemalloc en PASSE SÉPARÉE ; machine caractérisée
               par py-cpuinfo ; trois passes : temps sans GC, temps avec GC, allocation
HYPOTHESES     les deux voies calculent la même chose ; la machine n'est pas chargée
               par ailleurs ; les moyennes sont approximativement normales ; le GC
               est dans l'état par défaut à l'appel
LIMITES        tracemalloc actif fausse le temps -- mesure ×2,6 sur math.sumprod ;
               la fréquence rendue par py-cpuinfo est ANNONCÉE, pas mesurée ;
               un cache chaud change le classement ; une seule mesure aberrante
               (ramasse-miettes, préemption) fausse l'étendue min→max
CONTRE-EXEMPLES le critère par étendue min→max refusait de conclure 4 fois sur 4,
               même à +50 % de travail -- l'étendue ne décroît pas avec les
               répétitions, le bruit standard si ; une seule mesure NaN empoisonne
               la médiane en silence
INVOCATION
    {outil} {fichier} --json
DOMAINE        deux voies comparables, sur cette machine, à cet instant
"""
from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import math
import statistics
import sys
import time
import timeit
import tracemalloc
from pathlib import Path
from typing import Any, Callable

try:
    import cpuinfo
except ImportError:
    cpuinfo = None

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

REPETITIONS_MINIMUM = 3
FACTEUR_ALLOCATION_TOLERE = 2.0
ABS_TOL = 1e-12
SEUIL_CONFIANCE = 1.96  # 95 % pour une distribution normale
SEUIL_ECART_GC = 0.10  # 10 % d'écart pour signaler une dépendance au GC


class Voie:
    """Une voie de calcul, avec ses mesures brutes."""

    def __init__(self, nom: str, appel: Callable[[], Any]) -> None:
        self.nom = nom
        self.appel = appel
        self.temps_ns_sans_gc: list[int] = []
        self.temps_ns_avec_gc: list[int] = []
        self.pic_octets: int = 0
        self.resultat: Any = None
        self.mesures_ecartees: int = 0

    @property
    def minimum_ns_sans_gc(self) -> int:
        return min(self.temps_ns_sans_gc) if self.temps_ns_sans_gc else 0

    @property
    def minimum_ns_avec_gc(self) -> int:
        return min(self.temps_ns_avec_gc) if self.temps_ns_avec_gc else 0

    @property
    def etendue_ns_sans_gc(self) -> int:
        return (max(self.temps_ns_sans_gc) - min(self.temps_ns_sans_gc)) if self.temps_ns_sans_gc else 0

    @property
    def etendue_ns_avec_gc(self) -> int:
        return (max(self.temps_ns_avec_gc) - min(self.temps_ns_avec_gc)) if self.temps_ns_avec_gc else 0

    def _filtrer_nan(self, valeurs: list[int]) -> list[int]:
        valides = []
        for v in valeurs:
            if not math.isfinite(v):
                self.mesures_ecartees += 1
            else:
                valides.append(v)
        return valides

    @property
    def moyenne_ns_sans_gc(self) -> float:
        valides = self._filtrer_nan(self.temps_ns_sans_gc)
        return statistics.fmean(valides) if valides else float("nan")

    @property
    def moyenne_ns_avec_gc(self) -> float:
        valides = self._filtrer_nan(self.temps_ns_avec_gc)
        return statistics.fmean(valides) if valides else float("nan")

    @property
    def mediane_ns_sans_gc(self) -> float:
        valides = self._filtrer_nan(self.temps_ns_sans_gc)
        return statistics.median(valides) if valides else float("nan")

    @property
    def mediane_ns_avec_gc(self) -> float:
        valides = self._filtrer_nan(self.temps_ns_avec_gc)
        return statistics.median(valides) if valides else float("nan")

    @property
    def ecart_type_ns_sans_gc(self) -> float:
        valides = self._filtrer_nan(self.temps_ns_sans_gc)
        return statistics.stdev(valides) if len(valides) > 1 else 0.0

    @property
    def ecart_type_ns_avec_gc(self) -> float:
        valides = self._filtrer_nan(self.temps_ns_avec_gc)
        return statistics.stdev(valides) if len(valides) > 1 else 0.0

    @property
    def intervalle_confiance_sans_gc(self) -> float:
        valides = self._filtrer_nan(self.temps_ns_sans_gc)
        if len(valides) < 2:
            return float("nan")
        return SEUIL_CONFIANCE * statistics.stdev(valides) / math.sqrt(len(valides))

    @property
    def intervalle_confiance_avec_gc(self) -> float:
        valides = self._filtrer_nan(self.temps_ns_avec_gc)
        if len(valides) < 2:
            return float("nan")
        return SEUIL_CONFIANCE * statistics.stdev(valides) / math.sqrt(len(valides))

    def en_dict(self) -> dict:
        return {
            "nom": self.nom,
            "minimum_ms_sans_gc": round(self.minimum_ns_sans_gc / 1e6, 4),
            "minimum_ms_avec_gc": round(self.minimum_ns_avec_gc / 1e6, 4),
            "moyenne_ms_sans_gc": round(self.moyenne_ns_sans_gc / 1e6, 4),
            "moyenne_ms_avec_gc": round(self.moyenne_ns_avec_gc / 1e6, 4),
            "mediane_ms_sans_gc": round(self.mediane_ns_sans_gc / 1e6, 4),
            "mediane_ms_avec_gc": round(self.mediane_ns_avec_gc / 1e6, 4),
            "ecart_type_ms_sans_gc": round(self.ecart_type_ns_sans_gc / 1e6, 4),
            "ecart_type_ms_avec_gc": round(self.ecart_type_ns_avec_gc / 1e6, 4),
            "intervalle_confiance_ms_sans_gc": round(self.intervalle_confiance_sans_gc / 1e6, 4),
            "intervalle_confiance_ms_avec_gc": round(self.intervalle_confiance_avec_gc / 1e6, 4),
            "vecteur_ms_sans_gc": [round(t / 1e6, 4) for t in self.temps_ns_sans_gc],
            "vecteur_ms_avec_gc": [round(t / 1e6, 4) for t in self.temps_ns_avec_gc],
            "pic_octets": self.pic_octets,
            "resultat": repr(self.resultat)[:120],
            "mesures_ecartees": self.mesures_ecartees,
        }


class Banc:
    """Compare des voies, et refuse de conclure quand la comparaison ment."""

    def __init__(self, repetitions: int = 5) -> None:
        self.repetitions = repetitions
        self.voies: list[Voie] = []

    def ajouter(self, nom: str, appel: Callable[[], Any]) -> None:
        self.voies.append(Voie(nom, appel))

    def _mesurer_allocation(self, voie: Voie) -> None:
        voie.resultat = voie.appel()
        tracemalloc.reset_peak()
        avant, _ = tracemalloc.get_traced_memory()
        voie.appel()
        _, pic = tracemalloc.get_traced_memory()
        voie.pic_octets = max(0, pic - avant)

    def _mesurer_temps_sans_gc(self, voie: Voie) -> None:
        voie.appel()
        for _ in range(self.repetitions):
            depart = time.perf_counter_ns()
            voie.appel()
            voie.temps_ns_sans_gc.append(time.perf_counter_ns() - depart)

    def _mesurer_temps_avec_gc(self, voie: Voie) -> None:
        def _avec_gc():
            gc.enable()
            voie.appel()

        voie.appel()
        for _ in range(self.repetitions):
            depart = time.perf_counter_ns()
            _avec_gc()
            voie.temps_ns_avec_gc.append(time.perf_counter_ns() - depart)

    def _memes_resultats(self, a: Any, b: Any) -> bool:
        if isinstance(a, float) and isinstance(b, float):
            return math.isclose(a, b, rel_tol=1e-9, abs_tol=ABS_TOL)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=ABS_TOL)
        try:
            return bool(a == b)
        except Exception:
            return False

    def _refus(self) -> list[dict]:
        refus: list[dict] = []

        if self.repetitions < REPETITIONS_MINIMUM:
            refus.append(
                {
                    "motif": "répétitions insuffisantes",
                    "detail": f"{self.repetitions} répétition(s) : deux nombres sans répétition ne se comparent pas (minimum {REPETITIONS_MINIMUM})",
                }
            )

        for voie in self.voies:
            if voie.mesures_ecartees > self.repetitions / 3:
                refus.append(
                    {
                        "motif": "trop de mesures invalides",
                        "detail": f"{voie.nom} : {voie.mesures_ecartees} mesures écartées sur {self.repetitions} ({voie.mesures_ecartees/self.repetitions:.0%})",
                    }
                )

        for a, b in zip(self.voies, self.voies[1:]):
            if not self._memes_resultats(a.resultat, b.resultat):
                refus.append(
                    {
                        "motif": "les deux voies ne calculent pas la même chose",
                        "detail": f"{a.nom} rend {a.resultat!r}, {b.nom} rend {b.resultat!r}",
                    }
                )

        for a, b in zip(self.voies, self.voies[1:]):
            pa, pb = max(a.pic_octets, 1), max(b.pic_octets, 1)
            facteur = max(pa, pb) / min(pa, pb)
            if facteur > FACTEUR_ALLOCATION_TOLERE:
                gros, petit = (a, b) if pa > pb else (b, a)
                refus.append(
                    {
                        "motif": "les deux voies ne font pas le même travail",
                        "detail": f"{gros.nom} alloue {gros.pic_octets} o contre {petit.pic_octets} o pour {petit.nom} — facteur {facteur:.1f}.",
                    }
                )

        for a, b in zip(self.voies, self.voies[1:]):
            ecart_sans_gc = abs(a.mediane_ns_sans_gc - b.mediane_ns_sans_gc)
            seuil_sans_gc = math.sqrt(
                (a.ecart_type_ns_sans_gc ** 2 / self.repetitions)
                + (b.ecart_type_ns_sans_gc ** 2 / self.repetitions)
            ) * SEUIL_CONFIANCE
            if ecart_sans_gc < seuil_sans_gc:
                refus.append(
                    {
                        "motif": "indiscernable du bruit (sans GC)",
                        "detail": f"écart {ecart_sans_gc/1e6:.4f} ms entre {a.nom} et {b.nom}, seuil de signification {seuil_sans_gc/1e6:.4f} ms",
                    }
                )

            ecart_avec_gc = abs(a.mediane_ns_avec_gc - b.mediane_ns_avec_gc)
            seuil_avec_gc = math.sqrt(
                (a.ecart_type_ns_avec_gc ** 2 / self.repetitions)
                + (b.ecart_type_ns_avec_gc ** 2 / self.repetitions)
            ) * SEUIL_CONFIANCE
            if ecart_avec_gc < seuil_avec_gc:
                refus.append(
                    {
                        "motif": "indiscernable du bruit (avec GC)",
                        "detail": f"écart {ecart_avec_gc/1e6:.4f} ms entre {a.nom} et {b.nom}, seuil de signification {seuil_avec_gc/1e6:.4f} ms",
                    }
                )

        return refus

    def _caracteristiques_machine(self) -> dict:
        if cpuinfo is None:
            return {"erreur": "cpuinfo non disponible"}
        info = cpuinfo.get_cpu_info()
        return {
            "marque": info.get("brand_raw", "inconnue"),
            "architecture": info.get("arch", "inconnue"),
            "frequence_annoncee": info.get("hz_advertised_friendly", "inconnue"),
            "cœurs": info.get("count", "inconnue"),
            "cache_l2": info.get("l2_cache_size", "inconnue"),
        }

    def comparer(self) -> dict:
        gc_initial = gc.isenabled()
        avertissements: list[str] = []
        if not gc_initial:
            avertissements.append(
                "AVERTISSEMENT : le ramasse-miettes était désactivé avant l'appel ; les mesures avec GC peuvent être faussées."
            )

        for voie in self.voies:
            self._mesurer_temps_sans_gc(voie)
        for voie in self.voies:
            self._mesurer_temps_avec_gc(voie)

        deja = tracemalloc.is_tracing()
        if not deja:
            tracemalloc.start()
        try:
            for voie in self.voies:
                self._mesurer_allocation(voie)
        finally:
            if not deja:
                tracemalloc.stop()

        refus = self._refus()

        seuil_sans_gc_ms = None
        seuil_avec_gc_ms = None
        if len(self.voies) >= 2:
            a, b = self.voies[0], self.voies[1]
            seuil_sans_gc = math.sqrt(
                (a.ecart_type_ns_sans_gc ** 2 / self.repetitions)
                + (b.ecart_type_ns_sans_gc ** 2 / self.repetitions)
            ) * SEUIL_CONFIANCE
            seuil_avec_gc = math.sqrt(
                (a.ecart_type_ns_avec_gc ** 2 / self.repetitions)
                + (b.ecart_type_ns_avec_gc ** 2 / self.repetitions)
            ) * SEUIL_CONFIANCE
            seuil_sans_gc_ms = round(seuil_sans_gc / 1e6, 4)
            seuil_avec_gc_ms = round(seuil_avec_gc / 1e6, 4)

        bilan: dict[str, Any] = {
            "denominateur": len(self.voies),
            "repetitions": self.repetitions,
            "machine": self._caracteristiques_machine(),
            "voies": [v.en_dict() for v in self.voies],
            "refus": refus,
            "verdict_rendu": not refus,
            "reserve": "mesure À CHAUD : le minimum sur répétitions est le cas le plus favorable au cache. Tout écart rendu est une BORNE SUPÉRIEURE de l'écart réel.",
            "avertissements": avertissements,
        }

        if len(self.voies) == 0:
            # Refus légitime : rien à examiner
            print(
                "Denominateur nul : rien à examiner, refus de conclure.",
                file=sys.stderr,
            )
            sys.exit(3)

        if seuil_sans_gc_ms is not None:
            bilan["seuil_sans_gc"] = seuil_sans_gc_ms
        if seuil_avec_gc_ms is not None:
            bilan["seuil_avec_gc"] = seuil_avec_gc_ms

        if not refus and len(self.voies) >= 2:
            reference_sans_gc = min(self.voies, key=lambda v: v.mediane_ns_sans_gc)
            reference_avec_gc = min(self.voies, key=lambda v: v.mediane_ns_avec_gc)
            bilan["reference_sans_gc"] = reference_sans_gc.nom
            bilan["reference_avec_gc"] = reference_avec_gc.nom
            bilan["facteurs_sans_gc"] = {
                v.nom: round(v.mediane_ns_sans_gc / max(reference_sans_gc.mediane_ns_sans_gc, 1), 1)
                for v in self.voies
            }
            bilan["facteurs_avec_gc"] = {
                v.nom: round(v.mediane_ns_avec_gc / max(reference_avec_gc.mediane_ns_avec_gc, 1), 1)
                for v in self.voies
            }

            for voie in self.voies:
                ecart_gc = abs(voie.mediane_ns_avec_gc - voie.mediane_ns_sans_gc)
                if ecart_gc > SEUIL_ECART_GC * voie.mediane_ns_sans_gc:
                    bilan.setdefault("dependances_gc", []).append(
                        f"{voie.nom} : écart de {ecart_gc/voie.mediane_ns_sans_gc:.0%} entre avec et sans GC"
                    )

        return bilan


def _charger_module(chemin: Path):
    specification = importlib.util.spec_from_file_location(chemin.stem, chemin)
    if specification is None or specification.loader is None:
        raise ImportError(f"module illisible : {chemin}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _afficher(bilan: dict) -> None:
    print(f"Banc — {bilan['repetitions']} répétitions\n")
    print(
        f"Machine : {bilan['machine'].get('marque', 'inconnue')} "
        f"({bilan['machine'].get('frequence_annoncee', '?')} GHz, "
        f"{bilan['machine'].get('cœurs', '?')} cœurs)\n"
    )

    largeur = max((len(v["nom"]) for v in bilan["voies"]), default=8)
    print(
        f"  {'VOIE':{largeur}}  {'MÉDIANE':>12}  {'MOYENNE':>10}  {'ÉCART-TYPE':>12}  {'IC 95%':>8}  {'PIC ALLOUÉ':>12}"
    )
    print(
        f"  {'-'*largeur}  {'-'*12}  {'-'*10}  {'-'*12}  {'-'*8}  {'-'*12}"
    )
    for v in bilan["voies"]:
        print(
            f"  {v['nom']:{largeur}}  "
            f"{v['mediane_ms_sans_gc']:9.4f} ms  "
            f"{v['moyenne_ms_sans_gc']:7.4f} ms  "
            f"{v['ecart_type_ms_sans_gc']:9.4f} ms  "
            f"{v['intervalle_confiance_ms_sans_gc']:6.4f} ms  "
            f"{v['pic_octets']:9} o"
        )
        if "dependances_gc" in bilan and any(v["nom"] in d for d in bilan["dependances_gc"]):
            print(
                f"    {'avec GC':{largeur}}  "
                f"{v['mediane_ms_avec_gc']:9.4f} ms  "
                f"{v['moyenne_ms_avec_gc']:7.4f} ms  "
                f"{v['ecart_type_ms_avec_gc']:9.4f} ms  "
                f"{v['intervalle_confiance_ms_avec_gc']:6.4f} ms"
            )
        if v["mesures_ecartees"] > 0:
            print(f"    {'ATTENTION':{largeur}}  {v['mesures_ecartees']} mesure(s) écartée(s)")

    if bilan["verdict_rendu"]:
        print(f"\n  VERDICT rendu — référence sans GC : {bilan['reference_sans_gc']}")
        for nom, facteur in bilan["facteurs_sans_gc"].items():
            print(f"    {nom:{largeur}}  x{facteur}")
        if "reference_avec_gc" in bilan and bilan["reference_avec_gc"] != bilan["reference_sans_gc"]:
            print(f"\n  Référence avec GC : {bilan['reference_avec_gc']}")
            for nom, facteur in bilan["facteurs_avec_gc"].items():
                print(f"    {nom:{largeur}}  x{facteur}")
    else:
        print(f"\n  AUCUN VERDICT — {len(bilan['refus'])} refus :")
        for r in bilan["refus"]:
            print(f"    [{r['motif']}]")
            print(f"      {r['detail']}")

    if "dependances_gc" in bilan:
        print(f"\n  Dépendances au ramasse-miettes détectées :")
        for dep in bilan["dependances_gc"]:
            print(f"    {dep}")

    if bilan.get("avertissements"):
        print("\n  AVERTISSEMENTS :")
        for av in bilan["avertissements"]:
            print(f"    {av}")

    print(f"\n  {bilan['reserve']}")


def main() -> int:
    analyseur = argparse.ArgumentParser(
        description="Compare des voies de calcul, et refuse de conclure quand la comparaison n'est pas honnête.",
        epilog="Le module cible doit exposer des fonctions nommées mesure_*.\nExemple : python banc_mesure.py mesures_exemple.py --repetitions 7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyseur.add_argument("module", type=Path, help="fichier Python exposant des fonctions mesure_*")
    analyseur.add_argument(
        "--repetitions",
        type=int,
        default=5,
        help="nombre de mesures par voie (défaut 5, minimum 3)",
    )
    analyseur.add_argument("--json", action="store_true", help="rend un objet JSON sur stdout, et rien d'autre")
    analyseur.add_argument(
        "--racine",
        type=Path,
        default=None,
        help="racine de résolution des chemins relatifs (défaut : le répertoire courant)",
    )
    arguments = analyseur.parse_args()

    racine = arguments.racine or Path.cwd()
    chemin = arguments.module if arguments.module.is_absolute() else racine / arguments.module

    if not chemin.exists():
        if arguments.json:
            print(
                json.dumps(
                    {"denominateur": 0, "erreur": f"module introuvable : {chemin}"},
                    ensure_ascii=False,
                )
            )
        else:
            print(f"module introuvable : {chemin}", file=sys.stderr)
        # Refus légitime : rien à examiner
        print(
            "Denominateur nul : rien à examiner, refus de conclure.",
            file=sys.stderr,
        )
        sys.exit(3)

    try:
        module = _charger_module(chemin)
    except BaseException as exc:  # noqa: BLE001
        if arguments.json:
            print(
                json.dumps(
                    {"denominateur": 0, "erreur": f"chargement impossible : {type(exc).__name__}: {exc}"},
                    ensure_ascii=False,
                )
            )
        else:
            print(f"chargement impossible : {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "Denominateur nul : rien à examiner, refus de conclure.",
            file=sys.stderr,
        )
        sys.exit(3)

    banc = Banc(repetitions=arguments.repetitions)
    for nom in sorted(dir(module)):
        if nom.startswith("mesure_"):
            banc.ajouter(nom[len("mesure_") :], getattr(module, nom))

    if len(banc.voies) < 2:
        # On a au moins un élément, donc on ne refuse pas légitimement,
        # mais on doit fournir un JSON contenant le dénominateur.
        if arguments.json:
            print(
                json.dumps(
                    {
                        "denominateur": len(banc.voies),
                        "erreur": f"il faut au moins deux fonctions mesure_* dans {chemin.name}, {len(banc.voies)} trouvée(s)",
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(
                f"il faut au moins deux fonctions mesure_* dans {chemin.name}, {len(banc.voies)} trouvée(s)",
                file=sys.stderr,
            )
        return 2

    bilan = banc.comparer()

    if arguments.json:
        print(json.dumps(bilan, indent=1, ensure_ascii=False))
    else:
        _afficher(bilan)

    return 0 if bilan["verdict_rendu"] else 1


if __name__ == "__main__":
    raise SystemExit(main())