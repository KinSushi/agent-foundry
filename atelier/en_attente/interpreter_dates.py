"""Une date écrite par un humain se lit souvent de plusieurs façons, et les bibliothèques
choisissent sans le dire : dans cette session, dateparser 1.4.3 a rendu « 03/04/2026 » comme
2026-03-04 sans signaler la lecture 3 avril, et a lu « demain à 15h » (référence 2026-10-02 10:00)
comme 2026-10-04 01:00 — quinze heures ajoutées au lieu d'une heure fixée ; « mardi prochain » et
« next Tuesday » lui ont rendu None. Cet outil donne chaque lecture possible et dit laquelle il retient.

QUESTION
    Quelle date, quel instant ou quelle période exacte désigne cette expression (français ou
    anglais), et est-elle ambiguë ?
MESURE
    Analyseur à règles en stdlib : ISO 8601 (date, instant, semaine, ordinal, année-mois),
    formats numériques j/m/a et m/j/a, noms de mois et de jours français et anglais, mots
    relatifs (demain, après-demain, hier…), décalages (« dans 3 semaines », « il y a 2 jours »,
    « 3 days ago »), jours de semaine qualifiés (« mardi prochain », « last Friday »), périodes
    (« la semaine prochaine », « ce week-end », « fin du mois »), heures (15h30, 3pm, midi,
    minuit) et fuseaux (décalage, UTC, abréviations, nom de zone). Chaque lecture possible est
    rendue en ISO 8601 avec la règle qui l'a produite ; une expression qui en admet plusieurs
    est déclarée ambiguë. Les décalages se calculent depuis une date de référence explicite
    (--reference ; défaut : horloge système au moment de l'appel, rapportée dans la sortie).
    Avec --comparer, dateparser et python-dateutil (si installées) lisent la même expression
    et l'outil dit si leur réponse est l'une de ses lectures.
HYPOTHÈSES
    Calendrier grégorien proleptique ; semaine du lundi au dimanche (ISO 8601) ; « prochain »
    peut désigner l'occurrence suivante ou celle de la semaine suivante, les deux sont rendues
    quand elles diffèrent ; une année à deux chiffres tombe dans la fenêtre de cent ans centrée
    sur l'année de référence ; une date sans année désigne l'année de référence, ou la
    suivante si elle est déjà passée (les deux lectures sont alors rendues).
LIMITES
    Pas de jours ouvrés (voir calculer_jours_ouvres), pas d'expressions composées (« le
    premier lundi de mars », « dans 2 semaines et 3 jours »), pas de moments flous (« ce soir »,
    « en fin de matinée »), pas d'autres langues que le français et l'anglais. Les
    abréviations de fuseau sont prises à décalage fixe (CET = +01:00 même en été). L'ordre
    année/jour/mois n'est jamais envisagé. --fuseau et les noms de zone lisent la base de
    fuseaux du système via zoneinfo.
CONTRE-EXEMPLES
    « 8 days » et « dans 8 jours » ne sont pas traités pareil : seul le français est déclaré
    ambigu (8 jours littéraux ou une semaine), alors qu'un anglophone peut aussi entendre une
    semaine. « mar 4 » n'est pas reconnu : « mar » est pris pour mars (March) et jamais pour
    mardi. « 1/2 » est lu comme une date (1er février ou 2 janvier), jamais comme une fraction.
INVOCATION
    {outil} "03/04/2026" "mardi prochain" "demain à 15h" --reference "2026-10-02 10:00" --json

DOMAINE
    Expressions de date isolées (une par argument ou par ligne de --fichier), en français ou en
    anglais, pour des échéances, plannings et journaux entre les années 1 et 9999.
"""

from __future__ import annotations

import argparse
import calendar
import importlib.util
import json
import re
import sys
import unicodedata
import warnings
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from importlib import metadata
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

TITRE_QUESTION = "QUESTION"
TITRE_MESURE = "MESURE"
TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_LIMITES = "LIMITES"
TITRE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
TITRE_INVOCATION = "INVOCATION"
TITRE_DOMAINE = "DOMAINE"
TITRES_CONTRAT = (TITRE_QUESTION, TITRE_MESURE, TITRE_HYPOTHESES, TITRE_LIMITES,
                  TITRE_CONTRE_EXEMPLES, TITRE_INVOCATION, TITRE_DOMAINE)

MOTEUR = "stdlib"
TEMOINS_OPTIONNELS = (("dateparser", "dateparser"), ("dateutil", "python-dateutil"))
LIMITE_EXAMINES = 50
LIMITE_LIGNES = 10_000
LIMITE_OCTETS = 1_048_576
LIMITE_LECTURES = 8
LIMITE_LONGUEUR = 200

STATUT_EXACTE = "exacte"
STATUT_AMBIGUE = "ambigue"
STATUT_INCOHERENTE = "incoherente"
STATUT_NON_INTERPRETEE = "non_interpretee"

ETIQUETTES = ("jma", "mja", "futur", "passe", "occurrence-suivante", "semaine-suivante",
              "occurrence-precedente", "semaine-precedente", "semaine-en-cours", "meme-jour",
              "litteral", "usage", "premiere", "seconde", "debut-du-jour", "fin-du-jour")

NOMS_JOURS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

