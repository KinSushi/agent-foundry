"""QUESTION      La prochaine occurrence d'une tâche récurrente selon une expression cron.
MESURE        Les dates générées par un interpréteur cron en pur Python pour les expressions simples,
              ou par la bibliothèque croniter si disponible, pour les expressions complexes.
HYPOTHESES    L'expression cron est valide, et le fuseau horaire est UTC. Les champs supportent valeurs,
              listes, plages et pas. Avec croniter : alias (@daily), noms de mois/jours, 6e champ (secondes),
              caractères spéciaux L/W/#.
LIMITES       Sans croniter : ne gère pas les expressions avec secondes, années, ou caractères spéciaux L/W/#.
CONTRE-EXEMPLE L'expression `0 0 * * 1-5/2` (lundi à vendredi, tous les 2 jours) peut être mal interprétée
              sans croniter.
INVOCATION    {outil} entre "0 0 * * *" "2023-01-01T00:00:00" "2023-01-02T00:00:00" --json
DOMAINE       Planification de tâches récurrentes dans un calendrier grégorien, avec ou sans dépendances externes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional

# ------------------------------------------------------------
# Configuration d'encodage
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
# ------------------------------------------------------------

RACINE = Path(__file__).resolve().parent

class CronToolError(Exception):
    """Exception levée par le cœur de l'outil en cas d'erreur d'utilisation."""
    pass

# Tentative d'import de croniter pour le mode réel
try:
    from croniter import croniter
    CRONITER_DISPONIBLE = True
except ImportError:
    CRONITER_DISPONIBLE = False
    sys.stderr.write("mode degrade : croniter absent\n")

def _parse_iso(texte: str) -> datetime:
    """Convertit une chaîne ISO 8601 en datetime, en UTC naïf."""
    try:
        return datetime.fromisoformat(texte)
    except Exception as exc:
        raise CronToolError(f"Date invalide « {texte} »") from exc

def _parse_cron_field(champ: str, min_val: int, max_val: int) -> Set[int]:
    """Parse un champ cron (minute, heure, etc.) en un ensemble de valeurs valides."""
    if champ == "*":
        return set(range(min_val, max_val + 1))

    valeurs: Set[int] = set()
    for partie in champ.split(","):
        if "/" in partie:
            plage, pas = partie.split("/")
            pas_int = int(pas)
            if plage == "*":
                plage_vals = range(min_val, max_val + 1)
            else:
                plage_vals = _parse_range(plage, min_val, max_val)
            valeurs.update(v for i, v in enumerate(plage_vals) if i % pas_int == 0)
        else:
            valeurs.update(_parse_range(partie, min_val, max_val))
    return valeurs

def _parse_range(partie: str, min_val: int, max_val: int) -> Set[int]:
    """Parse une plage ou une valeur unique dans un champ cron."""
    if "-" in partie:
        debut, fin = partie.split("-")
        return set(range(int(debut), int(fin) + 1))
    else:
        val = int(partie)
        if not (min_val <= val <= max_val):
            raise CronToolError(f"Valeur {val} hors plage [{min_val}, {max_val}]")
        return {val}

def _parse_cron_expression(expr: str) -> Tuple[Set[int], Set[int], Set[int], Set[int], Set[int]]:
    """Parse une expression cron en cinq champs."""
    champs = expr.split()
    if len(champs) != 5:
        raise CronToolError("L'expression cron doit avoir exactement cinq champs")

    minutes = _parse_cron_field(champs[0], 0, 59)
    heures = _parse_cron_field(champs[1], 0, 23)
    jours = _parse_cron_field(champs[2], 1, 31)
    mois = _parse_cron_field(champs[3], 1, 12)
    jours_semaine = _parse_cron_field(champs[4], 0, 6)  # 0=dimanche, 6=samedi

    return minutes, heures, jours, mois, jours_semaine

def _est_date_valide(dt: datetime, jours: Set[int], mois: Set[int], jours_semaine: Set[int]) -> bool:
    """Vérifie si une date correspond aux champs cron."""
    return (
        dt.day in jours
        and dt.month in mois
        and (dt.weekday() + 1) % 7 in jours_semaine  # weekday() donne lundi=0, dimanche=6
    )

