"""Identifie le vrai type d'un fichier par ses octets (signatures magiques) et dit si son extension ment.

Pourquoi : les outils courants jugent sur le nom. Mesuré le 2026-10-02 : pour une copie de
/bin/ls nommée faux.png, ``mimetypes.guess_type`` rend ('image/png', None) quand
``file --mime-type`` répond application/x-pie-executable. Sur 412 fichiers de cette machine
(382 réels : testdata Go, caches uv et pip, polices ; 30 témoins fabriqués), cet outil et
file(1) divergent sur 6 : 3 objets coff que l'outil ne reconnaît pas, 3 où file se trompe
(témoin texte commençant par « OTTO », témoin utf-16 sans BOM, WAV réel grand-boutiste).

QUESTION
    Quel est le vrai type de ce fichier, et son extension ment-elle ?
MESURE
    Lecture bornée de la tête (64 Kio) et, au besoin, d'octets ciblés (en-tête PE à e_lfanew,
    queue Parquet, répertoire central ZIP) ; reconnaissance par signatures : png, jpeg, gif,
    webp, bmp, ico, tiff, pdf, zip et, par le contenu de l'archive, docx/xlsx/pptx/odf/epub/
    jar/apk/whl ; gzip, bz2, xz, zstd, lz4, 7z, rar, cab, tar (ustar à 257, ou somme de
    contrôle v7), ar/deb, rpm, iso ; ELF, PE, MZ, Mach-O, classe Java, wasm ; SQLite, Parquet,
    ole2 ; riff (wav, avi, webp), mp3 (ID3 et trames), aac, flac, ogg, mp4/iso-bmff par
    marque ftyp (mp4, m4a, mov, 3gp, heif, avif), Matroska/WebM, mpeg-ts/ps, midi ; polices ;
    .pyc avec version de Python d'après le nombre magique ; sinon texte (BOM, utf-8, 8 bits)
    contre binaire. Verdict d'extension : concordante, compatible (même famille, ex. .zip
    sur un docx), contradiction, ou indéterminée (extension absente, générique ou inconnue).
HYPOTHÈSES
    Le type se lit dans les premiers octets ou dans des structures internes standard. Une
    extension « connue » appartient à une liste interne de types texte et binaires.
LIMITES
    Ne valide pas le fichier entier (un PNG tronqué reste « png »). Les sous-types ole2
    (doc, xls, msi) ne sont pas distingués. Le texte n'est pas typé finement : un .json
    contenant du html n'est pas une contradiction. Avec python-magic, la base libmagic du
    système est lue (hors des chemins donnés) ; puremagic et filetype n'ont pas ce défaut.
CONTRE-EXEMPLES
    Constaté : un .mp3 réduit à une trame valide (FF FB 90 64) suivie d'octets quelconques est
    classé « données » et déclaré en contradiction, alors que file(1) y voit du mp3 : l'outil
    exige une seconde trame. Constaté : un .txt en chinois encodé utf-16-le sans BOM (pas
    d'alternance d'octets nuls) est déclaré « données », donc en contradiction.
INVOCATION
    {outil} {dossier} --json
DOMAINE
    Contrôle d'artefacts avant publication, tri de fichiers téléversés, détection de
    fichiers déguisés ou corrompus, inventaire d'un dépôt.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import platform
import re
import struct
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Callable, NamedTuple, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import puremagic
except ImportError:
    puremagic = None
try:
    import filetype
except ImportError:
    filetype = None
try:
    import magic
except ImportError:
    magic = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
             INTITULE_INVOCATION, "DOMAINE")

SIGNATURE_OTF = "OTTO"
TAILLE_TETE = 65536
MAX_EXAMINES = 200
DOSSIERS_IGNORES = frozenset({".git", ".hg", ".svn"})
ORDRE_BIBLIOTHEQUES = ("puremagic", "filetype", "magic")


class TypeInfo(NamedTuple):
    """Fiche d'un type : description, MIME, extensions concordantes, famille (type parent)."""

    description: str
    mime: str
    extensions: tuple[str, ...]
    famille: str = ""


