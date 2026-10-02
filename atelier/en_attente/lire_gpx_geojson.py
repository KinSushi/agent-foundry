"""Décrire une trace GPX ou un GeoJSON et dire s'il est valide (RFC 7946, GPX 1.0/1.1).

Un validateur géométrique ne suffit pas à dire qu'un GeoJSON est conforme : mesuré dans
cette session, shapely 2.1.2 déclare `is_valid == True` un polygone dont l'anneau
extérieur tourne dans le sens horaire, contraire à la règle de la main droite de la
RFC 7946 (§3.1.6) ; et `json.loads('[NaN]')` réussit alors que NaN n'est pas du JSON.
Côté GPX, gpxpy 1.6.2 mesure les distances sur une sphère de rayon 6 378 137 m
(rayon équatorial) : ses longueurs dépassent de 0,11 % celles d'une haversine au
rayon moyen ; cet outil publie la sienne et, si gpxpy est là, l'écart.

QUESTION
    Que décrit cette trace GPX ou ce GeoJSON, et est-il valide ?
MESURE
    GPX (lu en flux par xml.etree.iterparse, DTD refusée) : version et espace de
    noms, traces, segments, routes, points de passage ; par segment, nombre de
    points, distance haversine (rayon moyen 6 371 008,8 m), dénivelés positif et
    négatif bruts et filtrés par hystérésis (--seuil-denivele), durée, vitesses
    moyenne et maximale, points aberrants (vitesse au-delà de --vitesse-max,
    temps non croissant, pic isolé aller-retour), coordonnées hors bornes.
    GeoJSON (RFC 7946) : types d'objets et de géométries, membres obligatoires,
    positions (2 ou 3 nombres finis), bornes longitude/latitude et inversion
    probable de l'ordre (hors bornes, ou hors --region alors que l'ordre inverse
    y tombe), anneaux fermés d'au moins 4 positions, règle de la main droite,
    anneaux auto-sécants (jusqu'à --max-sommets sommets), franchissement de
    l'antiméridien, membre bbox cohérent, membre crs obsolète, clés en double,
    NaN refusé. Boîte englobante calculée. Si shapely est installé, chaque
    polygone est revalidé (is_valid) ; si gpxpy l'est, distances, dénivelés et
    durées sont recalculés par lui ; les écarts sont publiés.
HYPOTHÈSES
    Coordonnées WGS84 en degrés ; dans un GPX, temps en ISO 8601 (UTC si aucun
    décalage n'est écrit) ; un fichier .json d'un dossier n'est examiné que s'il
    porte un « type » GeoJSON.
LIMITES
    Distances sur la sphère (haversine), pas l'ellipsoïde ; dénivelé tiré des
    altitudes du fichier, sans modèle de terrain ; un trou hors de son anneau
    extérieur ou deux trous qui se recouvrent ne sont pas détectés sans shapely ;
    au-delà de --max-sommets, l'auto-intersection n'est pas cherchée ; l'inversion
    lon/lat n'est prouvable que hors bornes ou avec --region ; les extensions GPX
    (fréquence cardiaque...) sont ignorées.
CONTRE-EXEMPLES
    Constaté dans cette session : une trace à 5 m/s (un point toutes les 60 s,
    300 m d'écart) dont un point est décalé de 300 m de côté passe VALIDE sans
    aberrant — le détour ne dépasse pas 7,1 m/s, bien sous --vitesse-max ; un
    polygone autour de Paris écrit en [lat, lon] ([48.0, 2.0]...) tient dans les
    bornes et passe sans --region (il est pris avec --region -5.5,41,10,51.5).
INVOCATION
    {outil} --texte '{"type":"LineString","coordinates":[[2.3522,48.8566],[-0.1278,51.5074]]}' --json
DOMAINE
    Fichiers GPX 1.0 et 1.1 (montres, GPS, applications de sport) et GeoJSON
    RFC 7946 (API cartographiques, SIG web), jusqu'à --taille-max octets.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import gpxpy
except ImportError:
    gpxpy = None

try:
    import shapely
    from shapely.geometry import shape as forme_shapely
    from shapely.validation import explain_validity
except ImportError:
    shapely = None
    forme_shapely = None
    explain_validity = None

RACINE = Path(__file__).resolve().parent

INTITULE_QUESTION = "QUESTION"
INTITULE_MESURE = "MESURE"
INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_LIMITES = "LIMITES"
INTITULE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
INTITULE_INVOCATION = "INVOCATION"
INTITULE_DOMAINE = "DOMAINE"
INTITULES = (
    INTITULE_QUESTION, INTITULE_MESURE, INTITULE_HYPOTHESES, INTITULE_LIMITES,
    INTITULE_CONTRE_EXEMPLES, INTITULE_INVOCATION, INTITULE_DOMAINE,
)
NORME_GEOJSON = "RFC 7946"
NORME_TEMPS = "ISO 8601"
SYSTEME_WGS84 = "WGS84"
CONSTANTE_NAN = "NaN"
VERDICT_VALIDE = "VALIDE"
VERDICT_INVALIDE = "INVALIDE"

CODE_OK = 0
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_RIEN = 3

RAYON_MOYEN = 6371008.8
TAILLE_MAX_DEFAUT = 200 * 1024 * 1024
VITESSE_MAX_DEFAUT = 50.0
SEUIL_DENIVELE_DEFAUT = 3.0
MAX_SOMMETS_DEFAUT = 1000
MAX_MESSAGES = 200
EXAMINES_MAX = 50
PROFONDEUR_MAX = 32
OCTETS_PROLOGUE = 65536

ESPACES_GPX = {
    "http://www.topografix.com/GPX/1/0": "1.0",
    "http://www.topografix.com/GPX/1/1": "1.1",
}
TYPES_GEOMETRIE = ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon")
TYPES_GEOJSON = (*TYPES_GEOMETRIE, "GeometryCollection", "Feature", "FeatureCollection")
EXTENSIONS_GPX = (".gpx",)
EXTENSIONS_GEOJSON = (".geojson", ".json")


class ErreurLecture(Exception):
    """Entrée illisible : code de sortie porté."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Constats:
    """Erreurs et avertissements bornés d'un document."""

    erreurs: list[str] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)
    nb_erreurs: int = 0
    nb_avertissements: int = 0

    def erreur(self, message: str) -> None:
        """Consigne une erreur (invalidité)."""
        self.nb_erreurs += 1
        if len(self.erreurs) < MAX_MESSAGES:
            self.erreurs.append(message)

    def avertir(self, message: str) -> None:
        """Consigne un avertissement (conformité recommandée, pas obligatoire)."""
        self.nb_avertissements += 1
        if len(self.avertissements) < MAX_MESSAGES:
            self.avertissements.append(message)