def _prochaine_occurrence(
    debut: datetime,
    minutes: Set[int],
    heures: Set[int],
    jours: Set[int],
    mois: Set[int],
    jours_semaine: Set[int],
) -> datetime:
    """Trouve la prochaine occurrence après `debut`."""
    dt = datetime(debut.year, debut.month, debut.day, debut.hour, debut.minute)
    while True:
        if dt.minute in minutes and dt.hour in heures and _est_date_valide(dt, jours, mois, jours_semaine):
            return dt
        dt += timedelta(minutes=1)

def _prochaine_occurrence_croniter(expr: str, debut: datetime) -> datetime:
    """Trouve la prochaine occurrence après `debut` en utilisant croniter."""
    return croniter(expr, debut).get_next(datetime)

def _precedente_occurrence_croniter(expr: str, reference: datetime) -> datetime:
    """Trouve l'occurrence précédente avant `reference` en utilisant croniter."""
    return croniter(expr, reference).get_prev(datetime)

def generate_between(expr: str, debut_iso: str, fin_iso: str) -> List[str]:
    """Renvoie la liste des occurrences ISO entre deux dates (incluses si correspondantes)."""
    debut = _parse_iso(debut_iso)
    fin = _parse_iso(fin_iso)
    if debut > fin:
        raise CronToolError("La date de début est postérieure à la date de fin.")

    if CRONITER_DISPONIBLE and croniter.is_valid(expr):
        occurrences: List[str] = []
        prochaine = _prochaine_occurrence_croniter(expr, debut)
        while prochaine <= fin:
            occurrences.append(prochaine.isoformat())
            prochaine = _prochaine_occurrence_croniter(expr, prochaine + timedelta(minutes=1))
        return occurrences
    else:
        minutes, heures, jours, mois, jours_semaine = _parse_cron_expression(expr)
        occurrences: List[str] = []
        prochaine = _prochaine_occurrence(debut, minutes, heures, jours, mois, jours_semaine)
        while prochaine <= fin:
            occurrences.append(prochaine.isoformat())
            prochaine = _prochaine_occurrence(prochaine + timedelta(minutes=1), minutes, heures, jours, mois, jours_semaine)
        return occurrences

def verify_match(expr: str, date_iso: str) -> bool:
    """Retourne True si la date correspond à l'expression cron."""
    dt = _parse_iso(date_iso)
    if CRONITER_DISPONIBLE:
        return croniter.match(expr, dt)
    else:
        minutes, heures, jours, mois, jours_semaine = _parse_cron_expression(expr)
        return (
            dt.minute in minutes
            and dt.hour in heures
            and _est_date_valide(dt, jours, mois, jours_semaine)
        )

def previous_occurrence(expr: str, reference_iso: str) -> str:
    """Calcule l'occurrence précédente à la date de référence."""
    ref = _parse_iso(reference_iso)
    if CRONITER_DISPONIBLE and croniter.is_valid(expr):
        return _precedente_occurrence_croniter(expr, ref).isoformat()
    else:
        minutes, heures, jours, mois, jours_semaine = _parse_cron_expression(expr)
        dt = datetime(ref.year, ref.month, ref.day, ref.hour, ref.minute)
        while True:
            dt -= timedelta(minutes=1)
            if dt.minute in minutes and dt.hour in heures and _est_date_valide(dt, jours, mois, jours_semaine):
                return dt.isoformat()

def _construire_json_base(denominateur: int, examines: List[str], moteur: str) -> dict:
    """Construit le dictionnaire de base pour la sortie JSON."""
    examines_tronques = len(examines) > 200
    examines_limited = examines[:200]
    return {
        "denominateur": denominateur,
        "examines": examines_limited,
        "examines_tronques": examines_tronques,
        "moteur": moteur,
    }

def _sortie_json(denominateur: int, examines: List[str], moteur: str, **kwargs) -> None:
    """Génère la sortie JSON complète sur stdout."""
    base = _construire_json_base(denominateur, examines, moteur)
    base.update(kwargs)
    json.dump(base, sys.stdout, ensure_ascii=False)

