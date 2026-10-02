"""Un wheel falsifié s'installe sans un mot : mesuré dans cette session, après ajout
d'une ligne à pysrt/__init__.py dans pysrt-1.1.2-py3-none-any.whl (zip réécrit, RECORD
inchangé), zipfile.testzip() rend None, `pip install` (pip 26.2.1) et `uv pip install`
(uv 0.12.21) l'installent tous deux avec le code 0. Seul le recalcul des empreintes
du RECORD trahit la falsification ; cet outil le fait, fichier par fichier.

QUESTION
    Ce wheel ou cette sdist est-il intègre, que contient-il, et s'installe-t-il sur
    cet interpréteur ?
MESURE
    Wheel (.whl) : nom de fichier (nom, version, build, tags), METADATA (Name, Version,
    Requires-Python, Requires-Dist, Provides-Extra, License-Expression, License-File),
    WHEEL (Wheel-Version, Root-Is-Purelib, Tag), RECORD dont chaque empreinte est
    recalculée sur le contenu décompressé et comparée, taille comprise ; fichiers
    listés mais absents, fichiers présents hors RECORD, algorithmes interdits (md5,
    sha1), entrées en double, chemins absolus ou remontant par « .. », liens
    symboliques, entrées chiffrées, total décompressé et nombre de membres bornés
    avant toute lecture (bombe zip), CRC vérifié par la lecture complète ;
    entry_points.txt (console_scripts, gui_scripts, autres groupes) ; sous-dossiers
    .data (scripts, headers, data, purelib, platlib) ; cohérence nom/version entre
    nom de fichier, dossier .dist-info et METADATA ; licences PEP 639 présentes sous
    .dist-info/licenses ; compatibilité des tags avec l'interpréteur courant et
    satisfaction de Requires-Python. Sdist (.tar.gz, .tgz, .tar.bz2, .tar.xz, .zip) :
    PKG-INFO, pyproject.toml ([build-system], [project] name/version/dynamic),
    présence de setup.py, nom PEP 625, dossier racine unique, membres dangereux
    (chemins traversants, liens sortant de l'archive, périphériques), taille bornée.
    Avec --installes : chaque *.dist-info d'un site-packages, RECORD recalculé sur les
    fichiers installés. L'empreinte sha256 de chaque archive est rapportée.
    Si packaging est importable, les tags supportés et Requires-Python viennent de
    packaging ; sinon d'une table stdlib qui reproduit packaging.tags. Si pkginfo est
    importable, nom, version et Requires-Dist sont relus par pkginfo et comparés.
HYPOTHÈSES
    Les archives suivent la spécification des wheels (PEP 427 et suivantes) et des
    sdists (PEP 517, 625, 643). Le RECORD est la référence : une empreinte conforme
    prouve que le contenu n'a pas changé depuis l'écriture du RECORD, pas que
    l'auteur est légitime (aucune signature n'est vérifiée).
LIMITES
    Une archive refaite avec un RECORD recalculé passe pour intègre : seule une
    empreinte de l'archive venue d'ailleurs (lockfile, index) le révèle. Les sdists
    n'ont pas de RECORD : leur intégrité se limite à la lisibilité et à la cohérence
    PKG-INFO/pyproject. Sans packaging, les tags musllinux ne sont pas calculés et
    les tables macOS et Windows reproduisent packaging sans avoir été éprouvées ici.
    Les dépendances (Requires-Dist) sont listées, jamais résolues. En mode
    --installes, les fichiers listés hors du dossier donné (scripts de bin/) ne sont
    pas vérifiés : ils sont comptés à part ; les fichiers ajoutés au site-packages
    sans figurer dans aucun RECORD ne sont pas vus. Une archive .zip ou .tar.* sans
    PKG-INFO trouvée dans un dossier est ignorée (listée), pas jugée ; dans un
    dossier, les liens symboliques ne sont pas suivis.
CONTRE-EXEMPLES
    Constaté : le même pysrt-1.1.2 falsifié, mais avec un RECORD recalculé
    (empreinte et taille de pysrt/__init__.py réécrites), est déclaré INTÈGRE avec le
    code 0. L'outil prouve la cohérence interne de l'archive, pas sa conformité à
    l'archive publiée : seule la comparaison de sha256_archive (5cb5624a… au lieu de
    a71402c3…) avec l'empreinte d'un lockfile ou de l'index le révèle.
INVOCATION
    {outil} {dossier}/demo-0.1-py3-none-any.whl --json
DOMAINE
    Wheels et sdists Python avant installation (CI, miroir interne, revue de
    dépendance), et site-packages installés (--installes) ; interpréteur courant
    seulement pour la compatibilité.
"""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import email.parser
import hashlib
import io
import json
import lzma
import os
import platform
import posixpath
import re
import stat
import sys
import sysconfig
import tarfile
import tomllib
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from packaging import tags as packaging_tags
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version
except ImportError:
    packaging_tags = None

try:
    import pkginfo
except ImportError:
    pkginfo = None

RACINE = Path(__file__).resolve().parent

SECTION_HYPOTHESES = "HYPOTHÈSES"
SECTION_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", SECTION_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", SECTION_INVOCATION, "DOMAINE")

ETAT_INTEGRE = "INTÈGRE"
ETAT_DEFAUT = "DÉFAUT"
FICHIER_RECORD = "RECORD"
FICHIER_METADATA = "METADATA"
FICHIER_WHEEL = "WHEEL"
FICHIER_PKG_INFO = "PKG-INFO"
SIGNATURES_RECORD = ("RECORD.jws", "RECORD.p7s")
EXTENSIONS_SDIST = (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".zip")
ALGOS_INTERDITS = frozenset({"md5", "sha1", "sha224"})
DOSSIERS_IGNORES = frozenset({"__pycache__", "node_modules", ".git", ".hg", ".svn"})
CATEGORIES_DATA = ("scripts", "headers", "data", "purelib", "platlib")

TAILLE_BLOC = 1 << 20
TAILLE_MAX_DEFAUT = 3 * (1 << 30)
MEMBRES_MAX_DEFAUT = 200_000
FICHIERS_MAX_DEFAUT = 10_000
TAILLE_METADONNEE_MAX = 32 * (1 << 20)
RATIO_SUSPECT = 1000
TAILLE_RATIO_MIN = 1 << 20
MAX_EXAMINES = 200
MAX_CONSTATS = 200

NOMS_COURTS = {"python": "py", "cpython": "cp", "pypy": "pp", "ironpython": "ip", "jython": "jy"}
MANYLINUX_HISTORIQUES = {17: "manylinux2014", 12: "manylinux2010", 5: "manylinux1"}
ARCHS_MANYLINUX_ANCIENS = frozenset({"x86_64", "i686"})
ARCHS_MANYLINUX2014 = frozenset({"x86_64", "i686", "aarch64", "armv7l", "ppc64", "ppc64le", "s390x"})

MOTIF_VERSION = r"""
    v?
    (?:(?P<epoch>[0-9]+)!)?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>[-_.]?(?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)[-_.]?(?P<pre_n>[0-9]+)?)?
    (?P<post>(?:-(?P<post_n1>[0-9]+))|(?:[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n2>[0-9]+)?))?
    (?P<dev>[-_.]?(?P<dev_l>dev)[-_.]?(?P<dev_n>[0-9]+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
"""
RE_VERSION = re.compile(r"^\s*" + MOTIF_VERSION + r"\s*$", re.VERBOSE | re.IGNORECASE)
RE_SPECIFICATEUR = re.compile(r"^\s*(~=|===|==|!=|<=|>=|<|>)\s*(\S+?)\s*$")
RANG_PRE = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}


class EntreeInvalide(Exception):
    """Entrée inutilisable : chemin absent, format inconnu, valeur d'option absurde."""


