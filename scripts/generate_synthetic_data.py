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
            "Main pipeline leaking near {landmark} in {ward} wasting clean water",
            "Contaminated muddy tap water coming in {ward} colony houses",
            "Low water pressure in overhead tank lines at {ward} {landmark}",
            "Burst water line flooding road beside {landmark} in {ward}",
            "Illegal motor pumping causing zero supply to tail end street in {ward}",
            "Valve broken at {ward} junction near {landmark}, water flowing onto street",
            "Irregular municipal tanker schedule around {ward} {landmark}",
        ],
        "te": [
            "{ward} లో {landmark} దగ్గర {days} రోజులుగా త్రాగునీరు రావడం లేదు",
            "{ward} కాలనీలో మంచినీటి పైప్‌లైన్ పగిలి నీరు వృధా అవుతోంది",
            "{ward} {landmark} వద్ద నల్లా నీరు మురికిగా మరియు వాసనగా వస్తోంది",
            "{ward} లో వాటర్ సప్లై ప్రెజర్ చాలా తక్కువగా ఉంది దయచేసి చూడండి",
            "{ward} ప్రధాన రహదారి పక్కన తాగునీటి పైపు లీక్ అయి రోడ్డుపై నీరు నిలుస్తోంది",
        ],
        "hi": [
            "{ward} में {landmark} के पास {days} दिनों से पीने का पानी नहीं आ रहा है",
            "{ward} कॉलोनी की मुख्य पानी की पाइपलाइन टूट गई है और पानी बह रहा है",
            "{ward} में नल से गंदा और बदबूदार पानी आ रहा है कृपया जांच करें",
            "{ward} {landmark} के पास पानी का प्रेशर बहुत कम है टंकी नहीं भर रही",
            "{ward} में वाटर सप्लाई वाल्व खराब होने से सड़क पर पानी भर गया है",
        ],
        "mixed": [
            "{ward} lo {landmark} dagara {days} days nundi water supply ledu sir",
            "{ward} mein {landmark} ke paas water pipeline leak ho rahi hai urgent",
            "Tap water muddy ga vastundi in {ward} near {landmark} pls fix",
            "{ward} colony lo low water pressure problem since morning near {landmark}",
        ],
    },
    "Roads": {
        "en": [
            "Deep potholes on {ward} {landmark} stretch causing two wheeler skids",
            "Asphalt completely washed away near {landmark} in {ward}",
            "Broken footpath tiles and open trench beside {ward} {landmark}",
            "Uneven speed breaker without markings near {landmark} in {ward}",
            "Road caved in after rain near {ward} {landmark} blocking traffic",
            "Gravel and debris left unpatched after cable digging in {ward}",
            "Huge crater at {ward} {landmark} intersection damaging vehicles daily",
            "Waterlogged broken lane near {landmark} in {ward} needs resurfacing",
        ],
        "te": [
            "{ward} {landmark} వద్ద రోడ్డుపై పెద్ద గుంతలు పడి ప్రమాదాలు జరుగుతున్నాయి",
            "{ward} లో వర్షాలకు తారు రోడ్డు పూర్తిగా దెబ్బతింది వెంటనే మరమ్మత్తు చేయండి",
            "{ward} {landmark} దగ్గర ఫుట్‌పాత్ మరియు రోడ్డు అంచులు విరిగిపోయాయి",
            "{ward} ప్రధాన రహదారిపై తవ్విన గుంతను ఇంకా పూడ్చలేదు",
            "{ward} లో {landmark} సమీపంలో రోడ్డు కుంగిపోయి వాహనాలకు ఇబ్బందిగా ఉంది",
        ],
        "hi": [
            "{ward} में {landmark} के पास सड़क पर बड़े गड्ढे हो गए हैं दुर्घटना का डर है",
            "{ward} की मुख्य सड़क बारिश के बाद पूरी तरह टूट गई है मरम्मत कराएं",
            "{ward} {landmark} के नजदीक खुदाई के बाद सड़क की मरम्मत नहीं हुई है",
            "{ward} में बिना निशान वाला स्पीड ब्रेकर और टूटा फुटपाथ समस्या दे रहा है",
            "{ward} रोड पर जलभराव के कारण डामर उखड़ गया है कृपया ध्यान दें",
        ],
        "mixed": [
            "{ward} {landmark} road meeda pedda potholes unnai accidents avutunnai",
            "{ward} mein {landmark} ke paas road bahut damaged hai pls repair",
            "Footpath broken and road caved near {ward} {landmark} chala danger",
            "{ward} main rd full cracks and potholes near {landmark} urgently fix",
        ],
    },
    "Electricity": {
        "en": [
            "Frequent power cuts for {days} hours daily in {ward} near {landmark}",
            "Sparking from electric transformer beside {landmark} in {ward}",
            "Streetlights not working along {ward} {landmark} making area dark",
            "Low hanging live wire near {landmark} in {ward} posing electrocution risk",
            "Severe voltage fluctuation damaging appliances in {ward} homes",
            "Electric pole tilted dangerously after storm near {ward} {landmark}",
            "Feeder fuse blowing repeatedly at {ward} substation transformer",
            "Street lamp flickering continuously outside {landmark} in {ward}",
        ],
        "te": [
            "{ward} {landmark} వద్ద {days} రోజులుగా తరచుగా కరెంట్ కోతలు ఉంటున్నాయి",
            "{ward} లో ట్రాన్స్‌ఫార్మర్ నుండి నిప్పురవ్వలు వస్తున్నాయి అత్యవసరం",
            "{ward} {landmark} వీధిలో వీధి దీపాలు వెలగడం లేదు రాత్రి చీకటిగా ఉంది",
            "{ward} లో కరెంట్ వైర్లు కిందికి వేలాడుతున్నాయి ప్రమాదకరం",
            "{ward} కాలనీలో వోల్టేజ్ హెచ్చుతగ్గుల వల్ల ఇంట్లో పరికరాలు పాడవుతున్నాయి",
        ],
        "hi": [
            "{ward} में {landmark} के पास {days} दिनों से बार-बार बिजली कटौती हो रही है",
            "{ward} के ट्रांसफार्मर से चिंगारी निकल रही है तुरंत जांच करें",
            "{ward} {landmark} रोड की स्ट्रीट लाइट्स कई दिनों से बंद हैं",
            "{ward} में बिजली का खंभा झुक गया है और तार नीचे लटक रहे हैं",
            "{ward} इलाके में वोल्टेज फ्लक्चुएशन से उपकरण जल रहे हैं",
        ],
        "mixed": [
            "{ward} lo {landmark} dagara transformer sparking avutundi urgent",
            "{ward} mein street lights nahi jal rahi near {landmark} since {days} days",
            "Power outage in {ward} near {landmark} chala problem ga undi",
            "Loose electric wire hanging near {ward} {landmark} danger of shock",
        ],
    },
    "Sanitation": {
        "en": [
            "Garbage not collected for {days} days in {ward} near {landmark}",
            "Overflowing sewage manhole flooding lane at {ward} {landmark}",
            "Open drainage clogged with plastic waste causing mosquitoes in {ward}",
            "Overflowing community dustbins spreading foul smell in {ward} {landmark}",
            "Dead animal carcass lying near {landmark} in {ward} needs clearing",
            "Construction waste dumped illegally on street side in {ward}",
            "Blocked storm drain creating stagnant dirty water around {ward} {landmark}",
            "Sweeping staff absent for a week in {ward} residential lanes",
        ],
        "te": [
            "{ward} {landmark} వద్ద {days} రోజులుగా చెత్త సేకరణ జరగడం లేదు",
            "{ward} లో డ్రైనేజీ మ్యాన్‌హోల్ పొంగి మురుగు నీరు రోడ్డుపై పారుతోంది",
            "{ward} {landmark} దగ్గర డస్ట్‌బిన్లు నిండి దుర్వాసన వస్తోంది దోమలు పెరిగాయి",
            "{ward} కాలనీలో ఓపెన్ నాలా చెత్తతో మూసుకుపోయింది శుభ్రం చేయండి",
            "{ward} లో వీధి పారిశుధ్య పనులు వారం రోజులుగా జరగడం లేదు",
        ],
        "hi": [
            "{ward} में {landmark} के पास {days} दिनों से कचरा गाड़ी नहीं आई है",
            "{ward} की सीवर लाइन ओवरफ्लो हो रही है और गंदा पानी सड़क पर बह रहा है",
            "{ward} {landmark} के पास कूड़ेदान भरे पड़े हैं और बदबू से मच्छर बढ़ रहे हैं",
            "{ward} में खुली नाली जाम होने से गंदगी फैल रही है सफाई करवाएं",
            "{ward} कॉलोनी में सड़क किनारे कचरे का ढेर लगा हुआ है",
        ],
        "mixed": [
            "{ward} lo {landmark} dagara {days} days nundi garbage collect cheyaledu",
            "{ward} mein drainage overflow ho raha hai near {landmark} bad smell",
            "Sewage water leaking on street at {ward} {landmark} mosquito issue",
            "Dustbins overflowing in {ward} near {landmark} pls clean urgently",
        ],
    },
    "Traffic": {
        "en": [
            "Traffic signal stuck on red at {ward} {landmark} causing long jam",
            "Wrong side driving and illegal parking choking {ward} {landmark}",
            "Heavy congestion during office hours near {landmark} in {ward}",
            "Missing zebra crossing and traffic signage outside {ward} {landmark}",
            "Minor vehicle collision and signal pole damage at {ward} {landmark}",
            "Auto rickshaws blocking lane turn at {ward} {landmark} junction",
            "No traffic police deployment at peak hour in {ward} {landmark}",
            "Broken divider reflector causing night confusion near {ward} {landmark}",
        ],
        "te": [
            "{ward} {landmark} జంక్షన్ వద్ద ట్రాఫిక్ సిగ్నల్ పనిచేయక భారీ జామ్ అవుతోంది",
            "{ward} లో రాంగ్ రూట్ డ్రైవింగ్ మరియు అక్రమ పార్కింగ్ వల్ల ట్రాఫిక్ నిలిచిపోతోంది",
            "{ward} {landmark} వద్ద జీబ్రా క్రాసింగ్ మరియు సిగ్నల్ బోర్డులు లేవు",
            "{ward} ప్రధాన కూడలిలో రద్దీ సమయంలో ట్రాఫిక్ పోలీసులు లేరు",
            "{ward} {landmark} దగ్గర రోడ్డు ప్రమాదం జరిగి వాహనాలు నిలిచిపోయాయి",
        ],
        "hi": [
            "{ward} के {landmark} चौराहे पर ट्रैफिक सिग्नल खराब है लंबा जाम लगा है",
            "{ward} में गलत दिशा में वाहन चलाने और अवैध पार्किंग से जाम लग रहा है",
            "{ward} {landmark} के पास जेब्रा क्रॉसिंग और ट्रैफिक साइन बोर्ड नहीं हैं",
            "{ward} जंक्शन पर पीक आवर्स में ट्रैफिक पुलिस तैनात की जाए",
            "{ward} में सिग्नल लाइट टूटने से दुर्घटना की स्थिति बन रही है",
        ],
        "mixed": [
            "{ward} {landmark} junction lo traffic signal pani cheyatledu heavy jam",
            "{ward} mein {landmark} par illegal parking se traffic block hai",
            "Signal stuck at {ward} {landmark} causing huge rush and accident risk",
            "{ward} junction pe traffic police chahiye peak hours mein",
        ],
    },
    "General / Other": {
        "en": [
            "Stray dog menace near {landmark} in {ward} troubling pedestrians",
            "Park benches and boundary fence broken in {ward} {landmark}",
            "Noise pollution from late night loudspeakers near {ward} {landmark}",
            "Encroachment of public pavement shops around {ward} {landmark}",
            "Tree branches overhanging dangerously above walkway in {ward}",
            "Public toilet locked and unmaintained near {ward} {landmark}",
            "Request for CCTV camera installation at {ward} {landmark}",
            "Abandoned rusted vehicle lying for months in {ward} {landmark}",
        ],
        "te": [
            "{ward} {landmark} దగ్గర వీధి కుక్కల బెడద ఎక్కువగా ఉంది చర్యలు తీసుకోండి",
            "{ward} పార్క్‌లో బెంచీలు మరియు గేటు విరిగిపోయాయి",
            "{ward} {landmark} వద్ద రాత్రిపూట లౌడ్ స్పీకర్ శబ్ద కాలుష్యం ఎక్కువగా ఉంది",
            "{ward} లో పాదచారుల దారిని ఆక్రమించుకున్నారు తొలగించండి",
            "{ward} {landmark} సమీపంలో పబ్లిక్ టాయిలెట్ మూసివేసి ఉంది",
        ],
        "hi": [
            "{ward} में {landmark} के पास आवारा कुत्तों का आतंक बढ़ गया है",
            "{ward} पार्क की बेंच और चारदीवारी टूटी हुई है कृपया ठीक कराएं",
            "{ward} {landmark} के पास रात में तेज लाउडस्पीकर से शोर प्रदूषण हो रहा है",
            "{ward} में सार्वजनिक फुटपाथ पर अतिक्रमण हटाने की कृपा करें",
            "{ward} {landmark} के नजदीक सार्वजनिक शौचालय बंद पड़ा है",
        ],
        "mixed": [
            "{ward} lo {landmark} dagara stray dogs chala unnai pls check",
            "{ward} mein {landmark} ke paas park bench broken hai",
            "Noise pollution late night near {ward} {landmark} chala disturbance",
            "Public footpath encroachment in {ward} near {landmark} remove pls",
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


def generate_dataset(n_rows: int = 5500, seed: int = 42, out_path: Path | None = None) -> Path:
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

    fieldnames = list(rows[0].keys())
    with open(target, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return target


if __name__ == "__main__":
    out = generate_dataset(5500, seed=42)
    print(f"Generated 5,500 synthetic complaints -> {out}")
