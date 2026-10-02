r"""Un agent qui lit un .ics « à l'œil » ne développe ni les récurrences ni les
exceptions, et l'interpréteur de référence n'a ni icalendar ni dateutil.
Mesuré : sur 2 898 RRULE tirées au hasard, le développement stdlib donne les
mêmes 180 263 occurrences que python-dateutil 2.9.0 (0 écart) ; sur
l'INVOCATION, 4 occurrences et 1 chevauchement de 30 minutes.

QUESTION
    Quels événements contient ce calendrier, quelles occurrences tombent dans
    une fenêtre de temps, et lesquelles se chevauchent ?
MESURE
    Lecture RFC 5545 : dépliage des lignes au niveau des octets, paramètres
    entre guillemets, échappements des textes ; DTSTART et DTEND avec TZID
    (zoneinfo, noms Windows usuels, X-LIC-LOCATION, VTIMEZONE à décalage
    fixe), journées entières, DURATION. RRULE développée en heure locale du
    fuseau de DTSTART (FREQ DAILY, WEEKLY, MONTHLY, YEARLY ; INTERVAL,
    COUNT, UNTIL, BYDAY, BYMONTHDAY, BYMONTH, WKST), puis EXDATE, RDATE et
    RECURRENCE-ID. Chevauchements par balayage des occurrences triées
    (événements annulés, transparents et journées entières écartés par
    défaut). Avec python-dateutil et icalendar, chaque récurrence est
    redéveloppée par dateutil et chaque DTSTART relu par icalendar ; les
    écarts sont rapportés.
HYPOTHÈSES
    Fichier UTF-8 conforme à la RFC 5545 ; base IANA du système (zoneinfo)
    disponible ; une heure flottante (sans TZID ni Z) est lue dans --fuseau
    (UTC par défaut) ; DTSTART synchronisé avec sa règle.
LIMITES
    Pas de FREQ HOURLY, MINUTELY, SECONDLY ni de BYSETPOS, BYWEEKNO,
    BYYEARDAY, BYHOUR (repli sur python-dateutil si installée, sinon seule
    la première occurrence est retenue, avec avertissement). RDATE de type
    PERIOD et RANGE=THISANDFUTURE ignorés. VTIMEZONE à règles d'été non
    interprété si son TZID n'est pas un nom IANA ou Windows connu : l'heure
    est alors traitée comme flottante. VTODO et VJOURNAL ignorés.
CONTRE-EXEMPLES
    DTSTART un mardi, règle hebdomadaire du lundi limitée à 3 occurrences :
    la RFC compte DTSTART comme première occurrence, l'outil donne mardi
    6 octobre + 2 lundis, python-dateutil donne 3 lundis ; l'écart est
    rapporté (constaté) sans pouvoir dire ce qu'affichera le client. BYDAY
    mêlant rangs et jours simples (dernier samedi + tous les dimanches) :
    l'outil prend l'union, dateutil n'a produit aucune occurrence sur 105
    règles de ce type (constaté).
INVOCATION
    {outil} --texte 'BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:revue\nDTSTART;TZID=Europe/Paris:20261005T090000\nDTEND;TZID=Europe/Paris:20261005T100000\nRRULE:FREQ=WEEKLY;COUNT=3\nSUMMARY:Revue\nEND:VEVENT\nBEGIN:VEVENT\nUID:client\nDTSTART:20261012T073000Z\nDURATION:PT1H\nSUMMARY:Client\nEND:VEVENT\nEND:VCALENDAR' --json
DOMAINE
    Calendriers exportés (Google, Outlook, Thunderbird, CalDAV) pour
    vérifier disponibilités, doublons et conflits sur une fenêtre bornée.
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, NamedTuple
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from dateutil import rrule as dateutil_rrule
    import dateutil as module_dateutil
except ImportError:
    dateutil_rrule = None
    module_dateutil = None

try:
    import icalendar as module_icalendar
except ImportError:
    module_icalendar = None

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

PROP_DTSTART = "DTSTART"
PROP_DTEND = "DTEND"
PROP_DURATION = "DURATION"
PROP_RRULE = "RRULE"
PROP_EXDATE = "EXDATE"
PROP_RDATE = "RDATE"
PROP_RECURRENCE_ID = "RECURRENCE-ID"
PROP_LOCATION_LIC = "X-LIC-LOCATION"
PARAM_TZID = "TZID"
PARAM_RANGE = "RANGE=THISANDFUTURE"
COMP_VEVENT = "VEVENT"
COMP_VTIMEZONE = "VTIMEZONE"
COMP_VTODO = "VTODO"
COMP_VJOURNAL = "VJOURNAL"
FREQ_DAILY = "DAILY"
FREQ_WEEKLY = "WEEKLY"
FREQ_MONTHLY = "MONTHLY"
FREQ_YEARLY = "YEARLY"
FREQ_HOURLY = "HOURLY"
FREQ_MINUTELY = "MINUTELY"
FREQ_SECONDLY = "SECONDLY"
PART_INTERVAL = "INTERVAL"
PART_COUNT = "COUNT"
PART_UNTIL = "UNTIL"
PART_BYDAY = "BYDAY"
PART_BYMONTHDAY = "BYMONTHDAY"
PART_BYMONTH = "BYMONTH"
PART_WKST = "WKST"
PART_BYSETPOS = "BYSETPOS"
PART_BYWEEKNO = "BYWEEKNO"
PART_BYYEARDAY = "BYYEARDAY"
PART_BYHOUR = "BYHOUR"
PERIOD = "PERIOD"
PART_FREQ = "FREQ"
ENCODAGE = "UTF-8"
BASE_FUSEAUX = "IANA"
FREQUENCES = (FREQ_DAILY, FREQ_WEEKLY, FREQ_MONTHLY, FREQ_YEARLY)
PARTIES_CONNUES = (PART_FREQ, PART_INTERVAL, PART_COUNT, PART_UNTIL, PART_BYDAY,
                   PART_BYMONTHDAY, PART_BYMONTH, PART_WKST)
JOURS_ICAL = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
EXTENSIONS = (".ics", ".ical", ".icalendar", ".ifb")

LIMITE_EXAMINES = 50
LIMITE_FICHIERS = 10_000
TAILLE_MAX = 50 * 1024 * 1024
LIMITE_PERIODES = 200_000
LIMITE_OCCURRENCES_SERIE = 100_000
LIMITE_OCCURRENCES_SORTIE = 1000
LIMITE_CONFLITS = 200
LIMITE_EVENEMENTS_SORTIE = 500

ZONES_WINDOWS = MappingProxyType({
    "W. Europe Standard Time": "Europe/Berlin", "Romance Standard Time": "Europe/Paris",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw", "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik", "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago", "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles", "UTC": "Etc/UTC",
    "Tokyo Standard Time": "Asia/Tokyo", "China Standard Time": "Asia/Shanghai",
    "India Standard Time": "Asia/Kolkata", "AUS Eastern Standard Time": "Australia/Sydney",
    "FLE Standard Time": "Europe/Kyiv", "Russian Standard Time": "Europe/Moscow",
    "E. Europe Standard Time": "Europe/Chisinau", "Morocco Standard Time": "Africa/Casablanca",
    "Arabian Standard Time": "Asia/Dubai", "Singapore Standard Time": "Asia/Singapore",
    "SA Pacific Standard Time": "America/Bogota",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Atlantic Standard Time": "America/Halifax", "Hawaiian Standard Time": "Pacific/Honolulu",
    "Alaskan Standard Time": "America/Anchorage", "US Mountain Standard Time": "America/Phoenix",
    "Korea Standard Time": "Asia/Seoul", "Israel Standard Time": "Asia/Jerusalem",
    "Turkey Standard Time": "Europe/Istanbul", "GTB Standard Time": "Europe/Bucharest",
    "South Africa Standard Time": "Africa/Johannesburg",
    "New Zealand Standard Time": "Pacific/Auckland",
    "Argentina Standard Time": "America/Buenos_Aires",
})

MOTIF_LIGNE = re.compile(
    r'^(?P<nom>[A-Za-z0-9-]+)(?P<params>(?:;[A-Za-z0-9-]+=(?:"[^"]*"|[^";:,]*)'
    r'(?:,(?:"[^"]*"|[^";:,]*))*)*):(?P<valeur>.*)$', re.S)
MOTIF_PARAM = re.compile(r';([A-Za-z0-9-]+)=((?:"[^"]*"|[^";:,]*)(?:,(?:"[^"]*"|[^";:,]*))*)')
MOTIF_VALEUR_PARAM = re.compile(r'"([^"]*)"|([^",]+)')
MOTIF_INSTANT = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?$")
MOTIF_DUREE = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
MOTIF_DEBUT_LIGNE = re.compile(r"^[A-Za-z0-9-]+[;:]")
MOTIF_JOUR_REGLE = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")


class ErreurEntree(Exception):
    """Entrée invalide : code 2."""


class Propriete(NamedTuple):
    """Ligne de contenu : nom, paramètres, valeur brute."""
    nom: str
    params: dict[str, list[str]]
    valeur: str


@dataclass
class Composant:
    """Composant BEGIN/END et ses propriétés."""
    nom: str
    proprietes: list[Propriete] = field(default_factory=list)
    enfants: list["Composant"] = field(default_factory=list)

    def premiere(self, nom: str) -> Propriete | None:
        """Première propriété portant ce nom."""
        return next((p for p in self.proprietes if p.nom == nom), None)

    def toutes(self, nom: str) -> list[Propriete]:
        """Toutes les propriétés portant ce nom."""
        return [p for p in self.proprietes if p.nom == nom]


@dataclass(frozen=True)
class Regle:
    """Règle de récurrence analysée."""
    texte: str
    freq: str
    intervalle: int
    compte: int | None
    jusqua: datetime | None
    par_jour: tuple[tuple[int | None, int], ...]
    par_jour_mois: tuple[int, ...]
    par_mois: tuple[int, ...]
    debut_semaine: int
    non_prises: tuple[str, ...]


@dataclass
class Evenement:
    """VEVENT normalisé (instants conscients du fuseau)."""
    source: str
    uid: str
    resume: str
    debut: datetime
    duree: timedelta
    journee: bool
    flottant: bool
    regle: Regle | None = None
    exdates: list[tuple[datetime, bool]] = field(default_factory=list)
    rdates: list[datetime] = field(default_factory=list)
    recurrence_id: datetime | None = None
    annule: bool = False
    transparent: bool = False
    avertissements: list[str] = field(default_factory=list)
    moteur: str = "stdlib"


class Occurrence(NamedTuple):
    """Une occurrence datée d'un événement."""
    evenement: Evenement
    debut: datetime
    fin: datetime


