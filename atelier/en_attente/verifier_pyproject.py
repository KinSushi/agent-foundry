"""Un pyproject.toml validé par schéma peut encore casser la construction. Mesuré dans
cette session : validate-pyproject 0.26 déclare « Valid file » trois fichiers que les
backends refusent — un motif license-files qui ne trouve aucun fichier (PEP 639
impose une erreur), license = "MIT" avec le classifieur « License :: OSI Approved ::
MIT License » (setuptools 77.0.1, flit_core 3.11.0 et pdm-backend 2.4.0 échouent), et
license = "MIT" avec requires = ["setuptools<70"] (setuptools 76.1.0 échoue). Cet outil
ajoute au schéma la cohérence avec le disque et avec le backend déclaré.

QUESTION
    Ce pyproject.toml est-il conforme aux PEP 517/518/621/639 (et 735, 685) et
    cohérent avec son projet et son backend ?
MESURE
    Lecture tomllib (utf-8 strict, taille bornée, marqueurs de conflit git repérés).
    [build-system] : requires présent et chaque entrée au format PEP 508,
    build-backend « module.chemin(:objet) », backend-path relatif et interne, clés
    inconnues ; backend connu fourni par requires. [project] : clés inconnues ; name
    valide (forme normalisée PEP 503 rapportée) et jamais dynamique ; version PEP 440
    (forme normalisée) ou listée dans dynamic, jamais les deux ; description sur une
    ligne ; readme (suffixe .md/.rst, table file/text exclusifs avec content-type
    admis, fichier existant) ; requires-python (spécificateurs PEP 440) ; license :
    expression SPDX vérifiée contre la liste SPDX 3.27.0 embarquée (identifiants
    dépréciés signalés, forme canonique rapportée) ou table dépréciée, interdite avec
    license-files ; license-files : motifs PEP 639 (pas de « .. », pas de « \\ », pas de
    « / » initial, caractères admis) et chacun trouvant au moins un fichier ;
    classifieurs License :: dépréciés, refusés avec une expression par setuptools,
    flit_core et pdm-backend ; classifieurs de version Python exclus par
    requires-python ; authors/maintainers (clés name/email, pas de virgule, courriel
    plausible) ; urls ; dependencies et optional-dependencies au format PEP 508
    (noms d'extras PEP 685, doublons après normalisation, dépendances par URL) ;
    scripts, gui-scripts, entry-points (références objet, groupes console_scripts
    et gui_scripts interdits) ; dynamic ; [dependency-groups] (PEP 735 : noms,
    include-group existant, cycles). Tables de premier niveau inconnues. Plancher de
    version du backend pour les expressions de licence, mesuré par construction réelle.
    Si packaging est importable, les PEP 508, les spécificateurs, les versions et les
    expressions SPDX sont jugés par packaging et les écarts avec le repli stdlib
    rapportés ; si validate-pyproject est importable, son verdict est rapporté à côté,
    réseau coupé (VALIDATE_PYPROJECT_NO_NETWORK).
HYPOTHÈSES
    Le fichier est le pyproject.toml d'un projet dont l'arborescence est celle du
    disque (readme et license-files cherchés à partir de son dossier). La liste SPDX
    embarquée (3.27.0) et la table des backends reflètent les versions mesurées.
LIMITES
    Les classifieurs ne sont pas confrontés à la liste trove complète ; les tables
    [tool.*] ne sont pas validées (seuls leurs noms sont rapportés) ; les champs
    dynamiques ne sont pas calculés ; la cohérence backend/requires ne couvre que
    les backends de la table ; un backend inconnu n'est pas jugé. Le plancher PEP 639
    n'est connu que pour setuptools, hatchling, flit-core et pdm-backend.
CONTRE-EXEMPLES
    Constaté : readme = "README.txt" rend un avertissement et non un défaut, alors
    que la spécification exige une erreur pour un suffixe non reconnu ; l'outil
    tolère .txt parce que des backends l'acceptent, et peut donc dire « conforme » à
    un fichier qu'un backend strict refusera.
INVOCATION
    {outil} {dossier}/pyproject.toml --json
DOMAINE
    Fichiers pyproject.toml de projets Python (PEP 517/518/621/639/685/735), avant
    construction ou publication ; pas la configuration propre des outils ([tool.*]).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from packaging.licenses import InvalidLicenseExpression, canonicalize_license_expression
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version
except ImportError:
    Requirement = None

try:
    from validate_pyproject import api as vp_api
    from validate_pyproject import errors as vp_errors
except ImportError:
    vp_api = None

RACINE = Path(__file__).resolve().parent

SECTION_HYPOTHESES = "HYPOTHÈSES"
SECTION_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", SECTION_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", SECTION_INVOCATION, "DOMAINE")
VARIABLE_RESEAU_VP = "VALIDATE_PYPROJECT_NO_NETWORK"
NORME_LICENCES = "SPDX"
ETAT_CONFORME = "CONFORME"
ETAT_NON_CONFORME = "NON CONFORME"

TAILLE_MAX_DEFAUT = 5 * (1 << 20)
FICHIERS_MAX_DEFAUT = 5_000
MAX_EXAMINES = 200
MAX_CONSTATS = 300
DOSSIERS_IGNORES = frozenset({"__pycache__", "node_modules", "site-packages", "dist-packages",
                              "venv", ".venv", "build", "dist"})

CLES_PREMIER_NIVEAU = frozenset({"build-system", "project", "tool", "dependency-groups"})
CLES_BUILD_SYSTEM = frozenset({"requires", "build-backend", "backend-path"})
CLES_PROJECT = frozenset({
    "name", "version", "description", "readme", "requires-python", "license", "license-files",
    "authors", "maintainers", "keywords", "classifiers", "urls", "scripts", "gui-scripts",
    "entry-points", "dependencies", "optional-dependencies", "dynamic", "import-names",
    "import-namespaces",
})
TYPES_README = frozenset({"text/markdown", "text/x-rst", "text/plain"})

# module du backend -> distribution qui le fournit
BACKENDS = {
    "setuptools.build_meta": "setuptools", "setuptools.build_meta:__legacy__": "setuptools",
    "hatchling.build": "hatchling", "flit_core.buildapi": "flit-core", "flit.buildapi": "flit",
    "poetry.core.masonry.api": "poetry-core", "poetry.masonry.api": "poetry",
    "pdm.backend": "pdm-backend", "pdm.pep517.api": "pdm-pep517", "maturin": "maturin",
    "scikit_build_core.build": "scikit-build-core", "mesonpy": "meson-python",
    "uv_build": "uv-build", "sipbuild.api": "sip", "pbr.build": "pbr", "whey": "whey",
}
# Première version acceptant license = "<expression SPDX>" et dernière qui ne l'accepte pas,
# mesurées dans cette session par build_wheel() réel : setuptools 76.1.0 refuse (ValueError),
# hatchling 1.26.3 écrit « License: MIT » en Metadata 2.3 (expression perdue sans erreur),
# flit_core 3.10.1 refuse (ConfigError), pdm-backend 2.3.3 refuse (ValidationError).
PLANCHERS_PEP639 = {
    "setuptools": ("77.0.1", "76.1.0"), "hatchling": ("1.27.0", "1.26.3"),
    "flit-core": ("3.11.0", "3.10.1"), "pdm-backend": ("2.4.0", "2.3.3"),
}
# Backends dont la construction échoue avec une expression ET un classifieur License ::
# (mesuré : setuptools 77.0.1, flit_core 3.11.0, pdm-backend 2.4.0 ; hatchling 1.27.0 accepte).
REFUS_CLASSIFIEUR_LICENCE = frozenset({"setuptools", "flit-core", "pdm-backend"})

RE_NOM = re.compile(r"^([A-Z0-9]|[A-Z0-9][A-Z0-9._-]*[A-Z0-9])$", re.IGNORECASE)
RE_IDENTIFIANT = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*")
RE_OP = re.compile(r"===|==|~=|!=|<=|>=|<|>")
RE_VERSION_JETON = re.compile(r"[A-Za-z0-9_.*+!-]+")
RE_ARBITRAIRE = re.compile(r"[^\s;)]+")
RE_URL = re.compile(r"[^ \t]+")
RE_CHAINE = re.compile(r"'[^']*'|\"[^\"]*\"")
RE_VARIABLE = re.compile(
    r"(python_version|python_full_version|os[._]name|sys[._]platform|platform_(?:release|system)"
    r"|platform[._](?:version|machine|python_implementation)|python_implementation"
    r"|implementation_(?:name|version)|extras?|dependency_groups)\b")
RE_BOOLEEN = re.compile(r"(or|and)\b")
RE_MOTIF_LICENCE = re.compile(r"^[\w.\-/*?\[\]]+$")
RE_COURRIEL = re.compile(r"^[^@\s,<>]+@[^@\s,<>]+\.[^@\s,<>]+$")
RE_REFERENCE = re.compile(r"^\s*([\w.]+)\s*(?::\s*([\w.]+))?\s*(\[[^\]]*\])?\s*$")
RE_CLASSIFIEUR_PY = re.compile(r"^Programming Language :: Python :: (\d+)\.(\d+)$")
RE_JETON_SPDX = re.compile(r"\(|\)|[^\s()]+")
RE_LICENCEREF = re.compile(r"^[A-Za-z0-9.-]+$")

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
RANG_PRE = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}
LETTRE_PRE = {0: "a", 1: "b", 2: "rc"}

# Liste SPDX 3.27.0, reprise de packaging 26.3 (packaging/licenses/_spdx.py) :
# identifiants séparés par des espaces ; un « ! » final marque un identifiant déprécié.
VERSION_SPDX = "3.27.0"
LICENCES_SPDX = (
    "0BSD 3D-Slicer-1.0 AAL Abstyles AdaCore-doc Adobe-2006 Adobe-Display-PostScript Adobe-Glyph "
    "Adobe-Utopia ADSL AFL-1.1 AFL-1.2 AFL-2.0 AFL-2.1 AFL-3.0 Afmparse AGPL-1.0! AGPL-1.0-only "
    "AGPL-1.0-or-later AGPL-3.0! AGPL-3.0-only AGPL-3.0-or-later Aladdin AMD-newlib AMDPLPA AML "
    "AML-glslang AMPAS ANTLR-PD ANTLR-PD-fallback any-OSI any-OSI-perl-modules Apache-1.0 "
    "Apache-1.1 Apache-2.0 APAFML APL-1.0 App-s2p APSL-1.0 APSL-1.1 APSL-1.2 APSL-2.0 Arphic-1999 "
    "Artistic-1.0 Artistic-1.0-cl8 Artistic-1.0-Perl Artistic-2.0 Artistic-dist Aspell-RU "
    "ASWF-Digital-Assets-1.0 ASWF-Digital-Assets-1.1 Baekmuk Bahyph Barr bcrypt-Solar-Designer "
    "Beerware Bitstream-Charter Bitstream-Vera BitTorrent-1.0 BitTorrent-1.1 blessing "
    "BlueOak-1.0.0 Boehm-GC Boehm-GC-without-fee Borceux Brian-Gladman-2-Clause "
    "Brian-Gladman-3-Clause BSD-1-Clause BSD-2-Clause BSD-2-Clause-Darwin "
    "BSD-2-Clause-first-lines BSD-2-Clause-FreeBSD! BSD-2-Clause-NetBSD! BSD-2-Clause-Patent "
    "BSD-2-Clause-pkgconf-disclaimer BSD-2-Clause-Views BSD-3-Clause BSD-3-Clause-acpica "
    "BSD-3-Clause-Attribution BSD-3-Clause-Clear BSD-3-Clause-flex BSD-3-Clause-HP "
    "BSD-3-Clause-LBNL BSD-3-Clause-Modification BSD-3-Clause-No-Military-License "
    "BSD-3-Clause-No-Nuclear-License BSD-3-Clause-No-Nuclear-License-2014 "
    "BSD-3-Clause-No-Nuclear-Warranty BSD-3-Clause-Open-MPI BSD-3-Clause-Sun BSD-4-Clause "
    "BSD-4-Clause-Shortened BSD-4-Clause-UC BSD-4.3RENO BSD-4.3TAHOE "
    "BSD-Advertising-Acknowledgement BSD-Attribution-HPND-disclaimer BSD-Inferno-Nettverk "
    "BSD-Protection BSD-Source-beginning-file BSD-Source-Code BSD-Systemics BSD-Systemics-W3Works "
    "BSL-1.0 BUSL-1.1 bzip2-1.0.5! bzip2-1.0.6 C-UDA-1.0 CAL-1.0 CAL-1.0-Combined-Work-Exception "
    "Caldera Caldera-no-preamble Catharon CATOSL-1.1 CC-BY-1.0 CC-BY-2.0 CC-BY-2.5 CC-BY-2.5-AU "
    "CC-BY-3.0 CC-BY-3.0-AT CC-BY-3.0-AU CC-BY-3.0-DE CC-BY-3.0-IGO CC-BY-3.0-NL CC-BY-3.0-US "
    "CC-BY-4.0 CC-BY-NC-1.0 CC-BY-NC-2.0 CC-BY-NC-2.5 CC-BY-NC-3.0 CC-BY-NC-3.0-DE CC-BY-NC-4.0 "
    "CC-BY-NC-ND-1.0 CC-BY-NC-ND-2.0 CC-BY-NC-ND-2.5 CC-BY-NC-ND-3.0 CC-BY-NC-ND-3.0-DE "
    "CC-BY-NC-ND-3.0-IGO CC-BY-NC-ND-4.0 CC-BY-NC-SA-1.0 CC-BY-NC-SA-2.0 CC-BY-NC-SA-2.0-DE "
    "CC-BY-NC-SA-2.0-FR CC-BY-NC-SA-2.0-UK CC-BY-NC-SA-2.5 CC-BY-NC-SA-3.0 CC-BY-NC-SA-3.0-DE "
    "CC-BY-NC-SA-3.0-IGO CC-BY-NC-SA-4.0 CC-BY-ND-1.0 CC-BY-ND-2.0 CC-BY-ND-2.5 CC-BY-ND-3.0 "
    "CC-BY-ND-3.0-DE CC-BY-ND-4.0 CC-BY-SA-1.0 CC-BY-SA-2.0 CC-BY-SA-2.0-UK CC-BY-SA-2.1-JP "
    "CC-BY-SA-2.5 CC-BY-SA-3.0 CC-BY-SA-3.0-AT CC-BY-SA-3.0-DE CC-BY-SA-3.0-IGO CC-BY-SA-4.0 "
    "CC-PDDC CC-PDM-1.0 CC-SA-1.0 CC0-1.0 CDDL-1.0 CDDL-1.1 CDL-1.0 CDLA-Permissive-1.0 "
    "CDLA-Permissive-2.0 CDLA-Sharing-1.0 CECILL-1.0 CECILL-1.1 CECILL-2.0 CECILL-2.1 CECILL-B "
    "CECILL-C CERN-OHL-1.1 CERN-OHL-1.2 CERN-OHL-P-2.0 CERN-OHL-S-2.0 CERN-OHL-W-2.0 CFITSIO "
    "check-cvs checkmk ClArtistic Clips CMU-Mach CMU-Mach-nodoc CNRI-Jython CNRI-Python "
    "CNRI-Python-GPL-Compatible COIL-1.0 Community-Spec-1.0 Condor-1.1 copyleft-next-0.3.0 "
    "copyleft-next-0.3.1 Cornell-Lossless-JPEG CPAL-1.0 CPL-1.0 CPOL-1.02 Cronyx Crossword "
    "CryptoSwift CrystalStacker CUA-OPL-1.0 Cube curl cve-tou D-FSL-1.0 DEC-3-Clause diffmark "
    "DL-DE-BY-2.0 DL-DE-ZERO-2.0 DOC DocBook-DTD DocBook-Schema DocBook-Stylesheet DocBook-XML "
    "Dotseqn DRL-1.0 DRL-1.1 DSDP dtoa dvipdfm ECL-1.0 ECL-2.0 eCos-2.0! EFL-1.0 EFL-2.0 eGenix "
    "Elastic-2.0 Entessa EPICS EPL-1.0 EPL-2.0 ErlPL-1.1 etalab-2.0 EUDatagrid EUPL-1.0 EUPL-1.1 "
    "EUPL-1.2 Eurosym Fair FBM FDK-AAC Ferguson-Twofish Frameworx-1.0 FreeBSD-DOC FreeImage FSFAP "
    "FSFAP-no-warranty-disclaimer FSFUL FSFULLR FSFULLRSD FSFULLRWD FSL-1.1-ALv2 FSL-1.1-MIT FTL "
    "Furuseth fwlw Game-Programming-Gems GCR-docs GD generic-xts GFDL-1.1! "
    "GFDL-1.1-invariants-only GFDL-1.1-invariants-or-later GFDL-1.1-no-invariants-only "
    "GFDL-1.1-no-invariants-or-later GFDL-1.1-only GFDL-1.1-or-later GFDL-1.2! "
    "GFDL-1.2-invariants-only GFDL-1.2-invariants-or-later GFDL-1.2-no-invariants-only "
    "GFDL-1.2-no-invariants-or-later GFDL-1.2-only GFDL-1.2-or-later GFDL-1.3! "
    "GFDL-1.3-invariants-only GFDL-1.3-invariants-or-later GFDL-1.3-no-invariants-only "
    "GFDL-1.3-no-invariants-or-later GFDL-1.3-only GFDL-1.3-or-later Giftware GL2PS Glide Glulxe "
    "GLWTPL gnuplot GPL-1.0! GPL-1.0+! GPL-1.0-only GPL-1.0-or-later GPL-2.0! GPL-2.0+! "
    "GPL-2.0-only GPL-2.0-or-later GPL-2.0-with-autoconf-exception! GPL-2.0-with-bison-exception! "
    "GPL-2.0-with-classpath-exception! GPL-2.0-with-font-exception! GPL-2.0-with-GCC-exception! "
    "GPL-3.0! GPL-3.0+! GPL-3.0-only GPL-3.0-or-later GPL-3.0-with-autoconf-exception! "
    "GPL-3.0-with-GCC-exception! Graphics-Gems gSOAP-1.3b gtkbook Gutmann HaskellReport HDF5 "
    "hdparm HIDAPI Hippocratic-2.1 HP-1986 HP-1989 HPND HPND-DEC HPND-doc HPND-doc-sell "
    "HPND-export-US HPND-export-US-acknowledgement HPND-export-US-modify HPND-export2-US "
    "HPND-Fenneberg-Livingston HPND-INRIA-IMAG HPND-Intel HPND-Kevlin-Henney HPND-Markus-Kuhn "
    "HPND-merchantability-variant HPND-MIT-disclaimer HPND-Netrek HPND-Pbmplus "
    "HPND-sell-MIT-disclaimer-xserver HPND-sell-regexpr HPND-sell-variant "
    "HPND-sell-variant-MIT-disclaimer HPND-sell-variant-MIT-disclaimer-rev HPND-UC "
    "HPND-UC-export-US HTMLTIDY IBM-pibs ICU IEC-Code-Components-EULA IJG IJG-short ImageMagick "
    "iMatix Imlib2 Info-ZIP Inner-Net-2.0 InnoSetup Intel Intel-ACPI Interbase-1.0 IPA IPL-1.0 "
    "ISC ISC-Veillard Jam JasPer-2.0 jove JPL-image JPNIC JSON Kastrup Kazlib Knuth-CTAN LAL-1.2 "
    "LAL-1.3 Latex2e Latex2e-translated-notice Leptonica LGPL-2.0! LGPL-2.0+! LGPL-2.0-only "
    "LGPL-2.0-or-later LGPL-2.1! LGPL-2.1+! LGPL-2.1-only LGPL-2.1-or-later LGPL-3.0! LGPL-3.0+! "
    "LGPL-3.0-only LGPL-3.0-or-later LGPLLR Libpng libpng-1.6.35 libpng-2.0 libselinux-1.0 "
    "libtiff libutil-David-Nugent LiLiQ-P-1.1 LiLiQ-R-1.1 LiLiQ-Rplus-1.1 Linux-man-pages-1-para "
    "Linux-man-pages-copyleft Linux-man-pages-copyleft-2-para Linux-man-pages-copyleft-var "
    "Linux-OpenIB LOOP LPD-document LPL-1.0 LPL-1.02 LPPL-1.0 LPPL-1.1 LPPL-1.2 LPPL-1.3a "
    "LPPL-1.3c lsof Lucida-Bitmap-Fonts LZMA-SDK-9.11-to-9.20 LZMA-SDK-9.22 Mackerras-3-Clause "
    "Mackerras-3-Clause-acknowledgment magaz mailprio MakeIndex man2html Martin-Birgmeier "
    "McPhee-slideshow metamail Minpack MIPS MirOS MIT MIT-0 MIT-advertising MIT-Click MIT-CMU "
    "MIT-enna MIT-feh MIT-Festival MIT-Khronos-old MIT-Modern-Variant MIT-open-group "
    "MIT-testregex MIT-Wu MITNFA MMIXware Motosoto MPEG-SSG mpi-permissive mpich2 MPL-1.0 MPL-1.1 "
    "MPL-2.0 MPL-2.0-no-copyleft-exception mplus MS-LPL MS-PL MS-RL MTLL MulanPSL-1.0 "
    "MulanPSL-2.0 Multics Mup NAIST-2003 NASA-1.3 Naumen NBPL-1.0 NCBI-PD NCGL-UK-2.0 NCL NCSA "
    "Net-SNMP! NetCDF Newsletr NGPL ngrep NICTA-1.0 NIST-PD NIST-PD-fallback NIST-Software "
    "NLOD-1.0 NLOD-2.0 NLPL Nokia NOSL Noweb NPL-1.0 NPL-1.1 NPOSL-3.0 NRL NTIA-PD NTP NTP-0 "
    "Nunit! O-UDA-1.0 OAR OCCT-PL OCLC-2.0 ODbL-1.0 ODC-By-1.0 OFFIS OFL-1.0 OFL-1.0-no-RFN "
    "OFL-1.0-RFN OFL-1.1 OFL-1.1-no-RFN OFL-1.1-RFN OGC-1.0 OGDL-Taiwan-1.0 OGL-Canada-2.0 "
    "OGL-UK-1.0 OGL-UK-2.0 OGL-UK-3.0 OGTSL OLDAP-1.1 OLDAP-1.2 OLDAP-1.3 OLDAP-1.4 OLDAP-2.0 "
    "OLDAP-2.0.1 OLDAP-2.1 OLDAP-2.2 OLDAP-2.2.1 OLDAP-2.2.2 OLDAP-2.3 OLDAP-2.4 OLDAP-2.5 "
    "OLDAP-2.6 OLDAP-2.7 OLDAP-2.8 OLFL-1.3 OML OpenPBS-2.3 OpenSSL OpenSSL-standalone OpenVision "
    "OPL-1.0 OPL-UK-3.0 OPUBL-1.0 OSET-PL-2.1 OSL-1.0 OSL-1.1 OSL-2.0 OSL-2.1 OSL-3.0 PADL "
    "Parity-6.0.0 Parity-7.0.0 PDDL-1.0 PHP-3.0 PHP-3.01 Pixar pkgconf Plexus pnmstitch "
    "PolyForm-Noncommercial-1.0.0 PolyForm-Small-Business-1.0.0 PostgreSQL PPL PSF-2.0 psfrag "
    "psutils Python-2.0 Python-2.0.1 python-ldap Qhull QPL-1.0 QPL-1.0-INRIA-2004 radvd Rdisc "
    "RHeCos-1.1 RPL-1.1 RPL-1.5 RPSL-1.0 RSA-MD RSCPL Ruby Ruby-pty SAX-PD SAX-PD-2.0 Saxpath "
    "SCEA SchemeReport Sendmail Sendmail-8.23 Sendmail-Open-Source-1.1 SGI-B-1.0 SGI-B-1.1 "
    "SGI-B-2.0 SGI-OpenGL SGP4 SHL-0.5 SHL-0.51 SimPL-2.0 SISSL SISSL-1.2 SL Sleepycat SMAIL-GPL "
    "SMLNJ SMPPL SNIA snprintf SOFA softSurfer Soundex Spencer-86 Spencer-94 Spencer-99 SPL-1.0 "
    "ssh-keyscan SSH-OpenSSH SSH-short SSLeay-standalone SSPL-1.0 StandardML-NJ! SugarCRM-1.1.3 "
    "SUL-1.0 Sun-PPP Sun-PPP-2000 SunPro SWL swrule Symlinks TAPR-OHL-1.0 TCL TCP-wrappers "
    "TermReadKey TGPPL-1.0 ThirdEye threeparttable TMate TORQUE-1.1 TOSL TPDL TPL-1.0 TrustedQSL "
    "TTWL TTYP0 TU-Berlin-1.0 TU-Berlin-2.0 Ubuntu-font-1.0 UCAR UCL-1.0 ulem UMich-Merit "
    "Unicode-3.0 Unicode-DFS-2015 Unicode-DFS-2016 Unicode-TOU UnixCrypt Unlicense "
    "Unlicense-libtelnet Unlicense-libwhirlpool UPL-1.0 URT-RLE Vim VOSTROM VSL-1.0 W3C "
    "W3C-19980720 W3C-20150513 w3m Watcom-1.0 Widget-Workshop Wsuipa WTFPL wwl wxWindows! X11 "
    "X11-distribute-modifications-variant X11-swapped Xdebug-1.03 Xerox Xfig XFree86-1.1 xinetd "
    "xkeyboard-config-Zinoviev xlock Xnet xpp XSkat xzoom YPL-1.0 YPL-1.1 Zed Zeeff Zend-2.0 "
    "Zimbra-1.3 Zimbra-1.4 Zlib zlib-acknowledgement ZPL-1.1 ZPL-2.0 ZPL-2.1 "
)
EXCEPTIONS_SPDX = (
    "389-exception Asterisk-exception Asterisk-linking-protocols-exception Autoconf-exception-2.0 "
    "Autoconf-exception-3.0 Autoconf-exception-generic Autoconf-exception-generic-3.0 "
    "Autoconf-exception-macro Bison-exception-1.24 Bison-exception-2.2 Bootloader-exception "
    "CGAL-linking-exception Classpath-exception-2.0 CLISP-exception-2.0 "
    "cryptsetup-OpenSSL-exception Digia-Qt-LGPL-exception-1.1 DigiRule-FOSS-exception "
    "eCos-exception-2.0 erlang-otp-linking-exception Fawkes-Runtime-exception FLTK-exception "
    "fmt-exception Font-exception-2.0 freertos-exception-2.0 GCC-exception-2.0 "
    "GCC-exception-2.0-note GCC-exception-3.1 Gmsh-exception GNAT-exception "
    "GNOME-examples-exception GNU-compiler-exception gnu-javamail-exception "
    "GPL-3.0-389-ds-base-exception GPL-3.0-interface-exception GPL-3.0-linking-exception "
    "GPL-3.0-linking-source-exception GPL-CC-1.0 GStreamer-exception-2005 "
    "GStreamer-exception-2008 harbour-exception i2p-gpl-java-exception "
    "Independent-modules-exception KiCad-libraries-exception LGPL-3.0-linking-exception "
    "libpri-OpenH323-exception Libtool-exception Linux-syscall-note LLGPL LLVM-exception "
    "LZMA-exception mif-exception mxml-exception Nokia-Qt-exception-1.1! "
    "OCaml-LGPL-linking-exception OCCT-exception-1.0 OpenJDK-assembly-exception-1.0 "
    "openvpn-openssl-exception PCRE2-exception polyparse-exception "
    "PS-or-PDF-font-exception-20170817 QPL-1.0-INRIA-2004-exception Qt-GPL-exception-1.0 "
    "Qt-LGPL-exception-1.1 Qwt-exception-1.0 romic-exception RRDtool-FLOSS-exception-2.0 "
    "SANE-exception SHL-2.0 SHL-2.1 stunnel-exception SWI-exception Swift-exception "
    "Texinfo-exception u-boot-exception-2.0 UBDL-exception Universal-FOSS-exception-1.0 "
    "vsftpd-openssl-exception WxWindows-exception-3.1 x11vnc-openssl-exception "
)


class EntreeInvalide(Exception):
    """Entrée inutilisable : chemin absent, type de fichier inattendu."""


class ErreurExigence(ValueError):
    """Chaîne non conforme à la grammaire PEP 508."""


@dataclass
class Constats:
    """Défauts (non-conformité, code 1) et avertissements, bornés en nombre."""

    defauts: list[dict[str, str]] = field(default_factory=list)
    avertissements: list[dict[str, str]] = field(default_factory=list)
    ecarts_moteurs: list[dict[str, str]] = field(default_factory=list)
    omis: int = 0

    def defaut(self, code: str, champ: str, detail: str) -> None:
        """Ajoute un défaut."""
        self.ajouter(self.defauts, {"code": code, "champ": champ, "detail": detail})

    def avertir(self, code: str, champ: str, detail: str) -> None:
        """Ajoute un avertissement."""
        self.ajouter(self.avertissements, {"code": code, "champ": champ, "detail": detail})

    def ecart(self, champ: str, stdlib: str, bibliotheque: str) -> None:
        """Note un désaccord entre le repli stdlib et packaging."""
        self.ajouter(self.ecarts_moteurs, {"champ": champ, "stdlib": stdlib, "packaging": bibliotheque})

    def ajouter(self, liste: list[dict[str, str]], entree: dict[str, str]) -> None:
        """Ajoute sans dépasser MAX_CONSTATS."""
        if len(liste) >= MAX_CONSTATS:
            self.omis += 1
            return
        liste.append(entree)


@dataclass
class Contexte:
    """Fichier en cours d'examen et ses constats."""

    dossier: Path
    constats: Constats
    donnees: dict