TYPES = MappingProxyType({
    "png": TypeInfo("image PNG", "image/png", ("png", "apng")),
    "jpeg": TypeInfo("image JPEG", "image/jpeg", ("jpg", "jpeg", "jpe", "jfif", "pjpeg", "pjp")),
    "gif": TypeInfo("image GIF", "image/gif", ("gif",)),
    "webp": TypeInfo("image WebP", "image/webp", ("webp",), "riff"),
    "bmp": TypeInfo("image BMP", "image/bmp", ("bmp", "dib")),
    "ico": TypeInfo("icône Windows", "image/vnd.microsoft.icon", ("ico",)),
    "cur": TypeInfo("curseur Windows", "image/x-win-bitmap", ("cur",)),
    "tiff": TypeInfo("image TIFF (ou brut photo dérivé)", "image/tiff",
                     ("tif", "tiff", "dng", "nef", "cr2", "arw", "orf", "rw2", "pef", "srw", "nrw")),
    "psd": TypeInfo("image Photoshop", "image/vnd.adobe.photoshop", ("psd", "psb")),
    "pdf": TypeInfo("document PDF", "application/pdf", ("pdf", "ai")),
    "zip": TypeInfo("archive ZIP", "application/zip",
                    ("zip", "zipx", "nupkg", "vsix", "xpi", "kmz", "ipa", "aar", "egg", "cbz",
                     "3mf", "appx", "msix", "sketch", "usdz", "npz")),
    "docx": TypeInfo("document Word (OOXML)",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     ("docx", "docm", "dotx", "dotm"), "zip"),
    "xlsx": TypeInfo("classeur Excel (OOXML)",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     ("xlsx", "xlsm", "xltx", "xltm", "xlam"), "zip"),
    "pptx": TypeInfo("présentation PowerPoint (OOXML)",
                     "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                     ("pptx", "pptm", "potx", "potm", "ppsx", "ppsm"), "zip"),
    "odt": TypeInfo("texte OpenDocument", "application/vnd.oasis.opendocument.text", ("odt", "ott"), "zip"),
    "ods": TypeInfo("classeur OpenDocument", "application/vnd.oasis.opendocument.spreadsheet",
                    ("ods", "ots"), "zip"),
    "odp": TypeInfo("présentation OpenDocument", "application/vnd.oasis.opendocument.presentation",
                    ("odp", "otp"), "zip"),
    "odg": TypeInfo("dessin OpenDocument", "application/vnd.oasis.opendocument.graphics",
                    ("odg", "otg"), "zip"),
    "epub": TypeInfo("livre EPUB", "application/epub+zip", ("epub",), "zip"),
    "jar": TypeInfo("archive Java", "application/java-archive", ("jar", "war", "ear"), "zip"),
    "apk": TypeInfo("paquet Android", "application/vnd.android.package-archive", ("apk",), "zip"),
    "whl": TypeInfo("roue Python (wheel)", "application/zip", ("whl",), "zip"),
    "gzip": TypeInfo("flux gzip", "application/gzip", ("gz", "tgz", "gzip", "svgz")),
    "bzip2": TypeInfo("flux bzip2", "application/x-bzip2", ("bz2", "tbz", "tbz2", "bz")),
    "xz": TypeInfo("flux xz", "application/x-xz", ("xz", "txz")),
    "zstd": TypeInfo("flux Zstandard", "application/zstd", ("zst", "zstd", "tzst")),
    "lz4": TypeInfo("flux LZ4", "application/x-lz4", ("lz4",)),
    "7z": TypeInfo("archive 7-Zip", "application/x-7z-compressed", ("7z",)),
    "rar": TypeInfo("archive RAR", "application/vnd.rar", ("rar", "cbr")),
    "cab": TypeInfo("archive CAB", "application/vnd.ms-cab-compressed", ("cab",)),
    "tar": TypeInfo("archive tar", "application/x-tar", ("tar", "ova", "gem")),
    "ar": TypeInfo("archive ar", "application/x-archive", ("a", "lib", "ar")),
    "deb": TypeInfo("paquet Debian", "application/vnd.debian.binary-package", ("deb", "udeb"), "ar"),
    "rpm": TypeInfo("paquet RPM", "application/x-rpm", ("rpm",)),
    "iso": TypeInfo("image ISO 9660", "application/x-iso9660-image", ("iso",)),
    "elf": TypeInfo("binaire ELF", "application/x-executable", ("so", "o", "ko", "elf", "axf", "prx", "out", "node")),
    "pe": TypeInfo("exécutable Windows PE", "application/vnd.microsoft.portable-executable",
                   ("exe", "dll", "sys", "ocx", "scr", "cpl", "efi", "mui", "drv", "pyd", "node", "ax")),
    "mz": TypeInfo("exécutable DOS (MZ sans PE)", "application/x-dosexec", ("exe", "com", "ovl", "sys")),
    "macho": TypeInfo("binaire Mach-O", "application/x-mach-binary", ("dylib", "bundle", "so", "o", "node")),
    "java-class": TypeInfo("classe Java compilée", "application/java-vm", ("class",)),
    "wasm": TypeInfo("module WebAssembly", "application/wasm", ("wasm",)),
    "sqlite": TypeInfo("base SQLite 3", "application/vnd.sqlite3",
                       ("sqlite", "sqlite3", "db3", "s3db", "sl3", "gpkg", "mbtiles", "sqlitedb")),
    "parquet": TypeInfo("fichier Apache Parquet", "application/vnd.apache.parquet", ("parquet", "pq", "parq")),
    "cfb": TypeInfo("document composé OLE2 (doc, xls, ppt, msi...)", "application/x-ole-storage",
                    ("doc", "xls", "ppt", "msi", "msg", "dot", "xlt", "pot", "vsd", "pub", "mpp")),
    "riff": TypeInfo("conteneur RIFF", "application/octet-stream", ("riff", "rif", "ani", "rmi")),
    "wav": TypeInfo("audio WAV", "audio/wav", ("wav", "wave"), "riff"),
    "avi": TypeInfo("vidéo AVI", "video/x-msvideo", ("avi",), "riff"),
    "mp3": TypeInfo("audio MPEG (mp3)", "audio/mpeg", ("mp3", "mp2", "mpga")),
    "aac": TypeInfo("audio AAC (ADTS)", "audio/aac", ("aac",)),
    "flac": TypeInfo("audio FLAC", "audio/flac", ("flac",)),
    "ogg": TypeInfo("conteneur Ogg", "audio/ogg", ("ogg", "oga", "ogv", "opus", "spx", "ogx")),
    "midi": TypeInfo("musique MIDI", "audio/midi", ("mid", "midi")),
    "mp4": TypeInfo("conteneur MP4 (ISO BMFF)", "video/mp4",
                    ("mp4", "m4v", "m4a", "m4b", "m4p", "f4v", "f4a", "mpg4", "3gp", "3g2")),
    "m4a": TypeInfo("audio MPEG-4 (m4a)", "audio/mp4", ("m4a", "m4b", "m4p"), "mp4"),
    "mov": TypeInfo("vidéo QuickTime", "video/quicktime", ("mov", "qt"), "mp4"),
    "3gp": TypeInfo("vidéo 3GPP", "video/3gpp", ("3gp", "3g2", "3gpp"), "mp4"),
    "heif": TypeInfo("image HEIF/HEIC", "image/heif", ("heic", "heif", "hif"), "mp4"),
    "avif": TypeInfo("image AVIF", "image/avif", ("avif",), "mp4"),
    "matroska": TypeInfo("conteneur Matroska", "video/x-matroska", ("mkv", "mka", "mks", "mk3d")),
    "webm": TypeInfo("vidéo WebM", "video/webm", ("webm",), "matroska"),
    "mpeg-ts": TypeInfo("flux de transport MPEG", "video/mp2t", ("ts", "m2ts", "mts", "tsa", "tsv")),
    "mpeg-ps": TypeInfo("flux de programme MPEG", "video/mpeg", ("mpg", "mpeg", "vob", "m2p")),
    "woff": TypeInfo("police WOFF", "font/woff", ("woff",)),
    "woff2": TypeInfo("police WOFF2", "font/woff2", ("woff2",)),
    "otf": TypeInfo("police OpenType (CFF)", "font/otf", ("otf",)),
    "ttf": TypeInfo("police TrueType", "font/ttf", ("ttf", "tte", "dfont")),
    "ttc": TypeInfo("collection de polices", "font/collection", ("ttc", "otc")),
    "der": TypeInfo("structure ASN.1 DER (certificat ou clé)", "application/pkix-cert",
                    ("der", "cer", "crt", "p12", "pfx", "p7b", "p7c", "key", "pub", "csr")),
    "pyc": TypeInfo("bytecode Python", "application/x-python-code", ("pyc", "pyo")),
    "npy": TypeInfo("tableau NumPy", "application/x-npy", ("npy",)),
    "hdf5": TypeInfo("fichier HDF5", "application/x-hdf5", ("h5", "hdf5", "hdf", "he5", "nc", "h5ad")),
    "texte": TypeInfo("texte", "text/plain", ()),
    "donnees": TypeInfo("données binaires non reconnues", "application/octet-stream", ()),
    "vide": TypeInfo("fichier vide", "inode/x-empty", ()),
})

EXTENSIONS_TEXTE = frozenset("""
txt text md markdown rst adoc asciidoc py pyi pyw pyx pxd js mjs cjs ts tsx jsx json jsonc json5
jsonl ndjson geojson ipynb yaml yml toml ini cfg conf csv tsv psv xml xsd xsl xslt html htm xhtml
svg css scss sass less c h cc cpp cxx hpp hh hxx inl java kt kts scala sc go rs rb erb php pl pm t
sh bash zsh ksh fish ps1 psm1 psd1 bat cmd sql r rmd m mm swift lua vim el lisp clj cljs cljc edn
ex exs erl hrl hs lhs ml mli fs fsi fsx cs csx vb dart groovy gradle properties env log tex bib
sty cls rtf ps pem crt csr cer key pub p7b asc tf tfvars hcl nix dockerfile mk cmake in am ac po pot strings
srt vtt sub ics vcf eml mbox diff patch lock sum mod gitignore gitattributes editorconfig
rego proto graphql gql vue svelte astro jinja j2 tmpl tpl mustache hbs pug haml liquid
""".split())
EXTENSIONS_GENERIQUES = frozenset("bin dat data raw tmp temp bak old orig swp cache img db pack idx part".split())

PYC_PLAGES = ((3000, 3140, "3.0"), (3140, 3160, "3.1"), (3160, 3190, "3.2"), (3190, 3250, "3.3"),
              (3250, 3320, "3.4"), (3320, 3360, "3.5"), (3360, 3390, "3.6"), (3390, 3400, "3.7"),
              (3400, 3420, "3.8"), (3420, 3430, "3.9"), (3430, 3450, "3.10"))
PYC_DEUX = MappingProxyType({62211: "2.7", 62161: "2.6", 62131: "2.5", 62061: "2.4", 62021: "2.3"})

SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", 0, "png"), (b"GIF87a", 0, "gif"), (b"GIF89a", 0, "gif"),
    (b"7z\xbc\xaf\x27\x1c", 0, "7z"), (b"\xfd7zXZ\x00", 0, "xz"), (b"\x28\xb5\x2f\xfd", 0, "zstd"),
    (b"\x04\x22\x4d\x18", 0, "lz4"), (b"SQLite format 3\x00", 0, "sqlite"), (b"fLaC", 0, "flac"),
    (b"OggS\x00", 0, "ogg"), (b"wOFF", 0, "woff"), (b"wOF2", 0, "woff2"),     (b"ttcf\x00", 0, "ttc"), (b"\x93NUMPY", 0, "npy"), (b"\x89HDF\r\n\x1a\n", 0, "hdf5"), (b"MThd\x00\x00\x00\x06", 0, "midi"), (b"8BPS\x00", 0, "psd"),
    (b"MSCF\x00\x00\x00\x00", 0, "cab"), (b"Rar!\x1a\x07", 0, "rar"), (b"\xed\xab\xee\xdb", 0, "rpm"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0, "cfb"), (b"\x1f\x8b\x08", 0, "gzip"),
    (b"\x00\x00\x01\xba", 0, "mpeg-ps"), (b"\xff\xd8\xff", 0, "jpeg"), (b"II*\x00", 0, "tiff"),
    (b"MM\x00*", 0, "tiff"), (b"II+\x00", 0, "tiff"), (b"MM\x00+", 0, "tiff"),
    (b"\x00asm\x01\x00\x00\x00", 0, "wasm"), (b"CD001\x01", 32769, "iso"),
    (b"ustar\x0000", 257, "tar"), (b"ustar  \x00", 257, "tar"),
)
MARQUES_FTYP = MappingProxyType({
    "M4A ": "m4a", "M4B ": "m4a", "M4P ": "m4a", "qt  ": "mov", "heic": "heif", "heix": "heif",
    "mif1": "heif", "msf1": "heif", "hevc": "heif", "heim": "heif", "heis": "heif",
    "avif": "avif", "avis": "avif",
})
CLES_ZIP = (("word/document.xml", "docx"), ("xl/workbook.xml", "xlsx"), ("xl/workbook.bin", "xlsx"),
            ("ppt/presentation.xml", "pptx"), ("AndroidManifest.xml", "apk"),
            ("META-INF/MANIFEST.MF", "jar"))