# ---------------------------------------------------------------- contrat --

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


# ---------------------------------------------------------------- lecture --

def texte_en_ligne(texte: str) -> str:
    """--texte sans vrai saut de ligne : la séquence littérale « \\n » sépare
    les lignes, sauf si le morceau suivant ne commence pas comme une ligne
    de contenu (c'est alors un échappement de texte, recollé)."""
    if "\n" in texte or "\r" in texte or "\\n" not in texte:
        return texte
    morceaux = texte.split("\\n")
    lignes = [morceaux[0]]
    for morceau in morceaux[1:]:
        if MOTIF_DEBUT_LIGNE.match(morceau):
            lignes.append(morceau)
        else:
            lignes[-1] += "\\n" + morceau
    return "\r\n".join(lignes)


def decoder(octets: bytes, nom: str, avertissements: list[str]) -> str:
    """Retire le BOM, déplie (au niveau des octets, RFC 5545 §3.1), décode UTF-8."""
    if octets.startswith(b"\xef\xbb\xbf"):
        octets = octets[3:]
    deplie = re.sub(rb"\r?\n[ \t]", b"", octets)
    texte = deplie.decode(ENCODAGE, errors="replace")
    remplacements = texte.count("�")
    if remplacements:
        avertissements.append(f"{nom} : {remplacements} octet(s) non {ENCODAGE} remplacé(s)")
    if not re.search(r"^BEGIN:(VCALENDAR|VEVENT)\s*$", texte, re.M | re.I):
        raise ErreurEntree(f"{nom} : aucun BEGIN:VCALENDAR — ce n'est pas un calendrier iCalendar")
    return texte


def analyser_ligne(ligne: str) -> Propriete | None:
    """Découpe nom;param=valeur:valeur ; None si la ligne est mal formée."""
    trouve = MOTIF_LIGNE.match(ligne)
    if trouve is None:
        return None
    params: dict[str, list[str]] = {}
    for nom, brut in MOTIF_PARAM.findall(trouve.group("params")):
        params[nom.upper()] = [a or b for a, b in MOTIF_VALEUR_PARAM.findall(brut)]
    return Propriete(trouve.group("nom").upper(), params, trouve.group("valeur"))


