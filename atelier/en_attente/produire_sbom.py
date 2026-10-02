"""Produire la nomenclature logicielle (SBOM) CycloneDX 1.6 d'un projet ou d'un environnement Python.
Recopier les métadonnées ne suffit pas : mesuré dans cette session, sur 3 variantes d'un même
composant soumises au validateur officiel (cyclonedx-python-lib 11.12.0, JsonStrictValidator 1.6),
2 sont rejetées — le champ License de sortedcontainers 2.4.0 ('Apache 2.0') mis en license.id, et
une expression mêlée à une licence dans la même liste ; seule la forme license.name passe.

QUESTION
    De quels composants ce projet ou cet environnement est-il fait, au format CycloneDX ?
MESURE
    Inventaire depuis les distributions installées (--environnement : interpréteur courant ;
    --site <dossier> : ce dossier seul, lus par importlib.metadata), ou depuis un uv.lock, un
    pyproject.toml, des requirements*.txt (un dossier vaut son uv.lock s'il existe, sinon son
    pyproject.toml et ses requirements). Écrit dans --sortie, et nulle part ailleurs, un document
    CycloneDX 1.6 JSON : bomFormat, specVersion, serialNumber urn:uuid (uuid4), version 1,
    metadata.timestamp (UTC), metadata.tools, metadata.component (le projet s'il est connu),
    components (type library, bom-ref, name, version, purl pkg:pypi/<nom normalisé>@<version>,
    licenses, description), externalReferences distribution / source-distribution avec leurs
    empreintes SHA-256 quand uv.lock les donne, et le graphe dependencies. Licences : expression
    déclarée (License-Expression), identifiant si le champ License est exactement un identifiant
    connu, sinon nom déclaré ; jamais de licence devinée. Le document est relu par un contrôle
    interne (champs obligatoires, motifs, références) et, si cyclonedx-python-lib est
    importable, par son validateur JSON strict 1.6. Sur stdout : un résumé JSON.
HYPOTHÈSES
    Les métadonnées installées décrivent les distributions réellement présentes ; uv.lock est à
    jour ; une exigence non épinglée (sans ==) donne un composant sans version.
LIMITES
    Pas d'analyse des fichiers installés (empreintes du fichier record des .dist-info non
    reprises), pas de dépendances système ni de bibliothèques natives embarquées dans les roues.
    Les marqueurs d'environnement des Requires-Dist ne sont pas évalués (seules les dépendances
    d'extras sont écartées). Un requirements -r n'est pas suivi. Aucune vulnérabilité n'est
    recherchée.
CONTRE-EXEMPLES
    Constaté : sur un requirements.txt contenant « Arrow >= 1.0 », le composant sort sans
    version et avec le purl pkg:pypi/arrow sans @version, alors que 1.4.0 est installé : sans
    --environnement ni --site, l'outil ne sait pas ce qui sera réellement installé.
INVOCATION
    {outil} --environnement --sortie {dossier}/bom.cdx.json --json
DOMAINE
    Projets et environnements Python livrés en production ; SBOM transmis à un client, à un
    scanner de vulnérabilités ou à un registre de conformité.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
import uuid
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from cyclonedx.exception import MissingOptionalDependencyException
    from cyclonedx.schema import SchemaVersion
    from cyclonedx.validation.json import JsonStrictValidator
except ImportError:
    MissingOptionalDependencyException = None
    SchemaVersion = None
    JsonStrictValidator = None

RACINE = Path(__file__).resolve().parent

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
                  TITRE_INVOCATION, "DOMAINE")
FORMAT_BOM = "CycloneDX"
SIGLE_SBOM = "SBOM"
VERSION_SPEC = "1.6"
ALGO_SHA256 = "SHA-256"
UTC = "UTC"
NOM_OUTIL = "produire_sbom (Agent Foundry)"
FICHIER_PAR_DEFAUT = "bom.cdx.json"
DEPOT_PYPI = "https://pypi.org/simple"
MESSAGE_LIB_ABSENTE = ("cyclonedx-python-lib absent : SBOM relu par le contrôle interne (stdlib) "
                       "seulement, sans le validateur JSON officiel.")
MAX_EXAMINES = 200
MAX_REFERENCES = 50
MAX_TEXTE_LICENCE = 20_000
MAX_OCTETS_SOURCE = 20 * 1024 * 1024
TYPES_COMPOSANT = frozenset({"application", "framework", "library", "container", "platform",
                             "operating-system", "device", "device-driver", "firmware", "file",
                             "machine-learning-model", "data", "cryptographic-asset"})
# Identifiants vérifiés contre l'énumération spdx.schema.json fournie avec CycloneDX 1.6.
IDENTIFIANTS_SPDX = frozenset({
    "MIT", "MIT-0", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "ISC", "PSF-2.0", "Python-2.0",
    "Zlib", "Unlicense", "CC0-1.0", "BSL-1.0", "X11", "HPND", "NCSA", "UPL-1.0", "Unicode-3.0",
    "Unicode-DFS-2016", "curl", "Artistic-2.0", "BSD-3-Clause-Clear", "WTFPL", "Apache-2.0",
    "Apache-1.1", "BSD-4-Clause", "OpenSSL", "AFL-3.0", "MPL-2.0", "MPL-1.1", "EPL-1.0", "EPL-2.0",
    "CDDL-1.0", "CDDL-1.1", "EUPL-1.2", "LGPL-2.0-only", "LGPL-2.0-or-later", "LGPL-2.1-only",
    "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later", "GPL-2.0-only", "GPL-2.0-or-later",
    "GPL-3.0-only", "GPL-3.0-or-later", "AGPL-3.0-only", "AGPL-3.0-or-later", "SSPL-1.0",
    "BUSL-1.1", "Elastic-2.0", "CC-BY-4.0", "CC-BY-SA-4.0",
})
EXCEPTIONS_SPDX = frozenset({"Classpath-exception-2.0", "LLVM-exception", "GCC-exception-3.1",
                             "Autoconf-exception-3.0", "Bison-exception-2.2", "Font-exception-2.0",
                             "Linux-syscall-note", "Qt-LGPL-exception-1.1", "WxWindows-exception-3.1"})
CANONIQUE = MappingProxyType({i.lower(): i for i in IDENTIFIANTS_SPDX | EXCEPTIONS_SPDX})
MOTIF_SERIE = re.compile(r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MOTIF_PURL = re.compile(r"^pkg:pypi/[a-z0-9._-]+(?:@[^?#@]+)?(?:\?[^#]*)?$")
MOTIF_EMPREINTE = re.compile(r"^[a-fA-F0-9]{64}$")
MOTIF_NOM_REQUIS = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


class ErreurEntree(Exception):
    """Entrée invalide : source, sortie ou dossier --site (code 2)."""


# --------------------------------------------------------------------------- identité


def nom_purl(nom: str) -> str:
    """Nom PyPI selon la spécification purl : minuscules, « _ » remplacé par « - »."""
    return nom.lower().replace("_", "-")


def nom_normalise(nom: str) -> str:
    """Nom normalisé PEP 503, pour dédoublonner et apparier."""
    return re.sub(r"[-_.]+", "-", nom).lower()


def purl(nom: str, version: str | None, qualificatifs: dict[str, str] | None = None) -> str:
    """pkg:pypi/<nom>@<version encodée>[?qualificatifs triés]."""
    texte = f"pkg:pypi/{quote(nom_purl(nom), safe='.-')}"
    if version:
        texte += "@" + quote(version, safe=".-_~")
    if qualificatifs:
        texte += "?" + "&".join(f"{k}={quote(v, safe='')}" for k, v in sorted(qualificatifs.items()))
    return texte


# --------------------------------------------------------------------------- licences


def licence_par_expression(expression: str) -> list[dict[str, Any]]:
    """Une expression déclarée (License-Expression) : tuple d'un seul élément."""
    return [{"expression": expression, "acknowledgement": "declared"}]


