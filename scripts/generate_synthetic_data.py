"""
Generate a realistic, seeded synthetic dataset (5,500 complaints) across all
6 departments in English, Telugu, Hindi, and mixed language around Hyderabad wards.

IMPORTANT: This dataset is 100% SYNTHETIC (`is_synthetic=True`) for offline
model development, benchmarking, and demonstration. It includes overlapping
vocabulary, typos, abbreviations, and realistic label ambiguity so classifiers
are not trivially separable.

Usage:
    python scripts/generate_synthetic_data.py
"""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.priority_rules import map_duration_to_priority, bump_priority
from nlp.duration import analyze_duration_and_recurrence

HYDERABAD_WARDS = [
    ("Banjara Hills", 17.4156, 78.4347),
    ("Jubilee Hills", 17.4326, 78.4071),
    ("Madhapur",      17.4486, 78.3908),
    ("Gachibowli",    17.4401, 78.3489),
    ("Kondapur",      17.4622, 78.3568),
    ("Kukatpally",    17.4948, 78.3996),
    ("Miyapur",       17.4968, 78.3614),
    ("Secunderabad",  17.4399, 78.4983),
    ("Begumpet",      17.4447, 78.4664),
    ("Ameerpet",      17.4375, 78.4483),
    ("Abids",         17.3914, 78.4765),
    ("Charminar",     17.3616, 78.4747),
    ("Dilsukhnagar",  17.3688, 78.5247),
    ("LB Nagar",      17.3457, 78.5522),
    ("Uppal",         17.4058, 78.5591),
]

LANDMARKS = [
    "main road", "bus stop", "govt school", "metro pillar", "water tank",
    "community hall", "railway crossing", "primary health center", "market lane",
    "temple street", "ring road junction", "hospital gate", "park corner",
]