# --------------------------------------------------------------------------- #
# PEP 440 et PEP 503
# --------------------------------------------------------------------------- #

def normaliser_nom(nom: str) -> str:
    """Nom normalisé PEP 503."""
    return re.sub(r"[-_.]+", "-", nom).lower()


def version_normalisee(texte: str) -> str | None:
    """Forme normalisée PEP 440, ou None si invalide."""
    m = RE_VERSION.match(texte)
    if not m:
        return None
    morceaux = [f"{int(m['epoch'])}!" if m["epoch"] and int(m["epoch"]) else ""]
    morceaux.append(".".join(str(int(x)) for x in m["release"].split(".")))
    if m["pre"]:
        morceaux.append(f"{LETTRE_PRE[RANG_PRE[m['pre_l'].lower()]]}{int(m['pre_n'] or 0)}")
    if m["post"]:
        morceaux.append(f".post{int(m['post_n1'] or m['post_n2'] or 0)}")
    if m["dev"]:
        morceaux.append(f".dev{int(m['dev_n'] or 0)}")
    if m["local"]:
        morceaux.append("+" + re.sub(r"[-_]", ".", m["local"].lower()))
    return "".join(morceaux)


def cle_version(texte: str) -> tuple | None:
    """Clé d'ordre PEP 440 (sans la partie locale)."""
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