def est_expression_connue(texte: str) -> bool:
    """Vrai si chaque mot est un opérateur ou un identifiant/exception connu."""
    mots = [m for m in re.split(r"[\s()]+", texte) if m]
    return bool(mots) and all(m in ("AND", "OR", "WITH") or m.lower() in CANONIQUE for m in mots)


def licences_depuis_meta(meta: Any) -> list[dict[str, Any]]:
    """Licences déclarées d'une distribution, sans inférence."""
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return licence_par_expression(expression)
    champ = (meta.get("License") or "").strip()
    if champ and champ.upper() != "UNKNOWN":
        return licence_depuis_champ(champ)
    noms = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or [])
            if c.startswith("License ::") and c.strip() != "License :: OSI Approved"]
    return [{"license": {"name": n, "acknowledgement": "declared"}} for n in dict.fromkeys(noms)]


def licence_depuis_champ(champ: str) -> list[dict[str, Any]]:
    """Champ License : identifiant exact, expression connue, nom court, ou texte intégral."""
    if champ.lower() in CANONIQUE:
        return [{"license": {"id": CANONIQUE[champ.lower()], "acknowledgement": "declared"}}]
    if "\n" not in champ and est_expression_connue(champ):
        return licence_par_expression(champ)
    if len(champ) <= 120 and "\n" not in champ:
        return [{"license": {"name": champ, "acknowledgement": "declared"}}]
    return [{"license": {"name": "texte de licence intégral déclaré", "acknowledgement": "declared",
                         "text": {"content": champ[:MAX_TEXTE_LICENCE], "contentType": "text/plain"}}}]


