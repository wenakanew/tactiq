from __future__ import annotations

from tactiq.domain.models import Insight

LANGUAGES = ("en-GB", "sw-KE", "fr-FR", "es-ES", "pt-BR", "ar-SA", "hi-IN")

# Reviewed domain fixtures can replace these templates without changing evidence.
COPY = {
    "en-GB": {
        "prefix": "", "window": "Window", "focus": "Player focus",
        "analysis_recap": "Analysis Recap",
        "no_player": "No supported contribution from {player} was found in this insight window.",
        "evidence_check": "Evidence Check",
        "insufficient": "More reliable evidence is needed before making a tactical claim.",
        "secondary": "The supporting measurements are available for this time window.",
        "sustained_pressure": ("Pressure Building", "{team} are sustaining attacking pressure through progression, entries and shots."),
        "rhythm_shift": ("Momentum Shift", "{team} are progressing during a higher-intensity phase."),
        "player_influence": ("Player Influence Rising", "Player {player} contributed most of the team's progression and helped create a shot."),
        "player_speed_burst": ("Speed Burst Detected", "The evidence window contains high-speed movement samples for {team}."),
        "turning_point": ("Turning Point", "Progression or event intensity changed between the two measured windows."),
        "transition_attack": ("Fast Transition", "{team} advanced quickly after regaining possession."),
        "local_overload": ("Local Numerical Overload", "{team} have more observed players near the ball in this channel."),
        "pressing_trap": ("Pressing Convergence", "Several defenders converged near the ball before possession changed."),
        "post_match_recap": ("Post-Match Recap", "These chapters collect the strongest supported moments from the match."),
        "other": ("Match Insight", "A measurable pattern was identified in the selected time window."),
    },
    "sw-KE": {
        "prefix": "Muktadha wa mechi: ", "window": "Kipindi", "focus": "Mchezaji",
        "analysis_recap": "Muhtasari wa uchambuzi",
        "no_player": "Hakuna mchango wa {player} uliothibitishwa katika kipindi hiki.",
        "evidence_check": "Ukaguzi wa ushahidi",
        "insufficient": "Ushahidi wa kuaminika zaidi unahitajika kabla ya kutoa hitimisho la mbinu.",
        "secondary": "Vipimo vinavyounga mkono uchambuzi vinapatikana kwa kipindi hiki.",
        "sustained_pressure": ("Shinikizo linaongezeka", "{team} wanaendeleza shinikizo la mashambulizi kwa kusonga mbele, kuingia eneo la mwisho na kupiga mashuti."),
        "rhythm_shift": ("Mabadiliko ya kasi", "{team} wanasonga mbele wakati kasi ya matukio imeongezeka."),
        "player_influence": ("Mchango wa mchezaji", "Mchezaji {player} amechangia sehemu kubwa ya kusonga mbele na kusaidia kutengeneza shuti."),
        "player_speed_burst": ("Kasi ya juu imeonekana", "Vipimo vya kipindi hiki vinaonyesha mwendo wa kasi kwa {team}."),
        "turning_point": ("Mabadiliko muhimu", "Kusonga mbele au kasi ya matukio imebadilika kati ya vipindi viwili vilivyopimwa."),
        "transition_attack": ("Shambulizi la mpito", "{team} walisonga mbele haraka baada ya kurejesha umiliki."),
        "local_overload": ("Faida ya idadi", "{team} wana wachezaji wengi zaidi walioonekana karibu na mpira katika eneo hili."),
        "pressing_trap": ("Muunganiko wa shinikizo", "Mabeki kadhaa walikusanyika karibu na mpira kabla umiliki haujabadilika."),
        "post_match_recap": ("Muhtasari wa mechi", "Sura hizi zinakusanya matukio muhimu yanayoungwa mkono na ushahidi."),
        "other": ("Uchambuzi wa mechi", "Mpangilio unaoweza kupimwa umetambuliwa katika kipindi kilichochaguliwa."),
    },
    "fr-FR": {
        "prefix": "", "window": "Fenêtre", "focus": "Joueur",
        "analysis_recap": "Bilan de l'analyse",
        "no_player": "Aucune contribution étayée de {player} n'a été trouvée dans cette fenêtre.",
        "evidence_check": "Vérification des preuves",
        "insufficient": "Des preuves plus fiables sont nécessaires avant de conclure sur la tactique.",
        "secondary": "Les mesures justificatives sont disponibles pour cette fenêtre.",
        "sustained_pressure": ("Pression soutenue", "{team} maintient une pression offensive grâce à la progression, aux entrées et aux tirs."),
        "rhythm_shift": ("Changement de rythme", "{team} progresse pendant une phase plus intense."),
        "player_influence": ("Influence du joueur", "Le joueur {player} a contribué à la majorité de la progression et à la création d'un tir."),
        "player_speed_burst": ("Pointe de vitesse", "La fenêtre contient des mesures de déplacement rapide pour {team}."),
        "turning_point": ("Moment charnière", "La progression ou l'intensité des événements a changé entre les fenêtres mesurées."),
        "transition_attack": ("Transition rapide", "{team} a progressé rapidement après avoir récupéré le ballon."),
        "local_overload": ("Supériorité numérique locale", "{team} dispose de plus de joueurs observés près du ballon dans ce couloir."),
        "pressing_trap": ("Convergence du pressing", "Plusieurs défenseurs ont convergé vers le ballon avant le changement de possession."),
        "post_match_recap": ("Résumé du match", "Ces chapitres rassemblent les moments les mieux étayés du match."),
        "other": ("Analyse du match", "Une tendance mesurable a été identifiée dans la fenêtre sélectionnée."),
    },
    "es-ES": {
        "prefix": "", "window": "Intervalo", "focus": "Jugador",
        "analysis_recap": "Resumen del análisis",
        "no_player": "No se encontró una contribución respaldada de {player} en este intervalo.",
        "evidence_check": "Comprobación de evidencia",
        "insufficient": "Se necesitan pruebas más fiables antes de sacar una conclusión táctica.",
        "secondary": "Las mediciones de apoyo están disponibles para este intervalo.",
        "sustained_pressure": ("Presión sostenida", "{team} mantiene la presión ofensiva mediante progresiones, entradas y tiros."),
        "rhythm_shift": ("Cambio de ritmo", "{team} progresa durante una fase de mayor intensidad."),
        "player_influence": ("Influencia del jugador", "El jugador {player} aportó la mayor parte de la progresión y ayudó a crear un tiro."),
        "player_speed_burst": ("Punta de velocidad", "El intervalo contiene muestras de desplazamiento rápido de {team}."),
        "turning_point": ("Punto de inflexión", "La progresión o la intensidad cambió entre los dos intervalos medidos."),
        "transition_attack": ("Transición rápida", "{team} avanzó rápidamente después de recuperar la posesión."),
        "local_overload": ("Superioridad numérica local", "{team} tiene más jugadores observados cerca del balón en este carril."),
        "pressing_trap": ("Convergencia de presión", "Varios defensores se acercaron al balón antes del cambio de posesión."),
        "post_match_recap": ("Resumen del partido", "Estos capítulos reúnen los momentos mejor respaldados del partido."),
        "other": ("Análisis del partido", "Se identificó un patrón medible en el intervalo seleccionado."),
    },
    "pt-BR": {
        "prefix": "", "window": "Intervalo", "focus": "Jogador",
        "analysis_recap": "Resumo da análise",
        "no_player": "Não foi encontrada uma contribuição comprovada de {player} neste intervalo.",
        "evidence_check": "Verificação de evidências",
        "insufficient": "São necessárias evidências mais confiáveis antes de uma conclusão tática.",
        "secondary": "As medições de apoio estão disponíveis para este intervalo.",
        "sustained_pressure": ("Pressão sustentada", "{team} mantém a pressão ofensiva com progressões, entradas e finalizações."),
        "rhythm_shift": ("Mudança de ritmo", "{team} progride durante uma fase de maior intensidade."),
        "player_influence": ("Influência do jogador", "O jogador {player} participou da maior parte da progressão e ajudou a criar uma finalização."),
        "player_speed_burst": ("Pico de velocidade", "O intervalo contém amostras de deslocamento rápido de {team}."),
        "turning_point": ("Ponto de virada", "A progressão ou a intensidade mudou entre os dois intervalos medidos."),
        "transition_attack": ("Transição rápida", "{team} avançou rapidamente após recuperar a posse."),
        "local_overload": ("Superioridade numérica local", "{team} tem mais jogadores observados perto da bola neste corredor."),
        "pressing_trap": ("Convergência de pressão", "Vários defensores se aproximaram da bola antes da mudança de posse."),
        "post_match_recap": ("Resumo da partida", "Estes capítulos reúnem os momentos mais bem sustentados da partida."),
        "other": ("Análise da partida", "Um padrão mensurável foi identificado no intervalo selecionado."),
    },
    "ar-SA": {
        "prefix": "", "window": "الفترة", "focus": "اللاعب",
        "analysis_recap": "ملخص التحليل",
        "no_player": "لم يتم العثور على مساهمة مدعومة بالأدلة للاعب {player} في هذه الفترة.",
        "evidence_check": "التحقق من الأدلة",
        "insufficient": "نحتاج إلى أدلة أكثر موثوقية قبل إصدار استنتاج تكتيكي.",
        "secondary": "القياسات الداعمة متاحة لهذه الفترة الزمنية.",
        "sustained_pressure": ("ضغط هجومي مستمر", "{team} يواصل الضغط الهجومي بالتقدم والدخول إلى الثلث الأخير والتسديد."),
        "rhythm_shift": ("تغير إيقاع اللعب", "{team} يتقدم خلال فترة أكثر كثافة."),
        "player_influence": ("تأثير اللاعب", "اللاعب {player} ساهم في معظم التقدم وساعد في صناعة تسديدة."),
        "player_speed_burst": ("سرعة عالية", "تتضمن الفترة قياسات حركة سريعة للاعبي {team}."),
        "turning_point": ("نقطة تحول", "تغير التقدم أو معدل الأحداث بين الفترتين المقاسوَتين."),
        "transition_attack": ("تحول هجومي سريع", "{team} تقدم بسرعة بعد استعادة الاستحواذ."),
        "local_overload": ("تفوق عددي محلي", "{team} لديه عدد أكبر من اللاعبين المرصودين قرب الكرة في هذا الممر."),
        "pressing_trap": ("تقارب الضغط", "تقارب عدة مدافعين نحو الكرة قبل تغير الاستحواذ."),
        "post_match_recap": ("ملخص المباراة", "تجمع هذه الفصول أبرز اللحظات المدعومة بالأدلة."),
        "other": ("تحليل المباراة", "تم تحديد نمط قابل للقياس في الفترة المختارة."),
    },
    "hi-IN": {
        "prefix": "", "window": "अवधि", "focus": "खिलाड़ी",
        "analysis_recap": "विश्लेषण का सारांश",
        "no_player": "इस अवधि में {player} का साक्ष्य-समर्थित योगदान नहीं मिला।",
        "evidence_check": "साक्ष्य की जाँच",
        "insufficient": "सामरिक निष्कर्ष देने से पहले अधिक विश्वसनीय साक्ष्य चाहिए।",
        "secondary": "इस अवधि के लिए सहायक माप उपलब्ध हैं।",
        "sustained_pressure": ("लगातार आक्रामक दबाव", "{team} आगे बढ़ने, अंतिम तिहाई में प्रवेश और शॉट के ज़रिये दबाव बनाए हुए है।"),
        "rhythm_shift": ("खेल की लय में बदलाव", "{team} अधिक तीव्रता वाली अवधि में आगे बढ़ रहा है।"),
        "player_influence": ("खिलाड़ी का प्रभाव", "खिलाड़ी {player} ने टीम की अधिकतर प्रगति में योगदान दिया और शॉट बनाने में मदद की।"),
        "player_speed_burst": ("तेज़ गति", "इस अवधि में {team} के तेज़ गति वाले नमूने दर्ज हैं।"),
        "turning_point": ("महत्वपूर्ण मोड़", "मापी गई दोनों अवधियों के बीच प्रगति या घटनाओं की दर बदली है।"),
        "transition_attack": ("तेज़ आक्रामक बदलाव", "{team} ने गेंद वापस पाने के बाद तेज़ी से आगे बढ़ना शुरू किया।"),
        "local_overload": ("स्थानीय संख्यात्मक बढ़त", "इस क्षेत्र में गेंद के पास {team} के अधिक खिलाड़ी देखे गए हैं।"),
        "pressing_trap": ("दबाव का संकेंद्रण", "गेंद का कब्ज़ा बदलने से पहले कई रक्षक उसके पास आए।"),
        "post_match_recap": ("मैच का सारांश", "ये अध्याय साक्ष्य-समर्थित प्रमुख क्षणों को एकत्र करते हैं।"),
        "other": ("मैच विश्लेषण", "चुनी गई अवधि में मापने योग्य पैटर्न मिला है।"),
    },
}


def language_copy(language: str) -> dict:
    if language not in COPY:
        raise ValueError(f"Unsupported language: {language}")
    return COPY[language]


def localized_insight(insight: Insight, language: str) -> tuple[str, str]:
    copy = language_copy(language)
    team = insight.context.get("team_display_names", {}).get(insight.team_id, insight.team_id.replace("_", " ").title())
    player = insight.player_id or insight.context.get("supported_player_id") or ""
    title, template = copy.get(insight.type, copy["other"])
    if insight.type == "post_match_recap" and insight.context.get("analysis_scope") == "segment":
        title = copy["analysis_recap"]
    if language == "en-GB":
        return title if insight.type == "post_match_recap" else insight.title, insight.summary
    return title, copy["prefix"] + template.format(team=team, player=player)