# Templates grouped by (department, language) with template_group_id so train/test
# splits can prevent near-duplicate template leakage.
TEMPLATES: dict[str, dict[str, list[str]]] = {
    "Water": {
        "en": [
            "No drinking water supply in {ward} near {landmark} since {days} days",
            "Water supply completely stopped in {ward} {landmark} residential houses",
            "Main water pipeline leaking badly near {landmark} in {ward}",
            "Contaminated muddy tap water supply coming in {ward} colony homes",
            "Low water pressure in municipal tap pipeline at {ward} {landmark}",
            "Burst drinking water pipe flooding street beside {landmark} in {ward}",
            "Tap water supply irregular and dirty around {ward} {landmark}",
            "Valve broken on water supply pipeline at {ward} {landmark}",
            "Need municipal water tanker and pipeline repair near {ward} {landmark}",
            "Overhead water tank supply cut off for {days} days in {ward}",
        ],
        "te": [
            "{ward} లో {landmark} దగ్గర {days} రోజులుగా త్రాగునీరు సప్లై రావడం లేదు",
            "{ward} కాలనీలో మంచినీటి పైప్‌లైన్ పగిలి నీరు రోడ్డుపై వృధా అవుతోంది",
            "{ward} {landmark} వద్ద నల్లా నీరు మురికిగా మరియు వాసనగా వస్తోంది",
            "{ward} లో వాటర్ సప్లై ప్రెజర్ చాలా తక్కువగా ఉంది నీరు రావడం లేదు",
            "{ward} ప్రధాన రహదారి పక్కన తాగునీటి పైపు లీక్ అయి నీరు నిలుస్తోంది",
            "{ward} {landmark} సమీపంలో ట్యాంక్ నుండి త్రాగునీరు సరఫరా ఆగిపోయింది",
            "{ward} లో నల్లా కనెక్షన్ పైప్‌లైన్ వాల్వ్ పాడై నీరు లీక్ అవుతోంది",
            "{ward} ఇళ్లలో {days} రోజులుగా మంచినీటి ట్యాంకర్ మరియు సప్లై సమస్య ఉంది",
        ],
        "hi": [
            "{ward} में {landmark} के पास {days} दिनों से पीने का पानी सप्लाई नहीं आ रहा है",
            "{ward} कॉलोनी की मुख्य पानी की पाइपलाइन टूट गई है और पानी बह रहा है",
            "{ward} में नल से गंदा और बदबूदार पानी आ रहा है कृपया पाइपलाइन जांच करें",
            "{ward} {landmark} के पास पानी सप्लाई का प्रेशर बहुत कम है नल नहीं भर रहा",
            "{ward} में वाटर सप्लाई वाल्व और पाइप खराब होने से पानी लीक हो रहा है",
            "{ward} इलाके में ओवरहेड टैंक से पानी की सप्लाई {days} दिनों से बंद है",
            "{ward} {landmark} रोड पर पीने के पानी की पाइप फटने से पानी बर्बाद हो रहा है",
            "{ward} में पानी का टैंकर और नल जल सप्लाई नियमित करने की जरूरत है",
        ],
        "mixed": [
            "{ward} lo {landmark} dagara {days} days nundi drinking water supply ledu sir",
            "{ward} mein {landmark} ke paas water pipeline leak ho rahi hai urgent repair",
            "Tap water muddy ga vastundi in {ward} near {landmark} water supply check pls",
            "{ward} colony lo low water pressure and pipeline problem near {landmark}",
            "{ward} {landmark} side drinking water tanker aur tap supply {days} days se band hai",
            "Water pipe burst near {ward} {landmark} clean water waste avutundi",
        ],
    },
    "Roads": {
        "en": [
            "Deep potholes on {ward} {landmark} road stretch causing two wheeler skids",
            "Main road asphalt completely washed away near {landmark} in {ward}",
            "Broken road footpath and open road trench beside {ward} {landmark}",
            "Damaged speed breaker on main road without markings near {landmark} in {ward}",
            "Road caved in with potholes after rain near {ward} {landmark}",
            "Road left unpatched with gravel after digging near {ward} {landmark}",
            "Huge pothole crater at {ward} {landmark} road damaging vehicles daily",
            "Broken road surface near {landmark} in {ward} needs immediate resurfacing",
            "Dangerous road cracks and sinking asphalt outside {ward} {landmark}",
            "Uneven dug-up road stretch near {landmark} in {ward} troubling commuters",
        ],
        "te": [
            "{ward} {landmark} వద్ద రోడ్డుపై పెద్ద గుంతలు పడి ప్రమాదాలు జరుగుతున్నాయి",
            "{ward} లో వర్షాలకు తారు రోడ్డు పూర్తిగా దెబ్బతింది గుంతలు పూడ్చండి",
            "{ward} {landmark} దగ్గర ఫుట్‌పాత్ మరియు రోడ్డు అంచులు విరిగిపోయాయి",
            "{ward} ప్రధాన రహదారి రోడ్డుపై తవ్విన గుంతను ఇంకా మరమ్మత్తు చేయలేదు",
            "{ward} లో {landmark} సమీపంలో రోడ్డు కుంగిపోయి వాహనాలకు ఇబ్బందిగా ఉంది",
            "{ward} {landmark} రోడ్డు అంతా పగుళ్లు మరియు గుంతలతో అధ్వాన్నంగా ఉంది",
            "{ward} కాలనీ రోడ్డు తారు పోయి కంకర తేలింది వెంటనే రోడ్డు వేయండి",
            "{ward} లో {landmark} వద్ద స్పీడ్ బ్రేకర్ మరియు రోడ్డు పూర్తిగా పాడయ్యాయి",
        ],
        "hi": [
            "{ward} में {landmark} के पास सड़क पर बड़े गड्ढे हो गए हैं दुर्घटना का डर है",
            "{ward} की मुख्य सड़क बारिश के बाद पूरी तरह टूट गई है डामर मरम्मत कराएं",
            "{ward} {landmark} के नजदीक खुदाई के बाद टूटी सड़क की मरम्मत नहीं हुई है",
            "{ward} में बिना निशान वाला स्पीड ब्रेकर और टूटा सड़क फुटपाथ समस्या दे रहा है",
            "{ward} रोड पर गड्ढों और टूटी सड़क के कारण डामर उखड़ गया है कृपया ध्यान दें",
            "{ward} {landmark} के पास सड़क धंस गई है और गड्ढों से वाहन फिसल रहे हैं",
            "{ward} मुख्य रोड पर दरारें और गड्ढे ठीक करने के लिए नई सड़क की जरूरत है",
            "{ward} कॉलोनी रोड {landmark} के पास बहुत खराब हालत में है मरम्मत करें",
        ],
        "mixed": [
            "{ward} {landmark} road meeda pedda potholes unnai accidents avutunnai",
            "{ward} mein {landmark} ke paas road bahut damaged aur gaddhe hain pls repair",
            "Footpath broken and road potholes near {ward} {landmark} chala danger",
            "{ward} main road full cracks and potholes near {landmark} urgently repair",
            "{ward} {landmark} road digging tarvata asphalt patch cheyaledu potholes everywhere",
            "Road surface completely broken at {ward} {landmark} bike skid ho rahi hai",
        ],
    },
    "Electricity": {
        "en": [
            "Frequent electricity power cuts for {days} hours daily in {ward} near {landmark}",
            "Sparking from electric power transformer beside {landmark} in {ward}",
            "Electric streetlights not working along {ward} {landmark} making road dark",
            "Low hanging live electric wire near {landmark} in {ward} shock risk",
            "Severe electricity voltage fluctuation damaging appliances in {ward} homes",
            "Electric pole and power line tilted dangerously near {ward} {landmark}",
            "Power transformer fuse blowing repeatedly at {ward} {landmark} substation",
            "Streetlight electric lamp not glowing outside {landmark} in {ward}",
            "No electricity power supply since {days} hours in {ward} {landmark}",
            "Exposed live electric cable near {landmark} in {ward} needs insulation",
        ],
        "te": [
            "{ward} {landmark} వద్ద {days} రోజులుగా తరచుగా కరెంట్ కోతలు విద్యుత్ సమస్య ఉంది",
            "{ward} లో కరెంట్ ట్రాన్స్‌ఫార్మర్ నుండి నిప్పురవ్వలు వస్తున్నాయి అత్యవసరం",
            "{ward} {landmark} వీధిలో కరెంట్ వీధి దీపాలు స్ట్రీట్ లైట్లు వెలగడం లేదు",
            "{ward} లో కరెంట్ విద్యుత్ వైర్లు కిందికి వేలాడుతున్నాయి షాక్ ప్రమాదం",
            "{ward} కాలనీలో కరెంట్ వోల్టేజ్ హెచ్చుతగ్గుల వల్ల ఇంట్లో పరికరాలు పాడవుతున్నాయి",
            "{ward} {landmark} వద్ద కరెంట్ స్తంభం వంగి వైర్లు ప్రమాదకరంగా ఉన్నాయి",
            "{ward} లో ట్రాన్స్‌ఫార్మర్ ఫ్యూజ్ తరచుగా పోయి కరెంట్ సప్లై ఆగిపోతోంది",
            "{ward} లో రాత్రిపూట స్ట్రీట్ లైట్ విద్యుత్ దీపాలు పనిచేయక చీకటిగా ఉంది",
        ],
        "hi": [
            "{ward} में {landmark} के पास {days} दिनों से बार-बार बिजली कटौती हो रही है",
            "{ward} के बिजली ट्रांसफार्मर से चिंगारी निकल रही है तुरंत जांच करें",
            "{ward} {landmark} रोड की बिजली स्ट्रीट लाइट्स कई दिनों से बंद हैं",
            "{ward} में बिजली का खंभा झुक गया है और बिजली के तार नीचे लटक रहे हैं",
            "{ward} इलाके में बिजली वोल्टेज फ्लक्चुएशन से उपकरण जल रहे हैं",
            "{ward} {landmark} के पास ट्रांसफार्मर फ्यूज बार-बार उड़ने से बिजली नहीं है",
            "{ward} में खुला लाइव बिजली वायर लटक रहा है करंट लगने का खतरा है",
            "{ward} कॉलोनी में {days} घंटे से बिजली सप्लाई बंद है स्ट्रीट लाइट भी खराब है",
        ],
        "mixed": [
            "{ward} lo {landmark} dagara electric transformer sparking avutundi urgent",
            "{ward} mein street lights aur electricity nahi hai near {landmark} since {days} days",
            "Power cut and electricity outage in {ward} near {landmark} chala problem",
            "Loose live electric wire hanging near {ward} {landmark} voltage shock risk",
            "{ward} {landmark} transformer fuse problem valla current power ledu",
            "Streetlight pole sparking and power fluctuation in {ward} near {landmark}",
        ],
    },
    "Sanitation": {
        "en": [
            "Garbage waste not collected for {days} days in {ward} near {landmark}",
            "Overflowing sewage drainage manhole flooding lane at {ward} {landmark}",
            "Open sewage drainage clogged with garbage causing mosquitoes in {ward}",
            "Overflowing garbage dustbins spreading foul smell in {ward} {landmark}",
            "Garbage dump and dead animal waste near {landmark} in {ward} needs cleaning",
            "Uncollected waste and garbage piled up on street in {ward} {landmark}",
            "Blocked sewage drain creating dirty stagnant water around {ward} {landmark}",
            "Sanitation sweeping and garbage clearance absent in {ward} lanes",
            "Foul sewage smell from choked drainage pipeline near {ward} {landmark}",
            "Dustbins broken and garbage scattered outside {ward} {landmark}",
        ],
        "te": [
            "{ward} {landmark} వద్ద {days} రోజులుగా చెత్త సేకరణ జరగడం లేదు డస్ట్‌బిన్లు నిండాయి",
            "{ward} లో డ్రైనేజీ మ్యాన్‌హోల్ పొంగి మురుగు నీరు చెత్త రోడ్డుపై పారుతోంది",
            "{ward} {landmark} దగ్గర చెత్త డస్ట్‌బిన్లు నిండి దుర్వాసన మరియు దోమలు పెరిగాయి",
            "{ward} కాలనీలో ఓపెన్ డ్రైనేజీ నాలా చెత్తతో మూసుకుపోయింది శుభ్రం చేయండి",
            "{ward} లో వీధి పారిశుధ్య చెత్త పనులు వారం రోజులుగా జరగడం లేదు",
            "{ward} {landmark} పక్కన మురుగు డ్రైనేజీ నీరు నిలిచి దుర్వాసన వస్తోంది",
            "{ward} లో ఇంటింటి చెత్త సేకరణ వాహనం {days} రోజులుగా రావడం లేదు",
            "{ward} లో రోడ్డు పక్కన చెత్త కుప్పలు మరియు డ్రైనేజీ సమస్య తీవ్రంగా ఉంది",
        ],
        "hi": [
            "{ward} में {landmark} के पास {days} दिनों से कचरा गाड़ी सफाई के लिए नहीं आई है",
            "{ward} की सीवर ड्रेनेज लाइन ओवरफ्लो हो रही है और गंदा पानी बह रहा है",
            "{ward} {landmark} के पास कचरे के कूड़ेदान भरे पड़े हैं बदबू से मच्छर बढ़ रहे हैं",
            "{ward} में खुली नाली ड्रेनेज कचरे से जाम होने पर गंदगी फैल रही है सफाई करवाएं",
            "{ward} कॉलोनी में सड़क किनारे कचरे और गंदगी का ढेर लगा हुआ है",
            "{ward} {landmark} पर सीवर चैंबर बहने से गंदा पानी और बदबू फैल रही है",
            "{ward} में सफाई कर्मचारी नहीं आने से कचरा और नाली जाम की समस्या है",
            "{ward} गली में कचरा संग्रहण और ड्रेनेज की सफाई तुरंत करवाने की कृपा करें",
        ],
        "mixed": [
            "{ward} lo {landmark} dagara {days} days nundi garbage waste collect cheyaledu",
            "{ward} mein sewage drainage overflow ho raha hai near {landmark} bad smell",
            "Sewage drainage water leaking on street at {ward} {landmark} garbage issue",
            "Garbage dustbins overflowing in {ward} near {landmark} pls clean drainage",
            "{ward} {landmark} side kachra aur blocked drain valla chala foul smell undi",
            "Mosquitoes and uncollected garbage waste dump near {ward} {landmark}",
        ],
    },
    "Traffic": {
        "en": [
            "Traffic signal stuck on red at {ward} {landmark} causing long traffic jam",
            "Wrong side driving and illegal parking choking traffic near {ward} {landmark}",
            "Heavy traffic jam congestion during peak hours near {landmark} in {ward}",
            "Missing zebra crossing and traffic signal signage outside {ward} {landmark}",
            "Vehicle collision accident and damaged traffic signal pole at {ward} {landmark}",
            "Auto rickshaws illegal parking blocking traffic lane turn at {ward} {landmark}",
            "Need traffic police deployment for signal jam control in {ward} {landmark}",
            "Malfunctioning traffic signal lights causing junction confusion at {ward} {landmark}",
            "Buses stopping randomly choking junction traffic flow near {ward} {landmark}",
            "Pedestrian crossing unsafe due to broken traffic signal at {ward} {landmark}",
        ],
        "te": [
            "{ward} {landmark} జంక్షన్ వద్ద ట్రాఫిక్ సిగ్నల్ పనిచేయక భారీ ట్రాఫిక్ జామ్ అవుతోంది",
            "{ward} లో రాంగ్ రూట్ డ్రైవింగ్ మరియు అక్రమ పార్కింగ్ వల్ల ట్రాఫిక్ జామ్ అయింది",
            "{ward} {landmark} వద్ద జీబ్రా క్రాసింగ్ మరియు ట్రాఫిక్ సిగ్నల్ బోర్డులు లేవు",
            "{ward} ప్రధాన కూడలిలో రద్దీ సమయంలో ట్రాఫిక్ సిగ్నల్ వద్ద ట్రాఫిక్ పోలీసులు లేరు",
            "{ward} {landmark} దగ్గర రోడ్డు ప్రమాదం జరిగి వాహనాల ట్రాఫిక్ నిలిచిపోయాయి",
            "{ward} జంక్షన్ వద్ద ఆటోల అక్రమ పార్కింగ్ వల్ల ట్రాఫిక్ కదలడం లేదు",
            "{ward} {landmark} వద్ద ట్రాఫిక్ సిగ్నల్ లైట్లు పనిచేయక వాహనదారులకు ఇబ్బందిగా ఉంది",
            "{ward} లో పీక్ అవర్స్‌లో భారీ ట్రాఫిక్ రద్దీ నియంత్రణ అవసరం",
        ],
        "hi": [
            "{ward} के {landmark} चौराहे पर ट्रैफिक सिग्नल खराब है लंबा ट्रैफिक जाम लगा है",
            "{ward} में गलत दिशा में वाहन चलाने और अवैध पार्किंग से ट्रैफिक जाम लग रहा है",
            "{ward} {landmark} के पास जेब्रा क्रॉसिंग और ट्रैफिक सिग्नल साइन बोर्ड नहीं हैं",
            "{ward} जंक्शन पर पीक आवर्स में ट्रैफिक जाम के लिए ट्रैफिक पुलिस तैनात की जाए",
            "{ward} में ट्रैफिक सिग्नल लाइट टूटने से जाम और दुर्घटना की स्थिति बन रही है",
            "{ward} {landmark} मोड़ पर अवैध पार्किंग और ऑटो से ट्रैफिक बाधित हो रहा है",
            "{ward} चौराहे पर रेड लाइट सिग्नल अटकने से भारी ट्रैफिक जाम लगा रहता है",
            "{ward} {landmark} के पास यातायात ट्रैफिक नियंत्रण और सिग्नल सुधार आवश्यक है",
        ],
        "mixed": [
            "{ward} {landmark} junction lo traffic signal pani cheyatledu heavy traffic jam",
            "{ward} mein {landmark} par illegal parking se traffic signal jam aur block hai",
            "Traffic signal stuck at {ward} {landmark} causing huge rush and jam",
            "{ward} junction pe traffic police aur working traffic signal chahiye",
            "{ward} {landmark} wrong side driving valla daily heavy traffic jam avutundi",
            "Broken traffic signal light and parking chaos near {ward} {landmark}",
        ],
    },
    "General / Other": {
        "en": [
            "Stray dog menace near {landmark} in {ward} troubling school children",
            "Public park benches and garden boundary fence broken in {ward} {landmark}",
            "Loudspeaker noise pollution late night near {landmark} in {ward}",
            "Illegal encroachment of shops on public pavement in {ward} {landmark}",
            "Overgrown park tree branches hanging low over walkway in {ward} {landmark}",
            "Public toilet locked and broken near {ward} {landmark}",
            "Request for CCTV camera installation at public park in {ward} {landmark}",
            "Abandoned rusted car lying unclaimed for months near {ward} {landmark}",
            "Community hall roof leaking and seating damaged in {ward} {landmark}",
            "Stray cattle roaming and noise nuisance near {landmark} in {ward}",
        ],
        "te": [
            "{ward} {landmark} దగ్గర వీధి కుక్కల బెడద ఎక్కువగా ఉంది పబ్లిక్ చర్యలు తీసుకోండి",
            "{ward} పబ్లిక్ పార్క్‌లో బెంచీలు మరియు గేటు విరిగిపోయాయి మరమ్మత్తు చేయండి",
            "{ward} {landmark} వద్ద రాత్రిపూట లౌడ్ స్పీకర్ శబ్ద కాలుష్యం ఎక్కువగా ఉంది",
            "{ward} లో పబ్లిక్ పాదచారుల దారిని ఆక్రమించుకున్నారు ఆక్రమణలు తొలగించండి",
            "{ward} {landmark} సమీపంలో పబ్లిక్ టాయిలెట్ మూసివేసి పాడై ఉంది",
            "{ward} పార్క్ వద్ద సిసిటివి కెమెరా మరియు చెట్ల కొమ్మల కత్తిరింపు అవసరం",
            "{ward} కమ్యూనిటీ హాల్ మరియు పబ్లిక్ గార్డెన్ నిర్వహణ సరిగా లేదు",
            "{ward} {landmark} వద్ద వదిలేసిన పాత వాహనం మరియు వీధి కుక్కల సమస్య ఉంది",
        ],
        "hi": [
            "{ward} में {landmark} के पास आवारा कुत्तों का आतंक बढ़ गया है पब्लिक परेशान है",
            "{ward} पब्लिक पार्क की बेंच और चारदीवारी टूटी हुई है कृपया ठीक कराएं",
            "{ward} {landmark} के पास रात में तेज लाउडस्पीकर से शोर प्रदूषण हो रहा है",
            "{ward} में सार्वजनिक पब्लिक जगह और पटरी पर अतिक्रमण हटाने की कृपा करें",
            "{ward} {landmark} के नजदीक सार्वजनिक पब्लिक शौचालय बंद पड़ा है",
            "{ward} पार्क में सीसीटीवी कैमरा लगाने और पेड़ों की छंटाई की मांग है",
            "{ward} कम्युनिटी हॉल की छत टपक रही है और पब्लिक सुविधाएं खराब हैं",
            "{ward} {landmark} के पास लावारिस पुराना वाहन और आवारा कुत्ते घूम रहे हैं",
        ],
        "mixed": [
            "{ward} lo {landmark} dagara stray dogs chala unnai public complaint",
            "{ward} mein {landmark} ke paas public park bench aur gate broken hai",
            "Noise pollution loudspeaker late night near {ward} {landmark} disturbance",
            "Public shop encroachment in {ward} near {landmark} pls remove",
            "{ward} {landmark} public toilet locked undi aur CCTV installation chahiye",
            "Tree branches and abandoned old car nuisance in {ward} park near {landmark}",
        ],
    },
}