# --------------------------------------------------------------------------- environnement


def dependances_declarees(meta: Any) -> list[str]:
    """Noms des Requires-Dist hors extras (marqueurs non évalués)."""
    noms = []
    for exigence in meta.get_all("Requires-Dist") or []:
        exigence_seule, _, marqueur = exigence.partition(";")
        if "extra" in marqueur:
            continue
        m = MOTIF_NOM_REQUIS.match(exigence_seule)
        if m:
            noms.append(m.group(1))
    return noms


def composants_environnement(sites: list[Path] | None) -> list[dict[str, Any]]:
    """Une fiche par distribution installée (première occurrence d'un nom)."""
    trouvees = metadata.distributions(path=[str(s) for s in sites]) if sites else \
        metadata.distributions()
    fiches: dict[str, dict[str, Any]] = {}
    for dist in trouvees:
        meta = dist.metadata
        nom = meta.get("Name")
        if not nom or nom_normalise(nom) in fiches:
            continue
        fiches[nom_normalise(nom)] = {
            "nom": nom, "version": meta.get("Version"), "licences": licences_depuis_meta(meta),
            "description": (meta.get("Summary") or "").strip() or None,
            "depend_de": dependances_declarees(meta), "references": [], "qualificatifs": {},
        }
    return list(fiches.values())


# --------------------------------------------------------------------------- fichiers


def lire_texte(chemin: Path) -> str:
    """Lecture UTF-8 bornée, binaire refusé."""
    if chemin.stat().st_size > MAX_OCTETS_SOURCE:
        raise ErreurEntree(f"{chemin.name} : plus de {MAX_OCTETS_SOURCE} octets")
    try:
        texte = chemin.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError) as exc:
        raise ErreurEntree(f"{chemin.name} : illisible en UTF-8 ({exc})") from exc
    if "\x00" in texte:
        raise ErreurEntree(f"{chemin.name} : octets NUL, fichier binaire refusé")
    return texte


def lire_toml(chemin: Path) -> dict[str, Any]:
    """TOML analysé ; erreur de syntaxe en ErreurEntree."""
    try:
        return tomllib.loads(lire_texte(chemin))
    except (tomllib.TOMLDecodeError, RecursionError) as exc:
        raise ErreurEntree(f"{chemin.name} : TOML invalide ({exc})") from exc


def fiche_exigence(exigence: str) -> dict[str, Any] | None:
    """Exigence PEP 508 → fiche (version seulement si épinglée par ==)."""
    m = MOTIF_NOM_REQUIS.match(exigence)
    if m is None:
        return None
    epingle = re.search(r"===?\s*([A-Za-z0-9.!+_-]+)\s*$", exigence.split(";")[0].strip())
    version = epingle.group(1) if epingle and not epingle.group(1).endswith("*") else None
    return {"nom": m.group(1), "version": version, "licences": [], "description": None,
            "depend_de": [], "references": [], "qualificatifs": {}}


