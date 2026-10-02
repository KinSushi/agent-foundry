r"""Vérifie qu'un CHANGELOG suit Keep a Changelog 1.1 et reste cohérent avec les versions publiées.

Mesuré le 2026-10-02 : keepachangelog 2.0.0 lit sans la moindre erreur un journal qui contient
la version 1.2.0 deux fois, 1.2.0 au-dessus de 1.3.0, la date 2026-13-45 et une rubrique
« Ajouté » ; il fusionne en silence les deux 1.2.0 en une seule entrée. Un agent qui
« lit » le journal par cette bibliothèque ne voit donc aucun de ces quatre défauts.

QUESTION
    Ce CHANGELOG suit-il Keep a Changelog 1.1 et est-il cohérent avec les versions ?
MESURE
    Analyse ligne à ligne du Markdown (blocs de code clôturés ignorés) : titre de niveau 1,
    sections de niveau 2 « [Unreleased] » puis « [X.Y.Z] - AAAA-MM-JJ » (mention [YANKED]
    admise), rubriques de niveau 3, éléments de liste, définitions de liens en bas. Contrôles :
    Unreleased présente, en tête et sans date ; versions SemVer 2.0 valides, sans doublon, en
    ordre de précédence strictement décroissant ; dates ISO 8601 réelles, jamais plus récentes
    que celles du dessus ; rubriques limitées à Added, Changed, Deprecated, Removed, Fixed,
    Security (casse comprise), sans doublon dans une version ; chaque version entre crochets
    a sa définition de lien ; un lien de comparaison « a...b » relie bien la version à la
    précédente (Unreleased : dernière version...HEAD). Avec --version (valeur ou fichier
    pyproject.toml, package.json, Cargo.toml), la version déclarée doit figurer dans le journal
    et en être la plus récente publiée. Si keepachangelog est importable, sa lecture des
    versions est comparée à la nôtre.
HYPOTHÈSES
    Le journal est en Markdown ATX (titres en #), en UTF-8, écrit selon keepachangelog.com
    1.1.0 ; les versions sont des numéros SemVer (un « v » initial est toléré avec
    avertissement) ; les étiquettes git des liens de comparaison sont « X.Y.Z » ou « vX.Y.Z ».
LIMITES
    Titres soulignés (Setext) non reconnus. Les liens ne sont jamais ouverts (aucun accès
    réseau) : seule leur forme est contrôlée, et seulement pour les liens « compare/a...b »
    (GitHub, GitLab, Gitea) ; les autres hébergeurs ne sont pas vérifiés. Le contenu des
    éléments n'est pas jugé (une entrée vide de sens passe). Une version publiée mais absente
    du journal n'est vue que si elle est donnée par --version.
CONTRE-EXEMPLES
    CONTRE_EXEMPLE_A_COMPLETER
INVOCATION
    {outil} --texte="# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-15\n### Added\n- Première version.\n\n[unreleased]: https://exemple.org/depot/compare/v1.0.0...HEAD\n[1.0.0]: https://exemple.org/depot/releases/tag/v1.0.0" --json
DOMAINE
    Journaux de modifications de projets qui publient des versions SemVer : contrôle avant
    publication, revue d'une contribution, cohérence entre manifeste et journal.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import keepachangelog
except ImportError:
    keepachangelog = None

RACINE = Path(__file__).resolve().parent

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
                  TITRE_INVOCATION, "DOMAINE")
CHANGELOG = "CHANGELOG"
MENTION_RETIREE = "YANKED"
REFERENCE_HEAD = "HEAD"
FORMAT_DATE = "AAAA-MM-JJ"
ENCODAGE = "UTF-8"
RUBRIQUES = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
NOMS_JOURNAL = ("CHANGELOG.md", "CHANGELOG", "CHANGELOG.markdown", "CHANGES.md", "HISTORY.md",
                "NEWS.md", "changelog.md")
MANIFESTES = ("pyproject.toml", "package.json", "Cargo.toml")

MAX_OCTETS = 16 * 1024 * 1024
MAX_EXAMINES = 200
MOTIF_SECTION = re.compile(r"^##(?!#)\s+(?P<titre>\[[^\]]*\]|[^\s\[]+)\s*(?:(?P<tiret>[-–—])\s*"
                           r"(?P<date>[^\s\[]+))?\s*(?P<retiree>\[YANKED\])?\s*$", re.IGNORECASE)
MOTIF_RUBRIQUE = re.compile(r"^###(?!#)\s+(?P<nom>.+?)\s*$")
MOTIF_LIEN = re.compile(r"^\s{0,3}\[(?P<ref>[^\]]+)\]:\s*(?P<url>\S+)")
MOTIF_SEMVER = re.compile(
    r"^(?P<maj>0|[1-9]\d*)\.(?P<min>0|[1-9]\d*)\.(?P<pat>0|[1-9]\d*)"
    r"(?:-(?P<pre>(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-]"
    r"[0-9A-Za-z-]*))*))?(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")
MOTIF_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MOTIF_COMPARE = re.compile(r"/compare/(?P<a>[^/\s]+?)\.\.\.(?P<b>[^/\s]+?)/?$")
MOTIF_CLOTURE = re.compile(r"^\s{0,3}(```|~~~)")


class ErreurEntree(Exception):
    """Entrée invalide (code 2) ou rien à examiner (code 3)."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Section:
    """Section de niveau 2 : Unreleased ou une version."""

    ligne: int
    brut: str
    nom: str
    entre_crochets: bool
    date: str | None
    tiret: str | None
    retiree: bool
    rubriques: list[tuple[int, str]] = field(default_factory=list)
    elements: int = 0
    lignes_hors_liste: int = 0

    @property
    def est_unreleased(self) -> bool:
        """Section « Unreleased » (casse indifférente)."""
        return self.nom.lower() == "unreleased"


