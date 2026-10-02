"""Comparer des numéros de version comme le font pip (PEP 440) et SemVer 2.0, jamais comme des chaînes.
Mesuré dans cette session : `python3.14 -c "print(max(['1.9','1.10','1.10rc1']))"` affiche 1.9,
et `'1.10' > '1.9'` vaut False — l'ordre des chaînes classe 1.9 au-dessus de 1.10.

QUESTION
    Cette version satisfait-elle cette contrainte, et quelle est la plus récente compatible ?
MESURE
    Mode pep440 (défaut) : analyse et normalisation PEP 440 complètes (époque N!, segments de
    publication, pré-versions a/b/rc et leurs synonymes alpha/beta/c/pre/preview, post-versions
    implicites et explicites, dev, version locale +xxx), clé d'ordre conforme à la PEP, opérateurs
    ~=, ==, !=, <=, >=, <, >, === et jokers ==1.2.*. La meilleure version suit la politique PEP 440
    des pré-versions : exclues sauf --pre, sauf si la contrainte en nomme une, ou si aucune
    version finale ne convient. Mode semver : grammaire officielle SemVer 2.0, précédence des
    pré-versions (numérique < alphanumérique, ensemble plus long > préfixe), métadonnées de build
    ignorées, comparateurs =, ==, !=, <, <=, >, >=. Si packaging (pep440) ou semver (semver) est
    importable, chaque réponse est recalculée par la bibliothèque et les écarts sont rapportés.
    --corpus rejoue un corpus intégré de cas à réponse connue (au moins 50) sur les deux moteurs.
HYPOTHÈSES
    Les versions sont passées une par argument (ou une par ligne avec --depuis) ; la contrainte
    est un ensemble de spécificateurs séparés par des virgules (pep440) ou des espaces/virgules
    (semver). Les versions « installées » ne sont pas distinguées des versions « disponibles ».
LIMITES
    Les plages npm (^, ~, x, ||) ne sont pas prises en charge : elles sont refusées (code 2), pas
    devinées. La règle npm qui exclut les pré-versions d'un autre triplet n'est pas appliquée.
    === compare la chaîne brute, sans normalisation. Aucune résolution de dépendances : une seule
    contrainte à la fois. --depuis lit au plus 100000 lignes et 10 Mo.
CONTRE-EXEMPLES
    Constaté : `comparer_versions.py 1.0.0 1.0.0-1` (mode pep440 par défaut) classe 1.0.0-1 comme
    post-version 1.0.0.post1, donc au-dessus de 1.0.0 et « meilleure » ; avec --mode semver, la
    même chaîne est une pré-version, au-dessous de 1.0.0. L'outil ne devine pas le schéma : une
    étiquette SemVer passée en mode pep440 reçoit une réponse fausse pour son auteur.
    En mode semver, 1.0.0+a et 1.0.0+b ont la même précédence : la « meilleure » est alors la
    première rencontrée, choix que la norme ne tranche pas.
INVOCATION
    {outil} 1.4.2 1.10 2.0.0rc1 --contrainte ">=1.4,<2" --json
    {outil} --corpus --json
DOMAINE
    Numéros de version de paquets Python (PEP 440) et de projets suivant SemVer 2.0 ; décisions
    d'épinglage, de mise à niveau et de compatibilité dans une chaîne de production.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, NamedTuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from packaging import specifiers as pk_specifiers
    from packaging import version as pk_version
except ImportError:
    pk_specifiers = None
    pk_version = None

try:
    import semver as lib_semver
except ImportError:
    lib_semver = None

RACINE = Path(__file__).resolve().parent

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
                  TITRE_INVOCATION, "DOMAINE")

MAX_LIGNES_DEPUIS = 100_000
MAX_OCTETS_DEPUIS = 10 * 1024 * 1024
MAX_EXAMINES = 200
LONGUEUR_MAX_VERSION = 256

MOTIF_PEP440 = re.compile(
    r"""
    ^\s*v?
    (?:(?P<epoque>[0-9]+)!)?
    (?P<publication>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>[-_.]?(?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)[-_.]?(?P<pre_n>[0-9]+)?)?
    (?P<post>(?:-(?P<post_n1>[0-9]+))|(?:[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n2>[0-9]+)?))?
    (?P<dev>[-_.]?(?P<dev_l>dev)[-_.]?(?P<dev_n>[0-9]+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)
MOTIF_JOKER = re.compile(r"^\s*v?(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*\.\*\s*$", re.IGNORECASE)
MOTIF_SPECIFICATEUR = re.compile(r"^\s*(===|~=|==|!=|<=|>=|<|>)\s*(\S.*?)\s*$")
IDENT_SEMVER = r"(?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)"
MOTIF_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    rf"(?:-({IDENT_SEMVER}(?:\.{IDENT_SEMVER})*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
MOTIF_COMPARATEUR_SEMVER = re.compile(r"^(>=|<=|!=|==|=|<|>)?(.+)$")
SYNONYMES_PRE = MappingProxyType({"alpha": "a", "a": "a", "beta": "b", "b": "b", "c": "rc",
                                  "rc": "rc", "pre": "rc", "preview": "rc"})
RANG_PRE = MappingProxyType({"a": 0, "b": 1, "rc": 2})


class ErreurEntree(Exception):
    """Entrée invalide : contrainte mal formée, fichier illisible (code 2)."""


class VersionPep440(NamedTuple):
    """Version PEP 440 analysée."""

    epoque: int
    publication: tuple[int, ...]
    pre: tuple[str, int] | None
    post: int | None
    dev: int | None
    local: tuple[int | str, ...] | None


class VersionSemver(NamedTuple):
    """Version SemVer 2.0 analysée."""

    majeur: int
    mineur: int
    correctif: int
    pre: tuple[str, ...]
    build: tuple[str, ...]


class Specificateur(NamedTuple):
    """Un spécificateur PEP 440 (opérateur + version)."""

    operateur: str
    texte: str
    version: VersionPep440 | None
    joker: bool


# --------------------------------------------------------------------------- PEP 440


def entier_ou_zero(texte: str | None) -> int:
    """Convertit un numéro optionnel (implicite = 0)."""
    return int(texte) if texte else 0


def segment_local(partie: str) -> int | str:
    """Un segment local numérique devient un entier, sinon reste en minuscules."""
    return int(partie) if partie.isascii() and partie.isdigit() else partie.lower()


def analyser_pep440(texte: str) -> VersionPep440 | None:
    """Analyse une version PEP 440 ; None si la chaîne n'est pas conforme (ou démesurée)."""
    m = MOTIF_PEP440.match(texte) if len(texte) <= LONGUEUR_MAX_VERSION else None
    if m is None:
        return None
    pre = None
    if m.group("pre_l"):
        pre = (SYNONYMES_PRE[m.group("pre_l").lower()], entier_ou_zero(m.group("pre_n")))
    post = None
    if m.group("post"):
        post = entier_ou_zero(m.group("post_n1") or m.group("post_n2"))
    dev = entier_ou_zero(m.group("dev_n")) if m.group("dev") else None
    local = None
    if m.group("local"):
        local = tuple(segment_local(p) for p in re.split(r"[-_.]", m.group("local")))
    publication = tuple(int(p) for p in m.group("publication").split("."))
    return VersionPep440(entier_ou_zero(m.group("epoque")), publication, pre, post, dev, local)


def normaliser_pep440(v: VersionPep440) -> str:
    """Forme normalisée PEP 440."""
    texte = f"{v.epoque}!" if v.epoque else ""
    texte += ".".join(str(p) for p in v.publication)
    if v.pre is not None:
        texte += f"{v.pre[0]}{v.pre[1]}"
    if v.post is not None:
        texte += f".post{v.post}"
    if v.dev is not None:
        texte += f".dev{v.dev}"
    if v.local is not None:
        texte += "+" + ".".join(str(p) for p in v.local)
    return texte


def cle_pep440(v: VersionPep440) -> tuple[Any, ...]:
    """Clé d'ordre PEP 440 (zéros finaux ignorés, dev < pre < final < post)."""
    publication = list(v.publication)
    while publication and publication[-1] == 0:
        publication.pop()
    if v.pre is None and v.post is None and v.dev is not None:
        pre: tuple[Any, ...] = (0,)
    elif v.pre is None:
        pre = (2,)
    else:
        pre = (1, RANG_PRE[v.pre[0]], v.pre[1])
    post = (0,) if v.post is None else (1, v.post)
    dev = (2,) if v.dev is None else (1, v.dev)
    local: tuple[Any, ...] = (0,)
    if v.local is not None:
        local = (1, tuple((1, p, "") if isinstance(p, int) else (0, 0, p) for p in v.local))
    return (v.epoque, tuple(publication), pre, post, dev, local)


def est_pre_pep440(v: VersionPep440) -> bool:
    """Une version dev ou pré est une pré-version au sens PEP 440."""
    return v.pre is not None or v.dev is not None


def publique(v: VersionPep440) -> VersionPep440:
    """Version sans segment local."""
    return v._replace(local=None)


def base(v: VersionPep440) -> VersionPep440:
    """Version réduite à l'époque et à la publication."""
    return VersionPep440(v.epoque, v.publication, None, None, None, None)


def correspond_prefixe(v: VersionPep440, prefixe: VersionPep440) -> bool:
    """==X.* : même époque, publication complétée de zéros puis tronquée égale au préfixe."""
    n = len(prefixe.publication)
    complete = (v.publication + (0,) * n)[:n]
    return v.epoque == prefixe.epoque and complete == prefixe.publication


def valider_version_specificateur(operateur: str, texte: str) -> tuple[VersionPep440 | None, bool]:
    """Vérifie la version d'un spécificateur selon son opérateur ; rend (version, joker)."""
    if operateur == "===":
        return None, False
    if operateur in ("==", "!=") and texte.endswith(".*"):
        if not MOTIF_JOKER.match(texte):
            raise ErreurEntree(f"joker invalide : {operateur}{texte[:80]} (seul X.Y.* est permis)")
        return analyser_pep440(texte[:-2]), True
    v = analyser_pep440(texte)
    if v is None:
        raise ErreurEntree(f"version invalide dans le spécificateur {operateur}{texte[:80]}")
    if v.local is not None and operateur not in ("==", "!="):
        raise ErreurEntree(f"version locale interdite avec {operateur} : {texte[:80]}")
    if operateur == "~=" and len(v.publication) < 2:
        raise ErreurEntree(f"~= exige au moins deux segments de publication : {texte[:80]}")
    return v, False


def analyser_specificateurs(texte: str) -> list[Specificateur]:
    """Analyse un ensemble de spécificateurs PEP 440 séparés par des virgules."""
    resultat = []
    for morceau in texte.split(","):
        if not morceau.strip():
            raise ErreurEntree(f"spécificateur vide dans « {texte} »")
        m = MOTIF_SPECIFICATEUR.match(morceau)
        if m is None:
            raise ErreurEntree(f"spécificateur sans opérateur valide : « {morceau.strip()[:80]} »")
        operateur, version_texte = m.group(1), m.group(2)
        version, joker = valider_version_specificateur(operateur, version_texte)
        resultat.append(Specificateur(operateur, version_texte, version, joker))
    return resultat


def egal_pep440(v: VersionPep440, s: Specificateur) -> bool:
    """Opérateur == (local ignoré si la contrainte n'en porte pas)."""
    assert s.version is not None
    if s.joker:
        return correspond_prefixe(v, s.version)
    candidat = v if s.version.local is not None else publique(v)
    return cle_pep440(candidat) == cle_pep440(s.version)


def compatible_pep440(v: VersionPep440, s: Specificateur) -> bool:
    """Opérateur ~= : >= V et == préfixe(V).*."""
    assert s.version is not None
    prefixe = VersionPep440(s.version.epoque, s.version.publication[:-1], None, None, None, None)
    return cle_pep440(publique(v)) >= cle_pep440(s.version) and correspond_prefixe(v, prefixe)


def cible_pre_version(v: VersionPep440) -> VersionPep440:
    """Version finale dont v est la pré-version (a/b/rc : la publication ; dev seul : v sans dev)."""
    if v.pre is not None:
        return base(v)
    return v._replace(dev=None, local=None)


def inferieur_strict(v: VersionPep440, borne: VersionPep440) -> bool:
    """<V (local ignoré) exclut les pré-versions de V elle-même, sauf si V en est une."""
    p = publique(v)
    if not cle_pep440(p) < cle_pep440(borne):
        return False
    if est_pre_pep440(borne) or not est_pre_pep440(p):
        return True
    return cle_pep440(cible_pre_version(p)) != cle_pep440(borne)


def superieur_strict(v: VersionPep440, borne: VersionPep440) -> bool:
    """>V (local ignoré) exclut les post-versions de V elle-même, sauf si V en est une."""
    p = publique(v)
    if not cle_pep440(p) > cle_pep440(borne):
        return False
    if borne.post is not None or p.post is None:
        return True
    return cle_pep440(p._replace(post=None, dev=None)) != cle_pep440(borne)


def satisfait_specificateur(brute: str, v: VersionPep440 | None, s: Specificateur) -> bool:
    """Teste un spécificateur, politique des pré-versions mise à part."""
    if s.operateur == "===":
        return brute.lower() == s.texte.lower()
    if v is None or s.version is None:
        return False
    operations: dict[str, Callable[[], bool]] = {
        "==": lambda: egal_pep440(v, s),
        "!=": lambda: not egal_pep440(v, s),
        "~=": lambda: compatible_pep440(v, s),
        "<=": lambda: cle_pep440(publique(v)) <= cle_pep440(s.version),
        ">=": lambda: cle_pep440(publique(v)) >= cle_pep440(s.version),
        "<": lambda: inferieur_strict(v, s.version),
        ">": lambda: superieur_strict(v, s.version),
    }
    return operations[s.operateur]()


def contrainte_nomme_pre(specs: list[Specificateur]) -> bool:
    """Vrai si un spécificateur (hors != et ==X.*) nomme une pré-version."""
    for s in specs:
        if s.operateur == "!=" or s.joker:
            continue
        version = analyser_pep440(s.texte) if s.operateur == "===" else s.version
        if version is not None and est_pre_pep440(version):
            return True
    return False


# --------------------------------------------------------------------------- SemVer


def analyser_semver(texte: str) -> VersionSemver | None:
    """Analyse une version SemVer 2.0 stricte ; None si non conforme (ou démesurée)."""
    m = MOTIF_SEMVER.match(texte) if len(texte) <= LONGUEUR_MAX_VERSION else None
    if m is None:
        return None
    pre = tuple(m.group(4).split(".")) if m.group(4) else ()
    build = tuple(m.group(5).split(".")) if m.group(5) else ()
    return VersionSemver(int(m.group(1)), int(m.group(2)), int(m.group(3)), pre, build)


def cle_semver(v: VersionSemver) -> tuple[Any, ...]:
    """Précédence SemVer 2.0 (build ignoré)."""
    if not v.pre:
        pre: tuple[Any, ...] = (1,)
    else:
        pre = (0, tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in v.pre))
    return (v.majeur, v.mineur, v.correctif, pre)