URGENCY_PREFIXES = {
    "High": [
        "URGENT ", "Emergency: ", "Critical hazard - ", "Immediate action needed: ",
        "Danger! ", "Fire risk / ", "Electrocution hazard: ", "Flooding emergency - ",
    ],
    "Medium": [
        "Issue: ", "Problem reported - ", "Needs repair: ", "Delay notice: ", "",
    ],
    "Low": [
        "Request: ", "Kindly check - ", "Suggestion: ", "",
    ],
}

TYPOS = [
    ("water", "wtr"), ("road", "rd"), ("please", "pls"), ("urgent", "urgnt"),
    ("electricity", "elec"), ("problem", "prblm"), ("garbage", "grbg"),
    ("traffic", "trfc"), ("broken", "brkn"), ("street", "st"),
    ("near", "nr"), ("since", "snc"), ("repair", "rpr"),
]


def _inject_noise(text: str, rng: random.Random) -> str:
    if rng.random() < 0.30:
        src, dst = rng.choice(TYPOS)
        if src in text.lower():
            text = text.replace(src, dst, 1)
    if rng.random() < 0.15:
        text = text + rng.choice([" pls help", " asap", " ward office", " thank you", "!!"])
    if rng.random() < 0.10:
        text = text.lower()
    return text


DURATION_SNIPPETS: dict[str, list[tuple[str, float, str]]] = {
    "en": [
        ("for 45 minutes", 0.75, "short"),
        ("for 3 hours", 3.0, "short"),
        ("for the last 10 hours", 10.0, "short"),
        ("since morning", 8.0, "short"),
        ("since last night", 12.0, "short"),
        ("for half a day", 12.0, "short"),
        ("since yesterday", 24.0, "med"),
        ("for 2 days", 48.0, "med"),
        ("for the last 3 days", 72.0, "med"),
        ("since 3 days", 72.0, "med"),
        ("past 4 days", 96.0, "med"),
        ("2 to 3 days", 72.0, "med"),
        ("3-4 days", 96.0, "med"),
        ("for two days", 48.0, "med"),
        ("for five dys", 120.0, "five"),
        ("from 6 days", 144.0, "six"),
        ("for 1 week", 168.0, "long"),
        ("for a week", 168.0, "long"),
        ("for one week", 168.0, "long"),
        ("since last week", 168.0, "long"),
        ("past 2 wks", 336.0, "long"),
        ("from last month", 720.0, "long"),
        ("since 2 months", 1440.0, "long"),
        ("for 10 hrs", 10.0, "short"),
    ],
    "te": [
        ("3 గంటలుగా", 3.0, "short"),
        ("ఉదయం నుండి", 8.0, "short"),
        ("రాత్రి నుండి", 12.0, "short"),
        ("నిన్నటి నుండి", 24.0, "med"),
        ("2 రోజులుగా", 48.0, "med"),
        ("3 రోజులుగా", 72.0, "med"),
        ("మూడు రోజులుగా", 72.0, "med"),
        ("౩ రోజులుగా", 72.0, "med"),
        ("5 రోజులు నుండి", 120.0, "five"),
        ("2 నుండి 3 రోజులు", 72.0, "med"),
        ("గత వారం నుండి", 168.0, "long"),
        ("ఒక వారం నుండి", 168.0, "long"),
        ("రెండు వారాలుగా", 336.0, "long"),
        ("గత నెల నుండి", 720.0, "long"),
    ],
    "hi": [
        ("4 घंटे से", 4.0, "short"),
        ("सुबह से", 8.0, "short"),
        ("रात से", 12.0, "short"),
        ("कल से", 24.0, "med"),
        ("पिछले 3 दिनों से", 72.0, "med"),
        ("तीन दिन से", 72.0, "med"),
        ("३ दिनों से", 72.0, "med"),
        ("2 से 3 दिन से", 72.0, "med"),
        ("पांच दिन से", 120.0, "five"),
        ("पिछले हफ्ते से", 168.0, "long"),
        ("एक सप्ताह से", 168.0, "long"),
        ("2 हफ्ते से", 336.0, "long"),
        ("पिछले महीने से", 720.0, "long"),
    ],
    "mixed": [
        ("5 hrs nundi", 5.0, "short"),
        ("subah se", 8.0, "short"),
        ("ninna nundi", 24.0, "med"),
        ("3 din se", 72.0, "med"),
        ("3 rojula nundi", 72.0, "med"),
        ("moodu rojulu ga", 72.0, "med"),
        ("teen din se", 72.0, "med"),
        ("2 to 4 days nundi", 96.0, "med"),
        ("5 din se", 120.0, "five"),
        ("1 week nundi", 168.0, "long"),
        ("two wks se", 336.0, "long"),
        ("2 months nundi", 1440.0, "long"),
    ],
}