def lire_requirements(chemin: Path, notes: list[str]) -> list[dict[str, Any]]:
    """requirements.txt : exigences simples ; options, URL et -r notés, non suivis."""
    fiches = []
    for ligne in re.sub(r"\\\r?\n", " ", lire_texte(chemin)).splitlines():
        ligne = re.split(r"(?:^|\s)#", ligne, maxsplit=1)[0].strip()
        if not ligne:
            continue
        if ligne.startswith("-") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://|^[./~]", ligne):
            notes.append(f"{chemin.name} : ligne non suivie « {ligne[:80]} »")
            continue
        fiche = fiche_exigence(ligne)
        if fiche:
            fiches.append(fiche)
    return fiches


def lire_pyproject(chemin: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """pyproject.toml : dépendances directes et fiche du projet lui-même."""
    projet = lire_toml(chemin).get("project", {})
    exigences = list(projet.get("dependencies") or [])
    for liste in (projet.get("optional-dependencies") or {}).values():
        exigences += liste
    fiches = [f for e in exigences if isinstance(e, str) and (f := fiche_exigence(e))]
    if not projet.get("name"):
        return fiches, None
    licence = projet.get("license")
    licences = licence_par_expression(licence) if isinstance(licence, str) else []
    racine = {"nom": projet["name"], "version": projet.get("version"), "licences": licences,
              "description": projet.get("description"), "references": [], "qualificatifs": {},
              "depend_de": [f["nom"] for f in fiches]}
    return fiches, racine


def references_uv(paquet: dict[str, Any]) -> list[dict[str, Any]]:
    """Archives source et roues de uv.lock, avec leur empreinte SHA-256."""
    archives = []
    sdist = paquet.get("sdist")
    if isinstance(sdist, dict) and sdist.get("url"):
        archives.append(("source-distribution", sdist))
    archives += [("distribution", r) for r in paquet.get("wheels", []) if r.get("url")]
    references = []
    for genre, archive in archives[:MAX_REFERENCES]:
        reference: dict[str, Any] = {"type": genre, "url": archive["url"]}
        algo, _, valeur = str(archive.get("hash", "")).partition(":")
        if algo == "sha256" and MOTIF_EMPREINTE.match(valeur):
            reference["hashes"] = [{"alg": ALGO_SHA256, "content": valeur}]
        references.append(reference)
    return references


def qualificatifs_uv(origine: dict[str, Any]) -> dict[str, str] | None:
    """Qualificatifs purl d'une origine uv ; None pour un chemin local (pas de purl)."""
    if "registry" in origine:
        return {} if origine["registry"].rstrip("/") == DEPOT_PYPI else \
            {"repository_url": origine["registry"]}
    if "git" in origine:
        return {"vcs_url": "git+" + origine["git"]}
    if "url" in origine:
        return {"download_url": origine["url"]}
    return None


def lire_uv_lock(chemin: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """uv.lock : paquets verrouillés, empreintes, graphe ; le projet (éditable/virtuel) à part."""
    fiches, racine = [], None
    for paquet in lire_toml(chemin).get("package", []):
        origine = paquet.get("source", {})
        fiche = {"nom": paquet.get("name", "?"), "version": paquet.get("version"), "licences": [],
                 "description": None, "references": references_uv(paquet),
                 "depend_de": [d.get("name") for d in paquet.get("dependencies", []) if d.get("name")],
                 "qualificatifs": qualificatifs_uv(origine)}
        if "editable" in origine or "virtual" in origine:
            racine = fiche
        else:
            fiches.append(fiche)
    return fiches, racine


def choisir_fichiers(chemin: Path) -> list[Path]:
    """Un dossier vaut son uv.lock (+ pyproject.toml), sinon pyproject.toml et requirements."""
    if not chemin.is_dir():
        return [chemin]
    uv_lock, pyproject = chemin / "uv.lock", chemin / "pyproject.toml"
    if uv_lock.is_file():
        return [uv_lock] + ([pyproject] if pyproject.is_file() else [])
    exigences = sorted(p for p in chemin.iterdir() if p.is_file() and est_requirements(p))
    return ([pyproject] if pyproject.is_file() else []) + exigences


def est_requirements(chemin: Path) -> bool:
    """requirements*.txt / *.in, constraints*.txt."""
    nom = chemin.name.lower()
    return nom.endswith((".txt", ".in")) and ("requirement" in nom or nom.startswith("constraint"))


def fusionner_racines(racine: dict[str, Any] | None, projet: dict[str, Any] | None
                     ) -> dict[str, Any] | None:
    """Garde la première fiche projet, complétée par la suivante (licence, description, version)."""
    if racine is None or projet is None:
        return racine or projet
    for cle in ("version", "description", "licences"):
        racine[cle] = racine[cle] or projet[cle]
    return racine


def lire_fichier_source(fichier: Path, lock_lu: bool, notes: list[str]
                        ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Lit un fichier source selon son nom ; un pyproject.toml après uv.lock ne sert qu'au projet."""
    nom = fichier.name.lower()
    if nom == "uv.lock":
        return lire_uv_lock(fichier)
    if nom == "pyproject.toml":
        trouvees, projet = lire_pyproject(fichier)
        return ([] if lock_lu else trouvees), projet
    if est_requirements(fichier):
        return lire_requirements(fichier, notes), None
    raise ErreurEntree(f"{fichier.name} : ni uv.lock, ni pyproject.toml, ni requirements*.txt")


def lire_sources(chemins: list[Path], notes: list[str]
                 ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    """Fiches des composants, fiche du projet, fichiers lus."""
    fiches: list[dict[str, Any]] = []
    racine, lus, lock_lu = None, [], False
    for chemin in chemins:
        if not chemin.exists():
            raise ErreurEntree(f"chemin introuvable : {chemin}")
        for fichier in choisir_fichiers(chemin):
            trouvees, projet = lire_fichier_source(fichier, lock_lu, notes)
            lock_lu = lock_lu or fichier.name.lower() == "uv.lock"
            fiches += trouvees
            racine = fusionner_racines(racine, projet)
            lus.append(str(fichier))
    return fiches, racine, lus


# --------------------------------------------------------------------------- assemblage


def enrichir(fiches: list[dict[str, Any]], installees: list[dict[str, Any]], notes: list[str]) -> None:
    """Complète version et licences d'après les distributions installées (si consultées)."""
    index = {nom_normalise(f["nom"]): f for f in installees}
    for fiche in fiches:
        trouvee = index.get(nom_normalise(fiche["nom"]))
        if trouvee is None:
            continue
        if fiche["version"] and trouvee["version"] and fiche["version"] != trouvee["version"]:
            notes.append(f"{fiche['nom']} : {fiche['version']} demandée, {trouvee['version']} "
                         "installée (licence lue sur l'installée)")
        fiche["version"] = fiche["version"] or trouvee["version"]
        fiche["licences"] = fiche["licences"] or trouvee["licences"]
        fiche["description"] = fiche["description"] or trouvee["description"]


def dedoublonner(fiches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Une fiche par (nom normalisé, version), dans l'ordre d'apparition."""
    vues: dict[tuple[str, str | None], dict[str, Any]] = {}
    for fiche in fiches:
        vues.setdefault((nom_normalise(fiche["nom"]), fiche["version"]), fiche)
    return list(vues.values())


def composant(fiche: dict[str, Any], genre: str, refs: set[str]) -> dict[str, Any]:
    """Fiche → composant CycloneDX (bom-ref unique)."""
    qualificatifs = fiche.get("qualificatifs")
    identifiant = purl(fiche["nom"], fiche["version"], qualificatifs) if qualificatifs is not None \
        else None
    ref, rang = identifiant or f"local:{nom_normalise(fiche['nom'])}@{fiche['version'] or ''}", 2
    base_ref = ref
    while ref in refs:
        ref, rang = f"{base_ref}#{rang}", rang + 1
    refs.add(ref)
    sortie: dict[str, Any] = {"type": genre, "bom-ref": ref, "name": fiche["nom"]}
    if fiche["version"]:
        sortie["version"] = fiche["version"]
    if fiche.get("description"):
        sortie["description"] = fiche["description"][:1000]
    if fiche["licences"]:
        sortie["licenses"] = fiche["licences"]
    if identifiant:
        sortie["purl"] = identifiant
    if fiche["references"]:
        sortie["externalReferences"] = fiche["references"]
    return sortie


def graphe(fiches: list[dict[str, Any]], composants: list[dict[str, Any]],
           racine: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Graphe dependencies : chaque composant y figure, arêtes vers les composants connus."""
    par_nom: dict[str, str] = {}
    for fiche, comp in zip(fiches, composants):
        par_nom.setdefault(nom_normalise(fiche["nom"]), comp["bom-ref"])
    noeuds = list(zip(fiches, composants)) + ([(racine, racine["_composant"])] if racine else [])
    resultat = []
    for fiche, comp in noeuds:
        cibles = [par_nom[n] for n in dict.fromkeys(nom_normalise(d) for d in fiche["depend_de"])
                  if n in par_nom and par_nom[n] != comp["bom-ref"]]
        resultat.append({"ref": comp["bom-ref"], "dependsOn": cibles})
    return resultat


def assembler(fiches: list[dict[str, Any]], racine: dict[str, Any] | None) -> dict[str, Any]:
    """Document CycloneDX 1.6 complet."""
    refs: set[str] = set()
    meta: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tools": {"components": [{"type": "application", "name": NOM_OUTIL}]},
    }
    if racine is not None:
        racine["_composant"] = composant(racine, "application", refs)
        meta["component"] = racine["_composant"]
    composants = [composant(f, "library", refs) for f in fiches]
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": FORMAT_BOM, "specVersion": VERSION_SPEC,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1, "metadata": meta,
        "components": composants, "dependencies": graphe(fiches, composants, racine),
    }


# --------------------------------------------------------------------------- validation


def controler_licences(nom: str, licences: Any) -> list[str]:
    """licenseChoice : soit une seule expression, soit des licences id/name exclusives."""
    if not isinstance(licences, list) or not licences:
        return [f"{nom} : licenses doit être une liste non vide"]
    if any("expression" in l for l in licences):
        return [] if len(licences) == 1 and isinstance(licences[0].get("expression"), str) else \
            [f"{nom} : une expression doit être seule dans licenses"]
    erreurs = []
    for l in licences:
        corps = l.get("license", {})
        if ("id" in corps) == ("name" in corps):
            erreurs.append(f"{nom} : licence avec id ET name, ou ni l'un ni l'autre")
        elif "id" in corps and corps["id"] not in IDENTIFIANTS_SPDX | EXCEPTIONS_SPDX:
            erreurs.append(f"{nom} : identifiant hors liste {corps['id']}")
    return erreurs


def controler_composant(comp: dict[str, Any]) -> list[str]:
    """Champs obligatoires et motifs d'un composant."""
    nom = comp.get("name") or "?"
    erreurs = [] if comp.get("type") in TYPES_COMPOSANT and comp.get("name") else \
        [f"{nom} : type ou name manquant"]
    if "purl" in comp and not MOTIF_PURL.match(comp["purl"]):
        erreurs.append(f"{nom} : purl mal formé {comp['purl']}")
    if "licenses" in comp:
        erreurs += controler_licences(nom, comp["licenses"])
    for reference in comp.get("externalReferences", []):
        for empreinte in reference.get("hashes", []):
            if empreinte.get("alg") != ALGO_SHA256 or not MOTIF_EMPREINTE.match(empreinte.get("content", "")):
                erreurs.append(f"{nom} : empreinte invalide")
    return erreurs


def controle_interne(bom: dict[str, Any]) -> list[str]:
    """Relecture stdlib : en-tête, composants, unicité des bom-ref, références du graphe."""
    erreurs = []
    if bom.get("bomFormat") != FORMAT_BOM or bom.get("specVersion") != VERSION_SPEC:
        erreurs.append("bomFormat ou specVersion incorrect")
    if not MOTIF_SERIE.match(bom.get("serialNumber", "")):
        erreurs.append("serialNumber hors motif urn:uuid")
    if not isinstance(bom.get("version"), int) or bom["version"] < 1:
        erreurs.append("version doit être un entier >= 1")
    try:
        datetime.fromisoformat(bom["metadata"]["timestamp"])
    except (KeyError, ValueError):
        erreurs.append("metadata.timestamp absent ou non ISO 8601")
    tous = bom["components"] + ([bom["metadata"]["component"]] if "component" in bom["metadata"] else [])
    for comp in tous:
        erreurs += controler_composant(comp)
    refs = [c["bom-ref"] for c in tous]
    if len(refs) != len(set(refs)):
        erreurs.append("bom-ref en double")
    connus = set(refs)
    for dep in bom["dependencies"]:
        manquants = [r for r in [dep["ref"], *dep["dependsOn"]] if r not in connus]
        if manquants:
            erreurs.append(f"dependencies : référence inconnue {manquants[0]}")
    return erreurs


def validation_officielle(texte: str) -> dict[str, Any]:
    """Validateur JSON strict CycloneDX 1.6 de cyclonedx-python-lib, s'il est importable."""
    if JsonStrictValidator is None or SchemaVersion is None:
        return {"effectuee": False, "raison": "cyclonedx-python-lib absent"}
    assert MissingOptionalDependencyException is not None
    try:
        erreur = JsonStrictValidator(SchemaVersion.V1_6).validate_str(texte)
    except MissingOptionalDependencyException as exc:
        return {"effectuee": False, "raison": f"cyclonedx-python-lib sans jsonschema : {exc}"}
    try:
        version = metadata.version("cyclonedx-python-lib")
    except metadata.PackageNotFoundError:
        version = "?"
    return {"effectuee": True, "validateur": f"cyclonedx-python-lib {version} (JsonStrictValidator 1.6)",
            "valide": erreur is None, "erreur": None if erreur is None else str(erreur)[:2000]}


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
        description=f"Produit un {SIGLE_SBOM} {FORMAT_BOM} {VERSION_SPEC} JSON (fichier --sortie) depuis "
                    "l'environnement Python ou un uv.lock / pyproject.toml / requirements.txt.",
        epilog=f"Exemple : python {RACINE.name}/produire_sbom.py . --environnement "
               "--sortie bom.cdx.json --json")
    p.add_argument("sources", nargs="*", help="uv.lock, pyproject.toml, requirements*.txt, ou dossier")
    p.add_argument("--environnement", action="store_true",
                   help="inventorier (ou enrichir) avec les distributions de l'interpréteur courant")
    p.add_argument("--site", action="append", metavar="DOSSIER",
                   help="inventorier (ou enrichir) avec les distributions de ce dossier (répétable)")
    p.add_argument("--sortie", required=True, metavar="FICHIER",
                   help=f"fichier SBOM à écrire (un dossier reçoit {FICHIER_PAR_DEFAUT})")
    p.add_argument("--nom-projet", help="nom du composant racine (metadata.component)")
    p.add_argument("--version-projet", help="version du composant racine")
    p.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : courant)")
    p.add_argument("--json", action="store_true", help="résumé en un seul objet JSON sur stdout")
    return p


def chemin_sortie(texte: str, base: Path | None, sources: list[Path]) -> Path:
    """Fichier de sortie : dossier → bom.cdx.json dedans ; jamais une des sources."""
    sortie = base / texte if base is not None and not Path(texte).is_absolute() else Path(texte)
    if sortie.is_dir():
        sortie = sortie / FICHIER_PAR_DEFAUT
    if not sortie.parent.is_dir():
        raise ErreurEntree(f"--sortie : dossier parent introuvable : {sortie.parent}")
    if any(s.exists() and sortie.resolve() == s.resolve() for s in sources):
        raise ErreurEntree("--sortie désigne une des sources : refus d'écraser une entrée")
    return sortie


def inventaire(args: argparse.Namespace, notes: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str], list[Path]]:
    """Fiches, projet racine, fichiers lus, chemins sources résolus."""
    base = Path(args.racine) if args.racine else None
    sources = [base / s if base is not None and not Path(s).is_absolute() else Path(s) for s in args.sources]
    sites = [Path(s) for s in args.site] if args.site else None
    for site in sites or []:
        if not site.is_dir():
            raise ErreurEntree(f"--site : dossier introuvable : {site}")
    fiches, racine, lus = lire_sources(sources, notes)
    installees = composants_environnement(sites) if (args.environnement or sites) else []
    if sources:
        enrichir(fiches, installees, notes)
    else:
        fiches = installees
        lus.append("environnement : " + (", ".join(map(str, sites)) if sites else "interpréteur courant"))
    if args.nom_projet:
        racine = {"nom": args.nom_projet, "version": args.version_projet, "licences": [],
                  "description": None, "references": [], "qualificatifs": {},
                  "depend_de": racine["depend_de"] if racine else [f["nom"] for f in fiches]}
    return dedoublonner(fiches), racine, lus, sources