@dataclass
class Journal:
    """Journal lu : titre, sections, liens."""

    titre: str | None = None
    sections: list[Section] = field(default_factory=list)
    liens: dict[str, tuple[int, str]] = field(default_factory=dict)
    liens_doubles: list[str] = field(default_factory=list)


@dataclass
class Constats:
    """Erreurs (code 1) et avertissements."""

    erreurs: list[dict[str, Any]] = field(default_factory=list)
    avertissements: list[dict[str, Any]] = field(default_factory=list)

    def erreur(self, ligne: int | None, message: str) -> None:
        """Ajoute une erreur."""
        self.erreurs.append({"ligne": ligne, "message": message})

    def avertir(self, ligne: int | None, message: str) -> None:
        """Ajoute un avertissement."""
        self.avertissements.append({"ligne": ligne, "message": message})


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

def decoder_echappements(texte: str) -> str:
    """\\n et \\t d'un texte en ligne qui ne contient aucun vrai saut de ligne."""
    if "\n" in texte:
        return texte
    return re.sub(r"\\([nt\\])", lambda m: {"n": "\n", "t": "\t", "\\": "\\"}[m.group(1)], texte)


def lire_texte(chemin: Path) -> str:
    """Lit un fichier texte borné, UTF-8 strict (BOM toléré)."""
    try:
        with chemin.open("rb") as flux:
            donnees = flux.read(MAX_OCTETS + 1)
    except OSError as exc:
        raise ErreurEntree(f"lecture impossible de {chemin} : {exc.strerror}") from exc
    if len(donnees) > MAX_OCTETS:
        raise ErreurEntree(f"{chemin} dépasse {MAX_OCTETS // (1024 * 1024)} Mo")
    if b"\x00" in donnees[:8000]:
        raise ErreurEntree(f"{chemin} est binaire (octet nul), pas un journal Markdown")
    try:
        return donnees.decode(ENCODAGE).removeprefix("﻿")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{chemin} n'est pas en {ENCODAGE} (octet {exc.start})") from exc


