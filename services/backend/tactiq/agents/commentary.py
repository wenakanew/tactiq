from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tactiq.agents.grounding import Claim
from tactiq.agents.localization import language_copy, localized_insight
from tactiq.domain.models import Insight


class CommentaryTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["lead", "analyst"]
    text: str = Field(min_length=1, max_length=2000)


class GeneratedDialogue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turns: list[CommentaryTurn] = Field(min_length=2, max_length=6)
    claims: list[Claim] = Field(default_factory=list, max_length=40)
    entities: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def distinct_ordered_roles(self):
        if any(turn.role != ("lead" if index % 2 == 0 else "analyst") for index, turn in enumerate(self.turns)):
            raise ValueError("Commentary must alternate lead and analyst turns, beginning with the lead")
        return self


class GeneratedRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    texts: list[str] = Field(min_length=1, max_length=3)
    claims: list[Claim] = Field(default_factory=list, max_length=30)
    entities: list[str] = Field(default_factory=list, max_length=50)


METRICS = (
    "progressive_actions", "final_third_entries", "shots", "window_passes_completed", "window_carries",
    "window_recoveries", "sampled_frames", "observed_ball_frames", "predicted_ball_frames",
    "observed_player_samples", "ball_observation_rate_pct", "calibrated_samples",
    "forward_progression_m", "chain_actions", "top_player_speed_kmh", "player_progressive_actions",
)
LABELS = {
    "en-GB": "progressive actions|final-third entries|shots|completed passes|carries|recoveries|tracking samples|observed-ball samples|predicted-ball samples|player observations|ball observation coverage|calibrated samples|forward progression|possession actions|peak player speed|player progressive actions",
    "sw-KE": "hatua za kusonga mbele|kuingia theluthi ya mwisho|mashuti|pasi zilizokamilika|kusonga na mpira|urejeshaji wa mpira|sampuli za ufuatiliaji|sampuli zilizoona mpira|sampuli za utabiri wa mpira|uchunguzi wa wachezaji|kiwango cha kuonekana kwa mpira|sampuli zilizosawazishwa|maendeleo ya mbele|hatua za umiliki|kasi ya juu ya mchezaji|hatua za mbele za mchezaji",
    "fr-FR": "actions progressives|entrées dans le dernier tiers|tirs|passes réussies|conduites de balle|récupérations|échantillons de suivi|échantillons avec ballon observé|échantillons avec ballon prédit|observations de joueurs|couverture d'observation du ballon|échantillons calibrés|progression vers l'avant|actions de possession|vitesse maximale du joueur|actions progressives du joueur",
    "es-ES": "acciones progresivas|entradas al último tercio|tiros|pases completados|conducciones|recuperaciones|muestras de seguimiento|muestras con balón observado|muestras con balón predicho|observaciones de jugadores|cobertura de observación del balón|muestras calibradas|avance hacia delante|acciones de posesión|velocidad máxima del jugador|acciones progresivas del jugador",
    "pt-BR": "ações progressivas|entradas no terço final|finalizações|passes completos|conduções|recuperações|amostras de rastreamento|amostras com bola observada|amostras com bola prevista|observações de jogadores|cobertura de observação da bola|amostras calibradas|progressão para a frente|ações de posse|velocidade máxima do jogador|ações progressivas do jogador",
    "ar-SA": "تحركات تقدمية|دخول الثلث الأخير|تسديدات|تمريرات مكتملة|حمل الكرة|استعادة الكرة|عينات التتبع|عينات الكرة المرصودة|عينات الكرة المتوقعة|رصد اللاعبين|تغطية رصد الكرة|عينات معايرة|التقدم للأمام|تحركات الاستحواذ|أعلى سرعة للاعب|تحركات اللاعب التقدمية",
    "hi-IN": "प्रगतिशील गतिविधियाँ|अंतिम तिहाई में प्रवेश|शॉट|पूरे हुए पास|गेंद लेकर आगे बढ़ना|गेंद वापस पाना|ट्रैकिंग नमूने|देखी गई गेंद के नमूने|अनुमानित गेंद के नमूने|खिलाड़ी अवलोकन|गेंद अवलोकन कवरेज|अंशांकित नमूने|आगे की प्रगति|कब्ज़े की गतिविधियाँ|खिलाड़ी की अधिकतम गति|खिलाड़ी की प्रगतिशील गतिविधियाँ",
}
DETAIL = {
    "en-GB": {
        "scope": "This is the selected passage, from {start}s to {end}s, not a verdict on the full match.",
        "measure": "The supporting window records {metrics}. Read those measures together with the replay: they describe this passage, rather than guaranteeing the outcome of the next attack.",
        "sequence": "The recent sequence includes {sequence}. Following the order matters: a contribution in this passage should not be extended into a claim about every phase of the game.",
        "limits": "These are supported patterns, not a prediction of the result. Chance quality and off-camera movement need their own evidence before we can make stronger claims.",
        "simulation": "This is a reproducible simulated scenario with scripted events and generated positions. Its measurements illustrate this sequence, not validation against real football footage. Distances use the scenario's known pitch, not a camera survey.",
        "image": "These are image-space candidates, not surveyed pitch coordinates. Physical speeds, tactical spacing and player identities cannot be established from these detections alone.",
        "overview": "This is an observation overview, not a tactical verdict. It separates detector observations from short predictions, so an uncertain or missing ball is not presented as a verified football action.",
        "calibrated": "Surveyed ground-plane coordinates support these measurements. Predicted positions and missing observations still need to be treated separately from confirmed detections.",
    },
    "sw-KE": {
        "scope": "Hiki ni kipindi kilichochaguliwa, kutoka {start}s hadi {end}s, si hitimisho la mechi nzima.",
        "measure": "Vipimo vya kipindi hiki vinaonyesha {metrics}. Visome pamoja na marudio: vinaeleza kipindi hiki, wala havihakikishi matokeo ya shambulizi linalofuata.",
        "sequence": "Mfuatano wa karibuni unajumuisha {sequence}. Mpangilio ni muhimu: mchango katika kipindi hiki haupaswi kutumiwa kuhitimisha kila hatua ya mechi.",
        "limits": "Hii ni mifumo inayoungwa mkono na ushahidi, si utabiri wa matokeo. Ubora wa nafasi na mwendo nje ya kamera vinahitaji ushahidi wake kabla ya madai makubwa.",
        "simulation": "Huu ni mfano wa kuigwa unaoweza kurudiwa, wenye nafasi za wachezaji na mpira zilizozalishwa. Vipimo vinaeleza mfuatano; si uthibitisho kwa video halisi ya soka.",
        "image": "Hizi ni nafasi zinazokadiriwa kwenye picha, si viwianishi vya uwanja vilivyopimwa. Kasi halisi, nafasi za mbinu na utambulisho wa wachezaji havithibitishwi na utambuzi huu pekee.",
        "overview": "Huu ni muhtasari wa uchunguzi, si hitimisho la mbinu. Unatenganisha utambuzi na utabiri mfupi ili mpira usioonekana au usio na uhakika usidaiwe kuwa tukio lililothibitishwa.",
        "calibrated": "Viwianishi vya uso wa uwanja vilivyopimwa vinaunga mkono vipimo hivi. Nafasi zilizotabiriwa na uchunguzi unaokosekana bado vinahitaji kutenganishwa na utambuzi uliothibitishwa.",
    },
    "fr-FR": {
        "scope": "Il s'agit du passage sélectionné, de {start}s à {end}s, et non d'un verdict sur tout le match.",
        "measure": "La fenêtre étayée enregistre {metrics}. Il faut lire ces mesures avec le replay : elles décrivent ce passage, sans garantir l'issue de l'attaque suivante.",
        "sequence": "La séquence récente comprend {sequence}. L'ordre est important : une contribution dans ce passage ne permet pas de conclure sur toutes les phases du jeu.",
        "limits": "Ces tendances sont étayées, mais ne prédisent pas le résultat. La qualité des occasions et les déplacements hors champ exigent leurs propres preuves.",
        "simulation": "Il s'agit d'un scénario simulé reproductible, avec des positions générées. Ces mesures illustrent la séquence ; elles ne constituent pas une validation sur des images réelles.",
        "image": "Ce sont des candidats dans l'image, pas des coordonnées de terrain mesurées. Ils ne suffisent pas à établir vitesses physiques, distances tactiques ou identités.",
        "overview": "Ceci est un bilan d'observations, pas un verdict tactique. Il distingue détections et courtes prédictions, sans présenter un ballon incertain comme une action vérifiée.",
        "calibrated": "Des coordonnées mesurées du plan au sol étayent ces mesures. Les prédictions et les observations manquantes restent distinctes des détections confirmées.",
    },
    "es-ES": {
        "scope": "Este es el pasaje seleccionado, de {start}s a {end}s, no un veredicto sobre todo el partido.",
        "measure": "El intervalo respaldado registra {metrics}. Estas medidas deben leerse junto al replay: describen este pasaje, sin garantizar el resultado del siguiente ataque.",
        "sequence": "La secuencia reciente incluye {sequence}. El orden importa: una contribución en este pasaje no permite sacar conclusiones sobre todas las fases del juego.",
        "limits": "Son patrones respaldados, no una predicción del resultado. La calidad de las ocasiones y el movimiento fuera de cámara necesitan pruebas propias.",
        "simulation": "Es un escenario simulado reproducible, con posiciones generadas. Sus medidas ilustran la secuencia; no constituyen una validación con imágenes reales de fútbol.",
        "image": "Son candidatos en la imagen, no coordenadas de campo medidas. No bastan para establecer velocidades físicas, distancias tácticas ni identidades.",
        "overview": "Es un resumen de observaciones, no un veredicto táctico. Separa las detecciones de las predicciones cortas y no presenta un balón incierto como una acción verificada.",
        "calibrated": "Las coordenadas medidas del plano del terreno respaldan estas medidas. Las predicciones y las observaciones ausentes deben distinguirse de las detecciones confirmadas.",
    },
    "pt-BR": {
        "scope": "Este é o trecho selecionado, de {start}s a {end}s, não um veredito sobre a partida inteira.",
        "measure": "O intervalo sustentado registra {metrics}. Leia essas medidas com o replay: elas descrevem este trecho, sem garantir o resultado do próximo ataque.",
        "sequence": "A sequência recente inclui {sequence}. A ordem importa: uma contribuição neste trecho não autoriza conclusões sobre todas as fases da partida.",
        "limits": "São padrões sustentados, não uma previsão do resultado. A qualidade das chances e os movimentos fora da câmera exigem evidências próprias.",
        "simulation": "É um cenário simulado reproduzível, com posições geradas. As medidas ilustram a sequência; não representam validação com imagens reais de futebol.",
        "image": "São candidatos na imagem, não coordenadas medidas do campo. Não bastam para estabelecer velocidades físicas, distâncias táticas ou identidades.",
        "overview": "Este é um resumo de observações, não um veredito tático. Separa detecções de previsões curtas sem apresentar uma bola incerta como uma ação verificada.",
        "calibrated": "Coordenadas medidas do plano do campo sustentam estas medidas. Previsões e observações ausentes continuam distintas das detecções confirmadas.",
    },
    "ar-SA": {
        "scope": "هذا هو المقطع المحدد من {start}s إلى {end}s، وليس حكماً على المباراة كاملة.",
        "measure": "تسجل الفترة المدعومة {metrics}. تُقرأ هذه القياسات مع الإعادة؛ فهي تصف هذا المقطع ولا تضمن نتيجة الهجمة التالية.",
        "sequence": "يتضمن التسلسل الأخير {sequence}. ترتيب الأحداث مهم؛ ولا يجوز تعميم مساهمة في هذا المقطع على جميع مراحل اللعب.",
        "limits": "هذه أنماط مدعومة وليست توقعاً للنتيجة. جودة الفرص والحركة خارج الكاميرا تحتاجان أدلتهما الخاصة قبل استنتاجات أقوى.",
        "simulation": "هذا سيناريو محاكاة قابل للتكرار بمواقع مولدة للاعبين والكرة. توضح القياسات التسلسل ولا تمثل تحققاً باستخدام لقطات كرة قدم حقيقية.",
        "image": "هذه مواقع مرشحة في الصورة وليست إحداثيات ملعب مقاسة. لا تكفي وحدها لإثبات السرعات الحقيقية أو المسافات التكتيكية أو هويات اللاعبين.",
        "overview": "هذا ملخص للرصد وليس حكماً تكتيكياً. يميز بين الرصد والتوقعات القصيرة كي لا تُعرض الكرة غير المؤكدة كحدث مثبت.",
        "calibrated": "تدعم إحداثيات مستوى الأرض المقاسة هذه القياسات. يجب الاستمرار في فصل التوقعات والرصد المفقود عن الاكتشافات المؤكدة.",
    },
    "hi-IN": {
        "scope": "यह {start}s से {end}s तक का चुना गया अंश है, पूरे मैच पर निष्कर्ष नहीं।",
        "measure": "समर्थित अवधि में {metrics} दर्ज हैं। इन्हें रीप्ले के साथ पढ़ें: ये इस अंश को बताते हैं, अगले आक्रमण का नतीजा तय नहीं करते।",
        "sequence": "हाल के क्रम में {sequence} शामिल हैं। क्रम महत्वपूर्ण है: इस अंश के योगदान को खेल के हर चरण पर लागू नहीं करना चाहिए।",
        "limits": "ये साक्ष्य-समर्थित पैटर्न हैं, नतीजे की भविष्यवाणी नहीं। अवसर की गुणवत्ता और कैमरे से बाहर की गति के लिए अलग साक्ष्य चाहिए।",
        "simulation": "यह उत्पन्न खिलाड़ी और गेंद की स्थितियों वाला दोहराने योग्य अनुकरण है। माप क्रम समझाते हैं; असली फुटबॉल वीडियो पर सत्यापन नहीं हैं।",
        "image": "ये चित्र में अनुमानित स्थान हैं, मापे गए मैदान निर्देशांक नहीं। इनसे अकेले वास्तविक गति, सामरिक दूरी या खिलाड़ी की पहचान स्थापित नहीं होती।",
        "overview": "यह अवलोकन का सारांश है, सामरिक निष्कर्ष नहीं। यह देखी गई स्थितियों को छोटे अनुमानों से अलग करता है ताकि अनिश्चित गेंद को सत्यापित घटना न कहा जाए।",
        "calibrated": "मैदान के मापे गए समतल निर्देशांक इन मापों का आधार हैं। अनुमानित और अनुपस्थित अवलोकन पुष्ट पहचान से अलग रहते हैं।",
    },
}
ACTION_NAMES = {
    "en-GB": "completed pass|carry|shot|recovery|incomplete pass|goal",
    "sw-KE": "pasi iliyokamilika|kusonga na mpira|shuti|urejeshaji wa mpira|pasi isiyokamilika|bao",
    "fr-FR": "passe réussie|conduite de balle|tir|récupération|passe manquée|but",
    "es-ES": "pase completado|conducción|tiro|recuperación|pase incompleto|gol",
    "pt-BR": "passe completo|condução|finalização|recuperação|passe incompleto|gol",
    "ar-SA": "تمريرة مكتملة|حمل الكرة|تسديدة|استعادة الكرة|تمريرة غير مكتملة|هدف",
    "hi-IN": "पूरा हुआ पास|गेंद लेकर बढ़ना|शॉट|गेंद वापस पाना|अधूरा पास|गोल",
}
SIMULATED_PREFIX = {
    "en-GB": "Simulated passage: ", "sw-KE": "Katika mfano huu wa kuigwa: ",
    "fr-FR": "Séquence simulée : ", "es-ES": "Secuencia simulada: ", "pt-BR": "Sequência simulada: ",
    "ar-SA": "مقطع محاكاة: ", "hi-IN": "अनुकरण का अंश: ",
}


