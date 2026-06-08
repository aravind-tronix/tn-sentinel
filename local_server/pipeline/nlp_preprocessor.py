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
    "dharmapuri", "dindigul", "erode", "kallakurichi", "kanyakumari",
    "karur", "krishnagiri", "madurai", "mayiladuthurai", "nagapattinam",
    "namakkal", "nilgiris", "perambalur", "pudukkottai", "ramanathapuram",
    "ranipet", "salem", "sivaganga", "tenkasi", "thanjavur",
    "theni", "thoothukudi", "tiruchirappalli", "tirunelveli", "tiruppur",
    "tiruvallur", "tiruvannamalai", "tiruvarur", "vellore", "villupuram",
    "virudhunagar"
]

# City/Town names mapped to their districts
CITY_TO_DISTRICT = {
    "coimbatore": "coimbatore",
    "madurai": "madurai",
    "salem": "salem",
    "tiruchirappalli": "tiruchirappalli",
    "trichy": "tiruchirappalli",
    "erode": "erode",
    "vellore": "vellore",
    "tirunelveli": "tirunelveli",
    "nellai": "tirunelveli",
    "kanyakumari": "kanyakumari",
    "thanjavur": "thanjavur",
    "tanjore": "thanjavur",
    "tiruvannamalai": "tiruvannamalai",
    "tamil nadu": "unknown",
    "tn": "unknown",
    "chennai": "chennai",
    "madras": "chennai",
    "krishnagiri": "krishnagiri",
    "tiruppur": "tiruppur",
    "dindigul": "dindigul",
    "villupuram": "villupuram",
    "cuddalore": "cuddalore",
    "nagapattinam": "nagapattinam",
    "ramanathapuram": "ramanathapuram",
    "thoothukudi": "thoothukudi",
    "tuticorin": "thoothukudi",
    "pudukkottai": "pudukkottai",
    "puducherry": "unknown",
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
        "persons": [normalize_person_name(e.text) for e in doc.ents if e.label_ == "PERSON"],
        "organizations": [e.text for e in doc.ents if e.label_ == "ORG"],
        "locations": [e.text for e in doc.ents if e.label_ in ("GPE", "LOC")],
    }

    entities["persons"] = [
        person for person in entities["persons"]
        if person and person.lower() not in PERSON_NOISE and len(person.split()) <= 4
    ]

    text_lower = text.lower()
    detected_district = None
    
    # Stage 1: exact district keyword match in raw text
    detected_district = next((d for d in TN_DISTRICTS if d in text_lower), None)
    
    # Stage 2: exact city/town name match in raw text
    if not detected_district:
        for city, district in CITY_TO_DISTRICT.items():
            if city in text_lower and district != "unknown":
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