RECURRING_SNIPPETS: dict[str, list[str]] = {
    "en": ["every week", "twice a month", "every day in morning", "three times a week"],
    "te": ["ప్రతి వారం", "ప్రతి రోజు ఉదయం", "prathi vaaram"],
    "hi": ["हर हफ्ते", "हर दिन सुबह", "har mahine"],
    "mixed": ["har hafte", "prathi roju", "twice a month"],
}

NEGATIVE_SNIPPETS: dict[str, list[str]] = {
    "en": [
        "paid 3 days salary earlier",
        "ticket valid for 7 days at counter",
        "was fixed 3 days ago but broke again",
        "please call me after 3 days before festival",
    ],
    "te": ["గత వారం పండుగ ముగిసింది కానీ సమస్య ఉంది", "3 రోజుల సెలవు తర్వాత గమనించాను"],
    "hi": ["3 दिन की छुट्टी के बाद देखा", "बिल की वैधता 7 दिन की है"],
    "mixed": ["3 days festival holiday tarvata chusamu", "was fixed 4 days ago then again problem"],
}

AMBIGUOUS_SNIPPETS: dict[str, list[str]] = {
    "en": [
        "for ages",
        "for a long time",
        "called the office 3 days ago, no response",
        "no supply 2 days here and 1 week in next street",
    ],
    "te": ["చాలా రోజులుగా", "chala rojula nundi"],
    "hi": ["बहुत दिनों से", "bahut samay se"],
    "mixed": ["chala rojula nundi", "bahut dino se"],
}

