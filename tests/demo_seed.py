#!/usr/bin/env python3
"""An invented dataset, published into its own Postgres schema, to build and try the dashboard on.

  WBG_PG="postgresql:///work?host=/tmp" python3 tests/demo_seed.py --schema wbg_demo

Nothing here is real: the projects, the sentences, the procurement packages and the suppliers
are generated from templates with a seeded random generator (the same run always gives the same
rows), and no document text is used. The measures, their types, the assets and their kinds and
the flags are the real label files, so the dashboard shows the real names and definitions.

The tables the pipeline publishes are written directly with the pipeline's own loader
(load_pg.load_rows) and the real views are then built over them (load_pg.finish), so the
reader (src/wbg_portfolio/app.py) and the views are exactly the real ones. The schema is
dropped and rebuilt on every run (never `wbg`); its store schema (<schema>_store) is created
empty and never dropped. No model is called and nothing leaves the database.

To point the app at it: WBG_PG as above, WBG_SCHEMA=wbg_demo (and WBG_STORE=wbg_demo_store).
"""
import argparse
import datetime
import hashlib
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from wbg_portfolio import db  # noqa: E402
from wbg_portfolio import load_pg  # noqa: E402

ANCHOR = datetime.date(2026, 11, 20)          # "today" of the invented data
CLIMATE = "climate"

REGIONS = {
    "Africa": [("Kenya", "KE"), ("Tanzania", "TZ"), ("Ethiopia", "ET"), ("Nigeria", "NG"),
               ("Ghana", "GH"), ("Senegal", "SN"), ("Rwanda", "RW")],
    "East Asia and Pacific": [("Vietnam", "VN"), ("Indonesia", "ID"), ("Philippines", "PH"),
                              ("Fiji", "FJ"), ("Mongolia", "MN"), ("Cambodia", "KH")],
    "Europe and Central Asia": [("Moldova", "MD"), ("Georgia", "GE"), ("Armenia", "AM"),
                                ("Uzbekistan", "UZ"), ("Albania", "AL"), ("Kyrgyz Republic", "KG")],
    "Latin America and Caribbean": [("Colombia", "CO"), ("Peru", "PE"), ("Honduras", "HN"),
                                    ("Haiti", "HT"), ("Jamaica", "JM"), ("Ecuador", "EC")],
    "Middle East and North Africa": [("Morocco", "MA"), ("Jordan", "JO"), ("Tunisia", "TN"),
                                     ("Egypt", "EG"), ("Djibouti", "DJ")],
    "South Asia": [("Bangladesh", "BD"), ("Nepal", "NP"), ("Sri Lanka", "LK"), ("Pakistan", "PK"),
                   ("Maldives", "MV")],
}
REGIONAL = [("Eastern and Southern Africa", "3E", "Africa"), ("Western and Central Africa", "3W", "Africa")]
THEMES = ["Digital Connectivity and Resilience Project", "Digital Government Services Project",
          "Resilient Digital Infrastructure Project", "Data and Cloud Modernization Project",
          "Digital Economy Acceleration Project", "Climate-Smart Digital Services Project",
          "Digital Public Platforms Project", "Connected Communities Project"]
PRACTICES = [("Digital Development", "IDD"), ("Urban, Resilience and Land", "URL"),
             ("Energy and Extractives", "EEX"), ("Governance", "GOV")]
COMPONENTS = ["Digital infrastructure and connectivity", "Digital platforms and public services",
              "Data, cloud and hosting services", "Resilience and risk management",
              "Institutional strengthening and policy reform"]
AGENCIES = ["the Ministry of Digital Development", "the national ICT agency", "the telecommunications regulator",
            "the Ministry of Finance", "the national meteorological agency", "the disaster management authority"]
WHERE = ["in the coastal provinces", "in the northern districts", "across all regions",
         "in the capital and secondary cities", "in flood-prone districts", "at selected sites"]

# What each measure is about, as a noun phrase a sentence can carry (measure id -> phrase).
TOPIC = {
    "backup_power": "backup power and energy autonomy", "climate_governance": "climate-informed regulation and sector standards",
    "climate_information_systems": "climate and environmental information systems",
    "climate_informed_siting": "climate-informed siting and routing of digital infrastructure",
    "cra": "climate and disaster risk assessment", "cyber_security": "cybersecurity safeguards",
    "disaster_recovery": "data backup and disaster recovery", "energy_efficiency": "energy-efficient operation",
    "ewaste": "e-waste collection and management", "ews": "early warning and public alerting",
    "network_redundancy": "redundant network paths and connectivity", "renewable_energy": "renewable energy supply",
    "resilient_infrastructure_design": "hazard-resistant design and physical protection",
    "cloud_procurement": "procurement and management of cloud services",
    "cloud_strategy": "a government cloud strategy and adoption roadmap",
    "data_classification": "data classification and handling rules", "data_governance": "data governance and management",
    "data_sharing": "data sharing and interoperability", "datacenter_infrastructure": "modernization of datacenter infrastructure",
    "hosting_sovereignty": "data hosting, residency and sovereignty requirements",
    "hybrid_multicloud": "hybrid and multicloud environments", "private_government_cloud": "a private government cloud platform",
    "public_cloud_migration": "migration of government systems to the public cloud"}
POLICY_LEAN = {"climate_governance", "cloud_strategy", "data_classification", "data_governance", "data_sharing",
               "hosting_sovereignty", "cloud_procurement"}
# Which assets a measure is mostly about (measure id -> asset ids); others draw from all four.
PREFER = {"backup_power": ["hosting", "connectivity"], "ews": ["monitoring_control"],
          "network_redundancy": ["connectivity"], "climate_information_systems": ["monitoring_control", "platforms"],
          "cloud_procurement": ["hosting"], "cloud_strategy": ["hosting", "platforms"],
          "datacenter_infrastructure": ["hosting"], "private_government_cloud": ["hosting"],
          "public_cloud_migration": ["hosting", "platforms"], "data_sharing": ["platforms"],
          "disaster_recovery": ["hosting"], "hybrid_multicloud": ["hosting"], "ewaste": ["hosting", "connectivity"]}