MOIS = MappingProxyType({
    "janvier": 1, "janv": 1, "jan": 1, "january": 1,
    "fevrier": 2, "fevr": 2, "fev": 2, "february": 2, "feb": 2,
    "mars": 3, "mar": 3, "march": 3,
    "avril": 4, "avr": 4, "april": 4, "apr": 4,
    "mai": 5, "may": 5,
    "juin": 6, "june": 6, "jun": 6,
    "juillet": 7, "juil": 7, "july": 7, "jul": 7,
    "aout": 8, "august": 8, "aug": 8,
    "septembre": 9, "sept": 9, "sep": 9, "september": 9,
    "octobre": 10, "oct": 10, "october": 10,
    "novembre": 11, "nov": 11, "november": 11,
    "decembre": 12, "dec": 12, "december": 12,
})
JOURS = MappingProxyType({
    "lundi": 0, "lun": 0, "monday": 0, "mon": 0,
    "mardi": 1, "tuesday": 1, "tue": 1, "tues": 1,
    "mercredi": 2, "mer": 2, "wednesday": 2, "wed": 2,
    "jeudi": 3, "jeu": 3, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "vendredi": 4, "ven": 4, "friday": 4, "fri": 4,
    "samedi": 5, "sam": 5, "saturday": 5, "sat": 5,
    "dimanche": 6, "dim": 6, "sunday": 6, "sun": 6,
})
NOMBRES = MappingProxyType({
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
    "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14,
    "quinze": 15, "seize": 16, "vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50,
    "soixante": 60, "cent": 100,
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "hundred": 100,
})
# unité -> (genre, quantité) ; genre : jours, mois ou secondes
UNITES = MappingProxyType({
    "jour": ("jours", 1), "jours": ("jours", 1), "j": ("jours", 1),
    "day": ("jours", 1), "days": ("jours", 1), "d": ("jours", 1),
    "semaine": ("jours", 7), "semaines": ("jours", 7), "sem": ("jours", 7),
    "week": ("jours", 7), "weeks": ("jours", 7), "wk": ("jours", 7), "wks": ("jours", 7),
    "fortnight": ("jours", 14), "fortnights": ("jours", 14),
    "mois": ("mois", 1), "month": ("mois", 1), "months": ("mois", 1),
    "an": ("mois", 12), "ans": ("mois", 12), "annee": ("mois", 12), "annees": ("mois", 12),
    "year": ("mois", 12), "years": ("mois", 12), "yr": ("mois", 12), "yrs": ("mois", 12),
    "heure": ("secondes", 3600), "heures": ("secondes", 3600), "h": ("secondes", 3600),
    "hour": ("secondes", 3600), "hours": ("secondes", 3600), "hr": ("secondes", 3600),
    "hrs": ("secondes", 3600),
    "minute": ("secondes", 60), "minutes": ("secondes", 60), "min": ("secondes", 60),
    "mins": ("secondes", 60), "mn": ("secondes", 60),
    "seconde": ("secondes", 1), "secondes": ("secondes", 1), "second": ("secondes", 1),
    "seconds": ("secondes", 1), "sec": ("secondes", 1), "secs": ("secondes", 1),
})
UNITES_JOURS_FR = frozenset({"jour", "jours", "j"})
# Usage français : « dans huit jours » = une semaine, « dans quinze jours » = deux semaines.
JOURS_USAGE_FR = MappingProxyType({8: 7, 15: 14})
MOTS_JOUR = MappingProxyType({
    "aujourd'hui": 0, "aujourdhui": 0, "today": 0, "ce jour": 0,
    "demain": 1, "tomorrow": 1, "hier": -1, "yesterday": -1,
    "apres-demain": 2, "apres demain": 2, "day after tomorrow": 2,
    "avant-hier": -2, "avant hier": -2, "day before yesterday": -2,
})
MOTS_MAINTENANT = frozenset({"maintenant", "now", "right now", "a l'instant", "tout de suite"})
# abréviation -> ((décalage en minutes, région), ...) ; plusieurs entrées = ambiguë
FUSEAUX_ABREGES = MappingProxyType({
    "utc": ((0, "UTC"),), "gmt": ((0, "GMT"),), "z": ((0, "UTC"),),
    "wet": ((0, "Europe de l'Ouest"),), "west": ((60, "Europe de l'Ouest, été"),),
    "cet": ((60, "Europe centrale"),), "cest": ((120, "Europe centrale, été"),),
    "eet": ((120, "Europe de l'Est"),), "eest": ((180, "Europe de l'Est, été"),),
    "est": ((-300, "Amérique, Est"),), "edt": ((-240, "Amérique, Est, été"),),
    "cdt": ((-300, "Amérique, Centre, été"),), "mst": ((-420, "Amérique, Rocheuses"),),
    "mdt": ((-360, "Amérique, Rocheuses, été"),), "pst": ((-480, "Amérique, Pacifique"),),
    "pdt": ((-420, "Amérique, Pacifique, été"),), "jst": ((540, "Japon"),),
    "aest": ((600, "Australie, Est"),), "aedt": ((660, "Australie, Est, été"),),
    "cst": ((-360, "Amérique, Centre"), (480, "Chine")),
    "ist": ((330, "Inde"), (60, "Irlande, été"), (120, "Israël")),
    "bst": ((60, "Royaume-Uni, été"), (360, "Bangladesh")),
})

_ALT_MOIS = "|".join(sorted(MOIS, key=len, reverse=True))
_ALT_JOURS = "|".join(sorted(JOURS, key=len, reverse=True))
_ALT_NOMBRES = "|".join(sorted(NOMBRES, key=len, reverse=True))
_ALT_UNITES = "|".join(sorted(UNITES, key=len, reverse=True))
_NOMBRE = rf"(?P<n>\d{{1,4}}|{_ALT_NOMBRES})"
_UNITE = rf"(?P<u>{_ALT_UNITES})"
_ORDINAL = r"(?:er|re|e|eme|st|nd|rd|th)?"

MOTIFS_DECALAGE = (
    (re.compile(rf"^(?:dans|in|d'ici|sous|within)\s+{_NOMBRE}\s*{_UNITE}$"), 1),
    (re.compile(rf"^{_NOMBRE}\s*{_UNITE}\s+(?:from\s+now|later|plus\s+tard|hence)$"), 1),
    (re.compile(rf"^(?:il\s+y\s+a|y\s+a)\s+{_NOMBRE}\s*{_UNITE}$"), -1),
    (re.compile(rf"^{_NOMBRE}\s*{_UNITE}\s+(?:ago|plus\s+tot|earlier)$"), -1),
)
MOTIFS_JOUR_SEMAINE = (
    (re.compile(rf"^(?P<j>{_ALT_JOURS})$"), "nu"),
    (re.compile(rf"^(?:ce|this|coming)\s+(?P<j>{_ALT_JOURS})$"), "ce"),
    (re.compile(rf"^(?:next\s+(?P<j>{_ALT_JOURS})|(?P<k>{_ALT_JOURS})\s+(?:prochain|suivant|qui\s+vient))$"),
     "prochain"),
    (re.compile(rf"^(?:(?:last|past)\s+(?P<j>{_ALT_JOURS})|(?P<k>{_ALT_JOURS})\s+(?:dernier|passe))$"),
     "dernier"),
)
MOTIFS_PERIODE = (
    (re.compile(r"^(?:semaine\s+(?:prochaine|suivante|qui\s+vient)|next\s+week)$"), ("semaine", 1)),
    (re.compile(r"^(?:cette\s+semaine|this\s+week)$"), ("semaine", 0)),
    (re.compile(r"^(?:semaine\s+(?:derniere|passee|precedente)|last\s+week)$"), ("semaine", -1)),
    (re.compile(r"^(?:mois\s+(?:prochain|suivant)|next\s+month)$"), ("mois", 1)),
    (re.compile(r"^(?:ce\s+mois(?:-ci)?|this\s+month)$"), ("mois", 0)),
    (re.compile(r"^(?:mois\s+(?:dernier|passe|precedent)|last\s+month)$"), ("mois", -1)),
    (re.compile(r"^(?:(?:annee|an)\s+(?:prochaine?|suivante?)|next\s+year)$"), ("annee", 1)),
    (re.compile(r"^(?:cette\s+annee|this\s+year)$"), ("annee", 0)),
    (re.compile(r"^(?:(?:annee|an)\s+(?:derniere?|passee?|precedente?)|last\s+year)$"), ("annee", -1)),
    (re.compile(r"^(?:ce\s+week-?end|this\s+weekend|week-?end)$"), ("weekend", 0)),
    (re.compile(r"^(?:week-?end\s+(?:prochain|suivant)|next\s+weekend)$"), ("weekend", 1)),
    (re.compile(r"^(?:week-?end\s+(?:dernier|passe)|last\s+weekend)$"), ("weekend", -1)),
    (re.compile(r"^(?:(?:a\s+la\s+)?fin\s+(?:du|de)\s+mois|end\s+of\s+(?:the\s+)?month)$"), ("fin_mois", 0)),
    (re.compile(r"^(?:fin\s+du\s+mois\s+prochain|end\s+of\s+next\s+month)$"), ("fin_mois", 1)),
    (re.compile(r"^(?:fin\s+(?:d'annee|de\s+l'annee)|end\s+of\s+(?:the\s+)?year)$"), ("fin_annee", 0)),
)
NUMERIQUE = re.compile(r"^(\d{1,4})([/.\-])(\d{1,2})\2(\d{1,4})$")
NUMERIQUE_SANS_ANNEE = re.compile(r"^(\d{1,2})([/.])(\d{1,2})$")
JOUR_PUIS_MOIS = re.compile(
    rf"^(?:(?P<js>{_ALT_JOURS})\s+)?(?P<j>\d{{1,2}}){_ORDINAL}\s+(?:de\s+|d'|of\s+)?"
    rf"(?P<m>{_ALT_MOIS})\.?(?:\s+(?P<a>\d{{4}}|\d{{2}}))?$")
