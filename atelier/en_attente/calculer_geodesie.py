"""Distances, caps, point d'arrivée, geohash et boîte englobante sur l'ellipsoïde WGS84.

Un agent qui calcule une distance la fait sur une sphère (haversine) et ne voit pas
l'écart : mesuré dans cette session, `calculer_geodesie.py "48.8566,2.3522"
"40.7128,-74.0060"` (Paris → New York) rend 5 852 935,292 m sur WGS84 contre
5 837 248,9 m en haversine (rayon moyen 6 371 008,8 m), 15,7 km d'écart. Vincenty seul
ne suffit pas non plus : sur 10 000 paires tirées à moins de 1° de l'antipode (graine 1),
son itération ne converge pas pour 2 187 d'entre elles ; le repli par bissection de cet
outil rend alors la distance à 0,076 mm près de geographiclib 2.1 (Karney).

QUESTION
    Quelle distance et quel cap entre ces coordonnées, sur l'ellipsoïde WGS84 ?
MESURE
    Problème inverse de Vincenty (1975) itéré sur la longitude auxiliaire ; s'il ne
    converge pas en 200 itérations (points quasi antipodaux), repli : bissection sur
    l'azimut de départ dans la configuration canonique de Karney (2013), où la
    différence de longitude croît avec l'azimut, puis distance par les séries de
    Vincenty. Distance haversine sur la sphère de rayon moyen R1 = (2a+b)/3, à titre de
    comparaison. Caps initial et final (degrés depuis le nord, sens horaire, [0, 360)).
    Problème direct de Vincenty (point et cap d'arrivée). Geohash (base 32, 1 à 12
    caractères) encodé et décodé, avec la taille de cellule en mètres. Boîte
    englobante des sommets, élargie aux latitudes extrêmes atteintes par les
    géodésiques entre points consécutifs, et franchissement de l'antiméridien ;
    boîte d'un disque géodésique de rayon donné. Si geographiclib (ou à défaut pyproj)
    est installé, chaque résultat est refait par la méthode de Karney et l'écart est
    publié en millimètres.
HYPOTHÈSES
    Coordonnées géodésiques WGS84 (latitude, longitude en degrés ; l'ordre lon,lat de
    GeoJSON exige --ordre lonlat) ; hauteurs ellipsoïdales nulles ; distances en
    mètres le long de la géodésique (le plus court chemin sur l'ellipsoïde), pas
    d'une route ni d'une loxodromie.
LIMITES
    Aucune altitude ni relief ; aucun autre ellipsoïde ou datum (pas de reprojection
    ni de transformation ED50, NAD27...) ; pour des points exactement antipodaux la
    géodésique n'est pas unique et un seul cap est rendu (signalé) ; au pôle, le cap
    dépend de la convention (méridien de la longitude donnée) ; la largeur d'une
    cellule geohash est mesurée le long du parallèle central ; la boîte d'un disque
    cherche la longitude extrême par recherche ternaire (précision ~1e-9°).
CONTRE-EXEMPLES
    Constaté dans cette session : (0, 0) → (0, 90) donnerait 19 970 326 m par le
    repli seul, faux (la bonne valeur, 10 018 754,171 m, suit l'équateur) : la
    bissection ne vaut pas pour deux points de l'équateur, d'où le cas équatorial
    traité à part. Un geohash de chiffres seuls (« 12345 ») est ambigu avec un
    nombre : il est refusé sans le préfixe gh:.
INVOCATION
    {outil} "48.8566,2.3522" "51.5074,-0.1278" --geohash --boite --json
DOMAINE
    Positions terrestres WGS84 (GPS, cartographie web, logistique, aviation,
    maritime) : distances de quelques millimètres à la demi-circonférence, toutes
    latitudes, antiméridien compris.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from geographiclib.geodesic import Geodesic
    import geographiclib
except ImportError:
    Geodesic = None
    geographiclib = None

try:
    import pyproj
except ImportError:
    pyproj = None

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
ELLIPSOIDE = "WGS84"
DATUM_ED50 = "ED50"
DATUM_NAD27 = "NAD27"

CODE_OK = 0
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_RIEN = 3

DEMI_GRAND_AXE = 6378137.0
APLATISSEMENT = 1 / 298.257223563
DEMI_PETIT_AXE = DEMI_GRAND_AXE * (1 - APLATISSEMENT)
EXCENTRICITE2 = APLATISSEMENT * (2 - APLATISSEMENT)
SECONDE_EXCENTRICITE2 = (DEMI_GRAND_AXE ** 2 - DEMI_PETIT_AXE ** 2) / DEMI_PETIT_AXE ** 2
RAYON_MOYEN = (2 * DEMI_GRAND_AXE + DEMI_PETIT_AXE) / 3

ITERATIONS_MAX = 200
SEUIL_CONVERGENCE = 1e-12
TAILLE_MAX_FICHIER = 50 * 1024 * 1024
EXAMINES_MAX = 50
TOLERANCE_MM_DEFAUT = 1.0
PRECISION_GEOHASH_DEFAUT = 9
ALPHABET_GEOHASH = "0123456789bcdefghjkmnpqrstuvwxyz"
UNITES_DISTANCE = {"m": 1.0, "km": 1000.0, "nmi": 1852.0, "mi": 1609.344}

MOTIF_COMPOSANTE = re.compile(
    r"""^\s*(?P<h1>[NSEWO])?\s*(?P<signe>[+-])?\s*
    (?P<deg>\d+(?:\.\d+)?)\s*(?:°|º|d|:|\s)?\s*
    (?:(?P<min>\d+(?:\.\d+)?)\s*(?:'|′|’|m|:|\s)?\s*
       (?:(?P<sec>\d+(?:\.\d+)?)\s*(?:"|″|''|s)?)?)?
    \s*(?P<h2>[NSEWO])?\s*$""",
    re.VERBOSE | re.IGNORECASE,
)
MOTIF_GEOHASH = re.compile(r"^[0-9bcdefghjkmnpqrstuvwxyz]{1,12}$")
MOTIF_DISTANCE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*([a-zA-Z]*)\s*$")
MOTIF_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,5}$")


class ErreurGeodesie(Exception):
    """Erreur d'entrée, portant le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Point:
    """Un point géodésique lu en entrée."""

    lat: float
    lon: float
    entree: str
    nom: str = ""
    cellule: dict[str, float] | None = None


@dataclass
class Lecture:
    """Points lus et lignes rejetées."""

    points: list[Point] = field(default_factory=list)
    rejets: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Inverse:
    """Résultat d'un problème inverse."""

    distance: float
    cap_initial: float
    cap_final: float
    methode: str
    iterations: int


# --------------------------------------------------------------------------- #
# Outils angulaires

def normaliser_longitude(lon: float) -> float:
    """Ramène une longitude dans [-180, 180)."""
    return ((lon + 180.0) % 360.0) - 180.0


def normaliser_cap(cap: float) -> float:
    """Ramène un cap dans [0, 360), en effaçant le bruit d'arrondi."""
    valeur = round(cap % 360.0, 10)
    return 0.0 if valeur >= 360.0 else valeur


def ecart_angulaire(a: float, b: float) -> float:
    """Écart absolu entre deux angles en degrés, modulo 360."""
    return abs(((a - b + 180.0) % 360.0) - 180.0)


def latitude_reduite(lat_deg: float) -> float:
    """Latitude réduite (paramétrique) β en radians."""
    if abs(lat_deg) >= 90.0:
        return math.copysign(math.pi / 2, lat_deg)
    return math.atan((1 - APLATISSEMENT) * math.tan(math.radians(lat_deg)))


# --------------------------------------------------------------------------- #
# Séries de Vincenty

def coefficients_ab(u2: float) -> tuple[float, float]:
    """Coefficients A et B de Vincenty pour u²."""
    a = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    b = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    return a, b


def delta_sigma(b: float, sin_s: float, cos_s: float, c2sm: float) -> float:
    """Correction Δσ de Vincenty."""
    return b * sin_s * (c2sm + b / 4 * (cos_s * (-1 + 2 * c2sm ** 2)
                                       - b / 6 * c2sm * (-3 + 4 * sin_s ** 2) * (-3 + 4 * c2sm ** 2)))


def correction_longitude(sin_a0: float, c2a0: float, sigma: float, c2sm: float) -> float:
    """Terme (1-C) f sin α [σ + C sin σ (...)] reliant longitude auxiliaire et ellipsoïdale."""
    c = APLATISSEMENT / 16 * c2a0 * (4 + APLATISSEMENT * (4 - 3 * c2a0))
    crochet = c2sm + c * math.cos(sigma) * (-1 + 2 * c2sm ** 2)
    return (1 - c) * APLATISSEMENT * sin_a0 * (sigma + c * math.sin(sigma) * crochet)


def longueur_arc(c2a0: float, sigma: float, c2sm: float) -> float:
    """Longueur s = b A (σ - Δσ) d'un arc géodésique."""
    a, b = coefficients_ab(c2a0 * SECONDE_EXCENTRICITE2)
    return DEMI_PETIT_AXE * a * (sigma - delta_sigma(b, math.sin(sigma), math.cos(sigma), c2sm))


# --------------------------------------------------------------------------- #
# Problème inverse

def iterer_vincenty(beta1: float, beta2: float, l_rad: float) -> tuple[float, ...] | None:
    """Itère λ ; rend (λ, σ, sin α, cos² α, cos 2σm, n) ou None si pas de convergence."""
    su1, cu1, su2, cu2 = math.sin(beta1), math.cos(beta1), math.sin(beta2), math.cos(beta2)
    lam = l_rad
    for iteration in range(1, ITERATIONS_MAX + 1):
        sl, cl = math.sin(lam), math.cos(lam)
        sin_s = math.hypot(cu2 * sl, cu1 * su2 - su1 * cu2 * cl)
        if sin_s == 0.0:
            return lam, 0.0, 0.0, 1.0, 1.0, iteration
        cos_s = su1 * su2 + cu1 * cu2 * cl
        sigma = math.atan2(sin_s, cos_s)
        sin_a = cu1 * cu2 * sl / sin_s
        c2a = 1 - sin_a ** 2
        c2sm = cos_s - 2 * su1 * su2 / c2a if c2a != 0 else 0.0
        precedent = lam
        lam = l_rad + correction_longitude(sin_a, c2a, sigma, c2sm)
        if abs(lam) > math.pi:
            return None
        if abs(lam - precedent) < SEUIL_CONVERGENCE:
            return lam, sigma, sin_a, c2a, c2sm, iteration
    return None


def inverse_vincenty(p1: Point, p2: Point) -> Inverse | None:
    """Problème inverse de Vincenty, ou None s'il ne converge pas."""
    beta1, beta2 = latitude_reduite(p1.lat), latitude_reduite(p2.lat)
    l_rad = math.radians(normaliser_longitude(p2.lon - p1.lon))
    etat = iterer_vincenty(beta1, beta2, l_rad)
    if etat is None:
        return None
    lam, sigma, _sin_a, c2a, c2sm, iterations = etat
    if sigma == 0.0:
        return Inverse(0.0, 0.0, 0.0, "vincenty", iterations)
    su1, cu1, su2, cu2 = math.sin(beta1), math.cos(beta1), math.sin(beta2), math.cos(beta2)
    sl, cl = math.sin(lam), math.cos(lam)
    cap1 = math.degrees(math.atan2(cu2 * sl, cu1 * su2 - su1 * cu2 * cl))
    cap2 = math.degrees(math.atan2(cu1 * sl, -su1 * cu2 + cu1 * su2 * cl))
    distance = longueur_arc(c2a, sigma, c2sm)
    return Inverse(distance, normaliser_cap(cap1), normaliser_cap(cap2), "vincenty", iterations)


def longitude_canonique(alpha1: float, b1: tuple[float, float], b2: tuple[float, float]) -> dict[str, float]:
    """Pour un azimut α1 canonique, différence de longitude atteinte au premier passage nord de β2."""
    sb1, cb1 = b1
    sb2, cb2 = b2
    sa1, ca1 = math.sin(alpha1), math.cos(alpha1)
    sin_a0 = sa1 * cb1
    c2a0 = 1 - sin_a0 * sin_a0
    sig1 = math.atan2(sb1, ca1 * cb1)
    om1 = math.atan2(sin_a0 * math.sin(sig1), math.cos(sig1))
    ca2 = math.sqrt(max(0.0, (ca1 * cb1) ** 2 + (cb2 * cb2 - cb1 * cb1))) / cb2
    sig2 = math.atan2(sb2, ca2 * cb2)
    om2 = math.atan2(sin_a0 * math.sin(sig2), math.cos(sig2))
    sigma = (sig2 - sig1) % (2 * math.pi)
    omega = (om2 - om1) % (2 * math.pi)
    c2sm = math.cos(sig1 + sig2)
    lam = omega - correction_longitude(sin_a0, c2a0, sigma, c2sm)
    return {"lambda": lam, "sigma": sigma, "c2sm": c2sm, "c2a0": c2a0,
            "sa1": sa1, "ca1": ca1, "sa2": sin_a0 / cb2, "ca2": ca2}


def canoniser(p1: Point, p2: Point) -> tuple[float, float, float, float, float, float]:
    """Configuration canonique de Karney : (lat1, lat2, lon12, swapp, lonsign, latsign)."""
    lon12 = normaliser_longitude(p2.lon - p1.lon)
    if lon12 == -180.0:
        lon12 = 180.0
    lonsign = 1.0 if lon12 >= 0 else -1.0
    lon12 *= lonsign
    lat1, lat2 = p1.lat, p2.lat
    swapp = -1.0 if abs(lat1) < abs(lat2) else 1.0
    if swapp < 0:
        lonsign *= -1
        lat1, lat2 = lat2, lat1
    latsign = 1.0 if lat1 < 0 else -1.0
    return lat1 * latsign, lat2 * latsign, lon12, swapp, lonsign, latsign


def bissection_azimut(b1: tuple[float, float], b2: tuple[float, float], cible: float) -> float:
    """Cherche α1 ∈ [0, π] tel que la longitude atteinte égale la cible."""
    bas, haut = 0.0, math.pi
    for _ in range(ITERATIONS_MAX):
        milieu = (bas + haut) / 2
        if longitude_canonique(milieu, b1, b2)["lambda"] < cible:
            bas = milieu
        else:
            haut = milieu
        if haut - bas < 1e-15:
            break
    return (bas + haut) / 2


def inverse_repli(p1: Point, p2: Point) -> Inverse:
    """Repli quasi antipodal : bissection sur l'azimut de départ (configuration canonique)."""
    lat1, lat2, lon12, swapp, lonsign, latsign = canoniser(p1, p2)
    beta1, beta2 = latitude_reduite(lat1), latitude_reduite(lat2)
    b1, b2 = (math.sin(beta1), math.cos(beta1)), (math.sin(beta2), math.cos(beta2))
    alpha1 = bissection_azimut(b1, b2, math.radians(lon12))
    etat = longitude_canonique(alpha1, b1, b2)
    distance = longueur_arc(etat["c2a0"], etat["sigma"], etat["c2sm"])
    sa1, ca1, sa2, ca2 = etat["sa1"], etat["ca1"], etat["sa2"], etat["ca2"]
    if swapp < 0:
        sa1, sa2, ca1, ca2 = sa2, sa1, ca2, ca1
    cap1 = math.degrees(math.atan2(sa1 * swapp * lonsign, ca1 * swapp * latsign))
    cap2 = math.degrees(math.atan2(sa2 * swapp * lonsign, ca2 * swapp * latsign))
    return Inverse(distance, normaliser_cap(cap1), normaliser_cap(cap2), "repli-bissection-azimut", 0)


def inverse_equatorial(p1: Point, p2: Point) -> Inverse | None:
    """Cas des deux points sur l'équateur : géodésique équatoriale si |Δλ| ≤ (1-f)·180°."""
    if p1.lat != 0.0 or p2.lat != 0.0:
        return None
    dlon = normaliser_longitude(p2.lon - p1.lon)
    if abs(dlon) > (1 - APLATISSEMENT) * 180.0:
        return None
    cap = 90.0 if dlon >= 0 else 270.0
    return Inverse(DEMI_GRAND_AXE * math.radians(abs(dlon)), cap, cap, "equateur", 0)


def resoudre_inverse(p1: Point, p2: Point) -> Inverse:
    """Vincenty d'abord ; cas équatorial ; sinon repli par bissection."""
    resultat = inverse_vincenty(p1, p2)
    if resultat is not None:
        return resultat
    resultat = inverse_equatorial(p1, p2)
    if resultat is not None:
        return resultat
    return inverse_repli(p1, p2)


def distance_haversine(p1: Point, p2: Point) -> float:
    """Distance haversine sur la sphère de rayon moyen R1."""
    phi1, phi2 = math.radians(p1.lat), math.radians(p2.lat)
    dphi = phi2 - phi1
    dlam = math.radians(normaliser_longitude(p2.lon - p1.lon))
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * RAYON_MOYEN * math.asin(min(1.0, math.sqrt(h)))


# --------------------------------------------------------------------------- #
# Problème direct

def iterer_sigma_direct(distance: float, a: float, b: float, sigma1: float) -> float:
    """Itère σ dans le problème direct de Vincenty."""
    base = distance / (DEMI_PETIT_AXE * a)
    sigma = base
    for _ in range(ITERATIONS_MAX):
        c2sm = math.cos(2 * sigma1 + sigma)
        suivant = base + delta_sigma(b, math.sin(sigma), math.cos(sigma), c2sm)
        if abs(suivant - sigma) < SEUIL_CONVERGENCE:
            return suivant
        sigma = suivant
    return sigma


def resoudre_direct(lat1: float, lon1: float, cap1: float, distance: float) -> tuple[float, float, float]:
    """Problème direct de Vincenty : (latitude, longitude, cap final) d'arrivée."""
    a1 = math.radians(cap1)
    sa1, ca1 = math.sin(a1), math.cos(a1)
    beta1 = latitude_reduite(lat1)
    su1, cu1 = math.sin(beta1), math.cos(beta1)
    sigma1 = math.atan2(su1, cu1 * ca1)
    sin_a = cu1 * sa1
    c2a = 1 - sin_a * sin_a
    a, b = coefficients_ab(c2a * SECONDE_EXCENTRICITE2)
    sigma = iterer_sigma_direct(distance, a, b, sigma1)
    ss, cs = math.sin(sigma), math.cos(sigma)
    c2sm = math.cos(2 * sigma1 + sigma)
    x = su1 * ss - cu1 * cs * ca1
    lat2 = math.atan2(su1 * cs + cu1 * ss * ca1, (1 - APLATISSEMENT) * math.hypot(sin_a, x))
    lam = math.atan2(ss * sa1, cu1 * cs - su1 * ss * ca1)
    dlon = lam - correction_longitude(sin_a, c2a, sigma, c2sm)
    cap2 = math.degrees(math.atan2(sin_a, -x))
    return math.degrees(lat2), normaliser_longitude(lon1 + math.degrees(dlon)), normaliser_cap(cap2)


# --------------------------------------------------------------------------- #
# Geohash

def encoder_geohash(lat: float, lon: float, precision: int) -> str:
    """Encode une position en geohash de `precision` caractères."""
    bornes = [[-90.0, 90.0], [-180.0, 180.0]]
    caracteres: list[str] = []
    bit, valeur, axe = 0, 0, 1
    while len(caracteres) < precision:
        intervalle = bornes[axe]
        cible = lon if axe == 1 else lat
        milieu = (intervalle[0] + intervalle[1]) / 2
        valeur = (valeur << 1) | (1 if cible >= milieu else 0)
        intervalle[0 if cible >= milieu else 1] = milieu
        axe, bit = 1 - axe, bit + 1
        if bit == 5:
            caracteres.append(ALPHABET_GEOHASH[valeur])
            bit, valeur = 0, 0
    return "".join(caracteres)


def decoder_geohash(code: str) -> dict[str, float]:
    """Décode un geohash en cellule (bornes, centre, erreurs, dimensions en mètres)."""
    bornes = [[-90.0, 90.0], [-180.0, 180.0]]
    axe = 1
    for caractere in code.lower():
        index = ALPHABET_GEOHASH.find(caractere)
        if index < 0:
            raise ErreurGeodesie(f"geohash « {code} » : caractère « {caractere} » hors de l'alphabet base 32")
        for decalage in range(4, -1, -1):
            intervalle = bornes[axe]
            milieu = (intervalle[0] + intervalle[1]) / 2
            intervalle[0 if (index >> decalage) & 1 else 1] = milieu
            axe = 1 - axe
    return decrire_cellule(bornes[0][0], bornes[0][1], bornes[1][0], bornes[1][1])


def decrire_cellule(sud: float, nord: float, ouest: float, est: float) -> dict[str, float]:
    """Centre, demi-erreurs et dimensions métriques d'une cellule geohash."""
    lat, lon = (sud + nord) / 2, (ouest + est) / 2
    hauteur = resoudre_inverse(Point(sud, lon, ""), Point(nord, lon, "")).distance
    grande_normale = DEMI_GRAND_AXE / math.sqrt(1 - EXCENTRICITE2 * math.sin(math.radians(lat)) ** 2)
    largeur = grande_normale * math.cos(math.radians(lat)) * math.radians(est - ouest)
    return {"sud": sud, "nord": nord, "ouest": ouest, "est": est, "lat": lat, "lon": lon,
            "erreur_lat_deg": (nord - sud) / 2, "erreur_lon_deg": (est - ouest) / 2,
            "hauteur_m": round(hauteur, 4), "largeur_m": round(largeur, 4)}


# --------------------------------------------------------------------------- #
# Lecture des coordonnées

def valeur_composante(correspondance: re.Match[str], texte: str) -> float:
    """Convertit une composante décimale ou DMS en degrés signés (hors hémisphère)."""
    degres = float(correspondance["deg"])
    minutes = float(correspondance["min"]) if correspondance["min"] else 0.0
    secondes = float(correspondance["sec"]) if correspondance["sec"] else 0.0
    if correspondance["min"] and not degres.is_integer():
        raise ErreurGeodesie(f"« {texte} » : degrés décimaux suivis de minutes")
    if minutes >= 60 or secondes >= 60:
        raise ErreurGeodesie(f"« {texte} » : minutes ou secondes ≥ 60")
    if correspondance["sec"] and not minutes.is_integer():
        raise ErreurGeodesie(f"« {texte} » : minutes décimales suivies de secondes")
    valeur = degres + minutes / 60 + secondes / 3600
    return -valeur if correspondance["signe"] == "-" else valeur


def lire_composante(texte: str) -> tuple[float, str]:
    """Lit « 48.85 », « -2.3 », « 48°51'24"N », « 2 21 08 E » : (valeur signée, hémisphère)."""
    correspondance = MOTIF_COMPOSANTE.match(texte)
    if correspondance is None:
        raise ErreurGeodesie(f"« {texte} » : coordonnée illisible (décimal ou DMS attendu)")
    h1, h2 = correspondance["h1"], correspondance["h2"]
    if h1 and h2:
        raise ErreurGeodesie(f"« {texte} » : hémisphère donné deux fois")
    hemisphere = (h1 or h2 or "").upper()
    valeur = valeur_composante(correspondance, texte)
    if hemisphere and correspondance["signe"]:
        raise ErreurGeodesie(f"« {texte} » : signe et hémisphère à la fois (ambigu)")
    if hemisphere in ("S", "W", "O"):
        valeur = -valeur
    return valeur, hemisphere


def couper_paire(texte: str) -> tuple[str, str]:
    """Coupe « lat,lon », « lat;lon », « 48°N 2°E » ou « 48.8 2.3 » en deux composantes."""
    for separateur in (";", ",", "\t"):
        if separateur in texte:
            gauche, _, droite = texte.partition(separateur)
            return gauche, droite
    correspondance = re.match(r"^(.*?[NSEWO])\s*([NSEWO+-]?\s*\d.*)$", texte.strip(), re.IGNORECASE)
    if correspondance:
        return correspondance.group(1), correspondance.group(2)
    morceaux = texte.split()
    if len(morceaux) == 2:
        return morceaux[0], morceaux[1]
    raise ErreurGeodesie(f"« {texte} » : impossible de séparer latitude et longitude "
                         "(écrire « lat,lon » ou indiquer N/S et E/W)")


def ordonner_composantes(a: tuple[float, str], b: tuple[float, str], ordre: str) -> tuple[float, float]:
    """Attribue latitude et longitude selon les hémisphères ou l'ordre déclaré."""
    lettres_lat, lettres_lon = {"N", "S"}, {"E", "W", "O"}
    if a[1] in lettres_lon or b[1] in lettres_lat:
        return b[0], a[0]
    if a[1] in lettres_lat or b[1] in lettres_lon:
        return a[0], b[0]
    return (a[0], b[0]) if ordre == "latlon" else (b[0], a[0])


def verifier_bornes(lat: float, lon: float, texte: str) -> None:
    """Refuse une latitude hors [-90, 90] ou une longitude hors [-180, 180]."""
    if not math.isfinite(lat) or not math.isfinite(lon):
        raise ErreurGeodesie(f"« {texte} » : valeur non finie")
    if abs(lat) > 90:
        indice = " ; ordre lon,lat ? essayer --ordre lonlat" if abs(lon) <= 90 else ""
        raise ErreurGeodesie(f"« {texte} » : latitude {lat} hors de [-90, 90]{indice}")
    if abs(lon) > 180:
        raise ErreurGeodesie(f"« {texte} » : longitude {lon} hors de [-180, 180]")


def lire_point(texte: str, ordre: str, nom: str = "") -> Point:
    """Lit un point (paire de coordonnées) ou un geohash."""
    brut = texte.strip()
    if brut.lower().startswith("gh:"):
        return point_de_geohash(brut[3:], brut, nom)
    if MOTIF_GEOHASH.match(brut) and not brut.isdigit():
        return point_de_geohash(brut, brut, nom)
    if brut.replace(".", "", 1).lstrip("+-").isdigit():
        indice = f" (ou gh:{brut} pour un geohash)" if MOTIF_GEOHASH.match(brut) else ""
        raise ErreurGeodesie(f"« {brut} » : un seul nombre ; écrire « lat,lon »{indice}")
    gauche, droite = couper_paire(brut)
    lat, lon = ordonner_composantes(lire_composante(gauche), lire_composante(droite), ordre)
    verifier_bornes(lat, lon, brut)
    return Point(lat, lon, brut, nom)


def point_de_geohash(code: str, entree: str, nom: str) -> Point:
    """Point au centre d'une cellule geohash, cellule jointe."""
    code = code.strip().lower()
    if not 1 <= len(code) <= 12:
        raise ErreurGeodesie(f"geohash « {code} » : 1 à 12 caractères attendus")
    cellule = decoder_geohash(code)
    return Point(cellule["lat"], cellule["lon"], entree, nom, cellule)


def resoudre_chemin(texte: str, racine: Path | None) -> Path:
    """Chemin d'entrée, relatif à --racine si elle est donnée."""
    chemin = Path(texte).expanduser()
    if not chemin.is_absolute() and racine is not None:
        chemin = racine / chemin
    return chemin


def ressemble_chemin(texte: str) -> bool:
    """Vrai si le jeton désigne vraisemblablement un fichier."""
    return "/" in texte or "\\" in texte or bool(MOTIF_EXTENSION.search(texte))


def lire_texte_fichier(chemin: Path) -> str:
    """Lit un fichier texte UTF-8 borné ; refuse dossier, binaire, absent."""
    if not chemin.exists():
        raise ErreurGeodesie(f"{chemin} : fichier introuvable")
    if chemin.is_dir():
        raise ErreurGeodesie(f"{chemin} : est un dossier, un fichier de points est attendu")
    if chemin.stat().st_size > TAILLE_MAX_FICHIER:
        raise ErreurGeodesie(f"{chemin} : plus de {TAILLE_MAX_FICHIER} octets, refusé")
    try:
        donnees = chemin.read_bytes()
    except OSError as erreur:
        raise ErreurGeodesie(f"{chemin} : lecture impossible ({erreur.strerror})") from erreur
    if b"\x00" in donnees:
        raise ErreurGeodesie(f"{chemin} : contenu binaire (octet nul), fichier de points texte attendu")
    try:
        return donnees.decode("utf-8-sig")
    except UnicodeDecodeError as erreur:
        raise ErreurGeodesie(f"{chemin} : n'est pas du texte UTF-8 (octet {erreur.start})") from erreur


def colonnes_entete(ligne: str) -> dict[str, int] | None:
    """Repère une ligne d'en-tête lat/lon/nom ; rend les index de colonnes."""
    champs = [c.strip().lower() for c in re.split(r"[;,\t]", ligne)]
    noms = {"lat": ("lat", "latitude"), "lon": ("lon", "lng", "long", "longitude"), "nom": ("nom", "name", "id")}
    index: dict[str, int] = {}
    for cle, synonymes in noms.items():
        for position, champ in enumerate(champs):
            if champ in synonymes:
                index[cle] = position
    return index if "lat" in index and "lon" in index else None


def lire_ligne_points(ligne: str, entete: dict[str, int] | None, ordre: str) -> Point:
    """Lit une ligne de fichier de points (avec ou sans en-tête)."""
    if entete is None:
        champs = re.split(r"[;\t]", ligne) if (";" in ligne or "\t" in ligne) else ligne.split(",")
        if len(champs) >= 3:
            return lire_point(f"{champs[0]},{champs[1]}", ordre, ",".join(champs[2:]).strip())
        return lire_point(ligne, ordre)
    champs = [c.strip() for c in re.split(r"[;,\t]", ligne)]
    if max(entete.values()) >= len(champs):
        raise ErreurGeodesie(f"« {ligne} » : colonnes manquantes")
    nom = champs[entete["nom"]] if "nom" in entete else ""
    return lire_point(f"{champs[entete['lat']]},{champs[entete['lon']]}", "latlon", nom)


def lire_fichier_points(chemin: Path, ordre: str, lecture: Lecture) -> None:
    """Ajoute à `lecture` les points d'un fichier ; consigne les lignes rejetées."""
    entete: dict[str, int] | None = None
    for numero, brute in enumerate(lire_texte_fichier(chemin).splitlines(), start=1):
        ligne = brute.strip()
        if not ligne or ligne.startswith("#"):
            continue
        if not lecture.points and entete is None and not re.search(r"\d", ligne):
            entete = colonnes_entete(ligne)
            if entete is not None:
                continue
        try:
            lecture.points.append(lire_ligne_points(ligne, entete, ordre))
        except ErreurGeodesie as erreur:
            lecture.rejets.append(f"{chemin.name}:{numero} : {erreur}")


def lire_entrees(jetons: Sequence[str], fichiers: Sequence[str], ordre: str, racine: Path | None) -> Lecture:
    """Lit les points donnés en ligne, les geohash et les fichiers de points."""
    lecture = Lecture()
    for jeton in jetons:
        chemin = resoudre_chemin(jeton, racine)
        if ressemble_chemin(jeton) or chemin.exists():
            lire_fichier_points(chemin, ordre, lecture)
        else:
            lecture.points.append(lire_point(jeton, ordre))
    for nom in fichiers:
        lire_fichier_points(resoudre_chemin(nom, racine), ordre, lecture)
    return lecture


def lire_distance(texte: str) -> float:
    """Lit « 5000 », « 12.5km », « 3 nmi » en mètres."""
    correspondance = MOTIF_DISTANCE.match(texte)
    if correspondance is None or correspondance.group(2).lower() not in ("", *UNITES_DISTANCE):
        raise ErreurGeodesie(f"distance « {texte} » illisible (unités : m, km, nmi, mi)")
    valeur = float(correspondance.group(1)) * UNITES_DISTANCE.get(correspondance.group(2).lower() or "m")
    if not math.isfinite(valeur) or valeur < 0:
        raise ErreurGeodesie(f"distance « {texte} » : positive et finie attendue")
    return valeur


# --------------------------------------------------------------------------- #
# Formats de sortie

def en_dms(valeur: float, positif: str, negatif: str) -> str:
    """Formate des degrés décimaux en DMS (secondes au millième)."""
    hemisphere = positif if valeur >= 0 else negatif
    millisecondes = round(abs(valeur) * 3_600_000)
    degres, reste = divmod(millisecondes, 3_600_000)
    minutes, reste = divmod(reste, 60_000)
    return f"{degres}°{minutes:02d}'{reste / 1000:06.3f}\"{hemisphere}"


def decrire_point(point: Point, geohash: int | None) -> dict[str, Any]:
    """Description JSON d'un point."""
    description: dict[str, Any] = {
        "entree": point.entree, "lat": round(point.lat, 10), "lon": round(point.lon, 10),
        "dms": f"{en_dms(point.lat, 'N', 'S')} {en_dms(point.lon, 'E', 'W')}",
    }
    if point.nom:
        description["nom"] = point.nom
    if point.cellule is not None:
        description["cellule_geohash"] = point.cellule
    if geohash:
        description["geohash"] = encoder_geohash(point.lat, point.lon, geohash)
    return description


# --------------------------------------------------------------------------- #
# Contre-vérification (Karney)

def creer_controleur(moteur: str) -> tuple[str, Any]:
    """Choisit le moteur de contre-vérification ; annonce le repli sur stderr."""
    if moteur == "stdlib":
        return "stdlib", None
    if moteur in ("auto", "geographiclib") and Geodesic is not None:
        return "geographiclib", Geodesic.WGS84
    if moteur in ("auto", "pyproj") and pyproj is not None:
        return "pyproj", pyproj.Geod(ellps="WGS84")
    manquant = "geographiclib et pyproj absents" if moteur == "auto" else f"{moteur} absent"
    print(f"calculer_geodesie : {manquant} — repli stdlib seul (Vincenty + bissection), sans contre-vérification Karney",
          file=sys.stderr)
    return "stdlib", None


def version_moteur(nom: str) -> str | None:
    """Version de la bibliothèque de contre-vérification."""
    module = {"geographiclib": geographiclib, "pyproj": pyproj}.get(nom)
    return getattr(module, "__version__", None) if module is not None else None


def karney_inverse(controleur: tuple[str, Any], p1: Point, p2: Point) -> tuple[float, float, float]:
    """Distance et caps par la méthode de Karney (geographiclib ou pyproj)."""
    nom, objet = controleur
    if nom == "geographiclib":
        r = objet.Inverse(p1.lat, p1.lon, p2.lat, p2.lon)
        return r["s12"], r["azi1"], r["azi2"]
    cap1, cap_retour, distance = objet.inv(p1.lon, p1.lat, p2.lon, p2.lat)
    return distance, cap1, cap_retour + 180.0


def karney_direct(controleur: tuple[str, Any], p: Point, cap: float, distance: float) -> tuple[float, float]:
    """Point d'arrivée par la méthode de Karney."""
    nom, objet = controleur
    if nom == "geographiclib":
        r = objet.Direct(p.lat, p.lon, cap, distance)
        return r["lat2"], r["lon2"]
    lon2, lat2, _ = objet.fwd(p.lon, p.lat, cap, distance)
    return lat2, lon2


def controler_inverse(controleur: tuple[str, Any], p1: Point, p2: Point, calcul: Inverse) -> dict[str, Any] | None:
    """Écart (mm) entre le calcul de l'outil et la méthode de Karney."""
    if controleur[1] is None:
        return None
    distance, cap1, cap2 = karney_inverse(controleur, p1, p2)
    return {"moteur": controleur[0], "distance_m": round(distance, 6),
            "ecart_mm": round(abs(distance - calcul.distance) * 1000, 4),
            "ecart_cap_initial_deg": round(ecart_angulaire(cap1, calcul.cap_initial), 9),
            "ecart_cap_final_deg": round(ecart_angulaire(cap2, calcul.cap_final), 9)}


# --------------------------------------------------------------------------- #
# Calculs assemblés

def calculer_segment(controleur: tuple[str, Any], p1: Point, p2: Point, indices: tuple[int, int]) -> dict[str, Any]:
    """Distance, caps, haversine et contrôle pour une paire de points."""
    calcul = resoudre_inverse(p1, p2)
    haversine = distance_haversine(p1, p2)
    segment: dict[str, Any] = {
        "de": indices[0], "vers": indices[1], "distance_m": round(calcul.distance, 4),
        "cap_initial_deg": round(calcul.cap_initial, 9), "cap_final_deg": round(calcul.cap_final, 9),
        "methode": calcul.methode, "iterations": calcul.iterations,
        "distance_haversine_m": round(haversine, 4),
        "ecart_haversine_m": round(haversine - calcul.distance, 4),
    }
    if p1.lat == -p2.lat and abs(normaliser_longitude(p2.lon - p1.lon)) == 180.0:
        segment["geodesique_non_unique"] = True
    controle = controler_inverse(controleur, p1, p2, calcul)
    if controle is not None:
        segment["controle"] = controle
    return segment


def paires(points: Sequence[Point], etoile: bool) -> list[tuple[int, int]]:
    """Paires examinées : consécutives (chaîne) ou depuis le premier point (étoile)."""
    if etoile:
        return [(0, j) for j in range(1, len(points))]
    return [(i, i + 1) for i in range(len(points) - 1)]


def calculer_direct(controleur: tuple[str, Any], point: Point, cap: float, distance: float) -> dict[str, Any]:
    """Point d'arrivée depuis `point` au cap et à la distance donnés."""
    lat2, lon2, cap2 = resoudre_direct(point.lat, point.lon, cap, distance)
    resultat: dict[str, Any] = {
        "depart": point.entree, "cap_deg": cap, "distance_m": distance,
        "lat": round(lat2, 10), "lon": round(lon2, 10), "cap_final_deg": round(cap2, 9),
        "dms": f"{en_dms(lat2, 'N', 'S')} {en_dms(lon2, 'E', 'W')}",
    }
    if controleur[1] is not None:
        lat_k, lon_k = karney_direct(controleur, point, cap, distance)
        ecart = resoudre_inverse(Point(lat2, lon2, ""), Point(lat_k, lon_k, "")).distance
        resultat["controle"] = {"moteur": controleur[0], "lat": lat_k, "lon": lon_k, "ecart_mm": round(ecart * 1000, 4)}
    return resultat


def intervalle_longitudes(longitudes: Sequence[float]) -> tuple[float, float]:
    """Plus petit intervalle circulaire couvrant des longitudes isolées : (ouest, est)."""
    tries = sorted(normaliser_longitude(lon) for lon in longitudes)
    if len(tries) == 1:
        return tries[0], tries[0]
    trous = [(tries[(i + 1) % len(tries)] - tries[i]) % 360.0 for i in range(len(tries))]
    plus_grand = max(range(len(trous)), key=lambda i: trous[i])
    return tries[(plus_grand + 1) % len(tries)], tries[plus_grand]


def balayage_longitudes(points: Sequence[Point], couples: Sequence[tuple[int, int]]) -> tuple[float, float]:
    """Longitudes balayées par les géodésiques, déroulées depuis le premier point : (ouest, est)."""
    if not couples:
        return intervalle_longitudes([p.lon for p in points])
    deroulees = {couples[0][0]: points[couples[0][0]].lon}
    for i, j in couples:
        deroulees[j] = deroulees[i] + normaliser_longitude(points[j].lon - points[i].lon)
    mini, maxi = min(deroulees.values()), max(deroulees.values())
    if maxi - mini >= 360.0:
        return -180.0, 180.0
    return normaliser_longitude(mini), normaliser_longitude(maxi)


def latitude_sommet(p1: Point, calcul: Inverse) -> float | None:
    """Latitude extrême de la géodésique si son sommet tombe entre les deux points."""
    c1, c2 = math.cos(math.radians(calcul.cap_initial)), math.cos(math.radians(calcul.cap_final))
    if calcul.distance == 0 or c1 * c2 >= 0:
        return None
    sin_a0 = math.cos(latitude_reduite(p1.lat)) * math.sin(math.radians(calcul.cap_initial))
    beta = math.acos(min(1.0, abs(sin_a0)))
    lat = math.degrees(math.atan(math.tan(beta) / (1 - APLATISSEMENT))) if beta < math.pi / 2 else 90.0
    return lat if c1 > 0 else -lat


def boite_englobante(points: Sequence[Point], couples: Sequence[tuple[int, int]]) -> dict[str, Any]:
    """Boîte des sommets, élargie aux latitudes extrêmes et aux longitudes balayées par les géodésiques."""
    latitudes = [p.lat for p in points]
    for i, j in couples:
        sommet = latitude_sommet(points[i], resoudre_inverse(points[i], points[j]))
        if sommet is not None:
            latitudes.append(sommet)
    sud, nord = min(latitudes), max(latitudes)
    ouest, est = balayage_longitudes(points, couples)
    if nord >= 90.0 or sud <= -90.0:
        ouest, est = -180.0, 180.0
    return {"sud": round(sud, 10), "nord": round(nord, 10), "ouest": round(ouest, 10), "est": round(est, 10),
            "traverse_antimeridien": ouest > est, "contient_un_pole": nord >= 90.0 or sud <= -90.0,
            "elargie_par_geodesiques": sud < min(p.lat for p in points) or nord > max(p.lat for p in points)}


def longitude_extreme(point: Point, rayon: float) -> float:
    """Écart de longitude maximal (vers l'est) d'un disque géodésique, par recherche ternaire."""
    def ecart(cap: float) -> float:
        return normaliser_longitude(resoudre_direct(point.lat, point.lon, cap, rayon)[1] - point.lon)

    bas, haut = 0.0, 180.0
    for _ in range(100):
        tiers1, tiers2 = bas + (haut - bas) / 3, haut - (haut - bas) / 3
        if ecart(tiers1) < ecart(tiers2):
            bas = tiers1
        else:
            haut = tiers2
    return ecart((bas + haut) / 2)


def boite_rayon(point: Point, rayon: float) -> dict[str, Any]:
    """Boîte englobante du disque géodésique de rayon donné autour d'un point."""
    nord = resoudre_direct(point.lat, point.lon, 0.0, rayon)[0]
    sud = resoudre_direct(point.lat, point.lon, 180.0, rayon)[0]
    vers_pole_nord = resoudre_inverse(point, Point(90.0, point.lon, "")).distance
    vers_pole_sud = resoudre_inverse(point, Point(-90.0, point.lon, "")).distance
    if rayon >= vers_pole_nord or rayon >= vers_pole_sud:
        nord = 90.0 if rayon >= vers_pole_nord else nord
        sud = -90.0 if rayon >= vers_pole_sud else sud
        return {"centre": point.entree, "rayon_m": rayon, "sud": round(sud, 10), "nord": round(nord, 10),
                "ouest": -180.0, "est": 180.0, "contient_un_pole": True, "traverse_antimeridien": False}
    delta = longitude_extreme(point, rayon)
    ouest, est = normaliser_longitude(point.lon - delta), normaliser_longitude(point.lon + delta)
    return {"centre": point.entree, "rayon_m": rayon, "sud": round(sud, 10), "nord": round(nord, 10),
            "ouest": round(ouest, 10), "est": round(est, 10), "contient_un_pole": False,
            "traverse_antimeridien": ouest > est}


def depassements(segments: Sequence[dict[str, Any]], directs: Sequence[dict[str, Any]], tolerance_mm: float) -> list[str]:
    """Écarts au-delà de la tolérance entre l'outil et la méthode de Karney."""
    alertes = []
    for s in segments:
        if "controle" in s and s["controle"]["ecart_mm"] > tolerance_mm:
            alertes.append(f"segment {s['de']}→{s['vers']} : écart {s['controle']['ecart_mm']} mm > {tolerance_mm} mm")
    for d in directs:
        if "controle" in d and d["controle"]["ecart_mm"] > tolerance_mm:
            alertes.append(f"direct depuis {d['depart']} : écart {d['controle']['ecart_mm']} mm > {tolerance_mm} mm")
    return alertes


def calculer(args: argparse.Namespace, points: Sequence[Point], controleur: tuple[str, Any]) -> dict[str, Any]:
    """Assemble tous les calculs demandés."""
    couples = paires(points, args.etoile)
    segments = [calculer_segment(controleur, points[i], points[j], (i, j)) for i, j in couples]
    directs = []
    if args.direct:
        cap, distance = float(args.direct[0]), lire_distance(args.direct[1])
        directs = [calculer_direct(controleur, p, cap, distance) for p in points]
    rapport: dict[str, Any] = {
        "points": [decrire_point(p, args.precision if args.geohash else None) for p in points],
        "segments": segments, "directs": directs,
        "longueur_totale_m": None if args.etoile else round(sum(s["distance_m"] for s in segments), 4),
        "longueur_totale_haversine_m": None if args.etoile else round(sum(s["distance_haversine_m"] for s in segments), 4),
    }
    if args.boite:
        rapport["boite"] = boite_englobante(points, couples)
    if args.rayon is not None:
        rayon = lire_distance(args.rayon)
        rapport["boites_rayon"] = [boite_rayon(p, rayon) for p in points]
    rapport["alertes"] = depassements(segments, directs, args.tolerance_mm)
    return rapport


# --------------------------------------------------------------------------- #
# Interface

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
        description="Distances géodésiques (Vincenty, repli antipodal), caps, problème direct, geohash et "
                    "boîte englobante sur l'ellipsoïde WGS84 ; contre-vérification Karney si geographiclib est là.",
        epilog='exemple : calculer_geodesie.py "48.8566,2.3522" "40.7128,-74.0060" --json   '
               "| calculer_geodesie.py \"48°51'24\\\"N 2°21'08\\\"E\" u09tunq --geohash   "
               '| calculer_geodesie.py "0,0" --direct 45 100km --rayon 5km',
    )
    parseur.add_argument("points", nargs="*", metavar="ENTREE",
                         help='point « lat,lon » décimal ou DMS, geohash (u09tunq, gh:12345), ou fichier de points')
    parseur.add_argument("--fichier", action="append", default=[], metavar="FICHIER",
                         help="fichier de points (une paire par ligne, ou CSV avec colonnes lat/lon[/nom])")
    parseur.add_argument("--ordre", choices=("latlon", "lonlat"), default="latlon",
                         help="ordre des paires sans hémisphère (défaut latlon ; GeoJSON : lonlat)")
    parseur.add_argument("--etoile", action="store_true",
                         help="distances du premier point vers chacun des autres (défaut : points consécutifs)")
    parseur.add_argument("--direct", nargs=2, metavar=("CAP", "DISTANCE"),
                         help="problème direct depuis chaque point : cap en degrés, distance (m, km, nmi, mi)")
    parseur.add_argument("--geohash", action="store_true", help="geohash de chaque point")
    parseur.add_argument("--precision", type=int, default=PRECISION_GEOHASH_DEFAUT,
                         help=f"longueur du geohash, 1 à 12 (défaut {PRECISION_GEOHASH_DEFAUT})")
    parseur.add_argument("--boite", action="store_true", help="boîte englobante des points et des géodésiques")
    parseur.add_argument("--rayon", metavar="DISTANCE", help="boîte d'un disque géodésique de ce rayon autour de chaque point")
    parseur.add_argument("--moteur", choices=("auto", "stdlib", "geographiclib", "pyproj"), default="auto",
                         help="contre-vérification Karney : auto (geographiclib puis pyproj), ou jamais (stdlib)")
    parseur.add_argument("--tolerance-mm", type=float, default=TOLERANCE_MM_DEFAUT,
                         help=f"écart toléré avec Karney avant de signaler un défaut (défaut {TOLERANCE_MM_DEFAUT} mm)")
    parseur.add_argument("--racine", type=Path, default=None, help="base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def valider_arguments(args: argparse.Namespace) -> None:
    """Contrôles d'arguments qu'argparse ne fait pas."""
    if not 1 <= args.precision <= 12:
        raise ErreurGeodesie("--precision doit être compris entre 1 et 12")
    if args.direct:
        try:
            cap = float(args.direct[0])
        except ValueError as erreur:
            raise ErreurGeodesie(f"--direct : cap « {args.direct[0]} » non numérique") from erreur
        if not math.isfinite(cap):
            raise ErreurGeodesie("--direct : cap non fini")
    if not math.isfinite(args.tolerance_mm) or args.tolerance_mm < 0:
        raise ErreurGeodesie("--tolerance-mm doit être positive")
    if args.racine is not None and not args.racine.is_dir():
        raise ErreurGeodesie(f"--racine {args.racine} : dossier introuvable")


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Affichage lisible : points, segments, directs, boîtes."""
    for index, p in enumerate(rapport["points"]):
        suffixe = f"  geohash {p['geohash']}" if "geohash" in p else ""
        print(f"P{index} {p['lat']:.8f}, {p['lon']:.8f}  ({p['dms']}){suffixe}")
    for s in rapport["segments"]:
        controle = s.get("controle")
        verif = f" ; {controle['moteur']} : écart {controle['ecart_mm']} mm" if controle else ""
        print(f"P{s['de']} → P{s['vers']} : {s['distance_m']:.4f} m [{s['methode']}] ; cap initial "
              f"{s['cap_initial_deg']:.6f}°, final {s['cap_final_deg']:.6f}° ; haversine {s['distance_haversine_m']:.1f} m "
              f"(écart {s['ecart_haversine_m']:+.1f} m){verif}")
    if rapport["segments"] and rapport["longueur_totale_m"] is not None:
        print(f"Longueur totale : {rapport['longueur_totale_m']:.4f} m")
    for d in rapport["directs"]:
        print(f"Depuis {d['depart']} au cap {d['cap_deg']}° sur {d['distance_m']} m : {d['lat']:.9f}, {d['lon']:.9f} "
              f"({d['dms']}), cap final {d['cap_final_deg']:.6f}°")
    if "boite" in rapport:
        b = rapport["boite"]
        print(f"Boîte : sud {b['sud']}, nord {b['nord']}, ouest {b['ouest']}, est {b['est']}"
              + (" (traverse l'antiméridien)" if b["traverse_antimeridien"] else ""))
    for b in rapport.get("boites_rayon", []):
        print(f"Disque {b['rayon_m']} m autour de {b['centre']} : sud {b['sud']}, nord {b['nord']}, "
              f"ouest {b['ouest']}, est {b['est']}")


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : lit les points, calcule, publie."""
    args = construire_parseur().parse_args(argv)
    try:
        valider_arguments(args)
        lecture = lire_entrees(args.points, args.fichier, args.ordre, args.racine)
    except ErreurGeodesie as erreur:
        print(f"calculer_geodesie : {erreur}", file=sys.stderr)
        return erreur.code
    for rejet in lecture.rejets:
        print(f"calculer_geodesie : ligne rejetée {rejet}", file=sys.stderr)
    points = lecture.points
    if not points:
        print("calculer_geodesie : dénominateur nul — aucun point lisible, rien à examiner", file=sys.stderr)
        if args.json:
            print(json.dumps({"outil": "calculer_geodesie", "denominateur": 0, "examines": [],
                              "rejets": lecture.rejets}, ensure_ascii=False))
        return CODE_RIEN
    controleur = creer_controleur(args.moteur)
    try:
        rapport = calculer(args, points, controleur)
    except ErreurGeodesie as erreur:
        print(f"calculer_geodesie : {erreur}", file=sys.stderr)
        return erreur.code
    for alerte in rapport["alertes"]:
        print(f"calculer_geodesie : {alerte}", file=sys.stderr)
    if args.json:
        entete = {"outil": "calculer_geodesie", "moteur": controleur[0], "version_moteur": version_moteur(controleur[0]),
                  "ellipsoide": {"nom": ELLIPSOIDE, "a": DEMI_GRAND_AXE, "f": APLATISSEMENT, "rayon_moyen_m": RAYON_MOYEN},
                  "denominateur": len(points), "examines": [p.entree for p in points[:EXAMINES_MAX]],
                  "examines_tronques": len(points) > EXAMINES_MAX, "rejets": lecture.rejets}
        print(json.dumps({**entete, **rapport, "contrat": extraire_contrat(__doc__ or "")}, ensure_ascii=False, indent=2))
    else:
        afficher_humain(rapport)
    return CODE_DEFAUT if rapport["alertes"] or lecture.rejets else CODE_OK


if __name__ == "__main__":
    raise SystemExit(main())