def resume(bom: dict[str, Any], sortie: Path, lus: list[str], notes: list[str],
           erreurs: list[str], officielle: dict[str, Any]) -> dict[str, Any]:
    """Résumé JSON pour stdout."""
    noms = [f"{c['name']}@{c.get('version', '?')}" for c in bom["components"]]
    return {
        "denominateur": len(noms), "examines": noms[:MAX_EXAMINES],
        "examines_tronques": len(noms) > MAX_EXAMINES,
        "moteur": "cyclonedx-python-lib" if officielle["effectuee"] else "stdlib",
        "sortie": str(sortie), "format": f"{FORMAT_BOM} {VERSION_SPEC} JSON",
        "serialNumber": bom["serialNumber"], "sources_lues": lus,
        "projet": bom["metadata"].get("component", {}).get("name"),
        "avec_version": sum("version" in c for c in bom["components"]),
        "avec_licence": sum("licenses" in c for c in bom["components"]),
        "sans_licence": [c["name"] for c in bom["components"] if "licenses" not in c][:MAX_EXAMINES],
        "aretes": sum(len(d["dependsOn"]) for d in bom["dependencies"]),
        "controle_interne": {"erreurs": erreurs, "valide": not erreurs},
        "validation_officielle": officielle, "notes": notes,
    }


