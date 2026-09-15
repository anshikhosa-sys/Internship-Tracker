"""
Facts about employers: which industry, how large, who owns whom, and which
cap applications per cycle. Describes companies, not what any user wants;
users act on these facts through their own preferences (excluded industries,
company sizes).

Names match the posting's company field whole-word, case-insensitively.
Ambiguous short names that would misfire on unrelated companies are left out
on purpose: an unclassified employer is neutral, a misclassified one is wrong.
"""

COMPANY_INDUSTRIES = {
    "ai": [
        "openai", "anthropic", "google deepmind", "deepmind", "scale ai", "mistral", "cohere",
        "perplexity", "cursor", "anysphere", "sierra", "harvey", "glean", "deepgram", "modal",
        "together ai", "fireworks ai", "lambda labs", "runway", "elevenlabs", "suno", "midjourney",
        "character.ai", "ellipsis labs", "dedalus labs", "hugging face", "xai", "adept", "inflection",
    ],
    "robotics": ["figure", "boston dynamics", "neuralink", "skydio", "agility robotics", "1x"],
    "software": [
        "google", "alphabet", "meta", "facebook", "apple", "amazon", "aws", "microsoft", "netflix",
        "uber", "lyft", "airbnb", "salesforce", "adobe", "oracle", "ibm", "cisco", "vmware",
        "linkedin", "github", "snap", "pinterest", "spotify", "shopify", "dropbox", "twilio",
        "zoom", "atlassian", "servicenow", "workday", "snowflake", "mongodb", "elastic",
        "hashicorp", "cloudflare", "akamai", "reddit", "discord", "doordash", "instacart",
        "bytedance", "tiktok", "palantir", "databricks", "applied intuition", "datadog", "figma",
        "notion", "replit", "rippling", "samsara", "the trade desk", "liveramp", "appian", "axon",
        "poshmark", "veeva", "asana", "airtable", "vercel", "netlify", "supabase", "planetscale",
        "gitlab", "jetbrains", "docker", "grafana labs", "confluent", "temporal", "dbt labs",
        "sigma computing", "retool", "checkr", "gusto", "deel", "vanta", "wiz", "snyk",
        "1password", "okta", "crowdstrike", "palo alto networks", "sentinelone", "zscaler",
        "fastly", "digitalocean", "epic systems", "youtube", "waymo", "nuro",
    ],
    "fintech": [
        "stripe", "block", "square", "paypal", "robinhood", "ramp", "brex", "mercury", "plaid",
        "fiserv", "affirm", "chime", "sofi", "marqeta", "wealthfront",
    ],
    "crypto": ["coinbase", "kraken", "consensys", "circle", "chainalysis"],
    "gaming": ["roblox", "unity", "epic games", "riot games", "activision", "ea sports",
               "electronic arts", "valve", "blizzard"],
    "semiconductors": [
        "nvidia", "tsmc", "intel", "amd", "qualcomm", "broadcom", "micron", "texas instruments",
        "arm", "western digital", "seagate", "skyworks", "asm international", "tetramem", "etched",
        "cerebras", "groq", "sambanova", "analog devices", "marvell", "applied materials",
        "lam research", "kla",
    ],
    "defense": [
        "anduril", "shield ai", "saronic", "lockheed", "lockheed martin", "northrop",
        "northrop grumman", "general dynamics", "raytheon", "rtx", "bae systems", "caci", "leidos",
        "booz allen", "nightwing", "saic", "mitre", "aerojet", "kudu dynamics", "l3harris",
    ],
    "aerospace": ["spacex", "blue origin", "stoke space", "specter aerospace", "general astronautics",
                  "boeing", "ge aerospace", "rocket lab", "relativity space"],
    "automotive": ["tesla", "rivian", "lucid", "ford", "general motors", "gm", "toyota", "stellantis"],
    "quant_trading": [
        "akuna", "aquatic", "arrowstreet", "belvedere trading", "chicago trading", "citadel", "cubist",
        "cutler group", "de shaw", "d. e. shaw", "drw", "dv group", "dv trading", "five rings",
        "flow traders", "g-research", "garda capital", "gardacp", "group one trading", "headlands",
        "hudson river trading", "hyannis port research", "imc trading", "jane street", "jump trading",
        "marshall wace", "millennium", "old mission", "optiver", "pdt partners", "peak6", "point72",
        "quantbot", "quantlab", "radix trading", "stevens capital", "susquehanna", "tower research",
        "two sigma", "vatic labs", "verition", "virtu", "voloridge", "walleye", "wolverine trading",
        "xtx markets",
    ],
    "finance": [
        "jpmorgan", "jp morgan", "goldman sachs", "morgan stanley", "bank of america", "wells fargo",
        "citi", "citigroup", "truist", "regions bank", "keybank", "fifth third", "deutsche bank",
        "bnp paribas", "blackrock", "blackstone", "vanguard", "pimco", "american express",
        "capital one", "fannie mae", "freddie mac", "dtcc", "baird", "lpl financial", "stepstone",
        "psp investments", "intercontinental exchange", "affinius", "dimensional fund",
        "discover financial", "synchrony", "ally financial", "artisan partners",
        "mackenzie investments", "bny", "bny mellon", "castleton commodities",
    ],
    "insurance": [
        "auto-owners", "genworth", "cno financial", "arthur j. gallagher", "allstate", "progressive",
        "geico", "state farm", "nationwide", "liberty mutual", "travelers", "aflac", "metlife",
        "prudential", "marsh", "aon", "willis towers", "manulife",
    ],
    "pharma": ["abbvie", "pfizer", "merck", "johnson & johnson", "lilly", "eli lilly", "bristol",
               "bristol myers squibb", "amgen", "genentech", "novartis", "astrazeneca", "gsk", "sanofi"],
    "healthcare": ["medtronic", "medline", "medpace", "midmark", "philips", "humana", "guidewell",
                   "cigna", "aetna", "unitedhealth", "elevance", "cvs health", "hca", "kaiser"],
    "energy": ["devon energy", "diamondback", "continental resources", "exxon", "exxonmobil", "chevron",
               "shell", "bp", "conocophillips", "halliburton", "schlumberger", "ameren", "wec energy",
               "duke energy", "dominion", "nextera", "al warren oil", "the nuclear company",
               "solar turbines"],
    "manufacturing": [
        "ecolab", "dow", "dupont", "caterpillar", "springs window fashions", "springs windowfashions",
        "pentair", "vertiv", "hitachi", "motorola", "chamberlain group", "dee zee", "tmeic",
        "teledyne", "honeywell", "3m", "emerson", "rockwell", "parker hannifin", "illinois tool",
        "air products", "hadrian", "ge appliances", "ge vernova", "unison", "grainger",
        "w.w. grainger", "uline", "john deere", "cummins",
    ],
    "retail": ["walmart", "target", "costco", "kroger", "copart", "home depot", "lowe's", "best buy"],
    "consumer_goods": ["pepsico", "coca-cola", "nestle", "general mills", "kellogg", "conagra",
                       "tyson", "hormel", "procter & gamble", "unilever", "colgate", "kimberly-clark",
                       "cargill", "winland foods"],
    "logistics": ["fedex", "ups", "c.h. robinson", "ryder", "zipline", "flexport"],
    "consulting": [
        "pricewaterhousecoopers", "pwc", "deloitte", "kpmg", "ernst & young", "ey", "accenture",
        "cognizant", "infosys", "capgemini", "wipro", "tata consultancy", "alixpartners",
        "analysis group", "fti consulting", "kearney", "berrydunn", "wipfli", "mercer", "hntb", "wsp",
        "imeg", "fast enterprises", "mckinsey", "boston consulting group", "bain",
    ],
    "travel": ["hilton", "marriott", "delta air", "united airlines", "american airlines",
               "southwest airlines", "expedia", "booking.com"],
    "construction": ["ryan companies", "mortenson", "rrs group", "turner construction", "bechtel"],
    "government": ["allegheny county", "lawrence livermore", "los alamos", "sandia national", "oak ridge",
                   "argonne", "nasa", "jet propulsion laboratory"],
    "telecom": ["verizon", "at&t", "t-mobile", "comcast", "charter communications"],
    "education": ["coursera", "duolingo", "chegg", "khan academy"],
}