ASSET_PHRASE = {
    "fibre": "the national fibre-optic backbone", "international": "submarine cable landing stations and international links",
    "mobile_wireless": "mobile base stations and towers", "satellite": "satellite connectivity sites",
    "institutional": "the government institutional network", "last_mile": "last-mile connections to schools and clinics",
    "data_centre": "the national data centre", "cloud": "the government cloud platform",
    "recovery_site": "the disaster recovery site", "ixp": "the national internet exchange point",
    "dpi": "the digital public infrastructure platforms", "sector_systems": "sector management information systems",
    "geospatial": "the national geospatial data platform", "hazard_warning": "hazard monitoring and warning systems",
    "operational_tech": "operational control systems for utilities"}
ASSET_DEFAULT = {"connectivity": "connectivity networks", "hosting": "data hosting facilities",
                 "platforms": "digital platforms and systems", "monitoring_control": "monitoring and control systems"}
INVEST = ["The project will finance {topic} for {asset} {where}{incl}.",
          "Under Component {n}, the project will support {topic}{incl}, covering {asset}.",
          "{Agency} will procure and install equipment and services for {topic}{incl} at {asset}.",
          "Activities under Component {n} include {topic}{incl} for {asset}, to be carried out by {agency}.",
          "The project will strengthen {asset} through {topic}{incl}, with works and goods financed under Component {n}."]
POLICY = ["The project will support {agency} in preparing and adopting {topic}{incl}{about}.",
          "Under Component {n}, technical assistance will help the government to define {topic}{incl}{about}.",
          "{Agency} will update national standards and guidelines on {topic}{incl}{about}.",
          "The project will finance the drafting of a policy framework for {topic}{incl}{about}."]
FOLLOW = ["The work will follow the technical standards for {topic} agreed with the Bank.",
          "{Agency} will be responsible for implementation, starting in the second year of the project.",
          "Detailed specifications for {topic} will be agreed during the first year of implementation.",
          "About {pct} percent of the cost of Component {n} is allocated to this activity."]
NOT_DIGITAL = ["The project will finance {topic} for {thing} {where}.",
               "Under Component {n}, {topic} will be applied to {thing}."]
THINGS = ["the rural road network", "the municipal water supply system", "district health clinics",
          "the irrigation scheme in the river basin", "primary school buildings"]
BACKGROUND = ["Floods and storms have damaged network sites in recent years, and {topic} remains limited.",
              "Existing arrangements for {topic} are fragmented across agencies.",
              "The government has identified {topic} as a priority in its national digital strategy."]
# measure id -> (procurement category, what the package buys)
BUYS = {
    "backup_power": ("goods", "uninterruptible power supply units and batteries"),
    "climate_governance": ("consultant_services", "drafting of climate-resilience standards for digital infrastructure"),
    "climate_information_systems": ("goods", "weather and hydrological monitoring stations"),
    "climate_informed_siting": ("consultant_services", "climate-informed site selection study"),
    "cra": ("consultant_services", "climate and disaster risk assessment of digital infrastructure"),
    "cyber_security": ("goods", "security monitoring and incident response tools"),
    "disaster_recovery": ("goods", "backup storage and disaster recovery site equipment"),
    "energy_efficiency": ("goods", "energy-efficient cooling and power equipment"),
    "ewaste": ("non_consulting_services", "collection and recycling of electronic waste"),
    "ews": ("goods", "early warning sirens and alert dissemination system"),
    "network_redundancy": ("works", "redundant fibre routes and ring closure works"),
    "renewable_energy": ("goods", "solar power systems for network sites"),
    "resilient_infrastructure_design": ("works", "flood protection and hardening works at network sites"),
    "cloud_procurement": ("non_consulting_services", "government cloud services subscription"),
    "cloud_strategy": ("consultant_services", "cloud strategy and adoption roadmap"),
    "data_classification": ("consultant_services", "data classification framework and handling guidelines"),
    "data_governance": ("consultant_services", "data governance framework and data steward training"),
    "data_sharing": ("goods", "data exchange platform and interoperability gateways"),
    "datacenter_infrastructure": ("works", "datacenter modernization works"),
    "hosting_sovereignty": ("consultant_services", "assessment of data hosting and residency requirements"),
    "hybrid_multicloud": ("goods", "hybrid cloud management platform"),
    "private_government_cloud": ("goods", "private cloud servers, storage and networking"),
    "public_cloud_migration": ("non_consulting_services", "migration of government applications to the public cloud")}
PREFIX = {"goods": "Supply and installation of", "works": "Works for", "consultant_services": "Consulting services for",
          "non_consulting_services": "Services for"}
FILLERS = [("consultant_services", "Project management and implementation support consultant"),
           ("goods", "Office equipment and furniture for the project unit"), ("consultant_services", "Annual external audit"),
           ("goods", "Vehicles for project implementation"), ("non_consulting_services", "Training workshops and study tours"),
           ("non_consulting_services", "Public communications campaign"), ("consultant_services", "Legal advisory services"),
           ("consultant_services", "Environmental and social monitoring"), ("goods", "Laptops and peripherals"),
           ("consultant_services", "Individual consultant for procurement")]
METHODS = {"goods": [("RFB", "Request for Bids"), ("RFQ", "Request for Quotations"), ("DIR", "Direct Selection")],
           "works": [("RFB", "Request for Bids"), ("RFQ", "Request for Quotations")],
           "consultant_services": [("QCBS", "Quality and Cost-Based Selection"), ("CQS", "Consultant's Qualification-Based Selection"),
                                   ("INDV", "Individual Consultant Selection"), ("QBS", "Quality-Based Selection")],
           "non_consulting_services": [("RFB", "Request for Bids"), ("RFQ", "Request for Quotations")]}