def satisfait(version: str, op: str, cible: str) -> bool | None:
    """Une version satisfait-elle « op cible » ? None si non évaluable."""
    if op == "===":
        return version.lower() == cible.lower()
    if cible.endswith(".*"):
        mv, mp = RE_VERSION.match(version), RE_VERSION.match(cible[:-2])
        if not mv or not mp:
            return None
        rv, rp = [int(x) for x in mv["release"].split(".")], [int(x) for x in mp["release"].split(".")]
        rv += [0] * max(0, len(rp) - len(rv))
        egal = rv[:len(rp)] == rp
        return egal if op == "==" else not egal
    cv, cc = cle_version(version), cle_version(cible)
    if cv is None or cc is None:
        return None
    if op == "~=":
        prefixe = ".".join(cible.split(".")[:-1]) + ".*"
        return cv >= cc and bool(satisfait(version, "==", prefixe))
    return {"==": cv == cc, "!=": cv != cc, "<=": cv <= cc, ">=": cv >= cc,
            "<": cv < cc, ">": cv > cc}.get(op)


def admet(specificateurs: list[tuple[str, str]], version: str) -> bool | None:
    """L'ensemble de spécificateurs admet-il cette version ?"""
    if Requirement is not None:
        texte = ",".join(op + v for op, v in specificateurs)
        try:
            return SpecifierSet(texte).contains(Version(version), prereleases=True)
        except (InvalidSpecifier, InvalidVersion):
            return None
    resultats = [satisfait(version, op, v) for op, v in specificateurs]
    return None if any(r is None for r in resultats) else all(resultats)