def trouver_journal(chemin: Path) -> Path:
    """Le fichier lui-même, ou le journal d'un dossier (CHANGELOG.md, CHANGES.md…)."""
    if not chemin.exists():
        raise ErreurEntree(f"chemin introuvable : {chemin}")
    if chemin.is_file():
        return chemin
    if not chemin.is_dir():
        raise ErreurEntree(f"{chemin} n'est ni un fichier ni un dossier")
    for nom in NOMS_JOURNAL:
        if (chemin / nom).is_file():
            return chemin / nom
    raise ErreurEntree(f"aucun journal ({', '.join(NOMS_JOURNAL[:5])}…) dans {chemin} : "
                       "dénominateur nul, rien à examiner", code=3)


def analyser_section(numero: int, ligne: str) -> Section | None:
    """Titre de niveau 2 → Section, ou None s'il n'a pas la forme attendue."""
    trouve = MOTIF_SECTION.match(ligne.rstrip())
    if not trouve:
        return None
    titre = trouve.group("titre")
    crochets = titre.startswith("[")
    return Section(ligne=numero, brut=ligne.strip(), nom=titre.strip("[]").strip(),
                   entre_crochets=crochets, date=trouve.group("date"),
                   tiret=trouve.group("tiret"), retiree=trouve.group("retiree") is not None)


def lire_journal(texte: str, constats: Constats) -> Journal:
    """Découpe le Markdown en titre, sections, rubriques, éléments et liens."""
    journal = Journal()
    dans_code = False
    for numero, ligne in enumerate(texte.splitlines(), start=1):
        if MOTIF_CLOTURE.match(ligne):
            dans_code = not dans_code
            continue
        if dans_code:
            continue
        lire_ligne(journal, numero, ligne, constats)
    return journal


def lire_ligne(journal: Journal, numero: int, ligne: str, constats: Constats) -> None:
    """Range une ligne hors bloc de code."""
    if ligne.startswith("# ") and journal.titre is None and not journal.sections:
        journal.titre = ligne[2:].strip()
        return
    if re.match(r"^##(?!#)", ligne):
        section = analyser_section(numero, ligne)
        if section is None:
            constats.erreur(numero, f"titre de section illisible : « {ligne.strip()[:80]} »")
        else:
            journal.sections.append(section)
        return
    lien = MOTIF_LIEN.match(ligne)
    if lien:
        cle = lien.group("ref").strip().lower()
        if cle in journal.liens:
            journal.liens_doubles.append(cle)
        journal.liens.setdefault(cle, (numero, lien.group("url")))
        return
    if not journal.sections:
        return
    courante = journal.sections[-1]
    rubrique = MOTIF_RUBRIQUE.match(ligne)
    if rubrique:
        courante.rubriques.append((numero, rubrique.group("nom")))
    elif re.match(r"^\s*[-*+]\s+\S", ligne):
        courante.elements += 1
    elif ligne.strip() and not ligne.startswith((" ", "\t")):
        courante.lignes_hors_liste += 1


# ---------------------------------------------------------------------------
# SemVer
# ---------------------------------------------------------------------------

def cle_semver(version: str) -> tuple[Any, ...] | None:
    """Clé de précédence SemVer 2.0 (métadonnées de build ignorées) ; None si invalide."""
    trouve = MOTIF_SEMVER.match(version)
    if not trouve:
        return None
    base = (int(trouve.group("maj")), int(trouve.group("min")), int(trouve.group("pat")))
    pre = trouve.group("pre")
    if pre is None:
        return base + (1, ())
    identifiants = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return base + (0, identifiants)


def normaliser_version(nom: str, section: Section, constats: Constats) -> str:
    """Retire un « v » initial (avec avertissement)."""
    if re.match(r"^[vV]\d", nom):
        constats.avertir(section.ligne, f"« {nom} » : Keep a Changelog écrit les versions sans « v »")
        return nom[1:]
    return nom


# ---------------------------------------------------------------------------
# Contrôles
# ---------------------------------------------------------------------------

