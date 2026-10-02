export const meta = {
  name: 'lot-outils-agent-foundry',
  description: 'Écrit (option) puis vérifie de façon adversariale, corrige et contre-vérifie un lot de 5 outils Agent Foundry',
  whenToUse: 'Produire ou éprouver un lot d outils outils/<nom>.py du dépôt agent-foundry',
  phases: [
    { title: 'Écrire', detail: 'un rédacteur écrit les outils du lot (mode ecrire seulement)' },
    { title: 'Vérifier', detail: 'un sceptique indépendant par outil tente de réfuter' },
    { title: 'Corriger', detail: 'un correcteur par outil défaillant' },
    { title: 'Contre-vérifier', detail: 'un second sceptique, frais, après correction' },
  ],
}

const A = args
const PY = '/root/.local/share/uv/python/cpython-3.14.7-linux-x86_64-gnu/bin/python3.14'
const SCRATCH = '/tmp/claude-0/-home-user-agent-foundry/d483530f-dd05-5a46-817d-e51f0a1d2fc4/scratchpad'
const BRIEF = SCRATCH + '/BRIEF_COMMUN.md'

const RAPPORT_OUTIL = {
  type: 'object',
  properties: {
    nom: { type: 'string' },
    question: { type: 'string' },
    bibliotheques: { type: 'string', description: 'optionnelles tierces + modules stdlib notables' },
    chemin_optionnel: { type: 'string', description: 'éprouvé oui + version, ou non + raison' },
    porte: { type: 'string' },
    juge: { type: 'string', description: 'verdict + F3' },
    justesse: { type: 'string', description: 'entrée -> attendu -> obtenu' },
    fragile: { type: 'string' },
  },
  required: ['nom', 'porte', 'juge', 'justesse'],
}
const RAPPORT_ECRITURE = {
  type: 'object',
  properties: { outils: { type: 'array', items: RAPPORT_OUTIL } },
  required: ['outils'],
}
const VERDICT = {
  type: 'object',
  properties: {
    nom: { type: 'string' },
    fichier_present: { type: 'boolean' },
    porte_conforme: { type: 'boolean' },
    juge_verdict: { type: 'string' },
    f3: { type: 'string', description: 'REUSSI, INAPPLICABLE ou ECHEU' },
    cas_eprouves: { type: 'integer' },
    cas_faux: { type: 'integer' },
    defauts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          gravite: { type: 'string', enum: ['P0', 'P1', 'P2', 'P3'] },
          description: { type: 'string' },
          reproduction: { type: 'string' },
        },
        required: ['gravite', 'description', 'reproduction'],
      },
    },
    verdict: { type: 'string', enum: ['ACCEPTE', 'A_CORRIGER', 'REJETE'] },
    resume: { type: 'string' },
  },
  required: ['nom', 'fichier_present', 'porte_conforme', 'juge_verdict', 'cas_eprouves', 'cas_faux', 'defauts', 'verdict', 'resume'],
}

function promptEcriture() {
  const liste = A.outils.map((o, i) => `${i + 1}. ${o.nom} — ${o.consigne}`).join('\n\n')
  return `Tu écris ${A.outils.length} nouveaux outils CLI pour le dépôt Agent Foundry (/home/user/agent-foundry).
Lis d'abord ENTIÈREMENT le brief commun : ${BRIEF} — il est contraignant (socle, validation porte + juge, interdiction d'inventer des chiffres, NE RIEN commit ni push). Ton identifiant de lot pour le venv : lot${A.lot}.

LOT ${A.lot} — ${A.theme}. Fichiers à créer : ${A.outils.map(o => 'outils/' + o.nom + '.py').join(', ')}
Avant d'écrire, lis la docstring des outils existants proches (ls /home/user/agent-foundry/outils) et reste distinct d'eux.

${liste}

Rends, pour chaque outil, le rapport demandé par le brief (champs du schéma).`
}