# --------------------------------------------------------------------------- #
# PEP 508 (repli stdlib, calqué sur la grammaire de packaging)
# --------------------------------------------------------------------------- #

class Lecteur:
    """Curseur sur une chaîne d'exigence."""

    def __init__(self, texte: str) -> None:
        self.texte = texte
        self.pos = 0

    def blancs(self) -> bool:
        """Saute les espaces et tabulations ; dit s'il y en avait."""
        debut = self.pos
        while self.pos < len(self.texte) and self.texte[self.pos] in " \t":
            self.pos += 1
        return self.pos > debut

    def lire(self, motif: re.Pattern[str]) -> str | None:
        """Consomme le motif s'il est en tête."""
        m = motif.match(self.texte, self.pos)
        if not m:
            return None
        self.pos = m.end()
        return m.group(0)

    def exiger(self, motif: re.Pattern[str], attendu: str) -> str:
        """Consomme le motif ou lève ErreurExigence."""
        valeur = self.lire(motif)
        if valeur is None:
            raise ErreurExigence(f"{attendu} attendu en position {self.pos} : {self.texte[self.pos:self.pos + 20]!r}")
        return valeur

    def car(self, c: str) -> bool:
        """Consomme le caractère c s'il est en tête."""
        if self.texte.startswith(c, self.pos):
            self.pos += len(c)
            return True
        return False

    def fini(self) -> bool:
        """Fin de chaîne atteinte."""
        return self.pos >= len(self.texte)


def verifier_version_spec(op: str, version: str) -> None:
    """Règles de packaging sur la version selon l'opérateur."""
    if op == "===":
        return
    joker = version.endswith(".*")
    m = RE_VERSION.match(version[:-2] if joker else version)
    if not m:
        raise ErreurExigence(f"version invalide {version!r} après {op}")
    if joker and (op not in ("==", "!=") or m["pre"] or m["post"] or m["dev"] or m["local"]):
        raise ErreurExigence(f"joker .* interdit dans {op}{version}")
    if op not in ("==", "!=") and m["local"]:
        raise ErreurExigence(f"version locale interdite avec {op}")
    if op == "~=" and len(m["release"].split(".")) < 2:
        raise ErreurExigence(f"~= exige au moins deux segments : {version!r}")


def lire_specificateurs(lec: Lecteur) -> list[tuple[str, str]]:
    """version_many, avec ou sans parenthèses."""
    entre = lec.car("(")
    specs: list[tuple[str, str]] = []
    while True:
        lec.blancs()
        op = lec.lire(RE_OP)
        if op is None:
            if specs:
                raise ErreurExigence(f"spécificateur attendu après la virgule en position {lec.pos}")
            break
        lec.blancs()
        version = lec.exiger(RE_ARBITRAIRE if op == "===" else RE_VERSION_JETON, "version")
        verifier_version_spec(op, version)
        specs.append((op, version))
        lec.blancs()
        if not lec.car(","):
            break
    if entre:
        lec.blancs()
        if not lec.car(")"):
            raise ErreurExigence("parenthèse fermante attendue")
    return specs


def lire_extras(lec: Lecteur) -> list[str]:
    """[a, b] ; une virgule finale est une erreur."""
    extras: list[str] = []
    lec.blancs()
    if lec.car("]"):
        return extras
    while True:
        lec.blancs()
        extras.append(lec.exiger(RE_IDENTIFIANT, "nom d'extra"))
        lec.blancs()
        if lec.car("]"):
            return extras
        if not lec.car(","):
            raise ErreurExigence("« , » ou « ] » attendu dans les extras")


def lire_terme_marqueur(lec: Lecteur) -> None:
    """variable ou chaîne entre guillemets."""
    lec.blancs()
    if lec.lire(RE_VARIABLE) is None and lec.lire(RE_CHAINE) is None:
        raise ErreurExigence(f"variable de marqueur ou chaîne attendue en position {lec.pos}")


def lire_comparaison(lec: Lecteur) -> None:
    """marker_expr : (marker) ou terme op terme."""
    lec.blancs()
    if lec.car("("):
        lire_marqueur(lec)
        lec.blancs()
        if not lec.car(")"):
            raise ErreurExigence("parenthèse fermante attendue dans le marqueur")
        return
    lire_terme_marqueur(lec)
    lec.blancs()
    if lec.lire(RE_OP) is None and lec.lire(re.compile(r"in\b")) is None:
        if lec.lire(re.compile(r"not\b")) is None or not lec.blancs() or lec.lire(re.compile(r"in\b")) is None:
            raise ErreurExigence(f"opérateur de marqueur attendu en position {lec.pos}")
    lire_terme_marqueur(lec)


def lire_marqueur(lec: Lecteur) -> None:
    """marker_or / marker_and."""
    lire_comparaison(lec)
    while True:
        sauvegarde = lec.pos
        lec.blancs()
        if lec.lire(RE_BOOLEEN) is None:
            lec.pos = sauvegarde
            return
        lire_comparaison(lec)


def analyser_exigence(texte: str) -> dict[str, object]:
    """Analyse PEP 508 ; lève ErreurExigence si invalide."""
    lec = Lecteur(texte)
    lec.blancs()
    nom = lec.exiger(RE_IDENTIFIANT, "nom de distribution")
    if not RE_NOM.match(nom):
        raise ErreurExigence(f"nom invalide {nom!r}")
    lec.blancs()
    extras = lire_extras(lec) if lec.car("[") else []
    lec.blancs()
    url, specs = None, []
    if lec.car("@"):
        lec.blancs()
        url = lec.exiger(RE_URL, "URL après @")
        verifier_url(url)
        if not lec.fini() and not lec.blancs():
            raise ErreurExigence("espace attendu après l'URL")
    else:
        specs = lire_specificateurs(lec)
    lec.blancs()
    marqueur = None
    if lec.car(";"):
        debut = lec.pos
        lire_marqueur(lec)
        marqueur = texte[debut:lec.pos].strip()
    lec.blancs()
    if not lec.fini():
        raise ErreurExigence(f"texte inattendu en position {lec.pos} : {texte[lec.pos:lec.pos + 20]!r}")
    return {"nom": nom, "extras": extras, "url": url, "specificateurs": specs, "marqueur": marqueur}


def verifier_url(url: str) -> None:
    """URL directe : schéma et hôte (file: accepté tel quel)."""
    morceaux = urllib.parse.urlparse(url)
    if morceaux.scheme == "file":
        return
    if not (morceaux.scheme and morceaux.netloc):
        raise ErreurExigence(f"URL invalide {url!r}")


def juger_exigence(texte: object, champ: str, ctx: Contexte) -> dict[str, object] | None:
    """PEP 508 par packaging si présent (écarts avec stdlib notés), sinon stdlib."""
    if not isinstance(texte, str):
        ctx.constats.defaut("TYPE", champ, f"chaîne PEP 508 attendue, trouvé {type(texte).__name__}")
        return None
    try:
        resultat, erreur = analyser_exigence(texte), None
    except ErreurExigence as e:
        resultat, erreur = None, str(e)
    if Requirement is not None:
        try:
            Requirement(texte)
            avis = None
        except InvalidRequirement as e:
            avis = str(e).splitlines()[0]
        if (avis is None) != (erreur is None):
            ctx.constats.ecart(champ, erreur or "valide", avis or "valide")
        erreur = avis
        if erreur is None and resultat is None:
            resultat = {"nom": Requirement(texte).name, "extras": [], "url": None, "specificateurs": [], "marqueur": None}
    if erreur is not None:
        ctx.constats.defaut("PEP508", champ, f"{texte!r} : {erreur}")
        return None
    return resultat


