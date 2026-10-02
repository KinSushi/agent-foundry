"""Dire où une application doit ranger configuration, cache, données, état et journaux, sur ce système.

Mesuré dans cette session (conteneur Linux, compte root) : XDG_RUNTIME_DIR n'est pas défini et
/run/user/0 n'existe pas ; platformdirs 4.12.2 rend alors /tmp/runtime-0/monapp avec un
avertissement RuntimeDirWarning, alors que la spécification XDG ne fixe aucun défaut pour ce
dossier ; le dossier d'état par défaut (~/.local/state) n'existe pas non plus. Un agent qui
écrit « ~/.config/monapp » en dur ignore XDG_CONFIG_HOME, et ne sait pas si l'écriture passera.

QUESTION
    Où cette application doit-elle ranger sa configuration, son cache, ses données, son
    état, ses journaux et ses fichiers d'exécution sur ce système, et chacun de ces dossiers
    existe-t-il, est-il inscriptible ou peut-il être créé ?
MESURE
    Linux et autres Unix : spécification XDG Base Directory (défauts confrontés aux extraits
    de specifications.freedesktop.org/basedir/latest) : XDG_CONFIG_HOME (défaut ~/.config),
    XDG_DATA_HOME (~/.local/share), XDG_STATE_HOME (~/.local/state), XDG_CACHE_HOME
    (~/.cache), XDG_RUNTIME_DIR (sans défaut, propriétaire seul, mode 0700), et les chemins
    de recherche XDG_CONFIG_DIRS (/etc/xdg) et XDG_DATA_DIRS (/usr/local/share/:/usr/share/) ;
    une valeur relative est invalide et ignorée. macOS : ~/Library/Application Support,
    ~/Library/Caches, ~/Library/Logs. Windows : LOCALAPPDATA (ou APPDATA avec --itinerant),
    PROGRAMDATA, à défaut USERPROFILE. Pour chaque dossier : existence, nature, droit
    d'écriture (os.access), sinon premier ancêtre existant et possibilité de le créer.
    Rien n'est jamais créé ni ouvert : seulement stat et access. Comparaison facultative
    avec platformdirs.
HYPOTHÈSES
    Les variables d'environnement du processus sont celles de l'application visée ; os.access
    reflète les droits réels (vrai pour les droits Unix classiques). Le dossier personnel
    vient de HOME (USERPROFILE sous Windows) ou de --racine, jamais d'un fichier système.
LIMITES
    os.access ignore les listes de contrôle d'accès fines, les quotas et un disque plein ; un
    dossier « créable » peut encore échouer à la création ; sous le compte root, il répond
    oui presque partout. Les journaux n'ont pas de variable XDG : la convention retenue
    (comme platformdirs) est le dossier d'état suivi de log. Sur un système simulé
    (--systeme), existence et droits ne sont pas vérifiables. Les dossiers
    utilisateur (Documents, Téléchargements) ne sont pas traités : ils exigeraient de lire
    ~/.config/user-dirs.dirs.
CONTRE-EXEMPLES
    Constaté en instanciant ici les classes de platformdirs 4.12.2 : sa classe macOS, avec
    XDG_CONFIG_HOME=/srv/conf, rend /srv/conf/monapp, alors que cet outil répond
    ~/Library/Application Support/monapp : l'outil se trompe donc pour qui a exporté les
    variables XDG sur son Mac et s'attend à ce qu'elles soient suivies. Sa classe Windows sans auteur répète le nom
    (LOCALAPPDATA/monapp/monapp) : l'outil compare avec appauthor=False. Un dossier
    XDG_RUNTIME_DIR en mode 0755 est signalé ici comme défaut, platformdirs l'écarte et
    se replie sur /tmp/runtime-0.
INVOCATION
    {outil} monapp --json
DOMAINE
    Choix des chemins d'écriture d'une application ou d'un script (configuration, cache,
    données, état, journaux) avant de les écrire en dur, sous Linux, macOS ou Windows.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

try:
    import platformdirs
except ImportError:
    platformdirs = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", INTITULE_INVOCATION, "DOMAINE")

VAR_HOME = "HOME"
VAR_CONFIG = "XDG_CONFIG_HOME"
VAR_DATA = "XDG_DATA_HOME"
VAR_STATE = "XDG_STATE_HOME"
VAR_CACHE = "XDG_CACHE_HOME"
VAR_RUNTIME = "XDG_RUNTIME_DIR"
VAR_CONFIG_DIRS = "XDG_CONFIG_DIRS"
VAR_DATA_DIRS = "XDG_DATA_DIRS"
VAR_APPDATA = "APPDATA"
VAR_LOCALAPPDATA = "LOCALAPPDATA"
VAR_PROGRAMDATA = "PROGRAMDATA"
VAR_USERPROFILE = "USERPROFILE"
XDG_DEFAUTS = {VAR_CONFIG: (".config",), VAR_DATA: (".local", "share"),
               VAR_STATE: (".local", "state"), VAR_CACHE: (".cache",)}
XDG_CONFIG_DIRS_DEFAUT = ("/etc/xdg",)
XDG_DATA_DIRS_DEFAUT = ("/usr/local/share/", "/usr/share/")
MACOS_SITE = ("/Library/Application Support",)
WINDOWS_DEFAUTS = {VAR_APPDATA: ("AppData", "Roaming"), VAR_LOCALAPPDATA: ("AppData", "Local")}
GENRES_ECRITURE = ("config", "donnees", "etat", "cache", "journaux")
CORRESPONDANCE_PLATFORMDIRS = {"config": "user_config_dir", "donnees": "user_data_dir",
                               "etat": "user_state_dir", "cache": "user_cache_dir",
                               "journaux": "user_log_dir", "execution": "user_runtime_dir"}
SYSTEMES = ("linux", "macos", "windows")
NOM_OUTIL = "localiser_dossiers"
ENCODAGE = "utf-8"


class EntreeInvalide(Exception):
    """Nom d'application ou option inutilisable : code 2."""