# When no company name matches, words inside the name can still name the
# industry ("Acme Insurance Group").
INDUSTRY_NAME_HINTS = {
    "insurance": ["insurance", "assurance", "insurers", "mutual", "actuarial"],
    "finance": ["bancorp", "bankshares", "savings bank", "credit union", "financial group",
                "financial holdings", "wealth management", "asset management", "advisors",
                "investments", "investment management", "capital management"],
    "pharma": ["pharmaceutical", "pharmaceuticals", "pharma", "biopharma"],
    "biotech": ["biosciences", "biotherapeutics", "therapeutics", "genomics"],
    "healthcare": ["health system", "healthcare system", "hospital", "clinic", "medical center",
                   "physicians", "health"],
    "energy": ["petroleum", "oil company", "oil & gas", "natural gas", "energy group", "utilities",
               "power company", "electric company", "energy"],
    "manufacturing": ["manufacturing", "industries", "industrial", "fabrication", "window fashions",
                      "industrial gases"],
    "consumer_goods": ["foods", "food group", "beverage", "brewing", "farms"],
    "travel": ["hotels", "resorts", "hospitality", "airlines", "cruise"],
    "retail": ["retail group", "stores", "supermarkets", "grocery"],
    "construction": ["construction", "contractors", "builders"],
    "real_estate": ["realty", "real estate", "properties", "development group"],
    "logistics": ["logistics", "freight", "trucking", "shipping"],
    "consulting": ["consulting", "staffing", "recruiting agency", "accountants", "law firm", "legal group"],
    "education": ["university", "college", "school district", "academy"],
    "government": ["county", "city of", "state of", "department of", "ministry", "municipal",
                   "national laboratory"],
    "defense": ["defense", "aerospace & defense"],
    "ai": ["ai labs"],
}