function promptVerif(o, tour, precedent) {
  const rappel = precedent
    ? `\nCONTEXTE : un premier sceptique avait trouvé ces défauts, puis un correcteur est passé :\n${JSON.stringify(precedent.defauts, null, 1)}\nRapport du correcteur : ${precedent.correction || '(aucun)'}\nVérifie qu'ils sont RÉELLEMENT corrigés (rejoue chaque reproduction) ET cherche des défauts NOUVEAUX : une correction en introduit souvent.`
    : ''
  return `Tu es un vérificateur SCEPTIQUE et indépendant (tour ${tour}). Ta mission : RÉFUTER l'outil /home/user/agent-foundry/outils/${o.nom}.py. Tu ne le modifies PAS (lecture seule) ; tes fichiers d'essai vont uniquement dans ${SCRATCH}/verif_${o.nom}/.
Ce que l'outil est censé faire : ${o.consigne}
Les règles qu'il doit tenir : ${BRIEF} (lis-le).${rappel}

Fais, dans cet ordre, et note chaque résultat :
1. Fichier présent ? Sinon verdict REJETE.
2. Porte : cd /home/user/agent-foundry && ${PY} mesures/porte_qualite.py outils/${o.nom}.py  → CONFORME attendu.
3. Juge : cd /tmp && ${PY} /home/user/agent-foundry/mesures/test_isolation.py /home/user/agent-foundry/outils/${o.nom}.py --json  → lis rapports[0].verdict (LIVRABLE attendu) et details[] (F3, R1..R7). N'utilise JAMAIS d'autre interpréteur pour la porte et le juge, et n'y installe rien.
4. Lis le code EN ENTIER. Cherche : réponse fausse, cas limite non géré, exception non attrapée (traceback), except trop large qui masque, accès réseau ou lecture/écriture hors des chemins donnés, denominateur qui ne compte pas ce qui a été réellement examiné, code de sortie contraire au contrat 0/1/2/3, repli stdlib qui ne fait pas vraiment le travail.
5. Construis au moins 6 cas d'essai : 3 nominaux dont tu calcules la réponse attendue INDÉPENDAMMENT (à la main, ou avec une bibliothèque de référence installée dans un venv à toi : /usr/local/bin/uv venv ${SCRATCH}/venv_verif_${o.nom} --python 3.14.7), 2 cas limites (vide, Unicode, très grand, format inattendu), 1 hostile. Exécute l'outil (interpréteur propre ${PY}) et compare.
6. Docstring : chaque chiffre, date ou « fait mesuré » doit être reproductible ; essaie de le reproduire. Un chiffre que tu ne peux pas reproduire, ou qui paraît inventé, est un défaut P1. Un CONTRE-EXEMPLE doit être réel.
7. Doublon : la QUESTION ne doit pas répéter celle d'un outil existant (grep -A2 QUESTION /home/user/agent-foundry/outils/*.py).

Gravités : P0 = réponse fausse, plantage, fuite, fabrication ; P1 = contrat du socle violé, chiffre non reproductible ; P2 = robustesse ; P3 = style.
Verdict : ACCEPTE seulement si porte CONFORME, juge LIVRABLE, aucun cas faux et aucun défaut P0/P1/P2. Dans le doute : A_CORRIGER. REJETE si l'outil est absent, doublon, ou faux dans son principe.
Chaque défaut doit porter une reproduction exacte (commande + sortie obtenue + sortie attendue).`
}

function promptCorrection(o, v) {
  return `Tu corriges l'outil /home/user/agent-foundry/outils/${o.nom}.py (ce fichier SEULEMENT ; ne commit rien). S'il est absent, écris-le entièrement.
Ce qu'il doit faire : ${o.consigne}
Règles contraignantes : ${BRIEF} (lis-le).
Un vérificateur indépendant a trouvé ces défauts (verdict ${v.verdict}) :
${JSON.stringify(v.defauts, null, 1)}
Résumé : ${v.resume}

Corrige TOUS les défauts P0, P1 et P2 (et P3 s'ils sont simples). Ne supprime pas un chiffre gênant de la docstring pour le remplacer par un autre non mesuré : mesure-le ou retire-le. Puis :
- rejoue chaque reproduction et montre que la sortie est maintenant juste ;
- cd /home/user/agent-foundry && ${PY} mesures/porte_qualite.py outils/${o.nom}.py → CONFORME ;
- cd /tmp && ${PY} /home/user/agent-foundry/mesures/test_isolation.py /home/user/agent-foundry/outils/${o.nom}.py → LIVRABLE.
Rends le rapport (champs du schéma) en disant précisément ce que tu as changé.`
}

let ecriture = null
if (A.mode === 'ecrire') {
  phase('Écrire')
  ecriture = await agent(promptEcriture(), {
    label: `écrire:lot${A.lot}`, phase: 'Écrire', schema: RAPPORT_ECRITURE, agentType: 'general-purpose',
  })
  log(`lot ${A.lot} : rédacteur ${ecriture ? 'terminé' : 'EN ÉCHEC'}`)
}

const resultats = await pipeline(
  A.outils,
  (_, o) => agent(promptVerif(o, 1, null), {
    label: `vérifier:${o.nom}`, phase: 'Vérifier', schema: VERDICT, agentType: 'general-purpose',
  }),
  (v, o) => {
    if (!v) return { nom: o.nom, final: null, etapes: ['vérification morte'] }
    if (v.verdict === 'ACCEPTE') return { nom: o.nom, v1: v, final: v, etapes: ['accepté au tour 1'] }
    return agent(promptCorrection(o, v), {
      label: `corriger:${o.nom}`, phase: 'Corriger', schema: RAPPORT_OUTIL, agentType: 'general-purpose',
    }).then(c => ({ nom: o.nom, v1: v, correction: c, etapes: ['corrigé'] }))
  },
  (r, o) => {
    if (!r || r.final !== undefined) return r
    const precedent = { defauts: r.v1.defauts, correction: r.correction ? JSON.stringify(r.correction) : '' }
    return agent(promptVerif(o, 2, precedent), {
      label: `contre-vérifier:${o.nom}`, phase: 'Contre-vérifier', schema: VERDICT, agentType: 'general-purpose',
    }).then(v2 => ({ ...r, v2, final: v2 }))
  },
)

const bilan = resultats.map((r, i) => {
  const nom = A.outils[i].nom
  if (!r) return { nom, verdict: 'NON_VERIFIE', defauts_restants: [] }
  const f = r.final
  return {
    nom,
    verdict: f ? f.verdict : 'NON_VERIFIE',
    porte_conforme: f ? f.porte_conforme : null,
    juge: f ? f.juge_verdict : null,
    f3: f ? f.f3 : null,
    cas: f ? `${f.cas_eprouves - f.cas_faux}/${f.cas_eprouves} justes` : null,
    defauts_tour1: r.v1 ? r.v1.defauts.length : null,
    defauts_restants: f ? f.defauts.filter(d => d.gravite !== 'P3') : [],
    resume: f ? f.resume : '',
  }
})
log(`lot ${A.lot} : ${bilan.filter(b => b.verdict === 'ACCEPTE').length}/${bilan.length} acceptés`)
return { lot: A.lot, mode: A.mode, ecriture, bilan }
