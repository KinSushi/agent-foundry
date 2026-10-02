"""Dire quelles licences portent des dépendances Python et si elles s'accordent avec celle du projet.
Le champ de licence n'est pas fiable : mesuré dans cette session (importlib.metadata.distributions()
sur un venv de 36 distributions), 13 seulement déclarent License-Expression (PEP 639) ; 19 n'ont que le champ libre License (dont
« Dual License », le sigle de la PSF et un texte de licence complet) et 4 n'ont que des classifieurs.

QUESTION
    Quelles licences portent ces dépendances, et sont-elles compatibles avec la licence de mon
    projet ?
MESURE
    Sources des dépendances : pyproject.toml (dependencies, optional-dependencies,
    dependency-groups, tool.poetry), requirements*.txt / *.in, uv.lock, ou toutes les
    distributions installées (--environnement : interpréteur courant ; --site <dossier> : ce
    dossier seul) ; --expression analyse une expression en ligne. Licence d'une distribution lue
    dans ses métadonnées : License-Expression (PEP 639), sinon champ License (nom courant, texte
    reconnu par empreinte, ou expression), sinon classifieurs « License :: ». Expressions
    analysées selon la grammaire officielle (AND, OR, WITH, parenthèses, identifiants dépréciés
    normalisés). Compatibilité avec --cible (ou la licence déclarée du pyproject.toml) par une
    table de catégories écrite dans l'outil (permissive, Apache-2.0, copyleft faible, LGPL, GPL
    v2 seule / ultérieure, GPL v3, AGPL v3, restrictive, inconnue) : OR prend la meilleure
    branche, AND la pire, WITH n'aggrave jamais ; licence imprécise (plusieurs candidates qui
    divergent) ou inconnue → À VÉRIFIER. Si license-expression est importable, chaque expression
    est revalidée par elle et les écarts sont rapportés.
HYPOTHÈSES
    Les métadonnées installées décrivent bien la distribution ; la dépendance est utilisée par
    import (pas de copie de code source) ; le projet est distribué sous la cible. Les métadonnées
    ne sont lues qu'avec --environnement ou --site : sans elles, les licences des dépendances
    d'un fichier restent « non résolues ».
LIMITES
    Ce n'est pas un avis juridique : la table est prudente et schématique (position de la FSF
    sur Apache-2.0 et GPL v2, copyleft par combinaison à l'import), elle ignore les licences
    des sous-fichiers, les exceptions particulières, les dépendances transitives non installées
    et le mode de distribution (SaaS, interne). Un include -r d'un requirements n'est pas suivi.
CONTRE-EXEMPLES
    Constaté : python-dateutil 2.9.0.post0 déclare « Dual License » et les classifieurs BSD et
    Apache ; pour --cible GPL-2.0-only l'outil rend À VÉRIFIER (candidates divergentes). Son
    fichier de licence, lu à la main, place les contributions postérieures au 2017-12-01 sous
    Apache-2.0 et les plus anciennes sous BSD-3-Clause : l'expression exacte serait
    « Apache-2.0 AND BSD-3-Clause », donc INCOMPATIBLE PROBABLE — l'outil sous-estime le risque.
    Constaté aussi : rfc3987-syntax 1.1.0 déclare License-Expression MIT et le classifieur
    Apache ; l'outil suit MIT (règle PEP 639) et ne fait que noter la contradiction.
INVOCATION
    {outil} --expression "MIT OR Apache-2.0" --cible GPL-2.0-only --json
    {outil} --environnement --cible MIT --json
DOMAINE
    Revue de dépendances Python avant publication ou livraison ; tri des cas à soumettre à un
    juriste, jamais décision juridique.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import license_expression
except ImportError:
    license_expression = None

RACINE = Path(__file__).resolve().parent

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
                  TITRE_INVOCATION, "DOMAINE")
OPERATEUR_AND = "AND"
OPERATEUR_WITH = "WITH"
COMPATIBLE = "COMPATIBLE"
MOT_VERIFIER = "VÉRIFIER"
MOT_INCOMPATIBLE = "INCOMPATIBLE"
MOT_PROBABLE = "PROBABLE"
A_VERIFIER = f"À {MOT_VERIFIER}"
INCOMPATIBLE = f"{MOT_INCOMPATIBLE} {MOT_PROBABLE}"
SPDX = "SPDX"
MOT_LGPL = "LGPL"
MOT_AGPL = "AGPL"
SOURCES_EXPRESSION = frozenset({"License-Expression", "License (expression)", "--expression"})
AVERTISSEMENT_JURIDIQUE = "Indication technique et prudente, pas un avis juridique."
MESSAGE_LIB_ABSENTE = ("license-expression absent : analyse des expressions par la grammaire "
                       "intégrée (stdlib), sans revalidation externe.")
MAX_EXAMINES = 200
LONGUEUR_MAX_EXPRESSION = 4096
MAX_OCTETS_SOURCE = 20 * 1024 * 1024

VERDICT_PAR_LETTRE = MappingProxyType({"C": COMPATIBLE, "V": A_VERIFIER, "I": INCOMPATIBLE})
RANG_VERDICT = MappingProxyType({COMPATIBLE: 0, A_VERIFIER: 1, INCOMPATIBLE: 2})
CIBLES = ("PERM", "PROP", "MPL", "LGPL21", "LGPL3", "GPL2O", "GPL2P", "GPL3", "AGPL3")
# Une ligne par catégorie de dépendance, une lettre par catégorie de cible (ordre de CIBLES).
# C compatible, V à vérifier, I incompatible probable. Prudence : tout doute est un V.
MATRICE = MappingProxyType({
    "permissive": "CCCCCCCCC",
    "apache": "CCCVCIVCC",
    "permissive_gpl_incompatible": "CCVVVIIII",
    "mpl2": "VVCCCCCCC",
    "copyleft_faible_gpl_incompatible": "VVVVVIIII",
    "lgpl2": "VVVCCCCCC",
    "lgpl3": "VVVVCIVCC",
    "gpl2_seule": "IIIIICVII",
    "gpl2_ulterieure": "IIIIICCCC",
    "gpl3": "IIIIIIVCC",
    "agpl3": "IIIIIIIVC",
    "restrictive": "IVIIIIIII",
    "inconnue": "VVVVVVVVV",
})
RAISON_CATEGORIE = MappingProxyType({
    "permissive": "permissive (avis de copyright à conserver)",
    "apache": "Apache-2.0 (clauses de brevets : incompatible GPL v2 selon la FSF)",
    "permissive_gpl_incompatible": "permissive mais incompatible GPL selon la FSF",
    "mpl2": "copyleft faible par fichier (MPL-2.0, licences secondaires GNU admises)",
    "copyleft_faible_gpl_incompatible": "copyleft faible incompatible GPL",
    "lgpl2": "copyleft faible LGPL v2.x (obligations de remplacement de la bibliothèque)",
    "lgpl3": "copyleft faible LGPL v3",
    "gpl2_seule": "copyleft fort GPL v2 seule",
    "gpl2_ulterieure": "copyleft fort GPL v2 ou ultérieure",
    "gpl3": "copyleft fort GPL v3",
    "agpl3": "copyleft fort AGPL v3 (y compris l'usage en réseau)",
    "restrictive": "licence non libre ou à usage restreint",
    "inconnue": "licence hors de la table de l'outil",
})
CATEGORIES = MappingProxyType({
    # « permissive » : licences que la FSF déclare compatibles GPL ; le reste va en « inconnue ».
    "permissive": ("MIT", "MIT-0", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "ISC", "PSF-2.0",
                   "Python-2.0", "Python-2.0.1", "Zlib", "Unlicense", "CC0-1.0", "BSL-1.0", "X11",
                   "HPND", "NCSA", "UPL-1.0", "Unicode-3.0", "Unicode-DFS-2016", "curl",
                   "Artistic-2.0", "BSD-3-Clause-Clear", "WTFPL"),
    "apache": ("Apache-2.0",),
    "permissive_gpl_incompatible": ("Apache-1.1", "Apache-1.0", "BSD-4-Clause", "OpenSSL", "AFL-2.1",
                                    "AFL-3.0", "PHP-3.01"),
    "mpl2": ("MPL-2.0",),
    "copyleft_faible_gpl_incompatible": ("MPL-1.1", "MPL-1.0", "MPL-2.0-no-copyleft-exception",
                                         "EPL-1.0", "EPL-2.0", "CDDL-1.0", "CDDL-1.1", "CPL-1.0",
                                         "EUPL-1.1", "EUPL-1.2", "OSL-3.0", "Artistic-1.0"),
    "lgpl2": ("LGPL-2.0-only", "LGPL-2.0-or-later", "LGPL-2.1-only", "LGPL-2.1-or-later"),
    "lgpl3": ("LGPL-3.0-only", "LGPL-3.0-or-later"),
    "gpl2_seule": ("GPL-2.0-only",),
    "gpl2_ulterieure": ("GPL-2.0-or-later", "GPL-1.0-or-later"),
    "gpl3": ("GPL-3.0-only", "GPL-3.0-or-later"),
    "agpl3": ("AGPL-3.0-only", "AGPL-3.0-or-later"),
    "restrictive": ("SSPL-1.0", "BUSL-1.1", "Elastic-2.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0",
                    "CC-BY-ND-4.0", "CC-BY-NC-ND-4.0", "LicenseRef-Proprietary",
                    "LicenseRef-Proprietaire"),
})
CATEGORIE_CIBLE = MappingProxyType({
    "permissive": "PERM", "apache": "PERM", "permissive_gpl_incompatible": "PERM", "mpl2": "MPL",
    "lgpl2": "LGPL21", "lgpl3": "LGPL3", "gpl2_seule": "GPL2O", "gpl2_ulterieure": "GPL2P",
    "gpl3": "GPL3", "agpl3": "AGPL3", "restrictive": "PROP",
})
DEPRECIES = MappingProxyType({
    "gpl-1.0": "GPL-1.0-only", "gpl-1.0+": "GPL-1.0-or-later", "gpl-2.0": "GPL-2.0-only",
    "gpl-2.0+": "GPL-2.0-or-later", "gpl-3.0": "GPL-3.0-only", "gpl-3.0+": "GPL-3.0-or-later",
    "lgpl-2.0": "LGPL-2.0-only", "lgpl-2.0+": "LGPL-2.0-or-later", "lgpl-2.1": "LGPL-2.1-only",
    "lgpl-2.1+": "LGPL-2.1-or-later", "lgpl-3.0": "LGPL-3.0-only", "lgpl-3.0+": "LGPL-3.0-or-later",
    "agpl-3.0": "AGPL-3.0-only", "agpl-3.0+": "AGPL-3.0-or-later",
    "licenseref-proprietary": "LicenseRef-Proprietary", "proprietary": "LicenseRef-Proprietary",
    "proprietaire": "LicenseRef-Proprietary", "propriétaire": "LicenseRef-Proprietary",
})
EXCEPTIONS = ("Classpath-exception-2.0", "LLVM-exception", "GCC-exception-2.0", "GCC-exception-3.1",
              "Autoconf-exception-2.0", "Autoconf-exception-3.0", "Bison-exception-2.2",
              "Font-exception-2.0", "Linux-syscall-note", "OpenJDK-assembly-exception-1.0",
              "Qt-GPL-exception-1.0", "Qt-LGPL-exception-1.1", "Universal-FOSS-exception-1.0",
              "WxWindows-exception-3.1", "eCos-exception-2.0", "freertos-exception-2.0",
              "GPL-3.0-linking-exception", "GPL-3.0-linking-source-exception",
              "LGPL-3.0-linking-exception", "Libtool-exception", "LZMA-exception",
              "OCaml-LGPL-linking-exception", "openvpn-openssl-exception", "Swift-exception")
FAMILLE_BSD = ("BSD-2-Clause", "BSD-3-Clause", "0BSD", "BSD-4-Clause")
FAMILLE_GPL = ("GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later")
FAMILLE_LGPL = ("LGPL-2.0-only", "LGPL-2.0-or-later", "LGPL-2.1-only", "LGPL-2.1-or-later",
                "LGPL-3.0-only", "LGPL-3.0-or-later")
NOMS_COURANTS = MappingProxyType({
    "mit": ("MIT",), "mit license": ("MIT",), "the mit license": ("MIT",), "expat": ("MIT",),
    "mit licence": ("MIT",), "bsd": FAMILLE_BSD, "bsd license": FAMILLE_BSD,
    "new bsd": ("BSD-3-Clause",), "new bsd license": ("BSD-3-Clause",),
    "modified bsd": ("BSD-3-Clause",), "modified bsd license": ("BSD-3-Clause",),
    "3-clause bsd": ("BSD-3-Clause",), "bsd 3-clause": ("BSD-3-Clause",),
    "bsd-3": ("BSD-3-Clause",), "simplified bsd": ("BSD-2-Clause",), "bsd 2-clause": ("BSD-2-Clause",),
    "2-clause bsd": ("BSD-2-Clause",), "apache": ("Apache-2.0", "Apache-1.1"),
    "apache 2": ("Apache-2.0",), "apache 2.0": ("Apache-2.0",), "apache-2": ("Apache-2.0",),
    "apache license 2.0": ("Apache-2.0",), "apache license, version 2.0": ("Apache-2.0",),
    "apache license version 2.0": ("Apache-2.0",), "apache software license": ("Apache-2.0", "Apache-1.1"),
    "apache software license 2.0": ("Apache-2.0",), "asl 2.0": ("Apache-2.0",),
    "isc": ("ISC",), "isc license": ("ISC",), "iscl": ("ISC",), "psf": ("PSF-2.0",),
    "psfl": ("PSF-2.0",), "psf license": ("PSF-2.0",),
    "python software foundation license": ("PSF-2.0",), "mpl 2.0": ("MPL-2.0",),
    "mozilla public license 2.0": ("MPL-2.0",), "mozilla public license 2.0 (mpl 2.0)": ("MPL-2.0",),
    "lgpl": FAMILLE_LGPL, "lgplv2": ("LGPL-2.0-only", "LGPL-2.1-only"), "lgplv2+": ("LGPL-2.1-or-later",),
    "lgplv3": ("LGPL-3.0-only", "LGPL-3.0-or-later"), "lgplv3+": ("LGPL-3.0-or-later",),
    "gpl": FAMILLE_GPL, "gplv2": ("GPL-2.0-only", "GPL-2.0-or-later"), "gplv2+": ("GPL-2.0-or-later",),
    "gplv3": ("GPL-3.0-only", "GPL-3.0-or-later"), "gplv3+": ("GPL-3.0-or-later",),
    "agplv3": ("AGPL-3.0-only", "AGPL-3.0-or-later"), "agplv3+": ("AGPL-3.0-or-later",),
    "zlib": ("Zlib",), "zlib/libpng": ("Zlib",), "boost": ("BSL-1.0",),
    "boost software license 1.0": ("BSL-1.0",), "unlicense": ("Unlicense",),
    "the unlicense": ("Unlicense",), "cc0": ("CC0-1.0",),
})
CLASSIFIEURS = MappingProxyType({
    "MIT License": ("MIT",), "MIT No Attribution License (MIT-0)": ("MIT-0",),
    "BSD License": FAMILLE_BSD, "Apache Software License": ("Apache-2.0", "Apache-1.1"),
    "ISC License (ISCL)": ("ISC",), "Python Software Foundation License": ("PSF-2.0",),
    "Mozilla Public License 2.0 (MPL 2.0)": ("MPL-2.0",), "Mozilla Public License 1.1 (MPL 1.1)": ("MPL-1.1",),
    "GNU General Public License (GPL)": FAMILLE_GPL,
    "GNU General Public License v2 (GPLv2)": ("GPL-2.0-only", "GPL-2.0-or-later"),
    "GNU General Public License v2 or later (GPLv2+)": ("GPL-2.0-or-later",),
    "GNU General Public License v3 (GPLv3)": ("GPL-3.0-only", "GPL-3.0-or-later"),
    "GNU General Public License v3 or later (GPLv3+)": ("GPL-3.0-or-later",),
    "GNU Lesser General Public License v2 (LGPLv2)": ("LGPL-2.0-only", "LGPL-2.0-or-later"),
    "GNU Lesser General Public License v2 or later (LGPLv2+)": ("LGPL-2.0-or-later",),
    "GNU Lesser General Public License v3 (LGPLv3)": ("LGPL-3.0-only", "LGPL-3.0-or-later"),
    "GNU Lesser General Public License v3 or later (LGPLv3+)": ("LGPL-3.0-or-later",),
    "GNU Library or Lesser General Public License (LGPL)": FAMILLE_LGPL,
    "GNU Affero General Public License v3": ("AGPL-3.0-only", "AGPL-3.0-or-later"),
    "GNU Affero General Public License v3 or later (AGPLv3+)": ("AGPL-3.0-or-later",),
    "Eclipse Public License 1.0 (EPL-1.0)": ("EPL-1.0",), "Eclipse Public License 2.0 (EPL-2.0)": ("EPL-2.0",),
    "Boost Software License 1.0 (BSL-1.0)": ("BSL-1.0",), "The Unlicense (Unlicense)": ("Unlicense",),
    "zlib/libpng License": ("Zlib",), "Universal Permissive License (UPL)": ("UPL-1.0",),
    "Common Development and Distribution License 1.0 (CDDL-1.0)": ("CDDL-1.0",),
    "CC0 1.0 Universal (CC0 1.0) Public Domain Dedication": ("CC0-1.0",),
    "Other/Proprietary License": ("LicenseRef-Proprietary",),
    "Public Domain": ("LicenseRef-Domaine-Public",),
})
EMPREINTES_TEXTE = (
    (r"gnu affero general public license\s+version 3", ("AGPL-3.0-only", "AGPL-3.0-or-later")),
    (r"gnu lesser general public license\s+version 2\.1", ("LGPL-2.1-only", "LGPL-2.1-or-later")),
    (r"gnu lesser general public license\s+version 3", ("LGPL-3.0-only", "LGPL-3.0-or-later")),
    (r"gnu general public license\s+version 3", ("GPL-3.0-only", "GPL-3.0-or-later")),
    (r"gnu general public license\s+version 2", ("GPL-2.0-only", "GPL-2.0-or-later")),
    (r"mozilla public license,?\s+(?:version|v\.?)\s*2\.0", ("MPL-2.0",)),
    (r"apache license,?\s+version 2\.0", ("Apache-2.0",)),
    (r"permission is hereby granted, free of charge", ("MIT",)),
    (r"advertising materials mentioning", ("BSD-4-Clause",)),
    (r"neither the name of", ("BSD-3-Clause",)),
    (r"redistribution and use in source and binary forms", ("BSD-2-Clause",)),
    (r"python software foundation license", ("PSF-2.0",)),
)
CATEGORIE_PAR_ID = MappingProxyType({i.lower(): cat for cat, ids in CATEGORIES.items() for i in ids})
CANONIQUE = MappingProxyType({i.lower(): i for ids in CATEGORIES.values() for i in ids}
                             | {e.lower(): e for e in EXCEPTIONS})
MOTIF_JETON = re.compile(r"\s*(?:(\()|(\))|([A-Za-z0-9.+:_-]+))")
MOTIF_NOM_REQUIS = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


class ErreurEntree(Exception):
    """Entrée invalide : fichier, cible ou expression en ligne (code 2)."""


class ErreurExpression(ValueError):
    """Expression de licence mal formée."""


@dataclass
class Element:
    """Une dépendance (ou une expression en ligne) à juger."""

    nom: str
    version: str | None = None
    groupe: str | None = None
    source_licence: str = "non résolue"
    brute: str | None = None
    arbres: list[Any] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- expressions


def jetons(texte: str) -> list[str]:
    """Découpe une expression en jetons ( ) et mots."""
    resultat, position = [], 0
    while position < len(texte):
        if not texte[position:].strip():
            break
        m = MOTIF_JETON.match(texte, position)
        if m is None or m.end() == position:
            raise ErreurExpression(f"caractère inattendu en position {position} : {texte[position]!r}")
        resultat.append(m.group(1) or m.group(2) or m.group(3))
        position = m.end()
    return resultat


class Lecteur:
    """Analyse descendante : ou := et (OR et)* ; et := avec (AND avec)* ; avec := atome (WITH id)?"""

    def __init__(self, liste: list[str]) -> None:
        self.liste = liste
        self.position = 0

    def suivant(self) -> str | None:
        return self.liste[self.position] if self.position < len(self.liste) else None

    def mot(self, attendu: str) -> bool:
        jeton = self.suivant()
        if jeton is not None and jeton.upper() == attendu:
            self.position += 1
            return True
        return False

    def ou(self) -> Any:
        branches = [self.et()]
        while self.mot("OR"):
            branches.append(self.et())
        return branches[0] if len(branches) == 1 else ("or", branches)

    def et(self) -> Any:
        branches = [self.avec()]
        while self.mot(OPERATEUR_AND):
            branches.append(self.avec())
        return branches[0] if len(branches) == 1 else ("and", branches)

    def avec(self) -> Any:
        atome = self.atome()
        if self.mot(OPERATEUR_WITH):
            exception = self.suivant()
            if atome[0] != "id" or exception is None or exception in "()":
                raise ErreurExpression("WITH doit relier une licence à une exception")
            self.position += 1
            return ("with", atome[1], exception)
        return atome

    def atome(self) -> Any:
        jeton = self.suivant()
        if jeton is None:
            raise ErreurExpression("expression incomplète")
        self.position += 1
        if jeton == "(":
            noeud = self.ou()
            if not self.mot(")"):
                raise ErreurExpression("parenthèse non fermée")
            return noeud
        if jeton == ")" or jeton.upper() in ("AND", "OR", OPERATEUR_WITH):
            raise ErreurExpression(f"opérande attendu, trouvé « {jeton} »")
        return ("id", jeton)


def normaliser_id(brut: str, notes: list[str]) -> str:
    """Identifiant canonique (casse, dépréciés, suffixe +) ; note ce qui a été changé."""
    cle = brut.lower()
    if cle in DEPRECIES:
        notes.append(f"{brut} → {DEPRECIES[cle]} (identifiant déprécié ou alias)")
        return DEPRECIES[cle]
    if cle in CANONIQUE:
        return CANONIQUE[cle]
    if cle.endswith("+") and cle[:-1] in CANONIQUE:
        return CANONIQUE[cle[:-1]] + "+"
    if not cle.startswith(("licenseref-", "documentref-")):
        notes.append(f"{brut} : identifiant hors de la table de l'outil")
    return brut


def normaliser_arbre(noeud: Any, notes: list[str]) -> Any:
    """Normalise les identifiants et aplatit les opérateurs imbriqués identiques."""
    if noeud[0] == "id":
        return ("id", normaliser_id(noeud[1], notes))
    if noeud[0] == "with":
        return ("with", normaliser_id(noeud[1], notes), normaliser_id(noeud[2], notes))
    branches = []
    for enfant in (normaliser_arbre(e, notes) for e in noeud[1]):
        branches += enfant[1] if enfant[0] == noeud[0] else [enfant]
    return (noeud[0], branches)


def analyser_expression(texte: str, notes: list[str]) -> Any:
    """Expression → arbre normalisé ; ErreurExpression si la grammaire n'est pas respectée."""
    if len(texte) > LONGUEUR_MAX_EXPRESSION:
        raise ErreurExpression(f"expression de plus de {LONGUEUR_MAX_EXPRESSION} caractères")
    liste = jetons(texte)
    if not liste:
        raise ErreurExpression("expression vide")
    if any(j in ("and", "or", "with") for j in liste):
        notes.append("opérateurs en minuscules normalisés en majuscules")
    lecteur = Lecteur(liste)
    try:
        arbre = lecteur.ou()
    except RecursionError as exc:
        raise ErreurExpression("expression trop imbriquée") from exc
    if lecteur.suivant() is not None:
        raise ErreurExpression(f"jeton en trop : « {lecteur.suivant()} »")
    return normaliser_arbre(arbre, notes)