MOIS_PUIS_JOUR = re.compile(
    rf"^(?:(?P<js>{_ALT_JOURS})\s+)?(?P<m>{_ALT_MOIS})\.?\s+(?:the\s+)?(?P<j>\d{{1,2}})"
    rf"(?:st|nd|rd|th)?(?:\s+(?P<a>\d{{4}}))?$")
MOIS_SEUL = re.compile(rf"^(?:en\s+|in\s+)?(?P<m>{_ALT_MOIS})\.?(?:\s+(?:de\s+)?(?P<a>\d{{4}}))?$")
JOUR_DU_MOIS = re.compile(rf"^(?P<j>\d{{1,2}}){_ORDINAL}$")
ANNEE_SEULE = re.compile(r"^(?:en\s+|in\s+)?(?P<a>\d{4})$")
ARTICLE_INITIAL = re.compile(r"^(?:le\s+|la\s+|l'|the\s+|on\s+)")
HEURE = re.compile(
    r"(?:^|(?<=\s))(?:(?:a|at|vers|@|des)\s+)?"
    r"(?:(?P<h1>\d{1,2})\s*(?:h|heures?)(?:\s*(?P<m1>\d{2}))?"
    r"|(?P<h2>\d{1,2}):(?P<m2>\d{2})(?::(?P<s2>\d{2}))?(?:\s*(?P<ap2>[ap]\.?m\.?))?"
    r"|(?P<h3>\d{1,2})\s*(?P<ap3>[ap]\.?m\.?)"
    r"|(?P<mot>midi|minuit|noon|midnight))"
    r"(?:\s*(?P<tz>(?:utc|gmt)?\s*[+-]\d{1,2}(?::?\d{2})?|[a-z]{1,5}|[a-z]+/[a-z_]+(?:/[a-z_]+)?))?"
    r"(?=\s|$)")
FUSEAU_DECALAGE = re.compile(r"^(?:utc|gmt)?\s*(?P<s>[+-])(?P<h>\d{1,2})(?::?(?P<m>\d{2}))?$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{8}$|^\d{4}-?W\d{2}-?\d$")
ISO_MOIS = re.compile(r"^(\d{4})-(\d{2})$")
ISO_SEMAINE = re.compile(r"^(\d{4})-?W(\d{2})$")
ISO_ORDINAL = re.compile(r"^(\d{4})-?(\d{3})$")
ISO_INSTANT = re.compile(
    r"^\d{4}-?\d{2}-?\d{2}[T ]\d{2}(?::?\d{2}(?::?\d{2}(?:[.,]\d+)?)?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?$")


class ErreurUsage(Exception):
    """Entrée invalide : l'outil rend le code 2."""


@dataclass(frozen=True)
class Lecture:
    """Une lecture possible : un instant, un jour, ou une période [debut, fin]."""
    debut: date
    fin: date | None
    precision: str
    regle: str
    etiquettes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Contexte:
    """Référence des calculs relatifs et fuseau de sortie."""
    reference: datetime
    reference_sans_heure: bool
    fuseau: tzinfo | None


def lire_contrat() -> dict[str, str]:
    """Découpe la docstring du module en sections du contrat de mesure."""
    sections: dict[str, list[str]] = {}
    courant = ""
    for ligne in (__doc__ or "").splitlines():
        if ligne.strip() in TITRES_CONTRAT and not ligne.startswith(" "):
            courant = ligne.strip()
            sections[courant] = []
        elif courant:
            sections[courant].append(ligne.strip())
    return {cle: " ".join(m for m in val if m) for cle, val in sections.items()}


def normaliser(expression: str) -> str:
    """Minuscules, sans accents, apostrophes et espaces unifiés, article initial retiré."""
    texte = unicodedata.normalize("NFKC", expression).replace("’", "'").replace("‘", "'")
    texte = "".join(c for c in unicodedata.normalize("NFD", texte.lower())
                    if not unicodedata.combining(c))
    texte = re.sub(r",(?=\s|$)", " ", texte)
    texte = re.sub(r"\s+", " ", texte).strip().rstrip(".!?;").strip()
    return ARTICLE_INITIAL.sub("", texte, count=1)


def formater_instant(valeur: date) -> str:
    """ISO 8601 : AAAA-MM-JJ, ou AAAA-MM-JJTHH:MM:SS[±HH:MM]."""
    if isinstance(valeur, datetime):
        return valeur.isoformat(timespec="seconds")
    return valeur.isoformat()


def iso_lecture(lecture: Lecture) -> str:
    """Instant, jour, ou intervalle ISO 8601 « debut/fin »."""
    if lecture.fin is None:
        return formater_instant(lecture.debut)
    return f"{formater_instant(lecture.debut)}/{formater_instant(lecture.fin)}"


def jour_de(valeur: date) -> date:
    """Partie date d'une date ou d'un instant."""
    return valeur.date() if isinstance(valeur, datetime) else valeur


def lire_nombre(texte: str) -> int:
    """Entier écrit en chiffres ou en toutes lettres (français, anglais)."""
    return int(texte) if texte.isdigit() else NOMBRES[texte]


def ajouter_mois(depart: date, mois: int) -> tuple[date, bool]:
    """Ajoute des mois calendaires ; vrai si le jour a dû être ramené en fin de mois."""
    total = depart.year * 12 + depart.month - 1 + mois
    annee, mois_ = divmod(total, 12)
    if not 1 <= annee <= 9999:
        raise ValueError("année hors de 1..9999")
    dernier = calendar.monthrange(annee, mois_ + 1)[1]
    return depart.replace(year=annee, month=mois_ + 1, day=min(depart.day, dernier)), depart.day > dernier


def annee_deux_chiffres(valeur: int, reference: int) -> int:
    """Siècle choisi pour que l'année tombe à moins de 50 ans de la référence."""
    base = reference - 50
    annee = base - base % 100 + valeur
    return annee + 100 if annee < base else annee


def construire_date(annee: int, mois: int, jour: int) -> date | None:
    """Date si elle existe, sinon None."""
    try:
        return date(annee, mois, jour)
    except ValueError:
        return None


def lundi_de(jour: date) -> date:
    """Lundi de la semaine ISO du jour."""
    return jour - timedelta(days=jour.weekday())


# --------------------------------------------------------------------------- ISO 8601

def lire_iso(brut: str) -> list[Lecture] | None:
    """Formes ISO 8601 : date, instant, semaine, ordinal, année-mois."""
    texte = unicodedata.normalize("NFKC", brut).strip().upper()
    if ISO_INSTANT.match(texte):
        instant = datetime.fromisoformat(texte.replace(",", "."))
        precision = "seconde" if texte.count(":") >= 2 or re.search(r"T\d{6}", texte) else "minute"
        return [Lecture(instant, None, precision, "ISO 8601 date et heure")]
    if ISO_DATE.match(texte):
        return [Lecture(date.fromisoformat(texte), None, "jour", "ISO 8601 date")]
    return lire_iso_periode(texte)


def lire_iso_periode(texte: str) -> list[Lecture] | None:
    """Formes ISO 8601 réduites : AAAA-MM, AAAA-Www, AAAA-DDD."""
    if m := ISO_MOIS.match(texte):
        debut = date(int(m[1]), int(m[2]), 1)
        fin = debut.replace(day=calendar.monthrange(debut.year, debut.month)[1])
        return [Lecture(debut, fin, "mois", "ISO 8601 année-mois")]
    if m := ISO_SEMAINE.match(texte):
        debut = date.fromisocalendar(int(m[1]), int(m[2]), 1)
        return [Lecture(debut, debut + timedelta(days=6), "semaine", "ISO 8601 semaine")]
    if m := ISO_ORDINAL.match(texte):
        quantieme = int(m[2])
        if not 1 <= quantieme <= (366 if calendar.isleap(int(m[1])) else 365):
            raise ValueError(f"quantième {quantieme} hors de l'année {m[1]}")
        jour = date(int(m[1]), 1, 1) + timedelta(days=quantieme - 1)
        return [Lecture(jour, None, "jour", "ISO 8601 date ordinale")]
    return None