def normaliser_semver(v: VersionSemver) -> str:
    """Forme canonique (identique à l'entrée valide)."""
    texte = f"{v.majeur}.{v.mineur}.{v.correctif}"
    if v.pre:
        texte += "-" + ".".join(v.pre)
    if v.build:
        texte += "+" + ".".join(v.build)
    return texte


def analyser_contrainte_semver(texte: str) -> list[tuple[str, VersionSemver]]:
    """Analyse « >=1.2.3 <2.0.0 » (espaces ou virgules) en comparateurs."""
    if re.search(r"[\^~|*]|(?:^|[\s,.])[xX](?:$|[\s,.])", texte):
        raise ErreurEntree("plages npm (^, ~, x, *, ||) non prises en charge : "
                           "écrivez les bornes explicitement (>=1.2.3 <2.0.0)")
    compact = re.sub(r"(>=|<=|!=|==|=|<|>)\s+", r"\1", texte.strip())
    resultat = []
    for jeton in (j for j in re.split(r"[\s,]+", compact) if j):
        m = MOTIF_COMPARATEUR_SEMVER.match(jeton)
        version = analyser_semver(m.group(2)) if m else None
        if m is None or version is None:
            raise ErreurEntree(f"comparateur SemVer invalide : « {jeton[:80]} »")
        resultat.append(((m.group(1) or "=").replace("==", "="), version))
    if not resultat:
        raise ErreurEntree("contrainte SemVer vide")
    return resultat