def controler_unreleased(journal: Journal, constats: Constats) -> None:
    """Unreleased : présente, unique, en tête, sans date."""
    positions = [i for i, s in enumerate(journal.sections) if s.est_unreleased]
    if not positions:
        constats.avertir(None, "pas de section [Unreleased] (recommandée en tête du journal)")
        return
    if len(positions) > 1:
        constats.erreur(journal.sections[positions[1]].ligne, "section Unreleased en double")
    premiere = journal.sections[positions[0]]
    if positions[0] != 0:
        constats.erreur(premiere.ligne, "la section Unreleased doit être la première")
    if premiere.date:
        constats.erreur(premiere.ligne, "la section Unreleased ne porte pas de date")


def controler_date(section: Section, constats: Constats) -> date | None:
    """Date ISO 8601 réelle (AAAA-MM-JJ), tiret « - » attendu."""
    if not section.date:
        constats.erreur(section.ligne, f"version {section.nom} sans date de publication")
        return None
    if section.tiret != "-":
        constats.avertir(section.ligne, f"séparateur « {section.tiret} » : Keep a Changelog "
                                        "utilise « - » entre version et date")
    if not MOTIF_DATE.match(section.date):
        constats.erreur(section.ligne, f"date « {section.date} » hors format {FORMAT_DATE}")
        return None
    try:
        return date.fromisoformat(section.date)
    except ValueError:
        constats.erreur(section.ligne, f"date « {section.date} » inexistante")
        return None


def controler_rubriques(section: Section, constats: Constats) -> None:
    """Rubriques autorisées, sans doublon ; contenu non vide."""
    vues: set[str] = set()
    for ligne, nom in section.rubriques:
        connue = next((r for r in RUBRIQUES if r.lower() == nom.lower()), None)
        if connue is None:
            constats.erreur(ligne, f"rubrique « {nom} » hors de {', '.join(RUBRIQUES)}")
        elif connue != nom:
            constats.avertir(ligne, f"rubrique « {nom} » : écrire « {connue} »")
        if connue and connue in vues:
            constats.erreur(ligne, f"rubrique {connue} en double dans {section.nom}")
        vues.add(connue or nom)
    if not section.est_unreleased and section.elements == 0:
        constats.avertir(section.ligne, f"version {section.nom} sans aucun changement listé")
    if section.elements and not section.rubriques:
        constats.avertir(section.ligne, f"{section.nom} : changements hors rubrique "
                                        "(### Added, ### Fixed…)")


def controler_versions(journal: Journal, constats: Constats) -> list[tuple[Section, str]]:
    """SemVer, doublons, ordre décroissant, dates non croissantes vers le bas."""
    versions: list[tuple[Section, str]] = []
    vues: dict[str, int] = {}
    precedente: tuple[Section, tuple[Any, ...], date | None] | None = None
    for section in journal.sections:
        if section.est_unreleased:
            continue
        nom = normaliser_version(section.nom, section, constats)
        cle = cle_semver(nom)
        quand = controler_date(section, constats)
        controler_rubriques(section, constats)
        if cle is None:
            constats.erreur(section.ligne, f"« {section.nom} » n'est pas une version SemVer")
            continue
        if nom in vues:
            constats.erreur(section.ligne, f"version {nom} en double (déjà ligne {vues[nom]})")
        vues.setdefault(nom, section.ligne)
        controler_ordre(precedente, section, nom, cle, quand, constats)
        precedente = (section, cle, quand)
        versions.append((section, nom))
    return versions


def controler_ordre(precedente: tuple[Section, tuple[Any, ...], date | None] | None,
                    section: Section, nom: str, cle: tuple[Any, ...], quand: date | None,
                    constats: Constats) -> None:
    """La version doit précéder strictement celle du dessus ; sa date ne pas la dépasser."""
    if precedente is None:
        return
    dessus, cle_dessus, date_dessus = precedente
    if cle > cle_dessus:
        constats.erreur(section.ligne, f"{nom} est plus récente que {dessus.nom} placée "
                                       "au-dessus (ordre décroissant attendu)")
    if quand and date_dessus and quand > date_dessus:
        constats.erreur(section.ligne, f"{nom} datée du {quand} après {dessus.nom} "
                                       f"({date_dessus}) placée au-dessus")


