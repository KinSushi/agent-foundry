# Socle — obligatoire pour tout outil de cette boîte

Chaque règle vient d'un fait **mesuré sur cette machine**, pas d'une préférence.

## 1. stdlib pure

Aucun `import` hors bibliothèque standard. Aucun `pip install`.

Motif mesuré : sur 188 paquets installés, 50 portent un binaire natif et **35
sont bloqués** par la politique de contrôle d'application (WinError 4551). Les
138 autres n'ont aucun binaire — une dépendance absente ne peut pas être cassée
par une politique système qu'on n'a pas choisie.

## 2. Encodage en tête

Mesuré : la console Windows par défaut est en cp1252 et fait mourir tout script
qui imprime un accent.

```python
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
```

## 3. stdout ne porte que le résultat

Mesuré : une bibliothèque qui écrit un avertissement sur stdout rend invalide
tout JSON redirigé. Diagnostics, progression et avertissements vont sur
**stderr**.

## 4. Deux sorties

- par défaut : lisible par un humain ;
- `--json` : un seul objet JSON sur stdout, rien d'autre.

## 5. Code de sortie signifiant

`0` = rien à signaler. **Non nul** = défaut trouvé. L'outil doit s'enchaîner
sans être relu par un humain.

## 6. Racine portable — l'outil ne connaît aucun projet

```python
from pathlib import Path
RACINE = Path(__file__).resolve().parent
```

plus un `--racine` qui la surcharge. **Aucun chemin absolu en dur.** L'outil doit
fonctionner appelé depuis n'importe quel projet, par son chemin absolu.

## 7. Forme

- Python 3.14, `from __future__ import annotations`, annotations de type.
- `argparse`, aide en français, avec un exemple d'appel réel.
- Docstring en tête disant **pourquoi l'outil existe**, avec le fait mesuré.
- Noms de fonctions en français, fonctions courtes.
- Aucun état global ; tout passe par les arguments.
- `if __name__ == "__main__": raise SystemExit(main())`.


## 8. Déclarer le contrat de mesure — imposé par audit tiers

Tout outil expose, dans son docstring et dans sa sortie `--json` :

```
QUESTION      ce qu'on veut savoir
MESURE        ce que l'instrument observe réellement
HYPOTHÈSES    ce qui doit être vrai pour que la mesure vaille
LIMITES       ce que l'outil ne voit pas
CONTRE-EXEMPLES un cas connu où il se trompe
DOMAINE       où sa conclusion s'applique
```

**Motif** : le projet a rencontré neuf fois le même défaut — *bonne mesure,
mauvaise propriété, bonne conclusion sur la mauvaise question*. L'écart entre
**la propriété mesurée** et **la décision visée** doit être écrit, pas supposé.

---

## 9. Déclarer comment on l'exerce — `INVOCATION`

Ajouté le 8 septembre 2026, sur une mesure.

Le juge de livraison copie l'outil **seul** dans un dossier temporaire vide, y
dépose un `valide.py` de trois lignes, et **devine** comment l'appeler. Pour la
plupart des outils il devine mal : il passe un chemin à un outil qui attend deux
arbres, un nom de module, ou aucun positionnel du tout.

**Mesure** : sur 61 outils non livrables, **39 n'échouaient que sur F4** —
« dénominateur nul ». Ces outils publiaient bien leur dénominateur ; il valait
zéro parce qu'ils n'avaient rien à examiner.

| outil | tel que le juge l'appelait | correctement invoqué |
| --- | --- | --- |
| `comparer_arbres` | code 2, dénominateur 0 | code 0, **dénominateur 1** |
| `auditeur_permissions` | code 2, dénominateur 0 | code 1, **dénominateur 2** |
| `cache_distribue` | code 2, dénominateur 0 | code 0, **dénominateur 1** |

⇒ **Un septième intitulé, facultatif, entre CONTRE-EXEMPLES et DOMAINE :**

```
INVOCATION
    {outil} {dossier}/a {dossier}/b --json
```

Substitutions, et elles seules :

| jeton | ce qu'il devient |
| --- | --- |
| `{outil}` | le chemin de la copie de l'outil dans le bac |
| `{fichier}` | un fichier Python valide de trois lignes, déjà déposé |
| `{dossier}` | le bac temporaire — il ne contient que l'outil et `{fichier}` |
| `{racine}` | synonyme de `{dossier}` |

Un chemin écrit sous `{dossier}/…` est **créé avant l'essai** : un nom portant
un point devient un fichier copiant `{fichier}`, un nom sans point devient un
dossier. C'est ainsi qu'un outil comparant deux arbres se déclare éprouvable.

### ⚠️ Ce que cette déclaration n'achète PAS

- La commande déclarée doit rendre un **JSON valide** portant un `denominateur`
  **entier et non nul**. Sinon elle ne compte pas, et le juge reprend ses
  variantes devinées.
- Les contrôles **reverse R1..R7 gardent leurs propres entrées hostiles** : ils
  ne lisent jamais l'`INVOCATION`.
- Le contrôle **F5 d'ancrage** garde ses marqueurs plantés : un outil qui
  déclare une invocation et fabrique sa sortie est toujours pris.
- Un chemin déclaré **hors du bac** est refusé.

**L'outil choisit COMMENT on l'exerce, jamais SUR QUOI on le juge.**

---

## 10. La clé `denominateur` est toujours présente

Corollaire du §9, et il vaut seul.

`denominateur` doit figurer dans la sortie `--json` **même quand il n'y a rien à
examiner**. Il vaut alors `0`, l'outil écrit son refus sur stderr et rend le
code **3**.

Une clé **absente** est le zéro silencieux — le défaut que ce dépôt a payé trois
fois : `trace` déclarant mortes trois fonctions vives, `doctest` trouvant
« 0 exemple », et le détecteur de fuites aveugle du 8 septembre.