def satisfait_semver(v: VersionSemver, comparateurs: list[tuple[str, VersionSemver]]) -> bool:
    """Teste tous les comparateurs (conjonction)."""
    tests: dict[str, Callable[[tuple[Any, ...], tuple[Any, ...]], bool]] = {
        "=": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    }
    cle = cle_semver(v)
    return all(tests[op](cle, cle_semver(borne)) for op, borne in comparateurs)


# --------------------------------------------------------------------------- moteurs


class Moteur(NamedTuple):
    """Fonctions d'un mode : analyse, clé, normalisation, pré-version."""

    analyser: Callable[[str], Any]
    cle: Callable[[Any], tuple[Any, ...]]
    normaliser: Callable[[Any], str]
    est_pre: Callable[[Any], bool]


def moteur_du_mode(mode: str) -> Moteur:
    """Choisit les fonctions du mode demandé."""
    if mode == "semver":
        return Moteur(analyser_semver, cle_semver, normaliser_semver, lambda v: bool(v.pre))
    return Moteur(analyser_pep440, cle_pep440, normaliser_pep440, est_pre_pep440)


def preparer_contrainte(mode: str, texte: str | None) -> tuple[Callable[[str, Any], bool], bool]:
    """Rend (prédicat de satisfaction, la contrainte nomme-t-elle une pré-version)."""
    if texte is None:
        return (lambda brute, v: v is not None), False
    if mode == "semver":
        comparateurs = analyser_contrainte_semver(texte)
        nomme_pre = any(b.pre and op != "!=" for op, b in comparateurs)
        return (lambda brute, v: v is not None and satisfait_semver(v, comparateurs)), nomme_pre
    specs = analyser_specificateurs(texte)
    return (lambda brute, v: all(satisfait_specificateur(brute, v, s) for s in specs)), \
        contrainte_nomme_pre(specs)