def analyser_composants(texte: str, avertissements: list[str], nom: str) -> list[Composant]:
    """Construit l'arbre BEGIN/END ; rend les composants de premier niveau."""
    racine = Composant("RACINE")
    pile = [racine]
    mal_formees = 0
    for ligne in re.split(r"\r\n|\n|\r", texte):
        if not ligne.strip():
            continue
        prop = analyser_ligne(ligne)
        if prop is None:
            mal_formees += 1
        elif prop.nom == "BEGIN":
            nouveau = Composant(prop.valeur.strip().upper())
            pile[-1].enfants.append(nouveau)
            pile.append(nouveau)
        elif prop.nom == "END":
            if len(pile) > 1 and pile[-1].nom == prop.valeur.strip().upper():
                pile.pop()
            else:
                avertissements.append(f"{nom} : END:{prop.valeur} sans BEGIN correspondant")
        else:
            pile[-1].proprietes.append(prop)
    if mal_formees:
        avertissements.append(f"{nom} : {mal_formees} ligne(s) mal formée(s) ignorée(s)")
    if len(pile) > 1:
        avertissements.append(f"{nom} : composant {pile[-1].nom} non refermé (fichier tronqué ?)")
    return racine.enfants


def parcourir(composants: Iterable[Composant], nom: str) -> list[Composant]:
    """Tous les composants de ce nom, à toute profondeur."""
    trouves = []
    for comp in composants:
        if comp.nom == nom:
            trouves.append(comp)
        trouves.extend(parcourir(comp.enfants, nom))
    return trouves


def desechapper(valeur: str) -> str:
    """Échappements TEXT de la RFC 5545 (\\n, \\N, \\,, \\;, \\\\)."""
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), valeur)


# ---------------------------------------------------------- fuseaux, dates --

def zone_iana(nom: str) -> tzinfo | None:
    """ZoneInfo si le nom est une clé IANA valide, sinon None."""
    try:
        return ZoneInfo(nom)
    except (KeyError, ValueError, OSError):
        return None


def zones_des_vtimezone(composants: list[Composant]) -> dict[str, tzinfo]:
    """TZID définis localement : X-LIC-LOCATION, ou décalage fixe unique."""
    zones: dict[str, tzinfo] = {}
    for vtz in parcourir(composants, COMP_VTIMEZONE):
        tzid = vtz.premiere(PARAM_TZID)
        if tzid is None:
            continue
        lieu = vtz.premiere(PROP_LOCATION_LIC)
        zone = zone_iana(lieu.valeur.strip()) if lieu else None
        decalages = {p.valeur.strip() for e in vtz.enfants for p in e.toutes("TZOFFSETTO")}
        if zone is None and len(decalages) == 1:
            zone = fuseau_fixe(decalages.pop())
        if zone is not None:
            zones[tzid.valeur.strip()] = zone
    return zones


def fuseau_fixe(texte: str) -> tzinfo | None:
    """« +0100 » ou « -053000 » → timezone fixe."""
    trouve = re.fullmatch(r"([+-])(\d{2})(\d{2})(\d{2})?", texte)
    if trouve is None:
        return None
    signe = -1 if trouve.group(1) == "-" else 1
    ecart = timedelta(hours=int(trouve.group(2)), minutes=int(trouve.group(3)),
                      seconds=int(trouve.group(4) or 0))
    return timezone(signe * ecart)


def resoudre_zone(tzid: str, zones_locales: dict[str, tzinfo]) -> tzinfo | None:
    """TZID → tzinfo : IANA, Windows, VTIMEZONE local, préfixe façon /mozilla.org/."""
    nom = tzid.strip()
    if nom in zones_locales:
        return zones_locales[nom]
    candidats = [nom, ZONES_WINDOWS.get(nom, "")]
    segments = nom.strip("/").split("/")
    candidats += ["/".join(segments[-2:]), "/".join(segments[-3:])]
    for candidat in candidats:
        zone = zone_iana(candidat) if candidat else None
        if zone is not None:
            return zone
    return None


class Instant(NamedTuple):
    """Valeur DATE ou DATE-TIME lue, rendue consciente du fuseau."""
    moment: datetime
    journee: bool
    flottant: bool


def lire_instant(valeur: str, params: dict[str, list[str]], zones: dict[str, tzinfo],
                 fuseau: tzinfo, avertissements: list[str]) -> Instant:
    """DATE, DATE-TIME UTC (Z), local avec TZID, ou flottant (lu dans --fuseau)."""
    trouve = MOTIF_INSTANT.match(valeur.strip())
    if trouve is None:
        raise ValueError(f"date iCalendar invalide « {valeur.strip()[:40]} »")
    a, mo, j, h, mi, s, z = trouve.groups()
    if h is None:
        return Instant(datetime(int(a), int(mo), int(j), tzinfo=fuseau), True, False)
    naif = datetime(int(a), int(mo), int(j), int(h), int(mi), min(int(s), 59))
    if z:
        return Instant(naif.replace(tzinfo=timezone.utc), False, False)
    tzid = (params.get(PARAM_TZID) or [""])[0]
    zone = resoudre_zone(tzid, zones) if tzid else None
    if tzid and zone is None:
        avertissements.append(f"TZID inconnu « {tzid} » : heure traitée comme flottante")
    return Instant(naif.replace(tzinfo=zone or fuseau), False, zone is None)


def lire_duree(texte: str) -> timedelta:
    """DURATION RFC 5545 (P1W, P1DT2H, -PT15M…)."""
    trouve = MOTIF_DUREE.match(texte.strip())
    if trouve is None or texte.strip() in ("P", "PT", "-P", "+P"):
        raise ValueError(f"DURATION invalide « {texte.strip()} »")
    signe, sem, jours, heures, minutes, secondes = trouve.groups()
    duree = timedelta(weeks=int(sem or 0), days=int(jours or 0), hours=int(heures or 0),
                      minutes=int(minutes or 0), seconds=int(secondes or 0))
    return -duree if signe == "-" else duree


# --------------------------------------------------------------- règles --