def juger_specificateurs(texte: str, champ: str, ctx: Contexte) -> list[tuple[str, str]] | None:
    """Ensemble de spécificateurs PEP 440 séparés par des virgules."""
    specs: list[tuple[str, str]] = []
    erreur = None
    for morceau in (texte.split(",") if texte.strip() else []):
        lec = Lecteur(morceau)
        try:
            lus = lire_specificateurs(lec)
            lec.blancs()
            if len(lus) != 1 or not lec.fini():
                raise ErreurExigence(f"spécificateur invalide {morceau.strip()!r}")
            specs += lus
        except ErreurExigence as e:
            erreur = str(e)
            break
    if Requirement is not None:
        try:
            SpecifierSet(texte)
            avis = None
        except InvalidSpecifier as e:
            avis = str(e).splitlines()[0]
        if (avis is None) != (erreur is None):
            ctx.constats.ecart(champ, erreur or "valide", avis or "valide")
        erreur = avis
    if erreur is not None:
        ctx.constats.defaut("SPECIFICATEUR", champ, f"{texte!r} : {erreur}")
        return None
    return specs


# --------------------------------------------------------------------------- #
# Expressions SPDX (PEP 639)
# --------------------------------------------------------------------------- #

def table_spdx(brute: str) -> dict[str, tuple[str, bool]]:
    """minuscules -> (identifiant canonique, déprécié)."""
    table: dict[str, tuple[str, bool]] = {}
    for jeton in brute.split():
        deprecie = jeton.endswith("!")
        ident = jeton.rstrip("!")
        table[ident.lower()] = (ident, deprecie)
    return table


class AnalyseurSPDX:
    """Descente récursive : or > and > with > (expr) | identifiant."""

    def __init__(self, texte: str, licences: dict, exceptions: dict) -> None:
        self.jetons = RE_JETON_SPDX.findall(texte)
        self.i = 0
        self.licences, self.exceptions = licences, exceptions
        self.deprecies: list[str] = []

    def suivant(self) -> str | None:
        """Jeton courant, sans le consommer."""
        return self.jetons[self.i] if self.i < len(self.jetons) else None

    def operateur(self, nom: str) -> bool:
        """Consomme l'opérateur AND/OR/WITH (casse indifférente)."""
        jeton = self.suivant()
        if jeton is not None and jeton.lower() == nom:
            self.i += 1
            return True
        return False

    def analyser(self) -> str:
        """Expression complète, forme canonique."""
        if not self.jetons:
            raise ValueError("expression vide")
        resultat = self.ou()
        if self.suivant() is not None:
            raise ValueError(f"jeton inattendu {self.suivant()!r}")
        return resultat

    def ou(self) -> str:
        """expr OR expr."""
        termes = [self.et()]
        while self.operateur("or"):
            termes.append(self.et())
        return " OR ".join(termes)

    def et(self) -> str:
        """expr AND expr."""
        termes = [self.avec()]
        while self.operateur("and"):
            termes.append(self.avec())
        return " AND ".join(termes)

    def avec(self) -> str:
        """licence WITH exception."""
        if self.suivant() == "(":
            self.i += 1
            interieur = self.ou()
            if self.suivant() != ")":
                raise ValueError("parenthèse fermante manquante")
            self.i += 1
            return f"({interieur})"
        licence = self.identifiant()
        if self.operateur("with"):
            jeton = self.suivant()
            if jeton is None or jeton.lower() not in self.exceptions:
                raise ValueError(f"exception de licence inconnue : {jeton!r}")
            self.i += 1
            ident, deprecie = self.exceptions[jeton.lower()]
            if deprecie:
                self.deprecies.append(ident)
            return f"{licence} WITH {ident}"
        return licence

    def identifiant(self) -> str:
        """Identifiant SPDX, avec « + » éventuel, ou LicenseRef-."""
        jeton = self.suivant()
        if jeton is None or jeton in ("(", ")") or jeton.lower() in ("and", "or", "with"):
            raise ValueError(f"identifiant de licence attendu, trouvé {jeton!r}")
        self.i += 1
        base, plus = (jeton[:-1], "+") if jeton.endswith("+") else (jeton, "")
        if base.lower().startswith("licenseref-"):
            reste = base[len("licenseref-"):]
            if plus or not RE_LICENCEREF.match(reste):
                raise ValueError(f"LicenseRef invalide : {jeton!r}")
            return "LicenseRef-" + reste
        if base.lower() not in self.licences:
            raise ValueError(f"licence inconnue : {base!r}")
        ident, deprecie = self.licences[base.lower()]
        if deprecie:
            self.deprecies.append(ident)
        return ident + plus


def juger_licence_expression(texte: str, ctx: Contexte) -> str | None:
    """Expression SPDX : validité, forme canonique, identifiants dépréciés."""
    analyseur = AnalyseurSPDX(texte, table_spdx(LICENCES_SPDX), table_spdx(EXCEPTIONS_SPDX))
    try:
        canonique, erreur = analyseur.analyser(), None
    except ValueError as e:
        canonique, erreur = None, str(e)
    if Requirement is not None:
        try:
            avis_canon, avis = str(canonicalize_license_expression(texte)), None
        except InvalidLicenseExpression as e:
            avis_canon, avis = None, str(e)
        if (avis is None) != (erreur is None) or (avis_canon and canonique and avis_canon != canonique):
            ctx.constats.ecart("project.license", erreur or str(canonique), avis or str(avis_canon))
        canonique, erreur = avis_canon, avis
    if erreur is not None:
        ctx.constats.defaut("SPDX", "project.license", f"{texte!r} n'est pas une expression SPDX valide : {erreur}")
        return None
    for ident in sorted(set(analyseur.deprecies)):
        ctx.constats.avertir("SPDX_DEPRECIE", "project.license", f"identifiant SPDX déprécié : {ident}")
    if canonique != texte:
        ctx.constats.avertir("SPDX_NON_CANONIQUE", "project.license", f"forme canonique : {canonique!r}")
    return canonique


# --------------------------------------------------------------------------- #
# Sections du fichier
# --------------------------------------------------------------------------- #

def table_ou_rien(valeur: object, champ: str, ctx: Contexte) -> dict | None:
    """Valeur attendue sous forme de table TOML."""
    if valeur is None:
        return None
    if not isinstance(valeur, dict):
        ctx.constats.defaut("TYPE", champ, f"table attendue, trouvé {type(valeur).__name__}")
        return None
    return valeur


def liste_de_chaines(valeur: object, champ: str, ctx: Contexte) -> list[str]:
    """Tableau de chaînes ; les éléments d'un autre type sont signalés et écartés."""
    if valeur is None:
        return []
    if not isinstance(valeur, list):
        ctx.constats.defaut("TYPE", champ, f"tableau attendu, trouvé {type(valeur).__name__}")
        return []
    bons = [v for v in valeur if isinstance(v, str)]
    if len(bons) != len(valeur):
        ctx.constats.defaut("TYPE", champ, "tous les éléments doivent être des chaînes")
    return bons


def verifier_premier_niveau(ctx: Contexte) -> None:
    """Seules build-system, project, tool et dependency-groups sont admises (PEP 518)."""
    for cle in sorted(set(ctx.donnees) - CLES_PREMIER_NIVEAU):
        ctx.constats.defaut("TABLE_INCONNUE", cle, "table de premier niveau réservée aux PEP (PEP 518)")
    table_ou_rien(ctx.donnees.get("tool"), "tool", ctx)


def verifier_build_system(ctx: Contexte) -> dict[str, object]:
    """[build-system] : PEP 518 et PEP 517."""
    bs = table_ou_rien(ctx.donnees.get("build-system"), "build-system", ctx)
    if bs is None:
        if "build-system" not in ctx.donnees:
            ctx.constats.avertir("BUILD_SYSTEM_ABSENT", "build-system",
                                 "absent : pip suppose setuptools.build_meta:__legacy__")
        return {}
    for cle in sorted(set(bs) - CLES_BUILD_SYSTEM):
        ctx.constats.defaut("CLE_INCONNUE", f"build-system.{cle}", "clé inconnue")
    if "requires" not in bs:
        ctx.constats.defaut("REQUIRES_ABSENT", "build-system.requires", "obligatoire quand la table existe (PEP 518)")
    exigences = [juger_exigence(e, f"build-system.requires[{i}]", ctx)
                 for i, e in enumerate(liste_de_chaines(bs.get("requires"), "build-system.requires", ctx))]
    backend = bs.get("build-backend")
    if backend is None:
        ctx.constats.avertir("BACKEND_ABSENT", "build-system.build-backend",
                             "absent : setuptools.build_meta:__legacy__ supposé")
    elif not isinstance(backend, str) or not re.match(r"^[A-Za-z_][\w.]*(:[A-Za-z_][\w.]*)?$", backend):
        ctx.constats.defaut("BACKEND_INVALIDE", "build-system.build-backend", f"{backend!r} : « module.chemin(:objet) » attendu")
    for i, chemin in enumerate(liste_de_chaines(bs.get("backend-path"), "build-system.backend-path", ctx)):
        if os.path.isabs(chemin) or ".." in Path(chemin).parts:
            ctx.constats.defaut("BACKEND_PATH", f"build-system.backend-path[{i}]", f"{chemin!r} doit être relatif et interne au projet")
    return {"backend": backend if isinstance(backend, str) else None,
            "requires": [e for e in exigences if e], "backend_path": bool(bs.get("backend-path"))}


def verifier_backend_fourni(systeme: dict[str, object], ctx: Contexte) -> str | None:
    """Le backend connu doit être installé par requires ; rend la distribution du backend."""
    backend = systeme.get("backend")
    distribution = BACKENDS.get(str(backend)) if backend else None
    if distribution is None or systeme.get("backend_path"):
        return distribution
    noms = {normaliser_nom(str(e["nom"])) for e in systeme.get("requires") or []}
    if normaliser_nom(distribution) not in noms:
        ctx.constats.defaut("BACKEND_NON_FOURNI", "build-system.requires",
                            f"{backend} vient de {distribution}, absent de requires : construction isolée impossible")
    return distribution


def specs_du_backend(systeme: dict[str, object], distribution: str) -> list[tuple[str, str]]:
    """Spécificateurs posés sur la distribution du backend dans requires."""
    for e in systeme.get("requires") or []:
        if normaliser_nom(str(e["nom"])) == normaliser_nom(distribution):
            return list(e["specificateurs"])
    return []