def rendre(noeud: Any, parent: str | None = None) -> str:
    """Arbre → texte canonique (parenthèses autour d'un groupe AND ou OR imbriqué)."""
    if noeud[0] == "id":
        return noeud[1]
    if noeud[0] == "with":
        return f"{noeud[1]} {OPERATEUR_WITH} {noeud[2]}"
    texte = f" {noeud[0].upper()} ".join(rendre(e, noeud[0]) for e in noeud[1])
    return f"({texte})" if parent is not None else texte


# --------------------------------------------------------------------------- compatibilité


def categorie(identifiant: str) -> str:
    """Catégorie d'un identifiant (le + « ou ultérieure » ne change pas la catégorie)."""
    return CATEGORIE_PAR_ID.get(identifiant.lower().rstrip("+"), "inconnue")


def pire(verdicts: list[tuple[str, list[str]]]) -> tuple[str, list[str]]:
    """Verdict le plus défavorable, avec ses raisons."""
    return max(verdicts, key=lambda v: RANG_VERDICT[v[0]])


def verdict_noeud(noeud: Any, cible: str) -> tuple[str, list[str]]:
    """Verdict d'un arbre pour une catégorie de cible : OR → meilleure branche, AND → pire."""
    if noeud[0] == "id":
        cat = categorie(noeud[1])
        verdict = VERDICT_PAR_LETTRE[MATRICE[cat][CIBLES.index(cible)]]
        return verdict, [f"{noeud[1]} : {RAISON_CATEGORIE[cat]} → {verdict} (cible {cible})"]
    if noeud[0] == "with":
        verdict, raisons = verdict_noeud(("id", noeud[1]), cible)
        if verdict != COMPATIBLE:
            return A_VERIFIER, raisons + [f"exception {noeud[2]} : ajoute des permissions, "
                                          "portée à vérifier"]
        return verdict, raisons
    sous = [verdict_noeud(e, cible) for e in noeud[1]]
    if noeud[0] == "or":
        return min(sous, key=lambda v: RANG_VERDICT[v[0]])
    return pire(sous)