@dataclass(frozen=True)
class Demande:
    """Ce qu'on cherche à localiser, et dans quel contexte."""

    application: str
    auteur: str | None
    version: str | None
    systeme: str
    itinerant: bool
    environnement: dict[str, str]
    personnel: str | None
    simule: bool
    personnel_force: bool = False


def systeme_hote() -> str:
    """linux (et autres Unix), macos ou windows."""
    if sys.platform == "darwin":
        return "macos"
    return "windows" if os.name == "nt" else "linux"


def classe_chemin(systeme: str) -> type[PurePath]:
    """Chemins Windows ou POSIX selon le système visé (même simulé)."""
    return PureWindowsPath if systeme == "windows" else PurePosixPath


def valider_nom(nom: str, champ: str) -> str:
    """Un composant de chemin unique : pas de séparateur, pas de « . » ni « .. », pas de NUL."""
    if not nom or nom in (".", "..") or any(c in nom for c in "/\\\x00") or nom != nom.strip():
        raise EntreeInvalide(f"{champ} {nom!r} : un seul composant de chemin, sans / ni \\")
    try:
        nom.encode(ENCODAGE)
    except UnicodeEncodeError as exc:
        raise EntreeInvalide(f"{champ} {nom!r} : octets non UTF-8") from exc
    return nom


def variable_absolue(demande: Demande, nom: str) -> tuple[str | None, str | None]:
    """Valeur d'une variable si elle est définie, non vide et absolue ; sinon (None, raison)."""
    valeur = demande.environnement.get(nom)
    if not valeur:
        return None, "non définie"
    if not classe_chemin(demande.systeme)(valeur).is_absolute():
        return None, f"relative ({valeur!r}) : invalide, ignorée"
    return valeur, None


def suffixes(demande: Demande, *intermediaires: str) -> list[str]:
    """[auteur] application [intermédiaires] [version], selon le système."""
    parties = [demande.auteur] if demande.auteur and demande.systeme == "windows" else []
    parties.append(demande.application)
    parties += list(intermediaires)
    return parties + ([demande.version] if demande.version else [])