def verifier_plancher_pep639(systeme: dict[str, object], distribution: str | None, ctx: Contexte) -> None:
    """Expression de licence : le backend admis par requires sait-il l'écrire ?"""
    if distribution not in PLANCHERS_PEP639:
        return
    plancher, precedente = PLANCHERS_PEP639[distribution]
    specs = specs_du_backend(systeme, distribution)
    if not specs:
        return
    if admet(specs, plancher) is False and admet(specs, "9999") is False:
        ctx.constats.defaut("BACKEND_TROP_ANCIEN", "build-system.requires",
                            f"{distribution} borné sous {plancher} : {precedente} (mesuré) ne sait pas écrire License-Expression")
    elif admet(specs, precedente):
        ctx.constats.avertir("BACKEND_PLANCHER", "build-system.requires",
                             f"{distribution} {precedente} encore admis (mesuré : sans PEP 639) ; plancher conseillé >={plancher}")


def verifier_nom_version(projet: dict, dynamiques: set[str], ctx: Contexte) -> dict[str, object]:
    """name et version (PEP 621, PEP 503, PEP 440)."""
    nom = projet.get("name")
    resultat: dict[str, object] = {"nom": nom}
    if "name" in dynamiques:
        ctx.constats.defaut("NOM_DYNAMIQUE", "project.dynamic", "name ne peut pas être dynamique")
    if not isinstance(nom, str) or not RE_NOM.match(nom):
        ctx.constats.defaut("NOM", "project.name", f"{nom!r} : nom de projet absent ou invalide")
    else:
        resultat["nom_normalise"] = normaliser_nom(nom)
    version = projet.get("version")
    if version is not None and "version" in dynamiques:
        ctx.constats.defaut("STATIQUE_ET_DYNAMIQUE", "project.version", "version statique ET listée dans dynamic")
    if version is None and "version" not in dynamiques:
        ctx.constats.defaut("VERSION_ABSENTE", "project.version", "ni statique ni listée dans dynamic")
    if version is not None:
        resultat["version"] = verifier_version(version, ctx)
    return resultat


def verifier_version(version: object, ctx: Contexte) -> str | None:
    """Version PEP 440 et sa forme normalisée."""
    normale = version_normalisee(version) if isinstance(version, str) else None
    if Requirement is not None and isinstance(version, str):
        try:
            avis = str(Version(version))
        except InvalidVersion:
            avis = None
        if avis != normale:
            ctx.constats.ecart("project.version", str(normale), str(avis))
        normale = avis
    if normale is None:
        ctx.constats.defaut("VERSION", "project.version", f"{version!r} n'est pas une version PEP 440")
    elif normale != version:
        ctx.constats.avertir("VERSION_NON_NORMALISEE", "project.version", f"{version!r} : forme normalisée {normale!r}")
    return normale


def verifier_readme(valeur: object, ctx: Contexte) -> None:
    """readme : chemin (.md/.rst) ou table file/text + content-type."""
    if isinstance(valeur, str):
        verifier_fichier_projet(valeur, "project.readme", ctx)
        suffixe = Path(valeur).suffix.lower()
        if suffixe == ".txt":
            ctx.constats.avertir("README_SUFFIXE", "project.readme", ".txt : non prévu par la spécification, accepté par certains backends")
        elif suffixe not in (".md", ".rst"):
            ctx.constats.defaut("README_SUFFIXE", "project.readme", f"suffixe {suffixe or '(aucun)'!r} non reconnu : utiliser une table avec content-type")
        return
    table = table_ou_rien(valeur, "project.readme", ctx)
    if table is None:
        return
    for cle in sorted(set(table) - {"file", "text", "content-type"}):
        ctx.constats.defaut("CLE_INCONNUE", f"project.readme.{cle}", "clé inconnue")
    if ("file" in table) == ("text" in table):
        ctx.constats.defaut("README_TABLE", "project.readme", "exactement une des clés file ou text")
    if isinstance(table.get("file"), str):
        verifier_fichier_projet(table["file"], "project.readme.file", ctx)
    type_contenu = str(table.get("content-type") or "").split(";")[0].strip().lower()
    if type_contenu not in TYPES_README:
        ctx.constats.defaut("README_TYPE", "project.readme.content-type", f"{table.get('content-type')!r} absent ou non admis")


def verifier_fichier_projet(chemin: str, champ: str, ctx: Contexte) -> None:
    """Un chemin relatif au projet doit désigner un fichier existant."""
    if os.path.isabs(chemin) or ".." in Path(chemin).parts:
        ctx.constats.defaut("CHEMIN_HORS_PROJET", champ, f"{chemin!r} doit être relatif et interne au projet")
    elif not (ctx.dossier / chemin).is_file():
        ctx.constats.defaut("FICHIER_ABSENT", champ, f"{chemin!r} introuvable à côté du pyproject.toml")


def verifier_licence(projet: dict, distribution: str | None, systeme: dict, ctx: Contexte) -> dict[str, object]:
    """license et license-files (PEP 639)."""
    licence = projet.get("license")
    fichiers = projet.get("license-files")
    resultat: dict[str, object] = {}
    if isinstance(licence, str):
        resultat["licence"] = juger_licence_expression(licence, ctx)
        verifier_plancher_pep639(systeme, distribution, ctx)
        verifier_classifieurs_licence(projet, distribution, ctx)
    elif licence is not None:
        verifier_table_licence(licence, fichiers is not None, ctx)
    if fichiers is not None:
        for i, motif in enumerate(liste_de_chaines(fichiers, "project.license-files", ctx)):
            verifier_motif_licence(motif, f"project.license-files[{i}]", ctx)
    return resultat


def verifier_table_licence(licence: object, avec_fichiers: bool, ctx: Contexte) -> None:
    """Forme table dépréciée : file/text exclusifs, interdite avec license-files."""
    table = table_ou_rien(licence, "project.license", ctx)
    if table is None:
        return
    if avec_fichiers:
        ctx.constats.defaut("LICENCE_TABLE_ET_FICHIERS", "project.license", "table interdite avec license-files (PEP 639)")
    if ("file" in table) == ("text" in table) or set(table) - {"file", "text"}:
        ctx.constats.defaut("LICENCE_TABLE", "project.license", "exactement une des clés file ou text")
    if isinstance(table.get("file"), str):
        verifier_fichier_projet(table["file"], "project.license.file", ctx)
    ctx.constats.avertir("LICENCE_TABLE_DEPRECIEE", "project.license", "forme table dépréciée : préférer une expression SPDX (PEP 639)")


def verifier_classifieurs_licence(projet: dict, distribution: str | None, ctx: Contexte) -> None:
    """Expression SPDX et classifieurs License :: ensemble."""
    licences = [c for c in projet.get("classifiers") or [] if isinstance(c, str) and c.startswith("License ::")]
    if not licences:
        return
    if distribution in REFUS_CLASSIFIEUR_LICENCE:
        ctx.constats.defaut("CLASSIFIEUR_ET_EXPRESSION", "project.classifiers",
                            f"{distribution} refuse une expression avec {licences[0]!r} (mesuré)")
    else:
        ctx.constats.avertir("CLASSIFIEUR_ET_EXPRESSION", "project.classifiers",
                             f"{licences[0]!r} avec une expression : un backend PEUT refuser (PEP 639)")


def verifier_motif_licence(motif: str, champ: str, ctx: Contexte) -> None:
    """Motif PEP 639 valide et trouvant au moins un fichier."""
    if "\\" in motif or motif.startswith("/") or ".." in motif.split("/") or not RE_MOTIF_LICENCE.match(motif):
        ctx.constats.defaut("MOTIF_LICENCE", champ, f"{motif!r} : motif invalide (PEP 639)")
        return
    if motif.count("[") != motif.count("]"):
        ctx.constats.defaut("MOTIF_LICENCE", champ, f"{motif!r} : crochets déséquilibrés")
        return
    try:
        trouve = next((p for p in ctx.dossier.glob(motif) if p.is_file()), None)
    except (ValueError, OSError) as erreur:
        ctx.constats.defaut("MOTIF_LICENCE", champ, f"{motif!r} : {erreur}")
        return
    if trouve is None:
        ctx.constats.defaut("LICENCE_INTROUVABLE", champ, f"{motif!r} ne correspond à aucun fichier (PEP 639 : erreur)")


def verifier_personnes(projet: dict, ctx: Contexte) -> None:
    """authors / maintainers : tables name/email."""
    for cle in ("authors", "maintainers"):
        valeur = projet.get(cle)
        if valeur is None:
            continue
        if not isinstance(valeur, list):
            ctx.constats.defaut("TYPE", f"project.{cle}", "tableau de tables attendu")
            continue
        for i, personne in enumerate(valeur):
            verifier_personne(personne, f"project.{cle}[{i}]", ctx)


def verifier_personne(personne: object, champ: str, ctx: Contexte) -> None:
    """Une personne : clés name/email seulement, pas de virgule, courriel plausible."""
    if not isinstance(personne, dict):
        ctx.constats.defaut("TYPE", champ, "table {name, email} attendue")
        return
    for cle in sorted(set(personne) - {"name", "email"}):
        ctx.constats.defaut("CLE_INCONNUE", f"{champ}.{cle}", "seules name et email sont admises")
    nom, courriel = personne.get("name"), personne.get("email")
    if isinstance(nom, str) and "," in nom:
        ctx.constats.defaut("NOM_PERSONNE", f"{champ}.name", f"{nom!r} ne doit pas contenir de virgule")
    if courriel is not None and (not isinstance(courriel, str) or not RE_COURRIEL.match(courriel)):
        ctx.constats.defaut("COURRIEL", f"{champ}.email", f"{courriel!r} n'est pas une adresse valide")
    if not personne:
        ctx.constats.avertir("PERSONNE_VIDE", champ, "table vide")


