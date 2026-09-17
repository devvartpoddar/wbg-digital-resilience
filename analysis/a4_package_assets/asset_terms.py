#!/usr/bin/env python3
"""Asset vocabulary for matching procurement descriptions, in four languages.

THIS FILE IS A STAND-IN AND IT IS A GAP. The repository's data model and
AGENTS.md both put hand-maintained term lists under `inputs/terms/`, and that
directory does not exist: there is no `inputs/terms/assets.*` on disk and no
stage reads one. `inputs/` is hand-maintained and scripts never write to it, so
the list cannot be created here. It lives in the probe instead, and the PR says
so. When the list is authored properly it moves to `inputs/terms/` and this
module is deleted.

TWO LEVELS, AND WHY. methodology Stage 8 matches package text against asset
terms before any classifier, because package titles run five to fifteen words
and short text is where lexical matching is strongest and dense embeddings
weakest. The probe then asks a second question - how finely can a package
description actually be resolved? - so a term carries a level:

  fine    names one of the eight asset classes in inputs/taxonomy/assets.csv
  coarse  names a GROUP of classes (telecom, data_and_hosting, platform) but
          not which one inside it

A package that matches only a coarse term resolves at group level and no finer.
That coarse-versus-fine split is the headline number of A4.

WHY THE COARSE LIST IS DELIBERATELY NARROW. "digital", "system" and
"electronic" were considered and left out. On a 70-project digital-development
cohort almost every package description contains one of them, so they would
push coarse resolution towards 100% and make the split uninformative. The coarse
terms kept here identify a digital ASSET at group level - telecom, data and
hosting, platform - without naming which class it is. IF THE INTENT WAS a wider
coarse net, the coarse share rises and the fine share falls by the same amount.

Terms are stored in normal form with spaces. The matcher derives what it needs.
"""
from collections import OrderedDict

# The coarse groups, and which fine classes sit inside each. Nothing here
# redefines an asset class: the classes are exactly the eight in
# inputs/taxonomy/assets.csv, and the groups are a view over them.
GROUPS = OrderedDict([
    ("telecom", ("fiber", "mobile_towers", "submarine_cable")),
    ("data_and_hosting", ("data_hosting",)),
    ("platform", ("dpi", "cybersecurity", "drm_ews", "access_connectivity")),
])

# ---------------------------------------------------------------- fine terms
# Drawn from the `definition` column of inputs/taxonomy/assets.csv (read only),
# then translated. A term must be specific enough to name the class on its own.
FINE = {
    "fiber": {
        "en": ["fiber", "fibre", "fiber optic", "fibre optique", "optical fiber",
               "optical fibre", "fibre optic", "backbone", "backhaul", "ftth",
               "fttx", "duct", "ducts", "conduit", "opgw"],
        "fr": ["fibre", "fibre optique", "dorsale", "fourreau", "genie civil",
               "reseaux en fibre"],
        "es": ["fibra", "fibra optica", "dorsal", "conducto", "canalizacion"],
        "pt": ["fibra", "fibra optica", "dorsal", "conduto", "canalizacao"],
    },
    "mobile_towers": {
        "en": ["tower", "towers", "mast", "base station", "base transceiver",
               "bts", "radio access", "mobile network", "cell site", "antenna",
               "antennas", "mobile broadband site"],
        "fr": ["pylone", "pylones", "antenne", "antennes", "station de base",
               "reseau mobile", "site radio"],
        "es": ["torre", "torres", "antena", "antenas", "estacion base",
               "red movil"],
        "pt": ["torre", "torres", "antena", "antenas", "estacao base",
               "rede movel"],
    },
    "submarine_cable": {
        "en": ["submarine cable", "submarine fibre", "undersea cable",
               "subsea cable", "landing station", "branching unit"],
        "fr": ["cable sous marin", "station d atterrissement", "atterrissement"],
        "es": ["cable submarino", "estacion de aterrizaje"],
        "pt": ["cabo submarino", "estacao de aterragem"],
    },
    "data_hosting": {
        "en": ["data center", "data centre", "datacenter", "datacentre",
               "server", "servers", "server room", "hosting", "cloud",
               "colocation", "co location", "disaster recovery site",
               "storage area network"],
        "fr": ["centre de donnees", "centre de donnee", "serveur", "serveurs",
               "hebergement", "informatique en nuage", "salle serveur"],
        "es": ["centro de datos", "servidor", "servidores", "alojamiento",
               "nube", "sala de servidores"],
        "pt": ["centro de dados", "servidor", "servidores", "hospedagem",
               "nuvem", "sala de servidores"],
    },
    "cybersecurity": {
        "en": ["cybersecurity", "cyber security", "cyber", "cert", "soc",
               "security operations center", "firewall", "penetration test",
               "incident response", "data protection", "privacy",
               "vulnerability assessment"],
        "fr": ["cybersecurite", "securite informatique", "protection des donnees",
               "audit de securite", "centre operationnel de securite"],
        "es": ["ciberseguridad", "seguridad informatica", "proteccion de datos",
               "auditoria de seguridad"],
        "pt": ["ciberseguranca", "seguranca informatica", "protecao de dados",
               "auditoria de seguranca"],
    },
    "dpi": {
        "en": ["digital identity", "e government", "egovernment", "e gov",
               "interoperability", "digital payment", "payment platform",
               "payment system", "registry", "civil registration",
               "digital signature", "one stop shop", "government platform",
               "g2p", "management information system", "mis", "digital id",
               "national id", "identity management", "e service", "e services",
               "public service platform", "data exchange"],
        "fr": ["identite numerique", "gouvernement electronique",
               "paiement numerique", "systeme de paiement", "registre",
               "registre d etat civil", "interoperabilite",
               "signature electronique", "guichet unique",
               "plateforme gouvernementale", "systeme d information"],
        "es": ["identidad digital", "gobierno electronico", "pago digital",
               "sistema de pago", "registro", "registro civil",
               "interoperabilidad", "firma electronica", "ventanilla unica",
               "plataforma gubernamental"],
        "pt": ["identidade digital", "governo eletronico", "pagamento digital",
               "sistema de pagamento", "registro", "registro civil",
               "interoperabilidade", "assinatura eletronica",
               "balcao unico", "plataforma governamental"],
    },
    "drm_ews": {
        "en": ["early warning", "weather station", "hydromet", "hydrological",
               "meteorological", "hazard monitoring", "alert system",
               "alerting system", "gis", "geospatial", "remote sensing",
               "seismic", "river gauge", "disaster risk", "weather radar",
               "flood forecasting", "emergency operations center",
               "emergency communication", "hazard map", "climate information"],
        "fr": ["alerte precoce", "systeme d alerte", "station meteorologique",
               "hydrometeorologique", "risque de catastrophe",
               "radar meteorologique", "cartographie des risques",
               "information climatique"],
        "es": ["alerta temprana", "sistema de alerta", "estacion meteorologica",
               "hidrometeorologico", "riesgo de desastre",
               "radar meteorologico", "informacion climatica"],
        "pt": ["alerta precoce", "sistema de alerta", "estacao meteorologica",
               "hidrometeorologico", "risco de desastre",
               "radar meteorologico", "informacao climatica"],
    },
    "access_connectivity": {
        "en": ["universal service", "universal access", "universal service fund",
               "usf", "digital literacy", "digital skills", "digital inclusion",
               "public access", "telecentre", "telecenter", "community network",
               "wifi hotspot", "hotspot", "device", "devices", "subsidy",
               "last mile", "affordability", "school connectivity",
               "digital divide"],
        "fr": ["service universel", "acces universel", "litteratie numerique",
               "competences numeriques", "inclusion numerique",
               "point d acces public", "subvention", "fracture numerique"],
        "es": ["servicio universal", "acceso universal",
               "alfabetizacion digital", "competencias digitales",
               "inclusion digital", "punto de acceso publico", "subvencion",
               "brecha digital"],
        "pt": ["servico universal", "acesso universal",
               "literacia digital", "competencias digitais",
               "inclusao digital", "ponto de acesso publico", "subvencao",
               "fratura digital"],
    },
}