MARKETS = ["Open - International", "Open - National", "Limited - International", "Direct"]
AMOUNT = {"goods": (50e3, 4e6), "works": (300e3, 12e6), "consultant_services": (40e3, 1.5e6),
          "non_consulting_services": (30e3, 2e6)}
SUPPLIERS = [("Alder Networks Ltd", "United Kingdom"), ("Kestrel Telecom Services", "Kenya"),
             ("Marlow Systems SA", "Spain"), ("Tanager Engineering Co", "India"), ("Orchid Cloud Partners", "Singapore"),
             ("Redwood Infrastructure Group", "United States"), ("Lumen Works Consortium", "Turkiye"),
             ("Cobalt Power Solutions", "South Africa"), ("Harbour Data Ltd", "Ireland"), ("Pinegate Advisory", "Canada")]
OTH_TYPES = {"backup_power": ("microgrid", "Microgrid supply for critical sites"),
             "ews": ("community_alerts", "Community alert dissemination"),
             "cra": ("supply_chain", "Supply chain exposure review"),
             "cyber_security": ("incident_exchange", "Incident information exchange"),
             "data_governance": ("open_licence", "Open data licensing"),
             "disaster_recovery": ("recovery_drills", "Recovery drills and exercises"),
             "energy_efficiency": ("cooling", "Cooling optimization")}


def wchoice(rng, pairs):
    """One item of (weight, item) pairs."""
    return rng.choices([i for _, i in pairs], weights=[w for w, _ in pairs])[0]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def lower_first(text):
    return text if text[:2].isupper() else text[0].lower() + text[1:]


def iso(d):
    return d.isoformat() if d else ""