def verdict_element(element: Element, cibles: list[str]) -> tuple[str, list[str]]:
    """Pire verdict sur les catégories de cible ; candidates divergentes → À VÉRIFIER."""
    if not element.arbres:
        return A_VERIFIER, [f"licence {element.source_licence}"]
    par_arbre = [pire([verdict_noeud(a, c) for c in cibles]) for a in element.arbres]
    distincts = {v for v, _ in par_arbre}
    if len(distincts) > 1:
        candidates = ", ".join(rendre(a) for a in element.arbres)
        return A_VERIFIER, [f"licence imprécise : candidates {candidates} aux verdicts "
                            f"divergents ({', '.join(sorted(distincts))})"]
    return par_arbre[0]


def analyser_cible(texte: str) -> tuple[str, list[str]]:
    """Cible → (expression canonique, catégories de cible) ; refuse une cible hors table."""
    notes: list[str] = []
    try:
        arbre = analyser_expression(texte, notes)
    except ErreurExpression as exc:
        raise ErreurEntree(f"cible illisible « {texte} » : {exc}") from exc
    identifiants = list(identifiants_arbre(arbre))
    categories = []
    for ident in identifiants:
        cat = CATEGORIE_CIBLE.get(categorie(ident))
        if cat is None:
            raise ErreurEntree(f"cible hors de la table : {ident} (prises en charge : licences "
                               f"permissives, Apache-2.0, MPL-2.0, {MOT_LGPL}, GPL, {MOT_AGPL}, "
                               "LicenseRef-Proprietary)")
        categories.append(cat)
    return rendre(arbre), sorted(set(categories), key=CIBLES.index)