@dataclass
class Boite:
    """Boîte englobante accumulée."""

    ouest: float = math.inf
    sud: float = math.inf
    est: float = -math.inf
    nord: float = -math.inf

    def ajouter(self, lon: float, lat: float) -> None:
        """Étend la boîte à une position."""
        self.ouest, self.est = min(self.ouest, lon), max(self.est, lon)
        self.sud, self.nord = min(self.sud, lat), max(self.nord, lat)

    def decrire(self) -> dict[str, float] | None:
        """Boîte en JSON (None si vide)."""
        if self.ouest == math.inf:
            return None
        return {"ouest": self.ouest, "sud": self.sud, "est": self.est, "nord": self.nord}


@dataclass(frozen=True)
class Reglages:
    """Paramètres d'analyse."""

    vitesse_max: float
    seuil_denivele: float
    max_sommets: int
    region: tuple[float, float, float, float] | None
    strict: bool


@dataclass
class PointGpx:
    """Un point GPX lu."""

    lat: float
    lon: float
    ele: float | None
    temps: datetime | None


@dataclass
class EtatGpx:
    """État de la lecture en flux d'un GPX."""

    constats: Constats
    boite: Boite = field(default_factory=Boite)
    version: str | None = None
    createur: str | None = None
    traces: list[dict[str, Any]] = field(default_factory=list)
    routes: list[dict[str, Any]] = field(default_factory=list)
    points_passage: int = 0
    courant: list[PointGpx] = field(default_factory=list)
    nb_points: int = 0