class Demo:
    """Everything the seed writes, built in memory: rows by table (CSV column names, as the
    loader takes them)."""

    def __init__(self, seed=7, n_projects=60):
        self.rng = random.Random(seed)
        self.n_projects = n_projects
        self.t = defaultdict(list)
        self.measures = [r for r in load_pg.label_rows() if r["label_set"] != "assets" and r["active"]]
        self.assets = [r for r in load_pg.label_rows() if r["label_set"] == "assets"]
        self.subtypes = defaultdict(list)
        for s in load_pg.subtype_rows():
            if s["active"]:
                self.subtypes[s["asset_id"]].append(s["subtype_id"])
        self.types = defaultdict(list)                       # measure -> [(type_id, type_name)]
        self.type_rows = load_pg.type_rows("/nonexistent")
        for r in self.type_rows:
            self.types[r["label_set"]].append((r["type_id"], r["type_name"]))
        for m, (slug, name) in OTH_TYPES.items():
            tid = "OTH." + slug
            self.type_rows.append({"label_set": m, "label_id": m, "type_id": tid, "type_name": "OTH. " + name,
                                   "definition": "Proposed from sentences no approved type reaches: " + name.lower() + ".",
                                   "merged_from": ""})
        self.oth = {m: ("OTH." + s, "OTH. " + n) for m, (s, n) in OTH_TYPES.items()}
        self.activity_rows = []                              # for the procurement links

    # ------------------------------------------------------------ projects
    def build(self):
        for i, (country, region) in enumerate(self.assign_places(), 1):
            self.project(i, country, region)
        return self

    def assign_places(self):
        rng = self.rng
        countries = [(c, code, r) for r, cs in REGIONS.items() for c, code in cs]
        rng.shuffle(countries)
        counts = [4] + [3] * 5 + [2] * 10 + [1] * 19        # 35 countries, 58 projects, one with four
        places = [countries[k] for k, n in enumerate(counts) for _ in range(n)]
        rng.shuffle(places)
        n_regional = 2 if self.n_projects >= 30 else 0
        places = places[:self.n_projects - n_regional]
        places += [(n, code, r) for n, code, r in REGIONAL[:n_regional]]
        rng.shuffle(places)
        return [((c, code), r) for c, code, r in places]

    def project(self, i, country, region):
        rng, pid = self.rng, f"PD{i:04d}"
        used = getattr(self, "_names", set())
        name = next(f"{country[0]} {t}" for t in rng.sample(THEMES, len(THEMES)) if f"{country[0]} {t}" not in used)
        used.add(name)
        self._names = used
        fy = rng.choices(range(2020, 2027), weights=[1, 1, 2, 2, 3, 3, 2])[0]
        approval = min(datetime.date(fy - 1, 7, 1) + datetime.timedelta(days=rng.randint(0, 363)), datetime.date(2026, 6, 20))
        closing = approval + datetime.timedelta(days=365 * rng.randint(5, 8) + rng.randint(0, 300))
        practice, pcode = rng.choice(PRACTICES)
        p = dict(project_id=pid, name=name, country=country, region=region, approval=approval, fy=fy)
        self.t["projects"].append({
            "project_id": pid, "cohort_set": wchoice(rng, [(3, "digital"), (1, "digital_contributor")]),
            "in_projects_interface": "true", "project_name": name, "status": "Active" if closing > ANCHOR else "Closed",
            "approval_date": iso(approval), "approval_fy": fy, "closing_date": iso(closing), "region": region,
            "country_code": country[1], "country_name": country[0], "lending_instrument": "Investment Project Financing",
            "commitment_usd_m": (c := rng.choice(range(15, 351, 5))), "commitment_v2_usd_m": c, "commitment_v3_usd_m": c,
            "record_source": "v2+v3", "practice": practice, "practice_code": pcode,
            "sectors": "Information and Communications Technologies (60%), Public Administration (40%)",
            "managing_unit": f"{practice} - {region} ({pcode}0{rng.randint(1, 5)})", "unit_code": f"{pcode}0{rng.randint(1, 5)}"})
        p["commitment"] = c
        self.documents(p)
        self.components(p)
        self.activities(p)
        self.procurement(p)

    # ------------------------------------------------------------ documents and components
    def documents(self, p):
        rng, pid, ap = self.rng, p["project_id"], p["approval"]
        docs = [("pad", "Project Appraisal Document", "Project Appraisal Document", ap + datetime.timedelta(days=rng.randint(0, 20)))]
        for kind, dtype, title, lo, hi, chance in (
                ("additional_financing", "Project Paper", "Additional Financing", 360, 900, 0.35),
                ("restructuring", "Project Paper", "Restructuring Paper", 420, 1200, 0.4)):
            d = ap + datetime.timedelta(days=rng.randint(lo, hi))
            if rng.random() < chance and d <= ANCHOR:
                docs.append((kind, dtype, title, d))
        p["docs"] = {}
        for n, (kind, dtype, title, d) in enumerate(docs, 1):
            did = f"{pid}-D{n}"
            p["docs"][kind] = {"id": did, "date": d, "page": rng.randint(6, 12), "ordinal": 0, "offset": 0}
            self.t["documents"].append({
                "doc_id": did, "project_ids": pid, "doc_type": dtype, "doc_kind": kind, "title": f"{p['name']}: {title}",
                "disclosure_date": iso(d), "lang": "English", "fetched_at": iso(ANCHOR) + "T00:00:00Z",
                "pdf_url": f"https://documents.example.org/demo/{did}.pdf", "pdf_bytes": rng.randint(400_000, 4_000_000),
                "pdf_sha256": sha(did), "owner_unit": "Digital Development"})

    def components(self, p):
        rng = self.rng
        names = rng.sample(COMPONENTS, rng.randint(3, 5)) + ["Project management and monitoring"]
        share = [rng.random() + 0.3 for _ in names]
        pad = p["docs"]["pad"]
        p["components"] = []
        from wbg_portfolio.keys import name_key
        for n, (name, w) in enumerate(zip(names, share), 1):
            cost = round(p["commitment"] * w / sum(share), 1)
            p["components"].append((str(n), name))
            self.t["components"].append({
                "component_id": f"{pad['id']}:{n}", "project_ids": p["project_id"], "doc_id": pad["id"], "doc_kind": "pad",
                "disclosure_date": iso(pad["date"]), "source": "datasheet", "level": "component", "number": n, "name": name,
                "name_key": name_key(name), "cost_usd_m": cost, "page": 4})

    # ------------------------------------------------------------ activities and sentences
    def activities(self, p):
        rng = self.rng
        lean = rng.choice(["climate", "cloud_data", "both"])
        pool = [m for m in self.measures if lean == "both" or lean in (m["family"] or "").split("|")]
        n_meas = min(rng.randint(2, 12), len(pool))
        for m in rng.sample(pool, n_meas):
            ms = m["label_set"]
            n_act = wchoice(rng, [(30, 1), (25, 2), (18, 3), (12, 4), (8, 5), (7, 6)])
            for a in range(n_act):
                self.activity(p, ms, m["name"])
            if rng.random() < 0.3:
                self.not_digital(p, ms)
            if rng.random() < 0.5:
                self.background(p, ms)

    def types_for(self, ms):
        rng = self.rng
        choices = self.types[ms]
        k = wchoice(rng, [(12, 0), (60, 1), (22, 2)]) if choices else 0
        picks = [t for t, _ in rng.sample(choices, min(k, len(choices)))]
        if ms in self.oth and rng.random() < 0.18:
            picks = [self.oth[ms][0]] + picks[:1]
        return picks

    def assets_for(self, ms, policy):
        rng = self.rng
        k = wchoice(rng, [(70, 0), (28, 1), (2, 2)] if policy else [(6, 0), (84, 1), (10, 2)])
        ids = [a["label_id"] for a in self.assets]
        first = rng.choice(PREFER.get(ms, ids)) if rng.random() < 0.8 else rng.choice(ids)
        out = [first] if k >= 1 else []
        if k == 2:
            out.append(rng.choice([a for a in ids if a != first]))
        return [(a, rng.choice(self.subtypes[a]) if rng.random() > 0.08 else None) for a in out]

    def sentence_text(self, ms, first, policy, assets, types, comp_no, agency, where):
        rng = self.rng
        topic = TOPIC[ms]
        tname = {t: n for t, n in self.types[ms] + list(self.oth.values())}
        incl = (", including " + lower_first(tname[types[0]].replace("OTH. ", ""))) if types else ""
        asset = ASSET_PHRASE.get(assets[0][1]) or ASSET_DEFAULT[assets[0][0]] if assets else None
        if assets and len(assets) > 1:
            asset += " and " + (ASSET_PHRASE.get(assets[1][1]) or ASSET_DEFAULT[assets[1][0]])
        about = f", applying to {asset}" if (policy and asset) else ""
        if not first:
            return rng.choice(FOLLOW).format(topic=topic, Agency=agency[0].upper() + agency[1:], n=comp_no,
                                             pct=rng.choice([5, 8, 12, 15, 20]))
        tpl = rng.choice(POLICY if policy else INVEST)
        return tpl.format(topic=topic, incl=incl, asset=asset or "the sector", about=about, n=comp_no, agency=agency,
                          Agency=agency[0].upper() + agency[1:], where=where)

    def paragraph(self, p, kind, comp, sentences, extra):
        """Write one paragraph of a document with its sentences (search units) and return their ids."""
        d = p["docs"][kind]
        d["ordinal"] += 1
        pid_ = f"{d['id']}:p{d['ordinal']:05d}"
        text = " ".join(s for s, _ in sentences)
        d["page"] += self.rng.choice([0, 1, 1, 2])
        start = d["offset"]
        d["offset"] += len(text) + 2
        self.t["paragraphs"].append({
            "paragraph_id": pid_, "doc_id": d["id"], "project_ids": p["project_id"], "ordinal": d["ordinal"],
            "section_path": f"II.{comp[0]}", "section_title": f"Component {comp[0]}: {comp[1]}", "block": "narrative",
            "for_model": "true", "list_item": "false", "char_start": start, "char_end": start + len(text),
            "n_tokens": len(text.split()), "text_sha256": sha(text), "page_from": d["page"], "page_to": d["page"],
            "parser": "demo", "component_number": comp[0], "component_name": comp[1], "text": text})
        self.t["paragraph_projects"].append({"paragraph_id": pid_, "project_id": p["project_id"], "doc_id": d["id"]})
        ids, at = [], start
        for n, (s, _) in enumerate(sentences, 1):
            uid = f"{pid_}:s{n:03d}"
            ids.append(uid)
            self.t["search_units"].append({
                "unit_id": uid, "kind": "sentence", "paragraph_id": pid_, "doc_id": d["id"], "project_ids": p["project_id"],
                "block": "narrative", "section_path": f"II.{comp[0]}", "component_number": comp[0], "component_key": comp[1].lower(),
                "char_start": at, "char_end": at + len(s), "n_tokens": len(s.split()), "text_sha256": sha(s),
                "template_projects": 0, "template_share": 0, "text": s})
            at += len(s) + 1
        return ids

    def finding(self, p, ms, uid, para_comp, text, **kw):
        """A measure_findings row; kw sets the decision (score, committed, digital, instrument, assets, types)."""
        rng, s = self.rng, kw["score"]
        committed, band = kw.get("committed", True), kw.get("band", False)
        assets, types = kw.get("assets", []), kw.get("types", [])
        level = kw.get("level", "none")
        p3, p2 = round(0.75 * s, 3), round(0.25 * s, 3)
        row = {
            "label_set": ms, "label_id": ms, "unit_id": uid, "kind": "sentence", "paragraph_id": uid.rsplit(":", 1)[0],
            "doc_id": kw["doc"], "project_ids": p["project_id"], "component_number": para_comp, "copies": "",
            "route": rng.choice(["embedding", "pattern", "both"]), "round": 0, "score": s, "patterns": "",
            "firmness": rng.choice(["activity", "outcome", "none"]), "asset_unit": assets[0][0] if assets else "",
            "asset_p_unit": round(rng.uniform(0.5, 0.97), 3) if assets else "",
            "assets_yes": "|".join(a for a, _ in assets), "asset_link": assets[0][0] if assets else "",
            "asset_link_level": level if assets else "none", "asset_link_p": round(rng.uniform(0.5, 0.97), 3) if assets else "",
            "asset_links": "|".join(a for a, _ in assets), "template_projects": 0, "template_share": 0, "sent": "true",
            "committed": str(committed and kw.get("digital", True)).lower(), "measure_rule": str(committed).lower(),
            "confidence": round(max(s, 1 - s), 3), "p0": round((1 - s) * 0.8, 3),
            "p1": round((1 - s) * 0.2, 3), "p2": p2, "p3": p3, "p_committed_calibrated": s,
            "types_yes": "|".join(types), "best_type": types[0] if types else "", "best_p": round(rng.uniform(0.55, 0.95), 3) if types else "",
            "score_first": s, "score_context": kw.get("context", ""), "second_grade": kw.get("grade", ""),
            "decided_by": ("second_look" if band else "score") if committed else "",
            "is_digital": str(kw.get("digital", True)).lower(), "digital_p": kw.get("digital_p", round(rng.uniform(0.7, 0.99), 3)),
            "instrument": kw.get("instrument", ""), "model_served": "demo", "text": text}
        self.t["measure_findings"].append(row)

    def unit_assets(self, uid, para, p, assets, level):
        if not assets:
            self.t["unit_assets"].append({"unit_id": uid, "asset_rank": 0, "paragraph_id": para, "project_ids": p["project_id"],
                                          "asset_id": "", "subtype_id": "", "probability": "", "asset_level": "none"})
        for rank, (a, st) in enumerate(assets, 1):
            self.t["unit_assets"].append({
                "unit_id": uid, "asset_rank": rank, "paragraph_id": para, "project_ids": p["project_id"], "asset_id": a,
                "subtype_id": st or "", "probability": round(self.rng.uniform(0.5, 0.97), 3) if level == "unit" else "",
                "asset_level": level, "own_best_asset_id": a, "own_best_probability": round(self.rng.uniform(0.3, 0.9), 3),
                "second_look_asset_id": a if level == "second_look" else ""})

    def activity(self, p, ms, mname):
        rng = self.rng
        policy = rng.random() < (0.55 if ms in POLICY_LEAN else 0.12)
        assets, types = self.assets_for(ms, policy), self.types_for(ms)
        comp = rng.choice(p["components"][:-1])
        agency, where = rng.choice(AGENCIES), rng.choice(WHERE)
        n_sent = wchoice(rng, [(35, 1), (30, 2), (22, 3), (13, 4)])
        home = wchoice(rng, [(85, "pad"), (8, "additional_financing"), (7, "restructuring")])
        if home not in p["docs"]:
            home = "pad"
        by_doc = defaultdict(list)
        for k in range(n_sent):
            kind = home if rng.random() > 0.25 else rng.choice(list(p["docs"]))
            by_doc[kind].append(self.sentence_text(ms, k == 0, policy, assets, types, comp[0], agency, where))
        units, per_unit = [], {}
        for kind, texts in by_doc.items():
            sentences = [(t, None) for t in texts]
            for uid, t in zip(self.paragraph(p, kind, comp, sentences, None), texts):
                units.append(uid)
                per_unit[uid] = (t, kind)
        for uid, (t, kind) in per_unit.items():
            band = rng.random() < 0.45
            score = round(rng.uniform(0.5, 0.89) if band else rng.uniform(0.9, 0.995), 3)
            level = wchoice(rng, [(75, "unit"), (25, "second_look")]) if assets else "none"
            ins = "policy" if policy else "investment"
            if rng.random() < 0.03:
                ins = "investment" if policy else "policy"
            ctx = round(rng.uniform(0.7, 0.97), 3) if band else (round(rng.uniform(0.6, 0.99), 3) if rng.random() < 0.7 else "")
            grade = rng.choice([2, 3]) if band or rng.random() < 0.7 else ""
            self.finding(p, ms, uid, comp[0], t, score=score, band=band, context=ctx, grade=grade, assets=assets,
                         types=types, level=level, instrument=ins, doc=p["docs"][kind]["id"])
            self.unit_assets(uid, uid.rsplit(":", 1)[0], p, assets, level)
        from wbg_portfolio import activities as act
        aid = act.item_id(p["project_id"], ms, units)
        label = self.label(ms, policy, assets, types, mname)
        for uid in units:
            self.t["activities"].append({"activity_id": aid, "project_id": p["project_id"], "label_set": ms,
                                         "activity_label": label, "unit_id": uid, "n_sentences": len(units)})
        self.activity_rows.append({"id": aid, "project": p["project_id"], "measure": ms, "units": units,
                                   "assets": [a for a, _ in assets], "policy": policy, "types": types, "comp": comp})

    def label(self, ms, policy, assets, types, mname):
        rng = self.rng
        what = TOPIC[ms]
        if policy:
            return rng.choice(["Prepare and adopt ", "Update standards for ", "Draft a framework for "]) + what
        phrase = ASSET_PHRASE.get(assets[0][1]) or ASSET_DEFAULT[assets[0][0]] if assets else "the sector"
        return rng.choice(["Finance ", "Install ", "Support "]) + what + " for " + phrase

    def not_digital(self, p, ms):
        rng = self.rng
        comp = rng.choice(p["components"][:-1])
        for _ in range(rng.randint(1, 2)):
            text = rng.choice(NOT_DIGITAL).format(topic=TOPIC[ms], thing=rng.choice(THINGS), where=rng.choice(WHERE), n=comp[0])
            uid = self.paragraph(p, "pad", comp, [(text, None)], None)[0]
            self.finding(p, ms, uid, comp[0], text, score=round(rng.uniform(0.9, 0.99), 3), digital=False,
                         digital_p=round(rng.uniform(0.05, 0.4), 3), instrument="", doc=p["docs"]["pad"]["id"])
            self.unit_assets(uid, uid.rsplit(":", 1)[0], p, [], "none")

    def background(self, p, ms):
        rng = self.rng
        comp = rng.choice(p["components"][:-1])
        text = rng.choice(BACKGROUND).format(topic=TOPIC[ms])
        uid = self.paragraph(p, "pad", comp, [(text, None)], None)[0]
        self.finding(p, ms, uid, comp[0], text, score=round(rng.uniform(0.15, 0.49), 3), committed=False, digital=False,
                     digital_p="", doc=p["docs"]["pad"]["id"])
        self.unit_assets(uid, uid.rsplit(":", 1)[0], p, [], "none")

    # ------------------------------------------------------------ procurement
    def procurement(self, p):
        rng, pid = self.rng, p["project_id"]
        mine = [a for a in self.activity_rows if a["project"] == pid]
        target = wchoice(rng, [(6, 0), (35, rng.randint(3, 12)), (40, rng.randint(13, 30)), (19, rng.randint(31, 60))])
        wanted = []                                           # (category, text, asset, measure, activity or None)
        for a in mine:
            if not a["policy"] and rng.random() < 0.3:
                cat, buys = BUYS[a["measure"]]
                for k in range(rng.randint(1, 2)):
                    where = (" - Lot %d" % (k + 1)) if k else ""
                    wanted.append((cat, f"{PREFIX[cat]} {buys}{where}", (a["assets"] or [None])[0], a))
        wanted = wanted[:min(target, 60)] if target else []
        while len(wanted) < target:
            cat, text = rng.choice(FILLERS)
            wanted.append((cat, f"{text} ({len(wanted) + 1})" if rng.random() < 0.3 else text,
                           rng.choice([a["label_id"] for a in self.assets]) if rng.random() < 0.3 else None, None))
        if not wanted:
            return
        plans = [ANCHOR - datetime.timedelta(days=rng.randint(10, 400))]
        plans.append(plans[0] - datetime.timedelta(days=rng.randint(180, 400)))
        pkgs = []
        for n, (cat, text, asset, act_) in enumerate(wanted, 1):
            pkgs.append(self.package(p, n, cat, text, asset, act_, plans))
        self.links(p, pkgs)

    def package(self, p, n, cat, text, asset, act_, plans):
        rng, pid = self.rng, p["project_id"]
        ref = f"{pid[2:]}/{cat[0].upper()}/{n:02d}"
        pkg_id = f"{pid}-{ref}"
        lo = datetime.date(2023, 3, 1)
        span = max((min(datetime.date(2029, 12, 31), p["approval"] + datetime.timedelta(days=365 * 8)) - p["approval"]).days, 120)
        planned = max(lo, p["approval"] + datetime.timedelta(days=rng.randint(60, span)))
        planned = min(planned, datetime.date(2029, 12, 31))
        day = datetime.timedelta
        if planned <= ANCHOR - day(days=365):
            state = wchoice(rng, [(45, "completed"), (12, "implementation"), (8, "signed"), (7, "canceled"), (10, "dropped"),
                                  (10, "overdue"), (8, "tendered")])
        elif planned <= ANCHOR:
            state = wchoice(rng, [(15, "completed"), (25, "implementation"), (20, "signed"), (4, "canceled"), (12, "overdue"),
                                  (14, "tendered"), (10, "dropped")])
        else:
            state = wchoice(rng, [(78, "planned"), (6 if planned <= ANCHOR + day(days=150) else 0, "tendered"),
                                  (14, "dropped"), (2, "canceled")]) or "planned"
        revised = ""
        if state in ("planned", "tendered", "dropped") and rng.random() < 0.3:
            revised = planned + day(days=rng.randint(30, 200))
        plan_status = {"completed": "Completed", "implementation": "Under Implementation", "signed": "Signed",
                       "canceled": "Canceled"}.get(state, "Pending")
        method = rng.choice(METHODS[cat])
        lo_a, hi_a = AMOUNT[cat]
        est = round(rng.uniform(lo_a, hi_a), -3)
        notice = signed = None
        if state in ("tendered", "signed", "implementation", "completed") and (state == "tendered" or rng.random() < 0.8):
            pub = planned - day(days=rng.randint(10, 40))
            if state == "tendered":
                pub = min(planned - day(days=10), ANCHOR - day(days=rng.randint(1, 40)))
            notice = (min(pub, ANCHOR - day(days=2)), None)
            notice = (notice[0], notice[0] + day(days=rng.randint(30, 45)))
        if state in ("signed", "implementation", "completed"):
            signed = min(planned + day(days=rng.randint(-20, 120)), ANCHOR - day(days=rng.randint(5, 60)))
            if state == "completed":
                signed = min(signed, ANCHOR - day(days=rng.randint(200, 500)))
        latest = state != "dropped"
        plan = plans[0] if latest else plans[1]
        plan_id = f"{pid}-PLAN{1 if latest else 0}"
        self.t["packages"].append({
            "package_id": pkg_id, "project_id": pid, "borrower_ref": ref, "borrower_ref_norm": ref.replace("/", "-").upper(),
            "description": text, "description_clean": text, "description_match": text.lower().replace(" ", ""),
            "description_sha256": sha(text), "description_lang": "en", "component_number": p["components"][0][0] if act_ is None else act_["comp"][0],
            "component": p["components"][0][1] if act_ is None else act_["comp"][1], "component_source": "name_match",
            "component_doc_id": p["docs"]["pad"]["id"], "glue_repairs": 0, "is_rebid": "false", "is_placeholder": "false",
            "clean_version": "demo", "category": cat, "method": method[0], "method_name": method[1], "method_source": "method_cell",
            "method_raw": method[1], "market_approach": rng.choice(MARKETS[:3]) if method[0] != "DIR" else "Direct",
            "status": plan_status, "status_raw": plan_status, "status_source": "plan", "status_as_of": iso(plan),
            "planned_date": iso(planned), "revised_date": iso(revised) if revised else "", "estimated_amount": est,
            "currency": "USD", "amount_source": "plan", "actual_amount": round(est * rng.uniform(0.7, 1.1), -3) if signed else "",
            "plan_version": plan_id, "plan_disclosure_date": iso(plan), "in_latest_plan": str(latest).lower(),
            "fetched_at": iso(ANCHOR) + "T00:00:00Z"})
        country = p["country"][0]
        if notice:
            self.t["notices"].append({
                "notice_id": f"{pkg_id}-N1", "project_id": pid, "notice_type": "Invitation for Bids" if cat != "consultant_services" else "Request for Expressions of Interest",
                "notice_status": "Published", "publication_date": iso(notice[0]), "deadline_date": iso(notice[1]), "borrower_ref": ref,
                "borrower_ref_norm": ref.replace("/", "-").upper(), "description": text, "description_clean": text,
                "description_lang": "en", "is_placeholder": "false", "category": cat, "method": method[0], "method_name": method[1],
                "country_name": country, "package_id": pkg_id, "package_link": "reference", "clean_version": "demo"})
        if signed:
            supplier = rng.choice(SUPPLIERS)
            total = round(est * rng.uniform(0.7, 1.15), -3)
            self.t["awards"].append({
                "contract_id": f"{pkg_id}-C1", "project_id": pid, "borrower_ref": ref, "borrower_ref_norm": ref.replace("/", "-").upper(),
                "description": text, "description_clean": text, "description_lang": "en", "is_placeholder": "false",
                "clean_version": "demo", "signed_date": iso(signed), "no_objection_date": iso(signed - day(days=rng.randint(7, 30))),
                "total_amount": total, "currency": "USD", "category": cat, "method": method[0], "method_name": method[1],
                "review_type": rng.choice(["prior", "post"]), "supplier_name": supplier[0], "supplier_country": supplier[1],
                "supplier_amount": total, "region": p["region"], "sector": "Information and communications technology",
                "package_id": pkg_id, "package_link": "reference"})
        # which asset the model finds the package buys, and the ones it was asked about and said no to
        ids = [a["label_id"] for a in self.assets]
        order = ([asset] if asset else []) + [a for a in rng.sample(ids, len(ids)) if a != asset]
        for rank, a in enumerate(order[:3], 1):
            yes = asset is not None and rank == 1
            prob = round(rng.uniform(0.75, 0.97) if yes else rng.uniform(0.02, 0.45), 3)
            self.t["package_assets"].append({"package_id": pkg_id, "project_id": pid, "label_id": a,
                                             "cosine": round(rng.uniform(0.4, 0.85), 3), "cosine_rank": rank,
                                             "probability": prob, "p_calibrated": prob, "model_served": "demo"})
        return {"id": pkg_id, "asset": asset, "activity": act_, "category": cat.replace("_", " ").capitalize()}

    def links(self, p, pkgs):
        """Strong links to the activity a package was made from; asset links to others sharing its asset."""
        rng, pid = self.rng, p["project_id"]
        mine = [a for a in self.activity_rows if a["project"] == pid]
        seen = set()

        def link(pkg, a, strength):
            for uid in a["units"]:
                key = (a["measure"], uid, pkg["id"])
                if key in seen:
                    continue
                seen.add(key)
                shared = pkg["asset"] if pkg["asset"] in a["assets"] else ""
                self.t["commitment_package_links"].append({
                    "project_id": pid, "label_set": a["measure"], "unit_id": uid, "package_id": pkg["id"], "strength": strength,
                    "shared_asset_ids": shared, "strong_p": round(rng.uniform(0.72, 0.97), 3) if strength == "strong" else "",
                    "package_category": pkg["category"]})
        for pkg in pkgs:
            if pkg["activity"]:
                link(pkg, pkg["activity"], "strong")
            if pkg["asset"]:
                same = [a for a in mine if pkg["asset"] in a["assets"] and a is not pkg["activity"]]
                for a in rng.sample(same, min(len(same), rng.randint(0, 2))):
                    link(pkg, a, "asset")

    # ------------------------------------------------------------ the rest of the published tables
    def finish_tables(self):
        rng = self.rng
        t = self.t
        t["labels"] = load_pg.label_rows()
        t["measure_types"] = self.type_rows
        t["asset_subtypes"] = load_pg.subtype_rows()
        t["flags"] = load_pg.flag_rows()
        for m in self.measures:
            ms = m["label_set"]
            t["measure_quality"].append({"label_set": ms, "version": m["version"], "question_sha256": sha("q" + ms),
                                         "sheet": "evaluation", "graded_units": rng.randint(60, 200),
                                         "precision": round(rng.uniform(0.72, 0.95), 2), "recall": round(rng.uniform(0.5, 0.85), 2),
                                         "evaluated_at": iso(ANCHOR) + "T00:00:00Z", "report_path": ""})
        t["data_release"] = [{"release_id": "demo", "commit": "demo", "code_dirty": "false", "loaded_at": iso(ANCHOR) + "T00:00:00Z",
                              "appraisal_fetched_date": iso(ANCHOR - datetime.timedelta(days=30)),
                              "procurement_fetched_date": iso(ANCHOR - datetime.timedelta(days=10)),
                              "projects_fetched_date": iso(ANCHOR - datetime.timedelta(days=30))}]
        return self