# -------------------------------------------------------------- coarse terms
# Group-level only. See the module docstring for what was left out and why.
COARSE = {
    "telecom": {
        "en": ["telecom", "telecommunications", "ict", "information and "
               "communication technology", "network equipment", "broadband",
               "bandwidth", "transmission network", "core network",
               "access network"],
        "fr": ["telecom", "telecommunications", "télécom",
               "reseaux de telecommunications", "large bande", "bande passante"],
        "es": ["telecomunicaciones", "banda ancha", "ancho de banda",
               "equipos de red"],
        "pt": ["telecomunicacoes", "banda larga", "largura de banda",
               "equipamentos de rede"],
    },
    "data_and_hosting": {
        "en": ["data", "server", "storage", "cloud", "hosting", "database",
               "information system infrastructure"],
        "fr": ["informatique", "serveur", "stockage", "donnees", "nuage"],
        "es": ["informatica", "servidor", "almacenamiento", "datos", "nube"],
        "pt": ["informatica", "servidor", "armazenamento", "dados", "nuvem"],
    },
    "platform": {
        "en": ["platform", "software", "application", "portal", "website",
               "information system", "digital service", "online service",
               "computer equipment", "it equipment"],
        "fr": ["plateforme", "logiciel", "application", "portail",
               "systeme d information", "service numerique",
               "equipement informatique"],
        "es": ["plataforma", "software", "aplicacion", "portal",
               "sistema de informacion", "servicio digital",
               "equipo informatico"],
        "pt": ["plataforma", "software", "aplicacao", "portal",
               "sistema de informacao", "servico digital",
               "equipamento informatico"],
    },
}

LANGS = ("en", "fr", "es", "pt")


def terms_for(lang):
    """[(term, asset_id, level)] for one language. asset_id is None at coarse
    level, where the group is the answer instead."""
    out = []
    for aid, by_lang in FINE.items():
        for t in by_lang.get(lang, []):
            out.append((t, aid, "fine"))
    for group, by_lang in COARSE.items():
        for t in by_lang.get(lang, []):
            out.append((t, group, "coarse"))
    return out


def all_terms():
    for lang in LANGS:
        for t, aid, level in terms_for(lang):
            yield lang, t, aid, level


def group_of(asset_id):
    for group, members in GROUPS.items():
        if asset_id in members:
            return group
    return None
