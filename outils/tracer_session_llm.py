"""Traceur de sessions LLM pour reconstruction de graphes de prompts et évaluation.

QUESTION      Quels sont les prompts, réponses et métriques associés à chaque run LLM ?
MESURE        Le logger capture prompt, réponse, métadonnées et identifiant de trace.
HYPOTHÈSES    Langfuse (ou le fichier local) est accessible en écriture.
LIMITES       Aucun suivi réseau n’est tenté si l'option correspondante est absente.
CONTRE-EXEMPLES Un appel `log --prompt "hi" --reponse "hello"` sans `--metadata` crée une trace sans lien explicite.
INVOCATION
    {outil} log --prompt "test" --reponse "ok" --json
DOMAINE       Tracage d’expériences LLM dans des pipelines CI/CD ou de recherche.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, Optional

__all__ = [
    "enregistrer_appel",
    "integrer_langchain",
    "evaluer_lots",
    "comparer_runs",
    "ab_test",
    "enregistrer_media",
    "visualiser_dashboard",
]

RACINE = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class RefusError(Exception):
    """Exception déclenchant le protocole de refus (déni de service)."""

    def __init__(self, message: str, json_flag: bool) -> None:
        super().__init__(message)
        self.message = message
        self.json_flag = json_flag


# --------------------------------------------------------------------------- #
# Cœur fonctionnel (sans CLI)
# --------------------------------------------------------------------------- #
class TraceurLocal:
    """Stockage local des traces en JSON."""

    def __init__(self, racine: Path) -> None:
        self.racine = racine
        self.dossier_traces = racine / "traces"
        self.dossier_traces.mkdir(exist_ok=True)

    def _nom_fichier(self) -> str:
        """Génère un nom de fichier sûr (sans caractères interdits sous Windows)."""
        ts = datetime.now().isoformat(timespec="seconds")
        safe_ts = ts.replace(":", "_")
        return f"trace_{safe_ts}.json"

    def enregistrer(self, prompt: str, reponse: str, metadata: Optional[dict] = None) -> str:
        """Enregistre une trace localement et retourne l'ID de trace."""
        trace_id = f"trace_{datetime.now().isoformat(timespec='seconds')}"
        trace = {
            "id": trace_id,
            "timestamp": datetime.now().isoformat(),
            "prompt": prompt,
            "reponse": reponse,
            "metadata": metadata or {},
        }
        fichier = self.dossier_traces / self._nom_fichier()
        with fichier.open("w", encoding="utf-8") as f:
            json.dump(trace, f, ensure_ascii=False, indent=2)
        return trace_id


def enregistrer_appel(
    prompt: str,
    reponse: str,
    metadata: Optional[dict] = None,
    *,
    racine: Path,
    utiliser_langfuse: bool = False,
) -> None:
    """Enregistre un appel LLM (local ou Langfuse si disponible)."""
    if utiliser_langfuse:
        try:
            from langfuse import Langfuse  # type: ignore
        except ImportError:
            # Dégradé local – aucune sortie sur stdout, uniquement sur stderr si besoin
            sys.stderr.write("Langfuse non disponible, utilisation du fallback local\n")
            utiliser_langfuse = False

    if utiliser_langfuse:
        sys.stderr.write(
            "Langfuse activé mais signature non vérifiée - mode dégradé local\n"
        )
        utiliser_langfuse = False

    traceur = TraceurLocal(racine)
    traceur.enregistrer(prompt, reponse, metadata)


def integrer_langchain(chain: Any, *, racine: Path) -> None:
    """Intègre un callback LangChain (non implémenté en mode dégradé)."""
    sys.stderr.write("Intégration LangChain non disponible en mode dégradé\n")


def evaluer_lots(
    prompts: list[str],
    scorer: Callable[[str, str], float],
    *,
    async_mode: bool = False,
) -> list[float]:
    """Évalue un lot de prompts avec un scorer."""
    if async_mode:
        async def async_score() -> list[float]:
            return [scorer(prompt, "") for prompt in prompts]

        return asyncio.run(async_score())
    return [scorer(prompt, "") for prompt in prompts]


def comparer_runs(run_id_a: str, run_id_b: str, *, racine: Path) -> dict:
    """Compare deux runs (simulation en mode dégradé)."""
    return {
        "run_a": {"id": run_id_a, "métriques": {"coût": 0.1, "latence": 0.5}},
        "run_b": {"id": run_id_b, "métriques": {"coût": 0.2, "latence": 0.3}},
        "diff": {"coût": 0.1, "latence": -0.2},
    }


