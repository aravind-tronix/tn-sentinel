---
name: tn-crime-analyst
description: Tamil Nadu crime intelligence domain knowledge — districts, categories, viral scoring
---

## Tamil Nadu Crime Intelligence — Domain Knowledge

### The 38 Tamil Nadu Districts (always use these exact lowercase names)

| District | Major cities / towns within it |
|---|---|
| chennai | Chennai city, Kodambakkam, Adyar, T. Nagar, Anna Nagar, Perambur, Royapuram, Mylapore |
| coimbatore | Coimbatore city, Tiruppur (separate district), Pollachi, Mettupalayam |
| madurai | Madurai city, Melur, Usilampatti, Thirumangalam |
| tiruchirappalli | Trichy city, Srirangam, Lalgudi, Musiri |
| salem | Salem city, Mettur, Omalur, Attur |
| tirunelveli | Tirunelveli city, Palayamkottai, Tenkasi (separate district) |
| vellore | Vellore city, Ranipet (separate), Tirupattur (separate), Ambur |
| erode | Erode city, Gobichettipalayam, Bhavani |
| thoothukudi | Thoothukudi (Tuticorin) city, Kovilpatti, Ottapidaram |
| dindigul | Dindigul city, Kodaikanal, Palani |
| kanchipuram | Kanchipuram city, Chengalpattu (separate district) |
| krishnagiri | Krishnagiri city, Hosur, Denkanikottai |
| namakkal | Namakkal city, Rasipuram, Tiruchengode |
| theni | Theni city, Bodinayakkanur, Uthamapalayam |
| karur | Karur city, Kulithalai |
| dharmapuri | Dharmapuri city, Harur, Pennagaram |
| nilgiris | Ooty (Udhagamandalam), Coonoor, Gudalur, Kotagiri |
| ariyalur | Ariyalur city, Udayarpalayam |
| perambalur | Perambalur city, Kunnam |
| cuddalore | Cuddalore city, Chidambaram, Panruti, Neyveli |
| villupuram | Villupuram city, Tindivanam, Gingee, Kallakurichi (separate district) |
| nagapattinam | Nagapattinam city, Mayiladuthurai (separate district), Vedaranyam |
| thanjavur | Thanjavur city, Kumbakonam, Papanasam |
| tiruvarur | Tiruvarur city, Mannargudi |
| pudukkottai | Pudukkottai city, Aranthangi |
| sivaganga | Sivaganga city, Karaikudi, Devakottai |
| virudhunagar | Virudhunagar city, Sivakasi, Rajapalayam |
| ramanathapuram | Ramanathapuram city, Rameswaram, Paramakudi |
| tenkasi | Tenkasi city, Sankarankovil, Kadayanallur |
| kanyakumari | Nagercoil, Marthandam, Colachel |
| tiruppur | Tiruppur city, Kangeyam, Dharapuram |
| ranipet | Ranipet city, Arcot, Walajah |
| chengalpattu | Chengalpattu city, Tambaram, Mahabalipuram |
| tirupattur | Tirupattur city, Vaniyambadi, Jolarpet |
| kallakurichi | Kallakurichi city, Sankarapuram, Ulundurpet |
| mayiladuthurai | Mayiladuthurai city, Sirkazhi, Poompuhar |

### Crime Category Definitions

- **Homicide**: Murder, attempt to murder, culpable homicide, honour killing, mob lynching resulting in death. NOT suicide unless victim was killed.
- **Theft**: Robbery, burglary, chain snatching, vehicle theft, shoplifting, dacoity, looting. Includes attempted robbery.
- **Cybercrime**: Online fraud, phishing, identity theft, hacking, social media impersonation, SIM swap fraud, crypto scam.
- **Assault**: Physical attack, grievous hurt, stabbing (non-fatal), acid attack, gang assault, domestic violence with injury.
- **Narcotics**: Drug seizure, ganja/cocaine/heroin trafficking, drunk-driving if narcotics-specific, illicit liquor poisoning (hooch).
- **Road Accident**: Hit-and-run, vehicle collision causing death/injury with criminal negligence. Natural road crash = NOT crime unless negligence/drunk.
- **Sexual Offence**: Rape, sexual assault, POCSO cases, molestation, voyeurism, trafficking of women.
- **Fraud**: Financial fraud, insurance fraud, land grabbing, cheating, impersonation for financial gain, fake job scams.

### Viral Score Calibration (0-100)

| Scenario | Score range |
|---|---|
| Murder with multiple victims / children | 80-95 |
| Rape / sexual assault on minor | 75-90 |
| Honour killing / mob lynching | 75-88 |
| Gang murder / organised crime | 70-85 |
| Single murder, accused arrested | 55-70 |
| Large drug bust (>1 kg) | 60-75 |
| Acid attack | 65-80 |
| Major cybercrime (>₹10 lakh) | 50-65 |
| Chain snatching / vehicle theft | 20-40 |
| Minor assault, no serious injury | 20-35 |
| Small narcotics seizure | 25-40 |
| Routine petty fraud | 15-30 |

### Triage Decision Guide

Accept (YES) if: the article's main subject is a specific, occurred criminal incident.

Reject (NO) if:
- The article is PRIMARILY about a court hearing, bail order, verdict, or legal procedure
- It quotes politicians reacting to a past crime without describing the crime itself
- The incident is an accident caused by equipment failure, natural disaster, or infrastructure failure (not criminal negligence)
- It is a general crime review, weekly round-up, or awareness article