def verifier_dependances(projet: dict, ctx: Contexte) -> dict[str, object]:
    """dependencies et optional-dependencies (PEP 508, PEP 685)."""
    deps = [juger_exigence(d, f"project.dependencies[{i}]", ctx)
            for i, d in enumerate(liste_de_chaines(projet.get("dependencies"), "project.dependencies", ctx))]
    signaler_exigences(deps, "project.dependencies", ctx, projet_principal=True)
    extras = table_ou_rien(projet.get("optional-dependencies"), "project.optional-dependencies", ctx) or {}
    vus: dict[str, str] = {}
    for extra, liste in extras.items():
        champ = f"project.optional-dependencies.{extra}"
        verifier_nom_groupe(extra, vus, champ, ctx)
        exigences = [juger_exigence(d, f"{champ}[{i}]", ctx) for i, d in enumerate(liste_de_chaines(liste, champ, ctx))]
        signaler_exigences(exigences, champ, ctx, projet_principal=False)
    return {"dependances": sum(1 for d in deps if d), "extras": sorted(extras)}


def verifier_nom_groupe(nom: str, vus: dict[str, str], champ: str, ctx: Contexte) -> None:
    """Nom d'extra ou de groupe : valide, normalisé, sans collision après normalisation."""
    if not RE_NOM.match(nom):
        ctx.constats.defaut("NOM_GROUPE", champ, f"{nom!r} n'est pas un nom valide")
        return
    normal = normaliser_nom(nom)
    if normal != nom:
        ctx.constats.avertir("NOM_NON_NORMALISE", champ, f"{nom!r} : forme normalisée {normal!r} (PEP 685)")
    if normal in vus:
        ctx.constats.defaut("NOM_EN_DOUBLE", champ, f"{nom!r} et {vus[normal]!r} se normalisent en {normal!r}")
    vus[normal] = nom


def signaler_exigences(exigences: list, champ: str, ctx: Contexte, projet_principal: bool) -> None:
    """Doublons, URL directes et marqueurs extra dans les dépendances principales."""
    vus: set[str] = set()
    for e in (x for x in exigences if x):
        nom = normaliser_nom(str(e["nom"]))
        if nom in vus and not e.get("marqueur"):
            ctx.constats.avertir("DEPENDANCE_EN_DOUBLE", champ, f"{e['nom']} listé plusieurs fois")
        vus.add(nom)
        if e.get("url"):
            ctx.constats.avertir("DEPENDANCE_URL", champ, f"{e['nom']} @ {e['url']} : PyPI refuse les dépendances par URL")
        if projet_principal and e.get("marqueur") and re.search(r"\bextra\b", str(e["marqueur"])):
            ctx.constats.avertir("MARQUEUR_EXTRA", champ, f"{e['nom']} : marqueur « extra » sans effet dans dependencies")


def verifier_points_entree(projet: dict, ctx: Contexte) -> int:
    """scripts, gui-scripts et entry-points."""
    total = 0
    for cle in ("scripts", "gui-scripts"):
        total += verifier_groupe_ep(projet.get(cle), f"project.{cle}", ctx)
    groupes = table_ou_rien(projet.get("entry-points"), "project.entry-points", ctx) or {}
    for groupe, table in groupes.items():
        champ = f"project.entry-points.{groupe}"
        if groupe in ("console_scripts", "gui_scripts"):
            ctx.constats.defaut("GROUPE_INTERDIT", champ, f"utiliser [project.{groupe.replace('_', '-').replace('console-', '')}]")
        elif not re.match(r"^\w+(\.\w+)*$", groupe):
            ctx.constats.avertir("GROUPE_NOM", champ, f"{groupe!r} : lettres, chiffres, _ et . conseillés")
        total += verifier_groupe_ep(table, champ, ctx)
    return total


def verifier_groupe_ep(table: object, champ: str, ctx: Contexte) -> int:
    """Chaque point d'entrée : nom sans « = », référence « module:objet »."""
    table = table_ou_rien(table, champ, ctx)
    if table is None:
        return 0
    for nom, ref in table.items():
        if isinstance(ref, dict):
            ctx.constats.defaut("EP_IMBRIQUE", f"{champ}.{nom}", "sous-table interdite : un seul niveau")
            continue
        m = RE_REFERENCE.match(ref) if isinstance(ref, str) else None
        if m is None or not all(p.isidentifier() for p in (m.group(1) + "." + (m.group(2) or "x")).split(".")):
            ctx.constats.defaut("EP_REFERENCE", f"{champ}.{nom}", f"{ref!r} : « module.chemin:objet » attendu")
        elif m.group(3):
            ctx.constats.avertir("EP_EXTRAS", f"{champ}.{nom}", "extras entre crochets dépréciés")
        if "=" in nom or nom != nom.strip() or not nom:
            ctx.constats.defaut("EP_NOM", f"{champ}.{nom}", "nom vide, avec « = » ou espaces de bord")
    return len(table)


def verifier_classifieurs(projet: dict, ctx: Contexte) -> None:
    """Syntaxe, classifieurs License :: dépréciés, versions Python exclues."""
    classifieurs = liste_de_chaines(projet.get("classifiers"), "project.classifiers", ctx)
    rp = projet.get("requires-python") if isinstance(projet.get("requires-python"), str) else None
    for c in classifieurs:
        if not all(m.strip() for m in c.split("::")):
            ctx.constats.defaut("CLASSIFIEUR", "project.classifiers", f"{c!r} : segment vide")
        if c.startswith("License ::") and not isinstance(projet.get("license"), str):
            ctx.constats.avertir("CLASSIFIEUR_LICENCE", "project.classifiers", f"{c!r} déprécié (PEP 639)")
        m = RE_CLASSIFIEUR_PY.match(c)
        if m and rp is not None:
            specs = juger_specificateurs(rp, "project.requires-python", Contexte(ctx.dossier, Constats(), {}))
            if specs and admet(specs, f"{m.group(1)}.{m.group(2)}.0") is False and admet(specs, f"{m.group(1)}.{m.group(2)}.99") is False:
                ctx.constats.avertir("CLASSIFIEUR_PYTHON", "project.classifiers", f"{c!r} exclu par requires-python {rp!r}")


def verifier_dynamic(projet: dict, ctx: Contexte) -> set[str]:
    """dynamic : clés connues seulement, jamais statiques en même temps."""
    dynamiques = set(liste_de_chaines(projet.get("dynamic"), "project.dynamic", ctx))
    for cle in sorted(dynamiques - CLES_PROJECT):
        ctx.constats.defaut("DYNAMIC_INCONNU", "project.dynamic", f"{cle!r} n'est pas une clé de [project]")
    for cle in sorted(dynamiques & set(projet) - {"version", "name", "dynamic"}):
        ctx.constats.defaut("STATIQUE_ET_DYNAMIQUE", f"project.{cle}", "statique ET listé dans dynamic")
    return dynamiques


def verifier_divers(projet: dict, ctx: Contexte) -> None:
    """description, requires-python, keywords, urls, import-names."""
    description = projet.get("description")
    if description is not None and (not isinstance(description, str) or "\n" in description):
        ctx.constats.defaut("DESCRIPTION", "project.description", "une seule ligne de texte attendue")
    rp = projet.get("requires-python")
    if rp is not None:
        if isinstance(rp, str):
            juger_specificateurs(rp, "project.requires-python", ctx)
        else:
            ctx.constats.defaut("TYPE", "project.requires-python", "chaîne attendue")
    liste_de_chaines(projet.get("keywords"), "project.keywords", ctx)
    for cle in ("import-names", "import-namespaces"):
        for nom in liste_de_chaines(projet.get(cle), f"project.{cle}", ctx):
            module = nom.split(";")[0].strip()
            if not all(p.isidentifier() for p in module.split(".")):
                ctx.constats.defaut("IMPORT_NOM", f"project.{cle}", f"{nom!r} : chemin de module invalide")
    for etiquette, url in (table_ou_rien(projet.get("urls"), "project.urls", ctx) or {}).items():
        if not isinstance(url, str) or not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", url):
            ctx.constats.avertir("URL", f"project.urls.{etiquette}", f"{url!r} ne ressemble pas à une URL")
        if len(etiquette) > 32:
            ctx.constats.avertir("URL_ETIQUETTE", f"project.urls.{etiquette}", "étiquette de plus de 32 caractères")


def verifier_project(systeme: dict[str, object], distribution: str | None, ctx: Contexte) -> dict[str, object]:
    """[project] : PEP 621 et PEP 639."""
    projet = table_ou_rien(ctx.donnees.get("project"), "project", ctx)
    if projet is None:
        return {}
    for cle in sorted(set(projet) - CLES_PROJECT):
        ctx.constats.defaut("CLE_INCONNUE", f"project.{cle}", "clé inconnue de [project]")
    dynamiques = verifier_dynamic(projet, ctx)
    resultat = verifier_nom_version(projet, dynamiques, ctx)
    if "readme" in projet:
        verifier_readme(projet["readme"], ctx)
    resultat.update(verifier_licence(projet, distribution, systeme, ctx))
    verifier_personnes(projet, ctx)
    verifier_classifieurs(projet, ctx)
    verifier_divers(projet, ctx)
    resultat.update(verifier_dependances(projet, ctx))
    resultat["points_entree"] = verifier_points_entree(projet, ctx)
    resultat["dynamic"] = sorted(dynamiques)
    return resultat


def verifier_groupes(ctx: Contexte) -> list[str]:
    """[dependency-groups] (PEP 735)."""
    groupes = table_ou_rien(ctx.donnees.get("dependency-groups"), "dependency-groups", ctx) or {}
    vus: dict[str, str] = {}
    inclusions: dict[str, list[str]] = {}
    for nom, liste in groupes.items():
        champ = f"dependency-groups.{nom}"
        verifier_nom_groupe(nom, vus, champ, ctx)
        if not isinstance(liste, list):
            ctx.constats.defaut("TYPE", champ, "tableau attendu")
            continue
        inclusions[normaliser_nom(nom)] = verifier_entrees_groupe(liste, champ, ctx)
    for nom, cibles in inclusions.items():
        for cible in cibles:
            if cible not in inclusions:
                ctx.constats.defaut("GROUPE_INCLUS_ABSENT", f"dependency-groups.{nom}", f"include-group {cible!r} inexistant")
    for nom in sorted(groupes_en_cycle(inclusions)):
        ctx.constats.defaut("GROUPE_CYCLE", f"dependency-groups.{nom}", "inclusion circulaire")
    return sorted(groupes)


