import spacy
from spacy.language import Language
from spacy_langdetect import LanguageDetector
from typing import Dict
from difflib import SequenceMatcher

@Language.factory("language_detector")
def create_language_detector(nlp, name):
    return LanguageDetector()

nlp_en = spacy.load("en_core_web_sm")
nlp_en.add_pipe("language_detector", last=True)

nlp_multi = spacy.load("xx_ent_wiki_sm")

TN_DISTRICTS = [
    "ariyalur", "chengalpattu", "chennai", "coimbatore", "cuddalore",
    "dharmapuri", "dindigul", "erode", "kallakurichi", "kanchipuram",
    "kanyakumari", "karur", "krishnagiri", "madurai", "mayiladuthurai",
    "nagapattinam", "namakkal", "nilgiris", "perambalur", "pudukkottai",
    "ramanathapuram", "ranipet", "salem", "sivaganga", "tenkasi", "thanjavur",
    "theni", "thoothukudi", "tiruchirappalli", "tirunelveli", "tirupattur",
    "tiruppur", "tiruvallur", "tiruvannamalai", "tiruvarur", "vellore",
    "villupuram", "virudhunagar"
]

# City/Town names mapped to their districts
CITY_TO_DISTRICT = {
    # Districts (canonical names map to themselves)
    "coimbatore": "coimbatore",
    "madurai": "madurai",
    "salem": "salem",
    "tiruchirappalli": "tiruchirappalli",
    "erode": "erode",
    "vellore": "vellore",
    "tirunelveli": "tirunelveli",
    "kanyakumari": "kanyakumari",
    "thanjavur": "thanjavur",
    "tiruvannamalai": "tiruvannamalai",
    "chennai": "chennai",
    "krishnagiri": "krishnagiri",
    "tiruppur": "tiruppur",
    "dindigul": "dindigul",
    "villupuram": "villupuram",
    "cuddalore": "cuddalore",
    "nagapattinam": "nagapattinam",
    "ramanathapuram": "ramanathapuram",
    "thoothukudi": "thoothukudi",
    "pudukkottai": "pudukkottai",
    "karur": "karur",
    "ariyalur": "ariyalur",
    "ranipet": "ranipet",
    "chengalpattu": "chengalpattu",
    "sivaganga": "sivaganga",
    "tenkasi": "tenkasi",
    "mayiladuthurai": "mayiladuthurai",
    "perambalur": "perambalur",
    "dharmapuri": "dharmapuri",
    "namakkal": "namakkal",
    "theni": "theni",
    "kanchipuram": "kanchipuram",
    "tirupattur": "tirupattur",
    "tiruvallur": "tiruvallur",
    "kallakurichi": "kallakurichi",
    "tiruvarur": "tiruvarur",
    "nilgiris": "nilgiris",
    "virudhunagar": "virudhunagar",

    # Common aliases / alternate spellings
    "trichy": "tiruchirappalli",
    "tiruchy": "tiruchirappalli",
    "tiruchy city": "tiruchirappalli",
    "tanjore": "thanjavur",
    "nellai": "tirunelveli",
    "tuticorin": "thoothukudi",
    "madras": "chennai",
    "ramnad": "ramanathapuram",
    "thiruvallur": "tiruvallur",
    "kancheepuram": "kanchipuram",
    "conjeevaram": "kanchipuram",
    "tirupattur": "tirupattur",

    # Towns / localities → their district
    "sulur": "coimbatore",
    "pollachi": "coimbatore",
    "mettupalayam": "coimbatore",
    "ooty": "nilgiris",
    "udhagamandalam": "nilgiris",
    "coonoor": "nilgiris",
    "hosur": "krishnagiri",
    "begur": "krishnagiri",
    "nagercoil": "kanyakumari",
    "marthandam": "kanyakumari",
    "kumbakonam": "thanjavur",
    "papanasam": "thanjavur",
    "tambaram": "chennai",
    "pallavaram": "chennai",
    "ambattur": "chennai",
    "avadi": "tiruvallur",
    "ponneri": "tiruvallur",
    "gummidipoondi": "tiruvallur",
    "arakkonam": "ranipet",
    "walajapet": "ranipet",
    "vellore city": "vellore",
    "katpadi": "vellore",
    "gudiyatham": "vellore",
    "tindivanam": "villupuram",
    "villupuram city": "villupuram",
    "vaniyambadi": "tirupattur",
    "ambur": "tirupattur",
    "virudhunagar city": "virudhunagar",
    "sivakasi": "virudhunagar",
    "sattur": "virudhunagar",
    "karaikudi": "sivaganga",
    "devakottai": "sivaganga",
    "paramakudi": "ramanathapuram",
    "rameswaram": "ramanathapuram",
    "palani": "dindigul",
    "kodaikanal": "dindigul",
    "periyakulam": "theni",
    "bodinayakkanur": "theni",
    "kovilpatti": "thoothukudi",
    "nallur": "tirunelveli",
    "tenkasi city": "tenkasi",
    "courtallam": "tenkasi",
    "sankarankovil": "tenkasi",
    "mayiladuthurai city": "mayiladuthurai",
    "sirkazhi": "nagapattinam",
    "velankanni": "nagapattinam",
    "mannargudi": "tiruvarur",
    "papanasam tiruvarur": "tiruvarur",
    "aruppukkottai": "virudhunagar",
    "bhavani": "erode",
    "gobichettipalayam": "erode",
    "namakkal city": "namakkal",
    "rasipuram": "namakkal",
    "attur": "salem",
    "mettur": "salem",
    "yercaud": "salem",
    "harur": "dharmapuri",
    "pappireddipatti": "dharmapuri",

    # State-level / outside TN → unknown
    "tamil nadu": "unknown",
    "tn": "unknown",
    "puducherry": "unknown",
    "pondicherry": "unknown",

    # Tamil script district names
    "சென்னை": "chennai",
    "கோயம்புத்தூர்": "coimbatore",
    "மதுரை": "madurai",
    "திருச்சிராப்பள்ளி": "tiruchirappalli",
    "திருச்சி": "tiruchirappalli",
    "சேலம்": "salem",
    "திருநெல்வேலி": "tirunelveli",
    "வேலூர்": "vellore",
    "ஈரோடு": "erode",
    "தூத்துக்குடி": "thoothukudi",
    "தூத்துக்குடி": "thoothukudi",
    "திண்டுக்கல்": "dindigul",
    "கன்னியாகுமரி": "kanyakumari",
    "தஞ்சாவூர்": "thanjavur",
    "திருவண்ணாமலை": "tiruvannamalai",
    "கிருஷ்ணகிரி": "krishnagiri",
    "நாமக்கல்": "namakkal",
    "தேனி": "theni",
    "கரூர்": "karur",
    "தர்மபுரி": "dharmapuri",
    "நீலகிரி": "nilgiris",
    "அரியலூர்": "ariyalur",
    "பெரம்பலூர்": "perambalur",
    "கடலூர்": "cuddalore",
    "விழுப்புரம்": "villupuram",
    "நாகப்பட்டினம்": "nagapattinam",
    "புதுக்கோட்டை": "pudukkottai",
    "சிவகங்கை": "sivaganga",
    "விருதுநகர்": "virudhunagar",
    "இராமநாதபுரம்": "ramanathapuram",
    "தென்காசி": "tenkasi",
    "திருப்பூர்": "tiruppur",
    "ரானிப்பேட்டை": "ranipet",
    "செங்கல்பட்டு": "chengalpattu",
    "திருப்பத்தூர்": "tirupattur",
    "கள்ளக்குறிச்சி": "kallakurichi",
    "மயிலாடுதுறை": "mayiladuthurai",
    "திருவள்ளூர்": "tiruvallur",
    "திருவாரூர்": "tiruvarur",
    "கஞ்சிபுரம்": "kanchipuram",
}