# --------------------------------------------------------------------------- règles relatives

def regle_mot_du_jour(texte: str, ctx: Contexte) -> list[Lecture] | None:
    """aujourd'hui, demain, hier, après-demain, maintenant…"""
    if texte in MOTS_MAINTENANT:
        return [Lecture(ctx.reference, None, "seconde", "instant de référence")]
    if texte not in MOTS_JOUR:
        return None
    ecart = MOTS_JOUR[texte]
    jour = ctx.reference.date() + timedelta(days=ecart)
    return [Lecture(jour, None, "jour", f"référence {ecart:+d} jour(s)")]


def regle_decalage(texte: str, ctx: Contexte) -> list[Lecture] | None:
    """« dans 3 semaines », « il y a 2 jours », « 3 days ago », « in 2 hours »."""
    for motif, sens in MOTIFS_DECALAGE:
        if m := motif.match(texte):
            return decaler(ctx, lire_nombre(m["n"]) * sens, m["u"])
    return None


def decaler(ctx: Contexte, nombre: int, unite: str) -> list[Lecture]:
    """Applique un décalage signé exprimé dans une unité."""
    genre, quantite = UNITES[unite]
    if genre == "secondes":
        instant = ctx.reference + timedelta(seconds=nombre * quantite)
        regle = f"référence {nombre:+d} {unite}"
        if ctx.reference_sans_heure:
            regle += " (référence sans heure : minuit supposé)"
        return [Lecture(instant, None, "minute" if quantite >= 60 else "seconde", regle)]
    if genre == "mois":
        jour, ramene = ajouter_mois(ctx.reference.date(), nombre * quantite)
        regle = f"référence {nombre * quantite:+d} mois calendaire(s)"
        return [Lecture(jour, None, "jour", regle + (" ; jour ramené en fin de mois" if ramene else ""))]
    jours = nombre * quantite
    lectures = [Lecture(ctx.reference.date() + timedelta(days=jours), None, "jour",
                        f"référence {jours:+d} jour(s)", frozenset({"litteral"}))]
    if unite in UNITES_JOURS_FR and abs(nombre) in JOURS_USAGE_FR:
        usage = JOURS_USAGE_FR[abs(nombre)] * (1 if nombre > 0 else -1)
        lectures.append(Lecture(ctx.reference.date() + timedelta(days=usage), None, "jour",
                                f"usage français : {abs(nombre)} jours = {abs(usage) // 7} semaine(s)",
                                frozenset({"usage"})))
    return lectures


def regle_jour_semaine(texte: str, ctx: Contexte) -> list[Lecture] | None:
    """« mardi », « ce mardi », « mardi prochain », « last Friday »…"""
    for motif, mode in MOTIFS_JOUR_SEMAINE:
        if m := motif.match(texte):
            cible = JOURS[m["j"] or m.groupdict().get("k")]
            return lectures_jour_semaine(ctx.reference.date(), cible, mode)
    return None


def lectures_jour_semaine(ref: date, cible: int, mode: str) -> list[Lecture]:
    """Les deux sens possibles d'un jour de semaine qualifié ; un seul s'ils coïncident."""
    ecart = (cible - ref.weekday()) % 7
    nom = NOMS_JOURS_FR[cible]
    if mode == "nu":
        options = [(ref + timedelta(days=ecart), f"{nom} à venir (aujourd'hui compris)", "meme-jour"),
                   (ref + timedelta(days=ecart or 7), f"{nom} à venir (aujourd'hui exclu)",
                    "occurrence-suivante")]
    elif mode == "ce":
        options = [(lundi_de(ref) + timedelta(days=cible), f"{nom} de la semaine en cours",
                    "semaine-en-cours"),
                   (ref + timedelta(days=ecart), f"{nom} à venir", "occurrence-suivante")]
    elif mode == "prochain":
        options = [(ref + timedelta(days=ecart or 7), f"prochain {nom} (occurrence suivante)",
                    "occurrence-suivante"),
                   (lundi_de(ref) + timedelta(days=7 + cible), f"{nom} de la semaine suivante",
                    "semaine-suivante")]
    else:
        recul = (ref.weekday() - cible) % 7 or 7
        options = [(ref - timedelta(days=recul), f"dernier {nom} écoulé", "occurrence-precedente"),
                   (lundi_de(ref) - timedelta(days=7 - cible), f"{nom} de la semaine précédente",
                    "semaine-precedente")]
    return fusionner_options(options, "jour")


def fusionner_options(options: list[tuple[date, str, str]], precision: str) -> list[Lecture]:
    """Une lecture par valeur distincte ; les étiquettes de valeurs égales sont réunies."""
    par_valeur: dict[date, Lecture] = {}
    for valeur, regle, etiquette in options:
        if valeur in par_valeur:
            ancienne = par_valeur[valeur]
            par_valeur[valeur] = replace(ancienne, etiquettes=ancienne.etiquettes | {etiquette})
        else:
            par_valeur[valeur] = Lecture(valeur, None, precision, regle, frozenset({etiquette}))
    return list(par_valeur.values())


def regle_periode(texte: str, ctx: Contexte) -> list[Lecture] | None:
    """Semaine, mois, année, week-end prochains ou derniers ; fin du mois, fin d'année."""
    for motif, (unite, ecart) in MOTIFS_PERIODE:
        if motif.match(texte):
            return lectures_periode(ctx.reference.date(), unite, ecart)
    return None


def lectures_periode(ref: date, unite: str, ecart: int) -> list[Lecture]:
    """Bornes de la période désignée."""
    if unite == "semaine":
        debut = lundi_de(ref) + timedelta(days=7 * ecart)
        return [Lecture(debut, debut + timedelta(days=6), "semaine", f"semaine ISO {ecart:+d}")]
    if unite in ("mois", "fin_mois"):
        debut, _ = ajouter_mois(ref.replace(day=1), ecart)
        fin = debut.replace(day=calendar.monthrange(debut.year, debut.month)[1])
        if unite == "fin_mois":
            return [Lecture(fin, None, "jour", "fin du mois = son dernier jour")]
        return [Lecture(debut, fin, "mois", f"mois calendaire {ecart:+d}")]
    if unite in ("annee", "fin_annee"):
        annee = ref.year + ecart
        if unite == "fin_annee":
            return [Lecture(date(annee, 12, 31), None, "jour", "fin d'année = 31 décembre")]
        return [Lecture(date(annee, 1, 1), date(annee, 12, 31), "annee", f"année civile {ecart:+d}")]
    return lectures_weekend(ref, ecart)


def lectures_weekend(ref: date, ecart: int) -> list[Lecture]:
    """Samedi-dimanche : en cours ou à venir, suivant, ou précédent."""
    samedi_semaine = lundi_de(ref) + timedelta(days=5)
    if ecart == 0:
        options = [(samedi_semaine, "week-end de la semaine en cours", "semaine-en-cours")]
    elif ecart > 0:
        avant = (5 - ref.weekday()) % 7 or 7
        options = [(ref + timedelta(days=avant), "prochain week-end à venir", "occurrence-suivante"),
                   (samedi_semaine + timedelta(days=7), "week-end de la semaine suivante",
                    "semaine-suivante")]
    else:
        recul = (ref.weekday() - 5) % 7 or 7
        options = [(ref - timedelta(days=recul), "dernier week-end commencé", "occurrence-precedente"),
                   (samedi_semaine - timedelta(days=7), "week-end de la semaine précédente",
                    "semaine-precedente")]
    return [replace(lec, fin=lec.debut + timedelta(days=1), precision="week-end")
            for lec in fusionner_options(options, "week-end")]