def identifiants_arbre(noeud: Any) -> Iterator[str]:
    """Identifiants de licence (sans les exceptions) d'un arbre."""
    if noeud[0] in ("id", "with"):
        yield noeud[1]
    else:
        for enfant in noeud[1]:
            yield from identifiants_arbre(enfant)


# --------------------------------------------------------------------------- métadonnées


def arbres_candidats(identifiants: tuple[str, ...]) -> list[Any]:
    """Une liste d'identifiants candidats → un arbre par candidat."""
    return [("id", i) for i in dict.fromkeys(identifiants)]


def depuis_champ_license(texte: str, element: Element) -> bool:
    """Interprète le champ License : nom courant, expression, ou texte reconnu par empreinte."""
    propre = " ".join(texte.split())
    cle = propre.lower().rstrip(".")
    if not propre or cle == "unknown":
        return False
    if cle in NOMS_COURANTS:
        element.arbres = arbres_candidats(NOMS_COURANTS[cle])
        element.source_licence = "License (nom courant)"
        return True
    if len(propre) <= 200 and "\n" not in texte.strip():
        notes: list[str] = []
        try:
            arbre = analyser_expression(propre, notes)
        except ErreurExpression:
            arbre = None
        if arbre is not None and all(categorie(i) != "inconnue" for i in identifiants_arbre(arbre)):
            element.arbres, element.source_licence = [arbre], "License (expression)"
            element.notes += notes
            return True
    return depuis_texte_integral(texte, element)