def columns():
    from wbg_portfolio import clean_procurement as cp
    from wbg_portfolio import projects
    docs = ["doc_id", "project_ids", "doc_type", "doc_kind", "title", "disclosure_date", "lang", "fetched_at", "pdf_url",
            "pdf_bytes", "pdf_sha256", "owner_unit"]
    paras = ["paragraph_id", "doc_id", "project_ids", "ordinal", "section_path", "section_title", "block", "char_start",
             "char_end", "n_tokens", "text_sha256", "page_from", "page_to", "parser", "for_model", "list_item", "table_id",
             "raw_start", "raw_end", "component_number", "component_name", "subcomponent_number", "subcomponent_name",
             "component_mentions", "text"]
    comps = ["component_id", "project_ids", "doc_id", "doc_kind", "disclosure_date", "source", "level", "number", "name",
             "name_key", "cost_usd_m", "cost_before_usd_m", "action", "page"]
    return {"projects": projects.COLS, "documents": docs, "paragraphs": paras, "components": comps,
            "packages": cp.PACKAGE_COLS, "notices": cp.NOTICE_COLS, "awards": cp.AWARD_COLS,
            "paragraph_projects": ["paragraph_id", "project_id", "doc_id"], "search_units": load_pg.SEARCH_UNIT_COLS,
            "search_paragraphs": load_pg.ROLLUP_COLS, "data_release": ["release_id", "commit", "code_dirty", "loaded_at",
                                                                        "appraisal_fetched_date", "procurement_fetched_date",
                                                                        "projects_fetched_date"],
            **load_pg.DECISION_TABLES}