DUR_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "Water": {
        "en": [
            "No drinking water supply in {ward} near {landmark} {dur}",
            "Main water pipeline leaking continuously near {ward} {landmark} {dur}",
            "Muddy contaminated tap water coming at {ward} {landmark} {dur}",
            "Burst tap pipeline wasting drinking water beside {ward} {landmark} {dur}",
        ],
        "te": [
            "{ward} {landmark} దగ్గర {dur} త్రాగునీరు సప్లై రావడం లేదు",
            "{ward} కాలనీలో మంచినీటి పైప్‌లైన్ {dur} లీక్ అవుతోంది",
        ],
        "hi": [
            "{ward} में {landmark} के पास {dur} पीने का पानी सप्लाई नहीं आ रहा है",
            "{ward} कॉलोनी की पानी की पाइपलाइन {dur} से लीक हो रही है",
        ],
        "mixed": [
            "{ward} {landmark} dagara {dur} drinking water supply ravatledu",
            "{ward} mein {landmark} ke paas {dur} water pipeline leak ho rahi hai",
        ],
    },
    "Roads": {
        "en": [
            "Deep pothole on main road near {ward} {landmark} left unrepaired {dur}",
            "Broken asphalt road surface near {landmark} in {ward} {dur}",
            "Road trench left dug up and unsafe beside {ward} {landmark} {dur}",
        ],
        "te": [
            "{ward} {landmark} వద్ద రోడ్డు గుంతలు {dur} పూడ్చలేదు",
            "{ward} లో తారు రోడ్డు {dur} దెబ్బతిని ఉంది",
        ],
        "hi": [
            "{ward} {landmark} रोड पर बड़े गड्ढे {dur} से ठीक नहीं हुए",
            "{ward} मुख्य सड़क {dur} से टूटी पड़ी है",
        ],
        "mixed": [
            "{ward} {landmark} road meeda potholes {dur} unnai",
            "{ward} mein road damaged hai near {landmark} {dur}",
        ],
    },
    "Electricity": {
        "en": [
            "Frequent electricity outage near {ward} {landmark} {dur}",
            "Electric streetlight dark and non-working beside {ward} {landmark} {dur}",
            "Power transformer voltage fluctuation in {ward} near {landmark} {dur}",
        ],
        "te": [
            "{ward} {landmark} వద్ద {dur} కరెంట్ కోతలు విద్యుత్ సమస్య ఉంది",
            "{ward} వీధిలో స్ట్రీట్ లైట్లు {dur} వెలగడం లేదు",
        ],
        "hi": [
            "{ward} में {landmark} के पास {dur} से बिजली कटौती हो रही है",
            "{ward} रोड की स्ट्रीट लाइट {dur} से बंद है",
        ],
        "mixed": [
            "{ward} {landmark} dagara {dur} power cut problem undi",
            "{ward} mein electricity aur street light {dur} band hai",
        ],
    },
    "Sanitation": {
        "en": [
            "Garbage waste overflowing dustbins near {ward} {landmark} {dur}",
            "Blocked sewage drainage overflowing onto street in {ward} {landmark} {dur}",
        ],
        "te": [
            "{ward} {landmark} దగ్గర {dur} చెత్త సేకరణ జరగడం లేదు",
            "{ward} కాలనీలో డ్రైనేజీ మురుగు నీరు {dur} పారుతోంది",
        ],
        "hi": [
            "{ward} {landmark} के पास {dur} कचरा गाड़ी नहीं आई है",
            "{ward} में सीवर ड्रेनेज लाइन {dur} से ओवरफ्लो हो रही है",
        ],
        "mixed": [
            "{ward} {landmark} dagara {dur} garbage collect cheyaledu",
            "{ward} mein sewage drainage {dur} overflow ho raha hai",
        ],
    },
    "Traffic": {
        "en": [
            "Traffic signal stuck at {ward} {landmark} causing junction jam {dur}",
            "Illegal parking blocking traffic lane near {ward} {landmark} {dur}",
        ],
        "te": [
            "{ward} {landmark} జంక్షన్ వద్ద {dur} ట్రాఫిక్ సిగ్నల్ పనిచేయడం లేదు",
        ],
        "hi": [
            "{ward} {landmark} चौराहे पर {dur} ट्रैफिक सिग्नल खराब है",
        ],
        "mixed": [
            "{ward} {landmark} junction lo {dur} traffic signal pani cheyatledu",
        ],
    },
    "General / Other": {
        "en": [
            "Stray dogs nuisance near public park at {ward} {landmark} {dur}",
            "Park boundary wall and public toilet damaged in {ward} {landmark} {dur}",
        ],
        "te": [
            "{ward} {landmark} దగ్గర {dur} వీధి కుక్కల బెడద ఉంది",
        ],
        "hi": [
            "{ward} में {landmark} के पास {dur} से आवारा कुत्तों का आतंक है",
        ],
        "mixed": [
            "{ward} {landmark} lo {dur} stray dogs aur public bench issue undi",
        ],
    },
}