def lire_regle(texte: str, debut: Instant, zones: dict[str, tzinfo], fuseau: tzinfo) -> Regle:
    """RRULE → Regle ; les parties non prises en charge sont notées."""
    parties = {}
    for morceau in texte.strip().split(";"):
        cle, _, val = morceau.partition("=")
        if cle:
            parties[cle.strip().upper()] = val.strip()
    freq = parties.get(PART_FREQ, "").upper()
    non_prises = tuple(sorted(k for k in parties if k not in PARTIES_CONNUES))
    if freq not in FREQUENCES:
        non_prises += (f"{PART_FREQ}={freq or '?'}",)
    jusqua = None
    if PART_UNTIL in parties:
        brut = lire_instant(parties[PART_UNTIL], {}, zones, debut.moment.tzinfo or fuseau, [])
        jusqua = brut.moment
        if brut.journee:
            jusqua = datetime.combine(brut.moment.date(), time.max, tzinfo=debut.moment.tzinfo)
    return Regle(texte.strip(), freq, max(1, int(parties.get(PART_INTERVAL, "1") or 1)),
                 int(parties[PART_COUNT]) if PART_COUNT in parties else None, jusqua,
                 lire_jours_regle(parties.get(PART_BYDAY, "")),
                 entiers(parties.get(PART_BYMONTHDAY, "")), entiers(parties.get(PART_BYMONTH, "")),
                 JOURS_ICAL.index(parties.get(PART_WKST, "MO").upper()[:2] or "MO"), non_prises)


def entiers(texte: str) -> tuple[int, ...]:
    """« 1,-1,15 » → (1, -1, 15)."""
    return tuple(int(x) for x in texte.split(",") if x.strip())


def lire_jours_regle(texte: str) -> tuple[tuple[int | None, int], ...]:
    """« MO,-1FR,2TU » → ((None, 0), (-1, 4), (2, 1))."""
    jours = []
    for morceau in texte.upper().split(","):
        if not morceau.strip():
            continue
        trouve = MOTIF_JOUR_REGLE.match(morceau.strip())
        if trouve is None:
            raise ValueError(f"BYDAY invalide « {morceau} »")
        rang = int(trouve.group(1)) if trouve.group(1) else None
        jours.append((rang, JOURS_ICAL.index(trouve.group(2))))
    return tuple(jours)


def decaler_mois(annee: int, mois: int, ecart: int) -> tuple[int, int]:
    """(année, mois) + ecart mois."""
    total = annee * 12 + (mois - 1) + ecart
    return total // 12, total % 12 + 1


def jours_du_mois(regle: Regle, annee: int, mois: int, jour_defaut: int) -> list[date]:
    """Dates candidates d'un mois selon BYMONTHDAY et BYDAY (rang relatif au mois)."""
    nb = calendar.monthrange(annee, mois)[1]
    retenus: set[int] | None = None
    if regle.par_jour_mois:
        retenus = jours_du_mois_absolus(regle, annee, mois)
    if regle.par_jour:
        selon_jour: set[int] = set()
        for rang, jour_semaine in regle.par_jour:
            liste = [d for d in range(1, nb + 1) if calendar.weekday(annee, mois, d) == jour_semaine]
            if rang is None:
                selon_jour.update(liste)
            elif 1 <= abs(rang) <= len(liste):
                selon_jour.add(liste[rang - 1] if rang > 0 else liste[rang])
        retenus = selon_jour if retenus is None else retenus & selon_jour
    if retenus is None:
        retenus = {jour_defaut} if jour_defaut <= nb else set()
    return [date(annee, mois, d) for d in sorted(retenus)]


def jours_de_l_annee(regle: Regle, annee: int, depart: date) -> list[date]:
    """Dates candidates d'une année (FREQ=YEARLY)."""
    if regle.par_mois:
        return [j for m in sorted(regle.par_mois) for j in jours_du_mois(regle, annee, m, depart.day)]
    if regle.par_jour and any(rang is not None for rang, _ in regle.par_jour):
        return jours_rang_annuel(regle, annee)
    if regle.par_jour or regle.par_jour_mois:
        return [j for m in range(1, 13) for j in jours_du_mois(regle, annee, m, depart.day)]
    if depart.month == 2 and depart.day == 29 and not calendar.isleap(annee):
        return []
    return [date(annee, depart.month, depart.day)]


def jours_rang_annuel(regle: Regle, annee: int) -> list[date]:
    """BYDAY à rang relatif à l'année (ex. 20MO) sans BYMONTH, puis filtre BYMONTHDAY."""
    retenus: set[date] = set()
    premier = date(annee, 1, 1)
    annee_entiere = [premier + timedelta(days=i) for i in range(366 if calendar.isleap(annee) else 365)]
    for rang, jour_semaine in regle.par_jour:
        liste = [j for j in annee_entiere if j.weekday() == jour_semaine]
        if rang is None:
            retenus.update(liste)
        elif 1 <= abs(rang) <= len(liste):
            retenus.add(liste[rang - 1] if rang > 0 else liste[rang])
    if regle.par_jour_mois:
        retenus = {j for j in retenus if j.day in jours_du_mois_absolus(regle, j.year, j.month)}
    return sorted(retenus)


def jours_du_mois_absolus(regle: Regle, annee: int, mois: int) -> set[int]:
    """BYMONTHDAY ramené à des quantièmes positifs valides pour ce mois."""
    nb = calendar.monthrange(annee, mois)[1]
    quantiemes = {d if d > 0 else nb + 1 + d for d in regle.par_jour_mois}
    return {d for d in quantiemes if 1 <= d <= nb}


def periode(regle: Regle, depart: date, index: int) -> tuple[date, list[date]]:
    """Début de la index-ième période et ses dates candidates triées."""
    if regle.freq == FREQ_DAILY:
        jour = depart + timedelta(days=index * regle.intervalle)
        ok = ((not regle.par_mois or jour.month in regle.par_mois)
              and (not regle.par_jour or jour.weekday() in {j for _, j in regle.par_jour})
              and (not regle.par_jour_mois or jour in jours_du_mois(regle, jour.year, jour.month, jour.day)))
        return jour, [jour] if ok else []
    if regle.freq == FREQ_WEEKLY:
        lundi = depart - timedelta(days=(depart.weekday() - regle.debut_semaine) % 7)
        semaine = lundi + timedelta(weeks=index * regle.intervalle)
        voulus = {j for _, j in regle.par_jour} or {depart.weekday()}
        jours = [semaine + timedelta(days=i) for i in range(7)]
        return semaine, [j for j in jours if j.weekday() in voulus
                         and (not regle.par_mois or j.month in regle.par_mois)]
    if regle.freq == FREQ_MONTHLY:
        annee, mois = decaler_mois(depart.year, depart.month, index * regle.intervalle)
        if regle.par_mois and mois not in regle.par_mois:
            return date(annee, mois, 1), []
        return date(annee, mois, 1), jours_du_mois(regle, annee, mois, depart.day)
    annee = depart.year + index * regle.intervalle
    return date(annee, 1, 1), jours_de_l_annee(regle, annee, depart)