def choisir_meilleure(fiches: list[dict[str, Any]], admettre_pre: bool) -> dict[str, Any] | None:
    """Politique PEP 440 : finales d'abord ; pré-versions si admises ou faute de finale."""
    admises = [f for f in fiches if f["_admise"] and f["_v"] is not None]
    if not admettre_pre:
        admises = [f for f in admises if not f["pre_version"]] or admises
    meilleure = None
    for fiche in admises:
        if meilleure is None or fiche["_cle"] > meilleure["_cle"]:
            meilleure = fiche
    return meilleure


def evaluer(versions: list[str], mode: str, contrainte: str | None, pre: bool) -> dict[str, Any]:
    """Analyse chaque version, teste la contrainte, trie et choisit la meilleure."""
    moteur = moteur_du_mode(mode)
    predicat, nomme_pre = preparer_contrainte(mode, contrainte)
    fiches = []
    for brute in versions:
        v = moteur.analyser(brute)
        admise = predicat(brute, v)
        fiches.append({
            "brute": brute, "valide": v is not None,
            "normalisee": moteur.normaliser(v) if v is not None else None,
            "pre_version": bool(v is not None and moteur.est_pre(v)),
            "satisfait": admise if contrainte is not None else None,
            "_admise": admise, "_v": v, "_cle": moteur.cle(v) if v is not None else None,
        })
    valides = sorted((f for f in fiches if f["_v"] is not None), key=lambda f: f["_cle"])
    meilleure = choisir_meilleure(fiches, pre or nomme_pre)
    return {
        "fiches": fiches,
        "tri": [f["normalisee"] for f in valides],
        "meilleure": meilleure["brute"] if meilleure else None,
        "pre_admises": pre or nomme_pre,
    }


# --------------------------------------------------------------------------- bibliothèques


def version_bibliotheque(module: Any) -> str:
    """Nom et version d'une bibliothèque optionnelle."""
    nom = getattr(module, "__name__", "?").split(".")[0]
    try:
        from importlib.metadata import PackageNotFoundError, version
        return f"{nom} {version(nom)}"
    except (ImportError, PackageNotFoundError):
        return nom


def avis_pep440_bibliotheque(brute: str, contrainte: str | None) -> dict[str, Any]:
    """Réponse de packaging pour une version : validité, forme normale, satisfaction."""
    assert pk_version is not None and pk_specifiers is not None
    try:
        v = pk_version.Version(brute) if len(brute) <= LONGUEUR_MAX_VERSION else None
    except ValueError:
        v = None
    satisfait = None
    if contrainte is not None:
        satisfait = pk_specifiers.SpecifierSet(contrainte).contains(brute, prereleases=True)
    return {"valide": v is not None, "normalisee": str(v) if v is not None else None,
            "satisfait": satisfait}


def meilleure_pep440_bibliotheque(versions: list[str], contrainte: str | None,
                                  pre: bool) -> str | None:
    """Meilleure version selon packaging (filter puis max)."""
    assert pk_version is not None and pk_specifiers is not None
    valides = []
    for brute in versions:
        try:
            if len(brute) <= LONGUEUR_MAX_VERSION:
                valides.append((pk_version.Version(brute), brute))
        except ValueError:
            continue
    ensemble = pk_specifiers.SpecifierSet(contrainte or "", prereleases=True if pre else None)
    retenues = set(ensemble.filter([b for _, b in valides]))
    valides = [(v, b) for v, b in valides if b in retenues]
    return max(valides, key=lambda p: p[0])[1] if valides else None