def entree(genre: str, chemin: PurePath | None, source: str, note: str | None = None) -> dict[str, Any]:
    return {"genre": genre, "chemin": None if chemin is None else str(chemin), "source": source,
            "note": note}


def dossiers_xdg(demande: Demande) -> list[dict[str, Any]]:
    """Linux et Unix : dossiers d'écriture selon la spécification XDG Base Directory."""
    chemin = classe_chemin(demande.systeme)
    sortie = []
    bases: dict[str, PurePath | None] = {}
    for genre, variable in (("config", VAR_CONFIG), ("donnees", VAR_DATA),
                            ("etat", VAR_STATE), ("cache", VAR_CACHE)):
        valeur, raison = variable_absolue(demande, variable)
        if valeur is not None:
            bases[genre], source = chemin(valeur), variable
        else:
            personnel = demande.personnel
            bases[genre] = chemin(personnel, *XDG_DEFAUTS[variable]) if personnel else None
            source = f"défaut ({variable} {raison})"
        cible = None if bases[genre] is None else bases[genre].joinpath(*suffixes(demande))
        sortie.append(entree(genre, cible, source))
    etat = sortie[2]["chemin"]
    sortie.append(entree("journaux", chemin(etat, "log") if etat else None,
                         "convention : dossier d'état + log (pas de variable XDG)"))
    sortie.append(dossier_execution_xdg(demande))
    return sortie


def dossier_execution_xdg(demande: Demande) -> dict[str, Any]:
    """XDG_RUNTIME_DIR : aucun défaut dans la spécification, l'application doit se replier et avertir."""
    valeur, raison = variable_absolue(demande, VAR_RUNTIME)
    if valeur is None:
        return entree("execution", None, f"{VAR_RUNTIME} {raison}",
                      "aucun défaut prévu : se replier sur un dossier équivalent et avertir")
    return entree("execution", classe_chemin(demande.systeme)(valeur).joinpath(*suffixes(demande)),
                  VAR_RUNTIME)


def recherche_xdg(demande: Demande) -> list[dict[str, Any]]:
    """Chemins de recherche en lecture (configuration et données fournies par le système)."""
    chemin = classe_chemin(demande.systeme)
    sortie = []
    for genre, variable, defaut in (("recherche_config", VAR_CONFIG_DIRS, XDG_CONFIG_DIRS_DEFAUT),
                                    ("recherche_donnees", VAR_DATA_DIRS, XDG_DATA_DIRS_DEFAUT)):
        brut = demande.environnement.get(variable)
        valeurs = [v for v in (brut or "").split(":") if v and chemin(v).is_absolute()]
        source = variable if valeurs else f"défaut ({variable} non défini ou sans chemin absolu)"
        for base in valeurs or list(defaut):
            sortie.append(entree(genre, chemin(base).joinpath(*suffixes(demande)), source))
    return sortie


def dossiers_macos(demande: Demande) -> list[dict[str, Any]]:
    """macOS : ~/Library (conventions d'Apple, alignées sur platformdirs)."""
    if not demande.personnel:
        return [entree(g, None, f"{VAR_HOME} non défini") for g in GENRES_ECRITURE + ("execution",)]
    bibliotheque = PurePosixPath(demande.personnel, "Library")
    support = bibliotheque / "Application Support"
    plan = (("config", support), ("donnees", support), ("etat", support),
            ("cache", bibliotheque / "Caches"), ("journaux", bibliotheque / "Logs"),
            ("execution", bibliotheque / "Caches" / "TemporaryItems"))
    sortie = [entree(g, base.joinpath(*suffixes(demande)), "convention macOS") for g, base in plan]
    sortie += [entree("recherche_donnees", PurePosixPath(base).joinpath(*suffixes(demande)),
                      "convention macOS (tous les comptes)") for base in MACOS_SITE]
    return sortie