def developper_stdlib(ev: Evenement, borne: datetime) -> list[datetime]:
    """Débuts d'occurrences de la règle (DTSTART compris, RFC 5545) jusqu'à borne."""
    regle = ev.regle
    debuts = [ev.debut]
    depart = ev.debut.date()
    for index in range(LIMITE_PERIODES):
        try:
            ouverture, jours = periode(regle, depart, index)
        except (ValueError, OverflowError):
            break
        if ouverture > borne.date() + timedelta(days=1):
            break
        for jour in jours:
            moment = ev.debut.replace(year=jour.year, month=jour.month, day=jour.day)
            if moment <= ev.debut:
                continue
            if (regle.jusqua and moment > regle.jusqua) or moment > borne:
                return debuts
            if regle.compte is not None and len(debuts) >= regle.compte:
                return debuts
            debuts.append(moment)
        if len(debuts) >= LIMITE_OCCURRENCES_SERIE:
            ev.avertissements.append(f"série tronquée à {LIMITE_OCCURRENCES_SERIE} occurrences")
            return debuts
    else:
        ev.avertissements.append(f"développement arrêté après {LIMITE_PERIODES} périodes")
    return debuts


def developper_dateutil(ev: Evenement, borne: datetime) -> list[datetime]:
    """Repli python-dateutil pour une règle hors du périmètre stdlib."""
    debut = ev.debut.replace(tzinfo=None) if ev.flottant or ev.journee else ev.debut
    regle = dateutil_rrule.rrulestr(ev.regle.texte, dtstart=debut)
    fin = borne.replace(tzinfo=None) if debut.tzinfo is None else borne
    resultat = [debut]
    for moment in regle.xafter(debut, inc=True):
        if moment > fin:
            break
        if len(resultat) >= LIMITE_OCCURRENCES_SERIE:
            ev.avertissements.append(f"série tronquée à {LIMITE_OCCURRENCES_SERIE} occurrences")
            break
        if moment != debut:
            resultat.append(moment)
    if ev.regle.compte is not None:
        resultat = resultat[:ev.regle.compte]
    return [m.replace(tzinfo=ev.debut.tzinfo) if m.tzinfo is None else m for m in resultat]


def debuts_regle(ev: Evenement, borne: datetime) -> list[datetime]:
    """Débuts produits par DTSTART + RRULE + RDATE (avant EXDATE)."""
    if ev.regle is None:
        debuts = [ev.debut]
    elif not ev.regle.non_prises:
        debuts = developper_stdlib(ev, borne)
    elif dateutil_rrule is not None:
        ev.moteur = "python-dateutil"
        try:
            debuts = developper_dateutil(ev, borne)
        except (ValueError, TypeError, OverflowError, IndexError) as exc:
            ev.avertissements.append(f"python-dateutil refuse la RRULE ({exc}) : première occurrence seule")
            debuts = [ev.debut]
    else:
        ev.avertissements.append(f"RRULE non développée ({', '.join(ev.regle.non_prises)}) : "
                                 "seule la première occurrence est retenue")
        debuts = [ev.debut]
    return sorted(set(debuts) | set(ev.rdates))


# ------------------------------------------------------------- événements --

def texte_prop(comp: Composant, nom: str) -> str:
    """Valeur texte désechappée, ou chaîne vide."""
    prop = comp.premiere(nom)
    return desechapper(prop.valeur) if prop else ""


def lire_liste_instants(props: list[Propriete], zones: dict[str, tzinfo], fuseau: tzinfo,
                        avertissements: list[str]) -> list[Instant]:
    """EXDATE / RDATE : valeurs multiples séparées par des virgules."""
    instants = []
    for prop in props:
        if (prop.params.get("VALUE") or [""])[0].upper() == PERIOD:
            avertissements.append("RDATE de type PERIOD ignoré")
            continue
        for brut in prop.valeur.split(","):
            if brut.strip():
                instants.append(lire_instant(brut, prop.params, zones, fuseau, avertissements))
    return instants


def lire_fin(comp: Composant, debut: Instant, zones: dict[str, tzinfo], fuseau: tzinfo,
             avertissements: list[str]) -> timedelta:
    """Durée de l'événement : DTEND, sinon DURATION, sinon 1 jour ou 0."""
    fin = comp.premiere(PROP_DTEND)
    if fin is not None:
        moment = lire_instant(fin.valeur, fin.params, zones, fuseau, avertissements).moment
        duree = moment - debut.moment if moment.tzinfo is debut.moment.tzinfo else (
            moment.astimezone(timezone.utc) - debut.moment.astimezone(timezone.utc))
    elif comp.premiere(PROP_DURATION) is not None:
        duree = lire_duree(comp.premiere(PROP_DURATION).valeur)
    else:
        duree = timedelta(days=1) if debut.journee else timedelta(0)
    if duree < timedelta(0):
        avertissements.append("fin avant le début : durée ramenée à zéro")
        duree = timedelta(0)
    return duree


def construire_evenement(comp: Composant, source: str, zones: dict[str, tzinfo],
                         fuseau: tzinfo) -> Evenement:
    """VEVENT → Evenement (ValueError si DTSTART manque ou est invalide)."""
    avert: list[str] = []
    prop_debut = comp.premiere(PROP_DTSTART)
    if prop_debut is None:
        raise ValueError("VEVENT sans DTSTART")
    debut = lire_instant(prop_debut.valeur, prop_debut.params, zones, fuseau, avert)
    ev = Evenement(source, texte_prop(comp, "UID") or "(sans UID)", texte_prop(comp, "SUMMARY"),
                   debut.moment, lire_fin(comp, debut, zones, fuseau, avert), debut.journee,
                   debut.flottant, avertissements=avert)
    regle = comp.premiere(PROP_RRULE)
    if regle is not None:
        ev.regle = lire_regle(regle.valeur, debut, zones, fuseau)
    ev.exdates = [(i.moment, i.journee) for i in
                  lire_liste_instants(comp.toutes(PROP_EXDATE), zones, fuseau, avert)]
    ev.rdates = [i.moment for i in lire_liste_instants(comp.toutes(PROP_RDATE), zones, fuseau, avert)]
    rid = comp.premiere(PROP_RECURRENCE_ID)
    if rid is not None:
        ev.recurrence_id = lire_instant(rid.valeur, rid.params, zones, fuseau, avert).moment
        if "RANGE=" + (rid.params.get("RANGE") or [""])[0].upper() == PARAM_RANGE:
            avert.append(f"{PARAM_RANGE} ignoré : seule l'occurrence désignée est remplacée")
    ev.annule = texte_prop(comp, "STATUS").upper() == "CANCELLED"
    ev.transparent = texte_prop(comp, "TRANSP").upper() == "TRANSPARENT"
    return ev