def afficher_humain(sommaire: dict[str, Any]) -> None:
    """Sortie lisible."""
    print(f"{SIGLE_SBOM} {sommaire['format']} écrit : {sommaire['sortie']}")
    print(f"  composants : {sommaire['denominateur']} (avec version {sommaire['avec_version']}, "
          f"avec licence {sommaire['avec_licence']}) ; arêtes du graphe : {sommaire['aretes']}")
    print(f"  contrôle interne : {'valide' if sommaire['controle_interne']['valide'] else sommaire['controle_interne']['erreurs'][:5]}")
    officielle = sommaire["validation_officielle"]
    print(f"  validation officielle : {officielle.get('validateur', officielle.get('raison'))}"
          f"{'' if not officielle['effectuee'] else (' → valide' if officielle['valide'] else ' → INVALIDE : ' + str(officielle['erreur'])[:300])}")
    for note in sommaire["notes"][:10]:
        print(f"  note : {note}")


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


def afficher_resultat(sommaire: dict[str, Any], en_json: bool) -> None:
    """Écrit le résumé (JSON ou lisible) ; un tube fermé par le lecteur n'est pas une erreur."""
    try:
        if en_json:
            print(json.dumps(sommaire, ensure_ascii=False, indent=2))
        else:
            afficher_humain(sommaire)
        sys.stdout.flush()
    except BrokenPipeError:
        neutraliser_sortie()