@dataclass(frozen=True)
class Limites:
    """Bornes de lecture appliquées à chaque archive."""

    taille_max: int
    membres_max: int
    fichiers_max: int


@dataclass(frozen=True)
class Cible:
    """Une distribution à examiner."""

    chemin: Path
    genre: str
    explicite: bool
    base: Path | None = None


@dataclass
class Constats:
    """Défauts (rendent le code 1) et avertissements (informatifs), bornés en nombre."""

    defauts: list[dict[str, str]] = field(default_factory=list)
    avertissements: list[dict[str, str]] = field(default_factory=list)
    omis: int = 0

    def defaut(self, code: str, detail: str) -> None:
        """Ajoute un défaut."""
        self.ajouter(self.defauts, code, detail)

    def avertir(self, code: str, detail: str) -> None:
        """Ajoute un avertissement."""
        self.ajouter(self.avertissements, code, detail)

    def ajouter(self, liste: list[dict[str, str]], code: str, detail: str) -> None:
        """Ajoute sans dépasser MAX_CONSTATS."""
        if len(liste) >= MAX_CONSTATS:
            self.omis += 1
            return
        liste.append({"code": code, "detail": detail})


# --------------------------------------------------------------------------- #
# Versions PEP 440 (repli stdlib) et noms PEP 503
# --------------------------------------------------------------------------- #

def normaliser_nom(nom: str) -> str:
    """Nom normalisé PEP 503."""
    return re.sub(r"[-_.]+", "-", nom).lower()


def cle_version(texte: str) -> tuple | None:
    """Clé d'ordre PEP 440, ou None si la chaîne n'est pas une version."""
    m = RE_VERSION.match(texte or "")
    if not m:
        return None
    release = tuple(int(x) for x in m["release"].split("."))
    while len(release) > 1 and release[-1] == 0:
        release = release[:-1]
    pre = (1, RANG_PRE[m["pre_l"].lower()], int(m["pre_n"] or 0)) if m["pre"] else None
    post = (1, int(m["post_n1"] or m["post_n2"] or 0)) if m["post"] else (0,)
    dev = (1, int(m["dev_n"] or 0)) if m["dev"] else (2,)
    if pre is None:
        pre = (0,) if (not m["post"] and m["dev"]) else (2,)
    return (int(m["epoch"] or 0), release, pre, post, dev)


def versions_egales(a: str, b: str) -> bool:
    """Égalité PEP 440 (1.0 == 1.0.0), sinon égalité des chaînes."""
    ca, cb = cle_version(a), cle_version(b)
    if ca is None or cb is None:
        return a.strip().lower() == b.strip().lower()
    return ca == cb


def satisfait(version: str, specificateur: str) -> bool | None:
    """Évalue un spécificateur PEP 440 simple ; None si non évaluable."""
    m = RE_SPECIFICATEUR.match(specificateur)
    if not m:
        return None
    op, cible = m.group(1), m.group(2)
    if op == "===":
        return version.strip().lower() == cible.lower()
    if cible.endswith(".*"):
        return evaluer_joker(version, op, cible[:-2])
    cv, cc = cle_version(version), cle_version(cible)
    if cv is None or cc is None or op == "~=" and "." not in cible:
        return None
    if op == "~=":
        prefixe = ".".join(cible.split("!")[-1].split(".")[:-1])
        return cv >= cc and bool(evaluer_joker(version, "==", prefixe))
    return {"==": cv == cc, "!=": cv != cc, "<=": cv <= cc, ">=": cv >= cc,
            "<": cv < cc, ">": cv > cc}[op]


def evaluer_joker(version: str, op: str, prefixe: str) -> bool | None:
    """==X.* et !=X.* : comparaison du préfixe de publication."""
    if op not in ("==", "!="):
        return None
    mv, mp = RE_VERSION.match(version or ""), RE_VERSION.match(prefixe)
    if not mv or not mp or mp["pre"] or mp["post"] or mp["dev"]:
        return None
    rv = [int(x) for x in mv["release"].split(".")]
    rp = [int(x) for x in mp["release"].split(".")]
    rv += [0] * max(0, len(rp) - len(rv))
    egal = int(mv["epoch"] or 0) == int(mp["epoch"] or 0) and rv[:len(rp)] == rp
    return egal if op == "==" else not egal


def evaluer_requires_python(texte: str | None, version: str) -> bool | None:
    """Requires-Python satisfait par la version donnée (None : absent ou illisible)."""
    if not texte:
        return None
    if packaging_tags is not None:
        try:
            return SpecifierSet(texte).contains(Version(version), prereleases=True)
        except (InvalidSpecifier, InvalidVersion):
            return None
    resultats = [satisfait(version, s) for s in texte.split(",") if s.strip()]
    if any(r is None for r in resultats):
        return None
    return all(resultats)


# --------------------------------------------------------------------------- #
# Tags supportés par l'interpréteur courant
# --------------------------------------------------------------------------- #

def normaliser_plateforme(texte: str) -> str:
    """Plateforme sysconfig vers étiquette de wheel."""
    return texte.replace("-", "_").replace(".", "_").replace(" ", "_")


def abis_cpython(majeur: int, mineur: int) -> tuple[list[str], bool]:
    """ABI CPython (cp314, cp314t, cp314d…) et applicabilité d'abi3."""
    version = f"{majeur}{mineur}"
    fil = "t" if (majeur, mineur) >= (3, 13) and sysconfig.get_config_var("Py_GIL_DISABLED") else ""
    debogage = "d" if sysconfig.get_config_var("Py_DEBUG") else ""
    abis = [f"cp{version}{fil}{debogage}"]
    if debogage:
        abis.append(f"cp{version}{fil}")
    return abis, not fil


def abi_generique() -> list[str]:
    """ABI d'un interpréteur non CPython, déduite de EXT_SUFFIX (comme packaging)."""
    morceaux = (sysconfig.get_config_var("EXT_SUFFIX") or "").split(".")
    if len(morceaux) < 3 or not morceaux[1]:
        return []
    soabi = morceaux[1]
    if soabi.startswith("cpython"):
        return ["cp" + soabi.split("-")[1]]
    if soabi.startswith("pypy"):
        return [normaliser_plateforme("-".join(soabi.split("-")[:2]))]
    if soabi.startswith("graalpy"):
        return [normaliser_plateforme("-".join(soabi.split("-")[:3]))]
    return [normaliser_plateforme(soabi.split("-")[0])]


def version_glibc() -> tuple[int, int] | None:
    """Version de la glibc via os.confstr, None si autre libc."""
    try:
        texte = os.confstr("CS_GNU_LIBC_VERSION") or ""
    except (AttributeError, ValueError, OSError):
        return None
    m = re.match(r"glibc (\d+)\.(\d+)", texte)
    return (int(m.group(1)), int(m.group(2))) if m else None


def plateformes_linux(arch: str) -> list[str]:
    """linux_<arch> puis manylinux_2_x et alias historiques (ordre de packaging 26)."""
    archs = ["armv8l", "armv7l"] if arch == "armv8l" else [arch]
    resultat = [f"linux_{a}" for a in archs]
    glibc = version_glibc()
    if glibc is None or glibc[0] != 2:
        return resultat
    plancher = 4 if set(archs) & ARCHS_MANYLINUX_ANCIENS else 16
    for a in archs:
        for mineur in range(glibc[1], plancher, -1):
            resultat.append(f"manylinux_2_{mineur}_{a}")
            historique = MANYLINUX_HISTORIQUES.get(mineur)
            admis = ARCHS_MANYLINUX2014 if mineur == 17 else ARCHS_MANYLINUX_ANCIENS
            if historique and a in admis:
                resultat.append(f"{historique}_{a}")
    return resultat