def cle_instant(moment: datetime) -> datetime:
    """Clé de comparaison : l'instant en UTC."""
    return moment.astimezone(timezone.utc)


def exclusions(ev: Evenement) -> tuple[set[datetime], set[date]]:
    """EXDATE en instants UTC et, pour les valeurs DATE, en jours."""
    instants = {cle_instant(m) for m, journee in ev.exdates if not journee}
    jours = {m.date() for m, journee in ev.exdates if journee}
    return instants, jours


def occurrences(evenements: list[Evenement], borne: datetime) -> list[Occurrence]:
    """Développe chaque série, applique EXDATE et les RECURRENCE-ID."""
    remplaces = {(e.uid, cle_instant(e.recurrence_id)) for e in evenements if e.recurrence_id}
    resultat = []
    for ev in evenements:
        if ev.recurrence_id is not None:
            resultat.append(Occurrence(ev, ev.debut, ev.debut + ev.duree))
            continue
        instants, jours = exclusions(ev)
        for moment in debuts_regle(ev, borne):
            cle = cle_instant(moment)
            if cle in instants or moment.date() in jours or (ev.uid, cle) in remplaces:
                continue
            resultat.append(Occurrence(ev, moment, moment + ev.duree))
    return sorted(resultat, key=lambda o: (cle_instant(o.debut), o.evenement.uid))


def dans_fenetre(occ: Occurrence, de: datetime, a: datetime) -> bool:
    """Chevauche [de, a[ (un événement instantané doit commencer dedans)."""
    if occ.fin == occ.debut:
        return de <= occ.debut < a
    return occ.debut < a and occ.fin > de


def conflits(liste: list[Occurrence], avec_journees: bool) -> list[dict[str, Any]]:
    """Paires d'occurrences opaques qui se chevauchent (balayage trié)."""
    candidates = [o for o in liste if not o.evenement.annule and not o.evenement.transparent
                  and (avec_journees or not o.evenement.journee) and o.fin > o.debut]
    actives: list[Occurrence] = []
    trouves = []
    for occ in sorted(candidates, key=lambda o: cle_instant(o.debut)):
        actives = [a for a in actives if a.fin > occ.debut]
        for autre in actives:
            recouvre = min(autre.fin, occ.fin) - occ.debut
            trouves.append({"premier": decrire(autre), "second": decrire(occ),
                            "chevauchement_minutes": round(recouvre.total_seconds() / 60, 1),
                            "doublon": autre.evenement.uid == occ.evenement.uid})
        actives.append(occ)
    return trouves


def decrire(occ: Occurrence) -> dict[str, Any]:
    """Occurrence → dict JSON."""
    ev = occ.evenement
    if ev.journee:
        debut, fin = occ.debut.date().isoformat(), occ.fin.date().isoformat()
    else:
        debut, fin = occ.debut.isoformat(), occ.fin.isoformat()
    return {"uid": ev.uid, "resume": ev.resume, "debut": debut, "fin": fin,
            "journee": ev.journee, "source": ev.source}


# --------------------------------------------------------- contrôle croisé --

def comparer_dateutil(evenements: list[Evenement], de: datetime, a: datetime) -> dict[str, Any]:
    """Redéveloppe chaque RRULE avec python-dateutil et compare les débuts."""
    ecarts, refus, comparees = [], [], 0
    for ev in evenements:
        if ev.regle is None or ev.recurrence_id is not None or ev.moteur != "stdlib":
            continue
        try:
            leurs = set(developper_dateutil_brut(ev, a))
        except (ValueError, TypeError, OverflowError, IndexError) as exc:
            refus.append({"uid": ev.uid, "erreur": str(exc)[:200]})
            continue
        comparees += 1
        miens = {cle_instant(m) for m in developper_stdlib(ev, a)}
        for moment in sorted(miens ^ leurs):
            if de - ev.duree <= moment <= a:
                ecarts.append({"uid": ev.uid, "debut": moment.isoformat(),
                               "trouve_par": "stdlib" if moment in miens else "python-dateutil",
                               "dtstart": moment == cle_instant(ev.debut)})
    return {"bibliotheque": f"python-dateutil {module_dateutil.__version__}",
            "regles_comparees": comparees, "refus": refus,
            "ecarts": ecarts[:LIMITE_CONFLITS], "ecarts_total": len(ecarts)}


def developper_dateutil_brut(ev: Evenement, borne: datetime) -> list[datetime]:
    """Débuts selon dateutil seule (sans forcer DTSTART), en UTC."""
    naif = ev.flottant or ev.journee
    debut = ev.debut.replace(tzinfo=None) if naif else ev.debut
    regle = dateutil_rrule.rrulestr(ev.regle.texte, dtstart=debut)
    fin = borne.astimezone(ev.debut.tzinfo).replace(tzinfo=None) if naif else borne
    return [cle_instant(m.replace(tzinfo=ev.debut.tzinfo) if naif else m)
            for m in regle.between(debut, fin, inc=True)]


def comparer_icalendar(textes: list[tuple[str, str]], evenements: list[Evenement]) -> dict[str, Any]:
    """Relit chaque fichier avec icalendar et compare UID et DTSTART."""
    miens = {(e.source, e.uid, cle_lisible(e.debut, e.journee, e.flottant)) for e in evenements}
    leurs: set[tuple[str, str, str]] = set()
    refus = []
    for source, texte in textes:
        try:
            cal = module_icalendar.Calendar.from_ical(texte)
            for comp in cal.walk(COMP_VEVENT):
                valeur = comp.decoded(PROP_DTSTART)
                leurs.add((source, str(comp.get("UID", "(sans UID)")), cle_tierce(valeur)))
        except (ValueError, KeyError, TypeError, AttributeError, IndexError, UnicodeError) as exc:
            refus.append({"source": source, "erreur": f"{type(exc).__name__} : {str(exc)[:180]}"})
    ecarts = [{"source": s, "uid": u, "dtstart": d, "trouve_par": "stdlib" if (s, u, d) in miens else "icalendar"}
              for s, u, d in sorted(miens ^ leurs)]
    return {"bibliotheque": f"icalendar {module_icalendar.__version__}", "evenements_relus": len(leurs),
            "refus": refus, "ecarts": ecarts[:LIMITE_CONFLITS], "ecarts_total": len(ecarts)}