CRIME_KEYWORDS = {
    "Homicide": ["murder", "killed", "stabbed", "shot", "found dead", "homicide"],
    "Theft": ["stolen", "robbery", "burglary", "loot", "theft", "snatched"],
    "Cybercrime": ["hacked", "phishing", "scam", "online fraud", "data breach"],
    "Assault": ["assault", "attacked", "beaten", "injured", "violence"],
    "Narcotics": ["drugs", "ganja", "cocaine", "heroin", "narcotics", "smuggled"],
    "Road Accident": ["road accident", "crash", "collision", "hit-and-run", "fatal accident"],
    "Sexual Offence": ["rape", "molestation", "sexual assault", "sexual offence"],
    "Fraud": ["fraud", "cheated", "fake scheme", "fraudulent", "forgery"],
}

PERSON_NOISE = {
    "tamil nadu", "kerala", "karnataka", "india",
    "kerala kaumudi", "the news minute", "the hindu",
    "new indian express", "bjp", "tvk", "dmk", "aiadmk",
}

KEYWORDS_FLATTENED = {
    category: keywords
    for category, keywords in CRIME_KEYWORDS.items()
}


def normalize_person_name(name: str) -> str:
    return name.strip()


def preprocess(article: Dict) -> Dict:
    text = (article.get("text", "") or article.get("title", "")).strip()
    if not text:
        return {**article, "entities": {}, "detected_district": None, "detected_category": None, "needs_llm": True}

    doc = nlp_multi(text) if article.get("language") == "ta" else nlp_en(text)

    entities = {
        "persons": list(dict.fromkeys(normalize_person_name(e.text) for e in doc.ents if e.label_ == "PERSON")),
        "organizations": list(dict.fromkeys(e.text for e in doc.ents if e.label_ == "ORG")),
        "locations": list(dict.fromkeys(e.text for e in doc.ents if e.label_ in ("GPE", "LOC"))),
    }

    entities["persons"] = [
        person for person in entities["persons"]
        if person and person.lower() not in PERSON_NOISE and len(person.split()) <= 4
    ]

    text_lower = text.lower()
    detected_district = None

    # Stage 1: exact district keyword match in raw text
    detected_district = next((d for d in TN_DISTRICTS if d in text_lower), None)

    # Stage 2: exact city/town name match in raw text (handles Tamil script too)
    if not detected_district:
        for city, district in CITY_TO_DISTRICT.items():
            needle = city if any(ord(c) > 127 for c in city) else city  # Tamil script: check original text
            haystack = text if any(ord(c) > 127 for c in city) else text_lower
            if needle in haystack and district != "unknown":
                detected_district = district
                break
    
    # Stage 3: fuzzy match across all entity text, not only LOC
    if not detected_district:
        all_entity_texts = (
            entities["persons"] +
            entities["organizations"] +
            entities["locations"]
        )
        for location in all_entity_texts:
            location_lower = location.lower()
            
            # Try fuzzy match against districts
            for district in TN_DISTRICTS:
                similarity = SequenceMatcher(None, location_lower, district).ratio()
                if similarity > 0.75:
                    detected_district = district
                    break
            
            # Try fuzzy match against city names
            if not detected_district:
                for city, district in CITY_TO_DISTRICT.items():
                    if district != "unknown":
                        similarity = SequenceMatcher(None, location_lower, city).ratio()
                        if similarity > 0.75:
                            detected_district = district
                            break
            
            if detected_district:
                break
    
    detected_category = next(
        (category for category, keywords in KEYWORDS_FLATTENED.items() if any(keyword in text_lower for keyword in keywords)),
        None,
    )

    return {
        **article,
        "text": text,
        "entities": entities,
        "detected_district": detected_district,
        "detected_category": detected_category,
        "needs_llm": detected_district is None or detected_category is None,
    }