def _generate_duration_extra_rows(
    start_idx: int,
    n_extra: int,
    rng: random.Random,
    start_dt: datetime,
    end_dt: datetime,
) -> list[dict]:
    departments = list(DUR_TEMPLATES.keys())
    lang_choices = ["en", "te", "hi", "mixed"]
    lang_weights = [0.50, 0.18, 0.18, 0.14]
    out: list[dict] = []

    for offset in range(n_extra):
        idx = start_idx + offset
        dept = rng.choice(departments)
        lang = rng.choices(lang_choices, weights=lang_weights, k=1)[0]
        ward_name, base_lat, base_lon = rng.choice(HYDERABAD_WARDS)
        lat = round(base_lat + rng.uniform(-0.012, 0.012), 6)
        lon = round(base_lon + rng.uniform(-0.012, 0.012), 6)
        landmark = rng.choice(LANDMARKS)

        day_offset = rng.randint(0, 364)
        created_dt = (start_dt + timedelta(days=day_offset)).replace(
            hour=rng.randint(6, 22), minute=rng.randint(0, 59), second=rng.randint(0, 59)
        )

        # Mix: 76% clean duration snippets, 9% recurring, 8% negative irrelevant, 7% ambiguous
        roll = rng.random()
        if roll < 0.76:
            dur_phrase, _, bucket_tag = rng.choice(DURATION_SNIPPETS[lang])
            case_tag = f"clear_{bucket_tag}"
        elif roll < 0.85:
            dur_phrase = rng.choice(RECURRING_SNIPPETS[lang])
            case_tag = "rec"
        elif roll < 0.93:
            dur_phrase = rng.choice(NEGATIVE_SNIPPETS[lang])
            case_tag = "neg"
        else:
            dur_phrase = rng.choice(AMBIGUOUS_SNIPPETS[lang])
            case_tag = "amb"

        tmpl_list = DUR_TEMPLATES[dept][lang]
        t_idx = rng.randrange(len(tmpl_list))
        raw_text = tmpl_list[t_idx].format(ward=ward_name, landmark=landmark, dur=dur_phrase)

        # Small portion (~8%) include an emergency hazard word even on short durations
        emergency = False
        if rng.random() < 0.08:
            raw_text = rng.choice(["URGENT fire hazard - ", "Electrocution danger: ", "Flooding emergency: "]) + raw_text
            emergency = True

        text = _inject_noise(raw_text, rng)
        info = analyze_duration_and_recurrence(text)
        dur_prio = map_duration_to_priority(info["hours"], department=dept)
        if emergency:
            priority = "High"
        elif dur_prio is not None:
            priority = bump_priority(dur_prio, 1) if info["recurring"] else dur_prio
        elif info["recurring"]:
            priority = "Medium"
        else:
            # Negative or no duration -> Low or Medium based on mild issue cue
            priority = "Medium" if rng.random() < 0.35 else "Low"

        label_dept = dept
        if rng.random() < 0.04:
            label_dept = rng.choice([d for d in departments if d != dept])

        tmpl_group = f"dur_{dept}_{lang}_{t_idx}_{case_tag}"
        status = rng.choices(["Closed", "Resolved", "In Progress", "Pending"], weights=[0.4, 0.25, 0.2, 0.15], k=1)[0]
        res_hrs = ""
        resolved_at = ""
        rating = ""
        if status in ("Resolved", "Closed"):
            base_h = {"High": 7.0, "Medium": 32.0, "Low": 76.0}[priority]
            hrs = max(0.5, round(rng.lognormvariate(0, 0.5) * base_h, 2))
            res_hrs = str(hrs)
            resolved_at = (created_dt + timedelta(hours=hrs)).strftime("%Y-%m-%d %H:%M:%S")
            if status == "Closed":
                rating = str(rng.choices([2, 3, 4, 5], weights=[0.1, 0.2, 0.35, 0.35], k=1)[0])

        out.append({
            "complaint_id":     f"SYN-{idx:05d}",
            "text":             text,
            "language":         lang,
            "department":       label_dept,
            "priority":         priority,
            "status":           status,
            "ward_name":        ward_name,
            "latitude":         lat,
            "longitude":        lon,
            "template_group":   tmpl_group,
            "created_at":       created_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "resolved_at":      resolved_at,
            "resolution_hours": res_hrs,
            "rating":           rating,
            "reopen_count":     0,
            "is_synthetic":     "True",
        })
    return out


