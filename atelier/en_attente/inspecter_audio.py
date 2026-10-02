r"""Un agent ne « voit » pas un fichier audio, et l'outil habituel pour mesurer
des niveaux en pure stdlib a disparu : `import audioop` lève
ModuleNotFoundError sous Python 3.14.7. Ici crête, RMS et écrêtage sont
calculés avec array et math.sumprod ; sur l'étalon intégré (sinus 1 kHz
d'amplitude 0,5), l'outil mesure -6,02 dBFS crête et -9,03 dBFS RMS,
valeurs théoriques -6,02 et -9,03 (`inspecter_audio.py --etalon --json`).

QUESTION
    Quelles sont les caractéristiques de ce fichier audio (format, canaux,
    fréquence, profondeur, durée), et est-il saturé ou silencieux ?
MESURE
    WAV : morceaux RIFF lus pas à pas, PCM décodé par le module wave
    (8, 16, 24, 32 bits), flottant IEEE (32, 64 bits) décodé directement ;
    échantillons lus par blocs ; crête et RMS en dBFS par canal, composante
    continue, échantillons à pleine échelle, séries d'au moins N
    échantillons consécutifs à pleine échelle (écrêtage), fenêtres de 50 ms
    sous le seuil de silence, silence de tête et de queue. MP3 : étiquette
    ID3, première trame vérifiée par la suivante, durée par l'en-tête Xing,
    Info ou VBRI, sinon estimée en débit constant. FLAC : STREAMINFO et
    commentaires. OGG Vorbis et Opus : en-tête d'identification,
    commentaires, dernière position de granule. Avec soundfile, les formats
    compressés sont décodés pour mesurer leurs niveaux ; avec mutagen, durée,
    fréquence et canaux sont confrontés.
HYPOTHÈSES
    Pleine échelle = plus grande valeur entière du format (1,0 en
    flottant) ; RMS sans correction +3 dB (un sinus plein donne -3,01 dBFS) ;
    un fichier est « silencieux » si sa crête reste sous le seuil (défaut
    -60 dBFS).
LIMITES
    Sans soundfile, pas de niveaux pour MP3, FLAC et OGG (en-têtes seuls).
    Un écrêtage analogique ou par limiteur sous 0 dBFS (plafond -0,1 dBFS)
    n'est pas vu. WAV RIFX, ADPCM et µ-law : en-têtes seuls ; RF64 lu sans
    le module wave. OGG chaînés ou multiplexés : premier flux seulement.
CONTRE-EXEMPLES
    Un sinus écrêté puis atténué de 1 dB (plafond à -1 dBFS, forme carrée
    conservée) n'a aucun échantillon à pleine échelle : l'outil le déclare
    non saturé (constaté sur le témoin ecrete_attenue.wav de la session).
INVOCATION
    {outil} --etalon --json
DOMAINE
    Contrôle de livrables audio (voix, podcasts, jeux de données) avant
    diffusion ou entraînement : format attendu, absence de saturation
    numérique et de piste muette.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import struct
import sys
import wave
from array import array
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, NamedTuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import mutagen
except ImportError:
    mutagen = None

try:
    import soundfile
except ImportError:
    soundfile = None

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
FORMAT_WAV = "WAV"
FORMAT_MP3 = "MP3"
FORMAT_FLAC = "FLAC"
FORMAT_OGG = "OGG"
FORMAT_RIFF = "RIFF"
FORMAT_RF64 = "RF64"
FORMAT_RIFX = "RIFX"
FORMAT_ADPCM = "ADPCM"
BLOC_STREAMINFO = "STREAMINFO"
ETIQUETTE_ID3 = "ID3"
ENTETE_VBRI = "VBRI"
NORME_FLOTTANT = "IEEE"
CODE_PCM = 1
CODE_FLOTTANT = 3
CODE_EXTENSIBLE = 0xFFFE

LIMITE_EXAMINES = 50
LIMITE_FICHIERS = 10_000
FENETRE_S = 0.05
FENETRES_PAR_BLOC = 200
EXTENSIONS = (".wav", ".wave", ".mp3", ".flac", ".ogg", ".oga", ".opus")
TYPE_32 = "i" if array("i").itemsize == 4 else "l"
TABLE_8_BITS = bytes((x - 128) & 0xFF for x in range(256))
DEBITS_MP3 = MappingProxyType({
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
})
FREQUENCES_MP3 = MappingProxyType({1: (44100, 48000, 32000), 2: (22050, 24000, 16000),
                                   25: (11025, 12000, 8000)})
CADRES_ID3 = MappingProxyType({"TIT2": "titre", "TT2": "titre", "TPE1": "artiste", "TP1": "artiste",
              "TALB": "album", "TAL": "album", "TDRC": "annee", "TYER": "annee", "TYE": "annee",
              "TCON": "genre", "TCO": "genre", "TRCK": "piste", "TRK": "piste"})


class ErreurFormat(Exception):
    """Fichier non reconnu ou illisible."""


class Codage(NamedTuple):
    """Représentation des échantillons ; « haut » = seuil de pleine échelle
    (valeur entière maximale, ou 1 - 2**-15 en flottant : à 1 pas 16 bits près)."""
    nom: str
    largeur: int
    flottant: bool
    pleine_echelle: float
    haut: float
    bas: float


class Options(NamedTuple):
    """Seuils de mesure."""
    seuil_silence_dbfs: float
    serie_min: int


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


# --------------------------------------------------------------- mesures --

def codage_pour(largeur: int, flottant: bool) -> Codage:
    """Codage d'échantillons pour une largeur (octets) et une nature."""
    if flottant:
        return Codage(f"flottant {NORME_FLOTTANT} {largeur * 8} bits", largeur, True, 1.0, 1.0 - 2 ** -15, -1.0)
    if largeur == 3:
        return Codage("PCM 24 bits", 3, False, 2.0 ** 31, (2 ** 23 - 1) * 256, -(2 ** 31))
    pleine = 2.0 ** (8 * largeur - 1)
    return Codage(f"PCM {largeur * 8} bits", largeur, False, pleine, pleine - 1, -pleine)