MIMETYPES_ZIP = MappingProxyType({
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/vnd.oasis.opendocument.graphics": "odg",
    "application/epub+zip": "epub",
})
MACHINES_ELF = MappingProxyType({3: "x86", 0x28: "arm", 0x3E: "x86-64", 0xB7: "aarch64",
                                 0xF3: "riscv", 0x08: "mips", 0x14: "powerpc", 0x15: "powerpc64",
                                 0x16: "s390", 0x2B: "sparcv9", 0x102: "loongarch"})
MACHINES_PE = MappingProxyType({0x14C: "i386", 0x8664: "x86-64", 0xAA64: "arm64", 0x1C0: "arm",
                                0x1C4: "armnt", 0x200: "ia64", 0xEBC: "efi-bytecode"})
ALIAS_MIME = MappingProxyType({
    "application/x-sharedlib": ("elf",), "application/x-pie-executable": ("elf",),
    "application/x-object": ("elf",), "application/x-coredump": ("elf",),
    "application/x-dosexec": ("pe", "mz"), "audio/x-wav": ("wav",), "font/sfnt": ("ttf", "otf"),
    "application/vnd.ms-opentype": ("otf",), "application/x-font-otf": ("otf",),
    "application/x-font-ttf": ("ttf",), "application/x-gzip": ("gzip",), "image/x-icon": ("ico",),
    "image/x-ms-bmp": ("bmp",), "application/x-sqlite3": ("sqlite",), "audio/x-flac": ("flac",),
    "application/x-java-applet": ("java-class",), "application/x-bytecode.python": ("pyc",),
    "text/x-bytecode.python": ("pyc",), "image/svg+xml": ("texte",), "application/xml": ("texte",),
    "application/json": ("texte",), "application/javascript": ("texte",), "video/ogg": ("ogg",),
    "application/ogg": ("ogg",), "audio/mp4": ("m4a", "mp4"), "application/x-ustar": ("tar",),
})
ALIAS_EXTENSIONS = MappingProxyType({"db": ("sqlite", "cfb")})
DEBITS_MPEG1_L3 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)