def formats_mac(version: tuple[int, int], arch: str) -> list[str]:
    """Formats binaires macOS admis (reprend packaging.tags._mac_binary_formats)."""
    formats = [arch]
    if arch == "x86_64":
        if version < (10, 4):
            return []
        formats += ["intel", "fat64", "fat32"]
    if arch in ("arm64", "x86_64"):
        formats.append("universal2")
    if arch == "x86_64":
        formats.append("universal")
    return formats


def plateformes_macos(arch: str) -> list[str]:
    """Étiquettes macosx_* (table non éprouvée sur macOS dans cette session)."""
    morceaux = (platform.mac_ver()[0] or "10.16").split(".")
    version = (int(morceaux[0]), int(morceaux[1]) if len(morceaux) > 1 else 0)
    resultat: list[str] = []
    if version >= (11, 0):
        for majeur in range(version[0], 10, -1):
            resultat += [f"macosx_{majeur}_0_{f}" for f in formats_mac((majeur, 0), arch)]
        for mineur in range(16, 3, -1):
            formats = formats_mac((10, mineur), arch) if arch == "x86_64" else ["universal2"]
            resultat += [f"macosx_10_{mineur}_{f}" for f in formats]
        return resultat
    for mineur in range(version[1], -1, -1):
        resultat += [f"macosx_10_{mineur}_{f}" for f in formats_mac((10, mineur), arch)]
    return resultat


def plateformes() -> list[str]:
    """Plateformes supportées, dans l'ordre de préférence de packaging."""
    brute = normaliser_plateforme(sysconfig.get_platform())
    if brute.startswith("linux_"):
        arch = brute[len("linux_"):]
        if sys.maxsize <= 2 ** 32:
            arch = {"x86_64": "i686", "aarch64": "armv8l"}.get(arch, arch)
        return plateformes_linux(arch)
    if brute.startswith("macosx_"):
        return plateformes_macos(brute.rsplit("_", 1)[-1])
    return [brute]


def intervalle_py(majeur: int, mineur: int) -> list[str]:
    """py314, py3, py313 … py30."""
    return [f"py{majeur}{mineur}", f"py{majeur}"] + [f"py{majeur}{m}" for m in range(mineur - 1, -1, -1)]


def tags_stdlib() -> list[str]:
    """Reproduit packaging.tags.sys_tags() pour l'interpréteur courant."""
    majeur, mineur = sys.version_info[:2]
    plats = plateformes()
    court = NOMS_COURTS.get(sys.implementation.name, sys.implementation.name)
    interp = f"{court}{majeur}{mineur}"
    tags: list[str] = []
    if court == "cp":
        abis, abi3 = abis_cpython(majeur, mineur)
    else:
        abis, abi3 = abi_generique(), False
    for abi in abis + (["abi3"] if abi3 else []) + ["none"]:
        tags += [f"{interp}-{abi}-{p}" for p in plats]
    if abi3:
        for m in range(mineur - 1, 1, -1):
            tags += [f"cp{majeur}{m}-abi3-{p}" for p in plats]
    for py in intervalle_py(majeur, mineur):
        tags += [f"{py}-none-{p}" for p in plats]
    interp_any = {"cp": interp, "pp": f"pp{majeur}"}.get(court)
    if interp_any:
        tags.append(f"{interp_any}-none-any")
    tags += [f"{py}-none-any" for py in intervalle_py(majeur, mineur)]
    return tags


def tags_supportes() -> list[str]:
    """Tags supportés : packaging si présent, sinon table stdlib."""
    if packaging_tags is not None:
        return [str(t) for t in packaging_tags.sys_tags()]
    return tags_stdlib()


# --------------------------------------------------------------------------- #
# Outils communs aux archives
# --------------------------------------------------------------------------- #

def empreinte_fichier(chemin: Path) -> str:
    """sha256 hexadécimal d'un fichier, lu par blocs."""
    h = hashlib.sha256()
    with chemin.open("rb") as flux:
        while bloc := flux.read(TAILLE_BLOC):
            h.update(bloc)
    return h.hexdigest()


def b64_sans_bourrage(octets: bytes) -> str:
    """Encodage urlsafe-base64 sans « = », celui du RECORD."""
    return base64.urlsafe_b64encode(octets).decode("ascii").rstrip("=")


def digerer_flux(flux: IO[bytes], algo: str) -> tuple[str, int]:
    """Empreinte RECORD et taille d'un flux lu par blocs."""
    h = hashlib.new(algo)
    total = 0
    while bloc := flux.read(TAILLE_BLOC):
        h.update(bloc)
        total += len(bloc)
    return b64_sans_bourrage(h.digest()), total


def chemin_dangereux(nom: str) -> str | None:
    """Raison pour laquelle un nom de membre est dangereux à l'extraction."""
    if "\x00" in nom:
        return "octet nul dans le nom"
    if "\\" in nom:
        return "barre oblique inverse (séparateur Windows)"
    if nom.startswith("/") or re.match(r"^[A-Za-z]:", nom):
        return "chemin absolu"
    if ".." in nom.split("/"):
        return "remontée « .. »"
    return None


def decoder(octets: bytes, constats: Constats, nom: str) -> str:
    """Décode en utf-8 ; un octet invalide est signalé et remplacé."""
    try:
        return octets.decode("utf-8")
    except UnicodeDecodeError:
        constats.avertir("ENCODAGE", f"{nom} n'est pas de l'utf-8 valide (caractères remplacés)")
        return octets.decode("utf-8", errors="replace")


def lire_entetes(texte: str) -> dict[str, object]:
    """Champs de métadonnées cœur (METADATA, PKG-INFO) lus par email.parser."""
    msg = email.parser.HeaderParser().parsestr(texte)
    return {
        "metadata_version": msg.get("Metadata-Version"),
        "nom": msg.get("Name"),
        "version": msg.get("Version"),
        "resume": msg.get("Summary"),
        "requires_python": msg.get("Requires-Python"),
        "requires_dist": msg.get_all("Requires-Dist") or [],
        "provides_extra": msg.get_all("Provides-Extra") or [],
        "license": msg.get("License"),
        "license_expression": msg.get("License-Expression"),
        "license_files": msg.get_all("License-File") or [],
        "dynamic": msg.get_all("Dynamic") or [],
    }


def verifier_identite(meta: dict[str, object], nom: str | None, version: str | None,
                      origine: str, constats: Constats) -> None:
    """Le nom et la version des métadonnées doivent égaler ceux d'une autre source."""
    nom_meta, version_meta = meta.get("nom"), meta.get("version")
    if not nom_meta or not version_meta:
        constats.defaut("METADONNEES_INCOMPLETES", "Name ou Version absent des métadonnées")
        return
    if nom and normaliser_nom(str(nom_meta)) != normaliser_nom(nom):
        constats.defaut("NOM_INCOHERENT", f"{origine} : {nom!r} ≠ Name {nom_meta!r}")
    if version and not versions_egales(str(version_meta), version.replace("_", "-")):
        constats.defaut("VERSION_INCOHERENTE", f"{origine} : {version!r} ≠ Version {version_meta!r}")


def compatibilite(tags_fichier: list[str], requires_python: str | None,
                  supportes: list[str]) -> dict[str, object]:
    """Tags du wheel confrontés aux tags supportés, et Requires-Python."""
    rangs = {t: i for i, t in enumerate(supportes)}
    communs = sorted((t for t in tags_fichier if t in rangs), key=rangs.__getitem__)
    version = platform.python_version()
    rp = evaluer_requires_python(requires_python, version)
    return {
        "tags_compatibles": communs,
        "tag_retenu": communs[0] if communs else None,
        "requires_python": requires_python,
        "requires_python_satisfait": rp,
        "compatible": bool(communs) and rp is not False,
    }


