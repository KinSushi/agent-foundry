"""
QUESTION: Que répond ce fournisseur de LLM ?
MESURE: Envoie un prompt à un fournisseur de LLM et capture la réponse
        textuelle, en mesurant le temps, les tentatives, et en analysant la
        troncature ou le format JSON.
HYPOTHÈSES: Le point de terminaison suit l'API Chat Completions d'OpenAI.
            La clé d'API est fournie.
LIMITES: Sans openai ni httpx, utilise urllib (pas de streaming). Sans backoff,
        retries manuels. Sans mistral-common, pas de comptage de tokens.
CONTRE-EXEMPLES: Un fournisseur non compatible OpenAI renvoie une erreur HTTP
                lisible sur stderr. Une clé absente provoque un message clair.
DOMAINE: Fournisseurs de LLM exposant une API REST compatible OpenAI
        Chat Completions.

INVOCATION
    {outil} --hors-ligne --prompt "bonjour" --json

Cette option `--hors-ligne` désactive tout accès réseau (mode par défaut).
Tout accès réseau doit être explicitement demandé via `--en-ligne`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------


def _module_est_disponible(nom: str) -> bool:
    """Détecte la présence d'un module sans l'importer."""
    return importlib.util.find_spec(nom) is not None


_A_OPENAI = _module_est_disponible("openai")
_A_HTTPX = _module_est_disponible("httpx")
_A_BACKOFF = _module_est_disponible("backoff")
_A_MISTRAL = _module_est_disponible("mistral_common")


def _mode_courant() -> str:
    """Nom du mode de fonctionnement actif."""
    if _A_OPENAI:
        return "openai"
    if _A_HTTPX:
        return "httpx"
    return "urllib"


def _modules_disponibles() -> dict[str, bool]:
    """État de disponibilité des modules tiers."""
    return {
        "openai": _A_OPENAI,
        "httpx": _A_HTTPX,
        "backoff": _A_BACKOFF,
        "mistral_common": _A_MISTRAL,
    }


def _compter_tokens(texte: str) -> int | None:
    """Compte les tokens du texte si mistral-common est disponible."""
    if not _A_MISTRAL:
        return None
    try:
        from mistral_common.tokens.tokenizers.mistral import (
            MistralTokenizer,
        )
        tokenizer = MistralTokenizer.v3()
        tokens = tokenizer.encode_chat_completion(
            [{"role": "user", "content": texte}]
        )
        return len(tokens.tokens)
    except Exception:
        return None


def _appel_urllib(
    prompt: str,
    cle_api: str,
    url_base: str,
    modele: str,
    timeout: float,
) -> dict[str, Any]:
    """Appel HTTP via urllib (mode dégradé, pas de streaming)."""
    url = url_base.rstrip("/") + "/chat/completions"
    charge = {
        "model": modele,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    donnees = json.dumps(charge).encode("utf-8")
    requete = urllib.request.Request(
        url,
        data=donnees,
        headers={
            "Authorization": f"Bearer {cle_api}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(requete, timeout=timeout) as reponse:
            corps = reponse.read().decode("utf-8")
            return json.loads(corps)
    except urllib.error.HTTPError as e:
        corps = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {corps}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL: {e.reason}") from e


def _appel_httpx(
    prompt: str,
    cle_api: str,
    url_base: str,
    modele: str,
    timeout: float,
) -> dict[str, Any]:
    """Appel HTTP via httpx, avec fallback vers urllib si import impossible."""
    try:
        import httpx  # import local, exécuté seulement si le module est présent
    except ImportError:
        httpx = None

    if httpx is None:
        # Fallback vers urllib
        return _appel_urllib(prompt, cle_api, url_base, modele, timeout)

    url = url_base.rstrip("/") + "/chat/completions"
    charge = {
        "model": modele,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    with httpx.Client(timeout=timeout) as client:
        reponse = client.post(
            url,
            json=charge,
            headers={"Authorization": f"Bearer {cle_api}"},
        )
        reponse.raise_for_status()
        return reponse.json()


def _appel_openai(
    prompt: str,
    cle_api: str,
    url_base: str,
    modele: str,
    timeout: float,
) -> dict[str, Any]:
    """Appel via la bibliothèque openai, avec fallback vers urllib si import impossible."""
    try:
        import openai  # import local
    except ImportError:
        openai = None

    if openai is None:
        # Fallback vers urllib
        return _appel_urllib(prompt, cle_api, url_base, modele, timeout)

    client = openai.OpenAI(
        api_key=cle_api,
        base_url=url_base,
        timeout=timeout,
    )
    reponse = client.chat.completions.create(
        model=modele,
        messages=[{"role": "user", "content": prompt}],
        stream=False,
    )
    try:
        return reponse.model_dump()
    except AttributeError:
        return json.loads(reponse.json())


def _extraire_texte(reponse: dict[str, Any]) -> str:
    """Extrait le texte de la réponse au format OpenAI‑compatible."""
    try:
        choixs = reponse.get("choices", [])
        if not choixs:
            return ""
        texte = choixs[0].get("message", {}).get("content", "")
        return texte or ""
    except Exception:
        return ""


def _detecter_troncature(reponse: dict[str, Any], texte: str) -> bool:
    """Détecte si la réponse a été tronquée."""
    try:
        choixs = reponse.get("choices", [])
        if not choixs:
            return False
        raison = choixs[0].get("finish_reason", "")
        if raison == "length":
            return True
        if texte:
            stripped = texte.rstrip()
            if not stripped.endswith((".", "!", "?", "```", ")", "]", "}")):
                if "{" in texte and not stripped.endswith("}"):
                    return True
        return False
    except Exception:
        return False


def _tenter_parse_json_tronque(texte: str) -> Any:
    """Parse un JSON potentiellement tronqué (best‑effort)."""
    try:
        return json.loads(texte)
    except json.JSONDecodeError:
        pass
    ouvertes = texte.count("{") - texte.count("}")
    crochets = texte.count("[") - texte.count("]")
    if ouvertes > 0 or crochets > 0:
        corrige = texte + ("}" * max(ouvertes, 0)) + ("]" * max(crochets, 0))
        try:
            return json.loads(corrige)
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------


def extraire_prompt(cible: Path) -> str:
    """Extrait le prompt d'un fichier cible."""
    if not cible.exists():
        raise FileNotFoundError(f"Cible inexistante: {cible}")
    if not cible.is_file():
        raise ValueError(f"Cible n'est pas un fichier: {cible}")

    contenu = cible.read_text(encoding="utf-8")

    if cible.suffix == ".py":
        try:
            code = compile(contenu, cible.name, "exec")
        except SyntaxError as e:
            raise ValueError(f"Erreur de syntaxe dans {cible.name}: {e}") from e

        namespace: dict[str, Any] = {}
        exec(code, namespace)

        if "PROMPT" not in namespace:
            raise ValueError(f"La variable PROMPT n'est pas définie dans {cible.name}")

        prompt = namespace["PROMPT"]
        if not isinstance(prompt, str):
            raise ValueError("PROMPT n'est pas une chaîne de caractères")
        return prompt

    return contenu


