r"""Une photo partagée peut livrer l'adresse de celui qui l'a prise : un JPEG
porteur d'un bloc GPS EXIF ne montre rien à l'écran. L'interpréteur de
référence n'a pas Pillow (« import PIL » : ModuleNotFoundError) ; cet outil lit
les en-têtes seuls. Mesuré sur 11 images témoins écrites par Pillow 12.3.0
(PNG, APNG, JPEG, GIF, WebP, BMP, TIFF ; dont un JPEG 4000×3000 portant
48°51'29.6" N, 2°17'40.2" E, relu 48.858222, 2.2945) : dimensions, nombre
d'images, orientation et présence GPS identiques à Pillow (0 écart).

QUESTION
    Quelles sont les dimensions et métadonnées de cette image, et
    révèle-t-elle une position GPS (ou, pour un SVG, du contenu actif) ?
MESURE
    Lecture des en-têtes seulement, par format reconnu à ses octets :
    PNG (IHDR, pHYs, textes tEXt, zTXt, iTXt, eXIf, images APNG), JPEG
    (segments jusqu'à SOS : SOFn, JFIF, EXIF via TIFF, XMP, commentaires),
    GIF (écran logique, images comptées en sautant les données, boucle,
    commentaires), WebP (VP8, VP8L, VP8X, ANMF, EXIF), BMP, TIFF (IFD
    chaînés), SVG (racine, viewBox, unités, script et gestionnaires
    d'événements via expat). EXIF : appareil, logiciel, dates, orientation
    (dimensions affichées), numéro de série, bloc GPS converti en degrés
    décimaux. Accepte aussi une URI data:image/… en base64. Avec Pillow,
    dimensions, nombre d'images, orientation et présence GPS sont confrontés.
HYPOTHÈSES
    Le format est celui que disent les octets, pas l'extension ; une
    position est « révélée » si latitude et longitude EXIF (ou XMP) sont
    présentes, valides et non nulles toutes deux.
LIMITES
    HEIF, AVIF, ICO et formats bruts autres que TIFF reconnus mais non
    analysés. Une position écrite ailleurs que dans EXIF ou XMP (texte
    libre, nom de fichier) n'est pas vue. BigTIFF non lu. Les pixels ne
    sont jamais décodés : une image corrompue après ses en-têtes passe.
CONTRE-EXEMPLES
    Un PNG portant « GPS 48.8584 N 2.2945 E » dans un chunk tEXt Comment :
    l'outil affiche le texte mais ne déclare aucune position (constaté sur
    le témoin texte_gps.png de la session).
INVOCATION
    {outil} 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7' --json
DOMAINE
    Contrôle avant publication ou partage (vie privée), vérification de
    livrables graphiques (dimensions, orientation), revue de SVG reçus.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import io
import json
import struct
import sys
import zlib
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Callable, NamedTuple
from urllib.parse import unquote_to_bytes
from xml.parsers import expat

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from PIL import Image as ImagePillow
    import PIL as module_pillow
except ImportError:
    ImagePillow = None
    module_pillow = None

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
FORMAT_PNG = "PNG"
FORMAT_JPEG = "JPEG"
FORMAT_GIF = "GIF"
FORMAT_BMP = "BMP"
FORMAT_TIFF = "TIFF"
FORMAT_SVG = "SVG"
FORMAT_WEBP = "WebP"
FORMAT_HEIF = "HEIF"
FORMAT_AVIF = "AVIF"
FORMAT_ICO = "ICO"
FORMAT_BIGTIFF = "BigTIFF"
BLOC_IHDR = "IHDR"
BLOC_EXIF = "EXIF"
BLOC_XMP = "XMP"
BLOC_APNG = "APNG"
BLOC_SOS = "SOS"
BLOC_SOF = "SOFn"
BLOC_JFIF = "JFIF"
BLOC_ANMF = "ANMF"
BLOC_IFD = "IFD"
BLOC_GPS = "GPS"
BLOC_VP8 = "VP8"
BLOC_VP8L = "VP8L"
BLOC_VP8X = "VP8X"

LIMITE_EXAMINES = 50
LIMITE_FICHIERS = 10_000
LIMITE_BLOC = 1 << 20
LIMITE_TEXTE = 2000
LIMITE_SVG = 20 * (1 << 20)
LIMITE_DATA_URI = 20 * (1 << 20)
LIMITE_ENTREES_IFD = 1000
EXTENSIONS = (".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".gif", ".webp", ".bmp", ".dib", ".tif",
              ".tiff", ".svg", ".svgz", ".heic", ".heif", ".avif", ".ico", ".dng")
TAILLES_TIFF = MappingProxyType({1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8})
ETIQUETTES_IFD0 = MappingProxyType({0x010F: "fabricant", 0x0110: "modele", 0x0112: "orientation", 0x0131: "logiciel",
                   0x0132: "date_modification", 0x013B: "auteur", 0x8298: "copyright",
                   0x0100: "largeur", 0x0101: "hauteur", 0x0102: "bits", 0x0103: "compression",
                   0x010E: "description"})
ETIQUETTES_EXIF = MappingProxyType({0x9003: "date_prise", 0x9004: "date_numerisation", 0xA002: "largeur_exif",
                   0xA003: "hauteur_exif", 0xA431: "numero_serie", 0xA434: "objectif",
                   0xA420: "identifiant_image", 0x927C: "note_fabricant", 0x9286: "commentaire"})
POINTEUR_EXIF, POINTEUR_GPS = 0x8769, 0x8825
UNITES_SVG = MappingProxyType({"": 1.0, "px": 1.0, "pt": 96 / 72, "pc": 16.0, "mm": 96 / 25.4,
                               "cm": 96 / 2.54, "in": 96.0})
COULEURS_PNG = MappingProxyType({0: "niveaux de gris", 2: "RVB", 3: "palette", 4: "gris + alpha", 6: "RVBA"})


class ErreurFormat(Exception):
    """Fichier non reconnu ou illisible."""


class Source(NamedTuple):
    """Une image à examiner : nom et ouverture de son flux."""
    nom: str
    ouvrir: Callable[[], BinaryIO]
    chemin: Path | None


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


# ------------------------------------------------------------------ outils --

def lire(flux: BinaryIO, n: int) -> bytes:
    """Exactement n octets, sinon ErreurFormat (fichier tronqué)."""
    octets = flux.read(n)
    if len(octets) < n:
        raise ErreurFormat("fichier tronqué dans les en-têtes")
    return octets


def texte_propre(octets: bytes, codage: str = "utf-8") -> str:
    """Texte borné, sans NUL final."""
    return octets.rstrip(b"\x00").decode(codage, errors="replace")[:LIMITE_TEXTE]


# -------------------------------------------------------------- TIFF/EXIF --

class LecteurTiff:
    """Lecture d'IFD TIFF dans un flux (fichier TIFF ou bloc EXIF)."""

    def __init__(self, flux: BinaryIO, base: int) -> None:
        self.flux, self.base = flux, base
        flux.seek(base)
        tete = lire(flux, 8)
        if tete[:2] not in (b"II", b"MM"):
            raise ErreurFormat("en-tête TIFF invalide")
        self.ordre = "<" if tete[:2] == b"II" else ">"
        version, self.premier = struct.unpack(self.ordre + "HI", tete[2:8])
        if version == 43:
            raise ErreurFormat(f"{FORMAT_BIGTIFF} non pris en charge")
        if version != 42:
            raise ErreurFormat("signature TIFF invalide")

    def entrees(self, decalage: int) -> tuple[dict[int, Any], int]:
        """Entrées d'un IFD (étiquette → valeur) et décalage de l'IFD suivant."""
        self.flux.seek(self.base + decalage)
        nombre = struct.unpack(self.ordre + "H", lire(self.flux, 2))[0]
        brutes = lire(self.flux, 12 * min(nombre, LIMITE_ENTREES_IFD))
        suivant_brut = self.flux.read(4)
        suivant = struct.unpack(self.ordre + "I", suivant_brut)[0] if len(suivant_brut) == 4 else 0
        valeurs = {}
        for i in range(min(nombre, LIMITE_ENTREES_IFD)):
            etiquette, genre, compte = struct.unpack(self.ordre + "HHI", brutes[12 * i:12 * i + 8])
            valeurs[etiquette] = self.valeur(genre, compte, brutes[12 * i + 8:12 * i + 12])
        return valeurs, suivant

    def valeur(self, genre: int, compte: int, champ: bytes) -> Any:
        """Valeur d'une entrée, en ligne ou à son décalage (bornée)."""
        taille = TAILLES_TIFF.get(genre, 1) * compte
        if taille > LIMITE_BLOC:
            return None
        if taille <= 4:
            octets = champ[:taille]
        else:
            position = self.flux.tell()
            self.flux.seek(self.base + struct.unpack(self.ordre + "I", champ)[0])
            octets = self.flux.read(taille)
            self.flux.seek(position)
        return self.decoder(genre, compte, octets)

    def decoder(self, genre: int, compte: int, octets: bytes) -> Any:
        """Octets → valeur Python selon le type TIFF."""
        if genre == 2:
            return texte_propre(octets, "latin-1").strip()
        if genre in (5, 10):
            code = "I" if genre == 5 else "i"
            paires = struct.unpack(self.ordre + code * (2 * (len(octets) // 8)), octets[:len(octets) // 8 * 8])
            nombres = [n / d if d else None for n, d in zip(paires[0::2], paires[1::2])]
            return nombres if compte > 1 else (nombres[0] if nombres else None)
        codes = {1: "B", 3: "H", 4: "I", 6: "b", 8: "h", 9: "i", 11: "f", 12: "d"}
        if genre in codes:
            n = len(octets) // TAILLES_TIFF[genre]
            valeurs = struct.unpack(self.ordre + codes[genre] * n, octets[:n * TAILLES_TIFF[genre]])
            return valeurs[0] if n == 1 else list(valeurs)
        return f"({len(octets)} octets)"

    def sous_ifd(self, ifd: dict[int, Any], pointeur: int) -> dict[int, Any]:
        """IFD désigné par un pointeur (EXIF, GPS), ou {}."""
        decalage = ifd.get(pointeur)
        if not isinstance(decalage, int) or decalage <= 0:
            return {}
        try:
            return self.entrees(decalage)[0]
        except (ErreurFormat, struct.error):
            return {}


def degres(valeur: Any, reference: Any) -> float | None:
    """[degrés, minutes, secondes] + N/S/E/W → degrés décimaux."""
    if not isinstance(valeur, list) or len(valeur) != 3 or any(v is None for v in valeur):
        return None
    signe = -1 if str(reference).strip().upper()[:1] in ("S", "W") else 1
    return round(signe * (valeur[0] + valeur[1] / 60 + valeur[2] / 3600), 6)


def lire_gps(gps: dict[int, Any]) -> dict[str, Any] | None:
    """Bloc GPS EXIF → latitude, longitude, altitude (None si absent)."""
    if not gps:
        return None
    latitude, longitude = degres(gps.get(2), gps.get(1)), degres(gps.get(4), gps.get(3))
    altitude = gps.get(6)
    if isinstance(altitude, (int, float)) and gps.get(5) in (1, b"\x01"):
        altitude = -altitude
    return {"latitude": latitude, "longitude": longitude,
            "altitude_m": round(altitude, 1) if isinstance(altitude, (int, float)) else None,
            "date": gps.get(0x1D), "source": BLOC_EXIF}


def lire_exif(flux: BinaryIO, base: int) -> dict[str, Any]:
    """IFD0, IFD EXIF et GPS d'un bloc TIFF commençant à `base`."""
    lecteur = LecteurTiff(flux, base)
    ifd0, _ = lecteur.entrees(lecteur.premier)
    exif = lecteur.sous_ifd(ifd0, POINTEUR_EXIF)
    champs = {nom: ifd0[e] for e, nom in ETIQUETTES_IFD0.items() if e in ifd0}
    champs.update({nom: exif[e] for e, nom in ETIQUETTES_EXIF.items() if e in exif})
    if "note_fabricant" in champs:
        champs["note_fabricant"] = "présente"
    return {"champs": champs, "gps": lire_gps(lecteur.sous_ifd(ifd0, POINTEUR_GPS))}


def exif_depuis_octets(octets: bytes) -> dict[str, Any]:
    """Bloc EXIF (avec ou sans préfixe « Exif\\0\\0 ») → champs et GPS."""
    debut = 6 if octets.startswith(b"Exif\x00\x00") else 0
    try:
        return lire_exif(io.BytesIO(octets), debut)
    except (ErreurFormat, struct.error) as exc:
        return {"champs": {}, "gps": None, "erreur": str(exc)}


# -------------------------------------------------------------------- PNG --

def lire_bloc_texte_png(genre: bytes, donnees: bytes) -> tuple[str, str]:
    """tEXt, zTXt ou iTXt → (mot-clé, texte), décompression bornée."""
    cle, _, reste = donnees.partition(b"\x00")
    if genre == b"tEXt":
        return texte_propre(cle, "latin-1"), texte_propre(reste, "latin-1")
    if genre == b"zTXt":
        return texte_propre(cle, "latin-1"), texte_propre(decompresser(reste[1:]), "latin-1")
    compresse = reste[:1] == b"\x01"
    _, _, apres = reste[2:].partition(b"\x00")
    _, _, texte = apres.partition(b"\x00")
    return texte_propre(cle, "latin-1"), texte_propre(decompresser(texte) if compresse else texte)


def decompresser(octets: bytes) -> bytes:
    """zlib borné à LIMITE_TEXTE × 4 octets (pas de bombe de décompression)."""
    try:
        return zlib.decompressobj().decompress(octets, LIMITE_TEXTE * 4)
    except zlib.error:
        return b"(texte compresse illisible)"


def analyser_png(flux: BinaryIO) -> dict[str, Any]:
    """Morceaux PNG jusqu'à IEND ; IDAT sautés sans lecture."""
    flux.seek(8)
    fiche: dict[str, Any] = {"format": FORMAT_PNG, "textes": {}, "images": 1, "crc_faux": 0}
    for _ in range(100_000):
        tete = flux.read(8)
        if len(tete) < 8:
            fiche["notes"] = ["fin de fichier avant IEND"]
            break
        longueur, genre = struct.unpack(">I4s", tete)
        if genre == b"IDAT" or longueur > LIMITE_BLOC:
            flux.seek(longueur + 4, io.SEEK_CUR)
            continue
        donnees, crc = flux.read(longueur), flux.read(4)
        fiche["crc_faux"] += len(crc) == 4 and zlib.crc32(genre + donnees) != struct.unpack(">I", crc)[0]
        traiter_bloc_png(fiche, genre, donnees)
        if genre == b"IEND":
            break
    if "largeur" not in fiche:
        raise ErreurFormat(f"PNG sans {BLOC_IHDR}")
    return fiche


def traiter_bloc_png(fiche: dict[str, Any], genre: bytes, donnees: bytes) -> None:
    """Un morceau PNG utile."""
    if genre == b"IHDR" and len(donnees) >= 13:
        largeur, hauteur, bits, couleur, _, _, entrelace = struct.unpack(">IIBBBBB", donnees[:13])
        fiche.update({"largeur": largeur, "hauteur": hauteur, "profondeur_bits": bits,
                      "mode_couleur": COULEURS_PNG.get(couleur, str(couleur)), "entrelace": bool(entrelace)})
    elif genre in (b"tEXt", b"zTXt", b"iTXt"):
        cle, texte = lire_bloc_texte_png(genre, donnees)
        fiche["textes"][cle] = texte
    elif genre == b"eXIf":
        fiche["exif"] = exif_depuis_octets(donnees)
    elif genre == b"acTL" and len(donnees) >= 4:
        fiche["images"] = struct.unpack(">I", donnees[:4])[0]
        fiche["animation"] = BLOC_APNG
    elif genre == b"pHYs" and len(donnees) >= 9 and donnees[8] == 1:
        x, y = struct.unpack(">II", donnees[:8])
        fiche["resolution_dpi"] = [round(x * 0.0254), round(y * 0.0254)]


# ------------------------------------------------------------------- JPEG --

def segments_jpeg(flux: BinaryIO) -> list[tuple[int, bytes]]:
    """Segments (marqueur, données bornées) de SOI jusqu'à SOS."""
    flux.seek(2)
    segments = []
    for _ in range(10_000):
        octet = flux.read(1)
        if not octet:
            break
        if octet != b"\xff":
            raise ErreurFormat(f"segment JPEG invalide avant {BLOC_SOS}")
        marqueur = lire(flux, 1)[0]
        while marqueur == 0xFF:
            marqueur = lire(flux, 1)[0]
        if marqueur in (0x01, 0xD8) or 0xD0 <= marqueur <= 0xD7:
            continue
        if marqueur in (0xD9, 0xDA):
            segments.append((marqueur, b""))
            break
        longueur = struct.unpack(">H", lire(flux, 2))[0] - 2
        segments.append((marqueur, flux.read(min(max(longueur, 0), LIMITE_BLOC))))
        if longueur > LIMITE_BLOC:
            flux.seek(longueur - LIMITE_BLOC, io.SEEK_CUR)
    return segments


def analyser_jpeg(flux: BinaryIO) -> dict[str, Any]:
    """SOFn, JFIF, EXIF, XMP, commentaires."""
    fiche: dict[str, Any] = {"format": FORMAT_JPEG, "textes": {}, "images": 1}
    for marqueur, donnees in segments_jpeg(flux):
        if marqueur in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if len(donnees) >= 6 and "largeur" not in fiche:
                bits, hauteur, largeur, composantes = struct.unpack(">BHHB", donnees[:6])
                fiche.update({"largeur": largeur, "hauteur": hauteur, "profondeur_bits": bits,
                              "composantes": composantes, "progressif": marqueur in (0xC2, 0xC6, 0xCA, 0xCE)})
        elif marqueur == 0xE0 and donnees.startswith(b"JFIF\x00") and len(donnees) >= 12:
            unite, x, y = donnees[7], *struct.unpack(">HH", donnees[8:12])
            if unite in (1, 2):
                fiche["resolution_dpi"] = [x, y] if unite == 1 else [round(x * 2.54), round(y * 2.54)]
        elif marqueur == 0xE1 and donnees.startswith(b"Exif\x00\x00"):
            fiche["exif"] = exif_depuis_octets(donnees)
        elif marqueur == 0xE1 and donnees.startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
            fiche["xmp"] = lire_xmp(donnees)
        elif marqueur == 0xFE:
            fiche["textes"]["commentaire"] = texte_propre(donnees)
    if "largeur" not in fiche:
        raise ErreurFormat(f"JPEG sans segment {BLOC_SOF} avant {BLOC_SOS} (fichier tronqué ou corrompu)")
    return fiche


def lire_xmp(octets: bytes) -> dict[str, Any]:
    """Indices XMP : présence de coordonnées GPS (exif:GPSLatitude)."""
    texte = octets.decode("utf-8", errors="replace")
    return {"taille": len(octets), "gps_xmp": "GPSLatitude" in texte and "GPSLongitude" in texte}


# -------------------------------------------------------------------- GIF --

def sauter_sous_blocs(flux: BinaryIO, garder: bool = False) -> bytes:
    """Saute (ou lit, borné) une suite de sous-blocs GIF."""
    gardes = bytearray()
    while True:
        taille = lire(flux, 1)[0]
        if taille == 0:
            return bytes(gardes)
        if garder and len(gardes) < LIMITE_TEXTE:
            gardes += lire(flux, taille)
        else:
            flux.seek(taille, io.SEEK_CUR)


def analyser_gif(flux: BinaryIO) -> dict[str, Any]:
    """Écran logique, images (comptées), boucle, commentaires."""
    tete = lire(flux, 13)
    largeur, hauteur, drapeaux = struct.unpack("<HHB", tete[6:11])
    fiche: dict[str, Any] = {"format": FORMAT_GIF, "version": tete[3:6].decode("ascii", "replace"),
                             "largeur": largeur, "hauteur": hauteur, "images": 0, "textes": {},
                             "profondeur_bits": (drapeaux & 7) + 1}
    if drapeaux & 0x80:
        flux.seek(3 * (2 << (drapeaux & 7)), io.SEEK_CUR)
    for _ in range(1_000_000):
        bloc = flux.read(1)
        if not bloc or bloc == b";":
            fiche["termine"] = bloc == b";"
            break
        if bloc == b",":
            descripteur = lire(flux, 9)
            if descripteur[8] & 0x80:
                flux.seek(3 * (2 << (descripteur[8] & 7)), io.SEEK_CUR)
            lire(flux, 1)
            sauter_sous_blocs(flux)
            fiche["images"] += 1
        elif bloc == b"!":
            lire_extension_gif(flux, fiche)
        else:
            raise ErreurFormat(f"bloc GIF inconnu {bloc!r}")
    fiche["animation"] = fiche["images"] > 1
    return fiche


def lire_extension_gif(flux: BinaryIO, fiche: dict[str, Any]) -> None:
    """Extension GIF : commentaire, application (boucle NETSCAPE), autres."""
    etiquette = lire(flux, 1)[0]
    contenu = sauter_sous_blocs(flux, garder=etiquette in (0xFE, 0xFF))
    if etiquette == 0xFE:
        fiche["textes"]["commentaire"] = texte_propre(contenu, "latin-1")
    elif etiquette == 0xFF and contenu.startswith(b"NETSCAPE2.0") and len(contenu) >= 14:
        fiche["boucles"] = struct.unpack("<H", contenu[12:14])[0] or "infini"


# ---------------------------------------------------------- WebP, BMP, TIFF --

def analyser_webp(flux: BinaryIO) -> dict[str, Any]:
    """Morceaux RIFF/WEBP : VP8, VP8L, VP8X, ANMF, EXIF, XMP."""
    flux.seek(12)
    fiche: dict[str, Any] = {"format": FORMAT_WEBP, "images": 0, "textes": {}}
    for _ in range(100_000):
        tete = flux.read(8)
        if len(tete) < 8:
            break
        genre, taille = tete[:4], struct.unpack("<I", tete[4:])[0]
        position = flux.tell()
        donnees = flux.read(min(taille, LIMITE_BLOC)) if genre in (b"VP8 ", b"VP8L", b"VP8X", b"EXIF") else b""
        traiter_bloc_webp(fiche, genre, donnees)
        flux.seek(position + taille + (taille & 1))
    if "largeur" not in fiche:
        raise ErreurFormat(f"WebP sans {BLOC_VP8}, {BLOC_VP8L} ni {BLOC_VP8X}")
    fiche["images"] = max(fiche["images"], 1)
    return fiche


def traiter_bloc_webp(fiche: dict[str, Any], genre: bytes, donnees: bytes) -> None:
    """Un morceau WebP utile."""
    if genre == b"VP8X" and len(donnees) >= 10:
        fiche.update({"largeur": int.from_bytes(donnees[4:7], "little") + 1,
                      "hauteur": int.from_bytes(donnees[7:10], "little") + 1,
                      "animation": bool(donnees[0] & 0x02), "alpha": bool(donnees[0] & 0x10)})
    elif genre == b"VP8 " and len(donnees) >= 10 and donnees[3:6] == b"\x9d\x01\x2a" and "largeur" not in fiche:
        largeur, hauteur = struct.unpack("<HH", donnees[6:10])
        fiche.update({"largeur": largeur & 0x3FFF, "hauteur": hauteur & 0x3FFF, "codage": "avec pertes"})
    elif genre == b"VP8L" and len(donnees) >= 5 and donnees[0] == 0x2F and "largeur" not in fiche:
        bits = int.from_bytes(donnees[1:5], "little")
        fiche.update({"largeur": (bits & 0x3FFF) + 1, "hauteur": ((bits >> 14) & 0x3FFF) + 1,
                      "codage": "sans perte", "alpha": bool((bits >> 28) & 1)})
    elif genre == b"ANMF":
        fiche["images"] += 1
    elif genre == b"EXIF":
        fiche["exif"] = exif_depuis_octets(donnees)
    elif genre == b"XMP ":
        fiche["textes"]["xmp"] = "présent"


def analyser_bmp(flux: BinaryIO) -> dict[str, Any]:
    """En-tête BMP (BITMAPCOREHEADER ou BITMAPINFOHEADER et suivants)."""
    tete = lire(flux, 30)
    taille_dib = struct.unpack("<I", tete[14:18])[0]
    if taille_dib == 12:
        largeur, hauteur, _, bits = struct.unpack("<HHHH", tete[18:26])
        compression = 0
    elif taille_dib >= 40:
        tete += lire(flux, 4)
        largeur, hauteur, _, bits, compression = struct.unpack("<iiHHI", tete[18:34])
    else:
        raise ErreurFormat(f"en-tête DIB de {taille_dib} octets inconnu")
    return {"format": FORMAT_BMP, "largeur": abs(largeur), "hauteur": abs(hauteur), "profondeur_bits": bits,
            "compression": compression, "de_haut_en_bas": hauteur < 0, "images": 1, "textes": {}}


def analyser_tiff(flux: BinaryIO) -> dict[str, Any]:
    """IFD chaînés (pages), dimensions de la première, EXIF et GPS."""
    lecteur = LecteurTiff(flux, 0)
    ifd0, suivant = lecteur.entrees(lecteur.premier)
    pages, vus = 1, {lecteur.premier}
    while suivant and suivant not in vus and pages < 10_000:
        vus.add(suivant)
        try:
            _, suivant = lecteur.entrees(suivant)
        except (ErreurFormat, struct.error):
            break
        pages += 1
    exif = lire_exif(flux, 0)
    largeur, hauteur = ifd0.get(0x0100), ifd0.get(0x0101)
    if not isinstance(largeur, int) or not isinstance(hauteur, int):
        raise ErreurFormat(f"{FORMAT_TIFF} sans largeur ni hauteur dans le premier {BLOC_IFD}")
    bits = ifd0.get(0x0102)
    return {"format": FORMAT_TIFF, "largeur": largeur, "hauteur": hauteur, "images": pages,
            "profondeur_bits": bits[0] if isinstance(bits, list) else bits, "exif": exif, "textes": {}}


# -------------------------------------------------------------------- SVG --

class LecteurSvg:
    """Parcours expat d'un SVG : racine, dimensions, contenu actif."""

    def __init__(self) -> None:
        self.racine: tuple[str, dict[str, str]] | None = None
        self.elements = 0
        self.actifs: list[str] = []
        self.externes: list[str] = []

    def debut(self, nom: str, attributs: dict[str, str]) -> None:
        """Élément ouvrant."""
        local = nom.rsplit(" ", 1)[-1]
        self.elements += 1
        if self.racine is None:
            self.racine = (local, attributs)
        if local in ("script", "foreignObject", "handler"):
            self.actifs.append(f"élément <{local}>")
        for cle, valeur in attributs.items():
            attribut = cle.rsplit(" ", 1)[-1].lower()
            if attribut.startswith("on"):
                self.actifs.append(f"gestionnaire {attribut} sur <{local}>")
            if attribut == "href" and valeur.strip().lower().startswith("javascript:"):
                self.actifs.append(f"lien javascript: sur <{local}>")
            elif attribut in ("href", "src") and valeur.strip().lower().startswith(("http:", "https:", "//")):
                self.externes.append(valeur.strip()[:200])

    @staticmethod
    def refuser_entite(*_: Any) -> None:
        """Toute déclaration d'entité est refusée (bombe XML, entité externe)."""
        raise ErreurFormat("SVG avec déclaration d'entité : refusé")


def longueur_svg(texte: str | None) -> float | None:
    """« 210mm » → pixels à 96 ppp ; None pour %, em, ex ou absent."""
    if not texte:
        return None
    propre = texte.strip().lower()
    nombre = propre.rstrip("abcdefghijklmnopqrstuvwxyz%")
    unite = propre[len(nombre):]
    try:
        return round(float(nombre) * UNITES_SVG[unite], 2) if unite in UNITES_SVG else None
    except ValueError:
        return None


def analyser_svg(flux: BinaryIO) -> dict[str, Any]:
    """SVG ou SVGZ : dimensions, viewBox, contenu actif, références externes."""
    octets = flux.read(LIMITE_SVG + 1)
    if octets[:2] == b"\x1f\x8b":
        try:
            octets = gzip.GzipFile(fileobj=io.BytesIO(octets)).read(LIMITE_SVG + 1)
        except (OSError, EOFError, zlib.error) as exc:
            raise ErreurFormat(f"SVGZ illisible : {exc}") from exc
    if len(octets) > LIMITE_SVG:
        raise ErreurFormat(f"SVG de plus de {LIMITE_SVG >> 20} Mio refusé")
    lecteur = LecteurSvg()
    analyseur = expat.ParserCreate(namespace_separator=" ")
    analyseur.StartElementHandler = lecteur.debut
    analyseur.EntityDeclHandler = lecteur.refuser_entite
    try:
        analyseur.Parse(octets, True)
    except expat.ExpatError as exc:
        raise ErreurFormat(f"SVG mal formé : {exc}") from exc
    if lecteur.racine is None or lecteur.racine[0] != "svg":
        raise ErreurFormat("racine XML autre que <svg>")
    return fiche_svg(lecteur)


def fiche_svg(lecteur: LecteurSvg) -> dict[str, Any]:
    """Fiche d'un SVG lu."""
    attributs = {k.rsplit(" ", 1)[-1]: v for k, v in lecteur.racine[1].items()}
    boite = attributs.get("viewBox", "").replace(",", " ").split()
    vue = [float(x) for x in boite] if len(boite) == 4 and all(x.replace(".", "", 1).lstrip("-").isdigit()
                                                                for x in boite) else None
    largeur, hauteur = longueur_svg(attributs.get("width")), longueur_svg(attributs.get("height"))
    if largeur is None and vue:
        largeur, hauteur = vue[2], vue[3]
    return {"format": FORMAT_SVG, "largeur": largeur, "hauteur": hauteur, "images": 1, "textes": {},
            "attributs_dimensions": {k: attributs.get(k) for k in ("width", "height", "viewBox")},
            "elements": lecteur.elements, "contenu_actif": lecteur.actifs[:50],
            "references_externes": lecteur.externes[:50]}


# ---------------------------------------------------------------- aiguillage --

def reconnaitre(tete: bytes) -> str | None:
    """Format d'après les octets de tête."""
    signatures = ((b"\x89PNG\r\n\x1a\n", FORMAT_PNG), (b"\xff\xd8\xff", FORMAT_JPEG), (b"GIF87a", FORMAT_GIF),
                  (b"GIF89a", FORMAT_GIF), (b"II*\x00", FORMAT_TIFF), (b"MM\x00*", FORMAT_TIFF),
                  (b"II+\x00", FORMAT_BIGTIFF), (b"MM\x00+", FORMAT_BIGTIFF), (b"\x00\x00\x01\x00", FORMAT_ICO))
    for signature, nature in signatures:
        if tete.startswith(signature):
            return nature
    if tete[:4] == b"RIFF" and tete[8:12] == b"WEBP":
        return FORMAT_WEBP
    if tete[:2] == b"BM" and len(tete) >= 18:
        return FORMAT_BMP
    if tete[4:8] == b"ftyp":
        return FORMAT_AVIF if tete[8:12] in (b"avif", b"avis") else FORMAT_HEIF
    debut = tete.lstrip(b"\xef\xbb\xbf \t\r\n")
    if debut[:2] == b"\x1f\x8b" or (debut[:1] == b"<" and b"<svg" in tete):
        return FORMAT_SVG
    return None


ANALYSEURS = MappingProxyType({
    FORMAT_PNG: analyser_png, FORMAT_JPEG: analyser_jpeg, FORMAT_GIF: analyser_gif,
    FORMAT_WEBP: analyser_webp, FORMAT_BMP: analyser_bmp, FORMAT_TIFF: analyser_tiff,
    FORMAT_SVG: analyser_svg,
})


def analyser_flux(flux: BinaryIO) -> dict[str, Any]:
    """Fiche d'une image d'après ses octets."""
    tete = flux.read(4096)
    nature = reconnaitre(tete)
    flux.seek(0)
    if nature is None:
        raise ErreurFormat("format d'image non reconnu")
    if nature not in ANALYSEURS:
        return {"format": nature, "largeur": None, "hauteur": None, "images": None, "textes": {},
                "notes": [f"format {nature} reconnu mais non analysé : métadonnées et GPS non vérifiés"]}
    try:
        return ANALYSEURS[nature](flux)
    except (struct.error, IndexError, ValueError, OverflowError) as exc:
        raise ErreurFormat(f"{nature} illisible : {exc}") from exc


def completer(fiche: dict[str, Any]) -> dict[str, Any]:
    """Orientation, dimensions affichées, GPS, risques."""
    exif = fiche.pop("exif", None) or {"champs": {}, "gps": None}
    champs = exif["champs"]
    orientation = champs.get("orientation")
    fiche["metadonnees"] = {k: v for k, v in champs.items() if k not in ("largeur", "hauteur", "bits")}
    fiche["orientation"] = orientation
    tourne = orientation in (5, 6, 7, 8)
    fiche["largeur_affichee"] = fiche.get("hauteur") if tourne else fiche.get("largeur")
    fiche["hauteur_affichee"] = fiche.get("largeur") if tourne else fiche.get("hauteur")
    gps = exif["gps"]
    if gps is None and fiche.get("xmp", {}).get("gps_xmp"):
        gps = {"latitude": None, "longitude": None, "source": BLOC_XMP, "xmp_seul": True}
    fiche["gps"] = gps
    fiche["risques"] = risques(fiche)
    if "erreur" in exif:
        fiche.setdefault("notes", []).append(f"bloc {BLOC_EXIF} illisible : {exif['erreur']}")
    return fiche


def risques(fiche: dict[str, Any]) -> list[str]:
    """Position révélée, contenu actif SVG, numéro de série."""
    trouves = []
    gps = fiche["gps"]
    if gps and gps.get("xmp_seul"):
        trouves.append(f"position {BLOC_GPS} déclarée dans {BLOC_XMP} (coordonnées non décodées)")
    elif gps and gps["latitude"] is not None and gps["longitude"] is not None:
        valide = abs(gps["latitude"]) <= 90 and abs(gps["longitude"]) <= 180
        if valide and (gps["latitude"], gps["longitude"]) != (0.0, 0.0):
            trouves.append(f"position {BLOC_GPS} révélée : {gps['latitude']}, {gps['longitude']}")
    if fiche.get("contenu_actif"):
        trouves.append(f"SVG actif : {', '.join(fiche['contenu_actif'][:5])}")
    return trouves


# ------------------------------------------------------------------ Pillow --

def controle_pillow(source: Source, fiche: dict[str, Any]) -> dict[str, Any] | None:
    """Dimensions, images, orientation et présence GPS selon Pillow."""
    if ImagePillow is None or fiche["format"] in (FORMAT_SVG, FORMAT_HEIF, FORMAT_AVIF):
        return None
    try:
        with source.ouvrir() as flux, ImagePillow.open(flux) as image:
            exif = image.getexif()
            gps = exif.get_ifd(POINTEUR_GPS)
            leur = {"largeur": image.size[0], "hauteur": image.size[1],
                    "images": getattr(image, "n_frames", 1), "orientation": exif.get(0x0112),
                    "gps": bool(gps.get(2) and gps.get(4))}
    except (OSError, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, EOFError, SyntaxError,
            struct.error, ImagePillow.DecompressionBombError) as exc:
        refus = f"Pillow refuse : {type(exc).__name__} : {str(exc)[:160]}"
        return {"bibliotheque": f"Pillow {module_pillow.__version__}", "erreur": refus, "ecarts": [refus]}
    miens = {"largeur": fiche.get("largeur"), "hauteur": fiche.get("hauteur"), "images": fiche.get("images"),
             "orientation": fiche.get("orientation"),
             "gps": bool(fiche["gps"] and fiche["gps"].get("latitude") is not None)}
    ecarts = [f"{cle} : stdlib {miens[cle]} / Pillow {val}" for cle, val in leur.items() if miens[cle] != val]
    return {"bibliotheque": f"Pillow {module_pillow.__version__}", "valeurs": leur, "ecarts": ecarts}


# ------------------------------------------------------------------ entrée --

def source_data_uri(texte: str) -> Source:
    """URI data:image/…;base64,… → source en mémoire."""
    entete, virgule, charge = texte.partition(",")
    if not virgule or len(charge) > LIMITE_DATA_URI * 2:
        raise ErreurFormat("URI data: mal formée ou trop longue")
    try:
        octets = base64.b64decode(charge, validate=False) if entete.endswith(";base64") \
            else unquote_to_bytes(charge)
    except (binascii.Error, ValueError) as exc:
        raise ErreurFormat(f"URI data: base64 invalide : {exc}") from exc
    return Source(f"{entete[:40]} ({len(octets)} octets)", lambda: io.BytesIO(octets), None)


def sources(chemins: list[str], racine: Path | None) -> list[Source]:
    """Fichiers donnés, images trouvées dans les dossiers, URI data:."""
    trouvees: list[Source] = []
    for brut in chemins:
        if brut.startswith("data:"):
            trouvees.append(source_data_uri(brut))
            continue
        chemin = Path(brut)
        if racine is not None and not chemin.is_absolute():
            chemin = racine / chemin
        if chemin.is_dir():
            fichiers = sorted(p for p in chemin.rglob("*") if p.suffix.lower() in EXTENSIONS and p.is_file())
            trouvees += [Source(str(p), ouvreur(p), p) for p in fichiers[:LIMITE_FICHIERS]]
        elif chemin.is_file():
            trouvees.append(Source(str(chemin), ouvreur(chemin), chemin))
        else:
            raise ErreurFormat(f"chemin introuvable : {chemin}")
    return trouvees


def ouvreur(chemin: Path) -> Callable[[], BinaryIO]:
    """Fonction d'ouverture binaire différée d'un fichier."""
    return lambda: chemin.open("rb")


def examiner(source: Source) -> dict[str, Any]:
    """Fiche complète d'une source (erreur rangée dans la fiche)."""
    try:
        with source.ouvrir() as flux:
            fiche = completer(analyser_flux(flux))
    except ErreurFormat as exc:
        return {"chemin": source.nom, "erreur": str(exc), "risques": []}
    except OSError as exc:
        return {"chemin": source.nom, "erreur": f"illisible : {exc.strerror or exc}", "risques": []}
    fiche = {"chemin": source.nom, **fiche}
    fiche["controle_croise"] = controle_pillow(source, fiche)
    return fiche


def analyser(args: argparse.Namespace) -> dict[str, Any]:
    """Cœur : toutes les sources."""
    fiches = [examiner(s) for s in sources(args.chemins, args.racine)]
    noms = [f["chemin"] for f in fiches]
    return {"denominateur": len(fiches), "examines": noms[:LIMITE_EXAMINES],
            "examines_tronques": len(noms) > LIMITE_EXAMINES,
            "moteur": f"stdlib+Pillow {module_pillow.__version__}" if ImagePillow is not None else "stdlib",
            "images_a_risque": sum(1 for f in fiches if f["risques"]), "fichiers": fiches}


# ------------------------------------------------------------------ sortie --

def imprimer_humain(res: dict[str, Any]) -> None:
    """Sortie lisible."""
    print(f"{res['denominateur']} image(s) examinée(s), {res['images_a_risque']} à risque (moteur : {res['moteur']})")
    for fiche in res["fichiers"]:
        if "erreur" in fiche:
            print(f"\n{fiche['chemin']} : ERREUR {fiche['erreur']}")
            continue
        print(f"\n{fiche['chemin']} : {fiche['format']} {fiche.get('largeur')}×{fiche.get('hauteur')}"
              f" (affichée {fiche.get('largeur_affichee')}×{fiche.get('hauteur_affichee')}), "
              f"{fiche.get('images')} image(s)")
        for cle, valeur in list(fiche.get("metadonnees", {}).items())[:12]:
            print(f"  {cle} : {valeur}")
        for cle, valeur in list(fiche.get("textes", {}).items())[:8]:
            print(f"  texte {cle} : {str(valeur)[:120]}")
        for message in fiche["risques"] + fiche.get("notes", []):
            print(f"  ⚠ {message}")
        controle = fiche.get("controle_croise")
        if controle and controle.get("ecarts"):
            print(f"  écarts {controle['bibliotheque']} : {controle['ecarts']}")


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Dimensions et métadonnées d'images (PNG, JPEG, GIF, WebP, BMP, TIFF, SVG) par "
                    "lecture des en-têtes ; signale une position GPS ou un SVG actif.",
        epilog="Exemples : inspecter_image.py photo.jpg --json\n           inspecter_image.py site/static/\n"
               "           inspecter_image.py 'data:image/png;base64,iVBOR…'\n"
               "Codes : 0 rien à signaler ; 1 position GPS, SVG actif, écart avec Pillow ou fichier "
               "illisible parmi d'autres ; 2 entrée invalide ; 3 aucune image à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("chemins", nargs="+", help="fichiers, dossiers (récursif) ou URI data:image/…")
    parseur.add_argument("--racine", type=Path, help="base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def code_sortie(res: dict[str, Any]) -> int:
    """2 si tout est illisible ; 1 si risque, écart ou erreur ; 0 sinon."""
    fiches = res["fichiers"]
    if all("erreur" in f for f in fiches):
        return 2
    ecarts = any((f.get("controle_croise") or {}).get("ecarts") for f in fiches)
    return 1 if res["images_a_risque"] or ecarts or any("erreur" in f for f in fiches) else 0


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    if ImagePillow is None:
        print("Pillow absente : lecture des en-têtes en stdlib seule, sans contrôle croisé", file=sys.stderr)
    try:
        res = analyser(args)
    except ErreurFormat as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
    if res["denominateur"] == 0:
        print("dénominateur nul : aucune image trouvée, rien à examiner", file=sys.stderr)
        return 3
    res["contrat"] = lire_contrat()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    else:
        imprimer_humain(res)
    for fiche in res["fichiers"]:
        if "erreur" in fiche:
            print(f"erreur : {fiche['chemin']} : {fiche['erreur']}", file=sys.stderr)
    return code_sortie(res)


if __name__ == "__main__":
    raise SystemExit(main())