def vers_tableau(octets: bytes, codage: Codage) -> array:
    """Octets petit-boutistes → tableau d'échantillons signés."""
    if codage.largeur == 1:
        tableau = array("b", octets.translate(TABLE_8_BITS))
    elif codage.largeur == 3:
        etendu = bytearray(len(octets) // 3 * 4)
        etendu[1::4], etendu[2::4], etendu[3::4] = octets[0::3], octets[1::3], octets[2::3]
        tableau = array(TYPE_32, bytes(etendu))
    else:
        types = {(2, False): "h", (4, False): TYPE_32, (4, True): "f", (8, True): "d"}
        tableau = array(types[(codage.largeur, codage.flottant)], octets)
    if sys.byteorder == "big" and codage.largeur > 1:
        tableau.byteswap()
    return tableau


def dbfs(valeur: float, pleine: float) -> float | None:
    """Niveau en dBFS (None pour -∞)."""
    return round(20 * math.log10(valeur / pleine), 2) + 0.0 if valeur > 0 else None


def indices_extremes(canal: array, codage: Codage) -> list[int]:
    """Positions des échantillons à pleine échelle : ±maximum et minimum
    entiers (un convertisseur symétrique s'arrête à -maximum), |x| ≥ seuil en
    flottant."""
    if max(canal) < codage.haut and min(canal) > -codage.haut:
        return []
    if codage.flottant:
        return [i for i, x in enumerate(canal) if abs(x) >= codage.haut]
    indices = []
    for valeur in (int(codage.haut), -int(codage.haut), int(codage.bas)):
        position = 0
        while True:
            try:
                position = canal.index(valeur, position) + 1
            except ValueError:
                break
            indices.append(position - 1)
    return sorted(indices)


def compter_series(indices: list[int], longueur: int, report: int, minimum: int) -> tuple[int, int]:
    """Séries d'au moins `minimum` extrêmes consécutifs : (séries closes, série
    encore ouverte en fin de bloc, reportée au bloc suivant)."""
    series, courant, precedent = 0, report, -1
    for i in indices:
        if i == precedent + 1:
            courant += 1
        else:
            series += courant >= minimum
            courant = 1
        precedent = i
    if longueur and precedent == longueur - 1:
        return series, courant
    return series + (courant >= minimum), 0


class Mesureur:
    """Accumule les mesures de niveau, bloc après bloc."""

    def __init__(self, canaux: int, codage: Codage, frequence: int, options: Options) -> None:
        self.canaux, self.codage, self.frequence, self.options = canaux, codage, frequence, options
        self.trames = 0
        self.crete = [0.0] * canaux
        self.carres = [0.0] * canaux
        self.somme = [0.0] * canaux
        self.extremes = [0] * canaux
        self.series = [0] * canaux
        self.report = [0] * canaux
        self.trames_fenetre = max(1, round(frequence * FENETRE_S))
        self.seuil_carre = (codage.pleine_echelle * 10 ** (options.seuil_silence_dbfs / 20)) ** 2
        self.fenetres = 0
        self.actives = 0
        self.premiere_active: int | None = None
        self.derniere_active: int | None = None

    def ajouter(self, tableau: array) -> None:
        """Intègre un bloc entrelacé (trames complètes)."""
        n = len(tableau) // self.canaux
        if n == 0:
            return
        for c in range(self.canaux):
            self.ajouter_canal(c, tableau[c:n * self.canaux:self.canaux])
        self.mesurer_fenetres(tableau, n)
        self.trames += n

    def ajouter_canal(self, c: int, canal: array) -> None:
        """Crête, carrés, somme, extrêmes et séries d'un canal."""
        self.crete[c] = max(self.crete[c], abs(max(canal)), abs(min(canal)))
        self.carres[c] += math.sumprod(canal, canal)
        self.somme[c] += sum(canal)
        extremes = indices_extremes(canal, self.codage)
        self.extremes[c] += len(extremes)
        fermees, self.report[c] = compter_series(extremes, len(canal), self.report[c], self.options.serie_min)
        self.series[c] += fermees

    def mesurer_fenetres(self, tableau: array, n: int) -> None:
        """Fenêtres de 50 ms : actives si leur carré moyen atteint le seuil."""
        pas = self.trames_fenetre * self.canaux
        for debut in range(0, n * self.canaux, pas):
            morceau = tableau[debut:min(debut + pas, n * self.canaux)]
            if math.sumprod(morceau, morceau) / len(morceau) >= self.seuil_carre:
                self.actives += 1
                if self.premiere_active is None:
                    self.premiere_active = self.fenetres
                self.derniere_active = self.fenetres
            self.fenetres += 1

    def canal(self, c: int, series: int) -> dict[str, Any]:
        """Mesures d'un canal."""
        pleine, n = self.codage.pleine_echelle, max(self.trames, 1)
        return {"canal": c + 1, "crete_dbfs": dbfs(self.crete[c], pleine),
                "rms_dbfs": dbfs(math.sqrt(self.carres[c] / n), pleine),
                "decalage_continu_pct": round(100 * self.somme[c] / n / pleine, 4),
                "echantillons_pleine_echelle": self.extremes[c], "series_ecretees": series}

    def resultat(self) -> dict[str, Any]:
        """Mesures finales (séries encore ouvertes closes)."""
        series = [s + (r >= self.options.serie_min) for s, r in zip(self.series, self.report)]
        pleine, n = self.codage.pleine_echelle, max(self.trames, 1)
        duree = self.trames / self.frequence if self.frequence else 0.0
        fin_active = (self.derniere_active + 1) * FENETRE_S if self.derniere_active is not None else 0.0
        return {
            "codage": self.codage.nom, "trames_mesurees": self.trames,
            "crete_dbfs": dbfs(max(self.crete), pleine),
            "rms_dbfs": dbfs(math.sqrt(sum(self.carres) / (n * self.canaux)), pleine),
            "par_canal": [self.canal(c, series[c]) for c in range(self.canaux)],
            "echantillons_pleine_echelle": sum(self.extremes), "series_ecretees": sum(series),
            "fenetres_50ms": self.fenetres,
            "proportion_silence": round(1 - self.actives / self.fenetres, 4) if self.fenetres else None,
            "silence_debut_s": round((self.premiere_active if self.premiere_active is not None
                                      else self.fenetres) * FENETRE_S, 3),
            "silence_fin_s": round(max(0.0, duree - fin_active), 3),
        }


# ------------------------------------------------------------------ WAV --

def parcourir_riff(flux: BinaryIO) -> dict[str, Any]:
    """Morceaux RIFF : fmt, data, LIST/INFO ; sans rien décoder."""
    tete = flux.read(12)
    conteneurs = (FORMAT_RIFF.encode(), FORMAT_RF64.encode(), FORMAT_RIFX.encode())
    if len(tete) < 12 or tete[:4] not in conteneurs or tete[8:12] != b"WAVE":
        raise ErreurFormat("en-tête RIFF/WAVE absent")
    infos: dict[str, Any] = {"conteneur": tete[:4].decode("ascii"), "morceaux": [], "fmt": b"",
                             "data": None, "etiquettes": {}}
    while len(infos["morceaux"]) < 1000:
        entete = flux.read(8)
        if len(entete) < 8:
            break
        ident, taille = entete[:4], struct.unpack("<I", entete[4:])[0]
        position = flux.tell()
        infos["morceaux"].append(ident.decode("latin-1"))
        if ident == b"ds64" and taille >= 16:
            infos["taille_ds64"] = struct.unpack("<Q", flux.read(16)[8:16])[0]
        elif ident == b"fmt ":
            infos["fmt"] = flux.read(min(taille, 64))
        elif ident == b"data":
            if taille == 0xFFFFFFFF and "taille_ds64" in infos:
                taille = infos["taille_ds64"]
            infos["data"] = (position, taille)
        elif ident == b"LIST":
            infos["etiquettes"].update(lire_info_riff(flux.read(min(taille, 65536))))
        flux.seek(position + taille + (taille & 1))
    return infos


def lire_info_riff(octets: bytes) -> dict[str, str]:
    """Sous-morceaux LIST/INFO (INAM, IART…)."""
    if octets[:4] != b"INFO":
        return {}
    etiquettes, pos = {}, 4
    while pos + 8 <= len(octets):
        ident = octets[pos:pos + 4].decode("latin-1")
        taille = struct.unpack("<I", octets[pos + 4:pos + 8])[0]
        etiquettes[ident] = octets[pos + 8:pos + 8 + taille].rstrip(b"\x00").decode("latin-1")
        pos += 8 + taille + (taille & 1)
    return etiquettes


def lire_fmt(fmt: bytes) -> tuple[int, int, int, int]:
    """(code format effectif, canaux, fréquence, bits) du morceau fmt."""
    if len(fmt) < 16:
        raise ErreurFormat("morceau fmt absent ou trop court")
    code, canaux, frequence, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
    if code == CODE_EXTENSIBLE and len(fmt) >= 26:
        code = struct.unpack("<H", fmt[24:26])[0]
    return code, canaux, frequence, bits


def mesurer_blocs(mesureur: Mesureur, lire: Any, largeur_trame: int) -> None:
    """Lit des blocs d'octets par `lire(n_trames)` jusqu'à épuisement."""
    bloc = mesureur.trames_fenetre * FENETRES_PAR_BLOC
    while True:
        octets = lire(bloc)
        if not octets:
            break
        utiles = len(octets) // largeur_trame * largeur_trame
        mesureur.ajouter(vers_tableau(octets[:utiles], mesureur.codage))


def decoder_wave(flux: BinaryIO, options: Options) -> tuple[Mesureur, int]:
    """PCM entier par le module wave ; (mesureur, trames annoncées)."""
    with wave.open(flux, "rb") as lecteur:
        canaux, largeur = lecteur.getnchannels(), lecteur.getsampwidth()
        mesureur = Mesureur(canaux, codage_pour(largeur, False), lecteur.getframerate(), options)
        mesurer_blocs(mesureur, lecteur.readframes, canaux * largeur)
        return mesureur, lecteur.getnframes()


def decoder_direct(flux: BinaryIO, data: tuple[int, int], format_: tuple[int, int, int, int],
                   options: Options) -> tuple[Mesureur, int]:
    """Lecture directe du morceau data : flottant IEEE 32/64 bits, ou PCM
    entier d'un conteneur que wave ne lit pas (RF64)."""
    code, canaux, frequence, bits = format_
    flottant = code == CODE_FLOTTANT
    if (flottant and bits not in (32, 64)) or (not flottant and bits not in (8, 16, 24, 32)):
        raise ErreurFormat(f"{'flottant' if flottant else 'PCM'} {bits} bits non pris en charge")
    position, taille = data
    largeur_trame = canaux * bits // 8
    mesureur = Mesureur(canaux, codage_pour(bits // 8, flottant), frequence, options)
    flux.seek(position)
    restant = [taille]

    def lire(trames: int) -> bytes:
        octets = flux.read(min(trames * largeur_trame, restant[0]))
        restant[0] -= len(octets)
        return octets

    mesurer_blocs(mesureur, lire, largeur_trame)
    return mesureur, taille // largeur_trame


def analyser_wav(flux: BinaryIO, options: Options) -> dict[str, Any]:
    """Fiche d'un WAV : en-têtes, puis niveaux si le codage est décodable."""
    infos = parcourir_riff(flux)
    code, canaux, frequence, bits = lire_fmt(infos["fmt"])
    fiche: dict[str, Any] = {"format": FORMAT_WAV, "conteneur": infos["conteneur"], "code_format": code,
                             "canaux": canaux, "frequence_hz": frequence, "profondeur_bits": bits,
                             "morceaux": infos["morceaux"][:30], "etiquettes": infos["etiquettes"]}
    if not (0 < canaux <= 256 and 0 < frequence <= 1_000_000 and 0 < bits <= 64) or infos["data"] is None:
        raise ErreurFormat(f"WAV incohérent : {canaux} canaux, {frequence} Hz, {bits} bits, "
                           f"morceau data {'absent' if infos['data'] is None else 'présent'}")
    fiche["duree_s"] = round(infos["data"][1] / max(1, canaux * bits // 8) / frequence, 3)
    fiche["duree_methode"] = "taille du morceau data"
    if infos["conteneur"] == FORMAT_RIFX or code not in (CODE_PCM, CODE_FLOTTANT):
        fiche["niveaux"] = None
        fiche["note"] = f"codage {code} ou conteneur {infos['conteneur']} non décodé (ex. {FORMAT_ADPCM})"
        return fiche
    if bits not in (8, 16, 24, 32, 64):
        raise ErreurFormat(f"profondeur {bits} bits non décodable")
    flux.seek(0)
    if code == CODE_PCM and infos["conteneur"] == FORMAT_RIFF:
        mesureur, annoncees = decoder_wave(flux, options)
    else:
        mesureur, annoncees = decoder_direct(flux, infos["data"], (code, canaux, frequence, bits), options)
    fiche.update({"niveaux": mesureur.resultat(), "moteur_niveaux": "stdlib", "trames_annoncees": annoncees,
                  "duree_s": round(mesureur.trames / frequence, 3), "duree_methode": "trames décodées",
                  "tronque": mesureur.trames < annoncees})
    return fiche


# ------------------------------------------------------------------ MP3 --

def synchsafe(octets: bytes) -> int:
    """Entier ID3 « synchsafe » (7 bits utiles par octet)."""
    valeur = 0
    for octet in octets:
        valeur = (valeur << 7) | (octet & 0x7F)
    return valeur


def texte_id3(octets: bytes) -> str:
    """Contenu d'un cadre texte ID3 (octet d'encodage en tête)."""
    if not octets:
        return ""
    codecs = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}
    texte = octets[1:].decode(codecs.get(octets[0], "latin-1"), errors="replace")
    return texte.strip("\x00").replace("\x00", " / ")


def lire_id3v2(flux: BinaryIO) -> tuple[int, dict[str, Any]]:
    """(taille totale de l'étiquette, champs utiles) ; (0, {}) sans ID3v2."""
    tete = flux.read(10)
    if len(tete) < 10 or tete[:3] != b"ID3":
        return 0, {}
    version, drapeaux, taille = tete[3], tete[5], synchsafe(tete[6:10])
    total = 10 + taille + (10 if drapeaux & 0x10 else 0)
    corps = flux.read(min(taille, 1 << 20))
    position = 0
    if drapeaux & 0x40 and len(corps) >= 4:
        etendu = synchsafe(corps[:4]) if version == 4 else struct.unpack(">I", corps[:4])[0] + 4
        position = etendu
    champs: dict[str, Any] = {"version": f"2.{version}"}
    champs.update(cadres_id3(corps, position, version))
    return total, champs


def cadres_id3(corps: bytes, position: int, version: int) -> dict[str, Any]:
    """Cadres texte et image d'une étiquette ID3v2."""
    champs: dict[str, Any] = {}
    court = version == 2
    taille_entete = 6 if court else 10
    while position + taille_entete <= len(corps) and corps[position] != 0:
        ident = corps[position:position + (3 if court else 4)].decode("latin-1")
        brut = corps[position + (3 if court else 4):position + (6 if court else 8)]
        taille = (int.from_bytes(brut, "big") if version != 4 else synchsafe(brut))
        contenu = corps[position + taille_entete:position + taille_entete + taille]
        if ident in CADRES_ID3:
            champs[CADRES_ID3[ident]] = texte_id3(contenu)
        elif ident in ("APIC", "PIC"):
            champs["image_jointe_octets"] = taille
        if taille <= 0:
            break
        position += taille_entete + taille
    return champs


def entete_trame(octets: bytes) -> dict[str, Any] | None:
    """En-tête de trame MPEG audio, ou None s'il est invalide."""
    if len(octets) < 4 or octets[0] != 0xFF or (octets[1] & 0xE0) != 0xE0:
        return None
    version = {0: 25, 2: 2, 3: 1}.get((octets[1] >> 3) & 3)
    couche = {1: 3, 2: 2, 3: 1}.get((octets[1] >> 1) & 3)
    i_debit, i_freq = octets[2] >> 4, (octets[2] >> 2) & 3
    if version is None or couche is None or i_debit in (0, 15) or i_freq == 3:
        return None
    debit = DEBITS_MP3[(1 if version == 1 else 2, couche)][i_debit]
    frequence = FREQUENCES_MP3[version][i_freq]
    remplissage = (octets[2] >> 1) & 1
    echantillons = 384 if couche == 1 else (1152 if couche == 2 or version == 1 else 576)
    if couche == 1:
        longueur = (12 * debit * 1000 // frequence + remplissage) * 4
    else:
        longueur = echantillons // 8 * debit * 1000 // frequence + remplissage
    return {"version": version, "couche": couche, "debit_kbps": debit, "frequence_hz": frequence,
            "canaux": 1 if octets[3] >> 6 == 3 else 2, "echantillons": echantillons, "longueur": longueur}


def premiere_trame(donnees: bytes, strict: bool = False) -> tuple[int, dict[str, Any]]:
    """Première trame valide confirmée par la trame suivante (en mode non
    strict, une trame qui déborde des données lues est acceptée)."""
    position = donnees.find(b"\xff")
    while 0 <= position < len(donnees) - 4:
        trame = entete_trame(donnees[position:position + 4])
        if trame is not None:
            suivante = position + trame["longueur"]
            deborde = suivante + 4 > len(donnees)
            if (deborde and not strict) or entete_trame(donnees[suivante:suivante + 4]) is not None:
                return position, trame
        position = donnees.find(b"\xff", position + 1)
    raise ErreurFormat("aucune trame MPEG audio valide dans les 256 premiers Kio")


def duree_mp3(donnees: bytes, position: int, trame: dict[str, Any], octets_audio: int) -> tuple[float, str]:
    """Durée par Xing/Info ou VBRI, sinon estimée en débit constant."""
    lateral = (32 if trame["canaux"] == 2 else 17) if trame["version"] == 1 else (17 if trame["canaux"] == 2 else 9)
    xing = position + 4 + lateral
    if donnees[xing:xing + 4] in (b"Xing", b"Info"):
        drapeaux = struct.unpack(">I", donnees[xing + 4:xing + 8])[0]
        if drapeaux & 1:
            trames = struct.unpack(">I", donnees[xing + 8:xing + 12])[0]
            return trames * trame["echantillons"] / trame["frequence_hz"], donnees[xing:xing + 4].decode()
    vbri = position + 36
    if donnees[vbri:vbri + 4] == ENTETE_VBRI.encode():
        trames = struct.unpack(">I", donnees[vbri + 14:vbri + 18])[0]
        return trames * trame["echantillons"] / trame["frequence_hz"], ENTETE_VBRI
    return octets_audio * 8 / (trame["debit_kbps"] * 1000), "estimée (débit constant supposé)"


def analyser_mp3(flux: BinaryIO, taille: int) -> dict[str, Any]:
    """Fiche d'un MP3 : ID3, première trame, durée."""
    debut, etiquettes = lire_id3v2(flux)
    flux.seek(debut)
    donnees = flux.read(256 * 1024)
    position, trame = premiere_trame(donnees)
    flux.seek(max(0, taille - 128))
    fin = 128 if flux.read(3) == b"TAG" else 0
    octets_audio = taille - debut - position - fin
    duree, methode = duree_mp3(donnees, position, trame, octets_audio)
    return {"format": FORMAT_MP3, "codage": f"MPEG-{trame['version'] if trame['version'] != 25 else '2.5'} "
                                            f"couche {'I' * trame['couche']}",
            "canaux": trame["canaux"], "frequence_hz": trame["frequence_hz"], "profondeur_bits": None,
            "debit_kbps_premiere_trame": trame["debit_kbps"], "duree_s": round(duree, 3),
            "duree_methode": methode, "etiquettes": etiquettes, "id3v1": bool(fin), "niveaux": None}


# ----------------------------------------------------------- FLAC, OGG --

def lire_commentaires(octets: bytes, position: int = 0) -> dict[str, Any]:
    """Commentaires Vorbis (FLAC, Vorbis, Opus) : vendeur et champs."""
    try:
        longueur = struct.unpack_from("<I", octets, position)[0]
        vendeur = octets[position + 4:position + 4 + longueur].decode("utf-8", errors="replace")
        position += 4 + longueur
        nombre = struct.unpack_from("<I", octets, position)[0]
        position += 4
        champs: dict[str, Any] = {"vendeur": vendeur}
        for _ in range(min(nombre, 1000)):
            longueur = struct.unpack_from("<I", octets, position)[0]
            cle, _, valeur = octets[position + 4:position + 4 + longueur].decode("utf-8", "replace").partition("=")
            champs[cle.upper()] = valeur if len(valeur) < 500 else f"({len(valeur)} caractères)"
            position += 4 + longueur
        return champs
    except struct.error:
        return {"erreur": "commentaires tronqués"}


def analyser_flac(flux: BinaryIO) -> dict[str, Any]:
    """Fiche d'un FLAC : STREAMINFO et commentaires."""
    debut, _ = lire_id3v2(flux)
    flux.seek(debut)
    if flux.read(4) != b"fLaC":
        raise ErreurFormat("marqueur fLaC absent")
    fiche: dict[str, Any] = {"format": FORMAT_FLAC, "etiquettes": {}, "niveaux": None, "images": 0}
    for _ in range(256):
        entete = flux.read(4)
        if len(entete) < 4:
            break
        dernier, genre, taille = entete[0] & 0x80, entete[0] & 0x7F, int.from_bytes(entete[1:], "big")
        position = flux.tell()
        if genre == 0:
            fiche.update(lire_streaminfo(flux.read(taille)))
        elif genre == 4:
            fiche["etiquettes"] = lire_commentaires(flux.read(min(taille, 1 << 20)))
        fiche["images"] += genre == 6
        flux.seek(position + taille)
        if dernier:
            break
    if "frequence_hz" not in fiche:
        raise ErreurFormat(f"FLAC sans bloc {BLOC_STREAMINFO}")
    return fiche


def lire_streaminfo(bloc: bytes) -> dict[str, Any]:
    """Champs de STREAMINFO (34 octets)."""
    if len(bloc) < 34:
        raise ErreurFormat(f"{BLOC_STREAMINFO} tronqué")
    valeur = int.from_bytes(bloc[10:18], "big")
    frequence, total = valeur >> 44, valeur & 0xFFFFFFFFF
    return {"frequence_hz": frequence, "canaux": ((valeur >> 41) & 7) + 1,
            "profondeur_bits": ((valeur >> 36) & 0x1F) + 1, "echantillons": total,
            "duree_s": round(total / frequence, 3) if frequence and total else None,
            "duree_methode": BLOC_STREAMINFO, "md5_audio": bloc[18:34].hex()}


def paquets_ogg(flux: BinaryIO, nombre: int) -> tuple[int, list[bytes]]:
    """Premiers paquets du premier flux logique (numéro de série, paquets)."""
    paquets: list[bytes] = []
    courant, serie, lus = b"", None, 0
    while len(paquets) < nombre and lus < (1 << 20):
        entete = flux.read(27)
        if len(entete) < 27 or entete[:4] != b"OggS":
            break
        numero, segments = struct.unpack_from("<I", entete, 14)[0], entete[26]
        table = flux.read(segments)
        charge = flux.read(sum(table))
        lus += 27 + segments + len(charge)
        serie = numero if serie is None else serie
        if numero != serie:
            continue
        position = 0
        for lacet in table:
            courant += charge[position:position + lacet]
            position += lacet
            if lacet < 255:
                paquets.append(courant)
                courant = b""
    if serie is None:
        raise ErreurFormat("aucune page OggS")
    return serie, paquets


def derniere_granule(flux: BinaryIO, taille: int, serie: int) -> int | None:
    """Position de granule de la dernière page du flux (lecture de la fin)."""
    flux.seek(max(0, taille - 131072))
    fin = flux.read()
    position = fin.rfind(b"OggS")
    while position >= 0:
        if len(fin) >= position + 18 and struct.unpack_from("<I", fin, position + 14)[0] == serie:
            granule = struct.unpack_from("<q", fin, position + 6)[0]
            if granule >= 0:
                return granule
        position = fin.rfind(b"OggS", 0, position)
    return None


def analyser_ogg(flux: BinaryIO, taille: int) -> dict[str, Any]:
    """Fiche d'un OGG Vorbis ou Opus."""
    serie, paquets = paquets_ogg(flux, 2)
    if not paquets:
        raise ErreurFormat("OGG sans paquet d'identification")
    ident = paquets[0]
    granule = derniere_granule(flux, taille, serie)
    commentaires = paquets[1] if len(paquets) > 1 else b""
    if ident.startswith(b"\x01vorbis") and len(ident) >= 28:
        canaux, frequence, _, nominal = struct.unpack_from("<BIiI", ident, 11)
        duree = granule / frequence if granule is not None and frequence else None
        etiquettes = lire_commentaires(commentaires, 7) if commentaires.startswith(b"\x03vorbis") else {}
        codec, debit = "Vorbis", round(nominal / 1000) if nominal else None
    elif ident.startswith(b"OpusHead") and len(ident) >= 19:
        canaux, pre_saut, origine = ident[9], struct.unpack_from("<H", ident, 10)[0], struct.unpack_from("<I", ident, 12)[0]
        frequence = 48000
        duree = (granule - pre_saut) / 48000 if granule is not None else None
        etiquettes = lire_commentaires(commentaires, 8) if commentaires.startswith(b"OpusTags") else {}
        codec, debit = f"Opus (source {origine} Hz)", None
    else:
        raise ErreurFormat(f"flux OGG non pris en charge (en-tête {ident[:8]!r})")
    return {"format": f"{FORMAT_OGG} {codec.split()[0]}", "codage": codec, "canaux": canaux,
            "frequence_hz": frequence, "profondeur_bits": None, "debit_nominal_kbps": debit,
            "duree_s": round(duree, 3) if duree is not None else None, "duree_methode": "granule finale",
            "etiquettes": etiquettes, "niveaux": None}


# -------------------------------------------------------- aiguillage --

def reconnaitre(flux: BinaryIO) -> str | None:
    """Format d'après les octets de tête (jamais d'après l'extension)."""
    tete = flux.read(4096)
    if tete[:4] in (b"RIFF", b"RF64", b"RIFX") and tete[8:12] == b"WAVE":
        return FORMAT_WAV
    if tete[:4] == b"fLaC":
        return FORMAT_FLAC
    if tete[:4] == b"OggS":
        return FORMAT_OGG
    if tete[:3] == ETIQUETTE_ID3.encode() and len(tete) >= 10:
        flux.seek(10 + synchsafe(tete[6:10]))
        return FORMAT_FLAC if flux.read(4) == b"fLaC" else FORMAT_MP3
    try:
        premiere_trame(tete, strict=True)
    except ErreurFormat:
        return None
    return FORMAT_MP3


def analyser_flux(flux: BinaryIO, taille: int, options: Options) -> dict[str, Any]:
    """Fiche d'un flux audio, quel que soit son format reconnu."""
    nature = reconnaitre(flux)
    flux.seek(0)
    try:
        if nature == FORMAT_WAV:
            return analyser_wav(flux, options)
        if nature == FORMAT_MP3:
            return analyser_mp3(flux, taille)
        if nature == FORMAT_FLAC:
            return analyser_flac(flux)
        if nature == FORMAT_OGG:
            return analyser_ogg(flux, taille)
    except (struct.error, wave.Error, EOFError, IndexError, KeyError, ValueError, OverflowError) as exc:
        raise ErreurFormat(f"{nature} illisible : {exc}") from exc
    raise ErreurFormat("format audio non reconnu (ni WAV, MP3, FLAC, OGG)")


def defauts(fiche: dict[str, Any], options: Options) -> list[str]:
    """Saturation, silence, troncature."""
    trouves = []
    niveaux = fiche.get("niveaux")
    if niveaux:
        if niveaux["series_ecretees"]:
            trouves.append(f"écrêtage : {niveaux['series_ecretees']} série(s) d'au moins "
                           f"{options.serie_min} échantillons à pleine échelle")
        crete = niveaux["crete_dbfs"]
        if crete is None or crete < options.seuil_silence_dbfs:
            trouves.append(f"silencieux : crête {crete if crete is not None else '-inf'} dBFS "
                           f"< {options.seuil_silence_dbfs} dBFS")
    if fiche.get("tronque"):
        trouves.append(f"tronqué : {fiche['niveaux']['trames_mesurees']} trames lues sur "
                       f"{fiche['trames_annoncees']} annoncées")
    return trouves


# ------------------------------------------------------- bibliothèques --

def niveaux_soundfile(chemin: Path, options: Options) -> dict[str, Any]:
    """Décodage par soundfile (libsndfile) puis mêmes mesures qu'en stdlib."""
    infos = soundfile.info(str(chemin))
    mesureur = Mesureur(infos.channels, codage_pour(4, True), infos.samplerate, options)
    for bloc in soundfile.blocks(str(chemin), blocksize=mesureur.trames_fenetre * FENETRES_PAR_BLOC,
                                 dtype="float32", always_2d=True):
        mesureur.ajouter(array("f", bloc.tobytes()))
    return mesureur.resultat()


def controle_croise(chemin: Path, fiche: dict[str, Any], options: Options) -> dict[str, Any]:
    """Confronte avec mutagen et soundfile si présentes ; complète les niveaux."""
    resultat: dict[str, Any] = {}
    if mutagen is not None:
        resultat["mutagen"] = comparer_mutagen(chemin, fiche)
    if soundfile is not None:
        try:
            niveaux = niveaux_soundfile(chemin, options)
        except (RuntimeError, TypeError, ValueError) as exc:
            resultat["soundfile"] = {"erreur": str(exc)[:200]}
            return resultat
        if fiche.get("niveaux") is None:
            fiche["niveaux"], fiche["moteur_niveaux"] = niveaux, "soundfile"
            resultat["soundfile"] = {"role": "décodage des niveaux", "ecarts": []}
        else:
            resultat["soundfile"] = {"role": "contrôle", "ecarts": ecarts_niveaux(fiche["niveaux"], niveaux)}
    return resultat


def comparer_mutagen(chemin: Path, fiche: dict[str, Any]) -> dict[str, Any]:
    """Durée (tolérance 50 ms ou 0,5 %), fréquence et canaux selon mutagen."""
    try:
        lu = mutagen.File(str(chemin))
    except (mutagen.MutagenError, OSError, ValueError, TypeError, KeyError, IndexError, struct.error,
            ZeroDivisionError, OverflowError) as exc:
        return {"erreur": f"{type(exc).__name__} : {str(exc)[:180]}", "ecarts": []}
    if lu is None or lu.info is None:
        return {"erreur": "format non reconnu par mutagen", "ecarts": []}
    leur = {"duree_s": round(lu.info.length, 3), "frequence_hz": getattr(lu.info, "sample_rate", None),
            "canaux": getattr(lu.info, "channels", None)}
    ecarts = [f"{cle} : stdlib {fiche.get(cle)} / mutagen {val}" for cle, val in leur.items()
              if cle != "duree_s" and val is not None and fiche.get(cle) != val]
    duree = fiche.get("duree_s")
    if duree is not None and abs(duree - leur["duree_s"]) > max(0.05, 0.005 * leur["duree_s"]):
        ecarts.append(f"duree_s : stdlib {duree} / mutagen {leur['duree_s']}")
    return {"valeurs": leur, "ecarts": ecarts}


def ecarts_niveaux(miens: dict[str, Any], leurs: dict[str, Any]) -> list[str]:
    """Crête et RMS à 0,05 dB près."""
    ecarts = []
    for cle in ("crete_dbfs", "rms_dbfs"):
        a, b = miens[cle], leurs[cle]
        if (a is None) != (b is None) or (a is not None and abs(a - b) > 0.05):
            ecarts.append(f"{cle} : stdlib {a} / soundfile {b}")
    return ecarts


# --------------------------------------------------------------- étalon --

def wav_synthetique(frequence: int, canaux: int, largeur: int, duree: float,
                    amplitude: float, ton: float) -> bytes:
    """WAV PCM fabriqué en mémoire par le module wave (sinus, borné à ±1)."""
    pleine = 2 ** (8 * largeur - 1) - 1
    valeurs = [round(max(-1.0, min(1.0, amplitude * math.sin(2 * math.pi * ton * i / frequence))) * pleine)
               for i in range(int(frequence * duree))]
    tableau = array({1: "b", 2: "h", 3: TYPE_32}[largeur], [v for v in valeurs for _ in range(canaux)])
    if sys.byteorder == "big":
        tableau.byteswap()
    octets = tableau.tobytes()
    if largeur == 1:
        octets = bytes((b + 128) & 0xFF for b in octets)
    elif largeur == 3:
        octets = b"".join(octets[i:i + 3] for i in range(0, len(octets), 4))
    sortie = io.BytesIO()
    with wave.open(sortie, "wb") as ecrivain:
        ecrivain.setnchannels(canaux)
        ecrivain.setsampwidth(largeur)
        ecrivain.setframerate(frequence)
        ecrivain.writeframes(octets)
    return sortie.getvalue()


def wav_flottant(frequence: int, duree: float, amplitude: float, ton: float) -> bytes:
    """WAV flottant 32 bits mono fabriqué octet par octet (wave ne l'écrit pas)."""
    valeurs = array("f", (amplitude * math.sin(2 * math.pi * ton * i / frequence)
                          for i in range(int(frequence * duree))))
    if sys.byteorder == "big":
        valeurs.byteswap()
    data = valeurs.tobytes()
    fmt = struct.pack("<HHIIHH", CODE_FLOTTANT, 1, frequence, frequence * 4, 4, 32)
    corps = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(corps)) + corps


def etalons() -> list[tuple[str, bytes, dict[str, Any]]]:
    """(nom, octets, attendu) : valeurs théoriques calculées, pas mesurées."""
    sinus = 20 * math.log10(0.5 * 32767 / 32768)
    return [
        ("sinus_1kHz_-6dB_16bits_stereo", wav_synthetique(44100, 2, 2, 1.0, 0.5, 1000.0),
         {"crete_dbfs": round(sinus, 2), "rms_dbfs": round(sinus - 10 * math.log10(2), 2), "ecrete": False,
          "silencieux": False}),
        ("silence_8bits_mono", wav_synthetique(8000, 1, 1, 0.5, 0.0, 440.0),
         {"crete_dbfs": None, "ecrete": False, "silencieux": True}),
        ("sinus_ecrete_24bits_mono", wav_synthetique(48000, 1, 3, 0.25, 1.5, 440.0),
         {"ecrete": True, "silencieux": False}),
        ("sinus_-12dB_flottant32", wav_flottant(22050, 0.5, 0.25, 500.0),
         {"crete_dbfs": round(20 * math.log10(0.25), 2), "ecrete": False, "silencieux": False}),
    ]


def verifier_etalon(nom: str, octets: bytes, attendu: dict[str, Any], options: Options) -> dict[str, Any]:
    """Mesure un étalon et compare à la valeur théorique (tolérance 0,05 dB)."""
    fiche = analyser_flux(io.BytesIO(octets), len(octets), options)
    trouves = defauts(fiche, options)
    mesure = {"crete_dbfs": fiche["niveaux"]["crete_dbfs"], "rms_dbfs": fiche["niveaux"]["rms_dbfs"],
              "ecrete": any(d.startswith("écrêtage") for d in trouves),
              "silencieux": any(d.startswith("silencieux") for d in trouves)}
    ecarts = []
    for cle, valeur in attendu.items():
        obtenu = mesure[cle]
        proche = (isinstance(valeur, float) and isinstance(obtenu, float) and abs(valeur - obtenu) <= 0.05)
        if not (proche or valeur == obtenu):
            ecarts.append(f"{cle} : attendu {valeur}, mesuré {obtenu}")
    return {"chemin": f"etalon:{nom}", "format": fiche["format"], "canaux": fiche["canaux"],
            "frequence_hz": fiche["frequence_hz"], "profondeur_bits": fiche["profondeur_bits"],
            "duree_s": fiche["duree_s"], "attendu": attendu, "mesure": mesure,
            "conforme": not ecarts, "defauts": ecarts, "moteur_niveaux": "stdlib"}


# ---------------------------------------------------------------- entrée --

def lister_fichiers(chemins: list[str], racine: Path | None) -> list[Path]:
    """Fichiers donnés, ou fichiers audio trouvés dans les dossiers."""
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
            raise ErreurFormat(f"chemin introuvable : {chemin}")
    return fichiers


def examiner_fichier(chemin: Path, options: Options) -> dict[str, Any]:
    """Fiche complète d'un fichier (erreur rangée dans la fiche)."""
    try:
        with chemin.open("rb") as flux:
            fiche = analyser_flux(flux, chemin.stat().st_size, options)
    except ErreurFormat as exc:
        return {"chemin": str(chemin), "erreur": str(exc), "defauts": []}
    except OSError as exc:
        return {"chemin": str(chemin), "erreur": f"illisible : {exc.strerror or exc}", "defauts": []}
    fiche = {"chemin": str(chemin), **fiche}
    fiche["controle_croise"] = controle_croise(chemin, fiche, options)
    fiche["defauts"] = defauts(fiche, options)
    fiche["ecarts_moteurs"] = [e for bloc in fiche["controle_croise"].values() for e in bloc.get("ecarts", [])]
    return fiche


def analyser(args: argparse.Namespace) -> dict[str, Any]:
    """Cœur : étalons et fichiers."""
    options = Options(args.seuil_silence, args.serie_min)
    fiches = [verifier_etalon(n, o, a, options) for n, o, a in etalons()] if args.etalon else []
    fiches += [examiner_fichier(c, options) for c in lister_fichiers(args.chemins, args.racine)]
    moteurs = sorted({f.get("moteur_niveaux") or "stdlib" for f in fiches})
    noms = [f["chemin"] for f in fiches]
    return {"denominateur": len(fiches), "examines": noms[:LIMITE_EXAMINES],
            "examines_tronques": len(noms) > LIMITE_EXAMINES, "moteur": "+".join(moteurs) or "stdlib",
            "seuils": {"silence_dbfs": options.seuil_silence_dbfs, "serie_ecretage": options.serie_min},
            "fichiers": fiches}


# ---------------------------------------------------------------- sortie --

def imprimer_humain(res: dict[str, Any]) -> None:
    """Sortie lisible."""
    print(f"{res['denominateur']} élément(s) examiné(s) (moteur : {res['moteur']})")
    for fiche in res["fichiers"]:
        if "erreur" in fiche:
            print(f"\n{fiche['chemin']} : ERREUR {fiche['erreur']}")
            continue
        print(f"\n{fiche['chemin']} : {fiche['format']}, {fiche['canaux']} canal(aux), "
              f"{fiche['frequence_hz']} Hz, {fiche.get('profondeur_bits') or '-'} bits, "
              f"{fiche.get('duree_s')} s ({fiche.get('duree_methode', 'mesurée')})")
        if "attendu" in fiche:
            print(f"  attendu {fiche['attendu']}\n  mesuré  {fiche['mesure']}\n  "
                  f"{'conforme' if fiche['conforme'] else 'NON CONFORME'}")
        niveaux = fiche.get("niveaux")
        if niveaux:
            print(f"  crête {niveaux['crete_dbfs']} dBFS, RMS {niveaux['rms_dbfs']} dBFS, "
                  f"{niveaux['echantillons_pleine_echelle']} échantillon(s) à pleine échelle, "
                  f"silence {niveaux['proportion_silence']}")
        for defaut in fiche["defauts"] + fiche.get("ecarts_moteurs", []):
            print(f"  DÉFAUT {defaut}")


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Caractéristiques d'un fichier audio (WAV, MP3, FLAC, OGG) et détection "
                    "d'écrêtage ou de silence.",
        epilog="Exemples : inspecter_audio.py voix.wav --json\n           inspecter_audio.py livrables/\n"
               "           inspecter_audio.py --etalon --json   (contrôle de l'instrument)\n"
               "Codes : 0 rien à signaler ; 1 écrêtage, silence, troncature, écart entre moteurs "
               "ou étalon non conforme ; 2 entrée invalide ; 3 rien à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("chemins", nargs="*", help="fichiers audio ou dossiers (parcours récursif)")
    parseur.add_argument("--etalon", action="store_true",
                         help="mesurer d'abord 4 signaux synthétiques à valeurs théoriques connues")
    parseur.add_argument("--seuil-silence", type=float, default=-60.0, help="seuil de silence en dBFS (défaut -60)")
    parseur.add_argument("--serie-min", type=int, default=3,
                         help="échantillons consécutifs à pleine échelle pour parler d'écrêtage (défaut 3)")
    parseur.add_argument("--racine", type=Path, help="base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def signaler_bibliotheques() -> None:
    """Une ligne sur stderr par bibliothèque optionnelle absente."""
    if soundfile is None:
        print("soundfile absente : niveaux mesurés pour WAV seulement (MP3/FLAC/OGG : en-têtes)", file=sys.stderr)
    if mutagen is None:
        print("mutagen absente : durées et formats non confrontés", file=sys.stderr)


def code_sortie(res: dict[str, Any]) -> int:
    """2 si tout est illisible ; 1 si défaut ou écart ; 0 sinon."""
    fiches = res["fichiers"]
    if all("erreur" in f for f in fiches):
        return 2
    signale = any(f["defauts"] or f.get("ecarts_moteurs") or "erreur" in f for f in fiches)
    return 1 if signale else 0


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    parseur = construire_parseur()
    args = parseur.parse_args(argv)
    if not args.chemins and not args.etalon:
        parseur.print_usage(sys.stderr)
        print("erreur : donner un fichier, un dossier ou --etalon", file=sys.stderr)
        return 2
    if args.serie_min < 1:
        print("erreur : --serie-min doit valoir au moins 1", file=sys.stderr)
        return 2
    signaler_bibliotheques()
    try:
        res = analyser(args)
    except ErreurFormat as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
    if res["denominateur"] == 0:
        print("dénominateur nul : aucun fichier audio trouvé, rien à examiner", file=sys.stderr)
        return 3
    res["contrat"] = lire_contrat()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        imprimer_humain(res)
    for fiche in res["fichiers"]:
        if "erreur" in fiche:
            print(f"erreur : {fiche['chemin']} : {fiche['erreur']}", file=sys.stderr)
    return code_sortie(res)


if __name__ == "__main__":
    raise SystemExit(main())