def _refus(raison: str, json_mode: bool) -> int:
    """Applique le protocole de refus."""
    sys.stderr.write(f"denominateur {raison}\n")
    if json_mode:
        _sortie_json(0, [], "stdlib", erreur=raison)
    return 3

def main() -> int:
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Produit la sortie au format JSON unique.",
    )
    parent_parser.add_argument(
        "--racine",
        type=str,
        default=argparse.SUPPRESS,
        help="Chemin racine à utiliser à la place de la valeur par défaut.",
    )

    parser = argparse.ArgumentParser(
        description="Outil de calcul d'occurrences cron.",
        parents=[parent_parser],
        add_help=False
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # sous‑commande « entre »
    parser_entre = subparsers.add_parser(
        "entre",
        help="Génère les occurrences entre deux dates.",
        parents=[parent_parser],
        description="Génère toutes les occurrences entre deux dates ISO 8601.",
        epilog='Exemple : {outil} entre "0 0 * * *" "2023-01-01T00:00:00" "2023-01-03T00:00:00"',
    )
    parser_entre.add_argument("expression_cron", help="Expression cron.")
    parser_entre.add_argument("debut", help="Date de début ISO 8601.")
    parser_entre.add_argument("fin", help="Date de fin ISO 8601.")

    # sous‑commande « verifie »
    parser_verifie = subparsers.add_parser(
        "verifie",
        help="Vérifie si une date correspond à l'expression cron.",
        parents=[parent_parser],
        description="Indique si la date fournie correspond à l'expression cron.",
        epilog='Exemple : {outil} verifie "0 0 * * 1" "2023-01-02T00:00:00"',
    )
    parser_verifie.add_argument("expression_cron", help="Expression cron.")
    parser_verifie.add_argument("date", help="Date ISO 8601 à vérifier.")

    # sous‑commande « precedente »
    parser_precedente = subparsers.add_parser(
        "precedente",
        help="Calcule l'occurrence précédente.",
        parents=[parent_parser],
        description="Renvoie l'occurrence cron immédiatement antérieure à la date donnée.",
        epilog='Exemple : {outil} precedente "0 0 * * *" "2023-01-02T00:00:00"',
    )
    parser_precedente.add_argument("expression_cron", help="Expression cron.")
    parser_precedente.add_argument(
        "date_reference", help="Date de référence ISO 8601."
    )

    # Vérification de --help avant tout travail
    if "--help" in sys.argv or "-h" in sys.argv:
        parser.print_help()
        return 0

    args = parser.parse_args()
    json_mode = getattr(args, "json", False)
    racine = Path(args.racine) if hasattr(args, "racine") else RACINE
    moteur = "croniter" if CRONITER_DISPONIBLE else "stdlib"

    try:
        if args.commande == "entre":
            if not all([args.expression_cron, args.debut, args.fin]):
                return _refus("aucun argument valide", json_mode)
            dates = generate_between(
                args.expression_cron, args.debut, args.fin
            )
            if json_mode:
                _sortie_json(len(dates), dates, moteur, occurrences=dates)
            else:
                for d in dates:
                    print(d)
            return 0

        if args.commande == "verifie":
            if not all([args.expression_cron, args.date]):
                return _refus("aucun argument valide", json_mode)
            match = verify_match(args.expression_cron, args.date)
            if json_mode:
                _sortie_json(1, [args.date], moteur, match=match)
            else:
                print("oui" if match else "non")
            return 0

        if args.commande == "precedente":
            if not all([args.expression_cron, args.date_reference]):
                return _refus("aucun argument valide", json_mode)
            prev = previous_occurrence(args.expression_cron, args.date_reference)
            if json_mode:
                _sortie_json(1, [args.date_reference], moteur, precedente=prev)
            else:
                print(prev)
            return 0

        return _refus("commande inconnue", json_mode)

    except CronToolError as err:
        sys.stderr.write(f"Erreur : {err}\n")
        if json_mode:
            _sortie_json(1, [], moteur, erreur=str(err))
        return 1
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"Erreur interne : {exc}\n")
        return _refus("erreur interne", json_mode)

if __name__ == "__main__":
    raise SystemExit(main())