def lire_points_entree(texte: str, constats: Constats) -> dict[str, object]:
    """entry_points.txt : console_scripts, gui_scripts et autres groupes."""
    analyseur = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    analyseur.optionxform = str
    try:
        analyseur.read_string(texte)
    except configparser.Error as erreur:
        constats.avertir("POINTS_ENTREE_ILLISIBLES", str(erreur).splitlines()[0])
        return {}
    groupes = {s: dict(analyseur.items(s)) for s in analyseur.sections()}
    return {
        "console_scripts": sorted(groupes.pop("console_scripts", {})),
        "gui_scripts": sorted(groupes.pop("gui_scripts", {})),
        "autres_groupes": {g: len(v) for g, v in sorted(groupes.items())},
    }


# --------------------------------------------------------------------------- #
# Wheel
# --------------------------------------------------------------------------- #

def analyser_nom_wheel(nom_fichier: str) -> dict[str, object] | None:
    """Découpe {nom}-{version}(-{build})?-{py}-{abi}-{plateforme}.whl."""
    parties = nom_fichier[:-len(".whl")].split("-")
    if len(parties) not in (5, 6) or not all(parties):
        return None
    build = parties[2] if len(parties) == 6 else None
    if build is not None and not build[:1].isdigit():
        return None
    py, abi, plat = parties[-3:]
    tags = [f"{p}-{a}-{q}" for p in py.split(".") for a in abi.split(".") for q in plat.split(".")]
    return {"nom": parties[0], "version": parties[1], "build": build, "tags": tags}


def inventorier_zip(zf: zipfile.ZipFile, limites: Limites, constats: Constats) -> bool:
    """Contrôles préalables à toute décompression ; False si la lecture serait dangereuse."""
    infos = zf.infolist()
    noms = [i.filename for i in infos]
    if len(infos) > limites.membres_max:
        constats.defaut("TROP_DE_MEMBRES", f"{len(infos)} membres > borne {limites.membres_max}")
        return False
    total = sum(i.file_size for i in infos)
    if total > limites.taille_max:
        constats.defaut("BOMBE_OU_TROP_GROS", f"{total} octets décompressés annoncés > borne {limites.taille_max}")
        return False
    for nom, compte in sorted(Counter(noms).items()):
        if compte > 1:
            constats.defaut("ENTREE_EN_DOUBLE", f"{nom} ({compte} fois)")
    for info in infos:
        controler_membre_zip(info, constats)
    return True


def controler_membre_zip(info: zipfile.ZipInfo, constats: Constats) -> None:
    """Chemin traversant, lien symbolique, chiffrement, taux de compression."""
    raison = chemin_dangereux(info.filename)
    if raison:
        constats.defaut("CHEMIN_DANGEREUX", f"{info.filename!r} : {raison}")
    if stat.S_ISLNK(info.external_attr >> 16):
        constats.avertir("LIEN_SYMBOLIQUE", info.filename)
    if info.flag_bits & 0x1:
        constats.defaut("ENTREE_CHIFFREE", info.filename)
    if (info.compress_size and info.file_size > TAILLE_RATIO_MIN
            and info.file_size / info.compress_size > RATIO_SUSPECT):
        constats.avertir("TAUX_SUSPECT", f"{info.filename} : {info.file_size // info.compress_size}:1")


def trouver_dist_info(noms: list[str], constats: Constats) -> str | None:
    """Unique dossier {nom}-{version}.dist-info à la racine de l'archive."""
    racines = sorted({n.split("/", 1)[0] for n in noms if "/" in n and n.split("/", 1)[0].endswith(".dist-info")})
    if not racines:
        constats.defaut("DIST_INFO_ABSENT", "aucun dossier *.dist-info à la racine")
        return None
    if len(racines) > 1:
        constats.defaut("DIST_INFO_MULTIPLE", ", ".join(racines))
    return racines[0]


def lire_membre(zf: zipfile.ZipFile, nom: str, constats: Constats) -> str | None:
    """Lit un petit membre texte (METADATA, WHEEL, RECORD) en bornant sa taille."""
    try:
        info = zf.getinfo(nom)
    except KeyError:
        return None
    if info.file_size > TAILLE_METADONNEE_MAX:
        constats.defaut("METADONNEE_DEMESUREE", f"{nom} : {info.file_size} octets")
        return None
    try:
        return decoder(zf.read(info), constats, nom)
    except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, OSError) as erreur:
        constats.defaut("MEMBRE_ILLISIBLE", f"{nom} : {erreur}")
        return None


def lire_record(texte: str, constats: Constats) -> dict[str, tuple[str, str]]:
    """Lignes du RECORD : chemin -> (empreinte, taille) ; lignes mal formées signalées."""
    lignes: dict[str, tuple[str, str]] = {}
    for numero, ligne in enumerate(csv.reader(io.StringIO(texte)), start=1):
        if not ligne:
            continue
        if len(ligne) != 3:
            constats.defaut("RECORD_MAL_FORME", f"ligne {numero} : {len(ligne)} champs au lieu de 3")
            continue
        if ligne[0] in lignes:
            constats.defaut("RECORD_EN_DOUBLE", ligne[0])
        lignes[ligne[0]] = (ligne[1], ligne[2])
    return lignes


def algo_record(empreinte: str, chemin: str, constats: Constats) -> tuple[str, str] | None:
    """Sépare « algo=valeur » et refuse les algorithmes interdits ou inconnus."""
    algo, _, valeur = empreinte.partition("=")
    algo = algo.lower()
    if not valeur:
        constats.defaut("RECORD_EMPREINTE_ILLISIBLE", f"{chemin} : {empreinte!r}")
        return None
    if algo in ALGOS_INTERDITS or algo not in hashlib.algorithms_available:
        constats.defaut("RECORD_ALGO_INTERDIT", f"{chemin} : {algo}")
        return None
    if valeur.endswith("="):
        constats.avertir("RECORD_BOURRAGE", f"{chemin} : empreinte avec « = » final")
    return algo, valeur.rstrip("=")


def comparer_entree(chemin: str, attendu: tuple[str, str], obtenu: tuple[str, int],
                    constats: Constats) -> bool:
    """Compare empreinte et taille recalculées à la ligne du RECORD."""
    empreinte, taille = obtenu
    conforme = True
    if attendu[0] != empreinte:
        constats.defaut("RECORD_EMPREINTE", f"{chemin} : attendu {attendu[0]}, obtenu {empreinte}")
        conforme = False
    if attendu[1] and attendu[1].strip() != str(taille):
        constats.defaut("RECORD_TAILLE", f"{chemin} : attendu {attendu[1]} octets, obtenu {taille}")
        conforme = False
    return conforme


def verifier_membre(zf: zipfile.ZipFile, info: zipfile.ZipInfo,
                    record: dict[str, tuple[str, str]], constats: Constats) -> str:
    """Recalcule l'empreinte d'un membre ; rend 'conforme', 'ecart', 'hors' ou 'illisible'."""
    ligne = record.get(info.filename)
    algo = algo_record(ligne[0], info.filename, constats) if ligne and ligne[0] else None
    try:
        with zf.open(info) as flux:
            empreinte, taille = digerer_flux(flux, algo[0] if algo else "sha256")
    except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, OSError, EOFError) as erreur:
        constats.defaut("MEMBRE_ILLISIBLE", f"{info.filename} : {erreur}")
        return "illisible"
    if ligne is None:
        return "hors"
    if algo is None:
        if not ligne[0]:
            constats.defaut("RECORD_SANS_EMPREINTE", info.filename)
        return "ecart"
    ok = comparer_entree(info.filename, (f"{algo[0]}={algo[1]}", ligne[1]),
                         (f"{algo[0]}={empreinte}", taille), constats)
    return "conforme" if ok else "ecart"


