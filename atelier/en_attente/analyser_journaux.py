"""Résume des journaux applicatifs ou système : volumes par niveau, période couverte, erreurs dominantes.

Pourquoi : compter les erreurs avec grep mesure des mots, pas des niveaux. Mesuré le 2026-10-02
sur cette machine : ``grep -c -i error`` compte 18 lignes dans le journal npm
2026-07-23T23_14_24_947Z-debug-0.log et 32 dans /var/log/dpkg.log, alors qu'aucune ligne de
ces deux fichiers n'est de niveau erreur (npm : 1935 silly, 944 http, 12 verbose, 7 info,
5 notice, 1 warn ; dpkg.log ne porte pas de niveau).

QUESTION
    Que disent ces journaux : volumes par niveau, erreurs dominantes, période couverte ?
MESURE
    Lecture en flux, ligne à ligne bornée, de fichiers .log (et rotations .1, .2…), .jsonl,
    .gz, .bz2, .xz (décompression à la volée) ou de dossiers. Reconnaissance par ligne :
    JSON lines (level/severity/levelname/log.level, échelles syslog 0-7 et pino 10-60),
    logfmt, syslog RFC 5424 et RFC 3164 (sévérité tirée du PRI), syslog fichier, logging
    Python (« ERROR:nom:msg » et formats horodatés), npm, journaux d'erreurs Apache et nginx,
    journaux d'accès au format commun (5xx = erreur, 4xx = avertissement), texte horodaté.
    Les lignes indentées et les traces d'exception sont rattachées à l'enregistrement
    précédent. Comptes par niveau normalisé, période (premier et dernier horodatage), et
    gabarits des messages d'erreur : masquage des nombres, hexadécimaux, uuid, dates, IP,
    URL, chemins, puis regroupement à la Drain (même longueur, similarité ≥ 0,5), avec
    première et dernière occurrence (fichier:ligne, horodatage) et type d'exception.
HYPOTHÈSES
    Un enregistrement par ligne, hors continuations ; texte utf-8 (octets invalides
    remplacés) ; niveaux exprimés par des mots usuels (error, warn, fatal…) ou des codes
    numériques connus. Sans fuseau dans l'horodatage, l'heure est prise telle quelle.
LIMITES
    Un niveau absent de la ligne reste INCONNU (dpkg.log, sorties brutes) : aucune
    déduction d'après le contenu du message. Les années manquantes (syslog RFC 3164) ne sont
    pas devinées sans --annee. Un journal JSON multi-lignes n'est pas reconnu. Gabarits
    bornés par --max-gabarits ; lecture bornée par --max-lignes.
CONTRE-EXEMPLES
    Constaté : une ligne « 2026-10-01 12:00:00 Connexion ERROR détectée » est comptée ERROR
    alors que le mot est dans le message (motif « horodatage nom niveau message »). Constaté :
    dans un fichier sans aucun format reconnu, une ligne indentée est rattachée à la ligne
    précédente comme une continuation, même si elle est indépendante.
INVOCATION
    {outil} {fichier} --json
DOMAINE
    Journaux d'applications et de serveurs Linux, de quelques Mo à quelques Go, en revue
    d'incident ou en contrôle de CI (code 1 s'il y a des erreurs).
"""

from __future__ import annotations

import argparse
import bz2
import gzip
import json
import lzma
import platform
import re
import sys
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Iterator, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from drain3 import TemplateMiner
    from drain3.template_miner_config import TemplateMinerConfig
except ImportError:
    TemplateMiner = None
    TemplateMinerConfig = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
             INTITULE_INVOCATION, "DOMAINE")

NIVEAU_INCONNU = "INCONNU"
NIVEAU_ERREUR = "ERROR"
NIVEAUX = ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
MAX_EXAMINES = 200
LONGUEUR_LIGNE = 65536
SONDE_BINAIRE = 8192

SYNONYMES = MappingProxyType({
    **{m: "TRACE" for m in ("trace", "trc", "finest", "finer")},
    **{m: "DEBUG" for m in ("debug", "dbg", "fine", "verbose")},
    **{m: "INFO" for m in ("info", "inf", "information", "informational", "notice", "note")},
    **{m: "WARNING" for m in ("warn", "warning", "wrn")},
    **{m: "ERROR" for m in ("error", "err", "severe", "fail", "failure")},
    **{m: "CRITICAL" for m in ("critical", "crit", "fatal", "ftl", "alert", "emerg", "emergency", "panic")},
})
SYSLOG = ("CRITICAL", "CRITICAL", "CRITICAL", "ERROR", "WARNING", "INFO", "INFO", "DEBUG")
PINO = MappingProxyType({10: "TRACE", 20: "DEBUG", 30: "INFO", 40: "WARNING", 50: "ERROR", 60: "CRITICAL"})
PYTHON_NUM = MappingProxyType({10: "DEBUG", 20: "INFO", 30: "WARNING", 40: "ERROR", 50: "CRITICAL"})
MOIS = MappingProxyType({m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)})
CLES_NIVEAU = ("level", "lvl", "severity", "levelname", "loglevel", "log.level", "@level", "level_name", "sev")
CLES_TEMPS = ("time", "timestamp", "ts", "@timestamp", "datetime", "date", "asctime", "t", "eventTime", "logged_at")
CLES_MESSAGE = ("msg", "message", "event", "@message", "text", "log", "error", "err")