class EntreeInvalide(Exception):
    """Chemin donné inexistant ou option incohérente (code 2)."""


@dataclass
class Detection:
    """Résultat de l'identification d'un contenu."""

    cle: str
    details: dict[str, Any] = field(default_factory=dict)
    certitude: str = "forte"


@dataclass(frozen=True)
class Fichier:
    """Accès borné à un fichier ouvert : tête lue, taille, flux pour les lectures ciblées."""

    chemin: Path
    flux: BinaryIO
    tete: bytes
    taille: int

    def lire(self, position: int, longueur: int) -> bytes:
        """Lit `longueur` octets à `position` (dans la tête si possible)."""
        if position + longueur <= len(self.tete):
            return self.tete[position:position + longueur]
        if position >= self.taille:
            return b""
        self.flux.seek(position)
        return self.flux.read(longueur)


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


# --------------------------------------------------------------------------- détecteurs


def detecter_signature(f: Fichier) -> Detection | None:
    """Signatures fixes à une position connue ; « %PDF- » est admis dans le premier Kio."""
    for octets, position, cle in SIGNATURES:
        if f.lire(position, len(octets)) == octets:
            return Detection(cle)
    position = f.tete[:1024].find(b"%PDF-")
    if position < 0:
        return None
    version = f.tete[position + 5:position + 8].decode("ascii", "replace")
    if position == 0:
        return Detection("pdf", {"version": version})
    queue = f.lire(max(0, f.taille - 1024), 1024)
    if b"%%EOF" in queue:
        return Detection("pdf", {"version": version, "decalage": position}, "moyenne")
    return None


def detecter_elf(f: Fichier) -> Detection | None:
    """ELF : classe, boutisme, type d'objet, machine."""
    if not f.tete.startswith(b"\x7fELF") or len(f.tete) < 20:
        return None
    classe = {1: "32 bits", 2: "64 bits"}.get(f.tete[4], "?")
    ordre = "<" if f.tete[5] == 1 else ">"
    genre, machine = struct.unpack(ordre + "HH", f.tete[16:20])
    genres = {1: "objet relogeable", 2: "exécutable", 3: "objet partagé ou PIE", 4: "image mémoire"}
    return Detection("elf", {"classe": classe, "boutisme": "petit" if ordre == "<" else "grand",
                             "genre": genres.get(genre, str(genre)),
                             "machine": MACHINES_ELF.get(machine, hex(machine))})


def detecter_pe(f: Fichier) -> Detection | None:
    """MZ, puis signature « PE\\0\\0 » à e_lfanew : machine, DLL ou exécutable."""
    if not f.tete.startswith(b"MZ") or len(f.tete) < 64:
        return None
    decalage = struct.unpack("<I", f.tete[0x3C:0x40])[0]
    entete = f.lire(decalage, 24) if 0 < decalage < f.taille else b""
    if not entete.startswith(b"PE\x00\x00") or len(entete) < 24:
        return Detection("mz", {"e_lfanew": decalage}, "moyenne")
    machine, caracteristiques = struct.unpack("<H16xH", entete[4:24])
    return Detection("pe", {"machine": MACHINES_PE.get(machine, hex(machine)),
                            "dll": bool(caracteristiques & 0x2000)})


def detecter_macho(f: Fichier) -> Detection | None:
    """Mach-O (32/64, deux boutismes) ; CAFEBABE départagé entre Mach-O universel et classe Java."""
    tete = f.tete[:8]
    simples = {b"\xfe\xed\xfa\xce": "32 bits grand-boutiste", b"\xfe\xed\xfa\xcf": "64 bits grand-boutiste",
               b"\xce\xfa\xed\xfe": "32 bits petit-boutiste", b"\xcf\xfa\xed\xfe": "64 bits petit-boutiste"}
    if tete[:4] in simples:
        return Detection("macho", {"variante": simples[tete[:4]]})
    if tete[:4] != b"\xca\xfe\xba\xbe" or len(tete) < 8:
        return None
    valeur = struct.unpack(">I", tete[4:8])[0]
    if valeur < 45:
        return Detection("macho", {"variante": "universel", "architectures": valeur})
    mineure, majeure = struct.unpack(">HH", tete[4:8])
    return Detection("java-class", {"version_classe": f"{majeure}.{mineure}",
                                    "java": majeure - 44 if majeure >= 49 else None})


def detecter_ico(f: Fichier) -> Detection | None:
    """ICO/CUR : réservé nul, type 1 ou 2, nombre d'images plausible, entrée cohérente."""
    t = f.tete
    if len(t) < 22 or t[:2] != b"\x00\x00" or t[2:4] not in (b"\x01\x00", b"\x02\x00"):
        return None
    nombre = struct.unpack("<H", t[4:6])[0]
    taille, decalage = struct.unpack("<II", t[14:22])
    if not 0 < nombre <= 256 or t[9] != 0 or taille == 0 or decalage < 6 + 16 * nombre:
        return None
    return Detection("ico" if t[2] == 1 else "cur", {"images": nombre})


def detecter_bmp(f: Fichier) -> Detection | None:
    """BMP : « BM », réservés nuls, taille d'en-tête DIB connue."""
    t = f.tete
    if len(t) < 18 or not t.startswith(b"BM") or t[6:10] != b"\x00\x00\x00\x00":
        return None
    dib = struct.unpack("<I", t[14:18])[0]
    return Detection("bmp", {"entete_dib": dib}) if dib in (12, 40, 52, 56, 64, 108, 124) else None


def detecter_riff(f: Fichier) -> Detection | None:
    """RIFF (et RIFX grand-boutiste, RF64/BW64 au-delà de 4 Gio) : forme WAVE, AVI ou WEBP."""
    if f.tete[:4] not in (b"RIFF", b"RIFX", b"RF64", b"BW64") or len(f.tete) < 12:
        return None
    forme = f.tete[8:12]
    cle = {b"WAVE": "wav", b"AVI ": "avi", b"WEBP": "webp"}.get(forme, "riff")
    return Detection(cle, {"forme": forme.decode("latin-1"), "variante": f.tete[:4].decode("latin-1")})


def detecter_ftyp(f: Fichier) -> Detection | None:
    """ISO BMFF : boîte « ftyp » à l'octet 4, marque majeure à l'octet 8."""
    if f.tete[4:8] != b"ftyp" or len(f.tete) < 12:
        return None
    marque = f.tete[8:12].decode("latin-1")
    cle = MARQUES_FTYP.get(marque, "3gp" if marque.startswith("3g") else "mp4")
    return Detection(cle, {"marque": marque})