def generate_dataset(
    n_rows: int = 5500,
    n_duration_extra: int = 3200,
    seed: int = 42,
    out_path: Path | None = None,
) -> Path:
    rng = random.Random(seed)
    target = out_path or (PROJECT_ROOT / "data" / "synthetic_complaints.csv")
    target.parent.mkdir(parents=True, exist_ok=True)

    end_dt = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
    start_dt = end_dt - timedelta(days=365)

    # Department base weights
    departments = list(TEMPLATES.keys())
    lang_choices = ["en", "te", "hi", "mixed"]
    lang_weights = [0.55, 0.18, 0.15, 0.12]

    rows: list[dict] = []
    for idx in range(1, n_rows + 1):
        # Generate timestamp with weekday peak & seasonal shifts
        day_offset = rng.randint(0, 364)
        base_day = start_dt + timedelta(days=day_offset)
        # Weekend dampening
        if base_day.weekday() >= 5 and rng.random() < 0.40:
            day_offset = max(0, day_offset - rng.randint(1, 3))
            base_day = start_dt + timedelta(days=day_offset)
        hour = int(rng.triangular(0, 23, 11))
        minute = rng.randint(0, 59)
        created_dt = base_day.replace(hour=hour, minute=minute, second=rng.randint(0, 59))
        month = created_dt.month

        # Seasonal department weighting
        if month in (6, 7, 8, 9):  # Monsoon
            dept_weights = [0.24, 0.24, 0.14, 0.22, 0.10, 0.06]
        elif month in (3, 4, 5):   # Summer
            dept_weights = [0.28, 0.12, 0.28, 0.14, 0.10, 0.08]
        else:
            dept_weights = [0.20, 0.18, 0.18, 0.18, 0.14, 0.12]

        dept = rng.choices(departments, weights=dept_weights, k=1)[0]
        lang = rng.choices(lang_choices, weights=lang_weights, k=1)[0]

        ward_name, base_lat, base_lon = rng.choice(HYDERABAD_WARDS)
        lat = round(base_lat + rng.uniform(-0.012, 0.012), 6)
        lon = round(base_lon + rng.uniform(-0.012, 0.012), 6)
        landmark = rng.choice(LANDMARKS)
        days = rng.randint(1, 14)

        tmpl_list = TEMPLATES[dept][lang]
        tmpl_idx = rng.randrange(len(tmpl_list))
        tmpl_group = f"{dept}_{lang}_{tmpl_idx}"
        raw_text = tmpl_list[tmpl_idx].format(ward=ward_name, landmark=landmark, days=days)

        # Priority selection with realistic distribution + ambiguity
        prio_roll = rng.random()
        if dept in ("Electricity", "Water", "Traffic") and prio_roll < 0.30:
            priority = "High"
        elif prio_roll < 0.22:
            priority = "High"
        elif prio_roll < 0.72:
            priority = "Medium"
        else:
            priority = "Low"

        # Add urgency cue most of the time, but keep ~20% without explicit cue so it's non-trivial
        if rng.random() < 0.78:
            prefix = rng.choice(URGENCY_PREFIXES[priority])
            raw_text = f"{prefix}{raw_text}"

        # ~5% realistic label noise / cross-department overlap
        label_dept = dept
        if rng.random() < 0.05:
            label_dept = rng.choice([d for d in departments if d != dept])

        text = _inject_noise(raw_text, rng)

        # Realistic status, resolution hours, rating, reopen
        age_days = (end_dt - created_dt).total_seconds() / 86400.0
        if age_days > 14:
            status = rng.choices(
                ["Closed", "Resolved", "In Progress", "Assigned", "Reopened"],
                weights=[0.48, 0.25, 0.12, 0.08, 0.07],
                k=1,
            )[0]
        elif age_days > 3:
            status = rng.choices(
                ["Resolved", "In Progress", "Assigned", "Pending", "Reopened"],
                weights=[0.30, 0.32, 0.20, 0.12, 0.06],
                k=1,
            )[0]
        else:
            status = rng.choices(
                ["Pending", "Assigned", "In Progress", "Resolved"],
                weights=[0.45, 0.30, 0.18, 0.07],
                k=1,
            )[0]

        base_hrs = {"High": 7.0, "Medium": 32.0, "Low": 76.0}[priority]
        dept_factor = {"Water": 0.9, "Roads": 1.35, "Electricity": 0.8, "Sanitation": 1.0, "Traffic": 0.95, "General / Other": 1.2}[label_dept]
        res_hrs = ""
        resolved_at = ""
        rating = ""
        reopen_count = 1 if status == "Reopened" else (1 if rng.random() < 0.06 else 0)
        if status in ("Resolved", "Closed"):
            hrs = max(0.5, round(rng.lognormvariate(0, 0.55) * base_hrs * dept_factor, 2))
            res_hrs = str(hrs)
            resolved_dt = created_dt + timedelta(hours=hrs)
            resolved_at = resolved_dt.strftime("%Y-%m-%d %H:%M:%S")
            if status == "Closed" or rng.random() < 0.6:
                rating = str(rng.choices([1, 2, 3, 4, 5], weights=[0.05, 0.08, 0.18, 0.34, 0.35], k=1)[0])

        rows.append({
            "complaint_id":     f"SYN-{idx:05d}",
            "text":             text,
            "language":         lang,
            "department":       label_dept,
            "priority":         priority,
            "status":           status,
            "ward_name":        ward_name,
            "latitude":         lat,
            "longitude":        lon,
            "template_group":   tmpl_group,
            "created_at":       created_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "resolved_at":      resolved_at,
            "resolution_hours": res_hrs,
            "rating":           rating,
            "reopen_count":     reopen_count,
            "is_synthetic":     "True",
        })

    rows.extend(
        _generate_duration_extra_rows(
            start_idx=n_rows + 1,
            n_extra=n_duration_extra,
            rng=rng,
            start_dt=start_dt,
            end_dt=end_dt,
        )
    )

    fieldnames = list(rows[0].keys())
    with open(target, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return target


if __name__ == "__main__":
    out = generate_dataset(5500, n_duration_extra=3200, seed=42)
    print(f"Generated 8,700 synthetic complaints (5,500 base + 3,200 duration-aware) -> {out}")