def etiquette_vers_version(etiquette: str) -> str:
    """« v1.2.0 » → « 1.2.0 » pour comparer aux versions du journal."""
    return etiquette[1:] if re.match(r"^[vV]\d", etiquette) else etiquette


def controler_liens(journal: Journal, versions: list[tuple[Section, str]],
                    constats: Constats) -> int:
    """Définitions de liens : présentes, et plages de comparaison cohérentes."""
    controles = 0
    for cle in journal.liens_doubles:
        constats.avertir(journal.liens[cle][0], f"définition de lien [{cle}] en double")
    ordre = [nom for _, nom in versions]
    for section in journal.sections:
        if not section.entre_crochets:
            constats.avertir(section.ligne, f"{section.nom} sans crochets : pas de lien de "
                                            "comparaison")
            continue
        lien = journal.liens.get(section.nom.lower())
        if lien is None:
            constats.erreur(section.ligne, f"[{section.nom}] n'a pas de définition de lien "
                                           "en bas du journal (lien cassé)")
            continue
        controles += controler_plage(section, lien, ordre, constats)
    return controles


def controler_plage(section: Section, lien: tuple[int, str], ordre: list[str],
                    constats: Constats) -> int:
    """« compare/a...b » : b = cette version (HEAD pour Unreleased), a = la précédente."""
    trouve = MOTIF_COMPARE.search(lien[1])
    if not trouve:
        return 0
    a, b = etiquette_vers_version(trouve.group("a")), etiquette_vers_version(trouve.group("b"))
    if section.est_unreleased:
        attendu_b, attendu_a = REFERENCE_HEAD, (ordre[0] if ordre else None)
    else:
        nom = etiquette_vers_version(section.nom)
        rang = ordre.index(nom) if nom in ordre else None
        attendu_b = nom
        attendu_a = ordre[rang + 1] if rang is not None and rang + 1 < len(ordre) else None
    if b != attendu_b:
        constats.erreur(lien[0], f"lien [{section.nom}] compare jusqu'à « {trouve.group('b')} »"
                                 f" au lieu de {attendu_b}")
    if attendu_a is not None and a != attendu_a:
        constats.erreur(lien[0], f"lien [{section.nom}] part de « {trouve.group('a')} » au "
                                 f"lieu de la version précédente {attendu_a}")
    return 1


# ---------------------------------------------------------------------------
# Version déclarée
# ---------------------------------------------------------------------------

def version_du_manifeste(chemin: Path) -> str:
    """Version de pyproject.toml, package.json ou Cargo.toml."""
    texte = lire_texte(chemin)
    try:
        if chemin.name == "package.json":
            valeur = json.loads(texte).get("version")
        else:
            donnees = tomllib.loads(texte)
            valeur = (donnees.get("project", {}).get("version")
                      or donnees.get("tool", {}).get("poetry", {}).get("version")
                      or donnees.get("package", {}).get("version"))
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, AttributeError) as exc:
        raise ErreurEntree(f"manifeste illisible {chemin} : {exc}") from exc
    if not isinstance(valeur, str):
        raise ErreurEntree(f"aucune version statique dans {chemin} (version dynamique ?)")
    return valeur


def resoudre_version(valeur: str | None, racine: str | None) -> tuple[str | None, str | None]:
    """--version : numéro direct, ou chemin d'un manifeste → (version, origine)."""
    if valeur is None:
        return None, None
    chemin = Path(valeur)
    if not chemin.is_absolute() and racine:
        chemin = Path(racine) / chemin
    if chemin.name in MANIFESTES or chemin.suffix in (".toml", ".json"):
        if not chemin.is_file():
            raise ErreurEntree(f"manifeste introuvable : {chemin}")
        return version_du_manifeste(chemin), str(chemin)
    return valeur, "argument"