def verifier_record_wheel(zf: zipfile.ZipFile, dist_info: str, constats: Constats) -> dict[str, object]:
    """Chaque membre confronté au RECORD, et chaque ligne du RECORD à l'archive."""
    nom_record = f"{dist_info}/{FICHIER_RECORD}"
    texte = lire_membre(zf, nom_record, constats)
    if texte is None:
        constats.defaut("RECORD_ABSENT", nom_record)
        return {"lignes": 0}
    record = lire_record(texte, constats)
    exemptes = {nom_record} | {f"{dist_info}/{s}" for s in SIGNATURES_RECORD}
    bilan = {"conforme": 0, "ecart": 0, "hors": 0, "illisible": 0}
    hors: list[str] = []
    for info in zf.infolist():
        if info.is_dir() or info.filename in exemptes:
            continue
        etat = verifier_membre(zf, info, record, constats)
        bilan[etat] += 1
        if etat == "hors":
            hors.append(info.filename)
    for nom in hors:
        constats.defaut("HORS_RECORD", nom)
    presents = {i.filename for i in zf.infolist()}
    absents = sorted(n for n in record if n not in presents)
    for nom in absents:
        constats.defaut("LISTE_MAIS_ABSENT", nom)
    return {"lignes": len(record), "verifies": bilan["conforme"] + bilan["ecart"],
            "conformes": bilan["conforme"], "ecarts": bilan["ecart"],
            "hors_record": len(hors), "absents": len(absents), "illisibles": bilan["illisible"]}


def lire_wheel_meta(zf: zipfile.ZipFile, dist_info: str, constats: Constats) -> tuple[dict, dict]:
    """METADATA et WHEEL du dossier .dist-info."""
    texte_meta = lire_membre(zf, f"{dist_info}/{FICHIER_METADATA}", constats)
    texte_wheel = lire_membre(zf, f"{dist_info}/{FICHIER_WHEEL}", constats)
    if texte_meta is None:
        constats.defaut("METADATA_ABSENT", f"{dist_info}/{FICHIER_METADATA}")
    if texte_wheel is None:
        constats.defaut("WHEEL_ABSENT", f"{dist_info}/{FICHIER_WHEEL}")
    meta = lire_entetes(texte_meta) if texte_meta else {}
    wheel: dict[str, object] = {}
    if texte_wheel:
        msg = email.parser.HeaderParser().parsestr(texte_wheel)
        wheel = {"wheel_version": msg.get("Wheel-Version"), "generateur": msg.get("Generator"),
                 "root_is_purelib": msg.get("Root-Is-Purelib"), "tags": msg.get_all("Tag") or [],
                 "build": msg.get("Build")}
    return meta, wheel


def controler_fichier_wheel(wheel: dict, nom_fichier: dict | None, constats: Constats) -> None:
    """Wheel-Version 1.x et tags du fichier WHEEL égaux à ceux du nom de fichier."""
    version = str(wheel.get("wheel_version") or "")
    if wheel and not version.startswith("1."):
        constats.defaut("WHEEL_VERSION", f"Wheel-Version {version!r} : un installeur doit refuser une version majeure ≠ 1")
    if wheel and nom_fichier and set(wheel.get("tags") or []) != set(nom_fichier["tags"]):
        constats.avertir("TAGS_INCOHERENTS", f"WHEEL {sorted(wheel.get('tags') or [])} ≠ nom de fichier {sorted(nom_fichier['tags'])}")


def controler_licences(meta: dict, noms: set[str], dist_info: str, constats: Constats) -> None:
    """PEP 639 : chaque License-File doit figurer sous .dist-info/licenses."""
    if meta.get("license") and meta.get("license_expression"):
        constats.avertir("LICENCE_DOUBLE", "License et License-Expression présents : PyPI refuse ce cas (PEP 639)")
    version = cle_version(str(meta.get("metadata_version") or ""))
    if version is None or version < cle_version("2.4"):
        return
    for fichier in meta.get("license_files") or []:
        if f"{dist_info}/licenses/{fichier}" not in noms:
            constats.defaut("LICENCE_ABSENTE", f"License-File {fichier!r} absent de {dist_info}/licenses/")


def resumer_contenu(zf: zipfile.ZipFile, dist_info: str | None) -> dict[str, object]:
    """Racines, volumes et sous-dossiers .data du wheel."""
    infos = [i for i in zf.infolist() if not i.is_dir()]
    racines = sorted({i.filename.split("/", 1)[0] for i in infos})
    data_dir = dist_info[:-len(".dist-info")] + ".data" if dist_info else None
    data = {c: sum(1 for i in infos if data_dir and i.filename.startswith(f"{data_dir}/{c}/"))
            for c in CATEGORIES_DATA}
    return {"membres": len(infos), "octets_decompresses": sum(i.file_size for i in infos),
            "racines": racines[:50], "data": {c: n for c, n in data.items() if n}}


def analyser_wheel(chemin: Path, limites: Limites, supportes: list[str]) -> dict[str, object]:
    """Examen complet d'un wheel."""
    constats = Constats()
    resultat: dict[str, object] = {"type": "wheel"}
    nom_fichier = analyser_nom_wheel(chemin.name)
    if nom_fichier is None:
        constats.defaut("NOM_FICHIER", f"{chemin.name!r} ne suit pas {{nom}}-{{version}}(-{{build}})?-{{py}}-{{abi}}-{{plateforme}}.whl")
    try:
        with zipfile.ZipFile(chemin) as zf:
            resultat.update(examiner_zip_wheel(zf, nom_fichier, limites, constats))
    except (zipfile.BadZipFile, zlib.error, OSError, EOFError, ValueError) as erreur:
        constats.defaut("ARCHIVE_ILLISIBLE", f"pas une archive zip lisible : {erreur}")
    if nom_fichier is not None:
        resultat["compatibilite"] = compatibilite(nom_fichier["tags"],
                                                  (resultat.get("metadonnees") or {}).get("requires_python"), supportes)
        resultat["nom_fichier"] = nom_fichier
    return finaliser(resultat, constats)


def examiner_zip_wheel(zf: zipfile.ZipFile, nom_fichier: dict | None, limites: Limites,
                       constats: Constats) -> dict[str, object]:
    """Contenu, métadonnées et RECORD d'un wheel ouvert."""
    if not inventorier_zip(zf, limites, constats):
        return {}
    noms = zf.namelist()
    dist_info = trouver_dist_info(noms, constats)
    if dist_info is None:
        return {"contenu": resumer_contenu(zf, None)}
    meta, wheel = lire_wheel_meta(zf, dist_info, constats)
    if meta:
        nom_f = str(nom_fichier["nom"]) if nom_fichier else None
        version_f = str(nom_fichier["version"]) if nom_fichier else None
        verifier_identite(meta, nom_f, version_f, "nom de fichier", constats)
        nom_di, _, version_di = dist_info[:-len(".dist-info")].partition("-")
        verifier_identite(meta, nom_di, version_di, "dossier .dist-info", constats)
    controler_fichier_wheel(wheel, nom_fichier, constats)
    controler_licences(meta, set(noms), dist_info, constats)
    texte_ep = lire_membre(zf, f"{dist_info}/entry_points.txt", constats)
    return {"dist_info": dist_info, "metadonnees": meta, "wheel": wheel,
            "record": verifier_record_wheel(zf, dist_info, constats),
            "points_entree": lire_points_entree(texte_ep, constats) if texte_ep else {},
            "contenu": resumer_contenu(zf, dist_info)}


# --------------------------------------------------------------------------- #
# Sdist
# --------------------------------------------------------------------------- #

def membres_tar(tf: tarfile.TarFile, limites: Limites, constats: Constats) -> Iterator[tarfile.TarInfo]:
    """Parcourt les membres sans extraire, en arrêtant au-delà des bornes."""
    total = nombre = 0
    while (membre := tf.next()) is not None:
        nombre += 1
        total += max(membre.size, 0)
        if nombre > limites.membres_max:
            constats.defaut("TROP_DE_MEMBRES", f"plus de {limites.membres_max} membres : lecture arrêtée")
            return
        if total > limites.taille_max:
            constats.defaut("BOMBE_OU_TROP_GROS", f"plus de {limites.taille_max} octets annoncés : lecture arrêtée")
            return
        yield membre