def metric_text(insight: Insight, language: str, *, maximum: int = 4) -> str:
    labels = dict(zip(METRICS, LABELS[language].split("|")))
    return "; ".join(f"{labels.get(item.metric, item.metric)}: {item.value}{' ' + item.unit if item.unit != 'count' else ''}"
                     for item in insight.evidence[:maximum])


def deep_supported(insight: Insight) -> bool:
    if insight.type == "tracking_overview" and not any(
        item.value > 0 for item in insight.evidence if item.metric in {"observed_ball_frames", "observed_player_samples"}
    ):
        return False
    return len(insight.evidence) >= 3 and len({key for item in insight.evidence for key in item.supporting_event_ids}) >= 4


def deterministic_turns(insight: Insight, language: str) -> list[CommentaryTurn]:
    language_copy(language)
    copy = DETAIL[language]
    _, base = localized_insight(insight, language)
    overview = insight.type == "tracking_overview"
    if overview:
        base = copy["overview"]
    if insight.context.get("data_source") == "synthetic":
        base = SIMULATED_PREFIX[language] + base
    metrics = metric_text(insight, language, maximum=3)
    scope = copy["scope"].format(start=insight.window_start_ms // 1000, end=insight.window_end_ms // 1000)
    deep = insight.context.get("depth", "deep") == "deep" and deep_supported(insight)
    if not deep:
        return [CommentaryTurn(role="lead", text=base),
                CommentaryTurn(role="analyst", text=metrics or language_copy(language)["secondary"])]
    source = "simulation" if insight.context.get("data_source") == "synthetic" else (
        "calibrated" if insight.context.get("has_pitch_geometry") else "image")
    names = dict(zip(("pass_completed", "carry", "shot", "recovery", "pass_incomplete", "goal"), ACTION_NAMES[language].split("|")))
    sequence = [names[event["type"]] for event in insight.context.get("phase_events", []) if event["type"] in names][-3:]
    middle = copy["sequence"].format(sequence=", ".join(sequence)) if sequence else (
        copy["overview"] if not overview else copy["limits"])
    return [
        CommentaryTurn(role="lead", text=f"{base} {scope}"),
        CommentaryTurn(role="analyst", text=copy["measure"].format(metrics=metrics)),
        CommentaryTurn(role="lead", text=middle),
        CommentaryTurn(role="analyst", text=copy[source]),
    ]