def detecter_ebml(f: Fichier) -> Detection | None:
    """Matroska/WebM : en-tête EBML, DocType cherché dans les premiers octets."""
    if not f.tete.startswith(b"\x1a\x45\xdf\xa3"):
        return None
    return Detection("webm" if b"webm" in f.tete[:64] else "matroska")


def detecter_bzip2(f: Fichier) -> Detection | None:
    """bzip2 : « BZh » + niveau 1-9 + magie de bloc (ou de fin de flux)."""
    t = f.tete
    if len(t) >= 10 and t[:3] == b"BZh" and 0x31 <= t[3] <= 0x39 and t[4:10] in (
            b"\x31\x41\x59\x26\x53\x59", b"\x17\x72\x45\x38\x50\x90"):
        return Detection("bzip2", {"niveau": t[3] - 0x30})
    return None


def detecter_ar(f: Fichier) -> Detection | None:
    """Archive ar ; paquet Debian si le premier membre est debian-binary."""
    if not f.tete.startswith(b"!<arch>\n"):
        return None
    return Detection("deb" if f.tete[8:21] == b"debian-binary" else "ar")


def detecter_mpegts(f: Fichier) -> Detection | None:
    """MPEG-TS : synchronisation 0x47 tous les 188 octets (quatre paquets) et octets nuls."""
    t = f.tete
    if len(t) >= 565 and t[0] == t[188] == t[376] == t[564] == 0x47 and b"\x00" in t[:1024]:
        return Detection("mpeg-ts", certitude="moyenne")
    return None


def detecter_parquet(f: Fichier) -> Detection | None:
    """Parquet : « PAR1 » en tête et en queue."""
    if f.tete.startswith(b"PAR1") and f.taille >= 12 and f.lire(f.taille - 4, 4) == b"PAR1":
        return Detection("parquet")
    return None


def detecter_pyc(f: Fichier) -> Detection | None:
    """.pyc : nombre magique sur 2 octets + « \\r\\n » ; version de Python déduite."""
    t = f.tete
    if len(t) < 16 or t[2:4] != b"\r\n":
        return None
    nombre = struct.unpack("<H", t[:2])[0]
    version = version_pyc(nombre)
    if version is None:
        return None
    drapeaux = struct.unpack("<I", t[4:8])[0] if 3392 <= nombre < 62000 else 0
    return Detection("pyc", {"nombre_magique": nombre, "python": version,
                             "validation": "hachage" if drapeaux & 1 else "horodatage"})


def version_pyc(nombre: int) -> str | None:
    """Version de Python d'après le nombre magique (en-tête pycore_magic_number.h)."""
    if nombre in PYC_DEUX:
        return PYC_DEUX[nombre]
    for bas, haut, version in PYC_PLAGES:
        if bas <= nombre < haut:
            return version
    if 3450 <= nombre < 4500:
        return f"3.{(nombre - 2900) // 50}"
    return None


def detecter_tar_v7(f: Fichier) -> Detection | None:
    """tar sans magie ustar : somme de contrôle de l'en-tête de 512 octets valide."""
    t = f.tete
    if len(t) < 512 or not t[0] or t[0] in (0x2F,):
        return None
    try:
        attendu = int(t[148:156].split(b"\x00")[0].strip() or b"-1", 8)
    except ValueError:
        return None
    calcule = sum(t[:148]) + 8 * 32 + sum(t[156:512])
    return Detection("tar", {"format": "v7"}, "moyenne") if attendu == calcule else None


def detecter_der(f: Fichier) -> Detection | None:
    """ASN.1 DER : SEQUENCE à longueur longue contenant une SEQUENCE (ou un INTEGER)."""
    t = f.tete
    if len(t) < 8 or t[0] != 0x30 or t[1] not in (0x81, 0x82, 0x83):
        return None
    octets = t[1] - 0x80
    longueur = int.from_bytes(t[2:2 + octets], "big")
    if 2 + octets + longueur != f.taille or t[2 + octets] not in (0x30, 0x02):
        return None
    return Detection("der", certitude="moyenne")


def detecter_ttf(f: Fichier) -> Detection | None:
    """TrueType (0x00010000, « true ») ou OpenType CFF (« OTTO ») : nombre de tables plausible."""
    t = f.tete
    if len(t) < 12 or t[:4] not in (b"\x00\x01\x00\x00", b"true", SIGNATURE_OTF.encode()):
        return None
    tables = struct.unpack(">H", t[4:6])[0]
    if not 4 <= tables <= 64:
        return None
    return Detection("otf" if t[:4] == SIGNATURE_OTF.encode() else "ttf", {"tables": tables})


def detecter_audio_mpeg(f: Fichier) -> Detection | None:
    """mp3 : étiquette ID3v2 (sautée, puis contenu réexaminé) ou deux trames enchaînées."""
    debut = 0
    if f.tete.startswith(b"ID3") and len(f.tete) >= 10:
        taille = sum((f.tete[6 + i] & 0x7F) << (7 * (3 - i)) for i in range(4))
        debut = 10 + taille + (10 if f.tete[5] & 0x10 else 0)
        if f.lire(debut, 4) == b"fLaC":
            return Detection("flac", {"etiquette_id3": True})
    entete = f.lire(debut, 4)
    if len(entete) < 4 or entete[0] != 0xFF or entete[1] & 0xE0 != 0xE0:
        return Detection("mp3", {"etiquette_id3": True}, "moyenne") if debut else None
    if entete[1] & 0x06 == 0:
        return Detection("aac", certitude="moyenne") if entete[1] & 0xF6 == 0xF0 else None
    longueur = longueur_trame_mpeg(entete)
    suivante = f.lire(debut + longueur, 2) if longueur else b""
    if len(suivante) == 2 and suivante[0] == 0xFF and suivante[1] & 0xE0 == 0xE0:
        return Detection("mp3", {"etiquette_id3": bool(debut)})
    return Detection("mp3", {"etiquette_id3": True}, "moyenne") if debut else None