# --------------------------------------------------------------------------- règles absolues

def regle_numerique(texte: str, ctx: Contexte) -> list[Lecture] | None:
    """03/04/2026, 3.4.26, 2026/04/03, 03-04-2026, 03/04."""
    if m := NUMERIQUE.match(texte):
        a, sep, b, c = m[1], m[2], m[3], m[4]
        if len(a) == 4:
            jour = construire_date(int(a), int(b), int(c))
            return [Lecture(jour, None, "jour", "numérique année/mois/jour")] if jour else []
        if len(c) not in (2, 4) or len(a) > 2:
            return []
        annee = int(c) if len(c) == 4 else annee_deux_chiffres(int(c), ctx.reference.year)
        note = "" if len(c) == 4 else f" ; année {c} lue {annee} (fenêtre de cent ans autour de la référence)"
        return lectures_jour_mois(int(a), int(b), annee, ctx.reference.date(), sep + note)
    if m := NUMERIQUE_SANS_ANNEE.match(texte):
        return lectures_jour_mois(int(m[1]), int(m[3]), None, ctx.reference.date(), m[2] + " ; année absente")
    return None


def annees_possibles(ref: date, mois: int, jour: int, annee: int | None) -> list[tuple[int, set[str]]]:
    """Année écrite ; sinon année de référence, plus la suivante si la date est déjà passée."""
    if annee is not None:
        return [(annee, set())]
    valeur = construire_date(ref.year, mois, jour)
    if valeur is None:
        suivantes = [an for an in range(ref.year + 1, min(ref.year + 9, 10_000)) if construire_date(an, mois, jour)]
        return [(suivantes[0], {"futur"})] if suivantes else [(ref.year, set())]
    if valeur >= ref or ref.year == 9999:
        return [(ref.year, set())]
    return [(ref.year, {"passe"}), (ref.year + 1, {"futur"})]


def lectures_jour_mois(a: int, b: int, annee: int | None, ref: date, sep_note: str) -> list[Lecture]:
    """Lectures jour/mois et mois/jour (et, sans année, passée ou à venir)."""
    sep, note = sep_note[0], sep_note[1:]
    usage_point = " ; le point est un usage jour.mois.année" if sep == "." else ""
    lectures: list[Lecture] = []
    vues: set[date] = set()
    for jour, mois, ordre, libelle in ((a, b, "jma", "jour/mois/année (usage fr, en-GB, de, es, it)"),
                                       (b, a, "mja", "mois/jour/année (usage en-US)")):
        for an, sens in annees_possibles(ref, mois, jour, annee):
            valeur = construire_date(an, mois, jour)
            if valeur is None or valeur in vues:
                continue
            vues.add(valeur)
            lectures.append(Lecture(valeur, None, "jour", f"numérique {libelle}{note}{usage_point}",
                                    frozenset({ordre} | sens)))
    return lectures


def regle_nom_de_mois(texte: str, ctx: Contexte) -> list[Lecture] | None:
    """« 4 mars 2026 », « mardi 4 mars », « March 4, 2026 », « mars 2026 »."""
    m = JOUR_PUIS_MOIS.match(texte) or MOIS_PUIS_JOUR.match(texte)
    if m:
        return lectures_jour_nomme(ctx, int(m["j"]), MOIS[m["m"]], m["a"], m["js"])
    if m := MOIS_SEUL.match(texte):
        return lectures_mois_nomme(ctx.reference.date(), MOIS[m["m"]], int(m["a"]) if m["a"] else None)
    return None


def lectures_mois_nomme(ref: date, mois: int, annee: int | None) -> list[Lecture]:
    """Un mois entier ; sans année et déjà écoulé, ce mois-là ou celui de l'an prochain."""
    lectures: list[Lecture] = []
    for an, sens in annees_possibles(ref, mois, calendar.monthrange(ref.year, mois)[1], annee):
        debut = date(an, mois, 1)
        fin = debut.replace(day=calendar.monthrange(an, mois)[1])
        regle = "mois nommé" + ("" if annee else " ; année absente" + (" : " + next(iter(sens)) if sens else ""))
        lectures.append(Lecture(debut, fin, "mois", regle, frozenset(sens)))
    return lectures


def lectures_jour_nomme(ctx: Contexte, jour: int, mois: int, annee_txt: str | None,
                        jour_semaine: str | None) -> list[Lecture]:
    """Jour + mois nommé ; sans année, année de référence ou suivante si déjà passée."""
    ref = ctx.reference.date()
    annee = None
    if annee_txt:
        annee = int(annee_txt) if len(annee_txt) == 4 else annee_deux_chiffres(int(annee_txt), ref.year)
    lectures: list[Lecture] = []
    for an, sens in annees_possibles(ref, mois, jour, annee):
        valeur = construire_date(an, mois, jour)
        if valeur is None:
            continue
        regle = "jour et mois nommé" + ("" if annee else f" ; année absente : {an}")
        if sens:
            regle += " (date déjà passée)" if "passe" in sens else " (prochaine occurrence)"
        lectures.append(Lecture(valeur, None, "jour", regle, frozenset(sens)))
    return lectures


def regle_jour_du_mois(texte: str, ctx: Contexte) -> list[Lecture] | None:
    """« le 15 », « the 3rd » : jour du mois de référence, ou du suivant s'il est passé."""
    m = JOUR_DU_MOIS.match(texte)
    if not m or not 1 <= int(m["j"]) <= 31:
        return None
    ref = ctx.reference.date()
    options: list[tuple[date, str, str]] = []
    for ecart in (0, 1, 2):
        debut, _ = ajouter_mois(ref.replace(day=1), ecart)
        valeur = construire_date(debut.year, debut.month, int(m["j"]))
        if valeur is not None and valeur >= ref:
            options.append((valeur, "prochain jour du mois portant ce numéro", "futur"))
            break
    passe = construire_date(ref.year, ref.month, int(m["j"]))
    if passe is not None and passe < ref:
        options.insert(0, (passe, "ce numéro dans le mois de référence (déjà passé)", "passe"))
    return fusionner_options(options, "jour")


def regle_annee(texte: str, ctx: Contexte) -> list[Lecture] | None:
    """« 2027 », « en 2027 »."""
    if m := ANNEE_SEULE.match(texte):
        annee = int(m["a"])
        if annee >= 1:
            return [Lecture(date(annee, 1, 1), date(annee, 12, 31), "annee", "année seule")]
    return None


REGLES_DATE: tuple[Callable[[str, Contexte], list[Lecture] | None], ...] = (
    regle_mot_du_jour, regle_decalage, regle_jour_semaine, regle_periode, regle_numerique,
    regle_nom_de_mois, regle_jour_du_mois, regle_annee)


def lire_partie_date(texte: str, ctx: Contexte) -> list[Lecture] | None:
    """Première règle qui reconnaît le texte entier."""
    for regle in REGLES_DATE:
        lectures = regle(texte, ctx)
        if lectures is not None:
            return lectures
    return None


# --------------------------------------------------------------------------- heures et fuseaux

def lire_heure(m: re.Match[str]) -> tuple[time, str] | None:
    """Heure reconnue par le motif HEURE, ou None si hors bornes."""
    if m["mot"]:
        return (time(12, 0), "midi") if m["mot"] in ("midi", "noon") else (time(0, 0), "minuit")
    heure = int(m["h1"] or m["h2"] or m["h3"])
    minute = int(m["m1"] or m["m2"] or 0)
    seconde = int(m["s2"] or 0)
    ampm = (m["ap2"] or m["ap3"] or "").replace(".", "")
    if ampm:
        if not 1 <= heure <= 12:
            return None
        heure = heure % 12 + (12 if ampm == "pm" else 0)
    if heure > 23 or minute > 59 or seconde > 59:
        return None
    return time(heure, minute, seconde), "heure"