def base_windows(demande: Demande, variable: str) -> tuple[PureWindowsPath | None, str]:
    """Dossier connu Windows : variable d'environnement, sinon défaut sous USERPROFILE/--racine."""
    valeur, raison = variable_absolue(demande, variable)
    if valeur is not None:
        return PureWindowsPath(valeur), variable
    if variable in WINDOWS_DEFAUTS and demande.personnel:
        return (PureWindowsPath(demande.personnel, *WINDOWS_DEFAUTS[variable]),
                f"défaut sous le dossier personnel ({variable} {raison})")
    return None, f"{variable} {raison}"


def dossiers_windows(demande: Demande) -> list[dict[str, Any]]:
    """Windows : LOCALAPPDATA (APPDATA si itinérant), PROGRAMDATA ; plan de platformdirs."""
    locale, source_locale = base_windows(demande, VAR_LOCALAPPDATA)
    principale, source = (base_windows(demande, VAR_APPDATA) if demande.itinerant
                          else (locale, source_locale))
    donnees = None if principale is None else principale.joinpath(*suffixes(demande))
    sortie = [entree(g, donnees, source) for g in ("config", "donnees", "etat")]
    sortie.append(entree("cache", None if locale is None else
                         locale.joinpath(*suffixes(demande, "Cache")), source_locale))
    sortie.append(entree("journaux", None if donnees is None else donnees / "Logs", source))
    sortie.append(entree("execution", None if locale is None else
                         (locale / "Temp").joinpath(*suffixes(demande)), source_locale))
    commun, source_commun = base_windows(demande, VAR_PROGRAMDATA)
    sortie.append(entree("recherche_donnees", None if commun is None else
                         commun.joinpath(*suffixes(demande)), source_commun))
    return sortie


def premier_ancetre_existant(chemin: Path) -> Path | None:
    for parent in chemin.parents:
        if parent.exists():
            return parent
    return None


def verifier(dossier: dict[str, Any], demande: Demande) -> None:
    """Existence, nature et droits, par stat et access seulement (rien n'est créé ni ouvert)."""
    if dossier["chemin"] is None or demande.simule:
        dossier.update(existe=None, est_dossier=None, inscriptible=None, creable=None)
        return
    chemin = Path(dossier["chemin"])
    dossier["existe"] = chemin.exists()
    dossier["est_dossier"] = chemin.is_dir() if dossier["existe"] else None
    dossier["lien_symbolique"] = chemin.is_symlink()
    dossier["inscriptible"] = (os.access(chemin, os.W_OK | os.X_OK) if dossier["est_dossier"] else None)
    dossier["creable"] = None
    if not dossier["existe"]:
        ancetre = premier_ancetre_existant(chemin)
        dossier["ancetre_existant"] = None if ancetre is None else str(ancetre)
        dossier["ancetre_est_dossier"] = bool(ancetre and ancetre.is_dir())
        dossier["creable"] = bool(dossier["ancetre_est_dossier"] and os.access(ancetre, os.W_OK | os.X_OK))


def verifier_execution_xdg(dossier: dict[str, Any], demande: Demande) -> None:
    """XDG_RUNTIME_DIR lui-même doit appartenir à l'utilisateur et être en mode 0700."""
    valeur = demande.environnement.get(VAR_RUNTIME)
    if demande.simule or not valeur or not hasattr(os, "getuid") or not os.path.isdir(valeur):
        return
    infos = os.stat(valeur)
    mode = stat.S_IMODE(infos.st_mode)
    dossier["base_conforme"] = infos.st_uid == os.getuid() and mode & 0o077 == 0
    dossier["base_mode"] = oct(mode)