def longueur_trame_mpeg(entete: bytes) -> int:
    """Longueur d'une trame MPEG-1 couche III (0 si non calculable ou invalide)."""
    version, couche = (entete[1] >> 3) & 3, (entete[1] >> 1) & 3
    indice_debit, indice_freq = entete[2] >> 4, (entete[2] >> 2) & 3
    if version == 1 or couche == 0 or indice_debit in (0, 15) or indice_freq == 3:
        return 0
    if version != 3 or couche != 1:
        return 0
    frequence = (44100, 48000, 32000)[indice_freq]
    return 144 * DEBITS_MPEG1_L3[indice_debit] * 1000 // frequence + ((entete[2] >> 1) & 1)


def detecter_zip(f: Fichier) -> Detection | None:
    """ZIP, puis sous-type d'après le contenu (mimetype, fichiers caractéristiques)."""
    if f.tete[:4] not in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return None
    try:
        with zipfile.ZipFile(f.chemin) as archive:
            noms = archive.namelist()
            mimetype = lire_mimetype_zip(archive, noms)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError) as exc:
        return Detection("zip", {"erreur": f"archive illisible : {exc}"}, "moyenne")
    if mimetype in MIMETYPES_ZIP:
        return Detection(MIMETYPES_ZIP[mimetype], {"entrees": len(noms), "mimetype": mimetype})
    if any(n.endswith(".dist-info/WHEEL") for n in noms):
        return Detection("whl", {"entrees": len(noms)})
    ensemble = set(noms)
    for nom, cle in CLES_ZIP:
        if nom in ensemble:
            return Detection(cle, {"entrees": len(noms), "indice": nom})
    return Detection("zip", {"entrees": len(noms)})


def detecter_zip_prefixe(f: Fichier) -> Detection | None:
    """ZIP précédé de données (auto-extractible, préfixe) : fin de répertoire central en queue."""
    queue = f.lire(max(0, f.taille - 65558), 65558)
    if b"PK\x05\x06" not in queue:
        return None
    try:
        with zipfile.ZipFile(f.chemin) as archive:
            entrees = len(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError):
        return None
    return Detection("zip", {"entrees": entrees, "donnees_en_tete": True}, "moyenne")


def lire_mimetype_zip(archive: zipfile.ZipFile, noms: Sequence[str]) -> str:
    """Contenu du membre « mimetype » (OpenDocument, EPUB), borné à 100 octets."""
    if "mimetype" not in noms:
        return ""
    with archive.open("mimetype") as membre:
        return membre.read(100).decode("ascii", "replace").strip()


DETECTEURS: tuple[Callable[[Fichier], Detection | None], ...] = (
    detecter_zip, detecter_elf, detecter_pe, detecter_macho, detecter_riff, detecter_ftyp,
    detecter_ebml, detecter_bzip2, detecter_ar, detecter_parquet, detecter_pyc,
    detecter_signature, detecter_ico, detecter_bmp, detecter_ttf, detecter_audio_mpeg,
    detecter_mpegts, detecter_tar_v7, detecter_der, detecter_zip_prefixe,
)


# --------------------------------------------------------------------------- texte