def controler_declaree(declaree: str | None, versions: list[tuple[Section, str]],
                       constats: Constats) -> dict[str, Any] | None:
    """La version déclarée figure-t-elle, et est-ce la plus récente publiée ?"""
    if declaree is None:
        return None
    propre = etiquette_vers_version(declaree)
    noms = [nom for _, nom in versions]
    if propre not in noms:
        constats.erreur(None, f"la version déclarée {propre} ne figure pas dans le journal")
        return {"version": propre, "presente": False, "plus_recente": False}
    plus_recente = noms[0] == propre
    if not plus_recente:
        constats.avertir(None, f"la version déclarée {propre} n'est pas la plus récente du "
                               f"journal ({noms[0]})")
    return {"version": propre, "presente": True, "plus_recente": plus_recente}


# ---------------------------------------------------------------------------
# Contrôle croisé optionnel
# ---------------------------------------------------------------------------

def controler_keepachangelog(texte: str, versions: list[tuple[Section, str]]) -> dict[str, Any] | None:
    """Versions vues par keepachangelog, comparées aux nôtres (doublons compris)."""
    if keepachangelog is None:
        return None
    try:
        lues = keepachangelog.to_dict(texte.splitlines(), show_unreleased=False)
    except (ValueError, KeyError, AttributeError, TypeError) as exc:
        return {"bibliotheque": "keepachangelog", "erreur": f"{type(exc).__name__}: {exc}"}
    siennes = [v for v, d in lues.items() if d.get("metadata", {}).get("release_date")]
    nos = [nom for _, nom in versions]
    return {"bibliotheque": "keepachangelog", "versions_lues": len(siennes),
            "concordance": sorted(siennes) == sorted(nos),
            "seulement_nous": sorted(set(nos) - set(siennes)),
            "seulement_keepachangelog": sorted(set(siennes) - set(nos)),
            "doublons_fusionnes": len(nos) - len(set(nos))}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def contrat() -> dict[str, str]:
    """Contrat de mesure extrait de la docstring."""
    texte = __doc__ or ""
    sections: dict[str, str] = {}
    for rang, titre in enumerate(TITRES_CONTRAT):
        suivants = "|".join(re.escape(t) for t in TITRES_CONTRAT[rang + 1:]) or r"\Z"
        trouve = re.search(rf"^{re.escape(titre)}\s*\n(.*?)(?=^(?:{suivants})\s*$|\Z)", texte,
                           re.MULTILINE | re.DOTALL)
        sections[titre] = " ".join(trouve.group(1).split()) if trouve else ""
    return sections


def charger(args: argparse.Namespace) -> tuple[str, str]:
    """(origine, texte) du journal."""
    if (args.chemin is None) == (args.texte is None):
        raise ErreurEntree("donner soit un fichier ou dossier, soit --texte")
    if args.texte is not None:
        return "<texte>", decoder_echappements(args.texte)
    chemin = Path(args.chemin)
    if not chemin.is_absolute() and args.racine:
        chemin = Path(args.racine) / chemin
    journal = trouver_journal(chemin)
    return str(journal), lire_texte(journal)