def resoudre_fuseau(jeton: str | None) -> list[tuple[tzinfo, str]] | None:
    """Fuseaux désignés par un jeton (plusieurs si l'abréviation est ambiguë) ; None si inconnu."""
    if not jeton:
        return None
    jeton = jeton.strip()
    if m := FUSEAU_DECALAGE.match(jeton):
        minutes = int(m["h"]) * 60 + int(m["m"] or 0)
        if minutes > 14 * 60:
            return None
        signe = -1 if m["s"] == "-" else 1
        return [(timezone(timedelta(minutes=signe * minutes)), f"décalage {jeton}")]
    if jeton in FUSEAUX_ABREGES:
        return [(timezone(timedelta(minutes=minutes)), f"{jeton.upper()} ({region})")
                for minutes, region in FUSEAUX_ABREGES[jeton]]
    if "/" in jeton:
        nom = "/".join("_".join(p.capitalize() for p in partie.split("_")) for partie in jeton.split("/"))
        try:
            return [(ZoneInfo(nom), nom)]
        except (ZoneInfoNotFoundError, ValueError):
            return None
    return None


def chercher_heure(texte: str) -> tuple[time, str, list[tuple[tzinfo, str]] | None, str] | None:
    """Dernière heure du texte, son fuseau éventuel, et le texte qui reste."""
    trouvees = list(HEURE.finditer(texte))
    if not trouvees:
        return None
    m = trouvees[-1]
    lue = lire_heure(m)
    if lue is None:
        return None
    fuseaux = resoudre_fuseau(m["tz"])
    fin = m.end() if fuseaux or not m["tz"] else m.start("tz")
    reste = (texte[:m.start()] + " " + texte[fin:]).strip()
    return lue[0], lue[1], fuseaux, re.sub(r"\s+", " ", reste)


def lire_avec_heure(texte: str, ctx: Contexte) -> list[Lecture] | None:
    """Date (éventuellement implicite) + heure + fuseau éventuel."""
    trouvee = chercher_heure(texte)
    if trouvee is None:
        return None
    heure, genre, fuseaux, reste = trouvee
    if reste:
        jours = lire_partie_date(reste, ctx)
        if not jours:
            return jours
        if any(lec.fin is not None or lec.precision != "jour" for lec in jours):
            return None
    else:
        jours = jour_implicite(ctx, heure)
    lectures = combiner_jour_heure(jours, heure, genre)
    return appliquer_fuseaux(lectures, fuseaux) if fuseaux else lectures


def jour_implicite(ctx: Contexte, heure: time) -> list[Lecture]:
    """Heure seule : jour de référence, ou lendemain si l'heure est déjà passée."""
    ref = ctx.reference
    aujourd_hui = ref.date()
    if ctx.reference_sans_heure or datetime.combine(aujourd_hui, heure) >= ref.replace(tzinfo=None):
        return [Lecture(aujourd_hui, None, "jour", "date implicite : jour de référence")]
    return [Lecture(aujourd_hui, None, "jour", "jour de référence (heure déjà passée)", frozenset({"passe"})),
            Lecture(aujourd_hui + timedelta(days=1), None, "jour", "lendemain (prochaine occurrence)",
                    frozenset({"futur"}))]


def combiner_jour_heure(jours: list[Lecture], heure: time, genre: str) -> list[Lecture]:
    """Chaque jour candidat à l'heure dite ; minuit admet le début ou la fin du jour."""
    lectures: list[Lecture] = []
    precision = "seconde" if heure.second else "minute"
    for lec in jours:
        jour = jour_de(lec.debut)
        lectures.append(replace(lec, debut=datetime.combine(jour, heure), precision=precision,
                                regle=f"{lec.regle} ; {genre} {heure.isoformat('minutes')}",
                                etiquettes=lec.etiquettes | ({"debut-du-jour"} if genre == "minuit" else set())))
        if genre == "minuit":
            lectures.append(replace(lec, debut=datetime.combine(jour + timedelta(days=1), heure),
                                    precision=precision, regle=f"{lec.regle} ; minuit = fin de ce jour",
                                    etiquettes=lec.etiquettes | {"fin-du-jour"}))
    return lectures


def appliquer_fuseaux(lectures: list[Lecture], fuseaux: list[tuple[tzinfo, str]]) -> list[Lecture]:
    """Attache le fuseau écrit dans l'expression (produit cartésien si abréviation ambiguë)."""
    resultat: list[Lecture] = []
    for lec in lectures:
        for zone, libelle in fuseaux:
            if isinstance(zone, ZoneInfo):
                resultat.extend(localiser(lec, zone))
            else:
                resultat.append(replace(lec, debut=lec.debut.replace(tzinfo=zone),
                                        regle=f"{lec.regle} ; fuseau {libelle}"))
    return resultat


def localiser(lec: Lecture, zone: ZoneInfo) -> list[Lecture]:
    """Heure murale dans une zone : deux instants si elle est répétée ou inexistante."""
    naif = lec.debut.replace(tzinfo=None)
    premier, second = naif.replace(tzinfo=zone, fold=0), naif.replace(tzinfo=zone, fold=1)
    if premier.utcoffset() == second.utcoffset():
        return [replace(lec, debut=premier, regle=f"{lec.regle} ; zone {zone.key}")]
    existe = premier.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naif
    cause = "heure répétée (retour à l'heure d'hiver)" if existe else "heure inexistante (passage à l'heure d'été)"
    return [replace(lec, debut=instant if existe else instant.astimezone(timezone.utc).astimezone(zone),
                    regle=f"{lec.regle} ; zone {zone.key} : {cause}, {rang} lecture",
                    etiquettes=lec.etiquettes | {etiquette})
            for instant, rang, etiquette in ((premier, "première", "premiere"), (second, "seconde", "seconde"))]


def convertir_vers(lectures: list[Lecture], zone: tzinfo) -> list[Lecture]:
    """--fuseau : instants naïfs localisés, instants situés convertis ; les jours restent des jours."""
    resultat: list[Lecture] = []
    for lec in lectures:
        if not isinstance(lec.debut, datetime) or lec.fin is not None:
            resultat.append(lec)
        elif lec.debut.tzinfo is not None:
            resultat.append(replace(lec, debut=lec.debut.astimezone(zone)))
        elif isinstance(zone, ZoneInfo):
            resultat.extend(localiser(lec, zone))
        else:
            resultat.append(replace(lec, debut=lec.debut.replace(tzinfo=zone)))
    return resultat


# --------------------------------------------------------------------------- interprétation

def lire_lectures(expression: str, ctx: Contexte) -> list[Lecture] | None:
    """Toutes les lectures d'une expression (None : rien reconnu)."""
    lectures = lire_iso(expression)
    if lectures is None:
        texte = normaliser(expression)
        lectures = lire_partie_date(texte, ctx)
        if lectures is None:
            lectures = lire_avec_heure(texte, ctx)
    if lectures and ctx.fuseau is not None:
        lectures = convertir_vers(lectures, ctx.fuseau)
    return lectures