def nature_texte(tete: bytes, complet: bool) -> Detection:
    """Texte (BOM, utf-8, 8 bits, utf-16 sans BOM) ou données binaires."""
    for bom, codage in ((codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
                        (codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                        (codecs.BOM_UTF16_BE, "utf-16-be")):
        if tete.startswith(bom):
            return Detection("texte", {"encodage": codage, "bom": True,
                                       "genre": genre_texte(tete[len(bom):].decode(codage, "replace"))})
    utf16 = deviner_utf16(tete)
    if utf16:
        return Detection("texte", {"encodage": utf16, "bom": False}, "moyenne")
    if b"\x00" in tete:
        return Detection("donnees", certitude="moyenne")
    texte = decoder_utf8(tete, complet)
    if texte is not None:
        codage = "ascii" if tete.isascii() else "utf-8"
        return Detection("texte", {"encodage": codage, "genre": genre_texte(texte)})
    controles = sum(1 for o in tete if o < 32 and o not in (9, 10, 12, 13, 27) or o == 127)
    if controles <= len(tete) // 100:
        return Detection("texte", {"encodage": "8 bits non utf-8",
                                   "genre": genre_texte(tete.decode("latin-1"))}, "moyenne")
    return Detection("donnees", certitude="moyenne")


def decoder_utf8(tete: bytes, complet: bool) -> str | None:
    """Décode en utf-8 ; tolère une séquence coupée en fin de tête tronquée."""
    try:
        return tete.decode("utf-8")
    except UnicodeDecodeError as exc:
        if not complet and exc.start >= len(tete) - 3 and exc.reason == "unexpected end of data":
            return tete[:exc.start].decode("utf-8")
        return None


def deviner_utf16(tete: bytes) -> str | None:
    """utf-16 sans BOM : un octet sur deux nul, l'autre imprimable (texte latin)."""
    paires = len(tete) // 2
    if paires < 8:
        return None
    for nom, nuls, autres in (("utf-16-le", tete[1::2], tete[0::2]), ("utf-16-be", tete[0::2], tete[1::2])):
        if nuls.count(0) >= paires * 0.9 and sum(1 for o in autres if 32 <= o < 127 or o in (9, 10, 13)) >= paires * 0.9:
            return nom
    return None


def genre_texte(texte: str) -> str:
    """Genre d'un texte d'après son début : script, xml, svg, html, json, pem, rtf, postscript."""
    debut = texte.lstrip()[:1024]
    minus = debut.lower()
    if debut.startswith("#!"):
        return "script " + interprete(debut.splitlines()[0])
    for prefixe, genre in (("-----begin ", "pem"), ("{\\rtf", "rtf"), ("%!ps", "postscript")):
        if minus.startswith(prefixe):
            return genre
    if "<svg" in minus and (minus.startswith("<?xml") or minus.startswith("<svg") or minus.startswith("<!--")):
        return "svg"
    if minus.startswith("<?xml"):
        return "xml"
    if minus.startswith("<!doctype html") or minus.startswith("<html"):
        return "html"
    if debut[:1] in "{[" and debut:
        return "json probable"
    return "texte"


def interprete(ligne: str) -> str:
    """Nom de l'interpréteur d'une ligne shebang (« #!/usr/bin/env python3 » → python3)."""
    morceaux = ligne[2:].strip().split()
    if not morceaux:
        return "?"
    nom = morceaux[0].rsplit("/", 1)[-1]
    if nom == "env" and len(morceaux) > 1:
        options = [m for m in morceaux[1:] if not m.startswith("-")]
        return options[0] if options else "?"
    return nom


# --------------------------------------------------------------------------- verdict


def extension_de(nom: str) -> str:
    """Extension en minuscules ; « libx.so.1.2 » donne « so » ; « .bashrc » n'en a pas."""
    if nom.startswith(".") and nom.count(".") == 1:
        return ""
    so = re.search(r"\.so(\.\d+)+$", nom)
    if so:
        return "so"
    return nom.rsplit(".", 1)[-1].lower() if "." in nom else ""


def familles_de(cle: str) -> set[str]:
    """Le type et ses ancêtres (docx → zip ; webm → matroska ; m4a → mp4)."""
    resultat = {cle}
    while TYPES[cle].famille:
        cle = TYPES[cle].famille
        resultat.add(cle)
    return resultat


def types_annonces(ext: str) -> list[str]:
    """Types dont l'extension figure dans la liste concordante."""
    annonces = [cle for cle, info in TYPES.items() if ext in info.extensions]
    return annonces + (["texte"] if ext in EXTENSIONS_TEXTE else [])


def juger_extension(ext: str, cle: str) -> tuple[str, str]:
    """Verdict (concordante, compatible, contradiction, indeterminee) et explication."""
    if not ext or ext in EXTENSIONS_GENERIQUES or cle == "vide":
        return "indeterminee", "extension absente ou générique, ou fichier vide"
    annonces = types_annonces(ext)
    if not annonces:
        return "indeterminee", f"extension .{ext} inconnue de l'outil"
    if cle in annonces:
        return "concordante", ""
    if familles_de(cle) & set(annonces):
        return "compatible", f".{ext} désigne la famille du contenu ({TYPES[cle].description})"
    return "contradiction", (f"l'extension .{ext} annonce {', '.join(annonces)}, "
                             f"le contenu est {cle} ({TYPES[cle].description})")


# --------------------------------------------------------------------------- bibliothèques


def avis_bibliotheque(moteur: str, tete: bytes) -> dict[str, str]:
    """Extension et MIME proposés par la bibliothèque optionnelle (chaînes vides si aucun)."""
    if moteur == "puremagic":
        try:
            resultats = puremagic.magic_string(tete)
        except (puremagic.PureError, ValueError):
            return {"extension": "", "mime": ""}
        meilleur = resultats[0] if resultats else None
        return {"extension": (meilleur.extension or "").lstrip(".").lower() if meilleur else "",
                "mime": meilleur.mime_type if meilleur else ""}
    if moteur == "filetype":
        genre = filetype.guess(tete)
        return {"extension": genre.extension if genre else "", "mime": genre.mime if genre else ""}
    try:
        return {"extension": "", "mime": magic.from_buffer(tete, mime=True)}
    except getattr(magic, "MagicException", OSError):
        return {"extension": "", "mime": ""}


def types_selon_bibliotheque(avis: dict[str, str]) -> set[str]:
    """Types de l'outil que désigne l'avis externe (par extension, MIME ou alias)."""
    ext, mime = avis["extension"], avis["mime"]
    mime = "" if mime in ("application/octet-stream", "data") else mime
    types = set(types_annonces(ext)) if ext else set()
    types.update(ALIAS_EXTENSIONS.get(ext, ()))
    types.update(cle for cle, info in TYPES.items() if mime and info.mime == mime and cle != "donnees")
    types.update(ALIAS_MIME.get(mime, ()))
    if mime.startswith("text/"):
        types.add("texte")
    return types


def accord(cle: str, avis: dict[str, str]) -> bool | None:
    """Accord si l'avis désigne le type trouvé ou une de ses familles ; None sans avis comparable."""
    types = types_selon_bibliotheque(avis)
    if not types:
        return None
    return bool(types & familles_de(cle))


def choisir_moteur(demande: str) -> str:
    """Première bibliothèque disponible (puremagic, filetype, magic), ou stdlib."""
    disponibles = {"puremagic": puremagic, "filetype": filetype, "magic": magic}
    if demande == "stdlib":
        return "stdlib"
    if demande != "auto":
        if disponibles[demande] is None:
            raise EntreeInvalide(f"--moteur {demande} demandé mais la bibliothèque est absente")
        return demande
    for nom in ORDRE_BIBLIOTHEQUES:
        if disponibles[nom] is not None:
            return nom
    print("puremagic, filetype et python-magic absents : signatures internes seules "
          "(moteur stdlib)", file=sys.stderr)
    return "stdlib"


# --------------------------------------------------------------------------- examen


def identifier(chemin: Path, moteur: str) -> dict[str, Any]:
    """Fiche complète d'un fichier : type, détails, verdict d'extension, avis externe."""
    with chemin.open("rb") as flux:
        tete = flux.read(TAILLE_TETE)
        taille = os.fstat(flux.fileno()).st_size
        f = Fichier(chemin, flux, tete, taille)
        detection = Detection("vide") if taille == 0 else premier_detecteur(f)
    ext = extension_de(chemin.name)
    fiche: dict[str, Any] = {"chemin": str(chemin), "taille": taille, "extension": ext or None}
    avis = avis_bibliotheque(moteur, tete) if moteur != "stdlib" and taille else None
    if avis and detection.cle == "donnees" and avis["extension"]:
        detection = Detection("donnees", {"selon_bibliotheque": avis}, "faible")
        verdict, explication = juger_extension_externe(ext, avis["extension"])
    else:
        verdict, explication = juger_extension(ext, detection.cle)
    info = TYPES[detection.cle]
    fiche.update({"type": detection.cle, "description": info.description, "mime": info.mime,
                  "certitude": detection.certitude, "details": detection.details,
                  "verdict_extension": verdict, "explication": explication})
    if avis is not None:
        fiche["avis_bibliotheque"] = dict(avis, accord=accord(detection.cle, avis))
    return fiche


def premier_detecteur(f: Fichier) -> Detection:
    """Premier détecteur qui reconnaît le contenu, sinon analyse texte/binaire."""
    for detecteur in DETECTEURS:
        trouve = detecteur(f)
        if trouve is not None:
            return trouve
    return nature_texte(f.tete, complet=len(f.tete) >= f.taille)


def juger_extension_externe(ext: str, ext_bibliotheque: str) -> tuple[str, str]:
    """Verdict quand seul l'avis de la bibliothèque identifie le contenu."""
    if not ext or ext in EXTENSIONS_GENERIQUES:
        return "indeterminee", "extension absente ou générique"
    if ext == ext_bibliotheque:
        return "concordante", "selon la bibliothèque optionnelle"
    if types_annonces(ext):
        return "contradiction", (f"l'extension .{ext} annonce {', '.join(types_annonces(ext))}, "
                                 f"la bibliothèque reconnaît .{ext_bibliotheque}")
    return "indeterminee", f"extension .{ext} inconnue de l'outil"


def collecter(cibles: Sequence[str], base: Path,
              max_fichiers: int) -> tuple[list[tuple[Path, str]], list[dict[str, str]], bool]:
    """(chemin, affichage) à examiner — dossiers parcourus sans suivre les liens —, erreurs, troncature."""
    fichiers: list[tuple[Path, str]] = []
    erreurs: list[dict[str, str]] = []
    for texte in cibles:
        chemin = Path(texte) if Path(texte).is_absolute() else base / texte
        if not chemin.exists():
            erreurs.append({"chemin": texte, "erreur": "chemin inexistant"})
        elif chemin.is_dir():
            fichiers.extend((f, str(Path(texte) / f.relative_to(chemin)))
                            for f in parcourir(chemin, max_fichiers - len(fichiers)))
        elif chemin.is_file():
            fichiers.append((chemin, texte))
        else:
            erreurs.append({"chemin": texte, "erreur": "ni fichier régulier ni dossier (tube, périphérique)"})
        if len(fichiers) >= max_fichiers:
            return fichiers[:max_fichiers], erreurs, True
    return fichiers, erreurs, False


def parcourir(dossier: Path, limite: int) -> list[Path]:
    """Fichiers réguliers sous un dossier, triés, sans .git/.hg/.svn, au plus `limite`."""
    trouves: list[Path] = []
    for racine, sous_dossiers, noms in os.walk(dossier):
        sous_dossiers[:] = sorted(d for d in sous_dossiers if d not in DOSSIERS_IGNORES)
        for nom in sorted(noms):
            chemin = Path(racine) / nom
            if chemin.is_file() and not chemin.is_symlink():
                trouves.append(chemin)
                if len(trouves) >= limite:
                    return trouves
    return trouves


def examiner(fichiers: Sequence[tuple[Path, str]],
             moteur: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Identifie chaque fichier ; un fichier illisible est consigné, pas fatal."""
    fiches, illisibles = [], []
    for chemin, affichage in fichiers:
        try:
            fiche = identifier(chemin, moteur)
        except OSError as exc:
            illisibles.append({"chemin": affichage, "erreur": exc.strerror or str(exc)})
            continue
        fiche["chemin"] = affichage
        fiches.append(fiche)
    return fiches, illisibles


# --------------------------------------------------------------------------- rapport


def construire_rapport(fiches: list[dict[str, Any]], erreurs: list[dict[str, str]],
                       illisibles: list[dict[str, str]], moteur: str, tronque: bool) -> dict[str, Any]:
    """Assemble le rapport JSON."""
    par_type: dict[str, int] = {}
    par_verdict: dict[str, int] = {}
    for f in fiches:
        par_type[f["type"]] = par_type.get(f["type"], 0) + 1
        par_verdict[f["verdict_extension"]] = par_verdict.get(f["verdict_extension"], 0) + 1
    rapport: dict[str, Any] = {
        "outil": Path(__file__).stem,
        "python": platform.python_version(),
        "moteur": moteur,
        "denominateur": len(fiches),
        "examines": [f["chemin"] for f in fiches[:MAX_EXAMINES]],
        "examines_tronques": len(fiches) > MAX_EXAMINES,
        "parcours_tronque": tronque,
        "par_type": dict(sorted(par_type.items(), key=lambda kv: -kv[1])),
        "par_verdict": par_verdict,
        "contradictions": [f for f in fiches if f["verdict_extension"] == "contradiction"],
        "fichiers": fiches,
        "entrees_invalides": erreurs,
        "illisibles": illisibles,
    }
    if moteur != "stdlib":
        desaccords = [f["chemin"] for f in fiches if f.get("avis_bibliotheque", {}).get("accord") is False]
        rapport["desaccords_bibliotheque"] = desaccords
    return rapport


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Une ligne par fichier, contradictions marquées, puis le bilan."""
    for f in rapport["fichiers"]:
        marque = "!!" if f["verdict_extension"] == "contradiction" else "  "
        precision = ", ".join(str(v) for k, v in f["details"].items()
                              if k in ("genre", "encodage", "python", "machine", "marque") and v != "texte")
        print(f"{marque} {f['chemin']} : {f['type']} ({f['description']}{', ' + precision if precision else ''}) — extension "
              f"{f['verdict_extension']}{' : ' + f['explication'] if marque == '!!' else ''}")
    print(f"Bilan : {rapport['denominateur']} fichiers, {len(rapport['contradictions'])} contradiction(s), "
          f"types : {rapport['par_type']}")


def afficher_json(objet: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(objet, ensure_ascii=False, indent=2))


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande, aide en français."""
    parseur = argparse.ArgumentParser(
        prog=Path(__file__).name,
        description="Identifie le vrai type des fichiers par leurs octets et signale les "
                    "extensions qui mentent. Code 1 si une extension contredit le contenu.",
        epilog=f"Exemple : python {RACINE.name}/{Path(__file__).name} telechargements/ "
               "rapport.pdf --json",
    )
    parseur.add_argument("chemins", nargs="+", help="fichiers ou dossiers (parcourus récursivement)")
    parseur.add_argument("--moteur", choices=("auto", "stdlib") + ORDRE_BIBLIOTHEQUES, default="auto",
                         help="auto : signatures internes + avis de puremagic, filetype ou "
                              "python-magic (le premier installé)")
    parseur.add_argument("--max-fichiers", type=int, default=100_000,
                         help="fichiers examinés au plus (défaut : 100 000)")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="dossier de base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : 0 rien à signaler, 1 contradiction, 2 entrée invalide, 3 rien à examiner."""
    args = construire_parseur().parse_args(argv)
    base = args.racine if args.racine is not None else Path.cwd()
    try:
        moteur = choisir_moteur(args.moteur)
    except EntreeInvalide as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
    fichiers, erreurs, tronque = collecter(args.chemins, base, max(1, args.max_fichiers))
    fiches, illisibles = examiner(fichiers, moteur)
    rapport = construire_rapport(fiches, erreurs, illisibles, moteur, tronque)
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    for e in erreurs + illisibles:
        print(f"erreur : {e['chemin']} : {e['erreur']}", file=sys.stderr)
    code = 2 if erreurs else (1 if rapport["contradictions"] else 0)
    if not fiches and not erreurs:
        print("dénominateur nul : aucun fichier lisible, rien à examiner", file=sys.stderr)
        code = 3
    rapport["code_sortie"] = code
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