def defauts_de(dossier: dict[str, Any]) -> list[str]:
    """Raisons pour lesquelles l'application ne pourra pas écrire là."""
    if dossier["genre"] not in GENRES_ECRITURE + ("execution",):
        return []
    raisons = []
    if dossier["existe"] and not dossier["est_dossier"]:
        raisons.append("existe mais n'est pas un dossier")
    if dossier["est_dossier"] and dossier["inscriptible"] is False:
        raisons.append("existe sans droit d'écriture")
    if dossier["existe"] is False and dossier["creable"] is False:
        ancetre = dossier.get("ancetre_existant")
        nature = "n'est pas un dossier" if not dossier.get("ancetre_est_dossier") else "non inscriptible"
        raisons.append(f"absent et impossible à créer : l'ancêtre {ancetre} {nature}")
    if dossier.get("base_conforme") is False:
        raisons.append(f"{VAR_RUNTIME} non conforme (propriétaire ou mode {dossier.get('base_mode')})")
    if dossier["chemin"] is None and dossier["genre"] in GENRES_ECRITURE:
        raisons.append("chemin indéterminable (variable ou dossier personnel absent)")
    return raisons


def localiser(demande: Demande) -> list[dict[str, Any]]:
    """Plan des dossiers du système visé, vérifié."""
    if demande.systeme == "windows":
        dossiers = dossiers_windows(demande)
    elif demande.systeme == "macos":
        dossiers = dossiers_macos(demande)
    else:
        dossiers = dossiers_xdg(demande) + recherche_xdg(demande)
    for dossier in dossiers:
        verifier(dossier, demande)
        if dossier["genre"] == "execution" and demande.systeme == "linux":
            verifier_execution_xdg(dossier, demande)
        dossier["defauts"] = defauts_de(dossier)
    return dossiers


def comparer_platformdirs(dossiers: list[dict[str, Any]], demande: Demande) -> dict[str, Any] | None:
    """Ce que platformdirs rend pour la même demande (système réel, environnement réel seulement)."""
    if platformdirs is None:
        return None
    if demande.simule or demande.personnel_force:
        return {"compare": False, "raison": "système simulé, --racine ou --sans-env : non comparable"}
    pd = platformdirs.PlatformDirs(demande.application, appauthor=demande.auteur or False,
                                   version=demande.version, roaming=demande.itinerant)
    sortie = {}
    for dossier in dossiers:
        attribut = CORRESPONDANCE_PLATFORMDIRS.get(dossier["genre"])
        if attribut is None:
            continue
        if attribut == "user_runtime_dir" and demande.systeme == "linux":
            sortie[dossier["genre"]] = {"compare": False, "raison": "non appelé : si XDG_RUNTIME_DIR manque "
                                        "ou n'est pas en 0700, platformdirs se replie via "
                                        "tempfile.gettempdir, qui écrit un fichier sonde"}
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            autre = getattr(pd, attribut)
        sortie[dossier["genre"]] = {"platformdirs": autre, "accord": autre == dossier["chemin"]}
    return sortie


def lire_contrat() -> dict[str, str]:
    """Sections du contrat de mesure, lues dans la docstring du module."""
    sections: dict[str, list[str]] = {}
    courant = None
    for ligne in (__doc__ or "").splitlines():
        if ligne.strip() in INTITULES and not ligne.startswith(" "):
            courant = ligne.strip()
            sections[courant] = []
        elif courant:
            sections[courant].append(ligne.strip())
    return {cle: " ".join(m for m in morceaux if m) for cle, morceaux in sections.items()}


def construire_rapport(dossiers: list[dict[str, Any]], demande: Demande) -> dict[str, Any]:
    noms = [f"{d['genre']}:{d['chemin']}" for d in dossiers]
    return {
        "outil": NOM_OUTIL,
        "moteur": "stdlib",
        "comparaison": comparer_platformdirs(dossiers, demande),
        "application": demande.application,
        "systeme": demande.systeme,
        "systeme_simule": demande.simule,
        "dossier_personnel": demande.personnel,
        "denominateur": len(dossiers),
        "examines": noms,
        "examines_tronques": False,
        "avec_defauts": sum(1 for d in dossiers if d["defauts"]),
        "dossiers": dossiers,
        "a_creer": [d["chemin"] for d in dossiers
                    if d["genre"] in GENRES_ECRITURE and d.get("existe") is False and d.get("creable")],
        "contrat": lire_contrat(),
    }


