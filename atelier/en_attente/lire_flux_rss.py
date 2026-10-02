"""Lire un flux RSS, RDF, Atom ou JSON Feed local et dire ce qu'il publie et ce qui y est faux.

Mesuré dans cette session : feedparser 6.0.14 lit un flux RSS 2.0 non bien formé (une
esperluette non échappée dans le titre) et rend ses trois éléments avec un simple drapeau
bozo=1 ; il rend sans remarque deux éléments au même guid « 1 » et accepte la date
« Mon, 01 Oct 2026 » alors que le 1er octobre 2026 est un jeudi. Un agent qui lit un flux
ainsi ne voit ni le défaut de forme, ni le doublon, ni la date fausse.

QUESTION
    Que publie ce flux RSS/Atom/JSON Feed (titre, éléments, liens, dates, identifiants), et
    est-il bien formé ?
MESURE
    Lecture d'un fichier local (ou d'un texte en ligne), jamais du réseau. Le XML passe par
    pyexpat avec refus de toute déclaration d'entité et sans résolution externe ; le JSON par
    le module json. Formats reconnus : RSS 0.91 à 2.0, RDF (RSS 0.90 et 1.0), Atom 1.0,
    JSON Feed 1.0 et 1.1. Par élément : titre, lien, date normalisée en UTC (RFC 822 pour
    RSS, RFC 3339 pour Atom, dc:date et JSON Feed), identifiant. Contrôles : éléments
    obligatoires du format, identifiants absents ou dupliqués, liens dupliqués ou relatifs,
    dates illisibles, futures (au-delà d'une tolérance), au mauvais format, ou dont le jour
    de semaine contredit la date, guid non permalien sans isPermaLink="false".
HYPOTHÈSES
    Le flux suit la spécification de son format ; l'horloge locale (ou --maintenant) donne
    l'instant de référence des dates futures ; une date RFC 822 en -0000 est lue en UTC.
LIMITES
    Pas de résolution des liens relatifs (xml:base), pas de nettoyage du balisage des
    descriptions, Atom 0.3 et les flux à DTD déclarant des entités ne sont pas lus. Les
    extensions (iTunes, Media RSS) ne sont pas contrôlées. En dossier, les fichiers qui ne
    sont pas des flux sont ignorés, pas comptés. Fichier plafonné à 32 Mio par défaut.
CONTRE-EXEMPLES
    Constaté : un élément RSS daté en RFC 3339 (aaaa-mm-jjThh:mm:ssZ) est lu et normalisé
    avec un simple avertissement de format, alors que la spécification RSS 2.0 exige le
    RFC 822 (seule l'option --strict le compte comme défaut). Constaté aussi : deux fichiers a.rss et b.rss dont les éléments portent le même
    guid « meme-id » sont tous deux conformes (code 0) : les doublons ne sont cherchés qu'à
    l'intérieur d'un flux.
INVOCATION
    {outil} --texte '<rss version="2.0"><channel><title>T</title><link>https://exemple.org/</link><description>d</description><item><title>a</title><guid isPermaLink="false">a1</guid><pubDate>Thu, 01 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>' --json
DOMAINE
    Flux de syndication enregistrés localement (exports, fichiers produits par un site, réponses
    d'API sauvegardées) avant publication, ingestion ou comparaison.
"""

from __future__ import annotations

import argparse
import email.utils
import io
import json
import os
import re
import sys
import warnings
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, TreeBuilder

import pyexpat

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

try:
    import feedparser
except ImportError:
    feedparser = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", INTITULE_INVOCATION, "DOMAINE")

NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
NS_RSS10 = "http://purl.org/rss/1.0/"
NS_RSS090 = "http://my.netscape.com/rdf/simple/0.9/"
NS_ATOM = "http://www.w3.org/2005/Atom"
NS_DC = "http://purl.org/dc/elements/1.1/"
PREFIXE_JSONFEED = "https://jsonfeed.org/version/"
EXTENSIONS_FLUX = frozenset({".rss", ".xml", ".atom", ".rdf", ".json", ".feed"})
DOSSIERS_IGNORES = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv"})
JOURS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MOTIF_RFC3339 = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[Tt ](\d{2}:\d{2}:\d{2})(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")
MOTIF_JOUR = re.compile(r"^\s*([A-Za-z]{3})[A-Za-z]*\s*,")
MOTIF_ABSOLU = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
MOTIF_INDICE_FLUX = re.compile(r"<rss[\s>]|<feed[\s>]|<(?:\w+:)?RDF[\s>]|jsonfeed\.org/version")
TAILLE_INDICE = 4096
PLAFOND_EXAMINES = 200
PLAFOND_TEXTE = 200
NOM_OUTIL = "lire_flux_rss"
ENCODAGE = "utf-8"


class EntreeInvalide(Exception):
    """Entrée inutilisable (chemin absent, illisible, trop gros) : code 2."""


class FluxRefuse(Exception):
    """Document lu mais refusé (mal formé, entités déclarées, format inconnu).

    `peut_etre_flux` dit si le document ressemble à un flux : en dossier, seul un document
    qui n'en est visiblement pas un est ignoré au lieu d'être compté comme défaut.
    """

    def __init__(self, message: str, peut_etre_flux: bool = True) -> None:
        super().__init__(message)
        self.peut_etre_flux = peut_etre_flux


@dataclass(frozen=True)
class Source:
    """Une entrée : fichier, entrée standard ou texte en ligne."""

    nom: str
    chemin: Path | None = None
    texte: str | None = None


@dataclass(frozen=True)
class Reglages:
    """Paramètres d'analyse communs à tous les flux."""

    maintenant: datetime
    tolerance: timedelta
    max_elements: int
    max_octets: int
    strict: bool


@dataclass
class Rapport:
    """Anomalies d'un flux, rangées par gravité."""

    erreurs: list[dict[str, str]] = field(default_factory=list)
    avertissements: list[dict[str, str]] = field(default_factory=list)

    def erreur(self, genre: str, detail: str) -> None:
        self.erreurs.append({"type": genre, "detail": detail})

    def avertir(self, genre: str, detail: str) -> None:
        self.avertissements.append({"type": genre, "detail": detail})


def refuser_entite(*_args: Any) -> None:
    """Gestionnaire pyexpat : toute déclaration d'entité est refusée (rien n'est déployé)."""
    raise FluxRefuse("déclaration d'entité dans le flux : refusé (risque XXE ou bombe)")


def nom_clark(nom: str) -> str:
    """« uri}local » (séparateur pyexpat) devient « {uri}local » (notation d'ElementTree)."""
    return "{" + nom if "}" in nom else nom


def construire_arbre(donnees: bytes | str) -> Element:
    """Arbre ElementTree construit par pyexpat sans entités ni ressource externe."""
    batisseur = TreeBuilder()
    analyseur = pyexpat.ParserCreate(namespace_separator="}")
    analyseur.SetParamEntityParsing(pyexpat.XML_PARAM_ENTITY_PARSING_NEVER)
    analyseur.EntityDeclHandler = refuser_entite
    analyseur.ExternalEntityRefHandler = lambda *_args: 1
    analyseur.buffer_text = True
    analyseur.StartElementHandler = lambda nom, attrs: batisseur.start(
        nom_clark(nom), {nom_clark(k): v for k, v in attrs.items()})
    analyseur.EndElementHandler = lambda nom: batisseur.end(nom_clark(nom))
    analyseur.CharacterDataHandler = batisseur.data
    try:
        analyseur.Parse(donnees, True)
    except pyexpat.ExpatError as exc:
        raise FluxRefuse(f"XML mal formé ligne {exc.lineno} colonne {exc.offset} : "
                         f"{pyexpat.ErrorString(exc.code)}", ressemble_a_un_flux(donnees)) from exc
    except FluxRefuse as exc:
        raise FluxRefuse(str(exc), ressemble_a_un_flux(donnees)) from exc
    return batisseur.close()


def ressemble_a_un_flux(donnees: bytes | str) -> bool:
    """Indice sur le début du document : balise rss, feed, RDF ou version JSON Feed."""
    debut = donnees[:TAILLE_INDICE]
    texte = debut.decode("latin-1") if isinstance(debut, bytes) else debut
    return MOTIF_INDICE_FLUX.search(texte) is not None


def texte_de(noeud: Element | None) -> str | None:
    """Texte complet d'un élément (sous-éléments compris), espaces normalisés."""
    if noeud is None:
        return None
    texte = " ".join("".join(noeud.itertext()).split())
    return texte or None


def abreger(texte: str | None) -> str | None:
    if texte is None or len(texte) <= PLAFOND_TEXTE:
        return texte
    return texte[:PLAFOND_TEXTE] + "…"


def lire_rfc822(brut: str) -> tuple[datetime, list[str]]:
    """Date RFC 822/2822 ; signale un jour de semaine faux ou un fuseau inconnu."""
    try:
        date = email.utils.parsedate_to_datetime(brut)
    except (ValueError, TypeError, IndexError, OverflowError) as exc:
        raise ValueError(f"date RFC 822 illisible : {brut!r}") from exc
    notes = []
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
        notes.append("fuseau inconnu (-0000) lu en UTC")
    jour = MOTIF_JOUR.match(brut)
    if jour and jour.group(1).lower() in JOURS and JOURS.index(jour.group(1).lower()) != date.weekday():
        notes.append(f"jour {jour.group(1)} faux : le {date.date()} est un {JOURS[date.weekday()].capitalize()}")
    return date, notes


def lire_rfc3339(brut: str) -> tuple[datetime, list[str]]:
    """Date RFC 3339 stricte (fraction de seconde tronquée à la microseconde)."""
    morceaux = MOTIF_RFC3339.match(brut.strip())
    if not morceaux:
        raise ValueError(f"date RFC 3339 illisible : {brut!r}")
    jour, heure, fraction, fuseau = morceaux.groups()
    fraction = (fraction or "")[:7]
    fuseau = "+00:00" if fuseau in ("Z", "z") else fuseau
    try:
        return datetime.fromisoformat(f"{jour}T{heure}{fraction}{fuseau}"), []
    except ValueError as exc:
        raise ValueError(f"date RFC 3339 impossible : {brut!r}") from exc


def normaliser_date(brut: str | None, attendu: str, champ: str, rapport: Rapport,
                    reglages: Reglages) -> str | None:
    """Lit une date dans le format attendu, sinon dans l'autre (avec avertissement)."""
    if brut is None:
        return None
    lecteurs = {"rfc822": lire_rfc822, "rfc3339": lire_rfc3339}
    autre = "rfc3339" if attendu == "rfc822" else "rfc822"
    try:
        date, notes = lecteurs[attendu](brut)
    except ValueError:
        try:
            date, notes = lecteurs[autre](brut)
        except ValueError:
            rapport.erreur("date_illisible", f"{champ} : {brut!r}")
            return None
        notes.append(f"format {autre} au lieu de {attendu}")
        (rapport.erreur if reglages.strict else rapport.avertir)("date_format", f"{champ} : {brut!r}")
    for note in notes:
        if not note.startswith("format"):
            rapport.avertir("date_suspecte", f"{champ} : {note}")
    try:
        utc = date.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (OverflowError, ValueError):
        rapport.erreur("date_illisible", f"{champ} : {brut!r} hors de l'intervalle représentable")
        return None
    if date > reglages.maintenant + reglages.tolerance:
        rapport.erreur("date_future", f"{champ} : {utc}")
    return utc


def element_commun(titre: str | None, lien: str | None, identifiant: str | None,
                   date: str | None, brut: str | None) -> dict[str, Any]:
    """Forme commune d'un élément de flux, quel que soit le format."""
    return {"titre": abreger(titre), "lien": lien, "identifiant": identifiant,
            "date": date, "date_brute": brut}


def extraire_rss(racine: Element, rapport: Rapport, reglages: Reglages) -> dict[str, Any]:
    """RSS 0.91 à 2.0 : canal et éléments item."""
    canal = racine.find("channel")
    if canal is None:
        raise FluxRefuse("RSS sans élément channel")
    for obligatoire in ("title", "link", "description"):
        if texte_de(canal.find(obligatoire)) is None:
            rapport.erreur("canal_incomplet", f"channel sans {obligatoire}")
    elements = [extraire_item_rss(i, n, rapport, reglages)
                for n, i in enumerate(canal.findall("item") or racine.findall("item"), 1)]
    return {"format": f"rss {racine.get('version') or '?'}", "titre": texte_de(canal.find("title")),
            "lien": texte_de(canal.find("link")), "elements": elements}


def extraire_item_rss(item: Element, rang: int, rapport: Rapport, reglages: Reglages) -> dict[str, Any]:
    """Un item RSS : titre, lien, guid (et sa nature de permalien), pubDate ou dc:date."""
    titre, description = texte_de(item.find("title")), texte_de(item.find("description"))
    if titre is None and description is None:
        rapport.erreur("element_vide", f"item {rang} sans title ni description")
    guid_noeud = item.find("guid")
    guid = texte_de(guid_noeud)
    if guid is None:
        rapport.avertir("guid_absent", f"item {rang} ({abreger(titre) or 'sans titre'})")
    elif guid_noeud.get("isPermaLink", "true") == "true" and not MOTIF_ABSOLU.match(guid):
        rapport.avertir("guid_non_permalien", f"item {rang} : guid {guid!r} sans isPermaLink=\"false\"")
    brut = texte_de(item.find("pubDate"))
    date = normaliser_date(brut, "rfc822", f"item {rang} pubDate", rapport, reglages)
    if brut is None:
        brut = texte_de(item.find(f"{{{NS_DC}}}date"))
        date = normaliser_date(brut, "rfc3339", f"item {rang} dc:date", rapport, reglages)
    return element_commun(titre, texte_de(item.find("link")), guid, date, brut)


def extraire_rdf(racine: Element, rapport: Rapport, reglages: Reglages) -> dict[str, Any]:
    """RSS 1.0 et 0.90 (RDF) : canal et items frères, identifiant rdf:about."""
    ns = NS_RSS10 if racine.find(f"{{{NS_RSS10}}}channel") is not None else NS_RSS090
    canal = racine.find(f"{{{ns}}}channel")
    if canal is None:
        raise FluxRefuse("RDF sans channel RSS 1.0 ni 0.90")
    if texte_de(canal.find(f"{{{ns}}}title")) is None:
        rapport.erreur("canal_incomplet", "channel sans title")
    elements = []
    for rang, item in enumerate(racine.findall(f"{{{ns}}}item"), 1):
        brut = texte_de(item.find(f"{{{NS_DC}}}date"))
        date = normaliser_date(brut, "rfc3339", f"item {rang} dc:date", rapport, reglages)
        elements.append(element_commun(texte_de(item.find(f"{{{ns}}}title")),
                                       texte_de(item.find(f"{{{ns}}}link")),
                                       item.get(f"{{{NS_RDF}}}about"), date, brut))
    version = "rss 1.0" if ns == NS_RSS10 else "rss 0.90"
    return {"format": version, "titre": texte_de(canal.find(f"{{{ns}}}title")),
            "lien": texte_de(canal.find(f"{{{ns}}}link")), "elements": elements}


def lien_atom(noeud: Element) -> str | None:
    """Lien alternate d'Atom (rel absent vaut alternate)."""
    for lien in noeud.findall(f"{{{NS_ATOM}}}link"):
        if lien.get("rel", "alternate") == "alternate":
            return lien.get("href")
    return None


def extraire_atom(racine: Element, rapport: Rapport, reglages: Reglages) -> dict[str, Any]:
    """Atom 1.0 (RFC 4287) : id, title et updated obligatoires pour le flux et chaque entrée."""
    a = f"{{{NS_ATOM}}}"
    for obligatoire in ("id", "title", "updated"):
        if texte_de(racine.find(a + obligatoire)) is None:
            rapport.erreur("canal_incomplet", f"feed sans {obligatoire}")
    normaliser_date(texte_de(racine.find(a + "updated")), "rfc3339", "feed updated", rapport, reglages)
    elements = []
    for rang, entree in enumerate(racine.findall(a + "entry"), 1):
        for obligatoire in ("id", "title", "updated"):
            if texte_de(entree.find(a + obligatoire)) is None:
                rapport.erreur("element_incomplet", f"entry {rang} sans {obligatoire}")
        maj = normaliser_date(texte_de(entree.find(a + "updated")), "rfc3339",
                              f"entry {rang} updated", rapport, reglages)
        brut = texte_de(entree.find(a + "published"))
        date = normaliser_date(brut, "rfc3339", f"entry {rang} published", rapport, reglages) or maj
        elements.append(element_commun(texte_de(entree.find(a + "title")), lien_atom(entree),
                                       texte_de(entree.find(a + "id")), date,
                                       brut or texte_de(entree.find(a + "updated"))))
    return {"format": "atom 1.0", "titre": texte_de(racine.find(a + "title")),
            "lien": lien_atom(racine), "elements": elements}


def extraire_json_feed(document: dict[str, Any], rapport: Rapport, reglages: Reglages) -> dict[str, Any]:
    """JSON Feed 1.0/1.1 : title et items obligatoires, id obligatoire par item."""
    if not isinstance(document.get("title"), str):
        rapport.erreur("canal_incomplet", "flux sans title")
    items = document.get("items")
    if not isinstance(items, list):
        raise FluxRefuse("JSON Feed sans tableau items")
    elements = [extraire_item_json(item, rang, rapport, reglages) for rang, item in enumerate(items, 1)]
    version = str(document.get("version", ""))[len(PREFIXE_JSONFEED):]
    return {"format": f"json feed {version}", "titre": document.get("title"),
            "lien": document.get("home_page_url"), "elements": elements}


def extraire_item_json(item: Any, rang: int, rapport: Rapport, reglages: Reglages) -> dict[str, Any]:
    """Un item JSON Feed ; un id numérique est accepté mais signalé (la 1.1 exige une chaîne)."""
    if not isinstance(item, dict):
        rapport.erreur("element_invalide", f"item {rang} n'est pas un objet")
        return element_commun(None, None, None, None, None)
    identifiant = item.get("id")
    if identifiant is None or identifiant == "":
        rapport.erreur("identifiant_absent", f"item {rang} sans id")
    elif not isinstance(identifiant, str):
        rapport.avertir("identifiant_non_textuel", f"item {rang} : id {identifiant!r}")
    if item.get("content_html") is None and item.get("content_text") is None:
        rapport.avertir("contenu_absent", f"item {rang} sans content_html ni content_text")
    brut = item.get("date_published") if isinstance(item.get("date_published"), str) else None
    date = normaliser_date(brut, "rfc3339", f"item {rang} date_published", rapport, reglages)
    return element_commun(item.get("title") if isinstance(item.get("title"), str) else None,
                          item.get("url") if isinstance(item.get("url"), str) else None,
                          None if identifiant is None else str(identifiant), date, brut)


def reconnaitre_xml(racine: Element, rapport: Rapport, reglages: Reglages) -> dict[str, Any]:
    """Aiguille selon l'élément racine."""
    if racine.tag == "rss":
        return extraire_rss(racine, rapport, reglages)
    if racine.tag == f"{{{NS_RDF}}}RDF":
        return extraire_rdf(racine, rapport, reglages)
    if racine.tag == f"{{{NS_ATOM}}}feed":
        return extraire_atom(racine, rapport, reglages)
    raise FluxRefuse(f"format non reconnu (racine {racine.tag})", peut_etre_flux=False)


def est_json(donnees: bytes | str) -> bool:
    """Un JSON Feed commence par une accolade (après espaces et éventuelle marque d'ordre)."""
    debut = donnees.lstrip()[:4]
    return debut.lstrip(b"\xef\xbb\xbf" if isinstance(debut, bytes) else "\ufeff")[:1] in (b"{", "{")


def analyser_document(donnees: bytes | str, rapport: Rapport, reglages: Reglages) -> dict[str, Any]:
    """Lit le document (JSON Feed ou XML) et extrait le flux."""
    if not est_json(donnees):
        return reconnaitre_xml(construire_arbre(donnees), rapport, reglages)
    try:
        document = json.loads(donnees)
    except (ValueError, RecursionError) as exc:
        raise FluxRefuse(f"JSON invalide : {exc}", ressemble_a_un_flux(donnees)) from exc
    if not (isinstance(document, dict) and str(document.get("version", "")).startswith(PREFIXE_JSONFEED)):
        raise FluxRefuse("JSON sans version https://jsonfeed.org/version/…", peut_etre_flux=False)
    return extraire_json_feed(document, rapport, reglages)


def controler_doublons(elements: list[dict[str, Any]], rapport: Rapport) -> None:
    """Identifiants dupliqués (erreur), liens dupliqués et relatifs (avertissements)."""
    for cle, genre, signaler in (("identifiant", "identifiant_duplique", rapport.erreur),
                                 ("lien", "lien_duplique", rapport.avertir)):
        comptes = Counter(e[cle] for e in elements if e[cle])
        for valeur, nombre in comptes.items():
            if nombre > 1:
                signaler(genre, f"{valeur!r} apparaît {nombre} fois")
    relatifs = [e["lien"] for e in elements if e["lien"] and not MOTIF_ABSOLU.match(e["lien"])]
    if relatifs:
        rapport.avertir("lien_relatif", f"{len(relatifs)} lien(s) relatif(s), dont {relatifs[0]!r}")
    if not elements:
        rapport.avertir("flux_vide", "aucun élément")


def lire_donnees(source: Source, plafond: int) -> bytes | str:
    """Contenu brut d'une source, borné en taille."""
    if source.texte is not None:
        return source.texte
    flux = sys.stdin.buffer if source.chemin is None else None
    if source.chemin is not None:
        if source.chemin.stat().st_size > plafond:
            raise EntreeInvalide(f"{source.nom} dépasse {plafond} octets (--max-octets)")
        return source.chemin.read_bytes()
    donnees = flux.read(plafond + 1)
    if len(donnees) > plafond:
        raise EntreeInvalide(f"entrée standard au-delà de {plafond} octets (--max-octets)")
    return donnees


def analyser_source(source: Source, reglages: Reglages) -> dict[str, Any]:
    """Analyse complète d'un flux : contenu, anomalies, comparaison feedparser."""
    donnees = lire_donnees(source, reglages.max_octets)
    rapport = Rapport()
    try:
        flux = analyser_document(donnees, rapport, reglages)
    except FluxRefuse as exc:
        rapport.erreur("illisible", str(exc))
        flux = {"format": None, "titre": None, "lien": None, "elements": [],
                "peut_etre_flux": exc.peut_etre_flux}
    else:
        controler_doublons(flux["elements"], rapport)
    elements = flux.pop("elements")
    flux.update({"chemin": source.nom, "nombre_elements": len(elements),
                 "elements": elements[:reglages.max_elements],
                 "elements_tronques": len(elements) > reglages.max_elements,
                 "erreurs": rapport.erreurs, "avertissements": rapport.avertissements})
    flux["verdict"] = verdict_de(flux, reglages.strict)
    flux["feedparser"] = comparer_feedparser(donnees, flux)
    return flux


def verdict_de(flux: dict[str, Any], strict: bool) -> str:
    if flux["format"] is None:
        return "illisible"
    if flux["erreurs"] or (strict and flux["avertissements"]):
        return "defauts"
    return "conforme"


def comparer_feedparser(donnees: bytes | str, flux: dict[str, Any]) -> dict[str, Any] | None:
    """Ce que feedparser en tire, pour comparaison.

    Un flux en mémoire (BytesIO) : donné en octets ou en texte, feedparser tenterait d'ouvrir
    ce contenu comme un nom de fichier, voire une URL.
    """
    if feedparser is None:
        return None
    if any(e["type"] == "illisible" and "entité" in e["detail"] for e in flux["erreurs"]):
        return {"compare": False, "raison": "entités déclarées : non soumis à feedparser"}
    if est_json(donnees):
        return {"compare": False, "raison": "feedparser ne lit pas JSON Feed"}
    brut = donnees.encode(ENCODAGE) if isinstance(donnees, str) else donnees
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resultat = feedparser.parse(io.BytesIO(brut))
    nombre = len(resultat.entries)
    return {"compare": True, "version": resultat.get("version") or None,
            "bozo": bool(resultat.get("bozo")), "elements": nombre,
            "titre": resultat.feed.get("title"),
            "accord_nombre": nombre == flux["nombre_elements"]}


def lister_fichiers(dossier: Path) -> list[Path]:
    """Fichiers candidats d'un dossier (récursif, sans liens symboliques), triés."""
    trouves = []
    for courant, sous_dossiers, fichiers in os.walk(dossier):
        sous_dossiers[:] = sorted(d for d in sous_dossiers if d not in DOSSIERS_IGNORES)
        trouves += [Path(courant) / nom for nom in sorted(fichiers)
                    if Path(nom).suffix.lower() in EXTENSIONS_FLUX
                    and not (Path(courant) / nom).is_symlink()]
    return trouves


def rassembler_sources(entrees: list[str], texte: str | None, base: Path) -> tuple[list[Source], list[Source]]:
    """Sources explicites (toujours examinées) et sources de dossiers (examinées si flux)."""
    explicites = [Source("<texte>", texte=texte)] if texte is not None else []
    de_dossiers: list[Source] = []
    for entree in entrees:
        chemin = Path(entree) if Path(entree).is_absolute() else base / entree
        if entree == "-":
            explicites.append(Source("<stdin>"))
        elif chemin.is_dir():
            de_dossiers += [Source(str(f.relative_to(chemin)), chemin=f) for f in lister_fichiers(chemin)]
        elif chemin.is_file():
            explicites.append(Source(entree, chemin=chemin))
        else:
            raise EntreeInvalide(f"chemin introuvable ou non ordinaire : {entree}")
    return explicites, de_dossiers


def analyser_tout(explicites: list[Source], de_dossiers: list[Source],
                  reglages: Reglages) -> tuple[list[dict[str, Any]], list[str]]:
    """Analyse ; un fichier de dossier qui n'est pas un flux est ignoré, pas compté."""
    resultats, ignores = [], []
    for source in explicites + de_dossiers:
        try:
            resultat = analyser_source(source, reglages)
        except OSError as exc:
            raise EntreeInvalide(f"lecture impossible de {source.nom} : {exc.strerror or exc}") from exc
        except ValueError as exc:
            raise EntreeInvalide(f"texte inutilisable pour {source.nom} : {exc}") from exc
        if not resultat.pop("peut_etre_flux", True) and source in de_dossiers:
            ignores.append(source.nom)
        else:
            resultats.append(resultat)
    return resultats, ignores


def lire_contrat() -> dict[str, str]:
    """Sections du contrat de mesure, lues dans la docstring du module."""
    sections: dict[str, list[str]] = {}
    courant = None
    for ligne in (__doc__ or "").splitlines():
        if ligne.strip() in INTITULES and not ligne.startswith(" "):
            courant = ligne.strip()
            sections[courant] = []
        elif courant:
            sections[courant].append(ligne.strip())
    return {cle: " ".join(m for m in morceaux if m) for cle, morceaux in sections.items()}


def construire_rapport(resultats: list[dict[str, Any]], ignores: list[str],
                       reglages: Reglages) -> dict[str, Any]:
    verdicts = Counter(r["verdict"] for r in resultats)
    noms = [r["chemin"] for r in resultats]
    return {
        "outil": NOM_OUTIL,
        "moteur": "stdlib",
        "comparaison": f"feedparser {feedparser.__version__}" if feedparser else None,
        "denominateur": len(resultats),
        "examines": noms[:PLAFOND_EXAMINES],
        "examines_tronques": len(noms) > PLAFOND_EXAMINES,
        "ignores_non_flux": ignores[:PLAFOND_EXAMINES],
        "conformes": verdicts["conforme"],
        "avec_defauts": verdicts["defauts"],
        "illisibles": verdicts["illisible"],
        "maintenant": reglages.maintenant.isoformat(),
        "resultats": resultats,
        "contrat": lire_contrat(),
    }


def afficher_humain(rapport: dict[str, Any]) -> None:
    for r in rapport["resultats"]:
        print(f"{r['chemin']} : {r['verdict'].upper()} — {r['format'] or 'format inconnu'}, "
              f"« {r['titre'] or ''} », {r['nombre_elements']} élément(s)")
        for e in r["elements"][:10]:
            print(f"  - {e['date'] or 'sans date'} | {e['titre'] or '(sans titre)'} | {e['lien'] or ''}")
        for e in r["erreurs"]:
            print(f"  erreur {e['type']} : {e['detail']}")
        for a in r["avertissements"]:
            print(f"  avertissement {a['type']} : {a['detail']}")
    print(f"{rapport['denominateur']} flux : {rapport['conformes']} conforme(s), "
          f"{rapport['avec_defauts']} avec défauts, {rapport['illisibles']} illisible(s) ; "
          f"{len(rapport['ignores_non_flux'])} fichier(s) ignoré(s)")


def afficher_json(rapport: dict[str, Any]) -> None:
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


def lire_maintenant(texte: str | None) -> datetime:
    """Instant de référence : --maintenant (RFC 3339) ou l'horloge, en UTC."""
    if texte is None:
        return datetime.now(UTC)
    return lire_rfc3339(texte)[0]


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Lit un flux RSS, RDF, Atom ou JSON Feed local (jamais le réseau) : titre, "
                    "éléments, dates normalisées, identifiants, et ses défauts.",
        epilog="Exemple : python lire_flux_rss.py public/feed.xml --json\n"
               "          python lire_flux_rss.py flux/ --maintenant 2026-10-01T00:00:00Z\n"
               "Codes : 0 conforme, 1 défaut, 2 entrée invalide, 3 aucun flux à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument("chemins", nargs="*", help="fichiers de flux ou dossiers ; - = entrée standard")
    parseur.add_argument("--texte", help="flux donné en ligne (XML ou JSON)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="dossier de base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--maintenant", help="instant de référence RFC 3339 (défaut : horloge)")
    parseur.add_argument("--tolerance-futur", type=int, default=10,
                         help="minutes tolérées dans le futur avant de signaler une date")
    parseur.add_argument("--max-elements", type=int, default=50, help="éléments détaillés par flux")
    parseur.add_argument("--max-octets", type=int, default=32 << 20, help="taille maximale d'un flux")
    parseur.add_argument("--strict", action="store_true", help="les avertissements comptent comme défauts")
    return parseur


def base_relative(racine: Path | None) -> Path:
    """Base des chemins relatifs : --racine, sinon le dossier courant (sinon celui de l'outil)."""
    if racine is not None:
        return racine
    try:
        return Path.cwd()
    except OSError:
        return RACINE


def preparer(args: argparse.Namespace) -> Reglages:
    """Valide les options ; lève EntreeInvalide."""
    if not args.chemins and args.texte is None:
        raise EntreeInvalide("donner un chemin, - ou --texte")
    if args.max_elements < 0 or args.max_octets < 1 or args.tolerance_futur < 0:
        raise EntreeInvalide("plafonds et tolérance doivent être positifs")
    try:
        maintenant = lire_maintenant(args.maintenant)
    except ValueError as exc:
        raise EntreeInvalide(f"--maintenant : {exc}") from exc
    return Reglages(maintenant, timedelta(minutes=args.tolerance_futur), args.max_elements,
                    args.max_octets, args.strict)


def main() -> int:
    args = construire_parseur().parse_args()
    try:
        reglages = preparer(args)
        if feedparser is None:
            print("mode dégradé — feedparser absent : pas de comparaison, lecture stdlib seule",
                  file=sys.stderr)
        explicites, de_dossiers = rassembler_sources(args.chemins, args.texte, base_relative(args.racine))
        resultats, ignores = analyser_tout(explicites, de_dossiers, reglages)
    except EntreeInvalide as exc:
        print(f"entrée invalide : {exc}", file=sys.stderr)
        return 2
    rapport = construire_rapport(resultats, ignores, reglages)
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    if not resultats:
        print("dénominateur nul : aucun flux reconnu, rien à examiner", file=sys.stderr)
        return 3
    return 0 if rapport["conformes"] == len(resultats) else 1


if __name__ == "__main__":
    raise SystemExit(main())
