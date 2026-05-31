"""
Resend Alzheimer guideline — exact content from original email.
Step 1: python3 resend_alzheimer_guideline.py          → sends only to TEST_EMAIL
Step 2: python3 resend_alzheimer_guideline.py --all    → sends to all who haven't received it
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from digest import build_guidelines_html_email, generate_preferences_token

SEND_ALL   = "--all" in sys.argv
TEST_EMAIL = "vincenzolate95l@gmail.com"

GUIDELINE = {
    "macro_topic":  "Dementia and Alzheimer's Disease",
    "specific_topic": "Anti-amyloid monoclonal antibody therapy (lecanemab/aducanumab) in early symptomatic Alzheimer's disease",
    "guideline_headline": (
        "In appropriately selected patients with mild cognitive impairment or mild dementia due to "
        "Alzheimer's disease and biomarker-confirmed amyloid pathology, lecanemab is recommended "
        "with structured ARIA surveillance MRI, while APOE ε4 homozygotes and patients on "
        "anticoagulation require particularly cautious risk-benefit discussion."
    ),
    "themes": [
        {
            "title": "PATIENT SELECTION AND CONFIRMATION OF AMYLOID PATHOLOGY",
            "body": (
                "Appropriate use recommendations from the Alzheimer's Association Workgroup (AUR, Cummings et al., 2023) "
                "and the AAIC/CTAD consensus restrict lecanemab to patients with MCI or mild dementia due to AD "
                "(CDR 0.5–1.0, MMSE ≥22), with biomarker confirmation of cerebral amyloid by amyloid PET or CSF "
                "Aβ42/40 and p-tau profile. Patients with moderate or severe dementia, non-amnestic atypical "
                "presentations without biomarker support, or significant cerebrovascular burden were excluded from "
                "CLARITY-AD and fall outside guideline-endorsed use.\n"
                "The European Academy of Neurology position statement (Frisoni et al., EAN, 2024) emphasizes a "
                "comprehensive cognitive and functional baseline, exclusion of confounding comorbidities, and shared "
                "decision-making documenting that the modest clinical effect (~27% slowing of CDR-SB decline over "
                "18 months) is balanced against meaningful ARIA risk. APOE ε4 genotyping is now considered essential "
                "pre-treatment, as homozygotes have substantially elevated ARIA-E and ARIA-H risk."
            ),
            "implication": "Confirm amyloid positivity by PET or CSF and obtain APOE genotype before initiating anti-amyloid therapy.",
        },
        {
            "title": "BASELINE MRI AND CONTRAINDICATIONS",
            "body": (
                "A baseline MRI within 12 months of initiation is mandatory (AUR, 2023; FDA label). Exclusionary "
                "findings include >4 cerebral microbleeds, any prior macrohemorrhage >1 cm, superficial siderosis, "
                "vasogenic edema, or evidence of severe cerebral amyloid angiopathy. Patients with a history of "
                "spontaneous ICH or suspected CAA-related inflammation should not be treated.\n"
                "Guidelines (AUR, 2023; AAN practice advisory commentary) recommend against concurrent therapeutic "
                "anticoagulation due to increased risk of symptomatic ICH observed in CLARITY-AD open-label extension. "
                "Antiplatelet monotherapy is permissible. Caution is advised in patients with uncontrolled hypertension, "
                "seizure history, or immunologic conditions requiring chronic immunosuppression."
            ),
            "implication": "Obtain baseline brain MRI with susceptibility-weighted sequences and screen for anticoagulant use before the first infusion.",
        },
        {
            "title": "ARIA SURVEILLANCE AND MANAGEMENT",
            "body": (
                "ARIA-E (vasogenic edema) and ARIA-H (microhemorrhages/siderosis) occur in approximately 21% of "
                "lecanemab-treated patients overall and in ~45% of APOE ε4 homozygotes (CLARITY-AD; AUR, 2023). "
                "Guidelines mandate surveillance MRI prior to the 5th, 7th, and 14th infusions, with additional scans "
                "for new neurological symptoms (headache, confusion, focal deficits, seizures, visual disturbance).\n"
                "The ARIA management algorithm (Cogswell et al., 2022; incorporated into AUR 2023) stratifies by "
                "radiographic severity and symptoms: asymptomatic mild ARIA-E generally allows continued dosing with "
                "close monitoring; moderate or symptomatic ARIA-E requires suspension until radiographic resolution; "
                "severe or recurrent ARIA-E warrants permanent discontinuation. ARIA-H thresholds for discontinuation "
                "include >10 new microbleeds or any new macrohemorrhage. Corticosteroids may be considered for "
                "symptomatic ARIA-E, though evidence is limited to case series."
            ),
            "implication": "Schedule protocol-driven MRIs before the 5th, 7th, and 14th infusions and have a written ARIA management algorithm at the infusion site.",
        },
        {
            "title": "INFUSION LOGISTICS AND DURATION OF THERAPY",
            "body": (
                "Lecanemab is administered 10 mg/kg IV every 2 weeks; donanemab (FDA-approved July 2024) is administered "
                "700 mg monthly for 3 doses then 1400 mg monthly, with consideration of stopping based on amyloid PET "
                "clearance (TRAILBLAZER-ALZ 2; appropriate use recommendations, Rabinovici et al., 2025). Optimal "
                "treatment duration for lecanemab remains undefined, but consensus suggests reassessment of benefit at "
                "18 months with continued therapy if clinical stability persists.\n"
                "Infusion-related reactions occur in ~26% of lecanemab patients, mostly first infusion, and are managed "
                "with premedication and rate adjustment. Discontinuation is recommended for progression to moderate "
                "dementia (CDR ≥2) where evidence of benefit is absent."
            ),
            "implication": "Establish an infusion pathway with premedication protocols and predefined criteria for treatment discontinuation based on disease progression.",
        },
        {
            "title": "COUNSELING AND SHARED DECISION-MAKING",
            "body": (
                "Both AUR (2023) and EAN (2024) emphasize structured informed consent covering: the modest, time-limited "
                "symptomatic benefit; ARIA risk stratified by APOE genotype; the burden of biweekly infusions and serial "
                "MRIs; and the unknown long-term effects. The EAN statement explicitly notes that the clinical "
                "meaningfulness of the observed effect remains debated and that treatment should not be offered without "
                "infrastructure for biomarker confirmation, MRI surveillance, and emergency ARIA management.\n"
                "APOE ε4 homozygotes warrant particularly detailed discussion given a roughly threefold ARIA-E rate and "
                "higher symptomatic ARIA incidence. Some European bodies (including the EMA's initial 2024 negative "
                "opinion, later reversed conditionally in 2024) have recommended excluding homozygotes from treatment; "
                "U.S. guidance permits treatment with enhanced counseling."
            ),
            "implication": "Use a written, genotype-stratified informed consent document and document shared decision-making in the medical record.",
        },
    ],
    "key_recommendations": [
        "Restrict anti-amyloid therapy to patients with MCI or mild dementia due to AD with biomarker-confirmed amyloid pathology and CDR 0.5–1.0 (AUR, Cummings et al., 2023).",
        "Obtain APOE genotyping prior to treatment initiation and provide genotype-stratified ARIA risk counseling (AUR 2023; EAN, 2024).",
        "Perform baseline MRI and exclude patients with >4 microbleeds, prior macrohemorrhage, superficial siderosis, or probable CAA (AUR, 2023).",
        "Avoid concurrent therapeutic anticoagulation; antiplatelet monotherapy is acceptable (AUR, 2023).",
        "Conduct surveillance MRI before infusions 5, 7, and 14, and at any new neurological symptom, with predefined ARIA management algorithm (AUR, 2023; Cogswell et al., 2022).",
        "Treatment should be delivered only in centers with capacity for biomarker confirmation, MRI surveillance, and acute ARIA management (EAN, 2024).",
    ],
    "sources": [
        {
            "title": "Lecanemab: Appropriate Use Recommendations",
            "issuing_body": "Alzheimer's Association Workgroup (Cummings et al.)",
            "year": "2023",
            "doi": "10.1002/alz.13119",
            "url": "https://doi.org/10.1002/alz.13119",
        },
        {
            "title": "Donanemab: Appropriate Use Recommendations",
            "issuing_body": "Alzheimer's Association Workgroup (Rabinovici et al.)",
            "year": "2025",
            "doi": "",
            "url": "https://alz-journals.onlinelibrary.wiley.com/doi/10.1002/alz.14806",
        },
        {
            "title": "European Academy of Neurology and European Alzheimer's Disease Consortium position statement on lecanemab",
            "issuing_body": "EAN / EADC (Frisoni et al.)",
            "year": "2024",
            "doi": "10.1111/ene.16379",
            "url": "https://doi.org/10.1111/ene.16379",
        },
        {
            "title": "Amyloid-related imaging abnormalities with emerging Alzheimer disease therapeutics: detection and reporting recommendations for clinical practice",
            "issuing_body": "Cogswell et al., AJNR/ASNR",
            "year": "2022",
            "doi": "10.3174/ajnr.A7583",
            "url": "https://doi.org/10.3174/ajnr.A7583",
        },
        {
            "title": "Lecanemab in Early Alzheimer's Disease (CLARITY-AD)",
            "issuing_body": "Van Dyck et al., NEJM",
            "year": "2023",
            "doi": "10.1056/NEJMoa2212948",
            "url": "https://doi.org/10.1056/NEJMoa2212948",
        },
    ],
    "bottom_line": (
        "Anti-amyloid monoclonal antibody therapy is now a guideline-endorsed disease-modifying option for early "
        "symptomatic AD, but its safe delivery requires biomarker-confirmed amyloid pathology, mandatory APOE "
        "genotyping, structured MRI-based ARIA surveillance, exclusion of patients on anticoagulation or with "
        "significant CAA burden, and a center with infrastructure to manage ARIA."
    ),
}

SUBJECT = "NeuroDigest Guidelines — Anti-amyloid monoclonal antibody therapy (lecanemab/aducanumab) in early symptomatic Alzheimer's disease"


def main():
    from supabase import create_client
    import resend as resend_lib

    api_key   = os.getenv("RESEND_API_KEY", "")
    from_addr = os.getenv("RESEND_FROM", "NeuroDigest <digest@neuro-digest.com>")
    site_url  = os.getenv("SITE_URL", "https://neuro-digest-phi.vercel.app")
    resend_lib.api_key = api_key

    sb = create_client(
        os.getenv("SUPABASE_URL", ""),
        os.getenv("SUPABASE_SERVICE_KEY", ""),
    )

    if not SEND_ALL:
        # ── Test: send only to vincenzo ───────────────────────────────────────
        print(f"Sending test to {TEST_EMAIL}...")
        token = generate_preferences_token(TEST_EMAIL)
        html  = build_guidelines_html_email(GUIDELINE, token=token, site_url=site_url)
        resend_lib.Emails.send({
            "from":    from_addr,
            "to":      TEST_EMAIL,
            "subject": SUBJECT,
            "html":    html,
        })
        print(f"✅  Sent to {TEST_EMAIL} — check your inbox, then run with --all")
        return

    # ── Full send ─────────────────────────────────────────────────────────────

    # Save to Supabase first (so sends_log works)
    digest_id = None
    html_base = build_guidelines_html_email(GUIDELINE, site_url=site_url)
    try:
        # Check if already saved
        existing = sb.table("digests").select("id").eq("subject", SUBJECT).execute()
        if existing.data:
            digest_id = existing.data[0]["id"]
            print(f"  Digest already in Supabase (id={digest_id})")
        else:
            row = sb.table("digests").insert({
                "subject":     SUBJECT,
                "html":        html_base,
                "plain":       f"NeuroDigest Guidelines — Alzheimer's Disease\n\n{GUIDELINE['bottom_line']}",
                "digest_json": json.dumps(GUIDELINE),
            }).execute()
            if row.data:
                digest_id = row.data[0]["id"]
                print(f"  Saved to Supabase (id={digest_id})")
    except Exception as e:
        print(f"  Supabase save error: {e}")

    # Fetch confirmed subscribers
    subs = sb.table("subscribers").select("email,topics").eq("status", "confirmed").execute()
    all_subs = subs.data or []
    print(f"  {len(all_subs)} confirmed subscribers")

    # Who already received it?
    already_sent = set()
    if digest_id:
        sent_rows = sb.table("sends_log").select("email").eq("digest_id", digest_id).execute()
        already_sent = {r["email"] for r in (sent_rows.data or [])}
    # Also exclude the test send to vincenzo (already received)
    already_sent.add(TEST_EMAIL)

    to_send = [s for s in all_subs if s["email"] not in already_sent]
    print(f"  {len(already_sent)} already received it (including test send to you)")
    print(f"  {len(to_send)} subscriber(s) to send to now")

    if not to_send:
        print("✅  Everyone already has it.")
        return

    sent_addrs = []
    for sub in to_send:
        email = sub["email"]
        token = generate_preferences_token(email)
        html  = build_guidelines_html_email(GUIDELINE, token=token, site_url=site_url)
        try:
            resend_lib.Emails.send({
                "from":    from_addr,
                "to":      email,
                "subject": SUBJECT,
                "html":    html,
            })
            sent_addrs.append(email)
            print(f"  ✓ {email}")
            time.sleep(0.15)
        except Exception as e:
            print(f"  ✗ {email}: {e}")

    if sent_addrs and digest_id:
        sb.table("sends_log").upsert(
            [{"email": e, "digest_id": digest_id} for e in sent_addrs],
            on_conflict="email,digest_id", ignore_duplicates=True,
        ).execute()

    if sent_addrs:
        try:
            sb.table("guidelines_log").insert({
                "macro_topic":    GUIDELINE["macro_topic"],
                "specific_topic": GUIDELINE["specific_topic"],
            }).execute()
        except Exception:
            pass

    print(f"\n✅  Done — sent to {len(sent_addrs)}/{len(to_send)}")


if __name__ == "__main__":
    main()