MOT_NIVEAU = (r"(?:TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|ERR|CRITICAL|CRIT|FATAL|ALERT|EMERG|SEVERE"
              r"|FINE|FINER|FINEST|PANIC)")
HORODATAGE = r"\d{4}[-/]\d\d[-/]\d\d[T ]\d\d:\d\d(?::\d\d(?:[.,]\d+)?)?(?:Z|[+-]\d\d:?\d\d)?"
RE_HORODATE = re.compile(rf"^\[?({HORODATAGE})\]?[\s,|]+(.*)$")
RE_APRES_TS = (
    re.compile(rf"^[\[(<]({MOT_NIVEAU})[\])>]:?\s*(.*)$", re.I),
    re.compile(rf"^({MOT_NIVEAU})(?::\s*|\s+)(.*)$"),
    re.compile(rf"^-\s+\S+\s+-\s+({MOT_NIVEAU})\s+-\s+(.*)$"),
    re.compile(rf"^\[[^\]]*\]\s+({MOT_NIVEAU})\s+(.*)$"),
    re.compile(rf"^\S+\s+({MOT_NIVEAU})\s+(.*)$"),
)
RE_NPM = re.compile(r"^\d+ (silly|verbose|http|timing|info|notice|warn|error) (.*)$")
NIVEAUX_NPM = MappingProxyType({"silly": "TRACE", "verbose": "DEBUG", "http": "INFO", "timing": "INFO",
                                "info": "INFO", "notice": "INFO", "warn": "WARNING", "error": "ERROR"})
RE_PYTHON = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL):([^:]*):(.*)$")
RE_SYSLOG_5424 = re.compile(r"^<(\d{1,3})>1 (\S+) \S+ \S+ \S+ \S+ (?:-|\[.*?\])\s?(.*)$")
RE_SYSLOG_3164 = re.compile(r"^(?:<(\d{1,3})>)?([A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d) \S+ [^:\[\s]+(?:\[\d+\])?: (.*)$")
RE_SYSLOG_ISO = re.compile(rf"^({HORODATAGE}) \S+ [^:\[\s]+(?:\[\d+\])?: (.*)$")
RE_APACHE = re.compile(r"^\[(\w{3} (\w{3}) +(\d{1,2}) (\d\d:\d\d:\d\d)(?:\.\d+)? (\d{4}))\] \[(?:[\w-]+:)?(\w+)\] (.*)$")
RE_CLF = re.compile(r'^\S+ \S+ \S+ \[(\d\d)/(\w{3})/(\d{4}):(\d\d:\d\d:\d\d) ([+-]\d{4})\] "([^"]*)" (\d{3}) ')
RE_LOGFMT = re.compile(r'([\w.@-]+)=("(?:[^"\\]|\\.)*"|\S*)')
RE_TEXTE = re.compile(rf"^[\[(<]?({MOT_NIVEAU})[\])>]?:?\s+(.*)$", re.I)
RE_CONTINUATION = re.compile(r"^(?:\s|Traceback \(most recent call last\)|Caused by:|During handling of the "
                             r"above exception|The above exception was|\.\.\. \d+ more)")
RE_EXCEPTION = re.compile(r"^((?:[A-Za-z_][\w]*\.)*[A-Za-z_]\w*(?:Error|Exception|Warning|Exit|Interrupt))(?::|$)")
RE_NOM_JOURNAL = re.compile(r"^(?:.*\.(?:log|jsonl|ndjson|out|err)|syslog|messages|kern|auth|daemon|debug)"
                            r"(?:\.\d+)?(?:\.(?:gz|bz2|xz))?$", re.I)