def verifier_jour_semaine(expression: str, lectures: list[Lecture]) -> str | None:
    """Jour de semaine écrit qui contredit la date écrite (« mardi 4 mars 2026 »)."""
    texte = normaliser(expression)
    m = JOUR_PUIS_MOIS.match(texte) or MOIS_PUIS_JOUR.match(texte)
    if m is None and (heure := chercher_heure(texte)) is not None:
        m = JOUR_PUIS_MOIS.match(heure[3]) or MOIS_PUIS_JOUR.match(heure[3])
    if m is None or not m["js"]:
        return None
    attendu = JOURS[m["js"]]
    fausses = [lec for lec in lectures if jour_de(lec.debut).weekday() != attendu]
    if not fausses:
        return None
    jour = jour_de(fausses[0].debut)
    return f"le {jour.isoformat()} est un {NOMS_JOURS_FR[jour.weekday()]}, pas un {NOMS_JOURS_FR[attendu]}"


def trancher(lectures: list[Lecture], preferences: list[str]) -> tuple[Lecture | None, list[str]]:
    """Retient une lecture par les préférences données ; rend aussi celles qui ont servi."""
    restantes = lectures
    utilisees: list[str] = []
    for pref in preferences:
        filtrees = [lec for lec in restantes if pref in lec.etiquettes]
        if filtrees and len(filtrees) < len(restantes):
            restantes = filtrees
            utilisees.append(pref)
    return (restantes[0], utilisees) if len(restantes) == 1 else (None, utilisees)


def decrire_lecture(lec: Lecture) -> dict[str, Any]:
    """Représentation JSON d'une lecture."""
    sortie: dict[str, Any] = {"iso": iso_lecture(lec), "precision": lec.precision, "regle": lec.regle,
                              "etiquettes": sorted(lec.etiquettes)}
    if lec.fin is None:
        sortie["jour_semaine"] = NOMS_JOURS_FR[jour_de(lec.debut).weekday()]
    if lec.fin is not None:
        sortie["debut"], sortie["fin"] = formater_instant(lec.debut), formater_instant(lec.fin)
    return sortie


def interpreter(expression: str, ctx: Contexte, preferences: list[str]) -> dict[str, Any]:
    """Résultat complet pour une expression."""
    resultat: dict[str, Any] = {"expression": expression, "lectures": [], "retenue": None,
                                "tranchee_par": [], "notes": []}
    if len(expression) > LIMITE_LONGUEUR:
        resultat.update(statut=STATUT_NON_INTERPRETEE, notes=[f"plus de {LIMITE_LONGUEUR} caractères"])
        return resultat
    try:
        lectures = lire_lectures(expression, ctx)
    except (ValueError, OverflowError) as exc:
        resultat.update(statut=STATUT_NON_INTERPRETEE, notes=[f"date impossible : {exc}"])
        return resultat
    if not lectures:
        note = "forme reconnue mais date inexistante" if lectures == [] else "aucune règle ne reconnaît l'expression"
        resultat.update(statut=STATUT_NON_INTERPRETEE, notes=[note])
        return resultat
    return completer(resultat, expression, lectures[:LIMITE_LECTURES], preferences)


def completer(resultat: dict[str, Any], expression: str, lectures: list[Lecture],
              preferences: list[str]) -> dict[str, Any]:
    """Statut, lecture retenue, incohérences."""
    resultat["lectures"] = [decrire_lecture(lec) for lec in lectures]
    incoherence = verifier_jour_semaine(expression, lectures)
    if incoherence:
        resultat.update(statut=STATUT_INCOHERENTE, notes=[incoherence])
        return resultat
    if len(lectures) == 1:
        resultat.update(statut=STATUT_EXACTE, retenue=iso_lecture(lectures[0]))
        return resultat
    retenue, utilisees = trancher(lectures, preferences)
    resultat.update(statut=STATUT_AMBIGUE, tranchee_par=utilisees,
                    retenue=iso_lecture(retenue) if retenue else None)
    if retenue is None:
        etiquettes = sorted({e for lec in lectures for e in lec.etiquettes})
        resultat["notes"].append(f"{len(lectures)} lectures ; trancher avec --preferer parmi : "
                                 + (", ".join(etiquettes) or "aucune étiquette"))
    return resultat


# --------------------------------------------------------------------------- témoins optionnels

def charger_temoins() -> dict[str, Any]:
    """Bibliothèques témoins importées si présentes (import protégé)."""
    temoins: dict[str, Any] = {}
    try:
        import dateparser
        temoins["dateparser"] = dateparser
    except ImportError:
        pass
    try:
        from dateutil import parser as analyseur_dateutil
        temoins["python-dateutil"] = analyseur_dateutil
    except ImportError:
        pass
    return temoins


def bibliotheques_absentes() -> list[str]:
    """Noms des bibliothèques témoins non installées (sans les importer)."""
    return [distribution for module, distribution in TEMOINS_OPTIONNELS
            if importlib.util.find_spec(module) is None]


def version_de(distribution: str) -> str:
    """Version installée d'une distribution, ou « inconnue »."""
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "inconnue"


def reponses_temoin(nom: str, module: Any, expression: str, ctx: Contexte) -> list[datetime]:
    """Ce que la bibliothèque témoin lit (dateutil : jour d'abord puis mois d'abord)."""
    reference = ctx.reference.replace(tzinfo=None)
    if nom == "dateparser":
        reponse = module.parse(expression, languages=["fr", "en"],
                               settings={"RELATIVE_BASE": reference, "TIMEZONE": "UTC"})
        return [reponse] if reponse is not None else []
    reponses: list[datetime] = []
    for jour_d_abord in (True, False):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                reponse = module.parse(expression, dayfirst=jour_d_abord,
                                       default=reference.replace(hour=0, minute=0, second=0, microsecond=0))
            except (ValueError, OverflowError, TypeError):
                continue
        if reponse not in reponses:
            reponses.append(reponse)
    return reponses


def concorde(reponse: datetime, lectures: list[dict[str, Any]]) -> bool:
    """La réponse du témoin est-elle l'une des lectures de l'outil ?"""
    for lec in lectures:
        debut = lec.get("debut", lec["iso"])
        fin = lec.get("fin")
        if "T" not in debut and fin is None:
            if reponse.date().isoformat() == debut:
                return True
        elif fin is not None:
            if debut[:10] <= reponse.date().isoformat() <= fin[:10]:
                return True
        elif comparer_instants(reponse, datetime.fromisoformat(debut)):
            return True
    return False


def comparer_instants(a: datetime, b: datetime) -> bool:
    """Égalité à la minute ; deux instants situés sont comparés en UTC."""
    if (a.tzinfo is None) != (b.tzinfo is None):
        a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
    return abs((a - b).total_seconds()) < 60


def comparer(resultats: list[dict[str, Any]], ctx: Contexte) -> dict[str, Any]:
    """Confronte chaque expression aux bibliothèques témoins installées."""
    temoins = charger_temoins()
    bilan: dict[str, Any] = {"bibliotheques": {nom: version_de(nom) for nom in temoins}}
    for nom, module in temoins.items():
        compte = {"accords": 0, "desaccords": 0, "sans_reponse": 0, "ambigues_choisies_sans_le_dire": 0}
        for res in resultats:
            reponses = reponses_temoin(nom, module, res["expression"], ctx)
            accord = None if not reponses else all(concorde(r, res["lectures"]) for r in reponses)
            res.setdefault("temoins", {})[nom] = {"lectures": [formater_instant(r) for r in reponses],
                                                  "accord": accord}
            cle = "sans_reponse" if accord is None else ("accords" if accord else "desaccords")
            compte[cle] += 1
            if res["statut"] == STATUT_AMBIGUE and len(reponses) == 1:
                compte["ambigues_choisies_sans_le_dire"] += 1
        bilan[nom] = compte
    return bilan


# --------------------------------------------------------------------------- entrée / sortie