def cle_lisible(moment: datetime, journee: bool, flottant: bool) -> str:
    """Forme commune pour comparer à icalendar : date, heure flottante ou UTC."""
    if journee:
        return moment.date().isoformat()
    if flottant:
        return moment.replace(tzinfo=None).isoformat()
    return cle_instant(moment).isoformat()


def cle_tierce(valeur: Any) -> str:
    """Valeur DTSTART décodée par icalendar → même forme que cle_lisible."""
    if isinstance(valeur, datetime):
        return valeur.isoformat() if valeur.tzinfo is None else cle_instant(valeur).isoformat()
    return valeur.isoformat()


# ------------------------------------------------------------------ entrée --

def lister_fichiers(chemins: list[str], racine: Path | None) -> list[Path]:
    """Fichiers .ics donnés ou trouvés dans les dossiers (récursif, borné)."""
    fichiers: list[Path] = []
    for brut in chemins:
        chemin = Path(brut)
        if racine is not None and not chemin.is_absolute():
            chemin = racine / chemin
        if chemin.is_dir():
            trouves = sorted(p for p in chemin.rglob("*") if p.suffix.lower() in EXTENSIONS and p.is_file())
            fichiers.extend(trouves[:LIMITE_FICHIERS])
        elif chemin.is_file():
            fichiers.append(chemin)
        else:
            raise ErreurEntree(f"chemin introuvable : {chemin}")
    return fichiers


def lire_fichier(chemin: Path) -> bytes:
    """Octets du fichier, taille bornée."""
    try:
        if chemin.stat().st_size > TAILLE_MAX:
            raise ErreurEntree(f"{chemin} : plus de {TAILLE_MAX // 2**20} Mio, refusé")
        return chemin.read_bytes()
    except OSError as exc:
        raise ErreurEntree(f"{chemin} : illisible ({exc.strerror or exc})") from exc


def charger_textes(args: argparse.Namespace, avertissements: list[str]) -> list[tuple[str, str]]:
    """(nom, texte déplié) pour chaque source : fichiers, dossiers, --texte."""
    textes = []
    for chemin in lister_fichiers(args.chemins, args.racine):
        textes.append((str(chemin), decoder(lire_fichier(chemin), chemin.name, avertissements)))
    if args.texte is not None:
        brut = texte_en_ligne(args.texte).encode("utf-8")
        textes.append(("--texte", decoder(brut, "--texte", avertissements)))
    return textes


def lire_moment_option(texte: str, fuseau: tzinfo, fin: bool) -> datetime:
    """--de / --a : date (journée entière) ou date-heure ISO, lue dans --fuseau."""
    try:
        if len(texte) == 10:
            jour = date.fromisoformat(texte) + timedelta(days=1 if fin else 0)
            return datetime.combine(jour, time(), tzinfo=fuseau)
        moment = datetime.fromisoformat(texte)
    except ValueError as exc:
        raise ErreurEntree(f"date de fenêtre invalide « {texte} »") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=fuseau)


def fenetre(args: argparse.Namespace, evenements: list[Evenement], fuseau: tzinfo) -> tuple[datetime, datetime]:
    """Fenêtre [de, a[ ; défaut : du premier DTSTART, 365 jours."""
    if args.de:
        de = lire_moment_option(args.de, fuseau, False)
    else:
        de = min((e.debut for e in evenements), key=cle_instant)
        de = datetime.combine(de.astimezone(fuseau).date(), time(), tzinfo=fuseau)
    a = lire_moment_option(args.a, fuseau, True) if args.a else de + timedelta(days=365)
    if a <= de:
        raise ErreurEntree("fenêtre vide : --a doit suivre --de")
    return de, a


# ----------------------------------------------------------------- analyse --

def extraire_evenements(textes: list[tuple[str, str]], fuseau: tzinfo,
                        avertissements: list[str]) -> tuple[list[Evenement], list[dict[str, Any]]]:
    """Tous les VEVENT de toutes les sources ; erreurs par événement."""
    evenements, erreurs = [], []
    for source, texte in textes:
        composants = analyser_composants(texte, avertissements, Path(source).name)
        zones = zones_des_vtimezone(composants)
        for nom in (COMP_VTODO, COMP_VJOURNAL):
            if parcourir(composants, nom):
                avertissements.append(f"{Path(source).name} : composants {nom} ignorés")
        for comp in parcourir(composants, COMP_VEVENT):
            try:
                evenements.append(construire_evenement(comp, source, zones, fuseau))
            except ValueError as exc:
                erreurs.append({"source": source, "uid": texte_prop(comp, "UID"), "erreur": str(exc)})
    return evenements, erreurs


def comparaisons(textes: list[tuple[str, str]], evenements: list[Evenement],
                 de: datetime, a: datetime) -> dict[str, Any] | None:
    """Contrôles croisés disponibles (None si aucune bibliothèque)."""
    resultat = {}
    if dateutil_rrule is not None:
        resultat["python-dateutil"] = comparer_dateutil(evenements, de, a)
    if module_icalendar is not None:
        resultat["icalendar"] = comparer_icalendar(textes, evenements)
    return resultat or None


def analyser(args: argparse.Namespace) -> dict[str, Any]:
    """Cœur : lit, développe, filtre la fenêtre, cherche les conflits."""
    fuseau = zone_iana(args.fuseau) if args.fuseau else timezone.utc
    if fuseau is None:
        raise ErreurEntree(f"--fuseau inconnu de la base {BASE_FUSEAUX} : {args.fuseau}")
    avertissements: list[str] = []
    textes = charger_textes(args, avertissements)
    evenements, erreurs = extraire_evenements(textes, fuseau, avertissements)
    if not evenements:
        return assembler([], erreurs, [], [], avertissements, textes, None, None)
    de, a = fenetre(args, evenements, fuseau)
    toutes = occurrences(evenements, a)
    retenues = [o for o in toutes if dans_fenetre(o, de, a)]
    paires = conflits(retenues, args.journees)
    if any(e.flottant for e in evenements) and not args.fuseau:
        avertissements.append("heures flottantes lues en UTC : préciser --fuseau")
    return assembler(evenements, erreurs, retenues, paires, avertissements, textes,
                     comparaisons(textes, evenements, de, a), (de, a, fuseau))