def controler_membre_tar(membre: tarfile.TarInfo, constats: Constats) -> None:
    """Chemins traversants, liens sortants, périphériques et tubes."""
    raison = chemin_dangereux(membre.name)
    if raison:
        constats.defaut("CHEMIN_DANGEREUX", f"{membre.name!r} : {raison}")
    if membre.issym() or membre.islnk():
        cible = membre.linkname if membre.islnk() else posixpath.join(posixpath.dirname(membre.name), membre.linkname)
        if membre.linkname.startswith("/") or posixpath.normpath(cible).startswith(".."):
            constats.defaut("LIEN_SORTANT", f"{membre.name} -> {membre.linkname}")
        else:
            constats.avertir("LIEN", f"{membre.name} -> {membre.linkname}")
    if membre.isdev() or membre.isfifo():
        constats.defaut("MEMBRE_SPECIAL", f"{membre.name} : périphérique ou tube")


class LecteurArchive:
    """Accès uniforme aux sdists tar et zip : liste des noms et lecture bornée."""

    def __init__(self, chemin: Path, limites: Limites, constats: Constats) -> None:
        self.chemin, self.limites, self.constats = chemin, limites, constats
        self.noms: list[str] = []
        self.contenus: dict[str, bytes] = {}

    def charger(self, voulus: tuple[str, ...]) -> None:
        """Inventorie l'archive et garde le contenu des membres dont le nom finit par `voulus`."""
        if self.chemin.name.lower().endswith(".zip"):
            self.charger_zip(voulus)
        else:
            self.charger_tar(voulus)

    def charger_zip(self, voulus: tuple[str, ...]) -> None:
        """Variante zip."""
        with zipfile.ZipFile(self.chemin) as zf:
            if not inventorier_zip(zf, self.limites, self.constats):
                return
            self.noms = [i.filename for i in zf.infolist() if not i.is_dir()]
            for info in zf.infolist():
                if self.voulu(info.filename, voulus) and info.file_size <= TAILLE_METADONNEE_MAX:
                    self.contenus[info.filename] = zf.read(info)

    def charger_tar(self, voulus: tuple[str, ...]) -> None:
        """Variante tar (gz, bz2, xz)."""
        with tarfile.open(self.chemin, "r:*") as tf:
            for membre in membres_tar(tf, self.limites, self.constats):
                controler_membre_tar(membre, self.constats)
                if not membre.isfile():
                    continue
                self.noms.append(membre.name)
                if self.voulu(membre.name, voulus) and membre.size <= TAILLE_METADONNEE_MAX:
                    flux = tf.extractfile(membre)
                    self.contenus[membre.name] = flux.read() if flux else b""

    @staticmethod
    def voulu(nom: str, voulus: tuple[str, ...]) -> bool:
        """Membre de premier niveau sous la racine dont le nom fait partie des voulus."""
        morceaux = nom.split("/")
        return len(morceaux) == 2 and morceaux[1] in voulus


def nom_pep625(nom: str, version: str) -> str:
    """Nom de fichier attendu par PEP 625."""
    return f"{re.sub(r'[-_.]+', '_', nom).lower()}-{version}.tar.gz"


def lire_pyproject_sdist(octets: bytes, constats: Constats) -> dict[str, object]:
    """Résumé de [build-system] et [project] du pyproject.toml embarqué."""
    try:
        donnees = tomllib.loads(decoder(octets, constats, "pyproject.toml"))
    except tomllib.TOMLDecodeError as erreur:
        constats.defaut("PYPROJECT_INVALIDE", str(erreur))
        return {}
    systeme = donnees.get("build-system") if isinstance(donnees.get("build-system"), dict) else {}
    projet = donnees.get("project") if isinstance(donnees.get("project"), dict) else {}
    return {"build_backend": systeme.get("build-backend"), "build_requires": systeme.get("requires"),
            "nom": projet.get("name"), "version": projet.get("version"),
            "dynamic": projet.get("dynamic") or []}


def controler_sdist(lecteur: LecteurArchive, chemin: Path, constats: Constats) -> dict[str, object]:
    """PKG-INFO, pyproject et nom de fichier d'une sdist déjà inventoriée."""
    racines = sorted({n.split("/", 1)[0] for n in lecteur.noms})
    if len(racines) > 1:
        constats.avertir("RACINES_MULTIPLES", ", ".join(racines[:10]))
    racine = racines[0] if racines else ""
    pkg = lecteur.contenus.get(f"{racine}/{FICHIER_PKG_INFO}")
    if pkg is None:
        constats.defaut("PKG_INFO_ABSENT", f"{racine}/{FICHIER_PKG_INFO}")
        return {"racine": racine, "pas_une_sdist": True}
    meta = lire_entetes(decoder(pkg, constats, FICHIER_PKG_INFO))
    nom, version = str(meta.get("nom") or ""), str(meta.get("version") or "")
    verifier_identite(meta, None, None, "PKG-INFO", constats)
    if nom and version and racine != f"{nom}-{version}" and normaliser_nom(racine) != normaliser_nom(f"{nom}-{version}"):
        constats.avertir("RACINE_INATTENDUE", f"dossier racine {racine!r}, attendu {nom}-{version}")
    if nom and version and chemin.name != nom_pep625(nom, version):
        constats.avertir("NOM_NON_PEP625", f"{chemin.name!r}, attendu {nom_pep625(nom, version)!r}")
    pyproject = lecteur.contenus.get(f"{racine}/pyproject.toml")
    projet = lire_pyproject_sdist(pyproject, constats) if pyproject is not None else {}
    comparer_pyproject(projet, meta, constats)
    setup_py = f"{racine}/setup.py" in lecteur.noms
    if setup_py:
        constats.avertir("SETUP_PY", "setup.py présent : la construction exécute du code arbitraire")
    return {"racine": racine, "metadonnees": meta, "pyproject": projet,
            "setup_py": setup_py, "membres": len(lecteur.noms)}


def comparer_pyproject(projet: dict[str, object], meta: dict[str, object], constats: Constats) -> None:
    """[project] du pyproject.toml embarqué contre PKG-INFO."""
    if not projet:
        return
    if projet.get("nom") and normaliser_nom(str(projet["nom"])) != normaliser_nom(str(meta.get("nom") or "")):
        constats.defaut("NOM_INCOHERENT", f"pyproject {projet['nom']!r} ≠ PKG-INFO {meta.get('nom')!r}")
    if projet.get("version") and not versions_egales(str(projet["version"]), str(meta.get("version") or "")):
        constats.defaut("VERSION_INCOHERENTE", f"pyproject {projet['version']!r} ≠ PKG-INFO {meta.get('version')!r}")


def analyser_sdist(chemin: Path, limites: Limites) -> dict[str, object]:
    """Examen complet d'une sdist."""
    constats = Constats()
    lecteur = LecteurArchive(chemin, limites, constats)
    resultat: dict[str, object] = {"type": "sdist"}
    try:
        lecteur.charger((FICHIER_PKG_INFO, "pyproject.toml", "setup.py"))
        resultat.update(controler_sdist(lecteur, chemin, constats))
    except (tarfile.TarError, zipfile.BadZipFile, zlib.error, lzma.LZMAError, OSError, EOFError,
            ValueError) as erreur:
        constats.defaut("ARCHIVE_ILLISIBLE", f"archive illisible : {erreur}")
    return finaliser(resultat, constats)


# --------------------------------------------------------------------------- #
# Distributions installées (--installes)
# --------------------------------------------------------------------------- #