# --------------------------------------------------------------------------- #
# Géométrie commune

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance haversine en mètres sur la sphère de rayon moyen."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(((lon2 - lon1 + 180.0) % 360.0) - 180.0)
    h = math.sin((phi2 - phi1) / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * RAYON_MOYEN * math.asin(min(1.0, math.sqrt(h)))


def dans_bornes(lon: float, lat: float) -> bool:
    """Vrai si la position est dans [-180, 180] × [-90, 90]."""
    return -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0


def dans_region(lon: float, lat: float, region: tuple[float, float, float, float]) -> bool:
    """Vrai si la position est dans la région (ouest, sud, est, nord)."""
    ouest, sud, est, nord = region
    dans_lon = ouest <= lon <= est if ouest <= est else (lon >= ouest or lon <= est)
    return dans_lon and sud <= lat <= nord


# --------------------------------------------------------------------------- #
# Lecture des entrées

def lire_octets(chemin: Path, taille_max: int) -> bytes:
    """Lit un fichier borné ; refuse absent, dossier, trop gros."""
    if not chemin.exists():
        raise ErreurLecture(f"{chemin} : introuvable")
    if chemin.is_dir():
        raise ErreurLecture(f"{chemin} : est un dossier")
    if chemin.stat().st_size > taille_max:
        raise ErreurLecture(f"{chemin} : plus de {taille_max} octets (--taille-max)")
    try:
        return chemin.read_bytes()
    except OSError as erreur:
        raise ErreurLecture(f"{chemin} : lecture impossible ({erreur.strerror})") from erreur


def deviner_format(nom: str, donnees: bytes) -> str:
    """Rend « gpx » ou « geojson » d'après l'extension, sinon d'après le contenu."""
    bas = nom.lower()
    if bas.endswith(EXTENSIONS_GPX):
        return "gpx"
    if bas.endswith(EXTENSIONS_GEOJSON):
        return "geojson"
    if b"\x00" in donnees[:OCTETS_PROLOGUE]:
        raise ErreurLecture(f"{nom} : contenu binaire, ni GPX ni GeoJSON")
    debut = donnees.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    if debut == b"<":
        return "gpx"
    if debut == b"{":
        return "geojson"
    raise ErreurLecture(f"{nom} : format non reconnu (ni XML GPX ni objet GeoJSON)")


def est_geojson_probable(donnees: bytes) -> bool:
    """Pour un .json trouvé dans un dossier : objet portant un type GeoJSON ?"""
    try:
        objet = json.loads(donnees.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return False
    return isinstance(objet, dict) and objet.get("type") in TYPES_GEOJSON


def lister_dossier(dossier: Path, recursif: bool, taille_max: int) -> Iterator[Path]:
    """Fichiers GPX/GeoJSON pertinents d'un dossier (ordre trié)."""
    motif = "**/*" if recursif else "*"
    for chemin in sorted(dossier.glob(motif)):
        if not chemin.is_file():
            continue
        suffixe = chemin.suffix.lower()
        if suffixe in (".gpx", ".geojson"):
            yield chemin
        elif suffixe == ".json" and chemin.stat().st_size <= taille_max:
            if est_geojson_probable(lire_octets(chemin, taille_max)):
                yield chemin


# --------------------------------------------------------------------------- #
# GPX

def nom_local(balise: str) -> str:
    """Nom local d'une balise à espace de noms."""
    return balise.rsplit("}", 1)[-1]


def espace_de(balise: str) -> str:
    """Espace de noms d'une balise (chaîne vide sinon)."""
    return balise[1:].split("}", 1)[0] if balise.startswith("{") else ""


def lire_temps(texte: str | None, constats: Constats, ou: str) -> datetime | None:
    """Lit un horodatage ISO 8601 ; UTC si aucun décalage."""
    if texte is None:
        return None
    try:
        moment = datetime.fromisoformat(texte.strip())
    except ValueError:
        constats.avertir(f"{ou} : temps illisible « {texte.strip()[:40]} »")
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def lire_nombre_xml(texte: str | None, constats: Constats, ou: str) -> float | None:
    """Lit un nombre d'élément XML (altitude)."""
    if texte is None:
        return None
    try:
        valeur = float(texte)
    except ValueError:
        constats.avertir(f"{ou} : altitude illisible « {texte.strip()[:40]} »")
        return None
    return valeur if math.isfinite(valeur) else None


def lire_point_gpx(element: ET.Element, etat: EtatGpx, ou: str) -> PointGpx | None:
    """Lit lat/lon/ele/time d'un trkpt, rtept ou wpt."""
    try:
        lat, lon = float(element.attrib["lat"]), float(element.attrib["lon"])
    except KeyError:
        etat.constats.erreur(f"{ou} : attribut lat ou lon manquant")
        return None
    except ValueError:
        etat.constats.erreur(f"{ou} : lat/lon non numérique")
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or not dans_bornes(lon, lat):
        indice = " (lat et lon inversées ?)" if dans_bornes(lat, lon) else ""
        etat.constats.erreur(f"{ou} : position hors bornes lat={lat} lon={lon}{indice}")
        return None
    etat.boite.ajouter(lon, lat)
    ele = lire_nombre_xml(element.findtext("{*}ele"), etat.constats, ou)
    temps = lire_temps(element.findtext("{*}time"), etat.constats, ou)
    return PointGpx(lat, lon, ele, temps)


def deniveles(altitudes: Sequence[float], seuil: float) -> tuple[float, float, float, float]:
    """Dénivelés (positif, négatif) bruts puis filtrés par hystérésis de `seuil` mètres."""
    brut_plus = sum(max(0.0, b - a) for a, b in zip(altitudes, altitudes[1:]))
    brut_moins = sum(max(0.0, a - b) for a, b in zip(altitudes, altitudes[1:]))
    filtre_plus = filtre_moins = 0.0
    reference = altitudes[0] if altitudes else 0.0
    for altitude in altitudes[1:]:
        if altitude - reference >= seuil:
            filtre_plus, reference = filtre_plus + altitude - reference, altitude
        elif reference - altitude >= seuil:
            filtre_moins, reference = filtre_moins + reference - altitude, altitude
    return brut_plus, brut_moins, filtre_plus, filtre_moins


def vitesse(p: PointGpx, q: PointGpx, distance: float) -> float | None:
    """Vitesse en m/s entre deux points horodatés (inf si temps non croissant et déplacement)."""
    if p.temps is None or q.temps is None:
        return None
    duree = (q.temps - p.temps).total_seconds()
    if duree <= 0:
        return math.inf if distance > 0 else None
    return distance / duree


def qualifier_saut(points: Sequence[PointGpx], i: int, trop: Sequence[bool], vmax: float) -> str:
    """Raison d'un pas trop rapide : pic isolé si le point suivant revient sur une trajectoire plausible."""
    if i + 2 < len(points) and trop[i + 1]:
        a, c = points[i], points[i + 2]
        contournement = vitesse(a, c, haversine(a.lat, a.lon, c.lat, c.lon))
        if contournement is not None and contournement <= vmax:
            return "pic isolé"
    return "saut de vitesse"


def points_aberrants(points: Sequence[PointGpx], distances: Sequence[float], vmax: float) -> list[dict[str, Any]]:
    """Pics isolés (aller-retour rapide), sauts de vitesse et temps non croissants."""
    vitesses = [vitesse(points[i], points[i + 1], distances[i]) for i in range(len(distances))]
    trop = [v is not None and v > vmax for v in vitesses]
    aberrants: list[dict[str, Any]] = []
    retour_de_pic = False
    for i, v in enumerate(vitesses):
        if not trop[i] or retour_de_pic:
            retour_de_pic = False
            continue
        raison = "temps non croissant" if v == math.inf else qualifier_saut(points, i, trop, vmax)
        retour_de_pic = raison == "pic isolé"
        cible = points[i + 1]
        aberrants.append({"index": i + 1, "lat": cible.lat, "lon": cible.lon,
                          "vitesse_m_s": None if v == math.inf else round(v, 3), "raison": raison})
    return aberrants


def resumer_temps(points: Sequence[PointGpx], distance: float) -> dict[str, Any]:
    """Début, fin, durée et vitesse moyenne d'un segment."""
    temps = [p.temps for p in points if p.temps is not None]
    if len(temps) < 2:
        return {"debut": None, "fin": None, "duree_s": None, "vitesse_moyenne_m_s": None}
    duree = (temps[-1] - temps[0]).total_seconds()
    moyenne = round(distance / duree, 4) if duree > 0 else None
    return {"debut": temps[0].isoformat(), "fin": temps[-1].isoformat(), "duree_s": duree,
            "vitesse_moyenne_m_s": moyenne}


def analyser_segment(points: Sequence[PointGpx], reglages: Reglages) -> dict[str, Any]:
    """Statistiques d'un segment de trace ou d'une route."""
    distances = [haversine(p.lat, p.lon, q.lat, q.lon) for p, q in zip(points, points[1:])]
    distance = sum(distances)
    altitudes = [p.ele for p in points if p.ele is not None]
    plus, moins, filtre_plus, filtre_moins = deniveles(altitudes, reglages.seuil_denivele)
    vitesses = [v for v in (vitesse(p, q, d) for p, q, d in zip(points, points[1:], distances))
                if v is not None and v != math.inf]
    resume: dict[str, Any] = {
        "points": len(points), "distance_m": round(distance, 3),
        "points_avec_altitude": len(altitudes),
        "denivele_positif_m": round(plus, 3), "denivele_negatif_m": round(moins, 3),
        "denivele_positif_filtre_m": round(filtre_plus, 3), "denivele_negatif_filtre_m": round(filtre_moins, 3),
        "vitesse_max_m_s": round(max(vitesses), 4) if vitesses else None,
    }
    resume.update(resumer_temps(points, distance))
    resume["aberrants"] = points_aberrants(points, distances, reglages.vitesse_max)
    return resume


def ouvrir_balise(etat: EtatGpx, nom: str, element: ET.Element, pile: list[str]) -> None:
    """Traite l'ouverture d'une balise GPX."""
    if len(pile) == 1:
        verifier_racine_gpx(etat, element)
    elif nom == "trk":
        etat.traces.append({"nom": None, "segments": []})
    elif nom in ("trkseg", "rte"):
        etat.courant = []
        if nom == "rte":
            etat.routes.append({"nom": None})


def verifier_racine_gpx(etat: EtatGpx, element: ET.Element) -> None:
    """Racine gpx, espace de noms et version."""
    if nom_local(element.tag) != "gpx":
        etat.constats.erreur(f"racine <{nom_local(element.tag)}> au lieu de <gpx>")
        return
    espace = espace_de(element.tag)
    version = element.attrib.get("version")
    etat.version, etat.createur = version, element.attrib.get("creator")
    if espace not in ESPACES_GPX:
        etat.constats.avertir(f"espace de noms GPX inattendu « {espace or 'aucun'} »")
    elif version != ESPACES_GPX[espace]:
        etat.constats.avertir(f"version « {version} » ≠ espace de noms GPX {ESPACES_GPX[espace]}")


def fermer_balise(etat: EtatGpx, nom: str, element: ET.Element, pile: list[str], reglages: Reglages) -> None:
    """Traite la fermeture d'une balise GPX (points, noms, segments)."""
    parent = pile[-2] if len(pile) >= 2 else ""
    if nom in ("trkpt", "rtept", "wpt"):
        etat.nb_points += 1
        point = lire_point_gpx(element, etat, f"{nom} n°{etat.nb_points}")
        if point is not None and nom != "wpt":
            etat.courant.append(point)
        etat.points_passage += nom == "wpt"
        element.clear()
    elif nom == "name" and parent in ("trk", "rte"):
        cible = etat.traces[-1] if parent == "trk" else etat.routes[-1]
        cible["nom"] = (element.text or "").strip()
    elif nom == "trkseg" and etat.traces:
        etat.traces[-1]["segments"].append(analyser_segment(etat.courant, reglages))
        etat.courant = []
        element.clear()
    elif nom == "rte" and etat.routes:
        etat.routes[-1].update(analyser_segment(etat.courant, reglages))
        etat.courant = []
        element.clear()


def refuser_dtd(donnees: bytes) -> None:
    """Un GPX n'a pas de DTD : toute déclaration DOCTYPE/ENTITY est refusée."""
    prologue = donnees[:OCTETS_PROLOGUE]
    if b"<!DOCTYPE" in prologue or b"<!ENTITY" in prologue:
        raise ErreurLecture("déclaration DOCTYPE/ENTITY refusée (expansion d'entités) ; un GPX n'en a pas", CODE_DEFAUT)


def parcourir_gpx(flux: BinaryIO, etat: EtatGpx, reglages: Reglages) -> None:
    """Lecture en flux des évènements XML d'un GPX."""
    pile: list[str] = []
    for evenement, element in ET.iterparse(flux, events=("start", "end")):
        nom = nom_local(element.tag)
        if evenement == "start":
            pile.append(nom)
            ouvrir_balise(etat, nom, element, pile)
        else:
            fermer_balise(etat, nom, element, pile, reglages)
            pile.pop()


def analyser_gpx(donnees: bytes, reglages: Reglages) -> dict[str, Any]:
    """Analyse complète d'un document GPX."""
    etat = EtatGpx(Constats())
    try:
        refuser_dtd(donnees)
        parcourir_gpx(io.BytesIO(donnees), etat, reglages)
    except ErreurLecture as erreur:
        etat.constats.erreur(str(erreur))
    except ET.ParseError as erreur:
        etat.constats.erreur(f"XML mal formé : {erreur}")
    segments = [s for t in etat.traces for s in t["segments"]]
    aberrants = sum(len(s["aberrants"]) for s in segments) + sum(len(r.get("aberrants", [])) for r in etat.routes)
    if aberrants:
        etat.constats.avertir(f"{aberrants} point(s) aberrant(s) (vitesse > {reglages.vitesse_max} m/s ou temps non croissant)")
    if etat.nb_points == 0 and not etat.constats.nb_erreurs:
        etat.constats.avertir("aucun point (trkpt, rtept, wpt)")
    return {
        "format": "gpx", "version": etat.version, "createur": etat.createur,
        "points_examines": etat.nb_points, "traces": etat.traces, "routes": etat.routes,
        "points_de_passage": etat.points_passage, "boite": etat.boite.decrire(),
        "totaux": totaliser(segments), "constats": etat.constats, "aberrants": aberrants,
    }


def totaliser(segments: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Totaux sur tous les segments de traces."""
    durees = [s["duree_s"] for s in segments if s["duree_s"] is not None]
    return {
        "segments": len(segments), "points": sum(s["points"] for s in segments),
        "distance_m": round(sum(s["distance_m"] for s in segments), 3),
        "denivele_positif_m": round(sum(s["denivele_positif_m"] for s in segments), 3),
        "denivele_negatif_m": round(sum(s["denivele_negatif_m"] for s in segments), 3),
        "denivele_positif_filtre_m": round(sum(s["denivele_positif_filtre_m"] for s in segments), 3),
        "denivele_negatif_filtre_m": round(sum(s["denivele_negatif_filtre_m"] for s in segments), 3),
        "duree_s": sum(durees) if durees else None,
    }


# --------------------------------------------------------------------------- #
# GeoJSON : lecture

def refuser_constante(nom: str) -> float:
    """parse_constant : NaN et Infinity ne sont pas du JSON (RFC 8259)."""
    raise ValueError(f"constante {nom} interdite en JSON")


def paires_sans_doublon(constats: Constats) -> Any:
    """object_pairs_hook signalant les clés en double."""
    def crochet(paires: list[tuple[str, Any]]) -> dict[str, Any]:
        vues: set[str] = set()
        for cle, _ in paires:
            if cle in vues:
                constats.avertir(f"clé « {cle} » en double dans un objet (la dernière l'emporte)")
            vues.add(cle)
        return dict(paires)
    return crochet


def charger_json(donnees: bytes, constats: Constats) -> Any:
    """Décode et analyse le JSON ; consigne l'erreur et rend None si impossible."""
    try:
        texte = donnees.decode("utf-8-sig")
    except UnicodeDecodeError as erreur:
        constats.erreur(f"n'est pas de l'UTF-8 (octet {erreur.start}) ; la RFC 7946 impose UTF-8")
        return None
    try:
        return json.loads(texte, parse_constant=refuser_constante, object_pairs_hook=paires_sans_doublon(constats))
    except json.JSONDecodeError as erreur:
        constats.erreur(f"JSON invalide : {erreur.msg} (ligne {erreur.lineno}, colonne {erreur.colno})")
    except ValueError as erreur:
        constats.erreur(f"JSON invalide : {erreur}")
    except RecursionError:
        constats.erreur("JSON invalide : imbrication trop profonde")
    return None


# --------------------------------------------------------------------------- #
# GeoJSON : validation

@dataclass
class EtatGeojson:
    """Accumulateur de la validation GeoJSON."""

    constats: Constats
    reglages: Reglages
    boite: Boite = field(default_factory=Boite)
    positions: int = 0
    types: dict[str, int] = field(default_factory=dict)
    polygones: list[tuple[str, Any]] = field(default_factory=list)
    inversions: int = 0


def est_nombre(valeur: Any) -> bool:
    """Nombre JSON fini (bool exclu)."""
    return isinstance(valeur, (int, float)) and not isinstance(valeur, bool) and math.isfinite(valeur)


def valider_position(position: Any, ou: str, etat: EtatGeojson) -> tuple[float, float] | None:
    """Une position : 2 ou 3 nombres, bornes, inversion probable."""
    if not isinstance(position, list) or len(position) < 2 or not all(est_nombre(v) for v in position):
        etat.constats.erreur(f"{ou} : position invalide (tableau d'au moins 2 nombres attendu)")
        return None
    if len(position) > 3:
        etat.constats.avertir(f"{ou} : {len(position)} éléments ; la RFC 7946 déconseille plus de 3")
    lon, lat = float(position[0]), float(position[1])
    etat.positions += 1
    if not dans_bornes(lon, lat):
        indice = " — ordre [lat, lon] probable, la RFC 7946 impose [lon, lat]" if dans_bornes(lat, lon) else ""
        etat.inversions += bool(indice)
        etat.constats.erreur(f"{ou} : position hors bornes [{lon}, {lat}]{indice}")
        return None
    region = etat.reglages.region
    if region is not None and not dans_region(lon, lat, region) and dans_region(lat, lon, region):
        etat.inversions += 1
        etat.constats.erreur(f"{ou} : [{lon}, {lat}] hors --region, mais [{lat}, {lon}] y tombe : ordre inversé probable")
    etat.boite.ajouter(lon, lat)
    return lon, lat


def valider_ligne(coordonnees: Any, ou: str, etat: EtatGeojson, minimum: int) -> list[tuple[float, float]]:
    """Suite de positions (LineString, anneau, MultiPoint)."""
    if not isinstance(coordonnees, list):
        etat.constats.erreur(f"{ou} : tableau de positions attendu")
        return []
    if len(coordonnees) < minimum:
        etat.constats.erreur(f"{ou} : {len(coordonnees)} position(s), au moins {minimum} exigées")
    lues = [valider_position(p, f"{ou}[{i}]", etat) for i, p in enumerate(coordonnees)]
    sommets = [p for p in lues if p is not None]
    for i, (a, b) in enumerate(zip(sommets, sommets[1:])):
        if abs(b[0] - a[0]) > 180.0:
            etat.constats.avertir(f"{ou}[{i}] : saut de longitude > 180° (antiméridien non découpé, RFC 7946 §3.1.9)")
            break
    return sommets


def aire_signee(anneau: Sequence[tuple[float, float]]) -> float:
    """Aire signée (formule du lacet) dans le plan lon/lat : > 0 si anti-horaire."""
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(anneau, anneau[1:])) / 2


def orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    """Produit vectoriel (b - a) × (c - a)."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def sur_segment(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> bool:
    """p (colinéaire) appartient-il au segment [a, b] ?"""
    return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


def segments_se_coupent(a: tuple[float, float], b: tuple[float, float],
                        c: tuple[float, float], d: tuple[float, float]) -> bool:
    """Intersection (propre ou par contact) de [a, b] et [c, d]."""
    o1, o2, o3, o4 = orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b)
    if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0) and 0 not in (o1, o2, o3, o4):
        return True
    return any(o == 0 and sur_segment(p, q, r) for o, p, q, r in
               ((o1, a, b, c), (o2, a, b, d), (o3, c, d, a), (o4, c, d, b)))


def premiere_auto_intersection(anneau: Sequence[tuple[float, float]]) -> tuple[int, int] | None:
    """Premier couple de côtés non adjacents qui se coupent, ou None."""
    cotes = list(zip(anneau, anneau[1:]))
    n = len(cotes)
    for i in range(n):
        a, b = cotes[i]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            c, d = cotes[j]
            if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]):
                continue
            if segments_se_coupent(a, b, c, d):
                return i, j
    return None


def valider_anneau(coordonnees: Any, ou: str, etat: EtatGeojson, exterieur: bool) -> None:
    """Anneau linéaire : ≥ 4 positions, fermé, sens, auto-intersection."""
    anneau = valider_ligne(coordonnees, ou, etat, 4)
    if len(anneau) < 2 or len(anneau) != len(coordonnees):
        return
    if coordonnees[0] != coordonnees[-1]:
        etat.constats.erreur(f"{ou} : anneau non fermé (première position ≠ dernière)")
        return
    aire = aire_signee(anneau)
    if aire == 0:
        etat.constats.erreur(f"{ou} : anneau d'aire nulle (dégénéré)")
    elif (aire > 0) != exterieur:
        sens = "horaire" if aire < 0 else "anti-horaire"
        role = "extérieur" if exterieur else "intérieur (trou)"
        etat.constats.avertir(f"{ou} : anneau {role} {sens}, contraire à la règle de la main droite (RFC 7946 §3.1.6)")
    if len(anneau) - 1 > etat.reglages.max_sommets:
        etat.constats.avertir(f"{ou} : {len(anneau) - 1} sommets > --max-sommets, auto-intersection non cherchée")
        return
    croisement = premiere_auto_intersection(anneau)
    if croisement is not None:
        etat.constats.erreur(f"{ou} : anneau auto-sécant (côtés {croisement[0]} et {croisement[1]})")


def valider_polygone(coordonnees: Any, ou: str, etat: EtatGeojson) -> None:
    """Polygone : tableau d'anneaux, le premier extérieur."""
    if not isinstance(coordonnees, list) or not coordonnees:
        etat.constats.erreur(f"{ou} : tableau d'anneaux non vide attendu")
        return
    for i, anneau in enumerate(coordonnees):
        valider_anneau(anneau, f"{ou}[{i}]", etat, i == 0)


def valider_coordonnees(type_geo: str, coordonnees: Any, ou: str, etat: EtatGeojson) -> None:
    """Dispatch de la validation des coordonnées selon le type."""
    if type_geo == "Point":
        valider_position(coordonnees, ou, etat)
    elif type_geo in ("MultiPoint", "LineString"):
        valider_ligne(coordonnees, ou, etat, 2 if type_geo == "LineString" else 0)
    elif type_geo == "Polygon":
        valider_polygone(coordonnees, ou, etat)
    elif not isinstance(coordonnees, list):
        etat.constats.erreur(f"{ou} : tableau attendu pour {type_geo}")
    elif type_geo == "MultiLineString":
        for i, ligne in enumerate(coordonnees):
            valider_ligne(ligne, f"{ou}[{i}]", etat, 2)
    else:
        for i, polygone in enumerate(coordonnees):
            valider_polygone(polygone, f"{ou}[{i}]", etat)


def valider_bbox(objet: dict[str, Any], ou: str, etat: EtatGeojson) -> None:
    """Membre bbox : 2n nombres ; ouest ≤ est sauf antiméridien ; sud ≤ nord."""
    bbox = objet.get("bbox")
    if bbox is None:
        return
    if not isinstance(bbox, list) or len(bbox) not in (4, 6) or not all(est_nombre(v) for v in bbox):
        etat.constats.erreur(f"{ou}.bbox : 4 ou 6 nombres attendus")
        return
    moitie = len(bbox) // 2
    if bbox[1] > bbox[1 + moitie]:
        etat.constats.erreur(f"{ou}.bbox : sud {bbox[1]} > nord {bbox[1 + moitie]}")


def valider_geometrie(objet: Any, ou: str, etat: EtatGeojson, profondeur: int) -> None:
    """Un objet géométrie (y compris GeometryCollection)."""
    if not isinstance(objet, dict):
        etat.constats.erreur(f"{ou} : objet géométrie attendu")
        return
    type_geo = objet.get("type")
    if type_geo not in (*TYPES_GEOMETRIE, "GeometryCollection"):
        etat.constats.erreur(f"{ou} : membre « type » manquant" if type_geo is None
                             else f"{ou} : type de géométrie « {type_geo} » inconnu")
        return
    etat.types[type_geo] = etat.types.get(type_geo, 0) + 1
    valider_bbox(objet, ou, etat)
    if type_geo == "GeometryCollection":
        valider_collection_geometries(objet, ou, etat, profondeur)
        return
    if "coordinates" not in objet:
        etat.constats.erreur(f"{ou} : membre « coordinates » manquant")
        return
    valider_coordonnees(type_geo, objet["coordinates"], f"{ou}.coordinates", etat)
    if type_geo in ("Polygon", "MultiPolygon"):
        etat.polygones.append((ou, objet))


def valider_collection_geometries(objet: dict[str, Any], ou: str, etat: EtatGeojson, profondeur: int) -> None:
    """GeometryCollection : membre geometries ; imbrication déconseillée."""
    geometries = objet.get("geometries")
    if not isinstance(geometries, list):
        etat.constats.erreur(f"{ou} : membre « geometries » (tableau) manquant")
        return
    if profondeur > 0:
        etat.constats.avertir(f"{ou} : GeometryCollection imbriquée (déconseillé, RFC 7946 §3.1.8)")
    if profondeur >= PROFONDEUR_MAX:
        etat.constats.erreur(f"{ou} : imbrication de GeometryCollection > {PROFONDEUR_MAX}")
        return
    if len(geometries) == 1:
        etat.constats.avertir(f"{ou} : GeometryCollection d'une seule géométrie (déconseillé)")
    for i, geometrie in enumerate(geometries):
        valider_geometrie(geometrie, f"{ou}.geometries[{i}]", etat, profondeur + 1)


def valider_feature(objet: dict[str, Any], ou: str, etat: EtatGeojson) -> None:
    """Feature : membres geometry et properties obligatoires, id chaîne ou nombre."""
    etat.types["Feature"] = etat.types.get("Feature", 0) + 1
    valider_bbox(objet, ou, etat)
    if "properties" not in objet:
        etat.constats.erreur(f"{ou} : membre « properties » manquant (null admis)")
    elif objet["properties"] is not None and not isinstance(objet["properties"], dict):
        etat.constats.erreur(f"{ou}.properties : objet ou null attendu")
    if "id" in objet and not (isinstance(objet["id"], str) or est_nombre(objet["id"])):
        etat.constats.erreur(f"{ou}.id : chaîne ou nombre attendu")
    if "geometry" not in objet:
        etat.constats.erreur(f"{ou} : membre « geometry » manquant (null admis)")
    elif objet["geometry"] is not None:
        valider_geometrie(objet["geometry"], f"{ou}.geometry", etat, 0)


def valider_racine(objet: Any, etat: EtatGeojson) -> None:
    """Objet racine : FeatureCollection, Feature ou géométrie."""
    if not isinstance(objet, dict):
        etat.constats.erreur("la racine doit être un objet JSON")
        return
    if "crs" in objet:
        etat.constats.avertir("membre « crs » : retiré par la RFC 7946 (WGS84 imposé)")
    type_racine = objet.get("type")
    if type_racine == "FeatureCollection":
        etat.types["FeatureCollection"] = 1
        valider_bbox(objet, "$", etat)
        features = objet.get("features")
        if not isinstance(features, list):
            etat.constats.erreur("$ : membre « features » (tableau) manquant")
            return
        for i, feature in enumerate(features):
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                etat.constats.erreur(f"$.features[{i}] : objet de type « Feature » attendu")
                continue
            valider_feature(feature, f"$.features[{i}]", etat)
    elif type_racine == "Feature":
        valider_feature(objet, "$", etat)
    else:
        valider_geometrie(objet, "$", etat, 0)


def verifier_bbox_racine(objet: Any, etat: EtatGeojson) -> None:
    """Le bbox racine contient-il toutes les positions lues ?"""
    if not isinstance(objet, dict) or not isinstance(objet.get("bbox"), list):
        return
    bbox, boite = objet["bbox"], etat.boite.decrire()
    if boite is None or len(bbox) not in (4, 6) or not all(est_nombre(v) for v in bbox):
        return
    moitie = len(bbox) // 2
    ouest, sud, est, nord = bbox[0], bbox[1], bbox[moitie], bbox[moitie + 1]
    coins = ((boite["ouest"], boite["sud"]), (boite["est"], boite["nord"]))
    if not all(dans_region(lon, lat, (ouest, sud, est, nord)) for lon, lat in coins):
        etat.constats.erreur(f"$.bbox {bbox} ne contient pas toutes les positions (boîte calculée {boite})")


def controler_shapely(polygones: Sequence[tuple[str, Any]]) -> dict[str, Any] | None:
    """Revalide chaque polygone avec shapely (is_valid, explain_validity)."""
    if forme_shapely is None:
        return None
    invalides = []
    for ou, objet in polygones:
        try:
            geometrie = forme_shapely(objet)
            valide, raison = geometrie.is_valid, explain_validity(geometrie)
        except (ValueError, TypeError, AttributeError, IndexError) as erreur:
            valide, raison = False, f"construction impossible : {erreur}"
        if not valide:
            invalides.append({"ou": ou, "raison": raison})
    return {"moteur": "shapely", "version": getattr(shapely, "__version__", None),
            "polygones": len(polygones), "invalides": invalides[:MAX_MESSAGES]}


def analyser_geojson(donnees: bytes, reglages: Reglages) -> dict[str, Any]:
    """Analyse complète d'un document GeoJSON."""
    constats = Constats()
    objet = charger_json(donnees, constats)
    etat = EtatGeojson(constats, reglages)
    if objet is not None:
        valider_racine(objet, etat)
        verifier_bbox_racine(objet, etat)
    controle = controler_shapely(etat.polygones)
    if controle is not None:
        for invalide in controle["invalides"]:
            constats.avertir(f"shapely : {invalide['ou']} invalide ({invalide['raison']})")
    return {"format": "geojson", "type_racine": objet.get("type") if isinstance(objet, dict) else None,
            "types": etat.types, "points_examines": etat.positions, "boite": etat.boite.decrire(),
            "inversions_probables": etat.inversions, "constats": constats, "controle": controle}


# --------------------------------------------------------------------------- #
# Contre-vérification gpxpy

def controler_gpxpy(donnees: bytes, rapport: dict[str, Any]) -> dict[str, Any] | None:
    """Recalcule distance, dénivelés et durée avec gpxpy ; publie les écarts."""
    if gpxpy is None or rapport["constats"].nb_erreurs:
        return None
    try:
        document = gpxpy.parse(donnees.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError, gpxpy.gpx.GPXException) as erreur:
        return {"moteur": "gpxpy", "erreur": str(erreur)[:200]}
    distance = sum(s.length_2d() or 0.0 for t in document.tracks for s in t.segments)
    montee, descente = document.get_uphill_downhill()
    totaux = rapport["totaux"]
    return {"moteur": "gpxpy", "version": getattr(gpxpy, "__version__", None),
            "distance_m": round(distance, 3), "ecart_distance_m": round(distance - totaux["distance_m"], 3),
            "denivele_positif_m": round(montee, 3), "denivele_negatif_m": round(descente, 3),
            "duree_s": document.get_duration()}


# --------------------------------------------------------------------------- #
# Orchestration

def examiner(nom: str, donnees: bytes, reglages: Reglages) -> dict[str, Any]:
    """Examine un document (fichier ou texte) et rend son rapport."""
    format_doc = deviner_format(nom, donnees)
    if format_doc == "gpx":
        rapport = analyser_gpx(donnees, reglages)
        rapport["controle"] = controler_gpxpy(donnees, rapport)
    else:
        rapport = analyser_geojson(donnees, reglages)
    constats: Constats = rapport.pop("constats")
    invalide = constats.nb_erreurs > 0 or (reglages.strict and constats.nb_avertissements > 0)
    return {"source": nom, "valide": not invalide, **rapport,
            "erreurs": constats.erreurs, "nb_erreurs": constats.nb_erreurs,
            "avertissements": constats.avertissements, "nb_avertissements": constats.nb_avertissements}


def resoudre(texte: str, racine: Path | None) -> Path:
    """Chemin relatif à --racine si elle est donnée."""
    chemin = Path(texte).expanduser()
    return racine / chemin if racine is not None and not chemin.is_absolute() else chemin


def collecter(entrees: Sequence[str], racine: Path | None, recursif: bool, taille_max: int) -> list[Path]:
    """Fichiers à examiner : fichiers donnés, plus les pertinents des dossiers donnés."""
    fichiers: list[Path] = []
    for entree in entrees:
        chemin = resoudre(entree, racine)
        if chemin.is_dir():
            fichiers.extend(lister_dossier(chemin, recursif, taille_max))
        elif not chemin.exists():
            raise ErreurLecture(f"{chemin} : introuvable")
        else:
            fichiers.append(chemin)
    return fichiers


def lire_region(texte: str | None) -> tuple[float, float, float, float] | None:
    """Lit --region ouest,sud,est,nord."""
    if texte is None:
        return None
    try:
        valeurs = tuple(float(v) for v in texte.split(","))
    except ValueError as erreur:
        raise ErreurLecture(f"--region « {texte} » : quatre nombres ouest,sud,est,nord attendus") from erreur
    if len(valeurs) != 4 or not dans_bornes(valeurs[0], valeurs[1]) or not dans_bornes(valeurs[2], valeurs[3]):
        raise ErreurLecture(f"--region « {texte} » : quatre nombres ouest,sud,est,nord dans les bornes attendus")
    if valeurs[1] > valeurs[3]:
        raise ErreurLecture(f"--region « {texte} » : sud > nord")
    return valeurs  # type: ignore[return-value]


def extraire_contrat(doc: str) -> dict[str, str]:
    """Extrait du docstring les sections du contrat de mesure."""
    contrat: dict[str, str] = {}
    courant = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES and not ligne.startswith(" "):
            courant = tete
            contrat[courant] = ""
        elif courant:
            contrat[courant] = (contrat[courant] + " " + tete).strip()
    return contrat


def construire_parseur() -> argparse.ArgumentParser:
    """Déclare l'interface en ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Décrit et valide des traces GPX (1.0/1.1) et des documents GeoJSON (RFC 7946) : "
                    "distances, dénivelés, durées, points aberrants, géométries, anneaux, ordre lon/lat.",
        epilog="exemple : lire_gpx_geojson.py sortie_velo.gpx --json   | lire_gpx_geojson.py donnees/ --recursif   "
               "| lire_gpx_geojson.py communes.geojson --region -5.5,41,10,51.5",
    )
    parseur.add_argument("entrees", nargs="*", metavar="ENTREE", help="fichier .gpx/.geojson/.json ou dossier à parcourir")
    parseur.add_argument("--texte", action="append", default=[], metavar="TEXTE",
                         help="document GPX ou GeoJSON donné en ligne (répétable)")
    parseur.add_argument("--recursif", action="store_true", help="parcourir les sous-dossiers")
    parseur.add_argument("--vitesse-max", type=float, default=VITESSE_MAX_DEFAUT,
                         help=f"vitesse au-delà de laquelle un pas GPX est aberrant, m/s (défaut {VITESSE_MAX_DEFAUT})")
    parseur.add_argument("--seuil-denivele", type=float, default=SEUIL_DENIVELE_DEFAUT,
                         help=f"hystérésis du dénivelé filtré, m (défaut {SEUIL_DENIVELE_DEFAUT})")
    parseur.add_argument("--max-sommets", type=int, default=MAX_SOMMETS_DEFAUT,
                         help=f"au-delà, l'auto-intersection d'un anneau n'est pas cherchée (défaut {MAX_SOMMETS_DEFAUT})")
    parseur.add_argument("--region", metavar="O,S,E,N", help="région attendue : sert à prouver une inversion lon/lat")
    parseur.add_argument("--strict", action="store_true", help="les avertissements rendent aussi le document invalide")
    parseur.add_argument("--taille-max", type=int, default=TAILLE_MAX_DEFAUT, help="taille maximale d'un fichier, octets")
    parseur.add_argument("--racine", type=Path, default=None, help="base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def preparer(args: argparse.Namespace) -> tuple[Reglages, list[Path]]:
    """Contrôle les options et collecte les fichiers."""
    if args.vitesse_max <= 0 or args.seuil_denivele < 0 or args.max_sommets < 0 or args.taille_max <= 0:
        raise ErreurLecture("--vitesse-max > 0, --seuil-denivele ≥ 0, --max-sommets ≥ 0 et --taille-max > 0 attendus")
    if args.racine is not None and not args.racine.is_dir():
        raise ErreurLecture(f"--racine {args.racine} : dossier introuvable")
    reglages = Reglages(args.vitesse_max, args.seuil_denivele, args.max_sommets, lire_region(args.region), args.strict)
    return reglages, collecter(args.entrees, args.racine, args.recursif, args.taille_max)


def tout_examiner(args: argparse.Namespace, reglages: Reglages, fichiers: Sequence[Path]) -> list[dict[str, Any]]:
    """Examine les textes en ligne puis les fichiers."""
    rapports = []
    for numero, texte in enumerate(args.texte, start=1):
        rapports.append(examiner(f"<texte {numero}>", texte.encode("utf-8"), reglages))
    for chemin in fichiers:
        rapports.append(examiner(str(chemin), lire_octets(chemin, args.taille_max), reglages))
    return rapports


def afficher_humain(rapports: Sequence[dict[str, Any]]) -> None:
    """Une ligne de synthèse par document, puis erreurs et avertissements."""
    for r in rapports:
        etat = VERDICT_VALIDE if r["valide"] else VERDICT_INVALIDE
        if r["format"] == "gpx":
            t = r["totaux"]
            print(f"{r['source']} : GPX {r['version']} {etat} — {len(r['traces'])} trace(s), {t['segments']} segment(s), "
                  f"{r['points_examines']} point(s), {t['distance_m']} m, D+ {t['denivele_positif_m']} m "
                  f"(filtré {t['denivele_positif_filtre_m']} m), D- {t['denivele_negatif_m']} m, durée {t['duree_s']} s, "
                  f"{r['aberrants']} aberrant(s)")
        else:
            print(f"{r['source']} : GeoJSON {r['type_racine']} {etat} — {r['points_examines']} position(s), "
                  f"types {r['types']}, boîte {r['boite']}")
        for message in r["erreurs"]:
            print(f"  erreur : {message}")
        for message in r["avertissements"]:
            print(f"  avertissement : {message}")


def annoncer_moteurs() -> str:
    """Moteurs de contre-vérification disponibles ; une seule ligne stderr si l'un manque."""
    absents = [nom for nom, module in (("gpxpy", gpxpy), ("shapely", shapely)) if module is None]
    if absents:
        print(f"lire_gpx_geojson : {' et '.join(absents)} absent(s) — repli stdlib (xml.etree, json, géométrie plane "
              "maison) sans contre-vérification ; trous de polygones non confrontés à l'anneau extérieur",
              file=sys.stderr)
    presents = [nom for nom, module in (("gpxpy", gpxpy), ("shapely", shapely)) if module is not None]
    return "+".join(presents) if presents else "stdlib"


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : collecte, examine, publie."""
    args = construire_parseur().parse_args(argv)
    try:
        reglages, fichiers = preparer(args)
        moteur = annoncer_moteurs()
        rapports = tout_examiner(args, reglages, fichiers)
    except ErreurLecture as erreur:
        print(f"lire_gpx_geojson : {erreur}", file=sys.stderr)
        return erreur.code
    if not rapports:
        print("lire_gpx_geojson : dénominateur nul — aucun fichier GPX ou GeoJSON trouvé, rien à examiner", file=sys.stderr)
        if args.json:
            print(json.dumps({"outil": "lire_gpx_geojson", "denominateur": 0, "examines": []}, ensure_ascii=False))
        return CODE_RIEN
    invalides = [r["source"] for r in rapports if not r["valide"]]
    if args.json:
        sources = [r["source"] for r in rapports]
        print(json.dumps({"outil": "lire_gpx_geojson", "moteur": moteur, "denominateur": len(rapports),
                          "examines": sources[:EXAMINES_MAX], "examines_tronques": len(sources) > EXAMINES_MAX,
                          "invalides": invalides, "documents": rapports,
                          "contrat": extraire_contrat(__doc__ or "")}, ensure_ascii=False, indent=2))
    else:
        afficher_humain(rapports)
    return CODE_DEFAUT if invalides else CODE_OK


if __name__ == "__main__":
    raise SystemExit(main())