def lire_reference(texte: str | None, zone: tzinfo | None) -> tuple[datetime, bool, str]:
    """Référence explicite (AAAA-MM-JJ ou instant ISO) ou horloge système."""
    if texte is None:
        maintenant = datetime.now(zone) if zone else datetime.now()
        return maintenant.replace(microsecond=0), False, "horloge système au moment de l'appel"
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", texte.strip()):
            jour = date.fromisoformat(texte.strip())
            return datetime.combine(jour, time(0, 0), tzinfo=zone), True, "--reference (jour, minuit)"
        instant = datetime.fromisoformat(texte.strip())
    except ValueError as exc:
        raise ErreurUsage(f"--reference illisible ({texte!r}) : {exc}") from exc
    if zone is not None:
        instant = instant.astimezone(zone) if instant.tzinfo else instant.replace(tzinfo=zone)
    return instant, False, "--reference"


def lire_zone(nom: str | None) -> tzinfo | None:
    """--fuseau : nom IANA (Europe/Paris), UTC, ou décalage ±HH:MM."""
    if nom is None:
        return None
    if nom.lower() in ("utc", "z", "gmt") or FUSEAU_DECALAGE.match(nom.lower()):
        options = resoudre_fuseau(nom.lower())
        if options:
            return options[0][0]
    try:
        return ZoneInfo(nom)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ErreurUsage(f"--fuseau inconnu : {nom!r} ({exc})") from exc


def lire_fichier_expressions(chemin: Path) -> list[str]:
    """Une expression par ligne non vide ; lecture bornée, texte UTF-8 exigé."""
    if not chemin.exists():
        raise ErreurUsage(f"fichier introuvable : {chemin}")
    if not chemin.is_file():
        raise ErreurUsage(f"pas un fichier ordinaire : {chemin}")
    try:
        with chemin.open("rb") as flux:
            brut = flux.read(LIMITE_OCTETS + 1)
    except OSError as exc:
        raise ErreurUsage(f"lecture impossible de {chemin} : {exc}") from exc
    if len(brut) > LIMITE_OCTETS:
        raise ErreurUsage(f"{chemin} dépasse {LIMITE_OCTETS} octets")
    if b"\x00" in brut:
        raise ErreurUsage(f"{chemin} est binaire (octet nul)")
    try:
        texte = brut.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurUsage(f"{chemin} n'est pas du texte UTF-8 : {exc}") from exc
    lignes = [ligne.strip() for ligne in texte.splitlines() if ligne.strip()]
    if len(lignes) > LIMITE_LIGNES:
        raise ErreurUsage(f"{chemin} dépasse {LIMITE_LIGNES} lignes")
    return lignes


def analyser(args: argparse.Namespace, racine: Path) -> dict[str, Any]:
    """Cœur : lit les entrées, interprète, compare si demandé."""
    zone = lire_zone(args.fuseau)
    reference, sans_heure, source = lire_reference(args.reference, zone)
    ctx = Contexte(reference, sans_heure, zone)
    expressions = list(args.expressions)
    if args.fichier:
        chemin = Path(args.fichier)
        expressions += lire_fichier_expressions(chemin if chemin.is_absolute() else racine / chemin)
    resultats = [interpreter(e, ctx, args.preferer or []) for e in expressions]
    bilan = {statut: sum(1 for r in resultats if r["statut"] == statut)
             for statut in (STATUT_EXACTE, STATUT_AMBIGUE, STATUT_INCOHERENTE, STATUT_NON_INTERPRETEE)}
    bilan["ambigues_non_tranchees"] = sum(1 for r in resultats
                                          if r["statut"] == STATUT_AMBIGUE and r["retenue"] is None)
    sortie: dict[str, Any] = {
        "moteur": MOTEUR, "reference": formater_instant(reference), "reference_source": source,
        "fuseau": args.fuseau, "denominateur": len(expressions),
        "examines": expressions[:LIMITE_EXAMINES], "examines_tronques": len(expressions) > LIMITE_EXAMINES,
        "bilan": bilan, "resultats": resultats, "comparaison": None}
    if args.comparer and expressions:
        sortie["comparaison"] = comparer(resultats, ctx)
    return sortie


def imprimer_humain(res: dict[str, Any]) -> None:
    """Sortie lisible."""
    print(f"référence {res['reference']} ({res['reference_source']})"
          + (f", fuseau {res['fuseau']}" if res["fuseau"] else ""))
    for r in res["resultats"]:
        print(f"« {r['expression']} » : {r['statut']}"
              + (f" → {r['retenue']}" if r["retenue"] else ""))
        if r["statut"] != STATUT_EXACTE:
            for lec in r["lectures"]:
                print(f"    {lec['iso']}  [{', '.join(lec['etiquettes']) or '-'}]  {lec['regle']}")
        for note in r["notes"]:
            print(f"    note : {note}")
        for nom, temoin in r.get("temoins", {}).items():
            print(f"    {nom} : {', '.join(temoin['lectures']) or 'aucune lecture'} (accord : {temoin['accord']})")
    b = res["bilan"]
    print(f"{res['denominateur']} expression(s) : {b[STATUT_EXACTE]} exacte(s), {b[STATUT_AMBIGUE]} ambiguë(s) "
          f"dont {b['ambigues_non_tranchees']} non tranchée(s), {b[STATUT_INCOHERENTE]} incohérente(s), "
          f"{b[STATUT_NON_INTERPRETEE]} non interprétée(s)")


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Interprète des expressions de date en français ou en anglais, rend chaque "
                    "lecture possible en ISO 8601 et signale les ambiguïtés.",
        epilog="Exemple : interpreter_dates.py \"03/04/2026\" \"mardi prochain\" \"dans 3 semaines\" "
               "--reference 2026-10-02 --json\n"
               "Codes : 0 toutes exactes ou tranchées ; 1 ambiguïté non tranchée, incohérence ou "
               "expression non interprétée ; 2 entrée invalide ou aucune expression interprétable ; "
               "3 rien à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("expressions", nargs="*", help="expressions de date à interpréter")
    parseur.add_argument("--fichier", help="fichier texte UTF-8 : une expression par ligne")
    parseur.add_argument("--reference", help="date de référence AAAA-MM-JJ ou instant ISO "
                                             "(défaut : horloge système au moment de l'appel)")
    parseur.add_argument("--fuseau", help="fuseau de sortie : nom IANA (Europe/Paris), UTC ou ±HH:MM")
    parseur.add_argument("--preferer", action="append", choices=ETIQUETTES, metavar="ETIQUETTE",
                         help="tranche une ambiguïté (répétable) : " + ", ".join(ETIQUETTES))
    parseur.add_argument("--comparer", action="store_true",
                         help="confronter à dateparser et python-dateutil si installées")
    parseur.add_argument("--racine", type=Path, help="base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def code_de_sortie(res: dict[str, Any]) -> int:
    """0 net, 1 défaut signalé, 2 rien d'interprétable."""
    bilan = res["bilan"]
    if bilan[STATUT_NON_INTERPRETEE] == res["denominateur"]:
        return 2
    defauts = bilan["ambigues_non_tranchees"] + bilan[STATUT_INCOHERENTE] + bilan[STATUT_NON_INTERPRETEE]
    return 1 if defauts else 0


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    absentes = bibliotheques_absentes()
    if absentes:
        print(f"{', '.join(absentes)} absente(s) : interprétation par l'analyseur intégré (stdlib), "
              "--comparer sans ce(s) témoin(s)", file=sys.stderr)
    racine = args.racine if args.racine is not None else Path.cwd()
    try:
        res = analyser(args, racine)
    except ErreurUsage as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
    if res["denominateur"] == 0:
        print("dénominateur nul : rien à examiner (aucune expression fournie)", file=sys.stderr)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        return 3
    res["contrat"] = lire_contrat()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        imprimer_humain(res)
    code = code_de_sortie(res)
    if code == 2:
        print("erreur : aucune expression interprétable", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