def verifier_entrees_groupe(liste: list, champ: str, ctx: Contexte) -> list[str]:
    """Chaînes PEP 508 ou tables {include-group = …} ; rend les groupes inclus."""
    inclus: list[str] = []
    for i, entree in enumerate(liste):
        if isinstance(entree, dict):
            if set(entree) != {"include-group"} or not isinstance(entree["include-group"], str):
                ctx.constats.defaut("GROUPE_ENTREE", f"{champ}[{i}]", "table {include-group = \"nom\"} attendue")
            else:
                inclus.append(normaliser_nom(entree["include-group"]))
        else:
            juger_exigence(entree, f"{champ}[{i}]", ctx)
    return inclus


def groupes_en_cycle(inclusions: dict[str, list[str]]) -> set[str]:
    """Groupes atteignables depuis eux-mêmes."""
    en_cycle: set[str] = set()
    for depart in inclusions:
        pile, vus = list(inclusions[depart]), set()
        while pile:
            courant = pile.pop()
            if courant == depart:
                en_cycle.add(depart)
                break
            if courant in vus:
                continue
            vus.add(courant)
            pile += inclusions.get(courant, [])
    return en_cycle


# --------------------------------------------------------------------------- #
# Fichier, moteur optionnel validate-pyproject
# --------------------------------------------------------------------------- #

def lire_toml(chemin: Path, taille_max: int, constats: Constats) -> dict | None:
    """Lit et analyse ; les défauts de lecture sont des non-conformités."""
    if chemin.stat().st_size > taille_max:
        constats.defaut("TROP_GROS", "", f"{chemin.stat().st_size} octets > borne {taille_max}")
        return None
    octets = chemin.read_bytes()
    try:
        texte = octets.decode("utf-8")
    except UnicodeDecodeError as erreur:
        constats.defaut("ENCODAGE", "", f"le TOML doit être en utf-8 : {erreur}")
        return None
    if re.search(r"^(<{7}|>{7}|={7})( |$)", texte, re.MULTILINE):
        constats.defaut("CONFLIT_GIT", "", "marqueurs de conflit de fusion présents")
    try:
        return tomllib.loads(texte)
    except tomllib.TOMLDecodeError as erreur:
        constats.defaut("TOML_INVALIDE", "", str(erreur))
        return None


def avis_validate_pyproject(donnees: dict) -> dict[str, object] | None:
    """Verdict de validate-pyproject, téléchargement des classifieurs désactivé."""
    if vp_api is None:
        return None
    os.environ[VARIABLE_RESEAU_VP] = "1"
    try:
        vp_api.Validator()(donnees)
    except vp_errors.ValidationError as erreur:
        return {"conforme": False, "message": str(erreur).splitlines()[0][:400]}
    except ValueError as erreur:
        return {"conforme": False, "message": f"{type(erreur).__name__}: {str(erreur)[:300]}"}
    return {"conforme": True, "message": ""}


def examiner_fichier(chemin: Path, taille_max: int) -> dict[str, object]:
    """Examen complet d'un pyproject.toml."""
    constats = Constats()
    donnees = lire_toml(chemin, taille_max, constats)
    rapport: dict[str, object] = {"toml_valide": donnees is not None}
    if donnees is not None:
        ctx = Contexte(chemin.parent, constats, donnees)
        verifier_premier_niveau(ctx)
        systeme = verifier_build_system(ctx)
        distribution = verifier_backend_fourni(systeme, ctx)
        rapport["backend"] = systeme.get("backend")
        rapport.update(verifier_project(systeme, distribution, ctx))
        rapport["groupes"] = verifier_groupes(ctx)
        rapport["outils"] = sorted(donnees.get("tool") or {}) if isinstance(donnees.get("tool"), dict) else []
        if "project" not in donnees:
            constats.avertir("PROJECT_ABSENT", "project", "pas de [project] : métadonnées laissées au backend")
        vp = avis_validate_pyproject(donnees)
        if vp is not None:
            rapport["validate_pyproject"] = vp
    rapport.update({"conforme": not constats.defauts, "defauts": constats.defauts,
                    "avertissements": constats.avertissements, "ecarts_moteurs": constats.ecarts_moteurs,
                    "constats_omis": constats.omis})
    if "validate_pyproject" in rapport:
        rapport["validate_pyproject"]["accord"] = rapport["validate_pyproject"]["conforme"] == rapport["conforme"]
    return rapport


def collecter(chemins: list[Path], maximum: int) -> list[Path]:
    """pyproject.toml désignés ou trouvés sous les dossiers."""
    fichiers: list[Path] = []
    for chemin in chemins:
        if not chemin.exists():
            raise EntreeInvalide(f"chemin introuvable : {chemin}")
        if chemin.is_dir():
            fichiers += chercher(chemin, maximum)
        elif chemin.suffix.lower() != ".toml":
            raise EntreeInvalide(f"{chemin.name} : un fichier .toml (pyproject.toml) ou un dossier est attendu")
        else:
            fichiers.append(chemin)
    return fichiers[:maximum]


def chercher(dossier: Path, maximum: int) -> list[Path]:
    """pyproject.toml sous un dossier, sans dossiers cachés, environnements ni liens."""
    trouves: list[Path] = []
    for courant, sous, noms in os.walk(dossier):
        sous[:] = sorted(d for d in sous if not d.startswith(".") and d not in DOSSIERS_IGNORES)
        if "pyproject.toml" in noms and not (Path(courant) / "pyproject.toml").is_symlink():
            trouves.append(Path(courant) / "pyproject.toml")
            if len(trouves) >= maximum:
                break
    return trouves


def libelle(chemin: Path, base: Path) -> str:
    """Chemin affiché relatif à la base quand c'est possible."""
    try:
        return str(chemin.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(chemin)


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
    noms = [n for n, m in (("packaging", Requirement), ("validate-pyproject", vp_api)) if m is not None]
    return "+".join(noms) if noms else "stdlib"


def construire_sortie(rapports: list[dict]) -> dict[str, object]:
    """Objet de sortie complet."""
    examines = [r["chemin"] for r in rapports]
    return {
        "outil": "verifier_pyproject",
        "moteur": moteur(),
        "liste_spdx": f"{NORME_LICENCES} {VERSION_SPDX}",
        "denominateur": len(rapports),
        "examines": examines[:MAX_EXAMINES],
        "examines_tronques": len(examines) > MAX_EXAMINES,
        "bilan": {"conformes": sum(1 for r in rapports if r["conforme"]),
                  "non_conformes": sum(1 for r in rapports if not r["conforme"]),
                  "ecarts_moteurs": sum(len(r["ecarts_moteurs"]) for r in rapports)},
        "fichiers": rapports,
        "contrat": extraire_contrat(__doc__ or ""),
    }


def afficher_humain(sortie: dict[str, object]) -> None:
    """Rapport lisible."""
    print(f"verifier_pyproject — {sortie['denominateur']} fichier(s), moteur {sortie['moteur']}, liste {sortie['liste_spdx']}")
    for r in sortie["fichiers"]:
        etat = ETAT_CONFORME if r["conforme"] else ETAT_NON_CONFORME
        print(f"\n[{etat}] {r['chemin']}  {r.get('nom') or ''} {r.get('version') or ''}  backend {r.get('backend')}")
        for d in r["defauts"]:
            print(f"  défaut {d['code']} {d['champ']} : {d['detail']}")
        for a in r["avertissements"]:
            print(f"  avertissement {a['code']} {a['champ']} : {a['detail']}")
        for e in r["ecarts_moteurs"]:
            print(f"  écart de moteurs {e['champ']} : stdlib {e['stdlib']!r} / packaging {e['packaging']!r}")
        if "validate_pyproject" in r:
            vp = r["validate_pyproject"]
            print(f"  validate-pyproject : {'conforme' if vp['conforme'] else 'non conforme'} {vp['message']}")
    b = sortie["bilan"]
    print(f"\nBilan : {b['conformes']} conforme(s), {b['non_conformes']} non conforme(s)")


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    p = argparse.ArgumentParser(
        description=("Vérifie qu'un pyproject.toml est conforme aux PEP 517/518/621/639/685/735 "
                     "et cohérent avec son projet (fichiers de licence, readme) et son backend."),
        epilog=("Exemple : python verifier_pyproject.py pyproject.toml --json\n"
                "          python verifier_pyproject.py depot/   (tous les pyproject.toml du dépôt)\n"
                "Codes : 0 conforme, 1 non conforme, 2 entrée invalide, 3 rien à examiner."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("chemins", nargs="+", type=Path, help="pyproject.toml ou dossiers à examiner")
    p.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    p.add_argument("--racine", type=Path, default=None, help="base des chemins relatifs (défaut : dossier courant)")
    p.add_argument("--taille-max", type=int, default=TAILLE_MAX_DEFAUT, help="octets maximum par fichier")
    p.add_argument("--fichiers-max", type=int, default=FICHIERS_MAX_DEFAUT, help="fichiers maximum")
    return p


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    base = args.racine or Path.cwd()
    absents = [n for n, m in (("packaging", Requirement), ("validate-pyproject", vp_api)) if m is None]
    if absents:
        print(f"{', '.join(absents)} absent(s) : PEP 508/440/SPDX jugés par le repli stdlib, sans second avis",
              file=sys.stderr)
    try:
        if args.taille_max <= 0 or args.fichiers_max <= 0:
            raise EntreeInvalide("--taille-max et --fichiers-max doivent être positifs")
        fichiers = collecter([c if c.is_absolute() else base / c for c in args.chemins], args.fichiers_max)
        rapports = []
        for f in fichiers:
            rapport = examiner_fichier(f, args.taille_max)
            rapports.append({"chemin": libelle(f, base), **rapport})
    except EntreeInvalide as erreur:
        print(f"entrée invalide : {erreur}", file=sys.stderr)
        return 2
    except OSError as erreur:
        print(f"entrée invalide : lecture impossible ({erreur})", file=sys.stderr)
        return 2
    sortie = construire_sortie(rapports)
    if args.json:
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    if not rapports:
        print("dénominateur nul : aucun pyproject.toml trouvé, rien à examiner", file=sys.stderr)
        return 3
    if not args.json:
        afficher_humain(sortie)
    return 1 if sortie["bilan"]["non_conformes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