def appeler_fournisseur(
    prompt: str,
    *,
    cle_api: str,
    url_base: str = "https://api.openai.com/v1",
    modele: str = "gpt-4o-mini",
    timeout: float = 30.0,
    max_tentatives: int = 3,
) -> dict[str, Any]:
    """Interroge le fournisseur avec retries."""
    debut = time.monotonic()
    derniere_erreur: str | None = None
    tentatives = 0

    generateur_delais = None
    if _A_BACKOFF:
        try:
            import backoff  # import local
        except ImportError:
            backoff = None
        if backoff is not None:
            generateur_delais = backoff.expo(base=2, factor=1)

    for i in range(max_tentatives):
        tentatives = i + 1
        try:
            if _A_OPENAI:
                resultat = _appel_openai(prompt, cle_api, url_base, modele, timeout)
            elif _A_HTTPX:
                resultat = _appel_httpx(prompt, cle_api, url_base, modele, timeout)
            else:
                resultat = _appel_urllib(prompt, cle_api, url_base, modele, timeout)

            elapsed = time.monotonic() - debut
            return {
                "reponse": resultat,
                "temps": round(elapsed, 3),
                "tentatives": tentatives,
                "erreur": None,
            }
        except Exception as e:
            derniere_erreur = str(e)
            if i < max_tentatives - 1:
                if generateur_delais is not None:
                    try:
                        attente = next(generateur_delais)
                    except StopIteration:
                        attente = 2.0 ** i
                else:
                    attente = 2.0 ** i
                time.sleep(min(attente, 30.0))

    elapsed = time.monotonic() - debut
    return {
        "reponse": None,
        "temps": round(elapsed, 3),
        "tentatives": tentatives,
        "erreur": derniere_erreur,
    }


def analyser_reponse(
    resultat: dict[str, Any],
) -> dict[str, Any]:
    """Analyse la réponse du LLM et renvoie un résumé structuré."""
    erreur = resultat.get("erreur")
    reponse = resultat.get("reponse")

    if erreur is not None or reponse is None:
        return {
            "texte": "",
            "temps": resultat.get("temps", 0),
            "tentatives": resultat.get("tentatives", 0),
            "tronque": False,
            "tokens": None,
            "erreur": erreur,
            "json_parse": None,
            "mode": _mode_courant(),
        }

    texte = _extraire_texte(reponse)
    tronque = _detecter_troncature(reponse, texte)
    tokens = _compter_tokens(texte)
    json_parse = _tenter_parse_json_tronque(texte)

    return {
        "texte": texte,
        "temps": resultat.get("temps", 0),
        "tentatives": resultat.get("tentatives", 0),
        "tronque": tronque,
        "tokens": tokens,
        "erreur": None,
        "json_parse": json_parse,
        "mode": _mode_courant(),
    }