def depuis_texte_integral(texte: str, element: Element) -> bool:
    """Reconnaît un texte de licence complet par empreintes (résultat présumé)."""
    bas = " ".join(texte.lower().split())
    for motif, identifiants in EMPREINTES_TEXTE:
        if re.search(motif, bas):
            element.arbres = arbres_candidats(identifiants)
            element.source_licence = "License (texte reconnu, présumé)"
            return True
    element.notes.append(f"champ License non reconnu : « {' '.join(texte.split())[:80]} »")
    return False


def depuis_classifieurs(classifieurs: list[str], element: Element) -> bool:
    """Classifieurs « License :: » → candidats (union, prudente)."""
    candidats: list[str] = []
    for c in classifieurs:
        cle = c.split("::")[-1].strip() if c.startswith("License :: OSI Approved ::") else \
            c.removeprefix("License ::").strip()
        candidats += CLASSIFIEURS.get(cle, ())
    if not candidats:
        return False
    element.arbres = arbres_candidats(tuple(candidats))
    element.source_licence = "classifieurs"
    return True


def renseigner_licence(element: Element, meta: Any) -> None:
    """Remplit la licence d'un élément depuis les métadonnées d'une distribution."""
    expression = (meta.get("License-Expression") or "").strip()
    classifieurs = [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    if expression:
        element.brute = expression
        try:
            element.arbres = [analyser_expression(expression, element.notes)]
            element.source_licence = "License-Expression"
            if classifieurs:
                element.notes.append("classifieurs de licence présents mais ignorés (PEP 639) : "
                                     + "; ".join(c.split("::")[-1].strip() for c in classifieurs))
            return
        except ErreurExpression as exc:
            element.notes.append(f"License-Expression invalide ({exc})")
    licence = meta.get("License") or ""
    element.brute = element.brute or (licence.strip()[:200] or None)
    if depuis_champ_license(licence, element) or depuis_classifieurs(classifieurs, element):
        return
    element.source_licence = "aucune déclarée"


def nom_normalise(nom: str) -> str:
    """Nom de distribution normalisé (PEP 503)."""
    return re.sub(r"[-_.]+", "-", nom).lower()


def index_distributions(sites: list[Path] | None) -> dict[str, Any]:
    """Distributions installées (interpréteur courant, ou dossiers --site), par nom normalisé."""
    trouvees = metadata.distributions(path=[str(s) for s in sites]) if sites else \
        metadata.distributions()
    index: dict[str, Any] = {}
    for dist in trouvees:
        nom = dist.metadata.get("Name")
        if nom and nom_normalise(nom) not in index:
            index[nom_normalise(nom)] = dist
    return index


# --------------------------------------------------------------------------- sources


def lire_texte_borne(chemin: Path) -> str:
    """Lit un fichier source en UTF-8, taille bornée, binaire refusé."""
    if chemin.stat().st_size > MAX_OCTETS_SOURCE:
        raise ErreurEntree(f"{chemin.name} : plus de {MAX_OCTETS_SOURCE} octets")
    try:
        texte = chemin.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError) as exc:
        raise ErreurEntree(f"{chemin.name} : illisible en UTF-8 ({exc})") from exc
    if "\x00" in texte:
        raise ErreurEntree(f"{chemin.name} : octets NUL, fichier binaire refusé")
    return texte