# Industries whose product is technology — "tech employers only" on the dashboard.
TECH_INDUSTRIES = ["ai", "robotics", "software", "fintech", "crypto", "gaming", "semiconductors"]

# Known large employers beyond the tech list. Size is otherwise inferred from
# how many roles a company is posting (see config/scoring.py).
LARGE_COMPANIES = [
    "google", "alphabet", "meta", "facebook", "apple", "amazon", "aws", "microsoft", "netflix", "uber",
    "salesforce", "adobe", "oracle", "ibm", "cisco", "intel", "amd", "nvidia", "qualcomm", "broadcom",
    "linkedin", "tiktok", "bytedance", "tesla", "paypal", "jpmorgan", "jp morgan", "goldman sachs",
    "morgan stanley", "bank of america", "wells fargo", "citi", "capital one", "american express",
    "boeing", "lockheed martin", "northrop grumman", "raytheon", "rtx", "general dynamics", "honeywell",
    "walmart", "target", "costco", "pfizer", "merck", "abbvie", "johnson & johnson", "unitedhealth",
    "deloitte", "pwc", "kpmg", "accenture", "verizon", "at&t", "comcast", "exxonmobil", "chevron",
    "general motors", "ford", "micron", "texas instruments", "servicenow", "workday", "spotify",
]

# Parent company -> subsidiaries and brands whose postings belong to it. Used
# for quota grouping: an application at AWS counts against Amazon.
PARENT_COMPANIES = {
    "Amazon": ["amazon", "aws", "amazon web services", "twitch", "zoox", "whole foods market", "audible"],
    "Alphabet": ["google", "alphabet", "youtube", "deepmind", "google deepmind", "waymo", "verily",
                 "x development", "wing"],
    "Meta": ["meta", "facebook", "instagram", "whatsapp", "oculus", "reality labs"],
    "Microsoft": ["microsoft", "linkedin", "github", "activision", "blizzard"],
    "ByteDance": ["bytedance", "tiktok", "capcut", "lark"],
    "Salesforce": ["salesforce", "slack", "tableau", "mulesoft"],
    "Block": ["block", "square", "cash app", "afterpay"],
    "Apple": ["apple"],
    "Nvidia": ["nvidia"],
}

# Employers known to cap applications per candidate per hiring cycle. A fact
# about the employer's policy, stated per parent company above.
APPLICATION_LIMITS = {
    "ByteDance": 2,
}
# Everything else: no hard cap, but the dashboard shows at most this many roles
# per company so one employer cannot fill the list.
MAX_PER_COMPANY = 3