def publish(conn, demo, log=print):
    """Write the tables and build the views, in one transaction, into db.SCHEMA."""
    schema = db.SCHEMA
    if schema in ("wbg", "wbg_store") or schema.endswith("_store"):
        raise SystemExit(f"refusing to rebuild schema {schema}: the demo writes only its own schema")
    cols = columns()
    db.ensure(conn)
    with load_pg.transaction(conn):
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        load_pg.prepare(conn)
        for table, names in cols.items():
            n, bad = load_pg.load_rows(conn, table, names, demo.t.get(table, []), "demo")
            if bad:
                raise SystemExit(f"{table}: values that did not parse: {bad}")
            log(f"  {table:<26} {n:>7} rows")
        load_pg.finish(conn, log=log)


def check(conn, log=print):
    """Every reader function against the demo schema: rows come back, in the columns promised."""
    import pandas as pd
    from wbg_portfolio import app

    def q(sql, params=None):
        cur = conn.execute(sql, params)
        return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
    out = {"commitments": app.commitments(q), "projects": app.projects(q), "measures": app.measures(q),
           "families": app.families(q), "activities": app.activities(q), "asset_types": app.asset_types(q),
           "type_definitions": app.type_definitions(q), "packages_detail": app.packages_detail(q),
           "not_digital": app.not_digital(q), "coming_up": app.coming_up(q)}
    acts = out["activities"]
    out["activity_sentences"] = app.activity_sentences(q, acts["activity_id"].head(5))
    pid = out["packages_detail"]["package_id"].iloc[0]
    out["package.notices"] = app.package(q, pid)["notices"]
    for level in app.DOWNLOAD_LEVELS:
        out[f"download:{level}"] = app.download(q, level, family="climate")
    out["download_notes"] = app.download_notes(q, "activity", "climate", {"instrument": "policy"})
    for name, df in out.items():
        log(f"  {name:<26} {len(df):>6} rows x {df.shape[1]:>2} columns")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--schema", default="wbg_demo", help="the schema to (re)build; its store is <schema>_store")
    ap.add_argument("--projects", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-check", action="store_true", help="skip the reader check")
    args = ap.parse_args()
    db.SCHEMA, db.STORE = args.schema, args.schema + "_store"
    demo = Demo(args.seed, args.projects).build().finish_tables()
    conn = db.connect()
    print(f"seeding schema {db.SCHEMA} (store {db.STORE}) in {db.DSN}")
    publish(conn, demo)
    conn.commit()
    if not args.no_check:
        print("reader check:")
        check(conn)
    conn.close()


if __name__ == "__main__":
    main()