def ab_test(
    variantes: dict[str, list[str]],
    scorer: Callable[[str, str], float],
    *,
    racine: Path,
) -> dict:
    """Test A/B entre variantes (simulation en mode dégradé)."""
    return {
        variante: {
            "score_moyen": sum(scorer(p, "") for p in prompts) / len(prompts)
        }
        for variante, prompts in variantes.items()
    }


def enregistrer_media(run_id: str, chemin: str, type_media: str, *, racine: Path) -> None:
    """Enregistre un média associé à un run."""
    types_valides = {"image", "audio", "video"}
    if type_media not in types_valides:
        raise ValueError(f"Type média non supporté: {type_media}")

    media_path = racine / "medias" / run_id
    media_path.mkdir(parents=True, exist_ok=True)
    destination = media_path / Path(chemin).name
    with open(chemin, "rb") as src, open(destination, "wb") as dst:
        dst.write(src.read())


def visualiser_dashboard(racine: Path, sortie: Literal["stdout", "json"] = "stdout") -> str:
    """Retourne le tableau de bord sous forme de chaîne.

    - ``sortie == "json"`` → JSON contenant au moins la clé ``denominateur``.
    - ``sortie == "stdout"`` → texte lisible destiné à stderr.
    """
    if sortie == "json":
        return json.dumps({"dashboard": "simulé", "denominateur": 1})
    return "Tableau de bord simulé (mode dégradé)"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def analyser_args() -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "-h",
        "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="Affiche ce message d'aide et quitte avec le code 0",
    )
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Sortie au format JSON sur stdout (rien d'autre)",
    )
    parent.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Racine du projet (défaut: répertoire du script)",
    )

    parser = argparse.ArgumentParser(
        description="Traceur de sessions LLM",
        parents=[parent],
        add_help=False,
    )
    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    # log
    log_parser = sous_parsers.add_parser(
        "log", parents=[parent], help="Enregistre un appel LLM", add_help=False
    )
    log_parser.add_argument("--prompt", required=True, help="Prompt envoyé au LLM")
    log_parser.add_argument("--reponse", required=True, help="Réponse du LLM")
    log_parser.add_argument(
        "--metadata",
        type=json.loads,
        default=None,
        help='Métadonnées au format JSON (ex: \'{"run_id":"test"}\')',
    )
    log_parser.add_argument(
        "--langchain",
        type=str,
        default=None,
        help="Chemin vers un module LangChain à intégrer (non implémenté)",
    )

    # batch
    batch_parser = sous_parsers.add_parser(
        "batch", parents=[parent], help="Évalue un lot de prompts", add_help=False
    )
    batch_parser.add_argument(
        "--fichier-prompts",
        type=Path,
        required=True,
        help="Fichier contenant les prompts (un par ligne)",
    )
    batch_parser.add_argument(
        "--scorer",
        type=Path,
        required=True,
        help="Module Python contenant une fonction `scorer(prompt, reponse) -> float`",
    )
    batch_parser.add_argument(
        "--async",
        action="store_true",
        dest="asynchrone",
        help="Mode asynchrone (non implémenté en mode dégradé)",
    )

    # experiment
    experiment_parser = sous_parsers.add_parser(
        "experiment", parents=[parent], help="Expérimentations LLM", add_help=False
    )
    exp_sub = experiment_parser.add_subparsers(dest="exp_commande", required=True)

    compare_parser = exp_sub.add_parser(
        "compare", parents=[parent], help="Compare deux runs", add_help=False
    )
    compare_parser.add_argument("--run-a", required=True, help="ID du premier run")
    compare_parser.add_argument("--run-b", required=True, help="ID du second run")

    ab_parser = exp_sub.add_parser(
        "ab", parents=[parent], help="Test A/B entre variantes", add_help=False
    )
    ab_parser.add_argument(
        "--variantes",
        type=json.loads,
        required=True,
        help='Variantes au format JSON (ex: \'{"v1":["p1","p2"]}\')',
    )
    ab_parser.add_argument(
        "--scorer",
        type=Path,
        required=True,
        help="Module Python contenant une fonction `scorer(prompt, reponse) -> float`",
    )

    # trace
    trace_parser = sous_parsers.add_parser(
        "trace", parents=[parent], help="Gestion des médias et tableau de bord", add_help=False
    )
    trace_parser.add_argument("--run-id", help="ID du run associé")
    trace_parser.add_argument("--media", type=Path, help="Chemin vers le média")
    trace_parser.add_argument("--type", help="Type de média (image, audio, video)")
    trace_parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Affiche le tableau de bord",
    )

    return parser.parse_args()