def ecrire_et_valider(bom: dict[str, Any], sortie: Path) -> tuple[list[str], dict[str, Any]]:
    """Contrôle interne, validation officielle si possible, puis écriture dans --sortie."""
    texte = json.dumps(bom, ensure_ascii=False, indent=2)
    erreurs = controle_interne(bom)
    if JsonStrictValidator is None:
        print(MESSAGE_LIB_ABSENTE, file=sys.stderr)
    officielle = validation_officielle(texte)
    try:
        sortie.write_text(texte + "\n", encoding="utf-8")
    except OSError as exc:
        raise ErreurEntree(f"écriture impossible dans {sortie} ({exc})") from exc
    return erreurs, officielle


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    notes: list[str] = []
    try:
        fiches, racine, lus, sources = inventaire(args, notes)
        sortie = chemin_sortie(args.sortie, Path(args.racine) if args.racine else None, sources)
        if not fiches:
            return afficher_refus("dénominateur nul : rien à examiner (aucun composant ; donnez une "
                                  "source, --environnement ou --site). Aucun fichier écrit.", 3,
                                  args.json)
        bom = assembler(fiches, racine)
        erreurs, officielle = ecrire_et_valider(bom, sortie)
    except ErreurEntree as exc:
        return afficher_refus(f"entrée invalide : {exc}", 2, args.json)
    sommaire = resume(bom, sortie, lus, notes, erreurs, officielle)
    sommaire["contrat"] = extraire_contrat(__doc__ or "")
    code = 1 if erreurs or (officielle["effectuee"] and not officielle["valide"]) else 0
    if code:
        print(f"défaut : le {SIGLE_SBOM} écrit ne passe pas la validation (voir le résumé).",
              file=sys.stderr)
    afficher_resultat(sommaire, args.json)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