def nom_requis(ligne: str) -> str | None:
    """Nom de distribution en tête d'une exigence PEP 508."""
    m = MOTIF_NOM_REQUIS.match(ligne)
    return m.group(1) if m else None


def version_epinglee(exigence: str) -> str | None:
    """Version si l'exigence est épinglée par == (sans joker)."""
    m = re.search(r"===?\s*([A-Za-z0-9.!+_-]+)\s*(?:[;,]|$)", exigence.split(";")[0] + ";")
    return m.group(1) if m and not m.group(1).endswith("*") else None


def lire_requirements(chemin: Path, source: dict[str, Any]) -> list[Element]:
    """requirements.txt : une exigence par ligne ; -r/-c notés mais non suivis."""
    elements = []
    texte = re.sub(r"\\\r?\n", " ", lire_texte_borne(chemin))
    for ligne in texte.splitlines():
        ligne = re.split(r"(?:^|\s)#", ligne, maxsplit=1)[0].strip()
        if not ligne:
            continue
        if ligne.startswith(("-r", "-c", "--requirement", "--constraint")):
            source["inclusions_non_suivies"].append(ligne)
            continue
        egg = re.search(r"#egg=([A-Za-z0-9._-]+)", ligne)
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://|^[./~]", ligne) and not egg:
            source["lignes_ignorees"].append(ligne[:120])
            continue
        if ligne.startswith("-"):
            nom = egg.group(1) if egg else None
        else:
            nom = nom_requis(ligne)
        if nom and "://" not in nom:
            elements.append(Element(nom, version_epinglee(ligne)))
    return elements


def elements_pep508(exigences: Any, groupe: str | None) -> list[Element]:
    """Liste d'exigences PEP 508 → éléments."""
    if not isinstance(exigences, list):
        return []
    return [Element(n, version_epinglee(e), groupe) for e in exigences
            if isinstance(e, str) and (n := nom_requis(e))]


def lire_pyproject(chemin: Path, source: dict[str, Any]) -> list[Element]:
    """pyproject.toml : dépendances de [project], groupes, tool.poetry ; licence déclarée."""
    try:
        donnees = tomllib.loads(lire_texte_borne(chemin))
    except (tomllib.TOMLDecodeError, RecursionError) as exc:
        raise ErreurEntree(f"{chemin.name} : TOML invalide ({exc})") from exc
    projet = donnees.get("project", {})
    elements = elements_pep508(projet.get("dependencies"), None)
    for groupe, liste in (projet.get("optional-dependencies") or {}).items():
        elements += elements_pep508(liste, f"extra:{groupe}")
    for groupe, liste in (donnees.get("dependency-groups") or {}).items():
        elements += elements_pep508(liste, f"groupe:{groupe}")
    poetry = donnees.get("tool", {}).get("poetry", {}).get("dependencies", {})
    elements += [Element(n, None, "poetry") for n in poetry if n.lower() != "python"]
    licence = projet.get("license")
    if isinstance(licence, dict):
        licence = licence.get("text")
    source["licence_declaree"] = licence if isinstance(licence, str) else None
    return elements


def lire_uv_lock(chemin: Path, source: dict[str, Any]) -> list[Element]:
    """uv.lock : paquets verrouillés (le projet lui-même, éditable ou virtuel, est exclu)."""
    try:
        donnees = tomllib.loads(lire_texte_borne(chemin))
    except (tomllib.TOMLDecodeError, RecursionError) as exc:
        raise ErreurEntree(f"{chemin.name} : TOML invalide ({exc})") from exc
    elements = []
    for paquet in donnees.get("package", []):
        origine = paquet.get("source", {})
        if "editable" in origine or "virtual" in origine:
            source["projet"] = paquet.get("name")
            continue
        elements.append(Element(paquet.get("name", "?"), paquet.get("version")))
    return elements


def type_source(chemin: Path) -> str | None:
    """Type de fichier source reconnu d'après son nom."""
    nom = chemin.name.lower()
    if nom == "pyproject.toml":
        return "pyproject"
    if nom == "uv.lock":
        return "uv.lock"
    if nom.endswith((".txt", ".in")) and ("requirement" in nom or nom.startswith(("req", "constraint"))):
        return "requirements"
    return None


