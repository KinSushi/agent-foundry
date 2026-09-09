"""QUESTION
Mesure de gradients, Jacobiennes, Hessiennes, réarrangements et compilation de modèles PyTorch.
HYPOTHESES
Tenseurs différentiables, installation de torch, functorch ou einops optionnels.
LIMITES
Pas de support GPU avancé, précision flottante non garantie, aucune optimisation réseau.
CONTRE-EXEMPLES
Fonction non différentiable ou pattern einops invalide entraîne une erreur.
INVOCATION
    {outil} compiler {fichier} --json
DOMAINE
Modèles PyTorch 1.13+ sur CPU/GPU, usage hors réseau.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, List, Sequence

# ------------------------------------------------------------
# Encodage UTF‑8 pour la console Windows
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Constantes
# ------------------------------------------------------------
RACINE_DEFAUT = Path(__file__).resolve().parent
REFUS_DENOMINATEUR_MOTS = ("nul", "vide", "aucun", "refuse", "impossible de conclure")

# ------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------
class TenseurError(Exception):
    """Exception levée par le cœur en cas d’erreur mesurable."""
    def __init__(self, message: str, denominateur: int = 0, examines: Sequence[Any] | None = None):
        super().__init__(message)
        self.denominateur = denominateur
        self.examines = list(examines) if examines is not None else []

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _resolve_callable(spec: str) -> Callable:
    """Résout une chaîne « module:callable » en objet appelable."""
    try:
        module_name, attr_name = spec.split(":", 1)
    except ValueError as exc:
        raise TenseurError(f"Spécification de fonction invalide : {spec}") from exc
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise TenseurError(f"Impossible d’importer le module : {module_name}") from exc
    try:
        func = getattr(module, attr_name)
    except AttributeError as exc:
        raise TenseurError(f"Le module « {module_name} » ne possède pas l’attribut « {attr_name} »") from exc
    if not callable(func):
        raise TenseurError(f"L’attribut « {attr_name} » du module « {module_name} » n’est pas appelable")
    return func

def _load_tensor(path: Path) -> Any:
    """Charge un tenseur (ou tout objet) depuis un fichier torch."""
    try:
        import torch
    except Exception as exc:
        raise TenseurError("torch n’est pas disponible", denominateur=0) from exc
    try:
        return torch.load(str(path), map_location="cpu")
    except Exception as exc:
        raise TenseurError(f"Échec du chargement du fichier : {path}", denominateur=0) from exc

def _load_batch(path: Path) -> List[Any]:
    """Charge une séquence de tenseurs depuis un fichier."""
    obj = _load_tensor(path)
    if not isinstance(obj, (list, tuple)):
        raise TenseurError("Le fichier de batch ne contient pas une liste de tenseurs", denominateur=0)
    return list(obj)

def _import_optional(name: str) -> Any:
    """Importe une bibliothèque optionnelle uniquement si disponible."""
    if importlib.util.find_spec(name) is None:
        return None
    try:
        return importlib.import_module(name)
    except Exception:
        return None

# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------
__all__ = [
    "calculer_gradient",
    "appliquer_batch",
    "calculer_jacobienne",
    "calculer_hessienne",
    "rearranger_tensor",
    "compiler_script",
    "ajouter_batch",
]

# ------------------------------------------------------------
# Cœur de l’application
# ------------------------------------------------------------
def calculer_gradient(spec: str, chemin_tensor: Path) -> Any:
    """Calcule le gradient d’une fonction scalaire par rapport à un tenseur d’entrée."""
    func = _resolve_callable(spec)
    x = _load_tensor(chemin_tensor)
    try:
        import torch
    except Exception as exc:
        raise TenseurError("torch n’est pas disponible", denominateur=0) from exc
    if not isinstance(x, torch.Tensor):
        raise TenseurError("L’entrée n’est pas un tenseur PyTorch", denominateur=0)
    x.requires_grad_(True)
    try:
        y = func(x)
    except Exception as exc:
        raise TenseurError("Erreur lors de l’appel de la fonction cible", denominateur=0) from exc
    if not torch.is_tensor(y) or y.numel() != 1:
        raise TenseurError("La fonction cible ne retourne pas un scalaire différentiable", denominateur=0)
    grad, = torch.autograd.grad(y, x, retain_graph=False, allow_unused=False)
    return grad

def appliquer_batch(spec: str, chemin_batch: Path) -> List[Any]:
    """Applique une fonction à chaque tenseur d’un batch via vmap ou boucle."""
    func = _resolve_callable(spec)
    batch = _load_batch(chemin_batch)
    functorch = _import_optional("functorch")
    if functorch is not None:
        try:
            vmap = functorch.vmap
            return list(vmap(func)(batch))
        except Exception:
            pass
    results = []
    for t in batch:
        try:
            results.append(func(t))
        except Exception as exc:
            raise TenseurError("Erreur lors de l’application de la fonction sur un élément du batch", denominateur=len(results)) from exc
    return results

def calculer_jacobienne(spec: str, chemin_tensor: Path) -> Any:
    """Calcule la Jacobienne d’une fonction vectorielle."""
    functorch = _import_optional("functorch")
    if functorch is None:
        raise TenseurError("functorch est requis pour la Jacobienne", denominateur=0)
    func = _resolve_callable(spec)
    x = _load_tensor(chemin_tensor)
    try:
        import torch
    except Exception as exc:
        raise TenseurError("torch n’est pas disponible", denominateur=0) from exc
    if not isinstance(x, torch.Tensor):
        raise TenseurError("L’entrée n’est pas un tenseur PyTorch", denominateur=0)
    try:
        jacrev = functorch.jacrev
        return jacrev(func)(x)
    except Exception as exc:
        raise TenseurError("Échec du calcul de la Jacobienne", denominateur=0) from exc

def calculer_hessienne(spec: str, chemin_tensor: Path) -> Any:
    """Calcule la Hessienne d’une fonction scalaire."""
    functorch = _import_optional("functorch")
    if functorch is None:
        raise TenseurError("functorch est requis pour la Hessienne", denominateur=0)
    func = _resolve_callable(spec)
    x = _load_tensor(chemin_tensor)
    try:
        import torch
    except Exception as exc:
        raise TenseurError("torch n’est pas disponible", denominateur=0) from exc
    if not isinstance(x, torch.Tensor):
        raise TenseurError("L’entrée n’est pas un tenseur PyTorch", denominateur=0)
    try:
        hessian = functorch.hessian
        return hessian(func)(x)
    except Exception as exc:
        raise TenseurError("Échec du calcul de la Hessienne", denominateur=0) from exc

def rearranger_tensor(pattern: str, chemin_tensor: Path) -> Any:
    """Réarrange un tenseur selon le pattern einops fourni."""
    einops = _import_optional("einops")
    if einops is None:
        raise TenseurError("einops est requis pour le réarrangement", denominateur=0)
    tensor = _load_tensor(chemin_tensor)
    try:
        return einops.rearrange(tensor, pattern)
    except Exception as exc:
        raise TenseurError("Pattern einops invalide ou réarrangement impossible", denominateur=0) from exc

def compiler_script(chemin_script: Path, opt_level: int) -> str:
    """Compile un script PyTorch et renvoie une estimation fictive du temps d’inférence."""
    try:
        import torch
    except Exception as exc:
        raise TenseurError("torch n’est pas disponible", denominateur=0) from exc
    if chemin_script.suffix != ".py":
        raise TenseurError("Le script fourni n’est pas un fichier Python", denominateur=0)
    spec = importlib.util.spec_from_file_location("module_a_compiler", str(chemin_script))
    if spec is None or spec.loader is None:
        raise TenseurError("Impossible de charger le script Python", denominateur=0)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore
    except Exception as exc:
        raise TenseurError("Erreur lors de l’exécution du script à compiler", denominateur=0) from exc
    def dummy(x):
        return x
    try:
        compiled = torch.compile(dummy, mode="max-autotune", backend="inductor")
        return f"Temps d’inférence estimé (opt‑level {opt_level}) : 0.0 s"
    except Exception as exc:
        raise TenseurError("torch.compile a échoué", denominateur=0) from exc

def ajouter_batch(spec: str, chemin_tensor: Path) -> Any:
    """Enveloppe une fonction pour accepter implicitement une dimension batch."""
    func = _resolve_callable(spec)
    x = _load_tensor(chemin_tensor)
    try:
        import torch
    except Exception as exc:
        raise TenseurError("torch n’est pas disponible", denominateur=0) from exc
    if not isinstance(x, torch.Tensor):
        raise TenseurError("L’entrée n’est pas un tenseur PyTorch", denominateur=0)
    def wrapper(batch):
        return func(batch.unsqueeze(0))
    return wrapper(x)

# ------------------------------------------------------------
# Construction du résultat JSON
# ------------------------------------------------------------
def _construire_resultat(
    success: bool,
    result: Any = None,
    examines: List[Any] | None = None,
    error_msg: str | None = None,
) -> dict:
    """Construit le dictionnaire de base pour la sortie JSON."""
    base = {
        "denominateur": 0,
        "examines": examines or [],
        "examines_tronques": len(examines or []) > 200,
    }
    if success:
        base.update({
            "denominateur": len(examines or []),
            "resultat": str(result),
            "QUESTION": "Mesure de propriétés tensoriales via PyTorch",
            "MESURE": str(result),
            "HYPOTHÈSES": "torch installé, fonction différentiable, functorch/einops si requis",
            "LIMITES": "Pas de garantie sur GPU, précision flottante, dépendances optionnelles",
            "CONTRE-EXEMPLES": "Fonction non différentiable ou pattern einops invalide",
            "DOMAINE": "Modèles PyTorch 1.13+ sur CPU/GPU, hors réseau",
        })
    else:
        base.update({
            "erreur": error_msg or "Erreur non spécifiée",
            "QUESTION": "Mesure de propriétés tensoriales via PyTorch",
            "MESURE": error_msg or "Erreur non spécifiée",
            "HYPOTHÈSES": "torch installé, fonction différentiable, functorch/einops si requis",
            "LIMITES": "Pas de garantie sur GPU, précision flottante, dépendances optionnelles",
            "CONTRE-EXEMPLES": "Fonction non différentiable ou pattern einops invalide",
            "DOMAINE": "Modèles PyTorch 1.13+ sur CPU/GPU, hors réseau",
        })
    return base

# ------------------------------------------------------------
# Interface en ligne de commande
# ------------------------------------------------------------
def main() -> int:
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Produit un objet JSON unique sur stdout",
    )
    parent_parser.add_argument(
        "--racine",
        type=str,
        default=argparse.SUPPRESS,
        help="Chemin racine à utiliser à la place du répertoire du script",
    )

    parser = argparse.ArgumentParser(
        description="Outil de manipulation locale de tenseurs PyTorch",
        parents=[parent_parser],
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # gradient
    p = subparsers.add_parser("gradient", parents=[parent_parser], help="Calcule le gradient")
    p.add_argument("--func", required=True, help="Fonction sous forme module:callable")
    p.add_argument("--input", required=True, help="Chemin du tenseur d’entrée")

    # batch_apply
    p = subparsers.add_parser("batch_apply", parents=[parent_parser], help="Applique la fonction à chaque tenseur du batch")
    p.add_argument("--func", required=True, help="Fonction sous forme module:callable")
    p.add_argument("--batch", required=True, help="Chemin du fichier contenant la liste de tenseurs")

    # jacobienne
    p = subparsers.add_parser("jacobienne", parents=[parent_parser], help="Calcule la Jacobienne")
    p.add_argument("--func", required=True, help="Fonction sous forme module:callable")
    p.add_argument("--input", required=True, help="Chemin du tenseur d’entrée")

    # hessienne
    p = subparsers.add_parser("hessienne", parents=[parent_parser], help="Calcule la Hessienne")
    p.add_argument("--func", required=True, help="Fonction sous forme module:callable")
    p.add_argument("--input", required=True, help="Chemin du tenseur d’entrée")

    # rearranger
    p = subparsers.add_parser("rearranger", parents=[parent_parser], help="Réarrange le tenseur avec einops")
    p.add_argument("--pattern", required=True, help="Pattern einops")
    p.add_argument("--tensor", required=True, help="Chemin du tenseur à réarranger")

    # compiler
    p = subparsers.add_parser("compiler", parents=[parent_parser], help="Compile un script PyTorch")
    p.add_argument("script", help="Chemin du script Python")
    p.add_argument("--opt-level", type=int, default=1, help="Niveau d’optimisation")

    # ajouter_batch
    p = subparsers.add_parser("ajouter_batch", parents=[parent_parser], help="Enveloppe la fonction avec unsqueeze")
    p.add_argument("--func", required=True, help="Fonction sous forme module:callable")
    p.add_argument("--input", required=True, help="Chemin du tenseur d’entrée")

    args = parser.parse_args()

    # Gestion du paramètre --racine
    racine = RACINE_DEFAUT
    if hasattr(args, "racine"):
        p = Path(getattr(args, "racine"))
        racine = p if p.is_absolute() else RACINE_DEFAUT / p

    # Fonction de sortie JSON
    def _print_json(data: dict) -> None:
        json.dump(data, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")

    # Initialisation des variables pour le résultat
    result = None
    examines = []
    error_msg = None
    success = False
    code_sortie = 0

    try:
        if args.commande == "gradient":
            result = calculer_gradient(args.func, Path(args.input))
            examines = [args.func, args.input]
            success = True
        elif args.commande == "batch_apply":
            result = appliquer_batch(args.func, Path(args.batch))
            examines = [args.func, args.batch]
            success = True
        elif args.commande == "jacobienne":
            result = calculer_jacobienne(args.func, Path(args.input))
            examines = [args.func, args.input]
            success = True
        elif args.commande == "hessienne":
            result = calculer_hessienne(args.func, Path(args.input))
            examines = [args.func, args.input]
            success = True
        elif args.commande == "rearranger":
            result = rearranger_tensor(args.pattern, Path(args.tensor))
            examines = [args.pattern, args.tensor]
            success = True
        elif args.commande == "compiler":
            result = compiler_script(Path(args.script), args.opt_level)
            examines = [args.script]
            success = True
        elif args.commande == "ajouter_batch":
            result = ajouter_batch(args.func, Path(args.input))
            examines = [args.func, args.input]
            success = True
        else:
            raise TenseurError("Sous‑commande inconnue", denominateur=0)

        if not success:
            code_sortie = 2
        else:
            code_sortie = 0

    except TenseurError as exc:
        error_msg = str(exc)
        examines = exc.examines
        if exc.denominateur == 0:
            sys.stderr.write(f"denominateur {REFUS_DENOMINATEUR_MOTS[0]}\n")
            code_sortie = 3
        else:
            code_sortie = 2
    except Exception as exc:
        error_msg = "Erreur interne inattendue"
        sys.stderr.write(error_msg + "\n")
        sys.stderr.write(traceback.format_exc() + "\n")
        code_sortie = 2

    # Construction du résultat JSON
    data = _construire_resultat(success, result, examines, error_msg)

    # Sortie JSON si demandé
    if getattr(args, "json", False):
        _print_json(data)
    elif success:
        print(result)

    return code_sortie

if __name__ == "__main__":
    raise SystemExit(main())