def formater_sortie_humaine(analyse: dict[str, Any]) -> str:
    """Formate l'analyse pour une sortie humaine."""
    lignes: list[str] = []

    if analyse["erreur"]:
        lignes.append(f"ERREUR: {analyse['erreur']}")
        lignes.append(f"Tentatives: {analyse['tentatives']}")
        lignes.append(f"Temps: {analyse['temps']}s")
        lignes.append(f"Mode: {analyse['mode']}")
        return "\n".join(lignes)

    lignes.append(f"Mode: {analyse['mode']}")
    lignes.append(f"Temps: {analyse['temps']}s")
    lignes.append(f"Tentatives: {analyse['tentatives']}")
    if analyse["tokens"] is not None:
        lignes.append(f"Tokens: {analyse['tokens']}")
    if analyse["tronque"]:
        lignes.append("ATTENTION: réponse potentiellement tronquée")
    lignes.append("")
    lignes.append("RÉPONSE:")
    lignes.append(analyse["texte"])

    return "\n".join(lignes)


def formater_sortie_json(
    analyse: dict[str, Any],
    *,
    modules: dict[str, bool],
    requete_offline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Formate l'analyse pour une sortie JSON."""
    lignes_texte = [l for l in analyse["texte"].splitlines() if l.strip()]
    denominateur = len(lignes_texte) or (1 if requete_offline else 0)

    examines = [{"ligne": ligne} for ligne in lignes_texte[:200]]

    contrat = {
        "QUESTION": "Que répond ce fournisseur de LLM ?",
        "MESURE": (
            "Envoie un prompt à un point de terminaison compatible OpenAI "
            "et capture la réponse textuelle renvoyée par le modèle."
        ),
        "HYPOTHESES": (
            "Le point de terminaison suit l'API Chat Completions d'OpenAI. "
            "La clé d'API est fournie via --cle-api ou OPENAI_API_KEY."
        ),
        "LIMITES": (
            "Sans openai ni httpx, utilise urllib (pas de streaming). "
            "Sans backoff, retries manuels exponentiels. "
            "Sans mistral-common, pas de comptage de tokens."
        ),
        "CONTRE-EXEMPLES": (
            "Un fournisseur non compatible OpenAI renvoie une erreur HTTP "
            "lisible sur stderr. Une clé absente provoque un message clair."
        ),
        "DOMAINE": (
            "Fournisseurs de LLM exposant une API REST compatible OpenAI "
            "Chat Completions."
        ),
    }

    modules_absents = [k for k, v in modules.items() if not v]

    sortie = {
        "contrat": contrat,
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": denominateur > 200,
        "modules": modules,
        "modules_absents": modules_absents,
        "mode_degrade": len(modules_absents) > 0,
        "analyse": analyse,
    }

    if requete_offline is not None:
        sortie["requete_offline"] = requete_offline

    return sortie


# ---------------------------------------------------------------------------


def _creer_analyseur() -> argparse.ArgumentParser:
    """Crée l'analyseur d'arguments."""
    analyseur = argparse.ArgumentParser(
        prog="appeler_llm.py",
        description="Interroge un fournisseur de LLM et rapporte sa réponse.",
        epilog=(
            "Exemples :\n"
            "  python appeler_llm.py --fichier prompt.py --modele gpt-4o-mini\n"
            "  python appeler_llm.py --json --prompt 'Cite 3 villes' "
            "--modele mistral-large-latest --url-base https://api.mistral.ai/v1\n"
            "  python appeler_llm.py --prompt 'Hello' --cle-api sk-xxx "
            "--url-base http://localhost:11434/v1 --modele llama3"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyseur.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="Rend un seul objet JSON sur stdout au lieu du texte humain.",
    )
    analyseur.add_argument(
        "--racine",
        type=str,
        default=None,
        help="Surcharge la racine (insérée en tête de sys.path).",
    )
    groupe_prompt = analyseur.add_mutually_exclusive_group(required=True)
    groupe_prompt.add_argument(
        "--prompt",
        type=str,
        help="Le prompt à envoyer au LLM.",
    )
    groupe_prompt.add_argument(
        "--fichier",
        type=Path,
        help="Fichier contenant le prompt (.txt) ou définissant une variable PROMPT (.py).",
    )
    analyseur.add_argument(
        "--modele",
        type=str,
        default="gpt-4o-mini",
        help="Nom du modèle à interroger (défaut : gpt-4o-mini).",
    )
    analyseur.add_argument(
        "--cle-api",
        type=str,
        default=None,
        help="Clé d'API (défaut : variable d'environnement OPENAI_API_KEY).",
    )
    analyseur.add_argument(
        "--url-base",
        type=str,
        default="https://api.openai.com/v1",
        help="URL de base de l'API (défaut : https://api.openai.com/v1).",
    )
    analyseur.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Timeout en secondes (défaut : 30).",
    )
    analyseur.add_argument(
        "--max-tentatives",
        type=int,
        default=3,
        help="Nombre maximum de tentatives (défaut : 3).",
    )
    analyseur.add_argument(
        "--hors-ligne",
        action="store_true",
        dest="hors_ligne",
        help="Force le mode hors‑ligne (défaut). Aucun appel réseau ne sera effectué.",
    )
    analyseur.add_argument(
        "--en-ligne",
        action="store_true",
        dest="en_ligne",
        help="Autorise les appels réseau.",
    )
    return analyseur


def main() -> int:
    """Point d'entrée principal."""
    analyseur = _creer_analyseur()
    args = analyseur.parse_args()

    # Racine
    racine_defaut = Path(__file__).resolve().parent
    racine = Path(args.racine).resolve() if args.racine else racine_defaut
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))

    # Extraction du prompt
    try:
        prompt = extraire_prompt(args.fichier) if args.fichier else args.prompt or ""
    except Exception as e:
        print(f"ERREUR de lecture de la cible : {e}", file=sys.stderr)
        return 1

    if not prompt.strip():
        print("ERREUR: Le prompt est vide. Rien à examiner.", file=sys.stderr)
        return 3

    # Clé d'API
    cle_api = args.cle_api or os.environ.get("OPENAI_API_KEY", "")
    if not cle_api and args.en_ligne:
        print(
            "ERREUR: Aucune clé d'API. Fournissez --cle-api ou définissez "
            "la variable d'environnement OPENAI_API_KEY.",
            file=sys.stderr,
        )
        return 2

    # Modules disponibles
    modules = _modules_disponibles()
    modules_absents = [k for k, v in modules.items() if not v]
    if modules_absents:
        print(
            f"Mode dégradé : modules absents : {', '.join(modules_absents)}. "
            f"Utilisation du mode {_mode_courant()}.",
            file=sys.stderr,
        )

    # Décision hors‑ligne / en‑ligne
    hors_ligne = not args.en_ligne  # défaut hors‑ligne
    if hors_ligne:
        corps = {
            "model": args.modele,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        en_tetes = {"Content-Type": "application/json"}
        requete_offline = {
            "modele": args.modele,
            "en_tetes": en_tetes,
            "corps": corps,
        }
        analyse = {
            "texte": "",
            "temps": 0,
            "tentatives": 0,
            "tronque": False,
            "tokens": None,
            "erreur": None,
            "json_parse": None,
            "mode": "hors-ligne",
        }
        if args.json:
            sortie = formater_sortie_json(
                analyse,
                modules=modules,
                requete_offline=requete_offline,
            )
            if sortie["denominateur"] == 0:
                print(
                    "REFUS DE CONCLURE : denominateur nul, aucun élément examiné.",
                    file=sys.stderr,
                )
                return 3
            print(json.dumps(sortie, ensure_ascii=False, indent=2))
        else:
            print(
                "Mode hors‑ligne activé. Requête préparée (sans secret) :",
                file=sys.stderr,
            )
            print(json.dumps(requete_offline, ensure_ascii=False, indent=2), file=sys.stderr)
        return 0

    # Mode en‑ligne : appel réel
    try:
        resultat = appeler_fournisseur(
            prompt,
            cle_api=cle_api,
            url_base=args.url_base,
            modele=args.modele,
            timeout=args.timeout,
            max_tentatives=args.max_tentatives,
        )
    except Exception as e:
        print(f"ERREUR inattendue : {e}", file=sys.stderr)
        return 1

    analyse = analyser_reponse(resultat)

    code_sortie = 0
    if analyse["erreur"] or analyse["tronque"]:
        code_sortie = 1

    if args.json:
        sortie = formater_sortie_json(analyse, modules=modules)
        if sortie["denominateur"] == 0:
            print(
                "REFUS DE CONCLURE : denominateur nul, aucun élément examiné.",
                file=sys.stderr,
            )
            return 3
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    else:
        if not analyse["texte"].strip():
            print(
                "REFUS DE CONCLURE : réponse vide, aucun élément examiné.",
                file=sys.stderr,
            )
            return 3
        print(formater_sortie_humaine(analyse))

    return code_sortie


if __name__ == "__main__":
    raise SystemExit(main())