def fichiers_sources(chemins: list[Path]) -> list[Path]:
    """Fichiers sources donnés, ou trouvés au premier niveau des dossiers donnés."""
    fichiers = []
    for chemin in chemins:
        if not chemin.exists():
            raise ErreurEntree(f"chemin introuvable : {chemin}")
        if chemin.is_dir():
            fichiers += sorted(p for p in chemin.iterdir() if p.is_file() and type_source(p))
        elif type_source(chemin) is None:
            raise ErreurEntree(f"{chemin.name} : ni pyproject.toml, ni requirements*.txt/.in, "
                               "ni uv.lock")
        else:
            fichiers.append(chemin)
    return fichiers


def lire_sources(chemins: list[Path]) -> tuple[list[Element], list[dict[str, Any]]]:
    """Éléments de toutes les sources (dédoublonnés par nom normalisé et groupe)."""
    lecteurs = {"pyproject": lire_pyproject, "requirements": lire_requirements, "uv.lock": lire_uv_lock}
    elements: dict[tuple[str, str | None], Element] = {}
    sources = []
    for fichier in fichiers_sources(chemins):
        source: dict[str, Any] = {"chemin": str(fichier), "type": type_source(fichier),
                                  "inclusions_non_suivies": [], "lignes_ignorees": []}
        trouves = lecteurs[source["type"]](fichier, source)
        source["dependances"] = len(trouves)
        sources.append(source)
        for e in trouves:
            elements.setdefault((nom_normalise(e.nom), e.groupe), e)
    return list(elements.values()), sources


# --------------------------------------------------------------------------- license-expression


def revalider(elements: list[Element]) -> dict[str, Any]:
    """Revalide chaque expression avec license-expression et compare les structures."""
    assert license_expression is not None
    licensing = license_expression.get_spdx_licensing()
    ecarts = []
    comparees = 0
    for element in elements:
        if element.source_licence not in SOURCES_EXPRESSION:
            continue
        for arbre in element.arbres:
            comparees += 1
            ecart = ecart_bibliotheque(licensing, arbre)
            if ecart:
                ecarts.append({"element": element.nom, "stdlib": rendre(arbre), **ecart})
    return {"effectue": True, "bibliotheque": version_bibliotheque(), "comparees": comparees,
            "ecarts": ecarts}


def ecart_bibliotheque(licensing: Any, arbre: Any) -> dict[str, Any] | None:
    """Écart entre l'arbre stdlib et la validation de license-expression, ou None."""
    texte = rendre(arbre)
    try:
        info = licensing.validate(texte)
    except (license_expression.ExpressionError, AttributeError, ValueError, TypeError) as exc:
        return {"bibliotheque": f"erreur {type(exc).__name__} : {exc}"}
    references = all(str(s).startswith(("LicenseRef-", "DocumentRef-")) for s in info.invalid_symbols)
    if info.errors and not (info.invalid_symbols and references):
        return {"bibliotheque": "; ".join(info.errors)}
    if info.errors:
        return None
    try:
        autre = rendre(analyser_expression(info.normalized_expression, []))
    except ErreurExpression as exc:
        return {"bibliotheque": f"forme normalisée illisible ({exc})"}
    return None if autre == texte else {"bibliotheque": info.normalized_expression}


def version_bibliotheque() -> str:
    """Nom et version de license-expression."""
    try:
        return f"license-expression {metadata.version('license-expression')}"
    except metadata.PackageNotFoundError:
        return "license-expression"


# --------------------------------------------------------------------------- orchestration


def elements_expressions(expressions: list[str]) -> list[Element]:
    """Expressions passées en ligne ; une expression invalide est une entrée invalide."""
    elements = []
    for texte in expressions:
        element = Element(texte, source_licence="--expression", brute=texte)
        try:
            element.arbres = [analyser_expression(texte, element.notes)]
        except ErreurExpression as exc:
            raise ErreurEntree(f"expression invalide « {texte[:80]} » : {exc}") from exc
        elements.append(element)
    return elements


def resoudre_licences(elements: list[Element], index: dict[str, Any] | None) -> None:
    """Associe à chaque dépendance les métadonnées installées, si elles sont consultées."""
    for element in elements:
        if element.source_licence == "--expression":
            continue
        if index is None:
            element.source_licence = "non résolue (métadonnées non consultées)"
            continue
        dist = index.get(nom_normalise(element.nom))
        if dist is None:
            element.source_licence = "non installée"
            continue
        installee = dist.metadata.get("Version")
        if element.version and installee and element.version != installee:
            element.notes.append(f"version installée {installee} ≠ version demandée {element.version}")
        element.version = element.version or installee
        renseigner_licence(element, dist.metadata)


def fiche(element: Element, cibles: list[str] | None) -> dict[str, Any]:
    """Fiche JSON d'un élément, verdict compris si une cible est connue."""
    resultat = {"nom": element.nom, "version": element.version, "groupe": element.groupe,
                "source_licence": element.source_licence, "brute": element.brute,
                "licence": " | ".join(rendre(a) for a in element.arbres) or None,
                "candidates": len(element.arbres), "notes": element.notes}
    if cibles:
        resultat["verdict"], resultat["raisons"] = verdict_element(element, cibles)
    return resultat