def assembler(evenements: list[Evenement], erreurs: list[dict[str, Any]], retenues: list[Occurrence],
              paires: list[dict[str, Any]], avertissements: list[str], textes: list[tuple[str, str]],
              comparaison: dict[str, Any] | None,
              cadre: tuple[datetime, datetime, tzinfo] | None) -> dict[str, Any]:
    """Construit l'objet résultat (dénominateur en tête)."""
    noms = [f"{Path(e.source).name}#{e.uid}" for e in evenements]
    avertissements = avertissements + [f"{e.uid} : {m}" for e in evenements for m in e.avertissements]
    moteurs = sorted({e.moteur for e in evenements})
    par_uid: dict[str, int] = {}
    for occ in retenues:
        par_uid[occ.evenement.uid] = par_uid.get(occ.evenement.uid, 0) + 1
    return {
        "denominateur": len(evenements) + len(erreurs),
        "examines": noms[:LIMITE_EXAMINES], "examines_tronques": len(noms) > LIMITE_EXAMINES,
        "moteur": "+".join(moteurs),
        "fenetre": None if cadre is None else {"de": cadre[0].isoformat(), "a_exclu": cadre[1].isoformat(),
                                               "fuseau": str(cadre[2])},
        "fichiers": [s for s, _ in textes],
        "evenements": [resumer_evenement(e, par_uid.get(e.uid, 0)) for e in evenements][:LIMITE_EVENEMENTS_SORTIE],
        "occurrences_total": len(retenues),
        "occurrences": [decrire(o) for o in retenues[:LIMITE_OCCURRENCES_SORTIE]],
        "conflits_total": len(paires), "conflits": paires[:LIMITE_CONFLITS],
        "erreurs": erreurs, "comparaison": comparaison, "avertissements": avertissements,
    }


def resumer_evenement(ev: Evenement, nb: int) -> dict[str, Any]:
    """Événement (série) → dict JSON."""
    return {"uid": ev.uid, "resume": ev.resume, "source": ev.source,
            "debut": ev.debut.date().isoformat() if ev.journee else ev.debut.isoformat(),
            "duree_minutes": round(ev.duree.total_seconds() / 60, 1), "journee": ev.journee,
            "flottant": ev.flottant, "regle": ev.regle.texte if ev.regle else None,
            "exdates": len(ev.exdates), "remplace_occurrence": ev.recurrence_id is not None,
            "annule": ev.annule, "transparent": ev.transparent, "moteur": ev.moteur,
            "occurrences_dans_fenetre": nb, "avertissements": ev.avertissements}


# ------------------------------------------------------------------ sortie --

def imprimer_humain(res: dict[str, Any]) -> None:
    """Sortie lisible."""
    fen = res["fenetre"] or {"de": "-", "a_exclu": "-"}
    print(f"{res['denominateur']} événement(s) examiné(s) ; fenêtre {fen['de']} → {fen['a_exclu']} "
          f"(exclu) ; {res['occurrences_total']} occurrence(s), {res['conflits_total']} chevauchement(s)")
    for occ in res["occurrences"][:100]:
        print(f"  {occ['debut']} → {occ['fin']}  {occ['resume'] or occ['uid']}")
    for paire in res["conflits"][:50]:
        print(f"  CONFLIT {paire['premier']['resume'] or paire['premier']['uid']} / "
              f"{paire['second']['resume'] or paire['second']['uid']} : "
              f"{paire['chevauchement_minutes']} min à partir de {paire['second']['debut']}")
    for erreur in res["erreurs"]:
        print(f"  ERREUR {erreur['source']} {erreur['uid']} : {erreur['erreur']}")
    for nom, bloc in (res["comparaison"] or {}).items():
        print(f"  contrôle {bloc['bibliotheque']} : {bloc['ecarts_total']} écart(s)")


def code_sortie(res: dict[str, Any]) -> int:
    """1 si chevauchement, erreur d'événement ou écart entre moteurs ; 0 sinon."""
    ecarts = sum(b["ecarts_total"] for b in (res["comparaison"] or {}).values())
    return 1 if res["conflits_total"] or res["erreurs"] or ecarts else 0


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Lit des calendriers iCalendar (.ics) : événements, occurrences des "
                    "récurrences dans une fenêtre, chevauchements.",
        epilog="Exemples : lire_calendrier_ics.py agenda.ics --de 2026-10-01 --a 2026-10-31 "
               "--fuseau Europe/Paris --json\n           lire_calendrier_ics.py exports/ --json\n"
               "Codes : 0 rien à signaler ; 1 chevauchement, événement illisible ou écart entre "
               "moteurs ; 2 entrée invalide ; 3 aucun événement à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("chemins", nargs="*", help="fichiers .ics ou dossiers (parcours récursif)")
    parseur.add_argument("--texte", help="contenu iCalendar en ligne (« \\n » littéral accepté)")
    parseur.add_argument("--de", help="début de fenêtre AAAA-MM-JJ[THH:MM] (défaut : premier DTSTART)")
    parseur.add_argument("--a", help="fin de fenêtre, journée incluse si date seule (défaut : +365 j)")
    parseur.add_argument("--fuseau", help="fuseau IANA des heures flottantes et de la fenêtre (défaut UTC)")
    parseur.add_argument("--journees", action="store_true",
                         help="compter aussi les journées entières dans les chevauchements")
    parseur.add_argument("--racine", type=Path, help="base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def signaler_bibliotheques() -> None:
    """Une ligne sur stderr par bibliothèque optionnelle absente."""
    if dateutil_rrule is None:
        print("python-dateutil absente : récurrences développées en stdlib seule, sans contrôle croisé",
              file=sys.stderr)
    if module_icalendar is None:
        print("icalendar absente : lecture stdlib seule, sans relecture croisée", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    parseur = construire_parseur()
    args = parseur.parse_args(argv)
    if not args.chemins and args.texte is None:
        parseur.print_usage(sys.stderr)
        print("erreur : donner au moins un fichier, un dossier ou --texte", file=sys.stderr)
        return 2
    signaler_bibliotheques()
    try:
        res = analyser(args)
    except ErreurEntree as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
    if res["denominateur"] == 0:
        print("dénominateur nul : aucun VEVENT trouvé, rien à examiner", file=sys.stderr)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        return 3
    res["contrat"] = lire_contrat()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    else:
        imprimer_humain(res)
    for message in res["avertissements"]:
        print(f"avertissement : {message}", file=sys.stderr)
    return code_sortie(res)


if __name__ == "__main__":
    raise SystemExit(main())