def afficher_humain(rapport: dict[str, Any]) -> None:
    print(f"{rapport['application']} sur {rapport['systeme']}"
          f"{' (simulé)' if rapport['systeme_simule'] else ''} :")
    for d in rapport["dossiers"]:
        lecture = d["genre"].startswith("recherche")
        etat = ("?" if d["existe"] is None else "existe" if d["existe"] else
                "absent" if lecture else "à créer" if d["creable"] else "absent, non créable")
        droits = " inscriptible" if d["inscriptible"] else ""
        print(f"  {d['genre']:<18} {d['chemin'] or '(indéterminé)'}  [{etat}{droits}] ← {d['source']}")
        for raison in d["defauts"]:
            print(f"    défaut : {raison}")
        if d["note"]:
            print(f"    note : {d['note']}")
    print(f"{rapport['denominateur']} dossier(s) examiné(s), {rapport['avec_defauts']} avec défaut")


def afficher_json(rapport: dict[str, Any]) -> None:
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Dit où une application doit ranger configuration, cache, données, état, "
                    "journaux et fichiers d'exécution (XDG, macOS, Windows), sans rien créer.",
        epilog="Exemple : python localiser_dossiers.py monapp --json\n"
               "          python localiser_dossiers.py monapp --auteur MaSociete --systeme windows\n"
               "Codes : 0 tout est utilisable, 1 un dossier d'écriture inutilisable, "
               "2 entrée invalide, 3 rien à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument("application", help="nom de l'application (un seul composant de chemin)")
    parseur.add_argument("--auteur", help="éditeur (inséré avant l'application sous Windows)")
    parseur.add_argument("--version", dest="version_app", help="sous-dossier de version")
    parseur.add_argument("--systeme", choices=("auto",) + SYSTEMES, default="auto",
                         help="système visé ; autre que l'hôte = simulation sans vérification")
    parseur.add_argument("--itinerant", action="store_true", help="Windows : profil itinérant (APPDATA)")
    parseur.add_argument("--sans-env", action="store_true",
                         help="ignore les variables d'environnement (défauts purs)")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="dossier personnel à utiliser à la place de HOME (autre compte, conteneur)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def preparer(args: argparse.Namespace) -> Demande:
    """Valide les noms et fixe le contexte ; lève EntreeInvalide."""
    application = valider_nom(args.application, "application")
    auteur = valider_nom(args.auteur, "--auteur") if args.auteur is not None else None
    version = valider_nom(args.version_app, "--version") if args.version_app is not None else None
    hote = systeme_hote()
    systeme = hote if args.systeme == "auto" else args.systeme
    environnement = {} if args.sans_env else dict(os.environ)
    variable_perso = VAR_USERPROFILE if systeme == "windows" else VAR_HOME
    personnel = str(args.racine) if args.racine is not None else environnement.get(variable_perso)
    if personnel and not classe_chemin(systeme)(personnel).is_absolute():
        raise EntreeInvalide(f"dossier personnel relatif : {personnel!r}")
    return Demande(application, auteur, version, systeme, args.itinerant, environnement,
                           personnel or None, systeme != hote,
                           personnel_force=args.racine is not None or args.sans_env)


def main() -> int:
    args = construire_parseur().parse_args()
    try:
        demande = preparer(args)
    except EntreeInvalide as exc:
        print(f"entrée invalide : {exc}", file=sys.stderr)
        return 2
    if platformdirs is None:
        print("mode dégradé — platformdirs absent : calcul stdlib seul, sans comparaison", file=sys.stderr)
    try:
        dossiers = localiser(demande)
    except OSError as exc:
        print(f"entrée invalide : examen impossible ({exc})", file=sys.stderr)
        return 2
    rapport = construire_rapport(dossiers, demande)
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    if not dossiers:
        print("dénominateur nul : aucun dossier à examiner, rien à examiner", file=sys.stderr)
        return 3
    return 1 if rapport["avec_defauts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