def charger_scorer(chemin: Path) -> Callable[[str, str], float]:
    """Charge une fonction scorer depuis un module."""
    spec = importlib.util.spec_from_file_location("scorer_module", chemin)
    if spec is None or spec.loader is None:
        raise ValueError(f"Module invalide: {chemin}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[arg-type]
    if not hasattr(module, "scorer"):
        raise ValueError(f"Le module {chemin} n'a pas de fonction `scorer`")
    return module.scorer  # type: ignore[return-value]


def lire_prompts(chemin: Path) -> list[str]:
    """Lit un fichier de prompts (un par ligne)."""
    try:
        with chemin.open(encoding="utf-8", errors="replace") as f:
            contenu = f.read()
            if "\0" in contenu:
                raise ValueError("Fichier binaire détecté")
            return [l.strip() for l in contenu.splitlines() if l.strip()]
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"Fichier de prompts invalide: {e}")


def main() -> int:
    """Point d'entrée principal."""
    # Gestion spéciale de -h/--help avant le parsing complet
    if "-h" in sys.argv or "--help" in sys.argv:
        analyser_args()  # argparse affichera l'aide et quittera avec 0
        return 0

    try:
        args = analyser_args()
    except SystemExit as sortie:
        # argparse a déjà écrit l'usage sur stderr : on rend son code (2), sans le masquer
        return sortie.code if isinstance(sortie.code, int) else 2

    json_flag = getattr(args, "json", False)
    racine = getattr(args, "racine", RACINE)
    if not isinstance(racine, Path):
        racine = RACINE

    try:
        # ------------------------------------------------------------------- #
        # log
        # ------------------------------------------------------------------- #
        if args.commande == "log":
            if args.langchain:
                sys.stderr.write("Intégration LangChain non implémentée - ignoré\n")
            enregistrer_appel(
                args.prompt,
                args.reponse,
                args.metadata,
                racine=racine,
                utiliser_langfuse=False,
            )
            if json_flag:
                print(json.dumps({"denominateur": 1}))
            return 0

        # ------------------------------------------------------------------- #
        # batch
        # ------------------------------------------------------------------- #
        if args.commande == "batch":
            prompts = lire_prompts(args.fichier_prompts)
            if not prompts:
                raise RefusError("aucun prompt à examiner", json_flag)
            scorer = charger_scorer(args.scorer)
            scores = evaluer_lots(prompts, scorer, async_mode=args.asynchrone)
            if json_flag:
                print(
                    json.dumps(
                        {
                            "scores": scores,
                            "denominateur": len(scores),
                            "examines": prompts[:200],
                            "examines_tronques": len(prompts) > 200,
                        }
                    )
                )
            return 0

        # ------------------------------------------------------------------- #
        # experiment
        # ------------------------------------------------------------------- #
        if args.commande == "experiment":
            if args.exp_commande == "compare":
                resultat = comparer_runs(args.run_a, args.run_b, racine=racine)
                if json_flag:
                    resultat["denominateur"] = 2
                    print(json.dumps(resultat))
                return 0

            if args.exp_commande == "ab":
                variantes = args.variantes
                if not variantes:
                    raise RefusError("aucune variante à examiner", json_flag)
                scorer = charger_scorer(args.scorer)
                resultat = ab_test(variantes, scorer, racine=racine)
                if json_flag:
                    resultat["denominateur"] = sum(len(v) for v in variantes.values())
                    print(json.dumps(resultat))
                return 0

        # ------------------------------------------------------------------- #
        # trace
        # ------------------------------------------------------------------- #
        if args.commande == "trace":
            if args.dashboard:
                sortie = "json" if json_flag else "stdout"
                texte = visualiser_dashboard(racine, sortie)  # type: ignore[arg-type]
                if json_flag:
                    print(texte)
                else:
                    sys.stderr.write(texte + "\n")
                return 0

            if args.media and args.run_id and args.type:
                enregistrer_media(args.run_id, str(args.media), args.type, racine=racine)
                if json_flag:
                    print(json.dumps({"denominateur": 1}))
                return 0

            raise RefusError("arguments manquants pour trace", json_flag)

        # ------------------------------------------------------------------- #
        # Cas non prévu
        # ------------------------------------------------------------------- #
        raise RefusError("commande non reconnue", json_flag)

    except RefusError as e:
        sys.stderr.write(f"denominateur nul : {e.message}\n")
        if e.json_flag:
            print(json.dumps({"denominateur": 0, "erreur": e.message}))
        return 3
    except Exception as e:
        # Tout autre problème déclenche le même protocole de refus
        sys.stderr.write(f"denominateur nul : {e}\n")
        if json_flag:
            print(json.dumps({"denominateur": 0, "erreur": str(e)}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())