MASQUES = (
    (re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+"), "<URL>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "<EMAIL>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    (re.compile(rf"\b{HORODATAGE}"), "<DATE>"),
    (re.compile(r"\b\d\d:\d\d:\d\d(?:[.,]\d+)?\b"), "<HEURE>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<IP>"),
    (re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b"), "<IP>"),
    (re.compile(r"(?<![\w.~])(?:~|\.{1,2})?(?:/[\w.@%+=,~-]+){2,}/?"), "<CHEMIN>"),
    (re.compile(r"\b[A-Za-z]:\\[^\s\"']+"), "<CHEMIN>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    (re.compile(r"\b(?=[0-9a-fA-F]*\d)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{8,}\b"), "<HEX>"),
    (re.compile(r"(?<![A-Za-z<])[-+]?\d+(?:\.\d+)?"), "<NUM>"),
)


class EntreeInvalide(Exception):
    """Chemin inexistant, fichier binaire, option incohérente (code 2)."""


@dataclass
class Enregistrement:
    """Une entrée de journal reconnue."""

    niveau: str
    horodatage: datetime | None
    message: str
    format: str
    sans_annee: bool = False


@dataclass
class Gabarit:
    """Un regroupement de messages : jetons du gabarit, comptes, occurrences extrêmes."""

    jetons: list[str]
    occurrences: int = 0
    niveaux: dict[str, int] = field(default_factory=dict)
    exceptions: dict[str, int] = field(default_factory=dict)
    premiere: dict[str, Any] = field(default_factory=dict)
    derniere: dict[str, Any] = field(default_factory=dict)
    exemple: str = ""


@dataclass
class Stats:
    """Accumulateur global et par fichier."""

    lignes: int = 0
    enregistrements: int = 0
    continuations: int = 0
    tronquees: int = 0
    par_niveau: dict[str, int] = field(default_factory=dict)
    par_format: dict[str, int] = field(default_factory=dict)
    debut: datetime | None = None
    fin: datetime | None = None
    horodates: int = 0
    sans_annee: int = 0
    avec_fuseau: int = 0
    sans_fuseau: int = 0
    annee_inconnue: bool = False


@dataclass
class Regroupeur:
    """Gabarits à la Drain (stdlib) ou par drain3 ; borne le nombre de gabarits."""

    moteur: str
    maximum: int
    seaux: dict[tuple[int, str], list[Gabarit]] = field(default_factory=dict)
    par_id: dict[int, Gabarit] = field(default_factory=dict)
    total: int = 0
    hors_limite: int = 0
    mineur: Any = None


# --------------------------------------------------------------------------- contrat


def extraire_contrat(doc: str) -> dict[str, str]:
    """Découpe la docstring du module en sections selon les intitulés du socle."""
    contrat: dict[str, str] = {}
    courant = "POURQUOI"
    lignes: list[str] = []
    for ligne in doc.splitlines():
        if ligne.strip() in INTITULES and not ligne.startswith(" "):
            contrat[courant] = " ".join(x.strip() for x in lignes if x.strip())
            courant, lignes = ligne.strip(), []
        else:
            lignes.append(ligne)
    contrat[courant] = " ".join(x.strip() for x in lignes if x.strip())
    return contrat


# --------------------------------------------------------------------------- niveaux et dates


def normaliser_niveau(valeur: Any, echelle: str = "") -> str:
    """Niveau normalisé depuis un mot, un code syslog (0-7), pino (10-60) ou Python (levelno)."""
    if isinstance(valeur, bool):
        return NIVEAU_INCONNU
    if isinstance(valeur, (int, float)):
        entier = int(valeur)
        if echelle == "python":
            return PYTHON_NUM.get(entier, NIVEAU_INCONNU)
        if 0 <= entier <= 7:
            return SYSLOG[entier]
        return PINO.get(entier, NIVEAU_INCONNU)
    texte = str(valeur).strip().lower()
    if texte.isdigit():
        return normaliser_niveau(int(texte), echelle)
    return SYNONYMES.get(texte, NIVEAU_INCONNU)


def lire_iso(texte: str) -> datetime | None:
    """Horodatage ISO-ish (séparateurs / ou -, virgule décimale, nanosecondes, Z)."""
    propre = texte.strip().replace("/", "-", 2)
    try:
        return datetime.fromisoformat(propre)
    except ValueError:
        return None


def lire_epoque(valeur: float) -> datetime | None:
    """Horodatage numérique : secondes, millisecondes, microsecondes ou nanosecondes."""
    for diviseur, plafond in ((1, 1e11), (1e3, 1e14), (1e6, 1e17), (1e9, 1e20)):
        if abs(valeur) < plafond:
            try:
                return datetime.fromtimestamp(valeur / diviseur, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
    return None


def lire_temps_json(valeur: Any) -> datetime | None:
    """Horodatage d'un champ JSON : chaîne ISO ou nombre (époque)."""
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        return lire_epoque(float(valeur))
    if isinstance(valeur, str):
        return lire_iso(valeur)
    return None


def date_syslog(texte: str, annee: int | None) -> datetime | None:
    """« Oct  1 23:36:08 » ; l'année vient de --annee, sinon 2000 marquée inconnue."""
    try:
        mois = MOIS[texte[:3].lower()]
        jour = int(texte[4:6])
        heure, minute, seconde = (int(x) for x in texte[7:15].split(":"))
        return datetime(annee or 2000, mois, jour, heure, minute, seconde)
    except (KeyError, ValueError):
        return None


# --------------------------------------------------------------------------- reconnaissance


def reconnaitre(ligne: str, annee: int | None) -> Enregistrement:
    """Format, niveau, horodatage et message d'une ligne (premier format qui convient)."""
    if ligne.startswith("{"):
        trouve = reconnaitre_json(ligne)
        if trouve:
            return trouve
    for essai in (reconnaitre_syslog_pri, reconnaitre_apache, reconnaitre_clf, reconnaitre_horodate,
                  reconnaitre_syslog, reconnaitre_python, reconnaitre_logfmt, horodate_sans_niveau):
        trouve = essai(ligne, annee)
        if trouve:
            return trouve
    m = RE_TEXTE.match(ligne)
    if m and (m.group(1).isupper() or ligne[:1] in "[(<" or ligne[len(m.group(1)):len(m.group(1)) + 1] == ":"):
        return Enregistrement(normaliser_niveau(m.group(1)), None, m.group(2), "texte")
    return Enregistrement(NIVEAU_INCONNU, None, ligne, "texte")


def champ(objet: dict[str, Any], cles: Sequence[str]) -> Any:
    """Première clé présente (insensible à la casse), y compris « log.level » imbriqué."""
    minuscules = {str(k).lower(): v for k, v in objet.items()}
    for cle in cles:
        if cle.lower() in minuscules:
            return minuscules[cle.lower()]
        if "." in cle:
            tete, queue = cle.split(".", 1)
            sous = objet.get(tete)
            if isinstance(sous, dict) and queue in sous:
                return sous[queue]
    return None


def reconnaitre_json(ligne: str) -> Enregistrement | None:
    """JSON lines : niveau, horodatage, message selon les clés usuelles."""
    try:
        objet = json.loads(ligne)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(objet, dict):
        return None
    if "levelno" in objet:
        niveau = normaliser_niveau(objet["levelno"], "python")
    else:
        niveau = normaliser_niveau(champ(objet, CLES_NIVEAU)) if champ(objet, CLES_NIVEAU) is not None else NIVEAU_INCONNU
    message = champ(objet, CLES_MESSAGE)
    texte = message if isinstance(message, str) else json.dumps(message if message is not None else objet,
                                                                 ensure_ascii=False)[:500]
    return Enregistrement(niveau, lire_temps_json(champ(objet, CLES_TEMPS)), texte, "json")


def reconnaitre_logfmt(ligne: str, _annee: int | None) -> Enregistrement | None:
    """logfmt : au moins deux paires clé=valeur dont level, msg ou time."""
    if "=" not in ligne:
        return None
    paires = {k.lower(): v[1:-1].replace('\\"', '"') if v.startswith('"') else v
              for k, v in RE_LOGFMT.findall(ligne)}
    if len(paires) < 2 or not ({"level", "lvl", "msg", "message", "time", "ts", "severity"} & paires.keys()):
        return None
    niveau_txt = next((paires[k] for k in ("level", "lvl", "severity") if k in paires), None)
    temps_txt = next((paires[k] for k in ("time", "ts", "timestamp") if k in paires), None)
    message = next((paires[k] for k in ("msg", "message", "error", "err") if k in paires), ligne)
    niveau = normaliser_niveau(niveau_txt) if niveau_txt is not None else NIVEAU_INCONNU
    return Enregistrement(niveau, lire_iso(temps_txt) if temps_txt else None, message, "logfmt")


def reconnaitre_syslog_pri(ligne: str, _annee: int | None) -> Enregistrement | None:
    """syslog RFC 5424 : sévérité tirée du PRI."""
    m = RE_SYSLOG_5424.match(ligne)
    if m:
        return Enregistrement(SYSLOG[int(m.group(1)) % 8], lire_iso(m.group(2)), m.group(3), "syslog-5424")
    return None


def reconnaitre_syslog(ligne: str, annee: int | None) -> Enregistrement | None:
    """syslog RFC 3164 (PRI facultatif) et fichier syslog à horodatage ISO."""
    m = RE_SYSLOG_3164.match(ligne)
    if m:
        niveau = SYSLOG[int(m.group(1)) % 8] if m.group(1) else niveau_en_tete(m.group(3))
        return Enregistrement(niveau, date_syslog(m.group(2), annee), m.group(3), "syslog", annee is None)
    m = RE_SYSLOG_ISO.match(ligne)
    if m:
        return Enregistrement(niveau_en_tete(m.group(2)), lire_iso(m.group(1)), m.group(2), "syslog")
    return None


def niveau_en_tete(message: str) -> str:
    """Niveau annoncé en tête de message (« error: », « [ERROR] », « WARN »)."""
    m = RE_TEXTE.match(message)
    if m and (m.group(1).isupper() or message[:1] in "[(<" or message[len(m.group(1)):len(m.group(1)) + 1] == ":"):
        return normaliser_niveau(m.group(1))
    return NIVEAU_INCONNU


def reconnaitre_apache(ligne: str, _annee: int | None) -> Enregistrement | None:
    """Journal d'erreurs Apache : [Wed Oct 11 14:32:52 2000] [module:niveau] message."""
    m = RE_APACHE.match(ligne)
    if not m:
        return None
    try:
        quand = datetime(int(m.group(5)), MOIS[m.group(2).lower()], int(m.group(3)),
                         *(int(x) for x in m.group(4).split(":")))
    except (KeyError, ValueError):
        quand = None
    niveau = "TRACE" if m.group(6).lower().startswith("trace") else normaliser_niveau(m.group(6))
    return Enregistrement(niveau, quand, m.group(7), "apache")


def reconnaitre_clf(ligne: str, _annee: int | None) -> Enregistrement | None:
    """Journal d'accès au format commun : 5xx = ERROR, 4xx = WARNING, sinon INFO."""
    m = RE_CLF.match(ligne)
    if not m:
        return None
    decalage = m.group(5)
    minutes = (int(decalage[1:3]) * 60 + int(decalage[3:5])) * (-1 if decalage[0] == "-" else 1)
    try:
        quand = datetime(int(m.group(3)), MOIS[m.group(2).lower()], int(m.group(1)),
                         *(int(x) for x in m.group(4).split(":")),
                         tzinfo=timezone(timedelta(minutes=minutes)))
    except (KeyError, ValueError):
        quand = None
    statut = int(m.group(7))
    niveau = "ERROR" if statut >= 500 else ("WARNING" if statut >= 400 else "INFO")
    return Enregistrement(niveau, quand, f"{m.group(6)} -> {statut}", "acces-clf")


def reconnaitre_horodate(ligne: str, _annee: int | None) -> Enregistrement | None:
    """Texte horodaté en tête (logging Python, log4j, nginx…) avec un niveau juste après."""
    m = RE_HORODATE.match(ligne)
    if not m:
        return None
    for motif in RE_APRES_TS:
        n = motif.match(m.group(2))
        if n:
            return Enregistrement(normaliser_niveau(n.group(1)), lire_iso(m.group(1)), n.group(2), "horodate")
    return None


def horodate_sans_niveau(ligne: str, _annee: int | None) -> Enregistrement | None:
    """Texte horodaté sans niveau reconnaissable (dpkg.log, sorties de scripts)."""
    m = RE_HORODATE.match(ligne)
    return Enregistrement(NIVEAU_INCONNU, lire_iso(m.group(1)), m.group(2), "horodate") if m else None


def reconnaitre_python(ligne: str, _annee: int | None) -> Enregistrement | None:
    """Format par défaut du module logging (« NIVEAU:nom:message ») ou journal de débogage npm."""
    m = RE_PYTHON.match(ligne)
    if m:
        return Enregistrement(m.group(1), None, m.group(3), "python")
    m = RE_NPM.match(ligne)
    return Enregistrement(NIVEAUX_NPM[m.group(1)], None, m.group(2), "npm") if m else None


# --------------------------------------------------------------------------- gabarits


def masquer(message: str) -> str:
    """Remplace les parties variables (URL, UUID, dates, IP, chemins, hex, nombres)."""
    for motif, jeton in MASQUES:
        message = motif.sub(jeton, message)
    return message


def creer_regroupeur(moteur: str, maximum: int) -> Regroupeur:
    """Regroupeur stdlib, ou adossé à drain3 (configuration explicite, aucun fichier lu)."""
    regroupeur = Regroupeur(moteur, maximum)
    if moteur == "drain3":
        regroupeur.mineur = TemplateMiner(config=TemplateMinerConfig())
    return regroupeur


def trouver_gabarit(regroupeur: Regroupeur, masque: str) -> Gabarit | None:
    """Gabarit du message masqué (créé au besoin), ou None si la borne est atteinte."""
    if regroupeur.moteur == "drain3":
        resultat = regroupeur.mineur.add_log_message(masque)
        identifiant = resultat["cluster_id"]
        if identifiant not in regroupeur.par_id:
            if regroupeur.total >= regroupeur.maximum:
                return None
            regroupeur.total += 1
            regroupeur.par_id[identifiant] = Gabarit([])
        return regroupeur.par_id[identifiant]
    jetons = masque.split()
    seau = regroupeur.seaux.setdefault((len(jetons), jetons[0] if jetons and "<" not in jetons[0] else "*"), [])
    for gabarit in seau:
        egaux = sum(1 for a, b in zip(gabarit.jetons, jetons) if a in (b, "<*>"))
        if not jetons or egaux / len(jetons) >= 0.5:
            gabarit.jetons = [a if a == b else "<*>" for a, b in zip(gabarit.jetons, jetons)]
            return gabarit
    if regroupeur.total >= regroupeur.maximum:
        return None
    regroupeur.total += 1
    seau.append(Gabarit(jetons))
    return seau[-1]


def consigner_gabarit(regroupeur: Regroupeur, enr: Enregistrement, lieu: dict[str, Any]) -> Gabarit | None:
    """Ajoute un message d'erreur à son gabarit ; tient première et dernière occurrence."""
    gabarit = trouver_gabarit(regroupeur, masquer(enr.message.strip())[:2000])
    if gabarit is None:
        regroupeur.hors_limite += 1
        return None
    gabarit.occurrences += 1
    gabarit.niveaux[enr.niveau] = gabarit.niveaux.get(enr.niveau, 0) + 1
    if not gabarit.premiere:
        gabarit.premiere, gabarit.exemple = lieu, enr.message.strip()[:300]
    gabarit.derniere = lieu
    return gabarit


def texte_gabarit(regroupeur: Regroupeur, cle: int, gabarit: Gabarit) -> str:
    """Texte du gabarit (stdlib : jetons ; drain3 : gabarit de la grappe)."""
    if regroupeur.moteur == "drain3":
        for grappe in regroupeur.mineur.drain.clusters:
            if grappe.cluster_id == cle:
                return grappe.get_template()
    return " ".join(gabarit.jetons)


# --------------------------------------------------------------------------- lecture


def ouvrir(chemin: Path) -> BinaryIO:
    """Flux binaire, décompressé à la volée selon la signature (gzip, bz2, xz)."""
    with chemin.open("rb") as brut:
        tete = brut.read(6)
    if tete.startswith(b"\x1f\x8b"):
        return gzip.open(chemin, "rb")
    if tete.startswith(b"BZh"):
        return bz2.open(chemin, "rb")
    if tete.startswith(b"\xfd7zXZ\x00"):
        return lzma.open(chemin, "rb")
    return chemin.open("rb")


def lignes_bornees(flux: BinaryIO, limite: int) -> Iterator[tuple[bytes, bool]]:
    """Lignes d'au plus `limite` octets ; le reste d'une ligne trop longue est sauté."""
    while True:
        ligne = flux.readline(limite)
        if not ligne:
            return
        tronquee = not ligne.endswith(b"\n") and len(ligne) >= limite
        if tronquee:
            while (suite := flux.readline(limite)) and not suite.endswith(b"\n"):
                pass
        yield ligne, tronquee


@dataclass
class Contexte:
    """Paramètres d'analyse partagés par tous les fichiers."""

    annee: int | None
    niveau_gabarits: int
    max_lignes: int
    regroupeur: Regroupeur


def verifier_texte(chemin: Path) -> None:
    """Refuse un contenu binaire (octet nul dans les premiers Kio décompressés)."""
    try:
        with ouvrir(chemin) as sonde:
            bloc = sonde.read(SONDE_BINAIRE)
    except (OSError, EOFError, zlib.error, lzma.LZMAError) as exc:
        raise EntreeInvalide(f"illisible ou archive corrompue : {exc}") from exc
    if b"\x00" in bloc:
        raise EntreeInvalide("contenu binaire (octet nul) : pas un journal texte")


def analyser_fichier(chemin: Path, nom: str, ctx: Contexte, total: Stats) -> Stats:
    """Analyse un fichier en flux ; met à jour les statistiques globales et rend celles du fichier."""
    verifier_texte(chemin)
    stats = Stats()
    precedent: Gabarit | None = None
    try:
        with ouvrir(chemin) as flux:
            for numero, (brute, tronquee) in enumerate(lignes_bornees(flux, LONGUEUR_LIGNE), start=1):
                if total.lignes >= ctx.max_lignes:
                    raise Interruption(stats, "borne --max-lignes atteinte", borne=True)
                ligne = brute.decode("utf-8", "replace").rstrip("\r\n")
                for s in (stats, total):
                    s.lignes += 1
                    s.tronquees += tronquee
                precedent = traiter_ligne(ligne, f"{nom}:{numero}", ctx, (stats, total), precedent)
    except (OSError, EOFError, zlib.error, lzma.LZMAError) as exc:
        raise Interruption(stats, f"lecture interrompue : {exc}") from exc
    return stats


class Interruption(Exception):
    """Lecture arrêtée (borne atteinte ou flux corrompu) : porte les statistiques partielles."""

    def __init__(self, stats: Stats, raison: str, borne: bool = False) -> None:
        super().__init__(raison)
        self.stats = stats
        self.borne = borne


def traiter_ligne(ligne: str, lieu: str, ctx: Contexte, cibles: tuple[Stats, Stats],
                  precedent: Gabarit | None) -> Gabarit | None:
    """Continuation (rattachée) ou nouvel enregistrement (compté, daté, regroupé)."""
    if not ligne.strip():
        return precedent
    if cibles[0].enregistrements and RE_CONTINUATION.match(ligne) or (
            cibles[0].enregistrements and RE_EXCEPTION.match(ligne)):
        for s in cibles:
            s.continuations += 1
        exception = RE_EXCEPTION.match(ligne.strip())
        if precedent is not None and exception:
            precedent.exceptions[exception.group(1)] = precedent.exceptions.get(exception.group(1), 0) + 1
        return precedent
    enr = reconnaitre(ligne, ctx.annee)
    for s in cibles:
        compter(s, enr)
    if enr.niveau in NIVEAUX and NIVEAUX.index(enr.niveau) >= ctx.niveau_gabarits:
        endroit = {"lieu": lieu, "horodatage": formater(enr)}
        return consigner_gabarit(ctx.regroupeur, enr, endroit)
    return None


def compter(stats: Stats, enr: Enregistrement) -> None:
    """Comptes par niveau et par format ; bornes de la période."""
    stats.enregistrements += 1
    stats.par_niveau[enr.niveau] = stats.par_niveau.get(enr.niveau, 0) + 1
    stats.par_format[enr.format] = stats.par_format.get(enr.format, 0) + 1
    quand = enr.horodatage
    if quand is None:
        return
    if quand.tzinfo is not None:
        stats.avec_fuseau += 1
        quand = quand.astimezone(timezone.utc).replace(tzinfo=None)
    elif not enr.sans_annee:
        stats.sans_fuseau += 1
    if enr.sans_annee:
        stats.annee_inconnue = True
        stats.sans_annee += 1
        return
    stats.horodates += 1
    stats.debut = quand if stats.debut is None or quand < stats.debut else stats.debut
    stats.fin = quand if stats.fin is None or quand > stats.fin else stats.fin


def formater(enr: Enregistrement) -> str | None:
    """Horodatage ISO, en UTC s'il porte un fuseau ; « --MM-JJ » si l'année manque."""
    quand = enr.horodatage
    if quand is None:
        return None
    if enr.sans_annee:
        return "--" + quand.isoformat()[5:]
    if quand.tzinfo is not None:
        return quand.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return quand.isoformat()


def collecter(cibles: Sequence[str], base: Path) -> tuple[list[tuple[Path, str, bool]], list[dict[str, str]]]:
    """(chemin, affichage, explicite) des journaux ; les dossiers sont filtrés par nom."""
    fichiers: list[tuple[Path, str, bool]] = []
    invalides: list[dict[str, str]] = []
    for texte in cibles:
        chemin = Path(texte) if Path(texte).is_absolute() else base / texte
        if not chemin.exists():
            invalides.append({"fichier": texte, "erreur": "chemin inexistant"})
        elif chemin.is_dir():
            fichiers.extend((f, str(Path(texte) / f.relative_to(chemin)), False)
                            for f in sorted(chemin.rglob("*"))
                            if f.is_file() and RE_NOM_JOURNAL.match(f.name) and ".git" not in f.parts)
        elif chemin.is_file():
            fichiers.append((chemin, texte, True))
        else:
            invalides.append({"fichier": texte, "erreur": "ni fichier régulier ni dossier"})
    return fichiers, invalides


# --------------------------------------------------------------------------- rapport


def periode(stats: Stats) -> dict[str, Any]:
    """Début, fin, durée et qualité des horodatages."""
    fuseaux = ("UTC" if not stats.sans_fuseau else ("local inconnu" if not stats.avec_fuseau else "mélangés"))
    duree = (stats.fin - stats.debut).total_seconds() if stats.debut and stats.fin else None
    return {"debut": stats.debut.isoformat() if stats.debut else None,
            "fin": stats.fin.isoformat() if stats.fin else None,
            "duree_secondes": duree, "horodates": stats.horodates,
            "sans_horodatage": stats.enregistrements - stats.horodates - stats.sans_annee,
            "horodatages_sans_annee": stats.sans_annee,
            "fuseaux": fuseaux if stats.horodates else None,
            "annee_inconnue": stats.annee_inconnue}


def resume_gabarits(regroupeur: Regroupeur, top: int) -> list[dict[str, Any]]:
    """Gabarits triés par occurrences décroissantes."""
    if regroupeur.moteur == "drain3":
        paires = list(regroupeur.par_id.items())
    else:
        paires = [(i, g) for i, g in enumerate(g for seau in regroupeur.seaux.values() for g in seau)]
    paires.sort(key=lambda p: -p[1].occurrences)
    return [{"gabarit": texte_gabarit(regroupeur, cle, g), "occurrences": g.occurrences,
             "niveaux": g.niveaux, "exceptions": g.exceptions, "premiere": g.premiere,
             "derniere": g.derniere, "exemple": g.exemple} for cle, g in paires[:top]]


def construire_rapport(total: Stats, par_fichier: list[dict[str, Any]], ctx: Contexte,
                       args: argparse.Namespace, extra: dict[str, Any]) -> dict[str, Any]:
    """Assemble le rapport JSON."""
    rang = NIVEAUX.index(args.seuil)
    erreurs = sum(n for niv, n in total.par_niveau.items() if niv in NIVEAUX and NIVEAUX.index(niv) >= rang)
    noms = [f["fichier"] for f in par_fichier]
    return {
        "outil": Path(__file__).stem,
        "python": platform.python_version(),
        "moteur": ctx.regroupeur.moteur,
        "denominateur": total.enregistrements,
        "unite_denominateur": "enregistrement (ligne non vide hors continuations)",
        "examines": noms[:MAX_EXAMINES],
        "examines_tronques": len(noms) > MAX_EXAMINES,
        "lignes_lues": total.lignes,
        "lignes_continuation": total.continuations,
        "lignes_tronquees": total.tronquees,
        "par_niveau": {n: total.par_niveau[n] for n in (*reversed(NIVEAUX), NIVEAU_INCONNU) if n in total.par_niveau},
        "par_format": total.par_format,
        "periode": periode(total),
        "seuil": args.seuil,
        "erreurs_au_seuil": erreurs,
        "gabarits": {"niveau_minimal": NIVEAUX[ctx.niveau_gabarits], "total": ctx.regroupeur.total,
                     "hors_limite": ctx.regroupeur.hors_limite,
                     "dominants": resume_gabarits(ctx.regroupeur, args.top)},
        "par_fichier": par_fichier,
        **extra,
    }


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Volumes, période, gabarits dominants."""
    p = rapport["periode"]
    print(f"{rapport['denominateur']} enregistrements ({rapport['lignes_lues']} lignes) dans "
          f"{len(rapport['par_fichier'])} fichier(s) ; période {p['debut']} → {p['fin']} ({p['fuseaux']})")
    print("Par niveau : " + ", ".join(f"{n} {c}" for n, c in rapport["par_niveau"].items()))
    print("Par format : " + ", ".join(f"{n} {c}" for n, c in rapport["par_format"].items()))
    for g in rapport["gabarits"]["dominants"]:
        print(f"  {g['occurrences']:>7}  {g['gabarit'][:160]}")
        print(f"           1re {g['premiere'].get('lieu')} {g['premiere'].get('horodatage') or ''} ; "
              f"dernière {g['derniere'].get('lieu')} {g['derniere'].get('horodatage') or ''}"
              + (f" ; exceptions {g['exceptions']}" if g["exceptions"] else ""))
    print(f"Erreurs (≥ {rapport['seuil']}) : {rapport['erreurs_au_seuil']}")


def afficher_json(objet: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(objet, ensure_ascii=False, indent=2, default=str))


def choisir_moteur(demande: str) -> str:
    """'drain3' si disponible (ou demandé), sinon 'stdlib' avec une ligne sur stderr."""
    if demande == "stdlib":
        return "stdlib"
    if TemplateMiner is None:
        if demande == "drain3":
            raise EntreeInvalide("--moteur drain3 demandé mais drain3 n'est pas installé")
        print("drain3 absent : gabarits par regroupement stdlib à la Drain (moteur stdlib)", file=sys.stderr)
        return "stdlib"
    return "drain3"


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande, aide en français."""
    parseur = argparse.ArgumentParser(
        prog=Path(__file__).name,
        description="Résume des journaux (fichiers .log/.gz/.bz2/.xz ou dossiers) : volumes par "
                    "niveau, période couverte, gabarits d'erreurs dominants avec première et "
                    "dernière occurrence. Code 1 s'il y a des erreurs au seuil.",
        epilog=f"Exemple : python {RACINE.name}/{Path(__file__).name} /var/log/app/ --top 10 --json",
    )
    parseur.add_argument("chemins", nargs="+", help="fichiers de journal ou dossiers (récursif)")
    parseur.add_argument("--seuil", choices=NIVEAUX, default=NIVEAU_ERREUR,
                         help="niveau minimal compté comme erreur pour le code 1 (défaut : ERROR)")
    parseur.add_argument("--niveau-gabarits", choices=NIVEAUX, default=NIVEAU_ERREUR,
                         help="niveau minimal des messages regroupés en gabarits (défaut : ERROR)")
    parseur.add_argument("--top", type=int, default=20, help="gabarits dominants listés (défaut : 20)")
    parseur.add_argument("--annee", type=int, default=None,
                         help="année des horodatages syslog qui n'en portent pas")
    parseur.add_argument("--max-lignes", type=int, default=50_000_000,
                         help="lignes lues au plus, tous fichiers confondus")
    parseur.add_argument("--max-gabarits", type=int, default=5000, help="gabarits distincts au plus")
    parseur.add_argument("--moteur", choices=("auto", "stdlib", "drain3"), default="auto",
                         help="auto : drain3 s'il est installé, sinon regroupement stdlib")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="dossier de base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def executer(args: argparse.Namespace, moteur: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Lit chaque journal et assemble le rapport ; rend aussi les entrées invalides."""
    base = args.racine if args.racine is not None else Path.cwd()
    fichiers, invalides = collecter(args.chemins, base)
    ctx = Contexte(args.annee, NIVEAUX.index(args.niveau_gabarits), max(1, args.max_lignes),
                   creer_regroupeur(moteur, max(1, args.max_gabarits)))
    total, par_fichier, ecartes, tronque = Stats(), [], [], False
    for chemin, nom, explicite in fichiers:
        incident = None
        try:
            stats = analyser_fichier(chemin, nom, ctx, total)
        except Interruption as arret:
            stats, incident, tronque = arret.stats, str(arret), arret.borne
        except (EntreeInvalide, OSError) as exc:
            (invalides if explicite else ecartes).append({"fichier": nom, "erreur": str(exc)})
            continue
        par_fichier.append({"fichier": nom, "lignes": stats.lignes, "enregistrements": stats.enregistrements,
                            "par_niveau": stats.par_niveau, "periode": periode(stats), "incident": incident})
        if tronque:
            break
    extra = {"lecture_tronquee": tronque, "fichiers_ecartes": ecartes, "entrees_invalides": invalides}
    return construire_rapport(total, par_fichier, ctx, args, extra), invalides


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : 0 sans erreur, 1 erreurs au seuil, 2 entrée invalide, 3 rien à examiner."""
    args = construire_parseur().parse_args(argv)
    try:
        moteur = choisir_moteur(args.moteur)
    except EntreeInvalide as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
    rapport, invalides = executer(args, moteur)
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    for invalide in invalides:
        print(f"erreur : {invalide['fichier']} : {invalide['erreur']}", file=sys.stderr)
    code = 2 if invalides else (1 if rapport["erreurs_au_seuil"] else 0)
    if rapport["denominateur"] == 0 and not invalides:
        print("dénominateur nul : aucun enregistrement de journal, rien à examiner", file=sys.stderr)
        code = 3
    rapport["code_sortie"] = code
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