def choisir_cible(args: argparse.Namespace, sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    """--cible, sinon la licence déclarée du premier pyproject.toml qui en porte une."""
    texte, origine = args.cible, "--cible"
    if texte is None:
        declarees = [s for s in sources if s.get("licence_declaree")]
        if not declarees:
            return None
        texte, origine = declarees[0]["licence_declaree"], declarees[0]["chemin"]
    expression, categories = analyser_cible(texte)
    return {"brute": texte, "normalisee": expression, "categories": categories, "origine": origine}


def construire_rapport(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Rassemble éléments, licences, cible et verdicts."""
    chemins = [Path(args.racine) / c if args.racine and not Path(c).is_absolute() else Path(c)
               for c in args.sources]
    elements, sources = lire_sources(chemins)
    sites = [Path(s) for s in args.site] if args.site else None
    for site in sites or []:
        if not site.is_dir():
            raise ErreurEntree(f"--site : dossier introuvable : {site}")
    consulter = args.environnement or bool(sites)
    index = index_distributions(sites) if consulter else None
    if not args.sources and index is not None and not args.expression:
        elements = [Element(d.metadata["Name"]) for d in index.values()]
    elements += elements_expressions(args.expression or [])
    resoudre_licences(elements, index)
    cible = choisir_cible(args, sources)
    fiches = [fiche(e, cible["categories"] if cible else None) for e in elements]
    bilan = {v: sum(f.get("verdict") == v for f in fiches) for v in (COMPATIBLE, A_VERIFIER, INCOMPATIBLE)}
    rapport = {
        "denominateur": len(elements), "examines": [e.nom for e in elements][:MAX_EXAMINES],
        "examines_tronques": len(elements) > MAX_EXAMINES,
        "moteur": "license-expression" if license_expression is not None else "stdlib",
        "cible": cible, "sources": sources,
        "metadonnees": ("site : " + ", ".join(map(str, sites))) if sites else
                       ("environnement courant" if args.environnement else "non consultées"),
        "elements": fiches, "bilan": bilan if cible else None,
        "controle_croise": revalider(elements) if license_expression is not None else {"effectue": False},
        "avertissement": AVERTISSEMENT_JURIDIQUE,
    }
    code = 1 if bilan[INCOMPATIBLE] or (args.strict and bilan[A_VERIFIER]) else 0
    return rapport, code


# --------------------------------------------------------------------------- interface


def extraire_contrat(doc: str) -> dict[str, str]:
    """Sections du contrat de mesure lues dans le docstring."""
    contrat: dict[str, str] = {}
    courant = None
    for ligne in doc.splitlines():
        if ligne.strip() in TITRES_CONTRAT and not ligne.startswith(" "):
            courant = ligne.strip()
            contrat[courant] = ""
        elif courant is not None:
            contrat[courant] = (contrat[courant] + " " + ligne.strip()).strip()
    return contrat


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    p = argparse.ArgumentParser(
        description="Inventorie les licences de dépendances Python (métadonnées, expressions "
                    f"{SPDX}) et juge prudemment leur compatibilité avec la licence du projet.",
        epilog=f"Exemple : python {RACINE.name}/verifier_licences.py pyproject.toml --environnement "
               "--cible AGPL-3.0-or-later --json")
    p.add_argument("sources", nargs="*", help="pyproject.toml, requirements*.txt/.in, uv.lock, "
                                             "ou dossier qui en contient")
    p.add_argument("--environnement", action="store_true",
                   help="lire les métadonnées des distributions de l'interpréteur courant")
    p.add_argument("--site", action="append", metavar="DOSSIER",
                   help="lire les métadonnées dans ce dossier seulement (répétable)")
    p.add_argument("--expression", action="append", metavar="EXPR",
                   help=f"expression {SPDX} à juger (répétable)")
    p.add_argument("--cible", help="licence du projet (ex. MIT, Apache-2.0, AGPL-3.0-or-later, "
                                   "LicenseRef-Proprietary) ; défaut : celle du pyproject.toml")
    p.add_argument("--strict", action="store_true", help="code 1 aussi pour « À VÉRIFIER »")
    p.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : courant)")
    p.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return p


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Sortie lisible."""
    cible = rapport["cible"]
    print(f"Cible : {cible['normalisee'] + ' (' + cible['origine'] + ')' if cible else '(aucune)'}"
          f" — métadonnées : {rapport['metadonnees']}")
    for f in rapport["elements"]:
        verdict = f" → {f['verdict']}" if "verdict" in f else ""
        print(f"  {f['nom']} {f['version'] or ''} : {f['licence'] or '?'} [{f['source_licence']}]{verdict}")
        if f.get("verdict") and f["verdict"] != COMPATIBLE:
            for raison in f["raisons"][:3]:
                print(f"      {raison}")
    if rapport["bilan"]:
        print("Bilan : " + ", ".join(f"{k} {v}" for k, v in rapport["bilan"].items()))
    print(rapport["avertissement"])


def neutraliser_sortie() -> None:
    """Lecteur parti (tube fermé) : stdout est redirigé vers le néant, sans trace d'erreur."""
    nul = os.open(os.devnull, os.O_WRONLY)
    os.dup2(nul, sys.stdout.fileno())


def afficher_refus(message: str, code: int, en_json: bool) -> int:
    """Message sur stderr, objet JSON minimal (dénominateur 0) si --json ; rend le code."""
    print(message, file=sys.stderr)
    if en_json:
        print(json.dumps({"denominateur": 0, "examines": [], "erreur": message}, ensure_ascii=False))
    return code


def afficher_resultat(rapport: dict[str, Any], en_json: bool) -> None:
    """Écrit le rapport (JSON ou lisible) ; un tube fermé par le lecteur n'est pas une erreur."""
    try:
        if en_json:
            print(json.dumps(rapport, ensure_ascii=False, indent=2))
        else:
            afficher_humain(rapport)
        sys.stdout.flush()
    except BrokenPipeError:
        neutraliser_sortie()


def signaler(rapport: dict[str, Any], code: int) -> None:
    """Avis sur stderr : licences non résolues, défauts trouvés."""
    if rapport["metadonnees"] == "non consultées" and rapport["sources"]:
        print("licences des dépendances non résolues : ajoutez --environnement ou --site <dossier> "
              "pour lire les métadonnées installées.", file=sys.stderr)
    if code:
        print(f"défaut : {rapport['bilan'][INCOMPATIBLE]} incompatibilité(s) probable(s), "
              f"{rapport['bilan'][A_VERIFIER]} à vérifier.", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    if license_expression is None:
        print(MESSAGE_LIB_ABSENTE, file=sys.stderr)
    try:
        rapport, code = construire_rapport(args)
    except ErreurEntree as exc:
        return afficher_refus(f"entrée invalide : {exc}", 2, args.json)
    if rapport["denominateur"] == 0:
        return afficher_refus("dénominateur nul : rien à examiner (aucune dépendance ni expression ; "
                              "ajoutez une source, --environnement, --site ou --expression).",
                              3, args.json)
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    signaler(rapport, code)
    afficher_resultat(rapport, args.json)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