def verifier_installe(base: Path, chemin_rel: str, attendu: tuple[str, str],
                      constats: Constats, budget: list[int]) -> str:
    """Un fichier listé dans le RECORD d'une distribution installée."""
    norm = posixpath.normpath(chemin_rel)
    if norm.startswith("../") or norm == ".." or posixpath.isabs(norm) or re.match(r"^[A-Za-z]:", norm):
        return "hors_perimetre"
    fichier = base / norm
    if not fichier.is_file():
        constats.defaut("LISTE_MAIS_ABSENT", chemin_rel)
        return "absent"
    if not attendu[0]:
        return "sans_empreinte"
    algo = algo_record(attendu[0], chemin_rel, constats)
    if algo is None:
        return "ecart"
    budget[0] -= fichier.stat().st_size
    if budget[0] < 0:
        constats.defaut("BOMBE_OU_TROP_GROS", "borne --taille-max atteinte : vérification interrompue")
        return "illisible"
    with fichier.open("rb") as flux:
        empreinte, taille = digerer_flux(flux, algo[0])
    ok = comparer_entree(chemin_rel, (f"{algo[0]}={algo[1]}", attendu[1]), (f"{algo[0]}={empreinte}", taille), constats)
    return "conforme" if ok else "ecart"


def analyser_installee(dist_info: Path, base: Path, limites: Limites) -> dict[str, object]:
    """RECORD d'un *.dist-info confronté aux fichiers installés sous `base`."""
    constats = Constats()
    resultat: dict[str, object] = {"type": "installee"}
    meta_fichier = dist_info / FICHIER_METADATA
    if meta_fichier.is_file() and meta_fichier.stat().st_size <= TAILLE_METADONNEE_MAX:
        resultat["metadonnees"] = lire_entetes(decoder(meta_fichier.read_bytes(), constats, FICHIER_METADATA))
    record_fichier = dist_info / FICHIER_RECORD
    if not record_fichier.is_file() or record_fichier.stat().st_size > TAILLE_METADONNEE_MAX:
        constats.defaut("RECORD_ABSENT", str(record_fichier.name))
        return finaliser(resultat, constats)
    record = lire_record(decoder(record_fichier.read_bytes(), constats, FICHIER_RECORD), constats)
    bilan: dict[str, int] = {}
    budget = [limites.taille_max]
    for chemin_rel, attendu in record.items():
        etat = verifier_installe(base, chemin_rel, attendu, constats, budget)
        bilan[etat] = bilan.get(etat, 0) + 1
    resultat["record"] = {"lignes": len(record), **bilan}
    return finaliser(resultat, constats)


# --------------------------------------------------------------------------- #
# Contre-lecture pkginfo, collecte et synthèse
# --------------------------------------------------------------------------- #

def contre_lire(cible: Cible, resultat: dict[str, object]) -> dict[str, object] | None:
    """Relit nom, version et Requires-Dist avec pkginfo, et compare."""
    if pkginfo is None or cible.genre == "installee":
        return None
    meta = resultat.get("metadonnees") or {}
    try:
        lu = pkginfo.Wheel(str(cible.chemin)) if cible.genre == "wheel" else pkginfo.SDist(str(cible.chemin))
    except (ValueError, OSError, zipfile.BadZipFile, tarfile.TarError, EOFError) as erreur:
        return {"lisible": False, "erreur": str(erreur).splitlines()[0] if str(erreur) else type(erreur).__name__}
    accord = (lu.name == meta.get("nom") and lu.version == meta.get("version")
              and sorted(lu.requires_dist or []) == sorted(meta.get("requires_dist") or []))
    return {"lisible": True, "nom": lu.name, "version": lu.version, "accord": accord}


def finaliser(resultat: dict[str, object], constats: Constats) -> dict[str, object]:
    """Ajoute défauts, avertissements et verdict d'intégrité."""
    meta = resultat.get("metadonnees") or {}
    resultat["nom"] = meta.get("nom")
    resultat["version"] = meta.get("version")
    resultat["defauts"] = constats.defauts
    resultat["avertissements"] = constats.avertissements
    resultat["constats_omis"] = constats.omis
    resultat["integre"] = not constats.defauts
    return resultat


def lister_dossier(dossier: Path, limites: Limites) -> list[Path]:
    """Archives candidates sous un dossier, sans suivre les liens ni les dossiers cachés."""
    trouves: list[Path] = []
    for courant, sous, fichiers in os.walk(dossier):
        sous[:] = sorted(d for d in sous if not d.startswith(".") and d not in DOSSIERS_IGNORES)
        for nom in sorted(fichiers):
            if type_par_nom(nom) and not (Path(courant) / nom).is_symlink():
                trouves.append(Path(courant) / nom)
                if len(trouves) >= limites.fichiers_max:
                    return trouves
    return trouves


def type_par_nom(nom: str) -> str | None:
    """'wheel', 'sdist' ou None selon l'extension."""
    bas = nom.lower()
    if bas.endswith(".whl"):
        return "wheel"
    if bas.endswith(EXTENSIONS_SDIST):
        return "sdist"
    return None


def collecter(chemins: list[Path], limites: Limites, installes: bool) -> list[Cible]:
    """Cibles à examiner ; lève EntreeInvalide pour un chemin inutilisable."""
    cibles: list[Cible] = []
    for chemin in chemins:
        if not chemin.exists():
            raise EntreeInvalide(f"chemin introuvable : {chemin}")
        if chemin.is_dir():
            cibles += collecter_dossier(chemin, limites, installes)
        elif installes:
            raise EntreeInvalide(f"--installes attend un dossier site-packages, pas un fichier : {chemin}")
        elif type_par_nom(chemin.name) is None:
            raise EntreeInvalide(f"format non reconnu : {chemin.name} (attendu .whl, .tar.gz, .tgz, .tar.bz2, .tar.xz, .zip ou un dossier)")
        else:
            cibles.append(Cible(chemin, type_par_nom(chemin.name) or "", True))
    return cibles


def collecter_dossier(dossier: Path, limites: Limites, installes: bool) -> list[Cible]:
    """Archives d'un dossier, ou *.dist-info directement dessous avec --installes."""
    if installes:
        infos = sorted(p for p in dossier.iterdir() if p.is_dir() and p.name.endswith(".dist-info"))
        return [Cible(p, "installee", False, dossier) for p in infos[:limites.fichiers_max]]
    return [Cible(p, type_par_nom(p.name) or "", False) for p in lister_dossier(dossier, limites)]


def examiner(cible: Cible, limites: Limites, supportes: list[str]) -> dict[str, object]:
    """Aiguille vers l'analyse du bon type."""
    if cible.genre == "wheel":
        resultat = analyser_wheel(cible.chemin, limites, supportes)
    elif cible.genre == "sdist":
        resultat = analyser_sdist(cible.chemin, limites)
    else:
        resultat = analyser_installee(cible.chemin, cible.base or cible.chemin.parent, limites)
    if cible.genre != "installee":
        resultat["sha256_archive"] = empreinte_fichier(cible.chemin)
        resultat["taille_octets"] = cible.chemin.stat().st_size
    pk = contre_lire(cible, resultat)
    if pk is not None:
        resultat["contre_lecture_pkginfo"] = pk
    return resultat


