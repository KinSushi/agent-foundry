# Brief commun — écrire des outils Agent Foundry (lot de 5)

Dépôt : /home/user/agent-foundry (NE PAS commit, NE PAS push, NE PAS toucher README.md,
pyproject.toml, docs/, mesures/, Dockerfile, .github/ ni aucun outil existant).
Tu écris UNIQUEMENT tes 5 fichiers `outils/<nom>.py` (noms imposés ci-dessous).

Interpréteur de référence (celui du juge, PROPRE, sans bibliothèque tierce) :
  PY=/root/.local/share/uv/python/cpython-3.14.7-linux-x86_64-gnu/bin/python3.14
NE JAMAIS faire `pip install` dans cet interpréteur. Pour éprouver le chemin optionnel
d'une bibliothèque tierce, crée un venv à toi :
  /usr/local/bin/uv venv /tmp/claude-0/-home-user-agent-foundry/d483530f-dd05-5a46-817d-e51f0a1d2fc4/scratchpad/venv_<lot> --python 3.14.7
  /usr/local/bin/uv pip install --python <venv>/bin/python <bibliotheque>
(si l'installation échoue ou prend > 3 min, abandonne ce chemin et DIS-LE dans LIMITES
et dans ton rapport : « chemin <lib> non éprouvé »).

## À lire avant d'écrire (obligatoire)
1. /home/user/agent-foundry/SOCLE_OUTILS.md — le socle, règles 1 à 10. Non négociable.
2. /home/user/agent-foundry/outils/deviner_encodage.py et outils/resoudre_noms.py — deux
   outils LIVRABLES qui utilisent une bibliothèque tierce optionnelle avec repli stdlib.

## Exigences, chacune vérifiée par une machine
- Python 3.14, `from __future__ import annotations`, annotations de type, noms de
  fonctions en français, fonctions courtes, aucun état global mutable.
- En tête, juste après les imports :
      if hasattr(sys.stdout, "reconfigure"):
          sys.stdout.reconfigure(encoding="utf-8")
- `RACINE = Path(__file__).resolve().parent` + option `--racine` ; option `--json`.
- Fin de fichier exactement : `if __name__ == "__main__":\n    raise SystemExit(main())`
- Bibliothèque tierce : import PROTÉGÉ (try/except ImportError) ; si absente, UNE ligne
  sur stderr disant laquelle manque et quel mode dégradé est pris. Le repli stdlib doit
  FAIRE LE TRAVAIL (pas un stub qui refuse). Le JSON indique `"moteur"` : nom de la
  bibliothèque utilisée ou "stdlib".
- stdout ne porte QUE le résultat. `print` vers stdout seulement dans `main` ou des
  fonctions préfixées `afficher`/`presenter`/`imprimer` (contrôle P9).
- Aucune fonction morte (P10) ; si une fonction publique n'est appelée nulle part, déclare
  `__all__`. Aucun chemin absolu en dur (P6) : pas de "/home/", "/usr/", "/mnt/", "C:/".
- Arguments nommés des fonctions stdlib : uniquement ceux qui existent (P15).
- Sortie `--json` : UN objet JSON, avec TOUJOURS `denominateur` (int = nombre d'éléments
  réellement examinés) et `examines` (liste de leurs noms, tronquée si longue).
- Codes de sortie : 0 rien à signaler ; 1 défaut trouvé ; 2 entrée invalide/usage ;
  3 rien à examiner (dénominateur nul) — dans ce cas écrire sur stderr une phrase qui
  contient « dénominateur nul » ET « rien à examiner ».
- Entrées hostiles (le juge les essaie) : chemin inexistant, dossier à la place d'un
  fichier, fichier binaire nommé .py, argument inconnu → code NON NUL, message sur
  stderr, JAMAIS de traceback. Attrape les exceptions attendues, pas `except Exception`
  qui masque tout sans le dire.
- Isolation (le juge pose un mouchard) : ne lire QUE les chemins donnés en argument ;
  n'écrire QUE dans un chemin de sortie explicitement donné ; AUCUN accès réseau, même
  optionnel par défaut (si un outil PEUT aller sur le réseau, ce doit être derrière une
  option explicite désactivée par défaut). Ne lire aucun fichier de config utilisateur
  (~/.config, /etc, ...).
- Docstring de module, intitulés EXACTS en majuscules, chacun en début de ligne :
      QUESTION / MESURE / HYPOTHÈSES / LIMITES / CONTRE-EXEMPLES / INVOCATION / DOMAINE
  Avant QUESTION, 1 à 3 phrases : pourquoi l'outil existe, avec un FAIT MESURÉ PAR TOI
  dans cette session (commande + résultat chiffré). INTERDIT d'inventer un chiffre, une
  date ou une citation : si tu n'as pas mesuré, n'écris pas de chiffre.
  CONTRE-EXEMPLES : un cas réel où l'outil se trompe, que tu as constaté.
  INVOCATION : une ligne de commande que le juge rejouera, avec les jetons {outil},
  {fichier} (un .py valide de trois lignes + docstring à contrat), {dossier} (le bac,
  qui contient l'outil, {fichier}, un sous-dossier et deux .py). Elle DOIT rendre un JSON
  avec `denominateur` entier > 0 dans ce bac. Exemples :
      {outil} {fichier} --json
      {outil} {dossier} --json
      {outil} "3 km" --vers m --json        (argument non-chemin : permis)
  Si l'outil traite un format absent du bac (xlsx, wav, ics...), fais-le accepter AUSSI
  un dossier (il examine les fichiers pertinents qu'il y trouve) ou un texte en ligne ;
  sinon, dans le bac, il refusera proprement (code 3) — acceptable mais moins prouvé.
- argparse : description en français + `epilog` avec un exemple d'appel réel.

## Ce que l'outil doit apporter
Une capacité qu'un agent IA n'a pas, ou qu'il exerce mal, au service de la PRODUCTION
(code, données, documents, sécurité, exploitation). Mesurer, refuser de conclure sur
rien, dire ce qui a été examiné. Qualité « production » : cas limites, Unicode, gros
fichiers (lecture bornée ou en flux), messages d'erreur utiles.

## Validation obligatoire (avant de rendre)
Pour CHACUN de tes outils :
  cd /home/user/agent-foundry
  $PY mesures/porte_qualite.py outils/<nom>.py          → doit afficher CONFORME, code 0
  (cd /tmp && $PY /home/user/agent-foundry/mesures/test_isolation.py \
      /home/user/agent-foundry/outils/<nom>.py --detail)   → doit afficher LIVRABLE
  (le code de sortie du juge seul peut valoir 3 si F3 est un refus légitime : regarde le
   VERDICT affiché ; vise un F3 REUSSI via une INVOCATION qui marche.)
  Pour avoir le détail : ajoute `--json` au juge et lis details[].verdict/explication.
Puis essaie l'outil sur 2-3 cas réels (dont un cas limite) et vérifie que la réponse est
JUSTE, pas seulement qu'elle a la bonne forme. Si une bibliothèque optionnelle s'installe,
vérifie que les deux moteurs donnent des résultats cohérents sur le même cas.

## Rapport final (ton dernier message, concis, en français)
Pour chaque outil : nom ; question en une ligne ; bibliothèques optionnelles (et modules
stdlib notables) ; chemin optionnel éprouvé ? (oui + version / non + raison) ; porte
(CONFORME x/y) ; juge (verdict + F3 REUSSI/INAPPLICABLE) ; une vérification de justesse
faite (entrée → sortie attendue → sortie obtenue). Signale tout ce qui reste fragile.