def avis_semver_bibliotheque(brute: str, contrainte: str | None) -> dict[str, Any]:
    """Réponse de python-semver pour une version."""
    assert lib_semver is not None
    valide = len(brute) <= LONGUEUR_MAX_VERSION and lib_semver.Version.is_valid(brute)
    satisfait = None
    if contrainte is not None and valide:
        v = lib_semver.Version.parse(brute)
        comparateurs = analyser_contrainte_semver(contrainte)
        satisfait = all(v.match(("==" if op == "=" else op) + normaliser_semver(b))
                        for op, b in comparateurs)
    elif contrainte is not None:
        satisfait = False
    return {"valide": valide, "normalisee": brute if valide else None, "satisfait": satisfait}


def ordre_semver_bibliotheque(versions: list[str]) -> list[str]:
    """Tri croissant selon python-semver (build ignoré, tri stable)."""
    assert lib_semver is not None
    from functools import cmp_to_key
    valides = [b for b in versions if len(b) <= LONGUEUR_MAX_VERSION and lib_semver.Version.is_valid(b)]
    return sorted(valides, key=cmp_to_key(lambda a, b: lib_semver.compare(a, b)))


def ecarts_fiches(fiches: list[dict[str, Any]], avis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare champ à champ la réponse stdlib et celle de la bibliothèque."""
    ecarts = []
    for fiche, autre in zip(fiches, avis):
        for champ in ("valide", "normalisee", "satisfait"):
            if fiche[champ] != autre[champ]:
                ecarts.append({"version": fiche["brute"], "champ": champ,
                               "stdlib": fiche[champ], "bibliotheque": autre[champ]})
    return ecarts


def controle_croise(versions: list[str], mode: str, contrainte: str | None, pre: bool,
                    reponse: dict[str, Any]) -> dict[str, Any]:
    """Recalcule la réponse avec la bibliothèque du mode, si elle est importable."""
    if mode == "pep440" and pk_version is not None:
        avis = [avis_pep440_bibliotheque(b, contrainte) for b in versions]
        autre_meilleure = meilleure_pep440_bibliotheque(versions, contrainte, pre)
        nom = version_bibliotheque(pk_version)
    elif mode == "semver" and lib_semver is not None:
        avis = [avis_semver_bibliotheque(b, contrainte) for b in versions]
        autre_meilleure = None
        nom = version_bibliotheque(lib_semver)
    else:
        return {"effectue": False, "bibliotheque": None, "ecarts": []}
    ecarts = ecarts_fiches(reponse["fiches"], avis)
    if mode == "pep440" and autre_meilleure != reponse["meilleure"]:
        ecarts.append({"version": None, "champ": "meilleure", "stdlib": reponse["meilleure"],
                       "bibliotheque": autre_meilleure})
    if mode == "semver":
        tri_stdlib = [f["brute"] for f in sorted((f for f in reponse["fiches"] if f["_v"]),
                                                 key=lambda f: f["_cle"])]
        tri_lib = ordre_semver_bibliotheque(versions)
        if tri_stdlib != tri_lib:
            ecarts.append({"version": None, "champ": "tri", "stdlib": tri_stdlib,
                           "bibliotheque": tri_lib})
    return {"effectue": True, "bibliotheque": nom, "ecarts": ecarts}


def signaler_bibliotheque_absente(mode: str) -> None:
    """Une ligne sur stderr si la bibliothèque de contrôle du mode manque."""
    if mode == "pep440" and pk_version is None:
        print("packaging absent : contrôle croisé PEP 440 non effectué, moteur stdlib seul.",
              file=sys.stderr)
    elif mode == "semver" and lib_semver is None:
        print("semver absent : contrôle croisé SemVer non effectué, moteur stdlib seul.",
              file=sys.stderr)


# --------------------------------------------------------------------------- corpus


CORPUS_PEP440: tuple[tuple[str, str, Any, Any], ...] = (
    # (genre, entrée, contrainte ou seconde version, réponse attendue)
    ("norm", "1.0", None, "1.0"),
    ("norm", "v1.0", None, "1.0"),
    ("norm", " 1.0 ", None, "1.0"),
    ("norm", "1.0.0", None, "1.0.0"),
    ("norm", "1!2.0", None, "1!2.0"),
    ("norm", "0!1.0", None, "1.0"),
    ("norm", "1.0a", None, "1.0a0"),
    ("norm", "1.0-alpha.1", None, "1.0a1"),
    ("norm", "1.0BETA2", None, "1.0b2"),
    ("norm", "1.0c1", None, "1.0rc1"),
    ("norm", "1.0pre3", None, "1.0rc3"),
    ("norm", "1.0preview4", None, "1.0rc4"),
    ("norm", "1.0-1", None, "1.0.post1"),
    ("norm", "1.0.rev2", None, "1.0.post2"),
    ("norm", "1.0r", None, "1.0.post0"),
    ("norm", "1.0-dev", None, "1.0.dev0"),
    ("norm", "1.0.0007", None, "1.0.7"),
    ("norm", "1.0+Ubuntu-1", None, "1.0+ubuntu.1"),
    ("norm", "1.0+abc.007", None, "1.0+abc.7"),
    ("norm", "1.0a1.post2.dev3", None, "1.0a1.post2.dev3"),
    ("norm", "1.0+", None, None),
    ("norm", "1.0.x", None, None),
    ("norm", "01.2-beta", None, "1.2b0"),
    ("norm", "1.0-", None, None),
    ("norm", "", None, None),
    ("ordre", "1.9", "1.10", -1),
    ("ordre", "1.0", "1.0.0", 0),
    ("ordre", "1.0.dev1", "1.0a1", -1),
    ("ordre", "1.0a2", "1.0a10", -1),
    ("ordre", "1.0rc1", "1.0", -1),
    ("ordre", "1.0", "1.0.post1", -1),
    ("ordre", "1.0.post1.dev1", "1.0.post1", -1),
    ("ordre", "1.0a1.post1", "1.0a2", -1),
    ("ordre", "1.0", "1.0+local", -1),
    ("ordre", "1.0+abc", "1.0+1", -1),
    ("ordre", "1.0+1", "1.0+1.0", -1),
    ("ordre", "1!0.1", "2.0", 1),
    ("ordre", "1.0b1", "1.0c1", -1),
    ("contient", "1.4.2", ">=1.4,<2", True),
    ("contient", "2.0", ">=1.4,<2", False),
    ("contient", "2.0a1", "<2.0", False),
    ("contient", "2.0.dev1", "<2.0", False),
    ("contient", "2.0a1", "<2.0b1", True),
    ("contient", "1.7.0.post1", ">1.7", False),
    ("contient", "1.7.1", ">1.7", True),
    ("contient", "1.7.0.post2", ">1.7.post1", True),
    ("contient", "1.1.post1", "==1.1.*", True),
    ("contient", "1.10", "==1.1.*", False),
    ("contient", "1", "==1.0.*", True),
    ("contient", "1.1a1", "==1.1.*", True),
    ("contient", "1.0+local", "==1.0", True),
    ("contient", "1.0", "==1.0+local", False),
    ("contient", "1.0+local", "!=1.0", False),
    ("contient", "2.3", "~=2.2", True),
    ("contient", "3.0", "~=2.2", False),
    ("contient", "1.4.9", "~=1.4.5a4", True),
    ("contient", "1.5.0", "~=1.4.5", False),
    ("contient", "2.2.post3", "~=2.2.post3", True),
    ("contient", "1.0", "===1.0", True),
    ("contient", "1.0.0", "===1.0", False),
    ("contient", "1!1.0", "==1.*", False),
    ("contient", "1.0+local", "<=1.0", True),
    ("contient", "1.0.post1", ">1.0a1", True),
    ("contient", "1.0a1.post1", ">1.0a1", False),
    ("contient", "1.0.0.post1", ">1.0", False),
    ("contient", "1.0.post2.dev1", ">1.0.post1", True),
    ("contient", "2.0.0+abc", ">2.0.0", False),
    ("contient", "1.0a1", "<1.0.post1", True),
    ("contient", "1.0.post1.dev1", "<1.0.post1", False),
    ("contient", "2.0rc1.post1", "<2.0", False),
    ("contient", "2.0a1+local", "<2.0", False),
    ("meilleure", "1.0 1.1 2.0b1", ">=1.0", "1.1"),
    ("meilleure", "1.0 2.0b1", ">=1.0", "1.0"),
    ("meilleure", "0.9 2.0b1", ">=1.0", "2.0b1"),
    ("meilleure", "1.0 1.1 2.0b1", ">=1.0b1", "2.0b1"),
    ("meilleure", "1.0 1.1 1.2 2.0", "~=1.0,!=1.2", "1.1"),
)

CORPUS_SEMVER: tuple[tuple[str, str, Any, Any], ...] = (
    ("valide", "1.0.0", None, True),
    ("valide", "1.0.0-alpha.1+build.5", None, True),
    ("valide", "01.0.0", None, False),
    ("valide", "1.0.0-01", None, False),
    ("valide", "1.0", None, False),
    ("valide", "v1.0.0", None, False),
    ("valide", "1.0.0-", None, False),
    ("valide", "1.0.0-0A.is.legal", None, True),
    ("ordre", "1.0.0-alpha", "1.0.0-alpha.1", -1),
    ("ordre", "1.0.0-alpha.1", "1.0.0-alpha.beta", -1),
    ("ordre", "1.0.0-alpha.beta", "1.0.0-beta", -1),
    ("ordre", "1.0.0-beta.2", "1.0.0-beta.11", -1),
    ("ordre", "1.0.0-beta.11", "1.0.0-rc.1", -1),
    ("ordre", "1.0.0-rc.1", "1.0.0", -1),
    ("ordre", "1.0.0+a", "1.0.0+b", 0),
    ("ordre", "2.0.0", "10.0.0", -1),
    ("ordre", "1.0.0-2", "1.0.0-11", -1),
    ("ordre", "1.0.0-a10", "1.0.0-a9", -1),
    ("contient", "1.4.0", ">=1.2.3 <2.0.0", True),
    ("contient", "2.0.0-rc.1", "<2.0.0", True),
    ("contient", "1.2.3+build", "=1.2.3", True),
    ("contient", "1.2.3", "!=1.2.3", False),
)


def comparer_signe(a: tuple[Any, ...], b: tuple[Any, ...]) -> int:
    """Signe de la comparaison de deux clés."""
    return (a > b) - (a < b)


def reponse_stdlib_corpus(mode: str, genre: str, entree: str, autre: Any) -> Any:
    """Réponse de la mise en œuvre stdlib à un cas du corpus."""
    moteur = moteur_du_mode(mode)
    if genre == "norm":
        v = moteur.analyser(entree)
        return moteur.normaliser(v) if v is not None else None
    if genre == "valide":
        return moteur.analyser(entree) is not None
    if genre == "ordre":
        return comparer_signe(moteur.cle(moteur.analyser(entree)), moteur.cle(moteur.analyser(autre)))
    if genre == "contient":
        predicat, _ = preparer_contrainte(mode, autre)
        return predicat(entree, moteur.analyser(entree))
    return evaluer(entree.split(), mode, autre, False)["meilleure"]


def reponse_packaging_corpus(genre: str, entree: str, autre: Any) -> Any:
    """Réponse de packaging à un cas PEP 440 du corpus."""
    assert pk_version is not None and pk_specifiers is not None
    if genre == "norm":
        try:
            return str(pk_version.Version(entree))
        except pk_version.InvalidVersion:
            return None
    if genre == "ordre":
        a, b = pk_version.Version(entree), pk_version.Version(autre)
        return (a > b) - (a < b)
    if genre == "contient":
        return pk_specifiers.SpecifierSet(autre).contains(entree, prereleases=True)
    return meilleure_pep440_bibliotheque(entree.split(), autre, False)


def reponse_semver_corpus(genre: str, entree: str, autre: Any) -> Any:
    """Réponse de python-semver à un cas SemVer du corpus."""
    assert lib_semver is not None
    if genre == "valide":
        return lib_semver.Version.is_valid(entree)
    if genre == "ordre":
        return lib_semver.compare(entree, autre)
    return avis_semver_bibliotheque(entree, autre)["satisfait"]


def rejouer_corpus() -> dict[str, Any]:
    """Rejoue le corpus sur la stdlib et sur les bibliothèques importables."""
    cas, ecarts_stdlib, ecarts_bib = [], [], []
    bibliotheques = {"pep440": reponse_packaging_corpus if pk_version is not None else None,
                     "semver": reponse_semver_corpus if lib_semver is not None else None}
    for mode, corpus in (("pep440", CORPUS_PEP440), ("semver", CORPUS_SEMVER)):
        for genre, entree, autre, attendu in corpus:
            ident = f"{mode}:{genre}:{entree}" + (f" | {autre}" if autre is not None else "")
            cas.append(ident)
            obtenu = reponse_stdlib_corpus(mode, genre, entree, autre)
            if obtenu != attendu:
                ecarts_stdlib.append({"cas": ident, "attendu": attendu, "obtenu": obtenu})
            appel = bibliotheques[mode]
            if appel is not None:
                autre_reponse = appel(genre, entree, autre)
                if autre_reponse != attendu:
                    ecarts_bib.append({"cas": ident, "attendu": attendu, "bibliotheque": autre_reponse,
                                       "stdlib": obtenu})
    return {"cas": cas, "ecarts_stdlib": ecarts_stdlib, "ecarts_bibliotheques": ecarts_bib,
            "bibliotheques": [version_bibliotheque(m) for m in (pk_version, lib_semver) if m]}


# --------------------------------------------------------------------------- entrées / sorties


def extraire_contrat(doc: str) -> dict[str, str]:
    """Extrait les sections du contrat de mesure du docstring."""
    contrat: dict[str, str] = {}
    courant = None
    for ligne in doc.splitlines():
        if ligne.strip() in TITRES_CONTRAT and not ligne.startswith(" "):
            courant = ligne.strip()
            contrat[courant] = ""
        elif courant is not None:
            contrat[courant] = (contrat[courant] + " " + ligne.strip()).strip()
    return contrat


def lire_versions_fichier(chemin: Path) -> list[str]:
    """Lit une version par ligne (# = commentaire), lecture bornée."""
    if not chemin.exists():
        raise ErreurEntree(f"fichier introuvable : {chemin}")
    if not chemin.is_file():
        raise ErreurEntree(f"ce n'est pas un fichier : {chemin}")
    try:
        with chemin.open("rb") as flux:
            brut = flux.read(MAX_OCTETS_DEPUIS + 1)
        texte = brut[:MAX_OCTETS_DEPUIS].decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ErreurEntree(f"fichier illisible en UTF-8 : {chemin} ({exc})") from exc
    if "\x00" in texte:
        raise ErreurEntree(f"fichier binaire refusé : {chemin}")
    lignes = texte.splitlines()[:MAX_LIGNES_DEPUIS]
    return [ligne.split("#", 1)[0].strip() for ligne in lignes if ligne.split("#", 1)[0].strip()]


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Compare des versions (PEP 440 ou SemVer 2.0), teste une contrainte et "
                    "choisit la plus récente version compatible.",
        epilog=f"Exemple : python {RACINE.name}/comparer_versions.py 1.4.2 1.10 2.0.0rc1 "
               "--contrainte '>=1.4,<2' --json",
    )
    parseur.add_argument("versions", nargs="*", help="versions à examiner (une par argument)")
    parseur.add_argument("--contrainte", help="spécificateurs PEP 440 (« >=1.4,<2 ») ou "
                                              "comparateurs SemVer (« >=1.2.3 <2.0.0 »)")
    parseur.add_argument("--mode", choices=("pep440", "semver"), default="pep440",
                         help="grammaire des versions (défaut : pep440)")
    parseur.add_argument("--pre", action="store_true",
                         help="admettre les pré-versions dans le choix de la meilleure")
    parseur.add_argument("--depuis", metavar="FICHIER",
                         help="lire aussi des versions dans ce fichier (une par ligne)")
    parseur.add_argument("--corpus", action="store_true",
                         help="rejouer le corpus intégré de cas à réponse connue")
    parseur.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def tronquer(noms: list[str]) -> tuple[list[str], bool]:
    """Tronque la liste des éléments examinés."""
    return noms[:MAX_EXAMINES], len(noms) > MAX_EXAMINES


def rapport_versions(args: argparse.Namespace, versions: list[str]) -> tuple[dict[str, Any], int]:
    """Construit le rapport du mode normal et son code de sortie."""
    reponse = evaluer(versions, args.mode, args.contrainte, args.pre)
    croise = controle_croise(versions, args.mode, args.contrainte, args.pre, reponse)
    fiches = [{k: v for k, v in f.items() if not k.startswith("_")} for f in reponse["fiches"]]
    invalides = [f["brute"] for f in fiches if not f["valide"]]
    examines, tronques = tronquer(versions)
    rapport = {
        "denominateur": len(versions), "examines": examines, "examines_tronques": tronques,
        "moteur": croise["bibliotheque"].split()[0] if croise["effectue"] else "stdlib",
        "reponse_calculee_par": "stdlib", "mode": args.mode, "contrainte": args.contrainte,
        "versions": fiches, "tri_croissant": reponse["tri"], "meilleure": reponse["meilleure"],
        "pre_versions_admises": reponse["pre_admises"], "invalides": invalides,
        "controle_croise": croise,
    }
    code = 0
    if invalides or croise["ecarts"] or (args.contrainte is not None and reponse["meilleure"] is None):
        code = 1
    if len(invalides) == len(versions):
        code = 2
    return rapport, code


def rapport_corpus() -> tuple[dict[str, Any], int]:
    """Construit le rapport du mode --corpus."""
    resultat = rejouer_corpus()
    examines, tronques = tronquer(resultat["cas"])
    rapport = {
        "denominateur": len(resultat["cas"]), "examines": examines,
        "examines_tronques": tronques,
        "moteur": "+".join(b.split()[0] for b in resultat["bibliotheques"]) or "stdlib",
        "bibliotheques": resultat["bibliotheques"],
        "ecarts_stdlib": resultat["ecarts_stdlib"],
        "ecarts_bibliotheques": resultat["ecarts_bibliotheques"],
    }
    return rapport, 1 if resultat["ecarts_stdlib"] else 0


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Sortie lisible."""
    if "versions" not in rapport:
        print(f"Corpus : {rapport['denominateur']} cas ; écarts stdlib : "
              f"{len(rapport['ecarts_stdlib'])} ; écarts bibliothèques : "
              f"{len(rapport['ecarts_bibliotheques'])} ({', '.join(rapport['bibliotheques']) or 'aucune'})")
        for ecart in rapport["ecarts_stdlib"] + rapport["ecarts_bibliotheques"]:
            print(f"  {ecart}")
        return
    print(f"Mode {rapport['mode']} — contrainte : {rapport['contrainte'] or '(aucune)'}")
    for f in rapport["versions"]:
        etat = "invalide" if not f["valide"] else (
            "" if f["satisfait"] is None else ("satisfait" if f["satisfait"] else "ne satisfait pas"))
        pre = " (pré-version)" if f["pre_version"] else ""
        print(f"  {f['brute'][:60]:<20} → {(f['normalisee'] or '-')[:60]:<20} {etat}{pre}")
    print(f"Ordre croissant : {' < '.join(rapport['tri_croissant']) or '(aucune version valide)'}")
    print(f"Meilleure version : {rapport['meilleure'] or 'aucune'}")
    croise = rapport["controle_croise"]
    if croise["effectue"]:
        print(f"Contrôle croisé ({croise['bibliotheque']}) : {len(croise['ecarts'])} écart(s)")
        for ecart in croise["ecarts"]:
            print(f"  {ecart}")


def neutraliser_sortie() -> None:
    """Lecteur parti (tube fermé) : stdout est redirigé vers le néant, sans trace d'erreur."""
    nul = os.open(os.devnull, os.O_WRONLY)
    os.dup2(nul, sys.stdout.fileno())


def collecter_versions(args: argparse.Namespace) -> list[str]:
    """Versions passées en ligne puis lues dans --depuis."""
    versions = list(args.versions)
    if args.depuis:
        chemin = Path(args.depuis)
        if not chemin.is_absolute() and args.racine:
            chemin = Path(args.racine) / chemin
        versions += lire_versions_fichier(chemin)
    return versions


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


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    try:
        if args.corpus:
            if pk_version is None or lib_semver is None:
                print("packaging ou semver absent : corpus rejoué sans cette bibliothèque "
                      "(réponses stdlib confrontées aux réponses attendues seulement).",
                      file=sys.stderr)
            rapport, code = rapport_corpus()
        else:
            versions = collecter_versions(args)
            if not versions:
                return afficher_refus("dénominateur nul : rien à examiner (aucune version "
                                      "fournie).", 3, args.json)
            signaler_bibliotheque_absente(args.mode)
            rapport, code = rapport_versions(args, versions)
    except ErreurEntree as exc:
        return afficher_refus(f"entrée invalide : {exc}", 2, args.json)
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    afficher_resultat(rapport, args.json)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