def verifier(origine: str, texte: str, declaree: str | None) -> dict[str, Any]:
    """Tous les contrôles ; rend le rapport."""
    constats = Constats()
    journal = lire_journal(texte, constats)
    if not journal.sections:
        raise ErreurEntree(f"aucune section de version (## [X.Y.Z] - date) dans {origine} : "
                           "dénominateur nul, rien à examiner", code=3)
    if journal.titre is None or journal.titre.lower() != "changelog":
        constats.avertir(1, "titre de niveau 1 « # Changelog » attendu")
    if not origine.startswith("<") and Path(origine).name != f"{CHANGELOG}.md":
        constats.avertir(None, f"Keep a Changelog nomme le fichier {CHANGELOG}.md")
    controler_unreleased(journal, constats)
    versions = controler_versions(journal, constats)
    liens = controler_liens(journal, versions, constats)
    return {
        "outil": "verifier_changelog", "moteur": "stdlib", "contrat": contrat(),
        "journal": origine, "denominateur": len(journal.sections),
        "examines": [s.brut[:80] for s in journal.sections[:MAX_EXAMINES]],
        "examines_tronques": len(journal.sections) > MAX_EXAMINES,
        "versions": [{"version": nom, "date": s.date, "ligne": s.ligne, "retiree": s.retiree,
                      "rubriques": [r for _, r in s.rubriques], "elements": s.elements}
                     for s, nom in versions],
        "unreleased": any(s.est_unreleased for s in journal.sections),
        "liens_definis": len(journal.liens), "plages_controlees": liens,
        "version_declaree": controler_declaree(declaree, versions, constats),
        "erreurs": constats.erreurs, "avertissements": constats.avertissements,
        "controle": controler_keepachangelog(texte, versions),
    }


# ---------------------------------------------------------------------------
# Affichage et interface
# ---------------------------------------------------------------------------

def afficher_humain(rapport: dict[str, Any]) -> None:
    """Résumé lisible."""
    print(f"{rapport['journal']} : {rapport['denominateur']} section(s), "
          f"{len(rapport['versions'])} version(s), Unreleased "
          f"{'présente' if rapport['unreleased'] else 'absente'}, "
          f"{rapport['plages_controlees']} lien(s) de comparaison contrôlé(s)")
    for e in rapport["erreurs"]:
        print(f"  ERREUR ligne {e['ligne'] or '-'} : {e['message']}")
    for a in rapport["avertissements"]:
        print(f"  attention ligne {a['ligne'] or '-'} : {a['message']}")
    declaree = rapport["version_declaree"]
    if declaree:
        print(f"Version déclarée {declaree['version']} : "
              f"{'présente' if declaree['presente'] else 'ABSENTE'}"
              f"{', la plus récente' if declaree['plus_recente'] else ''}")
    controle = rapport["controle"]
    if controle and "concordance" in controle:
        print(f"keepachangelog : {controle['versions_lues']} version(s) lue(s), concordance "
              f"{controle['concordance']}")
    print("COHÉRENT" if not rapport["erreurs"] else f"{len(rapport['erreurs'])} ERREUR(S)")


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Vérifie un CHANGELOG selon Keep a Changelog 1.1 : Unreleased, versions "
                    "SemVer décroissantes, dates ISO, rubriques, liens de comparaison, version "
                    "déclarée.",
        epilog="Exemple : python outils/verifier_changelog.py CHANGELOG.md --version "
               "pyproject.toml --json")
    parseur.add_argument("chemin", nargs="?", help="journal (fichier) ou dossier qui en contient un")
    parseur.add_argument("--texte", help="journal en ligne ; sans vrai saut de ligne, \\n et \\t "
                                         "sont interprétés")
    parseur.add_argument("--version", dest="version_declaree",
                         help="version déclarée (X.Y.Z) ou manifeste (pyproject.toml, "
                              "package.json, Cargo.toml) qui la porte")
    parseur.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def main() -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args()
    if keepachangelog is None:
        print("keepachangelog absent : vérification stdlib seule, sans contre-lecture par "
              "keepachangelog", file=sys.stderr)
    try:
        declaree, _ = resoudre_version(args.version_declaree, args.racine)
        origine, texte = charger(args)
        rapport = verifier(origine, texte, declaree)
    except ErreurEntree as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"outil": "verifier_changelog", "moteur": "stdlib",
                              "denominateur": 0, "examines": [], "erreur": str(exc)},
                             ensure_ascii=False))
        return exc.code
    if rapport["controle"]:
        rapport["moteur"] = "stdlib+keepachangelog"
    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        afficher_humain(rapport)
    return 1 if rapport["erreurs"] else 0


__all__ = ["lire_journal", "cle_semver", "verifier", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