def libelle(chemin: Path, base: Path) -> str:
    """Chemin affiché relatif à la base quand c'est possible."""
    try:
        return str(chemin.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(chemin)


def examiner_tout(cibles: list[Cible], limites: Limites, base: Path) -> tuple[list[dict], list[dict]]:
    """Examine chaque cible ; une archive de dossier qui n'est pas une sdist est ignorée."""
    supportes = tags_supportes()
    rapports: list[dict] = []
    ignores: list[dict] = []
    for cible in cibles:
        try:
            rapport = examiner(cible, limites, supportes)
        except OSError as erreur:
            if cible.explicite:
                raise EntreeInvalide(f"lecture impossible : {cible.chemin} ({erreur})") from erreur
            ignores.append({"chemin": libelle(cible.chemin, base), "raison": str(erreur)})
            continue
        if rapport.get("pas_une_sdist") and not cible.explicite:
            ignores.append({"chemin": libelle(cible.chemin, base), "raison": "archive sans PKG-INFO : pas une sdist"})
            continue
        rapport["chemin"] = libelle(cible.chemin, base)
        rapports.append(rapport)
    return rapports, ignores


def extraire_contrat(doc: str) -> dict[str, str]:
    """Intitulés du contrat de mesure tirés de la docstring."""
    contrat: dict[str, list[str]] = {}
    courant: str | None = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if ligne[:1].strip() and tete in INTITULES:
            courant = tete
            contrat[courant] = []
        elif courant is not None and tete:
            contrat[courant].append(tete)
    return {cle: " ".join(valeur) for cle, valeur in contrat.items()}


def moteur() -> str:
    """Bibliothèques réellement utilisées."""
    noms = [n for n, m in (("packaging", packaging_tags), ("pkginfo", pkginfo)) if m is not None]
    return "+".join(noms) if noms else "stdlib"


def construire_sortie(rapports: list[dict], ignores: list[dict], exiger: bool) -> dict[str, object]:
    """Objet de sortie complet."""
    examines = [r["chemin"] for r in rapports]
    incompatibles = [r["chemin"] for r in rapports
                     if r.get("type") == "wheel" and not (r.get("compatibilite") or {}).get("compatible", False)]
    return {
        "outil": "lire_wheel",
        "moteur": moteur(),
        "interpreteur": {"version": platform.python_version(), "implementation": sys.implementation.name,
                         "plateforme": sysconfig.get_platform()},
        "denominateur": len(rapports),
        "examines": examines[:MAX_EXAMINES],
        "examines_tronques": len(examines) > MAX_EXAMINES,
        "ignores": ignores[:MAX_EXAMINES],
        "bilan": {"integres": sum(1 for r in rapports if r["integre"]),
                  "defectueuses": sum(1 for r in rapports if not r["integre"]),
                  "wheels_incompatibles": incompatibles, "compatibilite_exigee": exiger},
        "distributions": rapports,
        "contrat": extraire_contrat(__doc__ or ""),
    }


def code_retour(sortie: dict[str, object]) -> int:
    """1 si une distribution a un défaut (ou est incompatible avec --exiger-compatible)."""
    bilan = sortie["bilan"]
    if bilan["defectueuses"] or (bilan["compatibilite_exigee"] and bilan["wheels_incompatibles"]):
        return 1
    return 0


def afficher_humain(sortie: dict[str, object]) -> None:
    """Rapport lisible."""
    print(f"lire_wheel — {sortie['denominateur']} distribution(s), moteur {sortie['moteur']}, "
          f"Python {sortie['interpreteur']['version']} ({sortie['interpreteur']['plateforme']})")
    for r in sortie["distributions"]:
        etat = ETAT_INTEGRE if r["integre"] else ETAT_DEFAUT
        print(f"\n[{etat}] {r['chemin']} ({r['type']}) {r.get('nom') or '?'} {r.get('version') or '?'}")
        afficher_details(r)
    for i in sortie["ignores"]:
        print(f"\nignoré : {i['chemin']} — {i['raison']}")
    b = sortie["bilan"]
    print(f"\nBilan : {b['integres']} intègre(s), {b['defectueuses']} avec défaut(s), "
          f"{len(b['wheels_incompatibles'])} wheel(s) incompatible(s) ici")


def afficher_details(r: dict[str, object]) -> None:
    """Détails d'une distribution."""
    rec = r.get("record")
    if rec:
        print("  RECORD : " + ", ".join(f"{k} {v}" for k, v in rec.items()))
    compat = r.get("compatibilite")
    if compat:
        rp = {True: "oui", False: "NON", None: "non évalué"}[compat["requires_python_satisfait"]]
        print(f"  compatible ici : {'oui' if compat['compatible'] else 'NON'} "
              f"(tag retenu {compat['tag_retenu']}, Requires-Python {compat['requires_python']!r} satisfait : {rp})")
    meta = r.get("metadonnees") or {}
    if meta.get("requires_dist"):
        print(f"  Requires-Dist : {len(meta['requires_dist'])} — " + "; ".join(meta["requires_dist"][:8]))
    ep = r.get("points_entree") or {}
    if ep.get("console_scripts"):
        print("  console_scripts : " + ", ".join(ep["console_scripts"]))
    for d in r["defauts"]:
        print(f"  {ETAT_DEFAUT} {d['code']} : {d['detail']}")
    for a in r["avertissements"]:
        print(f"  avertissement {a['code']} : {a['detail']}")


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    p = argparse.ArgumentParser(
        description=("Vérifie l'intégrité d'un wheel ou d'une sdist (RECORD recalculé, archive "
                     "sûre, métadonnées cohérentes), décrit son contenu et dit s'il s'installe "
                     "sur l'interpréteur courant."),
        epilog=("Exemple : python lire_wheel.py dist/paquet-1.0-py3-none-any.whl --json\n"
                "          python lire_wheel.py .venv/lib/python3.14/site-packages --installes\n"
                "Codes : 0 rien à signaler, 1 défaut (RECORD incohérent, archive dangereuse…), "
                "2 entrée invalide, 3 rien à examiner."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("chemins", nargs="+", type=Path, help="wheels, sdists ou dossiers à examiner")
    p.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    p.add_argument("--racine", type=Path, default=None, help="base des chemins relatifs (défaut : dossier courant)")
    p.add_argument("--installes", action="store_true",
                   help="examiner les *.dist-info d'un site-packages au lieu d'archives")
    p.add_argument("--exiger-compatible", action="store_true",
                   help="code 1 si un wheel ne s'installe pas sur cet interpréteur")
    p.add_argument("--taille-max", type=int, default=TAILLE_MAX_DEFAUT, help="octets décompressés maximum par archive")
    p.add_argument("--membres-max", type=int, default=MEMBRES_MAX_DEFAUT, help="membres maximum par archive")
    p.add_argument("--fichiers-max", type=int, default=FICHIERS_MAX_DEFAUT, help="archives maximum par dossier")
    return p


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    base = args.racine or Path.cwd()
    absents = [n for n, m in (("packaging", packaging_tags), ("pkginfo", pkginfo)) if m is None]
    if absents:
        print(f"{', '.join(absents)} absent(s) : tags par table stdlib (copie de packaging.tags), "
              "métadonnées par email.parser sans contre-lecture", file=sys.stderr)
    try:
        if min(args.taille_max, args.membres_max, args.fichiers_max) <= 0:
            raise EntreeInvalide("les bornes --taille-max, --membres-max et --fichiers-max doivent être positives")
        limites = Limites(args.taille_max, args.membres_max, args.fichiers_max)
        chemins = [c if c.is_absolute() else base / c for c in args.chemins]
        rapports, ignores = examiner_tout(collecter(chemins, limites, args.installes), limites, base)
    except EntreeInvalide as erreur:
        print(f"entrée invalide : {erreur}", file=sys.stderr)
        return 2
    sortie = construire_sortie(rapports, ignores, args.exiger_compatible)
    if args.json:
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    if not rapports:
        detail = f" ({len(ignores)} archive(s) ignorée(s) : pas des sdists)" if ignores else ""
        print(f"dénominateur nul : aucune distribution trouvée{detail}, rien à examiner", file=sys.stderr)
        return 3
    if not args.json:
        afficher_humain(sortie)
    return code_retour(sortie)


if __name__ == "__main__":
    raise SystemExit(main())
